"""ComfyUI Service for production image generation."""

import json
import os
import logging
import asyncio
import uuid
import base64
import secrets
from io import BytesIO
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.models.order import Order, OrderStatus
from app.services.trial_access_service import prepare_delivered_image_urls, is_trial_order
from app.models.live_portrait_job import LivePortraitJob, LivePortraitStatus
from app.services.template_service import get_template_by_id
from app.services.credit_service import add_credits_async, COST_PER_GENERATION, COST_LIVE_PORTRAIT
from app.services import llm_service
from app.services.prompt_brain import build_prompt, get_negative_prompt
from app.services.qa_service import output_passes
from app.services.storage import storage_service

try:
    from PIL import Image, ImageOps, ImageDraw, ImageFilter
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFilter = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
settings = get_settings()


NEGATIVE_PROMPT = (
    "smooth skin, airbrushed, wax, plastic, 3d render, cgi, makeup filter, "
    "bright flat lighting, headless, cropped head, phantom limbs"
)


class ComfyUIService:
    """Client to submit workflows and poll results from ComfyUI."""

    def __init__(self) -> None:
        self._runtime_validation_ok = False
        self._runtime_validation_error: str | None = None

    @staticmethod
    def _api_base_url() -> str:
        return settings.comfy_api_base_url.rstrip("/")

    @staticmethod
    def _public_base_url() -> str:
        return settings.comfy_public_base_url.rstrip("/")

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        return dict(settings.comfy_auth_headers)

    @staticmethod
    def _using_cloud() -> bool:
        return settings.using_comfy_cloud

    def _http_client(
        self,
        *,
        timeout: float | httpx.Timeout = 30.0,
        follow_redirects: bool = False,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            headers=self._auth_headers(),
            follow_redirects=follow_redirects,
        )

    @staticmethod
    def _upload_timeout() -> float | httpx.Timeout:
        if settings.using_comfy_cloud:
            return httpx.Timeout(60.0, connect=30.0)
        return 30.0

    @staticmethod
    def _extract_output_assets(outputs: dict[str, Any]) -> list[dict[str, str]]:
        assets_with_urls: list[dict[str, str]] = []
        for output in outputs.values():
            if not isinstance(output, dict):
                continue
            for media_key in ("images", "gifs", "videos"):
                assets = output.get(media_key, [])
                if not isinstance(assets, list):
                    continue
                for asset in assets:
                    if not isinstance(asset, dict):
                        continue
                    filename = asset.get("filename")
                    subfolder = asset.get("subfolder", "")
                    asset_type = asset.get("type", "output")
                    if not filename:
                        continue
                    query = urlencode(
                        {
                            "filename": str(filename),
                            "subfolder": str(subfolder or ""),
                            "type": str(asset_type or "output"),
                        }
                    )
                    url = f"{settings.comfy_api_base_url.rstrip('/')}/view?{query}"
                    assets_with_urls.append(
                        {
                            "media_type": str(media_key),
                            "url": url,
                            "filename": str(filename),
                        }
                    )
        return assets_with_urls

    async def ping_runtime(self) -> tuple[bool, str]:
        self.validate_runtime_requirements(force=True)
        endpoint = f"{self._api_base_url()}/user" if self._using_cloud() else f"{self._public_base_url()}/system_stats"
        async with self._http_client(timeout=8.0) as client:
            response = await client.get(endpoint)
            response.raise_for_status()
        return True, "ok"

    async def probe_queue_capability(self) -> tuple[bool, str]:
        self.validate_runtime_requirements(force=True)
        if not self._using_cloud():
            return True, "local_provider"
        workflow = self._load_workflow()
        workflow = await self._apply_node_map(
            workflow,
            {
                "prompt": "wedding portrait, realistic skin texture, studio quality",
                "negative_prompt": NEGATIVE_PROMPT,
                "init_image_name": await self._upload_dummy_image("input"),
            },
            self._base_node_map_json(),
        )
        prompt_id = await self._submit(workflow)
        return True, f"queued:{prompt_id}"

    @staticmethod
    def _validate_node_map_alignment(
        workflow: dict[str, Any],
        node_map_raw: str | None,
        *,
        map_name: str,
    ) -> None:
        try:
            node_map = json.loads(node_map_raw or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"{map_name}: invalid node map JSON: {e}") from e

        if not isinstance(node_map, dict):
            raise ValueError(f"{map_name}: node map must be a JSON object")

        missing_nodes: list[str] = []
        missing_keys: list[str] = []
        for key, spec in node_map.items():
            if not isinstance(spec, dict):
                continue
            node_id = str(spec.get("id") or "").strip()
            input_key = str(spec.get("key") or "").strip()
            if not node_id:
                continue
            node = workflow.get(node_id)
            if not isinstance(node, dict):
                missing_nodes.append(f"{key}:{node_id}")
                continue
            inputs = node.get("inputs")
            if input_key and isinstance(inputs, dict) and input_key not in inputs:
                missing_keys.append(f"{key}:{node_id}.{input_key}")

        if missing_nodes or missing_keys:
            parts: list[str] = []
            if missing_nodes:
                parts.append(f"missing_nodes={','.join(missing_nodes[:8])}")
            if missing_keys:
                parts.append(f"missing_input_keys={','.join(missing_keys[:8])}")
            raise ValueError(f"{map_name}: node map mismatch ({'; '.join(parts)})")

    def validate_runtime_requirements(self, *, force: bool = False) -> None:
        """
        Validate workflow + node-map alignment before accepting paid jobs.

        This is a fail-fast guard for commercial mode to avoid enqueueing jobs that
        would definitely fail with `workflow empty` / node-map mismatch.
        """
        if self._runtime_validation_ok and not force:
            return

        base = self._load_workflow()
        inpaint = self._load_inpaint_workflow()

        self._validate_node_map_alignment(
            base,
            self._base_node_map_json(),
            map_name="base_workflow",
        )
        self._validate_node_map_alignment(
            inpaint,
            self._couple_node_map_json(),
            map_name="couple_inpaint_workflow",
        )
        if settings.live_portrait_enabled:
            live = self._load_live_portrait_workflow()
            self._validate_node_map_alignment(
                live,
                settings.comfyui_live_portrait_node_map,
                map_name="live_portrait_workflow",
            )

        self._runtime_validation_ok = True
        self._runtime_validation_error = None

    @staticmethod
    def _resolve_workflow_path(workflow_path: str) -> str:
        base_dir = os.path.dirname(os.path.dirname(__file__))
        if not os.path.isabs(workflow_path):
            workflow_path = os.path.join(base_dir, workflow_path)
        return workflow_path

    async def _upload_image_bytes(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str,
        upload_type: str = "input",
    ) -> str:
        async with self._http_client(timeout=self._upload_timeout()) as client:
            files = {"image": (filename, data, content_type)}
            resp = await client.post(
                f"{self._api_base_url()}/upload/image",
                data={"type": upload_type, "overwrite": "true"},
                files=files,
            )
            resp.raise_for_status()
            payload = resp.json()
        uploaded_name = payload.get("name") or payload.get("filename")
        if not uploaded_name:
            raise ValueError("ComfyUI upload did not return filename")
        return uploaded_name

    async def _build_couple_control_image(self, face_url_a: str, face_url_b: str) -> bytes | None:
        """
        Build a lightweight "couple control base" image by stitching two selfies side-by-side.

        This improves OpenPose/Depth/Normal preprocessing for couple generation without requiring
        a real couple photo. It is intentionally simple and deterministic.
        """
        if Image is None or ImageOps is None:
            return None

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp_a = await client.get(face_url_a)
            resp_a.raise_for_status()
            resp_b = await client.get(face_url_b)
            resp_b.raise_for_status()
            data_a = resp_a.content
            data_b = resp_b.content

        try:
            img_a = Image.open(BytesIO(data_a))
            img_b = Image.open(BytesIO(data_b))
            img_a = ImageOps.exif_transpose(img_a).convert("RGB")
            img_b = ImageOps.exif_transpose(img_b).convert("RGB")
        except Exception:
            return None

        target_h = 768
        max_w = 512
        gap = 24
        bg = (0, 0, 0)

        def _resize(img: Image.Image) -> Image.Image:
            w, h = img.size
            if w <= 0 or h <= 0:
                return img
            scale = min(target_h / float(h), max_w / float(w))
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
            return img.resize((new_w, new_h), resample=resample)

        img_a = _resize(img_a)
        img_b = _resize(img_b)

        w_a, h_a = img_a.size
        w_b, h_b = img_b.size
        canvas_w = w_a + w_b + gap
        canvas_h = max(h_a, h_b, target_h)
        canvas = Image.new("RGB", (canvas_w, canvas_h), bg)

        y_a = (canvas_h - h_a) // 2
        y_b = (canvas_h - h_b) // 2
        canvas.paste(img_a, (0, y_a))
        canvas.paste(img_b, (w_a + gap, y_b))

        out = BytesIO()
        canvas.save(out, format="JPEG", quality=92, optimize=True)
        return out.getvalue()

    @staticmethod
    def _build_couple_inpaint_mask(*, width: int, height: int) -> bytes | None:
        """
        Build a deterministic inpaint mask for couple close-up failures.

        We target the center/lower area (torso/arms/hands) and keep the top band (faces) mostly intact.
        """
        if Image is None or ImageDraw is None or ImageFilter is None:
            return None

        mask = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(mask)

        # Broad torso/arms region (avoid very top corners where faces usually sit).
        x0 = int(width * 0.28)
        x1 = int(width * 0.72)
        y0 = int(height * 0.30)
        y1 = int(height * 0.95)
        draw.rectangle([x0, y0, x1, y1], fill=255)

        # Center band: helps fix fused arms / merged faces in the middle.
        cx0 = int(width * 0.38)
        cx1 = int(width * 0.62)
        cy0 = int(height * 0.18)
        draw.rectangle([cx0, cy0, cx1, y1], fill=255)

        blur = max(12, int(min(width, height) * 0.025))
        mask = mask.filter(ImageFilter.GaussianBlur(radius=blur))

        out = BytesIO()
        mask.save(out, format="PNG", optimize=True)
        return out.getvalue()

    async def _download_and_resize_image(self, image_url: str, *, size: tuple[int, int]) -> bytes:
        """
        Download an image and resize it to an exact size for stable inpaint latents.
        Returns JPEG bytes.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            content = resp.content

        if Image is None or ImageOps is None:
            return content

        try:
            img = Image.open(BytesIO(content))
            img = ImageOps.exif_transpose(img).convert("RGB")
            resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
            img = ImageOps.fit(img, size, method=resample, centering=(0.5, 0.5))
            out = BytesIO()
            img.save(out, format="JPEG", quality=92, optimize=True)
            return out.getvalue()
        except Exception:
            return content

    @staticmethod
    def _apply_retry_seeds(workflow: dict[str, Any], *, attempt: int) -> dict[str, int]:
        """
        Ensure retries actually change the image output.

        ComfyUI KSampler nodes are deterministic for a fixed seed; if we retry with the same workflow,
        we'll usually get the same result. We randomize KSampler seeds per attempt and return the
        per-node seeds for audit/debug.
        """

        def _sort_key(node_id: str) -> tuple[int, str]:
            try:
                return int(node_id), node_id
            except Exception:
                return 10**9, node_id

        ks_nodes: list[str] = [
            str(node_id)
            for node_id, node in workflow.items()
            if isinstance(node, dict) and node.get("class_type") == "KSampler"
        ]
        if not ks_nodes:
            return {}

        base_seed = secrets.randbits(32)
        seeds: dict[str, int] = {}
        for idx, node_id in enumerate(sorted(ks_nodes, key=_sort_key)):
            node = workflow.get(node_id)
            if not isinstance(node, dict):
                continue
            node_inputs = node.setdefault("inputs", {})
            if not isinstance(node_inputs, dict):
                continue
            if "seed" not in node_inputs:
                continue
            seed = int((base_seed + idx + attempt * 9973) % (2**32))
            node_inputs["seed"] = seed
            seeds[node_id] = seed
        return seeds

    async def _persist_outputs_to_storage(
        self,
        output_urls: list[str],
        content_id: uuid.UUID,
        *,
        filename_prefix: str = "order",
        folder: str = "generated",
    ) -> tuple[list[str], str]:
        """
        Persist ComfyUI `/view` outputs to the configured object storage for stable delivery.

        Returns: (public_urls, source_label)
        - source_label: "storage" | "comfyui_view_fallback"
        """
        if not output_urls:
            return [], "comfyui_view_fallback"
        try:
            async with self._http_client(timeout=30.0, follow_redirects=True) as client:
                public_urls: list[str] = []
                from urllib.parse import urlparse, parse_qs

                def _ext_from_url(u: str) -> str | None:
                    try:
                        q = parse_qs(urlparse(u).query or "")
                        filename = (q.get("filename") or [None])[0]
                        if filename and "." in filename:
                            return filename.rsplit(".", 1)[-1].lower()
                    except Exception:
                        return None
                    return None

                def _ext_from_content_type(ct: str) -> str | None:
                    c = (ct or "").lower()
                    if "jpeg" in c or "jpg" in c:
                        return "jpg"
                    if "png" in c:
                        return "png"
                    if "webp" in c:
                        return "webp"
                    if "gif" in c:
                        return "gif"
                    if "mp4" in c:
                        return "mp4"
                    if c.startswith("video/"):
                        # Default video container for ComfyUI outputs
                        return "mp4"
                    return None

                for idx, url in enumerate(output_urls):
                    resp = await client.get(url)
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type") or "application/octet-stream"
                    ext = _ext_from_content_type(content_type) or _ext_from_url(url) or "bin"
                    if content_type == "application/octet-stream":
                        content_type = {
                            "jpg": "image/jpeg",
                            "jpeg": "image/jpeg",
                            "png": "image/png",
                            "webp": "image/webp",
                            "gif": "image/gif",
                            "mp4": "video/mp4",
                        }.get(ext, content_type)
                    filename = f"{filename_prefix}_{content_id}_media_{idx+1}.{ext}"
                    data = resp.content
                    public_url = await asyncio.to_thread(
                        storage_service.upload_file,
                        BytesIO(data),
                        filename,
                        content_type,
                        folder,
                    )
                    public_urls.append(public_url)
                return public_urls, "storage"
        except Exception as e:
            logger.warning(f"Persisting outputs to storage failed, falling back to ComfyUI view URLs: {e}")
            return output_urls, "comfyui_view_fallback"

    @staticmethod
    def _route_controlnet_images(
        workflow: dict[str, Any],
        *,
        use_pose_override: bool,
        use_depth_override: bool,
        use_normal_override: bool,
        is_couple: bool,
    ) -> dict[str, Any]:
        """
        Base workflow auto-generates pose/depth/normal via preprocessors.

        If an override control image is provided, rewire ControlNetApplyAdvanced to use the
        uploaded control image node instead.

        For couple mode, we prefer the stitched "couple control base" preprocessors so OpenPose
        detects two bodies and reduces fused limbs.
        """
        pose_auto = ["41", 0] if is_couple else ["31", 0]
        depth_auto = ["42", 0] if is_couple else ["32", 0]
        normal_auto = ["43", 0] if is_couple else ["33", 0]
        if "28" in workflow and workflow["28"].get("inputs"):
            workflow["28"]["inputs"]["image"] = ["22", 0] if use_pose_override else pose_auto
        if "29" in workflow and workflow["29"].get("inputs"):
            workflow["29"]["inputs"]["image"] = ["23", 0] if use_depth_override else depth_auto
        if "30" in workflow and workflow["30"].get("inputs"):
            workflow["30"]["inputs"]["image"] = ["24", 0] if use_normal_override else normal_auto
        return workflow

    def _load_workflow(self) -> dict[str, Any]:
        workflow_path = settings.comfy_cloud_workflow_path if self._using_cloud() else settings.comfyui_workflow_path
        workflow_path = self._resolve_workflow_path(workflow_path)
        if not os.path.exists(workflow_path):
            raise FileNotFoundError(f"ComfyUI workflow not found: {workflow_path}")
        # Some exported ComfyUI JSON files may include a UTF-8 BOM.
        with open(workflow_path, "r", encoding="utf-8-sig") as f:
            workflow = json.load(f)
        if not workflow:
            raise ValueError("ComfyUI workflow is empty")
        return workflow

    def _load_inpaint_workflow(self) -> dict[str, Any]:
        workflow_path = settings.comfy_cloud_couple_workflow_path if self._using_cloud() else settings.comfyui_inpaint_workflow_path
        workflow_path = self._resolve_workflow_path(workflow_path)
        if not os.path.exists(workflow_path):
            raise FileNotFoundError(f"ComfyUI inpaint workflow not found: {workflow_path}")
        with open(workflow_path, "r", encoding="utf-8-sig") as f:
            workflow = json.load(f)
        if not workflow:
            raise ValueError("ComfyUI inpaint workflow is empty")
        return workflow

    def _load_live_portrait_workflow(self) -> dict[str, Any]:
        workflow_path = self._resolve_workflow_path(settings.comfyui_live_portrait_workflow_path)
        if not os.path.exists(workflow_path):
            raise FileNotFoundError(f"ComfyUI Live Portrait workflow not found: {workflow_path}")
        with open(workflow_path, "r", encoding="utf-8-sig") as f:
            workflow = json.load(f)
        if not workflow:
            raise ValueError("ComfyUI Live Portrait workflow is empty")
        return workflow

    @staticmethod
    def _base_node_map_json() -> str | None:
        if settings.using_comfy_cloud:
            return settings.comfy_cloud_node_map
        return settings.comfyui_node_map

    @staticmethod
    def _couple_node_map_json() -> str | None:
        if settings.using_comfy_cloud:
            return settings.comfy_cloud_node_map
        return settings.comfyui_node_map

    @staticmethod
    async def _build_order_prompt(
        *,
        template,
        prompt_override: str | None,
        global_style_text: str | None,
        scene_text: str | None,
        outfit_text: str | None,
        is_couple: bool,
    ) -> str:
        legacy_override = (prompt_override or "").strip()
        normalized_global_style = (global_style_text or "").strip() or None
        normalized_scene_text = (scene_text or "").strip() or None
        normalized_outfit_text = (outfit_text or "").strip() or None

        if legacy_override and not any([normalized_global_style, normalized_scene_text, normalized_outfit_text]):
            prompt_text = legacy_override
        else:
            prompt_text = build_prompt(
                template,
                user_text=normalized_global_style or legacy_override or None,
                scene_text=normalized_scene_text,
                clothing_text=normalized_outfit_text,
                is_couple=is_couple,
            )

        if is_couple:
            prompt_text = (
                f"{prompt_text.rstrip('.')}."
                " Balanced couple blocking, equal prominence for both subjects,"
                " natural hand placement, readable silhouettes, clear arm separation,"
                " symmetric spacing between bride and groom."
            )
        return await llm_service.optimize_generation_prompt(prompt_text, is_couple=is_couple)

    @staticmethod
    def _build_negative_prompt(*, is_couple: bool) -> str:
        negative = get_negative_prompt()
        if not is_couple:
            return negative
        return (
            f"{negative}, fused faces, merged heads, duplicate bride, duplicate groom,"
            " shared torso, conjoined shoulders, extra bouquet, overlapping limbs,"
            " swapped identity, asymmetric couple framing"
        )

    @staticmethod
    def _tune_couple_workflow(
        workflow: dict[str, Any],
        *,
        is_couple: bool,
    ) -> dict[str, Any]:
        if not is_couple:
            return workflow

        if "4" in workflow and isinstance(workflow["4"], dict):
            workflow["4"].setdefault("inputs", {})
            workflow["4"]["inputs"]["width"] = 960
            workflow["4"]["inputs"]["height"] = 1280

        if "6" in workflow and isinstance(workflow["6"], dict):
            workflow["6"].setdefault("inputs", {})
            workflow["6"]["inputs"]["width"] = 1152
            workflow["6"]["inputs"]["height"] = 1536

        if "5" in workflow and isinstance(workflow["5"], dict):
            workflow["5"].setdefault("inputs", {})
            workflow["5"]["inputs"]["steps"] = 30
            workflow["5"]["inputs"]["cfg"] = 4.4

        if "7" in workflow and isinstance(workflow["7"], dict):
            workflow["7"].setdefault("inputs", {})
            workflow["7"]["inputs"]["steps"] = 18
            workflow["7"]["inputs"]["cfg"] = 4.0
            workflow["7"]["inputs"]["denoise"] = 0.28

        return workflow

    async def _apply_node_map(
        self,
        workflow: dict[str, Any],
        inputs: dict[str, Any],
        node_map_json: str | None = None,
    ) -> dict[str, Any]:
        try:
            raw = node_map_json if node_map_json is not None else settings.comfyui_node_map
            node_map = json.loads(raw or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid COMFYUI node map JSON: {e}")

        for key, mapping in node_map.items():
            if key not in inputs:
                continue
            value = inputs[key]
            node_id = str(mapping.get("id"))
            input_key = mapping.get("key", "text")
            if value is None:
                if mapping.get("allow_empty") and mapping.get("upload"):
                    value = await self._upload_dummy_image(mapping.get("type", "input"))
                else:
                    continue
            if mapping.get("upload"):
                value = await self._upload_image_url(value, mapping.get("type", "input"))
            if node_id not in workflow:
                continue
            workflow[node_id].setdefault("inputs", {})
            workflow[node_id]["inputs"][input_key] = value
        return workflow

    async def _upload_image_url(self, image_url: str, upload_type: str = "input") -> str:
        async with self._http_client(timeout=self._upload_timeout()) as client:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()
            files = {"image": ("upload.jpg", img_resp.content, "image/jpeg")}
            resp = await client.post(
                f"{self._api_base_url()}/upload/image",
                data={"type": upload_type, "overwrite": "true"},
                files=files,
            )
            resp.raise_for_status()
            data = resp.json()
        filename = data.get("name") or data.get("filename")
        if not filename:
            raise ValueError("ComfyUI upload did not return filename")
        return filename

    async def _prepare_cloud_init_image_name(
        self,
        *,
        user_images: list[str],
        is_couple: bool,
        couple_control_filename: str | None,
    ) -> str:
        if is_couple and couple_control_filename:
            return couple_control_filename
        if not user_images or not user_images[0]:
            raise ValueError("Missing portrait image for Comfy Cloud init input")
        return await self._upload_image_url(user_images[0], "input")

    async def _upload_dummy_image(self, upload_type: str = "input") -> str:
        # 1x1 white PNG
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO9mKQAAAABJRU5ErkJggg=="
        )
        async with self._http_client(timeout=self._upload_timeout()) as client:
            files = {"image": ("dummy.png", png_bytes, "image/png")}
            resp = await client.post(
                f"{self._api_base_url()}/upload/image",
                data={"type": upload_type, "overwrite": "true"},
                files=files,
            )
            resp.raise_for_status()
            data = resp.json()
        filename = data.get("name") or data.get("filename")
        if not filename:
            raise ValueError("ComfyUI dummy upload failed")
        return filename

    async def _submit(self, workflow: dict[str, Any]) -> str:
        async with self._http_client(timeout=20.0) as client:
            resp = await client.post(
                f"{self._api_base_url()}/prompt",
                json={"prompt": workflow},
            )
            if resp.status_code >= 400:
                detail = ""
                try:
                    payload = resp.json()
                    error_payload = payload.get("error") if isinstance(payload, dict) else None
                    if isinstance(error_payload, dict):
                        detail = str(error_payload.get("message") or error_payload.get("type") or "").strip()
                    elif isinstance(payload, dict):
                        detail = str(payload.get("message") or payload.get("detail") or "").strip()
                except Exception:
                    detail = ""
                if resp.status_code == 429 and detail:
                    raise RuntimeError(f"Comfy Cloud queue rejected: {detail}")
                resp.raise_for_status()
            data = resp.json()
        prompt_id = data.get("prompt_id") or data.get("prompt_id".upper())
        if not prompt_id:
            raise ValueError("ComfyUI response missing prompt_id")
        return prompt_id

    async def _poll_assets(self, prompt_id: str) -> list[dict[str, str]]:
        deadline = asyncio.get_event_loop().time() + settings.comfyui_poll_timeout
        async with self._http_client(timeout=10.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                if self._using_cloud():
                    status_resp = await client.get(f"{self._api_base_url()}/job/{prompt_id}/status")
                    status_resp.raise_for_status()
                    status_payload = status_resp.json() if status_resp.content else {}
                    status = str(status_payload.get("status") or "").strip().lower()
                    if status in {"completed", "success"}:
                        history_resp = await client.get(f"{self._api_base_url()}/history_v2/{prompt_id}")
                        history_resp.raise_for_status()
                        history_payload = history_resp.json() if history_resp.content else {}
                        outputs = history_payload.get("outputs", {}) if isinstance(history_payload, dict) else {}
                        assets_with_urls = self._extract_output_assets(outputs if isinstance(outputs, dict) else {})
                        if assets_with_urls:
                            return assets_with_urls
                    elif status in {"failed", "error", "cancelled", "canceled"}:
                        detail = (
                            status_payload.get("error")
                            or status_payload.get("message")
                            or status_payload.get("reason")
                            or status
                        )
                        raise RuntimeError(f"Comfy Cloud job failed: {detail}")
                else:
                    resp = await client.get(f"{self._api_base_url()}/history/{prompt_id}")
                    resp.raise_for_status()
                    history = resp.json()
                    item = history.get(prompt_id)
                    if item and item.get("outputs"):
                        assets_with_urls = self._extract_output_assets(item["outputs"])
                        if assets_with_urls:
                            return assets_with_urls
                await asyncio.sleep(settings.comfyui_poll_interval)
        raise TimeoutError("ComfyUI generation timed out")

    async def _poll_outputs(self, prompt_id: str) -> list[str]:
        assets = await self._poll_assets(prompt_id)
        return [a["url"] for a in assets if a.get("url")]

    @staticmethod
    def _pick_primary_url(assets: list[dict[str, str]], *, prefer: str) -> str | None:
        if not assets:
            return None
        if prefer == "video":
            order = ("videos", "gifs", "images")
        else:
            order = ("images", "videos", "gifs")
        for media_type in order:
            for asset in assets:
                if asset.get("media_type") == media_type and asset.get("url"):
                    return asset["url"]
        first = assets[0].get("url")
        return first or None

    async def _cleanup_source_images(self, source_urls: list[str]) -> None:
        if not settings.cleanup_source_images_on_complete:
            return
        for source_url in source_urls:
            if not isinstance(source_url, str) or not source_url:
                continue
            try:
                await asyncio.to_thread(storage_service.delete_file, source_url)
            except Exception as e:
                logger.warning(f"Failed to cleanup source image: {e}")

    async def _run_couple_inpaint_fix(
        self,
        *,
        attempt: int,
        node_inputs: dict[str, Any],
        init_image_url: str,
        couple_control_filename: str | None,
        use_pose_override: bool,
        use_depth_override: bool,
        use_normal_override: bool,
        use_couple_preprocessors: bool,
    ) -> tuple[str, list[str], dict[str, int]]:
        """
        Close-up couple fallback:
        - Build a deterministic mask for the torso/arms area
        - Inpaint on top of the failed generation to separate limbs / reduce fusion
        """
        workflow = self._load_inpaint_workflow()
        workflow = self._tune_couple_workflow(workflow, is_couple=True)
        seeds = self._apply_retry_seeds(workflow, attempt=attempt)

        if couple_control_filename and "36" in workflow:
            workflow["36"].setdefault("inputs", {})
            workflow["36"]["inputs"]["image"] = couple_control_filename

        target_size = (1024, 1280)
        init_jpeg = await self._download_and_resize_image(init_image_url, size=target_size)
        init_filename = await self._upload_image_bytes(
            data=init_jpeg,
            filename="init_input.jpg",
            content_type="image/jpeg",
            upload_type="input",
        )

        mask_png = self._build_couple_inpaint_mask(width=target_size[0], height=target_size[1])
        if not mask_png:
            raise ValueError("Pillow not available: cannot build inpaint mask")
        mask_filename = await self._upload_image_bytes(
            data=mask_png,
            filename="inpaint_mask.png",
            content_type="image/png",
            upload_type="input",
        )

        if "40" in workflow:
            workflow["40"].setdefault("inputs", {})
            workflow["40"]["inputs"]["image"] = init_filename
        if "44" in workflow:
            workflow["44"].setdefault("inputs", {})
            workflow["44"]["inputs"]["image"] = mask_filename

        workflow = await self._apply_node_map(
            workflow,
            node_inputs,
            node_map_json=self._couple_node_map_json(),
        )
        workflow = self._route_controlnet_images(
            workflow,
            use_pose_override=use_pose_override,
            use_depth_override=use_depth_override,
            use_normal_override=use_normal_override,
            is_couple=use_couple_preprocessors,
        )

        prompt_id = await self._submit(workflow)
        output_urls = await self._poll_outputs(prompt_id)
        return prompt_id, output_urls, seeds

    async def generate_live_portrait(
        self,
        *,
        job_id: str,
        image_url: str,
        seconds: int = 5,
    ) -> None:
        """
        Generate a short video (Live Portrait) from a static image.

        Notes:
        - Expects a production workflow at `settings.comfyui_live_portrait_workflow_path`.
        - Uses a dedicated node map `settings.comfyui_live_portrait_node_map`.
        """
        job_uuid = uuid.UUID(str(job_id))
        max_attempts = max(1, min(3, settings.comfyui_max_retries + 1))
        attempt_logs: list[dict[str, Any]] = []

        try:
            if not settings.live_portrait_enabled:
                raise ValueError("Live Portrait is disabled")

            for attempt in range(max_attempts):
                attempt_seconds = max(3, min(10, int(seconds) - attempt))
                prompt_id: str | None = None
                try:
                    workflow = self._load_live_portrait_workflow()
                    self._apply_retry_seeds(workflow, attempt=attempt)
                    node_inputs: dict[str, Any] = {
                        "image_url": image_url,
                        "seconds": int(attempt_seconds),
                    }
                    workflow = await self._apply_node_map(
                        workflow,
                        node_inputs,
                        node_map_json=settings.comfyui_live_portrait_node_map,
                    )

                    prompt_id = await self._submit(workflow)
                    output_assets = await self._poll_assets(prompt_id)
                    output_urls = [a["url"] for a in output_assets if a.get("url")]
                    video_urls = [
                        a["url"]
                        for a in output_assets
                        if a.get("url") and a.get("media_type") in {"videos", "gifs"}
                    ]

                    # Commercial strict mode: Live Portrait must deliver motion assets only.
                    persist_candidates = video_urls
                    if not persist_candidates:
                        raise ValueError("live_portrait_video_output_empty")

                    delivered_urls, delivered_source = await self._persist_outputs_to_storage(
                        persist_candidates,
                        job_uuid,
                        filename_prefix="live_portrait",
                        folder="live_portrait",
                    )
                    if settings.comfyui_require_storage_delivery and delivered_source != "storage":
                        raise ValueError("storage_delivery_required_but_unavailable")

                    chosen_url = delivered_urls[0] if delivered_urls else persist_candidates[0]

                    attempt_logs.append(
                        {
                            "attempt": attempt + 1,
                            "seconds": attempt_seconds,
                            "prompt_id": prompt_id,
                            "asset_count": len(output_urls),
                            "video_asset_count": len(video_urls),
                            "delivery_source": delivered_source,
                            "video_required": True,
                            "status": "ok",
                        }
                    )

                    async with async_session_maker() as db:
                        result = await db.execute(select(LivePortraitJob).where(LivePortraitJob.id == job_uuid))
                        job = result.scalar_one_or_none()
                        if job:
                            job.status = LivePortraitStatus.COMPLETED
                            job.video_url = chosen_url
                            job.error_message = None

                            base_params = job.generation_params if isinstance(job.generation_params, dict) else {}
                            debug = base_params.get("debug") if isinstance(base_params.get("debug"), dict) else {}
                            debug.update(
                                {
                                    "prompt_id": prompt_id,
                                    "comfyui_view_urls": output_urls,
                                    "delivery_source": delivered_source,
                                    "attempts": attempt_logs,
                                    "media_mode": "video",
                                }
                            )
                            base_params["debug"] = debug
                            job.generation_params = base_params
                    await db.commit()
                    return
                except Exception as attempt_error:
                    attempt_logs.append(
                        {
                            "attempt": attempt + 1,
                            "seconds": attempt_seconds,
                            "prompt_id": prompt_id,
                            "status": "error",
                            "error": str(attempt_error),
                        }
                    )
                    if attempt >= max_attempts - 1:
                        raise
                    continue
        except Exception as e:
            logger.error(f"ComfyUI live portrait failed: {e}")
            async with async_session_maker() as db:
                result = await db.execute(select(LivePortraitJob).where(LivePortraitJob.id == job_uuid))
                job = result.scalar_one_or_none()

                if job:
                    refund_amount = COST_LIVE_PORTRAIT
                    try:
                        refund_amount = int(job.credits_cost or refund_amount)
                    except Exception:
                        refund_amount = COST_LIVE_PORTRAIT
                    failure_code = self._classify_live_portrait_error(e)
                    if refund_amount:
                        await add_credits_async(db, job.user_id, refund_amount)
                    job.status = LivePortraitStatus.FAILED
                    job.error_message = str(e)
                    base_params = job.generation_params if isinstance(job.generation_params, dict) else {}
                    debug = base_params.get("debug") if isinstance(base_params.get("debug"), dict) else {}
                    debug.update({"attempts": attempt_logs})
                    base_params["debug"] = debug
                    base_params["failure_code"] = failure_code
                    base_params["refunded_credits"] = refund_amount
                    job.generation_params = base_params
                    await db.commit()
            return

    def _classify_live_portrait_error(self, error: Exception) -> str:
        text = str(error).strip().lower()
        if not text:
            return "unknown_error"
        if "model" in text or "checkpoint" in text or "clip" in text or "vae" in text or "lora" in text:
            return "model_missing"
        if "workflow" in text or "node map" in text or "invalid" in text:
            return "workflow_error"
        if "node" in text or "input" in text:
            return "node_error"
        if "video_output_empty" in text or "video" in text and "empty" in text:
            return "video_output_empty"
        if "storage" in text or "delivery" in text:
            return "delivery_error"
        return "unknown_error"

    def _classify_generation_error(self, error: Exception) -> str:
        text = str(error).strip().lower()
        if not text:
            return "unknown_error"
        if "subscription required to queue workflows" in text:
            return "cloud_subscription_required"
        if "queue rejected" in text:
            return "cloud_queue_rejected"
        if "storage_delivery_required" in text or "delivery" in text or "no delivered outputs" in text:
            return "delivery_error"
        if "timed out" in text or "timeout" in text:
            return "generation_timeout"
        if "workflow" in text or "node map" in text or "invalid" in text:
            return "workflow_error"
        if "job failed" in text:
            return "cloud_job_failed"
        return "unknown_error"

    async def generate_photo(
        self,
        order_id: str,
        template_id: str,
        user_images: list[str],
        subject_count: int | None = None,
        couple_flow: str | None = None,
        prompt_override: str | None = None,
        global_style_text: str | None = None,
        scene_text: str | None = None,
        outfit_text: str | None = None,
        scene_image_url: str | None = None,
        clothing_image_url: str | None = None,
        pose_image_url: str | None = None,
        depth_image_url: str | None = None,
        normal_image_url: str | None = None,
        scene_ip_weight: float | None = None,
        clothing_ip_weight: float | None = None,
        face_ip_weight: float | None = None,
        pose_cn_weight: float | None = None,
        depth_cn_weight: float | None = None,
        normal_cn_weight: float | None = None,
        pose_cn_start: float | None = None,
        pose_cn_end: float | None = None,
        depth_cn_start: float | None = None,
        depth_cn_end: float | None = None,
        normal_cn_start: float | None = None,
        normal_cn_end: float | None = None,
    ) -> None:
        order_uuid = uuid.UUID(str(order_id))
        try:
            template = get_template_by_id(template_id)
            if not template:
                raise ValueError("Template not found")

            max_retries = max(0, settings.comfyui_max_retries)

            couple_control_filename: str | None = None
            normalized_subject_count = max(0, int(subject_count or len([u for u in user_images if u])))
            has_two_faces = bool(
                normalized_subject_count >= 2
                and user_images
                and len(user_images) > 1
                and user_images[0]
                and user_images[1]
            )
            prompt_text = await self._build_order_prompt(
                template=template,
                prompt_override=prompt_override,
                global_style_text=global_style_text,
                scene_text=scene_text,
                outfit_text=outfit_text,
                is_couple=has_two_faces,
            )
            negative = self._build_negative_prompt(is_couple=has_two_faces)
            if has_two_faces and (Image is not None):
                try:
                    couple_bytes = await self._build_couple_control_image(user_images[0], user_images[1])
                    if couple_bytes:
                        couple_control_filename = await self._upload_image_bytes(
                            data=couple_bytes,
                            filename="couple_control.jpg",
                            content_type="image/jpeg",
                            upload_type="input",
                        )
                except Exception as e:
                    logger.warning(f"Couple control image build/upload failed; falling back to single preprocessors: {e}")
                    couple_control_filename = None

            inpaint_used = False
            inpaint_eligible = {
                "fused_faces",
                "body_fusion",
                "subject_missing",
                "identity_swap",
                "identity_mismatch",
                "extra_limbs",
                "bad_hands",
                "dress_exposure_error",
                "face_distortion",
                "cropped_face",
                "headless",
                "severe_artifacts",
            }
            if self._using_cloud():
                inpaint_eligible = set()

            for attempt in range(max_retries + 1):
                def pick_weight(value: float | None, default: float, has_image: bool) -> float:
                    if value is not None:
                        return float(value)
                    return default if has_image else 0.0

                scene_weight = pick_weight(scene_ip_weight, 0.55, bool(scene_image_url))
                clothing_weight = pick_weight(clothing_ip_weight, 0.6, bool(clothing_image_url))
                has_subject_image = bool(user_images and user_images[0])
                has_second_subject_image = bool(user_images and len(user_images) > 1 and user_images[1])
                is_couple = has_second_subject_image
                use_couple_preprocessors = bool(is_couple and couple_control_filename)
                default_face_weight = 0.82 if is_couple else 0.75
                default_face2_weight = 0.84 if is_couple else default_face_weight
                face_weight = pick_weight(face_ip_weight, default_face_weight, has_subject_image)
                face2_weight = pick_weight(None, default_face2_weight, has_second_subject_image)

                # ControlNet maps are auto-generated from the subject image by default.
                # For couple mode, only enable auto-preprocessors if we have a stitched couple control image.
                # Otherwise default to OFF (unless the user explicitly provides control images / overrides).
                pose_default = 0.7 if pose_image_url else (0.72 if use_couple_preprocessors else (0.0 if is_couple else 0.7))
                depth_default = 0.5 if depth_image_url else (0.34 if use_couple_preprocessors else (0.0 if is_couple else 0.5))
                normal_default = 0.45 if normal_image_url else (0.26 if use_couple_preprocessors else (0.0 if is_couple else 0.45))

                pose_weight = pick_weight(pose_cn_weight, pose_default, bool(pose_image_url) or has_subject_image)
                depth_weight = pick_weight(depth_cn_weight, depth_default, bool(depth_image_url) or has_subject_image)
                normal_weight = pick_weight(normal_cn_weight, normal_default, bool(normal_image_url) or has_subject_image)

                def pick_range(value: float | None, default: float) -> float:
                    return float(value) if value is not None else default

                pose_start = pick_range(pose_cn_start, 0.0)
                pose_end = pick_range(pose_cn_end, 1.0)
                depth_start = pick_range(depth_cn_start, 0.0)
                depth_end = pick_range(depth_cn_end, 1.0)
                normal_start = pick_range(normal_cn_start, 0.0)
                normal_end = pick_range(normal_cn_end, 1.0)

                if self._using_cloud() and is_couple:
                    workflow = self._load_inpaint_workflow()
                else:
                    workflow = self._load_workflow()
                    workflow = self._tune_couple_workflow(workflow, is_couple=is_couple)
                seeds = self._apply_retry_seeds(workflow, attempt=attempt)
                if (not self._using_cloud()) and couple_control_filename and "36" in workflow:
                    workflow["36"].setdefault("inputs", {})
                    workflow["36"]["inputs"]["image"] = couple_control_filename

                if self._using_cloud():
                    init_image_name = await self._prepare_cloud_init_image_name(
                        user_images=user_images,
                        is_couple=is_couple,
                        couple_control_filename=couple_control_filename,
                    )
                    node_inputs = {
                        "prompt": prompt_text,
                        "negative_prompt": negative,
                        "init_image_name": init_image_name,
                    }
                    workflow = await self._apply_node_map(
                        workflow,
                        node_inputs,
                        node_map_json=self._couple_node_map_json() if is_couple else self._base_node_map_json(),
                    )
                else:
                    node_inputs = {
                        "prompt": prompt_text,
                        "negative_prompt": negative,
                        "face_image_url": user_images[0] if user_images else None,
                        "face_image_url_2": user_images[1] if has_second_subject_image else None,
                        "scene_image_url": scene_image_url,
                        "clothing_image_url": clothing_image_url,
                        "scene_ip_weight": scene_weight,
                        "clothing_ip_weight": clothing_weight,
                        "face_ip_weight": face_weight,
                        "face2_ip_weight": face2_weight,
                        "pose_cn_weight": pose_weight,
                        "depth_cn_weight": depth_weight,
                        "normal_cn_weight": normal_weight,
                        "pose_cn_start": pose_start,
                        "pose_cn_end": pose_end,
                        "depth_cn_start": depth_start,
                        "depth_cn_end": depth_end,
                        "normal_cn_start": normal_start,
                        "normal_cn_end": normal_end,
                    }
                    if pose_image_url:
                        node_inputs["pose_image_url"] = pose_image_url
                    if depth_image_url:
                        node_inputs["depth_image_url"] = depth_image_url
                    if normal_image_url:
                        node_inputs["normal_image_url"] = normal_image_url

                    workflow = await self._apply_node_map(
                        workflow,
                        node_inputs,
                        node_map_json=self._couple_node_map_json() if is_couple else self._base_node_map_json(),
                    )
                    workflow = self._route_controlnet_images(
                        workflow,
                        use_pose_override=bool(pose_image_url),
                        use_depth_override=bool(depth_image_url),
                        use_normal_override=bool(normal_image_url),
                        is_couple=use_couple_preprocessors,
                    )

                prompt_id = await self._submit(workflow)

                async with async_session_maker() as db:
                    result = await db.execute(select(Order).where(Order.id == order_uuid))
                    order = result.scalar_one_or_none()
                    if order:
                        order.status = OrderStatus.GENERATING
                        order.task_id = prompt_id
                        base_params = order.generation_params if isinstance(order.generation_params, dict) else {}
                        base_params.update({
                            "engine": settings.generation_provider_name,
                            "prompt": prompt_text,
                            "negative_prompt": negative,
                            "attempt": attempt + 1,
                            "seeds": seeds,
                            "is_couple": is_couple,
                            "couple_strategy": {
                                "two_face_mode": bool(is_couple),
                                "couple_control_enabled": bool(couple_control_filename),
                                "auto_preprocessors_enabled": bool(use_couple_preprocessors),
                                "face_weight_primary": face_weight,
                                "face_weight_secondary": face2_weight,
                                "pose_weight": pose_weight,
                                "depth_weight": depth_weight,
                                "normal_weight": normal_weight,
                            },
                        })
                        if self._using_cloud():
                            base_params["cloud_minimal_mode"] = {
                                "enabled": True,
                                "scene_reference_ignored": bool(scene_image_url),
                                "outfit_reference_ignored": bool(clothing_image_url),
                                "pose_reference_ignored": bool(pose_image_url),
                                "depth_reference_ignored": bool(depth_image_url),
                                "normal_reference_ignored": bool(normal_image_url),
                                "stitched_couple_input": bool(is_couple and couple_control_filename),
                            }
                        order.generation_params = base_params
                        await db.commit()

                output_assets = await self._poll_assets(prompt_id)
                output_urls = [a["url"] for a in output_assets if a.get("url")]
                primary_image_url = self._pick_primary_url(output_assets, prefer="image")
                if not output_urls or not primary_image_url:
                    continue

                qa_ok, qa_reasons = await output_passes(
                    primary_image_url,
                    is_couple=is_couple,
                    source_image_urls=[str(url) for url in user_images if url],
                )
                if not qa_ok:
                    async with async_session_maker() as db:
                        result = await db.execute(select(Order).where(Order.id == order_uuid))
                        order = result.scalar_one_or_none()
                        if order:
                            base_params = order.generation_params if isinstance(order.generation_params, dict) else {}
                            debug = base_params.get("debug") if isinstance(base_params.get("debug"), dict) else {}
                            qa_history = debug.get("qa_history") if isinstance(debug.get("qa_history"), list) else []
                            qa_history.append(
                                {
                                    "attempt": attempt + 1,
                                    "reasons": list(qa_reasons),
                                    "candidate_url": primary_image_url,
                                }
                            )
                            debug["qa_history"] = qa_history[-8:]
                            base_params["debug"] = debug
                            base_params["qa_last_reasons"] = list(qa_reasons)
                            base_params["qa_attempt_count"] = attempt + 1
                            base_params["couple_guardrails"] = {
                                "is_couple": bool(is_couple),
                                "subject_count": normalized_subject_count,
                                "couple_flow": couple_flow,
                                "inpaint_eligible": sorted(inpaint_eligible),
                            }
                            order.generation_params = base_params
                            await db.commit()

                    # Couple close-up fallback: try a single masked inpaint fix before doing a full regenerate.
                    if (
                        is_couple
                        and (not inpaint_used)
                        and any(r in inpaint_eligible for r in qa_reasons)
                    ):
                        inpaint_used = True
                        try:
                            inpaint_prompt_id, inpaint_urls, inpaint_seeds = await self._run_couple_inpaint_fix(
                                attempt=attempt + 1,
                                node_inputs=node_inputs,
                                init_image_url=primary_image_url,
                                couple_control_filename=couple_control_filename,
                                use_pose_override=bool(pose_image_url),
                                use_depth_override=bool(depth_image_url),
                                use_normal_override=bool(normal_image_url),
                                use_couple_preprocessors=use_couple_preprocessors,
                            )

                            async with async_session_maker() as db:
                                result = await db.execute(select(Order).where(Order.id == order_uuid))
                                order = result.scalar_one_or_none()
                                if order:
                                    order.task_id = inpaint_prompt_id
                                    base_params = order.generation_params if isinstance(order.generation_params, dict) else {}
                                    debug = base_params.get("debug") if isinstance(base_params.get("debug"), dict) else {}
                                    debug.update(
                                        {
                                            "base_prompt_id": prompt_id,
                                            "inpaint_prompt_id": inpaint_prompt_id,
                                            "inpaint_seeds": inpaint_seeds,
                                            "inpaint_mask": "center_torso_v1",
                                            "qa_failed_reasons": qa_reasons,
                                        }
                                    )
                                    base_params["debug"] = debug
                                    order.generation_params = base_params
                                    await db.commit()

                            inpaint_ok, inpaint_reasons = await output_passes(
                                inpaint_urls[0],
                                is_couple=True,
                                source_image_urls=[str(url) for url in user_images if url],
                            )
                            if inpaint_ok:
                                output_urls = inpaint_urls
                                output_assets = [{"media_type": "images", "url": u, "filename": ""} for u in inpaint_urls]
                                primary_image_url = inpaint_urls[0]
                                qa_ok = True
                                qa_reasons = []
                            else:
                                logger.warning(f"Inpaint QA failed: {inpaint_reasons}")
                                if attempt < max_retries:
                                    continue
                                raise ValueError(f"QA failed: {','.join(inpaint_reasons)}")
                        except Exception as e:
                            logger.warning(f"Couple inpaint fallback failed: {e}")
                            # Fall through to standard retry behavior.

                    if not qa_ok:
                        logger.warning(f"QA failed on attempt {attempt+1}: {qa_reasons}")
                        if attempt < max_retries:
                            continue
                        raise ValueError(f"QA failed: {','.join(qa_reasons)}")

                delivered_urls, delivered_source = await self._persist_outputs_to_storage(output_urls, order_uuid)
                if settings.comfyui_require_storage_delivery and delivered_source != "storage":
                    raise ValueError("storage_delivery_required_but_unavailable")
                if not delivered_urls:
                    raise ValueError("No delivered outputs")

                async with async_session_maker() as db:
                    result = await db.execute(select(Order).where(Order.id == order_uuid))
                    order = result.scalar_one_or_none()
                    if order:
                        order.status = OrderStatus.COMPLETED
                        base_params = order.generation_params if isinstance(order.generation_params, dict) else {}
                        preview_urls, final_urls, preview_meta = await prepare_delivered_image_urls(
                            delivered_urls,
                            trial_preview=is_trial_order(base_params),
                        )
                        order.preview_image_urls = preview_urls
                        order.final_image_urls = final_urls
                        debug = base_params.get("debug") if isinstance(base_params.get("debug"), dict) else {}
                        debug.update({"comfyui_view_urls": output_urls, "delivery_source": delivered_source})
                        base_params["debug"] = debug
                        base_params["delivery"] = {
                            **(base_params.get("delivery") if isinstance(base_params.get("delivery"), dict) else {}),
                            **preview_meta,
                        }
                        base_params["qa_last_reasons"] = []
                        base_params["qa_attempt_count"] = attempt + 1
                        base_params["couple_guardrails"] = {
                            "is_couple": bool(is_couple),
                            "subject_count": normalized_subject_count,
                            "couple_flow": couple_flow,
                            "control_image_enabled": bool(couple_control_filename),
                            "auto_preprocessors_enabled": bool(use_couple_preprocessors),
                        }
                        order.generation_params = base_params
                        await db.commit()
                await self._cleanup_source_images([str(u) for u in user_images if u])
                return
        except Exception as e:
            logger.error(f"ComfyUI generation failed: {e}")
            async with async_session_maker() as db:
                result = await db.execute(select(Order).where(Order.id == order_uuid))
                order = result.scalar_one_or_none()
                refund_amount = COST_PER_GENERATION
                failure_code = self._classify_generation_error(e)
                clean_error_message = str(e).strip() or failure_code or type(e).__name__
                if order and isinstance(order.generation_params, dict):
                    params = order.generation_params
                    try:
                        if "credits_cost" in params:
                            refund_amount = max(0, int(params.get("credits_cost") or 0))
                    except Exception:
                        refund_amount = COST_PER_GENERATION
                if refund_amount:
                    target_user_id = order.user_id if order else None
                    if target_user_id:
                        await add_credits_async(db, target_user_id, refund_amount)
                if order:
                    order.status = OrderStatus.CREATED
                    order.error_message = clean_error_message
                    base_params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
                    base_params["failure_code"] = failure_code
                    base_params["failure_provider"] = settings.generation_provider_name
                    if refund_amount:
                        base_params["refunded_credits"] = refund_amount
                    order.generation_params = base_params
                    await db.commit()
            return


comfyui_service = ComfyUIService()
