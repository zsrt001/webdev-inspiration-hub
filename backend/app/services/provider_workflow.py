"""Shared image generation workflow and policy for hosted image providers."""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from sqlalchemy import select

try:
    from PIL import Image
except Exception:  # pragma: no cover - runtime dependency guard
    Image = None  # type: ignore[assignment]

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.models.order import Order, OrderStatus
from app.services.trial_access_service import prepare_delivered_image_urls, is_trial_order
from app.services import llm_service
from app.services.credit_service import COST_PER_GENERATION, refund_generation_credits_once_async
from app.services.generation_credit_policy import (
    generation_refund_metadata,
    merge_generation_refund_state,
    resolve_generation_refund_amount,
)
from app.services.generation_policy import (
    QA_MAX_ATTEMPTS as GENERATION_QA_MAX_ATTEMPTS,
    QA_RETRY_REASONS as GENERATION_QA_RETRY_REASONS,
    build_generation_negative_prompt,
    build_studio_generation_prompt,
    resolve_generation_aspect_ratio,
    should_retry_qa,
)
from app.services.generation_stage_service import merge_generation_stage
from app.services.generation_state_service import record_generation_qa_failure
from app.services import repair_policy
from app.services.identity_control import classify_identity_qa, identity_qa_requires_forced_repair
from app.services.prompt_brain import get_studio_guardrails
from app.services.qa_service import output_verdict
from app.services.storage import storage_service
from app.services.template_service import get_template_by_id

logger = logging.getLogger(__name__)
settings = get_settings()


class GenerationProviderWorkflow:
    """Shared order workflow, QA loop, repair policy, delivery, and failure handling."""

    PROVIDER = "wenwen"
    CIRCUIT_FAILURE_THRESHOLD = 5
    CIRCUIT_COOLDOWN_SECONDS = 60
    INLINE_REFERENCE_MAX_EDGE = 1600
    INLINE_REFERENCE_REENCODE_MIN_BYTES = 900_000
    INLINE_REFERENCE_JPEG_QUALITY = 94
    IMAGE_EDIT_REFERENCE_FILE_LIMIT = 8
    IMAGE_EDIT_MAX_ROUNDS = 3
    CANDIDATE_SELECTION_POLICY = repair_policy.CANDIDATE_SELECTION_POLICY
    IDENTITY_HARD_GATE_REASONS = repair_policy.IDENTITY_HARD_GATE_REASONS
    COMMERCIAL_HARD_GATE_REASONS = repair_policy.COMMERCIAL_HARD_GATE_REASONS
    LIGHTING_ONLY_REPAIR_REASONS = repair_policy.LIGHTING_ONLY_REPAIR_REASONS
    FINAL_POLISH_ONLY_REASONS = repair_policy.FINAL_POLISH_ONLY_REASONS
    CANDIDATE_REASON_PENALTIES = repair_policy.CANDIDATE_REASON_PENALTIES
    IMAGE_EDIT_REPAIR_SKIP_PREVIOUS_REASONS = repair_policy.IMAGE_EDIT_REPAIR_SKIP_PREVIOUS_REASONS
    QA_MAX_ATTEMPTS = GENERATION_QA_MAX_ATTEMPTS
    QA_RETRY_REASONS = GENERATION_QA_RETRY_REASONS

    def __init__(self) -> None:
        self._runtime_validation_ok = False
        self._consecutive_failures = 0
        self._circuit_open_until: float = 0

    @staticmethod
    def _base_url() -> str:
        return (settings.wenwen_api_base_url or "").rstrip("/")

    @classmethod
    def _origin_url(cls) -> str:
        parsed = urlparse(cls._base_url())
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return cls._base_url()

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = str(path or "").strip()
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized

    @classmethod
    def _generation_url(cls) -> str:
        return f"{cls._base_url()}{cls._normalize_path(settings.wenwen_image_generate_path)}"

    @classmethod
    def _image_edit_url(cls) -> str:
        return f"{cls._base_url()}{cls._normalize_path(settings.wenwen_image_edit_path)}"

    @classmethod
    def _native_generation_url(cls) -> str:
        return cls._native_generation_url_for_model(cls._effective_image_model())

    @classmethod
    def _native_generation_url_for_model(cls, model: str) -> str:
        template = str(settings.wenwen_native_image_generate_path_template or "").strip()
        if not template:
            template = "/v1beta/models/{model}:generateContent"
        path = template.replace("{model}", str(model or "").strip())
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{cls._origin_url()}{cls._normalize_path(path)}"

    @classmethod
    def _native_generation_request_url_for_model(cls, model: str) -> str:
        """Gemini format expects the API key in the query string, not Bearer auth."""
        url = cls._native_generation_url_for_model(model)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["key"] = [settings.wenwen_api_key]
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(query, doseq=True),
                parsed.fragment,
            )
        )

    @classmethod
    def _models_url(cls) -> str:
        return f"{cls._base_url()}{cls._normalize_path(settings.wenwen_models_path)}"

    @classmethod
    def _task_url(cls, task_id: str) -> str:
        template = str(settings.wenwen_task_path_template or "/tasks/{task_id}").strip()
        if "{task_id}" in template:
            path = template.replace("{task_id}", str(task_id))
        else:
            path = f"{template.rstrip('/')}/{task_id}"
        return f"{cls._base_url()}{cls._normalize_path(path)}"

    @classmethod
    def _supports_provider_task_submission(cls) -> bool:
        model = cls._effective_image_model().lower()
        return bool(model and not model.startswith("gemini"))

    @staticmethod
    def _effective_image_model() -> str:
        return str(settings.wenwen_image_model or "").strip()

    @classmethod
    def _effective_image_edit_model(cls) -> str:
        return str(settings.wenwen_image_edit_model or "").strip() or cls._effective_image_model()

    @staticmethod
    def _image_edit_uses_native_model(model: str | None) -> bool:
        lowered = str(model or "").strip().lower()
        return bool(lowered.startswith("gemini") or lowered.startswith("models/gemini") or "/gemini" in lowered)

    @staticmethod
    def _allowed_image_model(model: str) -> bool:
        allowed = settings.generation_allowed_image_model_list
        return not allowed or str(model or "").strip() in allowed

    @classmethod
    def _native_image_edit_model_candidates(cls, model: str) -> list[str]:
        candidate = str(model or "").strip()
        if candidate and cls._image_edit_uses_native_model(candidate) and cls._allowed_image_model(candidate):
            return [candidate]
        return []

    @classmethod
    def _image_edit_model_candidates(cls, model: str) -> list[str]:
        candidate = str(model or "").strip()
        if candidate and cls._allowed_image_model(candidate):
            return [candidate]
        return []

    @staticmethod
    def _is_model_unavailable_error(error: Exception) -> bool:
        text = str(error or "").strip().lower()
        return any(
            token in text
            for token in (
                "model_not_found",
                "no available channel",
                "not supported model for image generation",
                "wenwen_model_unavailable",
                "wenwen_image_edit_unavailable",
            )
        )

    @classmethod
    def _native_model_candidates(cls) -> list[str]:
        model = cls._effective_image_model()
        if model and cls._allowed_image_model(model):
            return [model]
        return []

    @staticmethod
    def _native_read_timeout(*, model_index: int, model_count: int) -> float:
        if not settings.is_vercel_runtime:
            return max(120.0, float(settings.wenwen_poll_timeout or 240))
        if model_count > 1:
            return 150.0 if model_index == 0 else 120.0
        return min(270.0, max(120.0, float(settings.wenwen_poll_timeout or 240)))

    @staticmethod
    def _image_edit_size(is_couple: bool) -> str:
        configured = settings.wenwen_image_edit_size_couple if is_couple else settings.wenwen_image_edit_size_single
        value = str(configured or "").strip()
        return value or "1152x1536"

    @staticmethod
    def _native_image_size() -> str:
        value = str(getattr(settings, "wenwen_native_image_size", "") or "").strip()
        return value or "4K"

    @staticmethod
    def _image_edit_candidate_count() -> int:
        try:
            configured = int(getattr(settings, "wenwen_image_edit_candidate_count", 2) or 1)
        except Exception:
            configured = 1
        return max(1, min(4, configured))

    @staticmethod
    def _identity_edit_required(user_images: list[str] | None) -> bool:
        has_identity_refs = any(str(url or "").strip() for url in (user_images or []))
        return bool(has_identity_refs)

    @staticmethod
    def _make_identity_closeup(content: bytes, content_type: str) -> tuple[bytes, str] | None:
        """Create a high-detail face/upper-body reference to improve identity anchoring."""
        if Image is None or not content:
            return None
        try:
            with Image.open(BytesIO(content)) as image:
                rgb = image.convert("RGB")
                width, height = rgb.size
                if width < 80 or height < 80:
                    return None

                if height >= width:
                    left = int(width * 0.18)
                    right = int(width * 0.82)
                    top = int(height * 0.03)
                    bottom = int(height * 0.52)
                else:
                    crop_width = int(width * 0.46)
                    left = max(0, (width - crop_width) // 2)
                    right = min(width, left + crop_width)
                    top = int(height * 0.04)
                    bottom = int(height * 0.72)

                cropped = rgb.crop((left, top, max(left + 1, right), max(top + 1, bottom)))
                largest_edge = max(cropped.size)
                if largest_edge > 1400:
                    scale = 1400 / float(largest_edge)
                    next_size = (max(1, round(cropped.size[0] * scale)), max(1, round(cropped.size[1] * scale)))
                    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
                    cropped = cropped.resize(next_size, resampling)

                buffer = BytesIO()
                cropped.save(buffer, format="JPEG", quality=95, optimize=True, progressive=True)
                return buffer.getvalue(), "image/jpeg"
        except Exception as exc:
            logger.warning("Failed to create identity close-up reference: %s", exc)
            return None

    async def _build_image_edit_reference_files(
        self,
        identity_refs: list[str],
        style_refs: list[str] | None = None,
        identity_reference_pack: dict | None = None,
        current_result_refs: list[str] | None = None,
        round_number: int = 1,
        qa_reasons: list[str] | None = None,
    ) -> list[tuple[str, tuple[str, bytes, str]]]:
        """Put identity refs first, current canvas next, then style refs."""
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        identity_source_refs = list(identity_refs[:2])
        extra_refs = list(style_refs or [])
        max_files = max(1, int(self.IMAGE_EDIT_REFERENCE_FILE_LIMIT))
        pack_files = await self._build_identity_pack_reference_files(
            identity_reference_pack, max_files=max_files, round_number=round_number, qa_reasons=qa_reasons
        )
        if pack_files:
            files.extend(pack_files)
            seen_pack_urls = self._identity_pack_reference_urls(identity_reference_pack)
            identity_source_refs = [
                ref
                for ref in identity_source_refs
                if str(ref or "").strip() and str(ref or "").strip() not in seen_pack_urls
            ]

        for index, ref in enumerate(identity_source_refs, start=1):
            if len(files) >= max_files:
                break
            content, content_type = await self._fetch_remote_image_bytes(ref)
            ext = self._ext_from_content_type(content_type)
            files.append(("image", (f"identity_full_{index}.{ext}", content, content_type)))

            closeup = self._make_identity_closeup(content, content_type)
            if closeup and len(files) < max_files:
                closeup_content, closeup_type = closeup
                files.append(("image", (f"identity_closeup_{index}.jpg", closeup_content, closeup_type)))

        for index, ref in enumerate(current_result_refs or [], start=1):
            if len(files) >= max_files:
                break
            value = str(ref or "").strip()
            if not value:
                continue
            content, content_type = await self._fetch_remote_image_bytes(value)
            ext = self._ext_from_content_type(content_type)
            files.append(("image", (f"current_candidate_{index}.{ext}", content, content_type)))

        for index, ref in enumerate(extra_refs, start=1):
            if len(files) >= max_files:
                break
            content, content_type = await self._fetch_remote_image_bytes(ref)
            ext = self._ext_from_content_type(content_type)
            files.append(("image", (f"style_reference_{index}.{ext}", content, content_type)))

        return files[:max_files]

    @staticmethod
    def _identity_pack_subjects(identity_reference_pack: dict | None) -> list[dict[str, Any]]:
        if not isinstance(identity_reference_pack, dict):
            return []
        subjects = identity_reference_pack.get("subjects")
        if not isinstance(subjects, list):
            return []
        return [subject for subject in subjects if isinstance(subject, dict)]

    @classmethod
    def _identity_pack_reference_urls(cls, identity_reference_pack: dict | None) -> set[str]:
        return {url for _label, url in cls._identity_pack_reference_url_list(identity_reference_pack)}

    @classmethod
    def _identity_pack_reference_url_list(cls, identity_reference_pack: dict | None) -> list[tuple[str, str]]:
        refs: list[tuple[str, str]] = []
        for subject in cls._identity_pack_subjects(identity_reference_pack):
            role = str(subject.get("role") or subject.get("identity_label") or "subject").strip() or "subject"
            for key, kind in (
                ("original_url", "original portrait"),
                ("face_crop_url", "face crop"),
                ("upper_body_crop_url", "upper-body crop"),
            ):
                value = str(subject.get(key) or "").strip()
                if value:
                    refs.append((f"{role} {kind}", value))
        return refs

    async def _build_identity_pack_reference_files(
        self,
        identity_reference_pack: dict | None,
        *,
        max_files: int,
        round_number: int = 1,
        qa_reasons: list[str] | None = None,
    ) -> list[tuple[str, tuple[str, bytes, str]]]:
        subjects = self._identity_pack_subjects(identity_reference_pack)
        if not subjects:
            return []

        is_identity_repair = round_number >= 2 and bool(qa_reasons) and any(
            r in {"identity_mismatch", "identity_swap"} for r in (qa_reasons or [])
        )

        prioritized_refs: list[tuple[str, str]] = []
        is_couple_pack = len(subjects) >= 2
        for subject_index, subject in enumerate(subjects[:2], start=1):
            role = str(subject.get("role") or f"subject_{subject_index}").strip() or f"subject_{subject_index}"

            if is_identity_repair:
                # Progressive: face crops first (weight), then original, skip upper_body
                face_key = "face_crop_url"
                face_val = str(subject.get(face_key) or "").strip()
                if face_val:
                    prioritized_refs.append((f"{role}_face_crop_identity_lock_{subject_index}", face_val))
                original_val = str(subject.get("original_url") or "").strip()
                if original_val:
                    prioritized_refs.append((f"{role}_original_{subject_index}", original_val))
            else:
                # Standard: original first, then face crop
                for kind in ("original", "face_crop"):
                    key = "original_url" if kind == "original" else "face_crop_url"
                    value = str(subject.get(key) or "").strip()
                    if value:
                        prioritized_refs.append((f"{role}_{kind}_{subject_index}", value))
                if not is_couple_pack:
                    value = str(subject.get("upper_body_crop_url") or "").strip()
                    if value:
                        prioritized_refs.append((f"{role}_upper_body_{subject_index}", value))

        files: list[tuple[str, tuple[str, bytes, str]]] = []
        seen: set[str] = set()
        for label, ref in prioritized_refs:
            if len(files) >= max_files:
                break
            if ref in seen:
                continue
            seen.add(ref)
            content, content_type = await self._fetch_remote_image_bytes(ref)
            ext = self._ext_from_content_type(content_type)
            files.append(("image", (f"{label}.{ext}", content, content_type)))
        return files

    @classmethod
    def _identity_pack_prompt_note(cls, identity_reference_pack: dict | None) -> str:
        subjects = cls._identity_pack_subjects(identity_reference_pack)
        if not subjects:
            return ""

        descriptors: list[str] = []
        for subject in subjects[:2]:
            label = str(subject.get("identity_label") or "").strip()
            role = str(subject.get("role") or "").strip()
            if label and role:
                descriptors.append(f"{label}={role}")
            elif label:
                descriptors.append(label)
            elif role:
                descriptors.append(role)

        if not descriptors:
            return ""
        if len(descriptors) >= 2:
            return (
                "Identity reference pack role order: "
                f"{', '.join(descriptors)}. Preserve each role separately; never swap, merge, or average faces. "
            )
        return (
            "Identity reference pack contains the original portrait, face crop, and upper-body crop for the subject. "
            "Use these as strict identity anchors. "
        )

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.wenwen_api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _native_headers() -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def validate_runtime_requirements(self, *, force: bool = False) -> None:
        if self._runtime_validation_ok and not force:
            return
        errors: list[str] = []
        if not settings.wenwen_api_key:
            errors.append("WENWEN_API_KEY is required")
        if not settings.wenwen_api_base_url:
            errors.append("WENWEN_API_BASE_URL is required")
        if not settings.wenwen_image_edit_path:
            errors.append("WENWEN_IMAGE_EDIT_PATH is required")
        if not self._effective_image_edit_model():
            errors.append("WENWEN_IMAGE_EDIT_MODEL or WENWEN_IMAGE_MODEL is required")
        if self._effective_image_edit_model() and not settings.generation_image_model_allowed(
            self._effective_image_edit_model()
        ):
            errors.append(f"WENWEN_IMAGE_EDIT_MODEL is not allowed: {self._effective_image_edit_model()}")
        if self._effective_image_model() and not settings.generation_image_model_allowed(
            self._effective_image_model()
        ):
            errors.append(f"WENWEN_IMAGE_MODEL is not allowed: {self._effective_image_model()}")
        fallback_models = [
            item.strip()
            for item in (
                f"{settings.wenwen_image_fallback_models},{settings.wenwen_image_edit_fallback_models}"
            ).split(",")
            if item.strip()
        ]
        if fallback_models:
            errors.append("WENWEN image fallback model settings must be empty; run explicit model comparisons instead")
        if errors:
            raise ValueError("; ".join(errors))
        self._runtime_validation_ok = True

    async def ping_runtime(self) -> tuple[bool, str]:
        self.validate_runtime_requirements(force=True)
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            response = await client.get(self._models_url(), headers=self._headers())
        if response.status_code in {200, 404, 405}:
            return True, f"http_{response.status_code}"
        if response.status_code in {401, 403}:
            raise RuntimeError(
                f"wenwen_auth_failed:{response.status_code} "
                f"(base_url={self._base_url()}, model={self._effective_image_model()})"
            )
        response.raise_for_status()
        return True, "ok"

    async def probe_queue_capability(self) -> tuple[bool, str]:
        await self.ping_runtime()
        return True, "not_required_for_wenwen"

    @staticmethod
    def _is_localish_url(value: str) -> bool:
        try:
            parsed = urlparse(str(value or "").strip())
        except Exception:
            return False
        return (parsed.hostname or "").lower() in {"127.0.0.1", "localhost"}

    @staticmethod
    def _normalize_content_type(content_type: str) -> str:
        normalized = (content_type or "image/jpeg").split(";", 1)[0].strip().lower()
        return normalized or "image/jpeg"

    @classmethod
    def _prepare_inline_image_reference(cls, content: bytes, content_type: str) -> tuple[bytes, str]:
        normalized_type = cls._normalize_content_type(content_type)
        if not content or Image is None:
            return content, normalized_type

        try:
            with Image.open(BytesIO(content)) as image:
                width, height = image.size
                largest_edge = max(width, height)
                needs_resize = largest_edge > cls.INLINE_REFERENCE_MAX_EDGE
                needs_reencode = (
                    normalized_type not in {"image/jpeg", "image/jpg"}
                    or len(content) >= cls.INLINE_REFERENCE_REENCODE_MIN_BYTES
                )
                if not needs_resize and not needs_reencode:
                    return content, normalized_type

                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                    background.alpha_composite(rgba)
                    prepared = background.convert("RGB")
                else:
                    prepared = image.convert("RGB")

                if needs_resize:
                    scale = cls.INLINE_REFERENCE_MAX_EDGE / float(largest_edge)
                    next_size = (max(1, round(width * scale)), max(1, round(height * scale)))
                    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
                    prepared = prepared.resize(next_size, resampling)

                buffer = BytesIO()
                prepared.save(
                    buffer,
                    format="JPEG",
                    quality=cls.INLINE_REFERENCE_JPEG_QUALITY,
                    optimize=True,
                    progressive=True,
                )
                encoded = buffer.getvalue()
                if not needs_resize and normalized_type in {"image/jpeg", "image/jpg"} and len(encoded) >= len(content):
                    return content, normalized_type
                return encoded, "image/jpeg"
        except Exception as exc:
            logger.warning("Failed to prepare inline reference image, using original bytes: %s", exc)
            return content, normalized_type

    async def _coerce_remote_image_ref(self, image_url: str) -> str:
        raw = str(image_url or "").strip()
        if not raw:
            return raw
        if raw.startswith("data:"):
            return raw
        if not raw.startswith(("http://", "https://")):
            return raw
        content, content_type = await self._fetch_remote_image_bytes(raw)
        encoded = base64.b64encode(content).decode("utf-8")
        return f"data:{content_type};base64,{encoded}"

    async def _fetch_remote_image_bytes(self, image_url: str) -> tuple[bytes, str]:
        raw = str(image_url or "").strip()
        if raw.startswith("data:") and ";base64," in raw:
            header, encoded = raw.split(",", 1)
            content_type = header[5:].split(";", 1)[0] or "image/jpeg"
            content = base64.b64decode(encoded)
            return self._prepare_inline_image_reference(content, content_type)
        if not raw.startswith(("http://", "https://")):
            raise ValueError("image_reference_must_be_remote_or_data_url")
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, trust_env=False) as client:
            response = await client.get(raw)
            response.raise_for_status()
            content_type = response.headers.get("content-type") or "image/jpeg"
            content, content_type = self._prepare_inline_image_reference(response.content, content_type)
            return content, content_type

    @staticmethod
    def _data_url_to_inline_part(data_url: str) -> dict[str, Any]:
        value = str(data_url or "").strip()
        if not value.startswith("data:") or ";base64," not in value:
            raise ValueError("image_reference_must_be_data_url")
        header, encoded = value.split(",", 1)
        mime_type = header[5:].split(";", 1)[0] or "image/jpeg"
        return {
            "inline_data": {
                "mime_type": mime_type,
                "data": encoded,
            }
        }

    @staticmethod
    def _walk_values(payload: Any, *, current_key: str = "") -> list[tuple[str, Any]]:
        items: list[tuple[str, Any]] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                items.append((str(key), value))
                items.extend(GenerationProviderWorkflow._walk_values(value, current_key=str(key)))
        elif isinstance(payload, list):
            for value in payload:
                items.extend(GenerationProviderWorkflow._walk_values(value, current_key=current_key))
        return items

    @classmethod
    def _extract_task_id(cls, payload: Any) -> str | None:
        for key, value in cls._walk_values(payload):
            lowered = key.lower()
            if lowered in {"id", "task_id", "taskid", "job_id", "jobid"} and isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @classmethod
    def _extract_status_value(cls, payload: Any) -> str | None:
        for key, value in cls._walk_values(payload):
            lowered = key.lower()
            if lowered in {"status", "state", "task_status", "taskstatus", "jobstatus"} and value is not None:
                return str(value).strip()
        return None

    @staticmethod
    def _is_url_candidate(key: str, value: str) -> bool:
        if not isinstance(value, str) or not value.startswith(("http://", "https://")):
            return False
        lowered_key = key.lower()
        if any(hint in lowered_key for hint in ("url", "result", "image", "output", "file")):
            return True
        parsed = urlparse(value)
        ext = parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
        if ext in {"jpg", "jpeg", "png", "webp", "gif", "bmp"}:
            return True
        query_filename = (parse_qs(parsed.query).get("filename") or [None])[0]
        return bool(query_filename and "." in query_filename)

    @classmethod
    def _extract_output_urls(cls, payload: Any) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for key, value in cls._walk_values(payload):
            if isinstance(value, str) and cls._is_url_candidate(key, value):
                if value not in seen:
                    seen.add(value)
                    urls.append(value)
            if key.lower() == "results" and isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.startswith(("http://", "https://")) and item not in seen:
                        seen.add(item)
                        urls.append(item)
        return urls

    async def _submit_provider_task(
        self,
        *,
        template: Any,
        user_images: list[str],
        subject_count: int | None,
        couple_flow: str | None,
        prompt_override: str | None,
        global_style_text: str | None,
        scene_text: str | None,
        outfit_text: str | None,
        identity_reference_pack: dict | None = None,
        scene_image_url: str | None,
        clothing_image_url: str | None,
    ) -> tuple[str | None, list[str], dict[str, Any], str, str]:
        payload, prompt_text, negative_prompt = await self._build_payload(
            template=template,
            user_images=list(user_images or []),
            subject_count=subject_count,
            prompt_override=prompt_override,
            global_style_text=global_style_text,
            scene_text=scene_text,
            outfit_text=outfit_text,
            identity_reference_pack=identity_reference_pack,
            scene_image_url=scene_image_url,
            clothing_image_url=clothing_image_url,
            couple_flow=couple_flow,
        )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=45.0, write=30.0, pool=10.0),
            follow_redirects=True,
            trust_env=False,
        ) as client:
            response = await client.post(self._generation_url(), json=payload, headers=self._headers())
        if response.status_code in {401, 403}:
            raise RuntimeError(f"wenwen_auth_failed:{response.status_code}")
        if response.status_code in {402, 429}:
            raise RuntimeError(f"wenwen_quota_rejected:{response.status_code}:{response.text[:200]}")
        response.raise_for_status()

        submission = response.json()
        task_id = self._extract_task_id(submission)
        output_urls = self._extract_output_urls(submission)
        return task_id, output_urls, payload, prompt_text, negative_prompt

    @staticmethod
    def _order_source_images(order: Order) -> list[str]:
        source = order.source_image_urls
        if isinstance(source, dict):
            images = source.get("images")
            if isinstance(images, list):
                return [str(url) for url in images if str(url or "").strip()]
            values: list[str] = []
            for value in source.values():
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())
            return values
        if isinstance(source, list):
            return [str(url) for url in source if str(url or "").strip()]
        return []

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _extract_error_message(cls, payload: Any) -> str | None:
        for key, value in cls._walk_values(payload):
            lowered = key.lower()
            if lowered in {"error", "message", "detail", "reason"} and value is not None:
                if isinstance(value, str) and value.strip():
                    return value.strip()[:500]
                if isinstance(value, (dict, list)):
                    return str(value)[:500]
        return None

    @classmethod
    def _should_retry_qa(cls, reasons: list[str], attempt: int) -> bool:
        return should_retry_qa(reasons, attempt, max_attempts=cls.QA_MAX_ATTEMPTS)

    @staticmethod
    def _vision_error_only(reasons: list[str] | tuple[str, ...] | None) -> bool:
        normalized = {str(reason or "").strip() for reason in (reasons or []) if str(reason or "").strip()}
        return normalized == {"vision_error"}

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    @staticmethod
    def _safe_vision_retry_limit() -> int:
        try:
            return max(1, int(settings.qa_vision_error_retry_attempts or 1))
        except Exception:
            return 3

    async def _complete_provider_urls(
        self,
        order_uuid: uuid.UUID,
        *,
        provider_urls: list[str],
        user_images: list[str],
        subject_count: int | None,
        couple_flow: str | None,
        qa_attempt_count: int,
    ) -> None:
        if not provider_urls:
            raise RuntimeError("wenwen_outputs_missing")
        is_couple = bool(subject_count and int(subject_count) >= 2)
        primary_image_url = provider_urls[0]
        qa_verdict = await output_verdict(
            primary_image_url,
            is_couple=is_couple,
            source_image_urls=[str(url) for url in user_images if url],
        )
        qa_ok = bool(qa_verdict.get("passed"))
        qa_reasons = list(qa_verdict.get("reasons") or [])
        qa_issues = [issue for issue in (qa_verdict.get("issues") or []) if isinstance(issue, dict)]
        if not qa_ok:
            await self._record_qa_failure(
                order_uuid,
                attempt=qa_attempt_count,
                reasons=qa_reasons,
                candidate_url=primary_image_url,
                issues=qa_issues,
            )
            raise ValueError(f"QA failed: {','.join(qa_reasons)}")

        delivered_urls = await self._persist_outputs_to_storage(provider_urls, order_uuid)
        if not delivered_urls:
            raise RuntimeError("wenwen_outputs_missing")
        await self._complete_order(
            order_uuid,
            delivered_urls=delivered_urls,
            provider_urls=provider_urls,
            qa_attempt_count=qa_attempt_count,
            is_couple=is_couple,
            subject_count=subject_count,
            couple_flow=couple_flow,
        )

    async def _record_native_raw_outputs(
        self,
        order_uuid: uuid.UUID,
        *,
        delivered_urls: list[str],
        provider_payload_keys: list[str],
        qa_attempt_count: int,
    ) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            debug = dict(params.get("debug")) if isinstance(params.get("debug"), dict) else {}
            debug.update(
                {
                    "native_raw_output_recorded_at": self._utc_now_iso(),
                    "native_raw_output_count": len(delivered_urls),
                    "wenwen_model": self._effective_image_model(),
                    "wenwen_configured_model": settings.wenwen_image_model,
                    "wenwen_submit_payload_keys": sorted(provider_payload_keys),
                }
            )
            params["debug"] = debug
            params["native_raw_output_urls"] = list(delivered_urls)
            params["native_raw_output_status"] = "stored"
            params["qa_retry_pending"] = False
            params["qa_retry_in_progress"] = False
            params["qa_attempt_count"] = int(qa_attempt_count)
            order.generation_params = params
            await db.commit()

    async def _mark_qa_retry_pending(
        self,
        order_uuid: uuid.UUID,
        *,
        attempt: int,
        reasons: list[str],
        candidate_url: str,
        retry_kind: str = "generation_repair",
    ) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            params = merge_generation_stage(params, "repairing", detail=retry_kind)
            debug = dict(params.get("debug")) if isinstance(params.get("debug"), dict) else {}
            retry_history = debug.get("qa_retry_history") if isinstance(debug.get("qa_retry_history"), list) else []
            retry_history.append(
                {
                    "attempt": int(attempt),
                    "reasons": list(reasons),
                    "candidate_url": candidate_url,
                    "retry_kind": str(retry_kind or "generation_repair"),
                    "queued_at": self._utc_now_iso(),
                }
            )
            debug["qa_retry_history"] = retry_history[-8:]
            params["debug"] = debug
            params["qa_retry_pending"] = True
            params["qa_retry_in_progress"] = False
            params["qa_retry_kind"] = str(retry_kind or "generation_repair")
            params["qa_retry_candidate_url"] = candidate_url
            params["qa_retry_uses_existing_candidate"] = str(retry_kind or "") == "vision_recheck"
            params["qa_retry_next_attempt"] = int(attempt) + 1
            params["qa_retry_max_attempts"] = (
                self._safe_vision_retry_limit()
                if str(retry_kind or "") == "vision_recheck"
                else self.QA_MAX_ATTEMPTS
            )
            params["automatic_repair_extra_charge"] = 0
            params["qa_last_reasons"] = list(reasons)
            params["qa_attempt_count"] = int(attempt)
            params["native_raw_output_status"] = "qa_rejected_retry_pending"
            order.status = OrderStatus.GENERATING
            order.error_message = None
            order.generation_params = params
            await db.commit()

    async def _mark_qa_retry_started(self, order_uuid: uuid.UUID) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            params = merge_generation_stage(params, "repairing", detail="qa_retry_started")
            params["qa_retry_pending"] = False
            params["qa_retry_in_progress"] = True
            params["qa_retry_started_at"] = self._utc_now_iso()
            order.status = OrderStatus.GENERATING
            order.error_message = None
            order.generation_params = params
            await db.commit()

    @staticmethod
    def _status_terminal_success(value: str | None, *, has_outputs: bool) -> bool:
        lowered = str(value or "").strip().lower()
        if has_outputs and not lowered:
            return True
        return lowered in {"done", "success", "succeed", "completed", "complete", "finished"}

    @staticmethod
    def _status_terminal_failure(value: str | None) -> bool:
        lowered = str(value or "").strip().lower()
        return lowered in {"failed", "error", "cancelled", "canceled", "timeout", "terminated"}

    @staticmethod
    def _build_size(is_couple: bool) -> str:
        configured = settings.wenwen_image_size_couple if is_couple else settings.wenwen_image_size_single
        return resolve_generation_aspect_ratio(configured, is_couple=is_couple)

    async def _build_subject_hints(self, user_images: list[str]) -> list[str]:
        hints: list[str] = []
        for index, image_url in enumerate((user_images or [])[:2]):
            try:
                description = await llm_service.analyze_image_prompt(image_url, "subject")
            except Exception as exc:
                logger.warning("Wenwen subject analysis failed for image %s: %s", index + 1, exc)
                continue
            cleaned = str(description or "").strip()
            if cleaned:
                hints.append(f"Subject {index + 1}: {cleaned}")
        return hints

    async def _build_payload(
        self,
        *,
        template: Any,
        user_images: list[str],
        subject_count: int | None,
        prompt_override: str | None,
        global_style_text: str | None,
        scene_text: str | None,
        outfit_text: str | None,
        identity_reference_pack: dict | None = None,
        scene_image_url: str | None,
        clothing_image_url: str | None,
        couple_flow: str | None,
        prompt_enrichment: bool = True,
    ) -> tuple[dict[str, Any], str, str]:
        is_couple = bool(subject_count and int(subject_count) >= 2)
        prompt_text = build_studio_generation_prompt(
            template=template,
            prompt_override=prompt_override,
            global_style_text=global_style_text,
            scene_text=scene_text,
            outfit_text=outfit_text,
            is_couple=is_couple,
        )
        negative_prompt = build_generation_negative_prompt(is_couple=is_couple)

        if prompt_enrichment:
            prompt_text = await llm_service.optimize_generation_prompt(prompt_text, is_couple=is_couple)
            subject_hints = await self._build_subject_hints(user_images)
            if subject_hints:
                prompt_text = f"{prompt_text} Identity guidance: {' '.join(subject_hints)}."
        pack_note = self._identity_pack_prompt_note(identity_reference_pack)
        if pack_note:
            prompt_text = f"{prompt_text} {pack_note}"
        prompt_text = f"{prompt_text} {get_studio_guardrails(is_couple=is_couple)}."

        refs: list[str] = []
        for candidate in [*(user_images or []), scene_image_url or "", clothing_image_url or ""]:
            value = str(candidate or "").strip()
            if not value:
                continue
            refs.append(await self._coerce_remote_image_ref(value))

        payload: dict[str, Any] = {
            "model": self._effective_image_model(),
            "prompt": prompt_text,
            "size": self._build_size(is_couple),
            "n": 1,
            "negative_prompt": negative_prompt,
        }
        if couple_flow:
            payload["couple_flow"] = couple_flow
        if refs:
            payload["images"] = refs
            payload["image_urls"] = refs
            payload["input_images"] = refs
            payload["image"] = refs[0]
        return payload, prompt_text, negative_prompt

    async def _build_native_payload(
        self,
        *,
        template: Any,
        user_images: list[str],
        subject_count: int | None,
        prompt_override: str | None,
        global_style_text: str | None,
        scene_text: str | None,
        outfit_text: str | None,
        identity_reference_pack: dict | None = None,
        scene_image_url: str | None,
        clothing_image_url: str | None,
        couple_flow: str | None,
        prompt_enrichment: bool = True,
    ) -> tuple[dict[str, Any], str, str]:
        payload, prompt_text, negative_prompt = await self._build_payload(
            template=template,
            user_images=user_images,
            subject_count=subject_count,
            prompt_override=prompt_override,
            global_style_text=global_style_text,
            scene_text=scene_text,
            outfit_text=outfit_text,
            identity_reference_pack=identity_reference_pack,
            scene_image_url=scene_image_url,
            clothing_image_url=clothing_image_url,
            couple_flow=couple_flow,
            prompt_enrichment=prompt_enrichment,
        )
        native_payload = await self._native_payload_from_provider_payload(
            payload,
            prompt_text=prompt_text,
            negative_prompt=negative_prompt,
            subject_count=subject_count,
        )
        return native_payload, prompt_text, negative_prompt

    async def _native_payload_from_provider_payload(
        self,
        payload: dict[str, Any],
        *,
        prompt_text: str,
        negative_prompt: str,
        subject_count: int | None,
    ) -> dict[str, Any]:
        refs = list(payload.get("images") or [])
        is_couple = bool(subject_count and int(subject_count) >= 2)
        reference_intro = (
            "Reference identity order: image 1 is the bride/subject A, image 2 is the groom/subject B. "
            "Preserve both identities separately and do not swap, average, or replace faces."
            if is_couple
            else "Reference identity: image 1 is the subject. Preserve this exact identity and do not replace the face."
        )
        parts: list[dict[str, Any]] = [
            {"text": f"{prompt_text}\n{reference_intro}\nNegative prompt: {negative_prompt}"}
        ]
        for index, ref in enumerate(refs[:3]):
            if index < 2:
                parts.append({"text": f"Identity reference image {index + 1}:"})
            coerced = await self._coerce_remote_image_ref(ref)
            parts.append(self._data_url_to_inline_part(coerced))
        native_payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "temperature": 0.8,
                "imageConfig": {
                    "aspectRatio": self._build_size(is_couple),
                    "imageSize": self._native_image_size(),
                },
            },
        }
        return native_payload

    @staticmethod
    def _native_payload_without_candidate_count(payload: dict[str, Any]) -> dict[str, Any]:
        generation_config = dict(payload.get("generationConfig") or {})
        generation_config.pop("candidateCount", None)
        return {**payload, "generationConfig": generation_config}

    def _native_image_edit_reference_entries(
        self,
        *,
        identity_refs: list[str],
        style_refs: list[str],
        current_result_refs: list[str],
        identity_reference_pack: dict | None,
        include_previous_result: bool,
    ) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(label: str, ref: str) -> None:
            value = str(ref or "").strip()
            if not value or value in seen or len(entries) >= self.IMAGE_EDIT_REFERENCE_FILE_LIMIT:
                return
            seen.add(value)
            entries.append((label, value))

        for label, ref in self._identity_pack_reference_url_list(identity_reference_pack):
            add(f"Identity anchor - {label}", ref)
        for index, ref in enumerate(identity_refs[:2], start=1):
            add(f"Identity full source image {index}", ref)
        if include_previous_result:
            for index, ref in enumerate(current_result_refs, start=1):
                add(f"Current candidate canvas {index}", ref)
        for index, ref in enumerate(style_refs, start=1):
            add(f"Style or scene reference image {index}", ref)
        return entries

    async def _build_native_image_edit_payload(
        self,
        *,
        edit_prompt: str,
        negative_prompt: str,
        identity_refs: list[str],
        style_refs: list[str],
        current_result_refs: list[str],
        identity_reference_pack: dict | None,
        include_previous_result: bool,
        is_couple: bool,
    ) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        reference_entries = self._native_image_edit_reference_entries(
            identity_refs=identity_refs,
            style_refs=style_refs,
            current_result_refs=current_result_refs,
            identity_reference_pack=identity_reference_pack,
            include_previous_result=include_previous_result,
        )
        if not reference_entries:
            raise RuntimeError("wenwen_native_image_edit_identity_refs_missing")

        role_intro = (
            "Reference role order: identity A is the bride/source person 1 and identity B is the groom/source person 2. "
            "Preserve both identities separately; never swap, merge, average, or beautify faces into generic people."
            if is_couple
            else "Reference role order: identity A is the single source person. Preserve the exact real identity."
        )
        parts: list[dict[str, Any]] = [
            {
                "text": (
                    f"{edit_prompt}\n\n"
                    "NATIVE GEMINI IMAGE-EDIT MODE: use every attached image as a reference for editing. "
                    "The identity anchor images override style, pose, outfit, and scene references. "
                    "Do not invent a new face, do not perform pure text-to-image generation, and do not use the "
                    "current candidate as identity evidence when identity QA failed.\n"
                    f"{role_intro}\n"
                    f"Negative prompt: {negative_prompt}"
                )
            }
        ]
        for index, (label, ref) in enumerate(reference_entries, start=1):
            parts.append({"text": f"Reference image {index}: {label}."})
            coerced = await self._coerce_remote_image_ref(ref)
            parts.append(self._data_url_to_inline_part(coerced))

        generation_config: dict[str, Any] = {
            "responseModalities": ["TEXT", "IMAGE"],
            "temperature": 0.72,
            "imageConfig": {
                "aspectRatio": self._build_size(is_couple),
                "imageSize": self._native_image_size(),
            },
        }
        candidate_count = self._image_edit_candidate_count()
        if candidate_count > 1:
            generation_config["candidateCount"] = candidate_count
        return {"contents": [{"role": "user", "parts": parts}], "generationConfig": generation_config}, reference_entries

    @staticmethod
    def _ext_from_content_type(content_type: str) -> str:
        lowered = (content_type or "").lower()
        if "jpeg" in lowered or "jpg" in lowered:
            return "jpg"
        if "png" in lowered:
            return "png"
        if "webp" in lowered:
            return "webp"
        if "gif" in lowered:
            return "gif"
        return "bin"

    async def _persist_outputs_to_storage(self, output_urls: list[str], order_uuid: uuid.UUID) -> list[str]:
        public_urls: list[str] = []
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, trust_env=False) as client:
            for index, url in enumerate(output_urls):
                response = await client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type") or "application/octet-stream"
                ext = self._ext_from_content_type(content_type)
                filename = f"order_{order_uuid}_media_{index + 1}.{ext}"
                public_url = await asyncio.to_thread(
                    storage_service.upload_file,
                    BytesIO(response.content),
                    filename,
                    content_type,
                    "generated",
                )
                public_urls.append(public_url)
        return public_urls

    async def _persist_binary_outputs_to_storage(
        self,
        outputs: list[tuple[bytes, str]],
        order_uuid: uuid.UUID,
    ) -> list[str]:
        public_urls: list[str] = []
        for index, (file_bytes, content_type) in enumerate(outputs):
            mime_type = self._normalize_content_type(content_type or "image/png")
            ext = mimetypes.guess_extension(mime_type) or ".png"
            filename = f"order_{order_uuid}_media_{index + 1}{ext}"
            public_url = await asyncio.to_thread(
                storage_service.upload_file,
                BytesIO(file_bytes),
                filename,
                mime_type,
                "generated",
            )
            public_urls.append(public_url)
        return public_urls

    async def _persist_inline_outputs_to_storage(
        self,
        parts: list[dict[str, Any]],
        order_uuid: uuid.UUID,
        *,
        filename_prefix: str | None = None,
    ) -> list[str]:
        public_urls: list[str] = []
        for index, part in enumerate(parts):
            inline = part.get("inlineData") if isinstance(part, dict) else None
            if not isinstance(inline, dict):
                inline = part.get("inline_data") if isinstance(part, dict) else None
            if not isinstance(inline, dict):
                continue
            data = inline.get("data")
            if not isinstance(data, str) or not data.strip():
                continue
            mime_type = str(inline.get("mimeType") or inline.get("mime_type") or "image/png")
            ext = mimetypes.guess_extension(mime_type.split(";")[0].strip()) or ".png"
            prefix = filename_prefix or f"order_{order_uuid}_media"
            filename = f"{prefix}_{index + 1}{ext}"
            file_bytes = base64.b64decode(data)
            public_url = await asyncio.to_thread(
                storage_service.upload_file,
                BytesIO(file_bytes),
                filename,
                mime_type,
                "generated",
            )
            public_urls.append(public_url)
        return public_urls

    async def _persist_native_candidate_outputs_to_storage(
        self,
        submission: dict[str, Any],
        order_uuid: uuid.UUID,
    ) -> list[str]:
        if not isinstance(submission, dict):
            return []
        raw_candidates = submission.get("candidates")
        candidates = raw_candidates if isinstance(raw_candidates, list) else []
        if not candidates:
            content = submission.get("content") if isinstance(submission.get("content"), dict) else {}
            if content:
                candidates = [{"content": content}]

        delivered_urls: list[str] = []
        for candidate_index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
            parts = content.get("parts") if isinstance(content.get("parts"), list) else []
            urls = await self._persist_inline_outputs_to_storage(
                parts,
                order_uuid,
                filename_prefix=f"order_{order_uuid}_native_candidate_{candidate_index + 1}_media",
            )
            if len(candidates) <= 1:
                delivered_urls.extend(urls)
            elif urls:
                delivered_urls.append(urls[0])
        return delivered_urls

    @classmethod
    def _extract_image_edit_binary_outputs(cls, payload: Any) -> list[tuple[bytes, str]]:
        outputs: list[tuple[bytes, str]] = []
        if not isinstance(payload, dict):
            return outputs
        for item in payload.get("data") or payload.get("images") or []:
            if not isinstance(item, dict):
                continue
            encoded = item.get("b64_json") or item.get("base64") or item.get("image_base64")
            if not isinstance(encoded, str) or not encoded.strip():
                continue
            content_type = "image/png"
            value = encoded.strip()
            if value.startswith("data:") and ";base64," in value:
                header, value = value.split(",", 1)
                content_type = header[5:].split(";", 1)[0] or content_type
            try:
                outputs.append((base64.b64decode(value), content_type))
            except Exception:
                logger.warning("Skipping invalid image edit base64 output")
        return outputs

    @staticmethod
    def _image_edit_round_stage(round_number: int) -> str:
        return repair_policy.image_edit_round_stage(round_number)

    @classmethod
    def _should_include_previous_edit_result(cls, reasons: list[str]) -> bool:
        return repair_policy.should_include_previous_edit_result(reasons)

    @classmethod
    def _is_lighting_only_repair(cls, reasons: list[str], *, round_number: int) -> bool:
        return repair_policy.is_lighting_only_repair(reasons, round_number=round_number)

    @classmethod
    def _can_enter_final_polish_round(cls, reasons: list[str]) -> bool:
        return repair_policy.can_enter_final_polish_round(reasons)

    @classmethod
    def _image_edit_repair_mode(cls, *, round_number: int, qa_reasons: list[str]) -> str:
        return repair_policy.image_edit_repair_mode(round_number=round_number, qa_reasons=qa_reasons)

    @staticmethod
    def _repair_focus_from_reasons(reasons: list[str], *, is_couple: bool) -> str:
        normalized = {str(reason or "").strip() for reason in reasons if str(reason or "").strip()}
        focus: list[str] = []
        if normalized & {"identity_mismatch", "identity_swap"}:
            focus.append(
                "restore facial identity from the original identity references; do not keep the wrong generated face"
            )
        if "subject_missing" in normalized:
            focus.append("restore the missing person and preserve the requested subject count")
        if normalized & {"face_distortion", "cropped_face", "headless", "fused_faces"}:
            focus.append("repair face geometry and keep every face complete, natural, and readable")
        if normalized & {"body_fusion", "extra_limbs"}:
            focus.append("repair body separation and natural anatomy without changing the locked faces")
        if "bad_hands" in normalized:
            focus.append(
                "replace complex or broken hand poses with simple professional bridal hand placement; "
                "hands may be relaxed, holding one bouquet, or partly covered by veil, sleeves, or gown fabric; "
                "preserve facial identity but do not preserve the failed hand pose"
            )
        if "dress_exposure_error" in normalized:
            focus.append("repair wedding dress coverage and fabric structure")
        if "poor_studio_quality" in normalized:
            focus.append(
                "upgrade broad commercial studio finish with controlled key, fill, rim separation, realistic skin texture, dress fabric, and professional color grading"
            )
        if "face_underexposed" in normalized:
            focus.append("raise facial exposure with soft frontal fill while preserving natural shadow shape and identity")
        if "flat_lighting" in normalized:
            focus.append("rebuild dimensional lighting with a directional key light, weak fill, and subtle rim separation")
        if "no_catchlights" in normalized:
            focus.append("restore natural eye catchlights from the key or fill light without changing eye shape or identity")
        if "oily_skin_highlight" in normalized:
            focus.append("remove oily, wet, plastic facial shine and restore semi-matte natural skin texture")
        if "dress_highlights_blown" in normalized:
            focus.append("recover white dress, lace, satin, veil, sky, and window highlight detail without gray muddy whites")
        if "mixed_color_temperature" in normalized:
            focus.append("unify color temperature across key, fill, rim, and ambient light; remove green-orange mixed-light cast")
        if normalized & {"subject_too_small", "face_too_small", "background_dominates", "excessive_headroom"}:
            focus.append(
                "reframe to commercial wedding proportions: subject prominent, face readable, intentional headroom, and background secondary"
            )
        if normalized & {"awkward_crop", "dress_cropped"}:
            focus.append(
                "restore full wedding crop boundaries with complete gown, train, shoes, limbs, and no joint cutoffs"
            )
        if "poor_subject_separation" in normalized:
            focus.append("improve subject-background separation with controlled depth, rim light, and clean visual hierarchy")
        if "background_brighter_than_face" in normalized:
            focus.append("darken the background slightly and make the face the clear exposure priority")
        if "flat_centered_pose" in normalized:
            focus.append("replace stiff centered tourist-photo blocking with directed editorial wedding posing")
        if "weak_couple_interaction" in normalized:
            focus.append("add subtle couple interaction and staggered blocking while keeping both faces unobstructed")
        if "harsh_backlight" in normalized:
            focus.append("replace harsh backlight with balanced outdoor fill, correct facial exposure, and preserved highlights")
        if normalized & {"black_or_blank", "severe_artifacts", "watermark_or_text"}:
            focus.append("regenerate a clean usable wedding portrait without artifacts, text, or blank regions")
        if is_couple:
            focus.append("keep bride/person A and groom/person B separate; never swap, merge, or average identities")
        if not focus:
            focus.append("perform identity-safe micro-corrections only; keep face, pose, and composition stable")
        return "; ".join(focus)

    @staticmethod
    def _structured_issue_repair_summary(issues: list[dict[str, Any]] | None) -> str:
        if not issues:
            return "none"
        parts: list[str] = []
        for issue in issues[:5]:
            if not isinstance(issue, dict):
                continue
            code = str(issue.get("code") or "other")
            category = str(issue.get("category") or "unknown")
            target = str(issue.get("target") or "unknown")
            severity = str(issue.get("severity") or "review")
            repair_action = str(issue.get("repair_action") or "manual_review")
            repair_hint = str(issue.get("repair_hint") or "").strip()
            text = f"{code} [{category}/{target}/{severity}] -> {repair_action}"
            if repair_hint:
                text = f"{text}: {repair_hint}"
            parts.append(text)
        return "; ".join(parts) if parts else "none"

    @classmethod
    def _candidate_hard_gate_reasons(cls, reasons: list[str], issues: list[dict[str, Any]] | None) -> list[str]:
        return repair_policy.candidate_hard_gate_reasons(reasons, issues)

    @classmethod
    def _score_candidate_verdict(
        cls,
        verdict: dict[str, Any],
        *,
        round_number: int,
        candidate_index: int,
    ) -> dict[str, Any]:
        return repair_policy.score_candidate_verdict(
            verdict,
            round_number=round_number,
            candidate_index=candidate_index,
        )

    @classmethod
    def _select_best_candidate(cls, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        if not candidates:
            raise RuntimeError("wenwen_image_edit_candidates_missing")

        def sort_key(candidate: dict[str, Any]) -> tuple[int, float, int]:
            selection = candidate.get("selection") if isinstance(candidate.get("selection"), dict) else {}
            return (
                1 if bool(candidate.get("qa_ok")) else 0,
                float(selection.get("score") or 0.0),
                -int(candidate.get("index") or 0),
            )

        return max(candidates, key=sort_key)

    @staticmethod
    def _result_selection_score(result: dict[str, Any] | None) -> float:
        if not isinstance(result, dict):
            return -1.0
        selection = result.get("selection") if isinstance(result.get("selection"), dict) else {}
        try:
            return float(selection.get("score") or 0.0)
        except Exception:
            return 0.0

    @classmethod
    def _best_passing_image_edit_round(cls, params: dict[str, Any]) -> dict[str, Any] | None:
        debug = params.get("debug") if isinstance(params.get("debug"), dict) else {}
        rounds = debug.get("image_edit_rounds") if isinstance(debug.get("image_edit_rounds"), list) else []
        passing_rounds = [
            round_item
            for round_item in rounds
            if isinstance(round_item, dict)
            and bool(round_item.get("qa_passed"))
            and str(round_item.get("selected_candidate_url") or round_item.get("candidate_url") or "").strip()
        ]
        if not passing_rounds:
            return None

        def score(round_item: dict[str, Any]) -> tuple[float, int]:
            candidate_scores = round_item.get("candidate_scores")
            selected_index = int(round_item.get("selected_candidate_index") or 0)
            if isinstance(candidate_scores, list):
                for candidate in candidate_scores:
                    if not isinstance(candidate, dict) or int(candidate.get("index") or 0) != selected_index:
                        continue
                    try:
                        return float(candidate.get("score") or 0.0), int(round_item.get("round") or 0)
                    except Exception:
                        break
            return 0.0, int(round_item.get("round") or 0)

        return max(passing_rounds, key=score)

    @classmethod
    def _round_result_from_debug(cls, round_item: dict[str, Any]) -> dict[str, Any]:
        selected_url = str(round_item.get("selected_candidate_url") or round_item.get("candidate_url") or "").strip()
        delivered_urls = [selected_url] if selected_url else []
        provider_urls = [
            str(url)
            for url in (round_item.get("candidate_urls") if isinstance(round_item.get("candidate_urls"), list) else [])
            if str(url or "").strip()
        ]
        candidate_scores = (
            round_item.get("candidate_scores")
            if isinstance(round_item.get("candidate_scores"), list)
            else []
        )
        selection = {}
        selected_index = int(round_item.get("selected_candidate_index") or 0)
        for candidate in candidate_scores:
            if isinstance(candidate, dict) and int(candidate.get("index") or 0) == selected_index:
                selection = candidate
                break
        return {
            "round": int(round_item.get("round") or 0),
            "stage": str(round_item.get("stage") or ""),
            "delivered_urls": delivered_urls,
            "all_delivered_urls": provider_urls or delivered_urls,
            "provider_urls": provider_urls,
            "qa_ok": bool(round_item.get("qa_passed")),
            "qa_reasons": list(round_item.get("qa_reasons") or []),
            "qa_issues": [
                issue for issue in (round_item.get("qa_issues") or []) if isinstance(issue, dict)
            ],
            "used_previous_result": bool(round_item.get("used_previous_result")),
            "selection": selection,
            "selected_candidate_index": selected_index,
            "candidate_scores": candidate_scores,
        }

    @staticmethod
    def _last_qa_retry_history_entry(params: dict[str, Any]) -> dict[str, Any]:
        debug = params.get("debug") if isinstance(params.get("debug"), dict) else {}
        history = debug.get("qa_retry_history") if isinstance(debug.get("qa_retry_history"), list) else []
        for item in reversed(history):
            if isinstance(item, dict):
                return item
        return {}

    @classmethod
    def _pending_vision_recheck_candidate(cls, params: dict[str, Any]) -> str:
        retry_kind = str(params.get("qa_retry_kind") or "").strip()
        if retry_kind != "vision_recheck" and not bool(params.get("qa_retry_uses_existing_candidate")):
            return ""
        candidate_url = str(params.get("qa_retry_candidate_url") or "").strip()
        if candidate_url:
            return candidate_url
        history_entry = cls._last_qa_retry_history_entry(params)
        return str(history_entry.get("candidate_url") or "").strip()

    @classmethod
    def _image_edit_round_for_candidate(cls, params: dict[str, Any], candidate_url: str) -> dict[str, Any]:
        debug = params.get("debug") if isinstance(params.get("debug"), dict) else {}
        rounds = debug.get("image_edit_rounds") if isinstance(debug.get("image_edit_rounds"), list) else []
        for round_item in reversed(rounds):
            if not isinstance(round_item, dict):
                continue
            urls = [
                str(round_item.get("selected_candidate_url") or "").strip(),
                str(round_item.get("candidate_url") or "").strip(),
            ]
            urls.extend(
                str(url).strip()
                for url in (round_item.get("candidate_urls") if isinstance(round_item.get("candidate_urls"), list) else [])
                if str(url or "").strip()
            )
            if candidate_url in urls:
                return round_item
        return {}

    async def _complete_vision_error_degraded_delivery(
        self,
        order_uuid: uuid.UUID,
        *,
        params: dict[str, Any],
        candidate_url: str,
        attempt: int,
        subject_count: int | None,
        couple_flow: str | None,
    ) -> bool:
        if settings.qa_fail_on_vision_error:
            await self._fail_order(
                order_uuid,
                "QA failed: vision_error",
                "qa_reject",
            )
            return True

        round_item = self._image_edit_round_for_candidate(params, candidate_url)
        provider_urls = [
            str(url)
            for url in (round_item.get("provider_urls") if isinstance(round_item.get("provider_urls"), list) else [])
            if str(url or "").strip()
        ]
        if not provider_urls:
            provider_urls = [
                str(url)
                for url in (round_item.get("candidate_urls") if isinstance(round_item.get("candidate_urls"), list) else [])
                if str(url or "").strip()
            ]
        provider_urls = provider_urls or [candidate_url]
        selected_round = self._safe_int(round_item.get("round"), attempt) if round_item else attempt
        selected_stage = str(round_item.get("stage") or "vision_qa_degraded_delivery")
        await self._complete_order(
            order_uuid,
            delivered_urls=[candidate_url],
            provider_urls=provider_urls,
            qa_attempt_count=attempt,
            is_couple=bool(subject_count and int(subject_count) >= 2),
            subject_count=subject_count,
            couple_flow=couple_flow,
            selected_round=selected_round,
            selected_stage=selected_stage,
            selection_summary={
                "policy": self.CANDIDATE_SELECTION_POLICY,
                "selected_round": selected_round,
                "selected_stage": selected_stage,
                "score": self._result_selection_score(self._round_result_from_debug(round_item)) if round_item else 0.0,
                "candidate_scores": round_item.get("candidate_scores") if isinstance(round_item.get("candidate_scores"), list) else [],
                "qa_degraded": True,
                "qa_degraded_reason": "vision_error_retry_exhausted",
                "vision_qa_retry_attempt": attempt,
                "requires_admin_review": True,
            },
        )
        await self._queue_completion_email(order_uuid)
        return True

    async def _update_image_edit_round_qa_state(
        self,
        order_uuid: uuid.UUID,
        *,
        candidate_url: str,
        qa_passed: bool,
        qa_reasons: list[str],
        qa_issues: list[dict[str, Any]],
        attempt: int,
    ) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            debug = dict(params.get("debug")) if isinstance(params.get("debug"), dict) else {}
            rounds = debug.get("image_edit_rounds") if isinstance(debug.get("image_edit_rounds"), list) else []
            for index in range(len(rounds) - 1, -1, -1):
                round_item = rounds[index]
                if not isinstance(round_item, dict):
                    continue
                urls = [
                    str(round_item.get("selected_candidate_url") or "").strip(),
                    str(round_item.get("candidate_url") or "").strip(),
                ]
                urls.extend(
                    str(url).strip()
                    for url in (round_item.get("candidate_urls") if isinstance(round_item.get("candidate_urls"), list) else [])
                    if str(url or "").strip()
                )
                if candidate_url not in urls:
                    continue
                updated_round = dict(round_item)
                updated_round["qa_passed"] = bool(qa_passed)
                updated_round["qa_reasons"] = [str(reason) for reason in qa_reasons]
                updated_round["qa_issues"] = [issue for issue in qa_issues if isinstance(issue, dict)]
                updated_round["qa_rechecked_at"] = self._utc_now_iso()
                updated_round["qa_recheck_attempt"] = int(attempt)
                rounds[index] = updated_round
                break
            debug["image_edit_rounds"] = rounds[-8:]
            params["debug"] = debug
            params["qa_last_reasons"] = [] if qa_passed else [str(reason) for reason in qa_reasons]
            params["qa_last_issues"] = [] if qa_passed else [issue for issue in qa_issues if isinstance(issue, dict)]
            params["qa_attempt_count"] = int(attempt)
            order.generation_params = params
            await db.commit()

    async def _retry_pending_vision_recheck(
        self,
        order_uuid: uuid.UUID,
        *,
        params: dict[str, Any],
        user_images: list[str],
        subject_count: int | None,
        couple_flow: str | None,
    ) -> bool:
        candidate_url = self._pending_vision_recheck_candidate(params)
        if not candidate_url:
            return False
        previous_attempt = self._safe_int(params.get("qa_attempt_count"), 0)
        if (
            self._vision_error_only(params.get("qa_last_reasons"))
            and previous_attempt >= self._safe_vision_retry_limit()
        ):
            return await self._complete_vision_error_degraded_delivery(
                order_uuid,
                params=params,
                candidate_url=candidate_url,
                attempt=previous_attempt,
                subject_count=subject_count,
                couple_flow=couple_flow,
            )
        attempt = previous_attempt + 1
        is_couple = bool(subject_count and int(subject_count) >= 2)
        qa_verdict = await output_verdict(
            candidate_url,
            is_couple=is_couple,
            source_image_urls=[str(url) for url in user_images if str(url or "").strip()],
        )
        qa_ok = bool(qa_verdict.get("passed"))
        qa_reasons = [str(reason) for reason in (qa_verdict.get("reasons") or []) if str(reason or "").strip()]
        qa_issues = [issue for issue in (qa_verdict.get("issues") or []) if isinstance(issue, dict)]
        await self._update_image_edit_round_qa_state(
            order_uuid,
            candidate_url=candidate_url,
            qa_passed=qa_ok,
            qa_reasons=qa_reasons,
            qa_issues=qa_issues,
            attempt=attempt,
        )
        if qa_ok:
            round_item = self._image_edit_round_for_candidate(params, candidate_url)
            provider_urls = [
                str(url)
                for url in (round_item.get("provider_urls") if isinstance(round_item.get("provider_urls"), list) else [])
                if str(url or "").strip()
            ] or [candidate_url]
            selected_round = self._safe_int(round_item.get("round"), attempt) if round_item else attempt
            selected_stage = str(round_item.get("stage") or "vision_recheck_recovered")
            await self._complete_order(
                order_uuid,
                delivered_urls=[candidate_url],
                provider_urls=provider_urls,
                qa_attempt_count=attempt,
                is_couple=is_couple,
                subject_count=subject_count,
                couple_flow=couple_flow,
                selected_round=selected_round,
                selected_stage=selected_stage,
                selection_summary={
                    "policy": self.CANDIDATE_SELECTION_POLICY,
                    "selected_round": selected_round,
                    "selected_stage": selected_stage,
                    "score": self._result_selection_score(self._round_result_from_debug(round_item)) if round_item else 0.0,
                    "candidate_scores": round_item.get("candidate_scores") if isinstance(round_item.get("candidate_scores"), list) else [],
                    "recovered_from_vision_qa_retry": True,
                    "vision_qa_retry_attempt": attempt,
                },
            )
            await self._queue_completion_email(order_uuid)
            return True

        await self._record_qa_failure(
            order_uuid,
            attempt=attempt,
            reasons=qa_reasons,
            candidate_url=candidate_url,
            issues=qa_issues,
        )
        if self._vision_error_only(qa_reasons):
            if attempt < self._safe_vision_retry_limit():
                await self._mark_qa_retry_pending(
                    order_uuid,
                    attempt=attempt,
                    reasons=qa_reasons,
                    candidate_url=candidate_url,
                    retry_kind="vision_recheck",
                )
            else:
                await self._complete_vision_error_degraded_delivery(
                    order_uuid,
                    params=params,
                    candidate_url=candidate_url,
                    attempt=attempt,
                    subject_count=subject_count,
                    couple_flow=couple_flow,
                )
            return True

        # A retry reached the vision model and returned actionable QA reasons.
        # Let the regular image-edit resume path use those reasons for repair.
        return False

    async def _load_image_edit_resume_state(self, order_uuid: uuid.UUID) -> dict[str, Any]:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order or not isinstance(order.generation_params, dict):
                return {}
            params = dict(order.generation_params)
        debug = params.get("debug") if isinstance(params.get("debug"), dict) else {}
        rounds = [
            round_item
            for round_item in (debug.get("image_edit_rounds") if isinstance(debug.get("image_edit_rounds"), list) else [])
            if isinstance(round_item, dict) and int(round_item.get("round") or 0) > 0
        ]
        if not rounds:
            return {}
        rounds.sort(key=lambda item: int(item.get("round") or 0))
        last_round = rounds[-1]
        best_passing = self._best_passing_image_edit_round(params)
        last_result = self._round_result_from_debug(last_round)
        resume: dict[str, Any] = {
            "next_round": min(self.IMAGE_EDIT_MAX_ROUNDS + 1, int(last_round.get("round") or 0) + 1),
            "last_result": last_result,
            "qa_reasons": list(last_round.get("qa_reasons") or []),
            "qa_issues": [issue for issue in (last_round.get("qa_issues") or []) if isinstance(issue, dict)],
            "identity_grade": str(last_round.get("identity_grade") or "identity_pass"),
            "previous_result_refs": [],
        }
        selected_url = str(last_round.get("selected_candidate_url") or last_round.get("candidate_url") or "").strip()
        if (
            selected_url
            and self._should_include_previous_edit_result(resume["qa_reasons"])
            and not identity_qa_requires_forced_repair(resume["identity_grade"])
        ):
            resume["previous_result_refs"] = [selected_url]
        if best_passing:
            resume["best_passed"] = self._round_result_from_debug(best_passing)
        return resume

    @classmethod
    def _build_image_edit_round_prompt(
        cls,
        *,
        base_prompt: str,
        negative_prompt: str,
        round_number: int,
        qa_reasons: list[str],
        identity_pack_note: str,
        include_previous_result: bool,
        is_couple: bool,
        qa_issues: list[dict[str, Any]] | None = None,
    ) -> str:
        stage = cls._image_edit_round_stage(round_number)
        if stage == "primary_generation":
            stage_instruction = (
                "ROUND 1 PRIMARY GENERATION: create the complete professional wedding portrait from the original "
                "identity references and requested scene. The identity reference files are hard face anchors. "
                "If multiple candidates are generated, vary only professional pose nuance, camera distance inside "
                "the commercial framing range, lighting polish, and background depth; never vary identity."
            )
        elif stage == "targeted_repair":
            if cls._is_lighting_only_repair(qa_reasons, round_number=round_number):
                focus = cls._repair_focus_from_reasons(qa_reasons, is_couple=is_couple)
                stage_instruction = (
                    "ROUND 2 RELIGHT/EDIT ONLY: the previous candidate is the current canvas. "
                    "Do not redraw, repaint, replace, beautify, morph, or re-synthesize the face. "
                    "Do not change facial geometry, eye shape, nose, mouth, jawline, chin, age impression, "
                    "hairline, expression, body shape, pose, crop, camera distance, outfit, role order, or scene layout. "
                    "Only edit lighting and finish: key light, fill light, rim separation, facial exposure, "
                    "catchlights, skin specular highlights, dress highlight recovery, color temperature, and "
                    "face-background exposure balance. Treat this as relight/edit, not character creation. "
                    f"Relight focus: {focus}."
                )
            else:
                focus = cls._repair_focus_from_reasons(qa_reasons, is_couple=is_couple)
                previous_instruction = (
                    "A previous candidate image may be included as the current canvas. Use it only as composition and "
                    "style context; the original identity references remain the authority for the face."
                    if include_previous_result
                    else "Do not rely on the previous failed candidate for facial identity; regenerate the repair from the original identity references."
                )
                stage_instruction = (
                    "ROUND 2 TARGETED REPAIR: fix only the QA-targeted issues. "
                    f"Repair focus: {focus}. {previous_instruction}"
                )
        else:
            if qa_reasons:
                focus = cls._repair_focus_from_reasons(qa_reasons, is_couple=is_couple)
                stage_instruction = (
                    "ROUND 3 FINAL POLISH ONLY: edit lighting and finish, not identity, anatomy, hands, crop, pose, "
                    f"wardrobe structure, or scene layout. Polish focus: {focus}. If any identity, face, hands, crop, "
                    "subject-size, or role-order failure remains, reject the candidate instead of redrawing it. "
                    "Allowed edits are facial exposure, catchlights, semi-matte realistic skin texture, controlled "
                    "highlights, white gown highlight recovery, color temperature, shadow cleanup, film grain, and "
                    "professional color grading."
                )
            else:
                stage_instruction = (
                    "ROUND 3 FINAL POLISH: perform final professional retouching only. Improve catchlights, facial "
                    "exposure, semi-matte natural skin texture, controlled facial highlights, dress fabric, color "
                    "grading, and background separation. Remove oily shine and wet glossy skin. Do not "
                    "change facial identity, role order, pose, body shape, camera framing, or scene concept."
                )

        reasons_text = ", ".join(str(reason) for reason in qa_reasons if str(reason).strip()) or "none"
        issues_text = cls._structured_issue_repair_summary(qa_issues)
        couple_note = (
            "Couple rule: the output must contain exactly two primary wedding subjects in the same frame. "
            "Person A/bride and person B/groom must remain separate, with no solo portrait, no missing partner, no role swap, and no face averaging. "
            if is_couple
            else ""
        )
        return (
            "This is an identity-preserving wedding photo edit, not text-to-image character creation. "
            "The identity reference file(s) define the real person or people. "
            "The generated face(s) must remain recognizably the same individual(s) from the source portrait(s). "
            "If a requested style conflicts with identity, prioritize identity.\n\n"
            "DELIVERY HARD GATE: only a candidate that preserves identity, face readability, commercial canvas "
            "proportion, natural crop boundaries, complete wedding wardrobe, and professional lighting may be "
            "delivered. Failed candidates are internal only and must be repaired or rejected.\n"
            f"{stage_instruction}\n"
            f"QA reasons from previous round: {reasons_text}.\n"
            f"Structured QA issues from previous round: {issues_text}.\n"
            f"{couple_note}"
            f"{base_prompt}\n\n"
            "Use the uploaded full identity reference(s), identity face crop(s), and identity upper-body crop(s) as "
            "hard identity anchors. Preserve exact facial structure, face shape, eyes, nose, mouth, jawline, chin, "
            "age impression, skin undertone, and natural expression. Do not generate a generic attractive bride or "
            "groom if that changes the source identity. Do not treat style, scene, clothing, or previous-result "
            "references as additional identities.\n"
            f"{identity_pack_note}"
            f"Negative prompt: {negative_prompt}"
        )

    async def _record_image_edit_round(
        self,
        order_uuid: uuid.UUID,
        *,
        round_number: int,
        stage: str,
        delivered_urls: list[str],
        provider_urls: list[str],
        provider_model: str,
        configured_model: str,
        fallback_used: bool,
        provider_endpoint: str,
        native_image_edit: bool,
        qa_passed: bool,
        qa_reasons: list[str],
        identity_grade: str,
        used_previous_result: bool,
        repair_mode: str | None = None,
        qa_issues: list[dict[str, Any]] | None = None,
        selected_candidate_url: str | None = None,
        selected_candidate_index: int | None = None,
        candidate_scores: list[dict[str, Any]] | None = None,
    ) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            debug = dict(params.get("debug")) if isinstance(params.get("debug"), dict) else {}
            rounds = debug.get("image_edit_rounds") if isinstance(debug.get("image_edit_rounds"), list) else []
            generation_attempt = params.get("generation_attempt")
            rounds.append(
                {
                    "round": int(round_number),
                    "generation_attempt": generation_attempt,
                    "stage": stage,
                    "repair_mode": str(repair_mode or stage),
                    "candidate_url": delivered_urls[0] if delivered_urls else "",
                    "candidate_urls": [str(url) for url in delivered_urls],
                    "selected_candidate_url": selected_candidate_url or (delivered_urls[0] if delivered_urls else ""),
                    "selected_candidate_index": selected_candidate_index,
                    "candidate_count": len(delivered_urls),
                    "provider_url_count": len(provider_urls),
                    "provider_model": str(provider_model or ""),
                    "configured_model": str(configured_model or ""),
                    "fallback_used": bool(fallback_used),
                    "provider_endpoint": str(provider_endpoint or ""),
                    "native_image_edit": bool(native_image_edit),
                    "candidate_scores": candidate_scores or [],
                    "selection_policy": self.CANDIDATE_SELECTION_POLICY,
                    "qa_passed": bool(qa_passed),
                    "qa_reasons": [str(reason) for reason in qa_reasons],
                    "qa_issues": qa_issues or [],
                    "identity_grade": str(identity_grade or "identity_pass"),
                    "used_previous_result": bool(used_previous_result),
                    "billable": False,
                    "billing_reason": "automatic_repair_included",
                    "extra_credits_charged": 0,
                    "completed_at": self._utc_now_iso(),
                }
            )
            debug["image_edit_rounds"] = rounds[-8:]
            params["debug"] = debug
            params["image_edit_round_current"] = int(round_number)
            params["image_edit_stage_current"] = stage
            params["automatic_repair_extra_charge"] = 0
            order.generation_params = params
            await db.commit()

    async def _finalize_image_edit_round_candidates(
        self,
        order_uuid: uuid.UUID,
        *,
        round_number: int,
        stage: str,
        delivered_urls: list[str],
        provider_urls: list[str],
        provider_model: str,
        configured_model: str,
        fallback_used: bool,
        provider_endpoint: str,
        native_image_edit: bool,
        user_images: list[str],
        is_couple: bool,
        include_previous: bool,
        repair_mode: str | None = None,
    ) -> dict[str, Any]:
        candidate_results: list[dict[str, Any]] = []
        source_images = [str(url) for url in user_images if str(url or "").strip()]
        await self._update_generation_stage(order_uuid, "qa_checking", detail=f"round_{round_number}")
        for index, candidate_url in enumerate(delivered_urls):
            qa_verdict = await output_verdict(
                candidate_url,
                is_couple=is_couple,
                source_image_urls=source_images,
            )
            selection = self._score_candidate_verdict(
                qa_verdict,
                round_number=round_number,
                candidate_index=index,
            )
            next_qa_reasons = list(qa_verdict.get("reasons") or [])
            next_qa_issues = [issue for issue in (qa_verdict.get("issues") or []) if isinstance(issue, dict)]
            candidate_results.append(
                {
                    "index": index,
                    "url": candidate_url,
                    "qa_ok": bool(selection.get("passed")),
                    "qa_reasons": next_qa_reasons,
                    "qa_issues": next_qa_issues,
                    "identity_grade": str(selection.get("identity_grade") or "identity_pass"),
                    "selection": selection,
                }
            )

        selected_candidate = self._select_best_candidate(candidate_results)
        primary_image_url = str(selected_candidate.get("url") or "")
        qa_ok = bool(selected_candidate.get("qa_ok"))
        next_qa_reasons = list(selected_candidate.get("qa_reasons") or [])
        next_qa_issues = [
            issue for issue in (selected_candidate.get("qa_issues") or []) if isinstance(issue, dict)
        ]
        selected_selection = selected_candidate.get("selection") if isinstance(selected_candidate.get("selection"), dict) else {}
        identity_grade = str(selected_selection.get("identity_grade") or "identity_pass")
        selected_index = int(selected_candidate.get("index") or 0)
        candidate_scores = [
            {
                "index": int(candidate.get("index") or 0),
                "url": str(candidate.get("url") or ""),
                "qa_passed": bool(candidate.get("qa_ok")),
                **(candidate.get("selection") if isinstance(candidate.get("selection"), dict) else {}),
            }
            for candidate in candidate_results
        ]
        await self._record_image_edit_round(
            order_uuid,
            round_number=round_number,
            stage=stage,
            repair_mode=repair_mode or stage,
            delivered_urls=delivered_urls,
            provider_urls=provider_urls,
            provider_model=provider_model,
            configured_model=configured_model,
            fallback_used=fallback_used,
            provider_endpoint=provider_endpoint,
            native_image_edit=native_image_edit,
            qa_passed=qa_ok,
            qa_reasons=next_qa_reasons,
            identity_grade=identity_grade,
            qa_issues=next_qa_issues,
            used_previous_result=include_previous,
            selected_candidate_url=primary_image_url,
            selected_candidate_index=selected_index,
            candidate_scores=candidate_scores,
        )
        if not qa_ok:
            await self._record_qa_failure(
                order_uuid,
                attempt=round_number,
                reasons=next_qa_reasons,
                candidate_url=primary_image_url,
                issues=next_qa_issues,
            )
        return {
            "round": int(round_number),
            "stage": stage,
            "delivered_urls": [primary_image_url],
            "all_delivered_urls": delivered_urls,
            "provider_urls": provider_urls,
            "provider_model": str(provider_model or ""),
            "configured_model": str(configured_model or ""),
            "fallback_used": bool(fallback_used),
            "provider_endpoint": str(provider_endpoint or ""),
            "native_image_edit": bool(native_image_edit),
            "qa_ok": qa_ok,
            "qa_reasons": next_qa_reasons,
            "qa_issues": next_qa_issues,
            "identity_grade": identity_grade,
            "used_previous_result": include_previous,
            "selection": selected_candidate.get("selection") if isinstance(selected_candidate, dict) else {},
            "selected_candidate_index": selected_index,
            "candidate_scores": candidate_scores,
        }

    async def _submit_native_image_edit_round(
        self,
        order_uuid: uuid.UUID,
        *,
        model: str,
        refs: list[str],
        identity_refs: list[str],
        style_refs: list[str],
        current_result_refs: list[str],
        prompt_text: str,
        negative_prompt: str,
        user_images: list[str],
        identity_reference_pack: dict | None,
        is_couple: bool,
        round_number: int,
        qa_reasons: list[str],
        configured_model: str | None = None,
        qa_issues: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        stage = self._image_edit_round_stage(round_number)
        repair_mode = self._image_edit_repair_mode(round_number=round_number, qa_reasons=qa_reasons)
        if repair_mode == "relight_edit_only" and not current_result_refs:
            raise RuntimeError("relight_edit_only_requires_previous_candidate")
        include_previous = bool(current_result_refs) and (
            repair_mode == "relight_edit_only" or self._should_include_previous_edit_result(qa_reasons)
        )
        identity_pack_note = self._identity_pack_prompt_note(identity_reference_pack)
        edit_prompt = self._build_image_edit_round_prompt(
            base_prompt=prompt_text,
            negative_prompt=negative_prompt,
            round_number=round_number,
            qa_reasons=qa_reasons,
            qa_issues=qa_issues,
            identity_pack_note=identity_pack_note,
            include_previous_result=include_previous,
            is_couple=is_couple,
        )
        native_payload, reference_entries = await self._build_native_image_edit_payload(
            edit_prompt=edit_prompt,
            negative_prompt=negative_prompt,
            identity_refs=identity_refs,
            style_refs=style_refs,
            current_result_refs=current_result_refs if include_previous else [],
            identity_reference_pack=identity_reference_pack,
            include_previous_result=include_previous,
            is_couple=is_couple,
        )
        if not reference_entries:
            raise RuntimeError("wenwen_native_image_edit_identity_refs_missing")

        model_candidates = self._native_image_edit_model_candidates(model)
        if not model_candidates:
            raise RuntimeError("wenwen_native_image_edit_model_missing")

        await self._update_order_generating(
            order_uuid,
            task_id=f"native-image-edit-{order_uuid}-round-{round_number}",
            payload={
                **native_payload,
                "_provider_model": model_candidates[0],
                "_requested_model": configured_model or model,
                "_model_candidates": model_candidates,
                "_fallback_used": model_candidates[0] != (configured_model or model),
                "_endpoint": self._native_generation_url_for_model(model_candidates[0]),
                "_reference_count": len(reference_entries),
                "_source_reference_count": min(len(refs), self.IMAGE_EDIT_REFERENCE_FILE_LIMIT),
                "_requested_candidate_count": self._image_edit_candidate_count(),
                "_round": int(round_number),
                "_stage": stage,
                "_repair_mode": repair_mode,
                "_native_image_edit": True,
                "_billable": False,
                "_billing_reason": "automatic_repair_included",
                "_extra_credits_charged": 0,
            },
            prompt_text=edit_prompt,
            negative_prompt=negative_prompt,
            generation_mode="native_image_edit_multi_round",
        )

        response: httpx.Response | None = None
        selected_model = model_candidates[0]
        last_transient_error: Exception | None = None
        timeout = httpx.Timeout(connect=10.0, read=self._native_read_timeout(model_index=0, model_count=1), write=120.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
            for model_index, candidate_model in enumerate(model_candidates):
                selected_model = candidate_model
                request_payload = native_payload
                try:
                    candidate_response = await client.post(
                        self._native_generation_request_url_for_model(candidate_model),
                        json=request_payload,
                        headers=self._native_headers(),
                    )
                    if (
                        candidate_response.status_code in {400, 422}
                        and "candidate" in candidate_response.text.lower()
                        and "candidateCount" in native_payload.get("generationConfig", {})
                    ):
                        request_payload = self._native_payload_without_candidate_count(native_payload)
                        candidate_response = await client.post(
                            self._native_generation_request_url_for_model(candidate_model),
                            json=request_payload,
                            headers=self._native_headers(),
                        )
                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    last_transient_error = exc
                    if model_index < len(model_candidates) - 1:
                        logger.warning(
                            "Wenwen native image-edit transient error on %s; trying configured candidate %s: %s",
                            candidate_model,
                            model_candidates[model_index + 1],
                            exc,
                        )
                        continue
                    raise

                if candidate_response.status_code in {400, 422, 500, 503}:
                    lowered = candidate_response.text.lower()
                    model_unavailable = (
                        "model_not_found" in lowered
                        or "no available channel" in lowered
                        or "not supported model for image generation" in lowered
                    )
                    if model_unavailable and model_index < len(model_candidates) - 1:
                        logger.warning(
                            "Wenwen native image-edit model unavailable on %s; trying configured candidate %s: %s",
                            candidate_model,
                            model_candidates[model_index + 1],
                            candidate_response.status_code,
                        )
                        continue
                response = candidate_response
                break

        if response is None:
            if last_transient_error:
                raise last_transient_error
            raise RuntimeError("wenwen_native_image_edit_response_missing")
        if response.status_code in {401, 403}:
            raise RuntimeError(f"wenwen_auth_failed:{response.status_code}")
        if response.status_code in {402, 429}:
            raise RuntimeError(f"wenwen_quota_rejected:{response.status_code}:{response.text[:200]}")
        if response.status_code in {400, 422, 500, 503}:
            lowered = response.text.lower()
            if "model_not_found" in lowered or "no available channel" in lowered or "not supported model for image generation" in lowered:
                raise RuntimeError(
                    f"wenwen_model_unavailable:{selected_model}:{response.status_code}:{response.text[:240]}"
                )
        response.raise_for_status()

        submission = response.json() if response.content else {}
        delivered_urls = await self._persist_native_candidate_outputs_to_storage(submission, order_uuid)
        if not delivered_urls:
            raise RuntimeError("wenwen_native_image_edit_outputs_missing")

        return await self._finalize_image_edit_round_candidates(
            order_uuid,
            round_number=round_number,
            stage=stage,
            delivered_urls=delivered_urls,
            provider_urls=[],
            provider_model=selected_model,
            configured_model=configured_model or model,
            fallback_used=selected_model != (configured_model or model),
            provider_endpoint=self._native_generation_url_for_model(selected_model),
            native_image_edit=True,
            user_images=user_images,
            is_couple=is_couple,
            include_previous=include_previous,
            repair_mode=repair_mode,
        )

    async def _submit_image_edit_round(
        self,
        order_uuid: uuid.UUID,
        *,
        model: str,
        refs: list[str],
        identity_refs: list[str],
        style_refs: list[str],
        current_result_refs: list[str],
        prompt_text: str,
        negative_prompt: str,
        user_images: list[str],
        identity_reference_pack: dict | None,
        is_couple: bool,
        round_number: int,
        qa_reasons: list[str],
        configured_model: str | None = None,
        qa_issues: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if self._image_edit_uses_native_model(model):
            raise RuntimeError("wenwen_native_image_edit_requires_generate_content")

        stage = self._image_edit_round_stage(round_number)
        repair_mode = self._image_edit_repair_mode(round_number=round_number, qa_reasons=qa_reasons)
        if repair_mode == "relight_edit_only" and not current_result_refs:
            raise RuntimeError("relight_edit_only_requires_previous_candidate")
        include_previous = bool(current_result_refs) and (
            repair_mode == "relight_edit_only" or self._should_include_previous_edit_result(qa_reasons)
        )
        identity_pack_note = self._identity_pack_prompt_note(identity_reference_pack)
        edit_prompt = self._build_image_edit_round_prompt(
            base_prompt=prompt_text,
            negative_prompt=negative_prompt,
            round_number=round_number,
            qa_reasons=qa_reasons,
            qa_issues=qa_issues,
            identity_pack_note=identity_pack_note,
            include_previous_result=include_previous,
            is_couple=is_couple,
        )
        files = await self._build_image_edit_reference_files(
            identity_refs,
            style_refs=style_refs,
            identity_reference_pack=identity_reference_pack,
            current_result_refs=current_result_refs if include_previous else [],
            round_number=round_number,
            qa_reasons=qa_reasons,
        )
        if not files:
            raise RuntimeError("wenwen_image_edit_identity_refs_missing")

        await self._update_order_generating(
            order_uuid,
            task_id=f"image-edit-{order_uuid}-round-{round_number}",
            payload={
                "model": model,
                "requested_model": configured_model or model,
                "fallback_used": model != (configured_model or model),
                "endpoint": settings.wenwen_image_edit_path,
                "size": self._image_edit_size(is_couple),
                "quality": settings.wenwen_image_edit_quality,
                "reference_count": min(len(refs), self.IMAGE_EDIT_REFERENCE_FILE_LIMIT),
                "uploaded_reference_file_count": len(files),
                "requested_candidate_count": self._image_edit_candidate_count(),
                "round": int(round_number),
                "stage": stage,
                "repair_mode": repair_mode,
                "multi_round_edit": True,
                "image_edit_max_rounds": self.IMAGE_EDIT_MAX_ROUNDS,
                "billable": False,
                "billing_reason": "automatic_repair_included",
                "extra_credits_charged": 0,
                "used_previous_result": include_previous,
                "identity_edit_hard_required": self._identity_edit_required(user_images),
                "native_text_to_image_fallback_allowed": False if self._identity_edit_required(user_images) else True,
                "identity_reference_pack_version": identity_reference_pack.get("version")
                if isinstance(identity_reference_pack, dict)
                else None,
            },
            prompt_text=edit_prompt,
            negative_prompt=negative_prompt,
            generation_mode="image_edit_multi_round",
        )

        data = {
            "model": model,
            "prompt": edit_prompt,
            "n": str(self._image_edit_candidate_count()),
            "size": self._image_edit_size(is_couple),
            "quality": str(settings.wenwen_image_edit_quality or "high"),
        }
        timeout = httpx.Timeout(connect=10.0, read=260.0, write=120.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
            response = await client.post(
                self._image_edit_url(),
                headers={"Authorization": f"Bearer {settings.wenwen_api_key}"},
                data=data,
                files=files,
            )
            if response.status_code in {400, 422} and data.get("n") != "1":
                lowered = response.text.lower()
                if any(token in lowered for token in ("n", "number", "candidate", "multiple")):
                    retry_data = dict(data)
                    retry_data["n"] = "1"
                    response = await client.post(
                        self._image_edit_url(),
                        headers={"Authorization": f"Bearer {settings.wenwen_api_key}"},
                        data=retry_data,
                        files=files,
                    )
        if response.status_code in {401, 403}:
            raise RuntimeError(f"wenwen_auth_failed:{response.status_code}")
        if response.status_code in {402, 429}:
            raise RuntimeError(f"wenwen_quota_rejected:{response.status_code}:{response.text[:200]}")
        if response.status_code in {404, 405, 422}:
            raise RuntimeError(f"wenwen_image_edit_unavailable:{response.status_code}:{response.text[:240]}")
        if response.status_code >= 500:
            raise RuntimeError(f"wenwen_image_edit_failed:{response.status_code}:{response.text[:240]}")
        response.raise_for_status()

        submission = response.json() if response.content else {}
        provider_urls = self._extract_output_urls(submission)
        delivered_urls: list[str] = []
        if provider_urls:
            delivered_urls = await self._persist_outputs_to_storage(provider_urls, order_uuid)
        if not delivered_urls:
            binary_outputs = self._extract_image_edit_binary_outputs(submission)
            if binary_outputs:
                delivered_urls = await self._persist_binary_outputs_to_storage(binary_outputs, order_uuid)
        if not delivered_urls:
            raise RuntimeError("wenwen_image_edit_outputs_missing")

        return await self._finalize_image_edit_round_candidates(
            order_uuid,
            round_number=round_number,
            stage=stage,
            delivered_urls=delivered_urls,
            provider_urls=provider_urls,
            provider_model=model,
            configured_model=configured_model or model,
            fallback_used=model != (configured_model or model),
            provider_endpoint=settings.wenwen_image_edit_path,
            native_image_edit=False,
            user_images=user_images,
            is_couple=is_couple,
            include_previous=include_previous,
            repair_mode=repair_mode,
        )

    async def _run_image_edit_generation(
        self,
        order_uuid: uuid.UUID,
        *,
        refs: list[str],
        prompt_text: str,
        negative_prompt: str,
        user_images: list[str],
        identity_reference_pack: dict | None,
        subject_count: int | None,
        couple_flow: str | None,
    ) -> bool:
        identity_edit_required = self._identity_edit_required(user_images)
        if not refs:
            return False
        if not identity_edit_required and not settings.wenwen_prefer_image_edit:
            return False
        model = self._effective_image_edit_model()
        if not model:
            if identity_edit_required:
                raise RuntimeError("wenwen_image_edit_model_missing")
            return False

        is_couple = bool(subject_count and int(subject_count) >= 2)
        identity_ref_count = min(len([url for url in (user_images or []) if str(url or "").strip()]), 2)
        identity_refs = refs[:identity_ref_count]
        style_refs = refs[identity_ref_count:]
        model_candidates = self._image_edit_model_candidates(model)

        best_passed: dict[str, Any] | None = None
        previous_result_refs: list[str] = []
        qa_reasons: list[str] = []
        qa_issues: list[dict[str, Any]] = []
        last_result: dict[str, Any] | None = None
        start_round = 1
        resume_state = await self._load_image_edit_resume_state(order_uuid)
        if resume_state:
            start_round = int(resume_state.get("next_round") or 1)
            previous_result_refs = [
                str(url)
                for url in (resume_state.get("previous_result_refs") or [])
                if str(url or "").strip()
            ]
            qa_reasons = [str(reason) for reason in (resume_state.get("qa_reasons") or []) if str(reason).strip()]
            qa_issues = [issue for issue in (resume_state.get("qa_issues") or []) if isinstance(issue, dict)]
            last_result = resume_state.get("last_result") if isinstance(resume_state.get("last_result"), dict) else None
            best_passed = resume_state.get("best_passed") if isinstance(resume_state.get("best_passed"), dict) else None

        for round_number in range(start_round, self.IMAGE_EDIT_MAX_ROUNDS + 1):
            if round_number >= 3 and not self._can_enter_final_polish_round(qa_reasons):
                logger.warning(
                    "Wenwen image-edit stopped before round 3 because remaining failures require targeted repair, not polish: %s",
                    qa_reasons,
                )
                break
            if round_number > 1:
                await self._update_generation_stage(order_uuid, "repairing", detail=f"round_{round_number}")
            result: dict[str, Any] | None = None
            for model_index, candidate_model in enumerate(model_candidates):
                try:
                    submit_round = (
                        self._submit_native_image_edit_round
                        if self._image_edit_uses_native_model(candidate_model)
                        else self._submit_image_edit_round
                    )
                    result = await submit_round(
                        order_uuid,
                        model=candidate_model,
                        refs=refs,
                        identity_refs=identity_refs,
                        style_refs=style_refs,
                        current_result_refs=previous_result_refs,
                        prompt_text=prompt_text,
                        negative_prompt=negative_prompt,
                        user_images=user_images,
                        identity_reference_pack=identity_reference_pack,
                        is_couple=is_couple,
                        round_number=round_number,
                        qa_reasons=qa_reasons,
                        configured_model=model,
                        qa_issues=qa_issues,
                    )
                    if candidate_model != model:
                        logger.warning("Wenwen image-edit alternate configured model succeeded: %s -> %s", model, candidate_model)
                    break
                except RuntimeError as exc:
                    if self._is_model_unavailable_error(exc) and model_index < len(model_candidates) - 1:
                        logger.warning(
                            "Wenwen image-edit model unavailable on %s; trying configured candidate %s: %s",
                            candidate_model,
                            model_candidates[model_index + 1],
                            exc,
                        )
                        continue
                    raise
            if result is None:
                raise RuntimeError("wenwen_image_edit_model_exhausted")
            last_result = result
            if result["qa_ok"]:
                if best_passed is None or self._result_selection_score(result) > self._result_selection_score(best_passed):
                    best_passed = result
                previous_result_refs = list(result["delivered_urls"][:1])
                qa_reasons = []
                qa_issues = []
            else:
                qa_reasons = list(result["qa_reasons"] or [])
                qa_issues = [issue for issue in (result.get("qa_issues") or []) if isinstance(issue, dict)]
                identity_grade = str(result.get("identity_grade") or classify_identity_qa(qa_reasons, qa_issues))
                if self._vision_error_only(qa_reasons):
                    candidate_url = str((result.get("delivered_urls") or [""])[0] or "").strip()
                    if candidate_url:
                        await self._mark_qa_retry_pending(
                            order_uuid,
                            attempt=round_number,
                            reasons=qa_reasons,
                            candidate_url=candidate_url,
                            retry_kind="vision_recheck",
                        )
                        logger.warning(
                            "Wenwen image-edit QA vision error; queued same-candidate QA recheck (round %d, max %d): %s",
                            round_number,
                            self._safe_vision_retry_limit(),
                            qa_reasons,
                        )
                        return True
                if best_passed and round_number >= self.IMAGE_EDIT_MAX_ROUNDS:
                    logger.warning(
                        "Wenwen final image-edit round failed; delivering previous passing round %s: %s",
                        best_passed.get("round"),
                        qa_reasons,
                    )
                    break
                if identity_qa_requires_forced_repair(identity_grade):
                    previous_result_refs = []
                else:
                    previous_result_refs = (
                        list(result["delivered_urls"][:1])
                        if self._should_include_previous_edit_result(qa_reasons)
                        else []
                    )

        selected = best_passed
        if selected is None:
            reasons = list((last_result or {}).get("qa_reasons") or qa_reasons or ["unknown"])
            raise ValueError(f"QA failed: {','.join(reasons)}")

        await self._complete_order(
            order_uuid,
            delivered_urls=list(selected["delivered_urls"]),
            provider_urls=list(selected["provider_urls"]),
            qa_attempt_count=int((last_result or selected).get("round") or self.IMAGE_EDIT_MAX_ROUNDS),
            is_couple=is_couple,
            subject_count=subject_count,
            couple_flow=couple_flow,
            selected_round=int(selected["round"]),
            selected_stage=str(selected["stage"]),
            selection_summary={
                "policy": self.CANDIDATE_SELECTION_POLICY,
                "selected_round": int(selected["round"]),
                "selected_stage": str(selected["stage"]),
                "selected_candidate_index": int(selected.get("selected_candidate_index") or 0),
                "score": self._result_selection_score(selected),
                "candidate_scores": selected.get("candidate_scores") if isinstance(selected.get("candidate_scores"), list) else [],
            },
        )
        return True

    async def _update_order_generating(
        self,
        order_uuid: uuid.UUID,
        *,
        task_id: str,
        payload: dict[str, Any],
        prompt_text: str,
        negative_prompt: str,
        provider_task_id: str | None = None,
        provider_task_status: str | None = None,
        generation_mode: str | None = None,
    ) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return
            if order.status == OrderStatus.COMPLETED:
                return
            order.status = OrderStatus.GENERATING
            order.task_id = task_id
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            params = merge_generation_stage(params, "provider_submitted", detail=generation_mode or provider_task_status)
            debug = dict(params.get("debug")) if isinstance(params.get("debug"), dict) else {}
            payload_model = str(payload.get("_provider_model") or payload.get("model") or "").strip()
            requested_model = str(payload.get("_requested_model") or payload.get("requested_model") or payload_model).strip()
            fallback_used = bool(payload.get("_fallback_used") or payload.get("fallback_used") or False)
            debug.update(
                {
                    "wenwen_submit_payload_keys": sorted(payload.keys()),
                    "wenwen_model": self._effective_image_model(),
                    "wenwen_configured_model": settings.wenwen_image_model,
                    "wenwen_requested_image_edit_model": requested_model,
                    "wenwen_actual_image_edit_model": payload_model,
                    "wenwen_image_edit_fallback_used": fallback_used,
                    "wenwen_submitted_at": self._utc_now_iso(),
                }
            )
            params.update(
                {
                    "engine": self.PROVIDER,
                    "provider": self.PROVIDER,
                    "prompt": prompt_text,
                    "negative_prompt": negative_prompt,
                    "debug": debug,
                    "configured_generation_model": requested_model,
                    "actual_generation_model": payload_model,
                    "generation_model_fallback_used": fallback_used,
                }
            )
            if provider_task_id:
                params["provider_task_id"] = provider_task_id
                params["wenwen_task_id"] = provider_task_id
            if provider_task_status:
                params["provider_task_status"] = provider_task_status
            if generation_mode:
                params["generation_mode"] = generation_mode
            order.generation_params = params
            await db.commit()

    async def _complete_order(
        self,
        order_uuid: uuid.UUID,
        *,
        delivered_urls: list[str],
        provider_urls: list[str],
        qa_attempt_count: int,
        is_couple: bool,
        subject_count: int | None,
        couple_flow: str | None,
        selected_round: int | None = None,
        selected_stage: str | None = None,
        selection_summary: dict[str, Any] | None = None,
    ) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return
            order.status = OrderStatus.COMPLETED
            params = order.generation_params if isinstance(order.generation_params, dict) else {}
            params = merge_generation_stage(params, "postprocessing")
            preview_urls, final_urls, preview_meta = await prepare_delivered_image_urls(
                delivered_urls,
                trial_preview=is_trial_order(params),
                template_id=order.template_id,
            )
            order.preview_image_urls = preview_urls
            order.final_image_urls = final_urls
            debug = params.get("debug") if isinstance(params.get("debug"), dict) else {}
            debug["wenwen_output_urls"] = provider_urls
            params["debug"] = debug
            params["delivery"] = {
                **(params.get("delivery") if isinstance(params.get("delivery"), dict) else {}),
                **preview_meta,
            }
            params["qa_last_reasons"] = []
            params["qa_last_issues"] = []
            params["qa_attempt_count"] = qa_attempt_count
            params["qa_retry_pending"] = False
            params["qa_retry_in_progress"] = False
            params["automatic_repair_extra_charge"] = 0
            if selected_round is not None:
                params["image_edit_selected_round"] = int(selected_round)
            if selected_stage:
                params["image_edit_selected_stage"] = str(selected_stage)
            if selection_summary:
                params["candidate_selection"] = selection_summary
            params["couple_guardrails"] = {
                "is_couple": bool(is_couple),
                "subject_count": subject_count,
                "couple_flow": couple_flow,
            }
            params = merge_generation_stage(params, "completed")
            order.generation_params = params
            await db.commit()

    async def _update_generation_stage(self, order_uuid: uuid.UUID, stage: str, *, detail: str | None = None) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order or order.status == OrderStatus.COMPLETED:
                return
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            order.generation_params = merge_generation_stage(params, stage, detail=detail)
            await db.commit()

    async def _queue_completion_email(self, order_uuid: uuid.UUID) -> None:
        try:
            from app.services.email_service import send_order_completed

            async with async_session_maker() as db:
                order = (await db.execute(select(Order).where(Order.id == order_uuid))).scalar_one_or_none()
                if not order or not order.user_id:
                    return
                from app.models.user import User

                user = await db.get(User, order.user_id)
                if user and user.email:
                    preview = (order.preview_image_urls or {}).get("image_1")
                    asyncio.create_task(
                        send_order_completed(
                            to=user.email,
                            order_id=str(order.id),
                            preview_url=preview,
                            action_url=self._order_result_url(str(order.id)),
                        )
                    )
        except Exception as mail_exc:
            logger.debug("Order completion email skipped: %s", mail_exc)

    @staticmethod
    def _order_result_url(order_id: str) -> str:
        from app.services.email_service import build_order_result_url

        return build_order_result_url(order_id)

    def _classify_error(self, error: Exception) -> str:
        if isinstance(error, (httpx.TimeoutException, TimeoutError, asyncio.TimeoutError)):
            return "generation_timeout"
        text = str(error or "").strip().lower()
        if not text:
            return "unknown_error"
        if "model_not_found" in text or "no available channel" in text or "not supported model for image generation" in text:
            return "provider_model_unavailable"
        if "auth_failed" in text or "401" in text or "403" in text:
            return "provider_auth_failed"
        if "bad_request" in text or "request_rejected" in text or "400" in text or "422" in text:
            return "provider_request_rejected"
        if "429" in text or "quota" in text or "insufficient" in text or "balance" in text:
            return "provider_quota_exhausted"
        if "timeout" in text:
            return "generation_timeout"
        if "qa failed" in text:
            return "qa_reject"
        if "storage" in text or "delivery" in text:
            return "delivery_error"
        return "unknown_error"

    async def _fail_order(self, order_uuid: uuid.UUID, error_message: str, failure_code: str) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            clean_error_message = str(error_message or "").strip() or failure_code or "unknown_generation_error"
            params = dict(order.generation_params) if order and isinstance(order.generation_params, dict) else {}
            if order and order.status == OrderStatus.COMPLETED:
                return
            refund_amount = resolve_generation_refund_amount(
                params,
                fallback_amount=COST_PER_GENERATION,
                failure_code=failure_code,
            )
            refund_applied = False
            if order and order.user_id and refund_amount:
                _balance, refund_applied = await refund_generation_credits_once_async(
                    db,
                    order.user_id,
                    refund_amount,
                    order_id=order.id,
                    failure_code=failure_code,
                    provider=self.PROVIDER,
                    description=f"Generation failed: {failure_code}",
                    metadata=generation_refund_metadata(
                        params,
                        failure_code=failure_code,
                        failure_provider=self.PROVIDER,
                        error_message=clean_error_message,
                    ),
                )
            if order:
                order.status = OrderStatus.FAILED
                order.error_message = clean_error_message
                params = merge_generation_stage(params, "failed", detail=failure_code)
                order.generation_params = merge_generation_refund_state(
                    params,
                    refund_amount=refund_amount,
                    refund_applied=refund_applied,
                    refund_already_recorded=bool(refund_amount and not refund_applied),
                    failure_code=failure_code,
                    failure_provider=self.PROVIDER,
                )
                await db.commit()

    async def _record_qa_failure(
        self,
        order_uuid: uuid.UUID,
        *,
        attempt: int,
        reasons: list[str],
        candidate_url: str,
        issues: list[dict[str, Any]] | None = None,
    ) -> None:
        await record_generation_qa_failure(
            order_uuid,
            attempt=attempt,
            reasons=reasons,
            candidate_url=candidate_url,
            engine=self.PROVIDER,
            issues=issues,
        )

    async def _load_order_generation_context(self, order_uuid: uuid.UUID) -> dict[str, Any] | None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return None
            template_id = str(order.template_id or "").strip()
            user_images = self._order_source_images(order)
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            if not template_id or not user_images:
                return None
            return {
                "order_id": str(order.id),
                "template_id": template_id,
                "user_images": user_images,
                "subject_count": params.get("subject_count"),
                "couple_flow": params.get("couple_flow"),
                "prompt_override": params.get("prompt_override"),
                "global_style_text": params.get("global_style_text"),
                "scene_text": params.get("scene_text"),
                "outfit_text": params.get("outfit_text"),
                "scene_image_url": params.get("effective_scene_image_url") or params.get("scene_image_url"),
                "clothing_image_url": params.get("effective_clothing_image_url") or params.get("clothing_image_url"),
            }

    @staticmethod
    def _recently_polled(debug: dict[str, Any], *, min_interval_seconds: int = 10) -> bool:
        raw = debug.get("last_task_poll_at")
        if not raw:
            return False
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc).timestamp() - parsed.timestamp() < min_interval_seconds
        except Exception:
            return False

    @staticmethod
    def _seconds_since(value: Any) -> float | None:
        if not value:
            return None
        try:
            parsed = value
            if isinstance(value, str):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc).timestamp() - parsed.timestamp()
        except Exception:
            return None

    async def _record_provider_poll(
        self,
        order_uuid: uuid.UUID,
        *,
        params: dict[str, Any],
        status_value: str | None,
        output_count: int,
        payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return
            next_params = dict(order.generation_params) if isinstance(order.generation_params, dict) else dict(params)
            debug = dict(next_params.get("debug")) if isinstance(next_params.get("debug"), dict) else {}
            debug["last_task_poll_at"] = self._utc_now_iso()
            if status_value:
                debug["last_task_status"] = status_value
                next_params["provider_task_status"] = status_value
            debug["last_task_output_count"] = output_count
            if isinstance(payload, dict):
                debug["last_task_payload_keys"] = sorted(payload.keys())
            if error_message:
                debug["last_task_poll_error"] = error_message[:500]
            next_params["debug"] = debug
            order.generation_params = next_params
            await db.commit()

    async def refresh_order_from_provider(self, order_id: str) -> bool:
        try:
            order_uuid = uuid.UUID(str(order_id))
        except (TypeError, ValueError):
            return False

        retry_context: dict[str, Any] | None = None
        pending_vision_recheck_params: dict[str, Any] | None = None
        recovery_round: dict[str, Any] | None = None
        user_images: list[str] = []
        subject_count: int | None = None
        couple_flow: str | None = None
        qa_attempt_count = 1
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return False
            status_value = order.status.value if isinstance(order.status, OrderStatus) else str(order.status or "")
            if status_value != OrderStatus.GENERATING.value:
                return False
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            pending_candidate = self._pending_vision_recheck_candidate(params)
            if (
                pending_candidate
                and self._vision_error_only(params.get("qa_last_reasons"))
                and self._safe_int(params.get("qa_attempt_count"), 0) >= self._safe_vision_retry_limit()
            ):
                user_images = self._order_source_images(order)
                try:
                    subject_count = int(params.get("subject_count") or len(user_images) or 0) or None
                except Exception:
                    subject_count = len(user_images) or None
                couple_flow = str(params.get("couple_flow") or "") or None
                retry_context = {
                    "user_images": list(user_images),
                    "subject_count": subject_count,
                    "couple_flow": couple_flow,
                }
                pending_vision_recheck_params = params
            if retry_context is None and (bool(params.get("qa_retry_pending")) or bool(params.get("qa_retry_in_progress"))):
                retry_started_at = params.get("qa_retry_started_at")
                if bool(params.get("qa_retry_in_progress")) and (self._seconds_since(retry_started_at) or 0) < 20 * 60:
                    return False
                retry_context = await self._load_order_generation_context(order_uuid)
                if not retry_context:
                    await self._fail_order(order_uuid, "qa_retry_missing_generation_context", "qa_reject")
                    return True
                await self._mark_qa_retry_started(order_uuid)
                if self._pending_vision_recheck_candidate(params):
                    pending_vision_recheck_params = params
            if retry_context:
                pass
            elif str(params.get("native_raw_output_status") or "").strip() == "qa_rejected_retry_pending":
                return False
            native_raw_output_urls = params.get("native_raw_output_urls")
            if retry_context is None and isinstance(native_raw_output_urls, list) and native_raw_output_urls:
                user_images = self._order_source_images(order)
                try:
                    subject_count = int(params.get("subject_count") or len(user_images) or 0) or None
                except Exception:
                    subject_count = len(user_images) or None
                couple_flow = str(params.get("couple_flow") or "") or None
                qa_attempt_count = int(params.get("qa_attempt_count") or 1)
                try:
                    await self._complete_provider_urls(
                        order_uuid,
                        provider_urls=[str(url) for url in native_raw_output_urls if str(url or "").strip()],
                        user_images=user_images,
                        subject_count=subject_count,
                        couple_flow=couple_flow,
                        qa_attempt_count=qa_attempt_count,
                    )
                    await self._queue_completion_email(order_uuid)
                except Exception as exc:
                    await self._fail_order(order_uuid, str(exc), self._classify_error(exc))
                return True
            user_images = self._order_source_images(order)
            try:
                subject_count = int(params.get("subject_count") or len(user_images) or 0) or None
            except Exception:
                subject_count = len(user_images) or None
            couple_flow = str(params.get("couple_flow") or "") or None
            qa_attempt_count = int(params.get("qa_attempt_count") or 1)
            if retry_context is None:
                recovery_round = self._best_passing_image_edit_round(params)
            task_id = str(params.get("provider_task_id") or params.get("wenwen_task_id") or "").strip()
            if recovery_round is not None:
                task_id = "__image_edit_recovery__"
            elif retry_context is not None:
                task_id = "__qa_retry__"
            if not task_id:
                if str(params.get("execution_mode") or "").strip() in {"inline", "inline_background"}:
                    age_seconds = self._seconds_since(params.get("inline_background_started_at")) or self._seconds_since(order.updated_at)
                    if age_seconds is not None and age_seconds > 20 * 60:
                        await self._fail_order(
                            order_uuid,
                            "generation_runtime_interrupted_before_output",
                            "generation_timeout",
                        )
                        return True
                return False
            debug = dict(params.get("debug")) if isinstance(params.get("debug"), dict) else {}
            if retry_context is None and recovery_round is None and self._recently_polled(debug):
                return False

        if recovery_round is not None:
            selected_url = str(recovery_round.get("selected_candidate_url") or recovery_round.get("candidate_url") or "").strip()
            provider_urls = [
                str(url)
                for url in (recovery_round.get("candidate_urls") if isinstance(recovery_round.get("candidate_urls"), list) else [])
                if str(url or "").strip()
            ]
            provider_urls = provider_urls or [selected_url]
            selected_round = int(recovery_round.get("round") or qa_attempt_count or 1)
            selected_stage = str(recovery_round.get("stage") or "recovered_image_edit_round")
            candidate_scores = (
                recovery_round.get("candidate_scores")
                if isinstance(recovery_round.get("candidate_scores"), list)
                else []
            )
            await self._complete_order(
                order_uuid,
                delivered_urls=[selected_url],
                provider_urls=provider_urls,
                qa_attempt_count=qa_attempt_count,
                is_couple=bool(subject_count and int(subject_count) >= 2),
                subject_count=subject_count,
                couple_flow=couple_flow,
                selected_round=selected_round,
                selected_stage=selected_stage,
                selection_summary={
                    "policy": self.CANDIDATE_SELECTION_POLICY,
                    "selected_round": selected_round,
                    "selected_stage": selected_stage,
                    "selected_candidate_index": int(recovery_round.get("selected_candidate_index") or 0),
                    "score": max(
                        [
                            float(candidate.get("score") or 0.0)
                            for candidate in candidate_scores
                            if isinstance(candidate, dict)
                        ]
                        or [0.0]
                    ),
                    "candidate_scores": candidate_scores,
                    "recovered_from_completed_round": True,
                },
            )
            await self._queue_completion_email(order_uuid)
            return True

        if pending_vision_recheck_params is not None and retry_context is not None:
            handled = await self._retry_pending_vision_recheck(
                order_uuid,
                params=pending_vision_recheck_params,
                user_images=[str(url) for url in (retry_context.get("user_images") or []) if str(url or "").strip()],
                subject_count=retry_context.get("subject_count"),
                couple_flow=retry_context.get("couple_flow"),
            )
            if handled:
                return True

        if retry_context is not None:
            await self.generate_photo(**retry_context)
            return True

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
                follow_redirects=True,
                trust_env=False,
            ) as client:
                response = await client.get(self._task_url(task_id), headers=self._headers())
            if response.status_code in {401, 403}:
                raise RuntimeError(f"wenwen_auth_failed:{response.status_code}")
            response.raise_for_status()
            payload = response.json() if response.content else {}
        except Exception as exc:
            await self._record_provider_poll(
                order_uuid,
                params=params,
                status_value=None,
                output_count=0,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            return False

        task_status = self._extract_status_value(payload)
        source_set = {url.strip() for url in user_images if url and url.strip()}
        output_urls = [url for url in self._extract_output_urls(payload) if url not in source_set]
        await self._record_provider_poll(
            order_uuid,
            params=params,
            status_value=task_status,
            output_count=len(output_urls),
            payload=payload if isinstance(payload, dict) else None,
        )

        if self._status_terminal_failure(task_status):
            error_message = self._extract_error_message(payload) or f"wenwen_task_failed:{task_status or 'unknown'}"
            await self._fail_order(order_uuid, error_message, self._classify_error(RuntimeError(error_message)))
            return True

        if self._status_terminal_success(task_status, has_outputs=bool(output_urls)):
            try:
                await self._complete_provider_urls(
                    order_uuid,
                    provider_urls=output_urls,
                    user_images=user_images,
                    subject_count=subject_count,
                    couple_flow=couple_flow,
                    qa_attempt_count=qa_attempt_count,
                )
                await self._queue_completion_email(order_uuid)
            except Exception as exc:
                await self._fail_order(order_uuid, str(exc), self._classify_error(exc))
            return True

        return False

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
        identity_reference_pack: dict | None = None,
        scene_image_url: str | None = None,
        clothing_image_url: str | None = None,
        **_: Any,
    ) -> None:
        order_uuid = uuid.UUID(str(order_id))
        try:
            if time.monotonic() < self._circuit_open_until:
                raise RuntimeError(
                    f"{self.PROVIDER}_circuit_open: provider unavailable, retry after {int(self._circuit_open_until - time.monotonic())}s"
                )
            self.validate_runtime_requirements()
            template = get_template_by_id(template_id)
            if not template:
                raise ValueError("template_not_found")

            identity_edit_required = self._identity_edit_required(user_images)
            if settings.is_vercel_runtime and self._supports_provider_task_submission() and not identity_edit_required:
                task_id, output_urls, provider_payload, prompt_text, negative_prompt = await self._submit_provider_task(
                    template=template,
                    user_images=list(user_images or []),
                    subject_count=subject_count,
                    prompt_override=prompt_override,
                    global_style_text=global_style_text,
                    scene_text=scene_text,
                    outfit_text=outfit_text,
                    identity_reference_pack=identity_reference_pack,
                    scene_image_url=scene_image_url,
                    clothing_image_url=clothing_image_url,
                    couple_flow=couple_flow,
                )
                if not task_id and not output_urls:
                    raise RuntimeError("wenwen_task_missing")

                stored_task_id = task_id or f"provider-immediate-{order_id}"
                await self._update_order_generating(
                    order_uuid,
                    task_id=stored_task_id,
                    payload=provider_payload,
                    prompt_text=prompt_text,
                    negative_prompt=negative_prompt,
                    provider_task_id=task_id,
                    provider_task_status="submitted" if task_id else "completed",
                    generation_mode="provider_task",
                )
                if output_urls:
                    await self._complete_provider_urls(
                        order_uuid,
                        provider_urls=output_urls,
                        user_images=list(user_images or []),
                        subject_count=subject_count,
                        couple_flow=couple_flow,
                        qa_attempt_count=1,
                    )
                    await self._queue_completion_email(order_uuid)
                self._consecutive_failures = 0
                self._circuit_open_until = 0
                return

            provider_payload, prompt_text, negative_prompt = await self._build_payload(
                template=template,
                user_images=list(user_images or []),
                subject_count=subject_count,
                prompt_override=prompt_override,
                global_style_text=global_style_text,
                scene_text=scene_text,
                outfit_text=outfit_text,
                identity_reference_pack=identity_reference_pack,
                scene_image_url=scene_image_url,
                clothing_image_url=clothing_image_url,
                couple_flow=couple_flow,
                prompt_enrichment=not settings.is_vercel_runtime,
            )
            refs = list(provider_payload.get("images") or [])
            if identity_edit_required and not refs:
                raise RuntimeError("identity_edit_reference_missing")
            try:
                image_edit_completed = await self._run_image_edit_generation(
                    order_uuid,
                    refs=refs,
                    prompt_text=prompt_text,
                    negative_prompt=negative_prompt,
                    user_images=list(user_images or []),
                    identity_reference_pack=identity_reference_pack,
                    subject_count=subject_count,
                    couple_flow=couple_flow,
                )
            except RuntimeError as exc:
                error_text = str(exc)
                if error_text.startswith((
                    "wenwen_auth_failed",
                    "wenwen_quota_rejected",
                    f"{self.PROVIDER}_auth_failed",
                    f"{self.PROVIDER}_quota_rejected",
                )):
                    raise
                if identity_edit_required:
                    raise RuntimeError(f"identity_edit_required_failed:{error_text}") from exc
                logger.warning("Wenwen image edit path failed; trying native generation path: %s", exc)
                image_edit_completed = False
            if image_edit_completed:
                await self._queue_completion_email(order_uuid)
                self._consecutive_failures = 0
                self._circuit_open_until = 0
                return
            if identity_edit_required:
                raise RuntimeError("identity_edit_required_not_completed")

            native_payload = await self._native_payload_from_provider_payload(
                provider_payload,
                prompt_text=prompt_text,
                negative_prompt=negative_prompt,
                subject_count=subject_count,
            )

            max_retries = max(0, settings.wenwen_max_retries)
            is_couple = bool(subject_count and int(subject_count) >= 2)
            delivered_urls: list[str] = []
            native_models = self._native_model_candidates()
            for attempt in range(1 + max_retries):
                try:
                    response: httpx.Response | None = None
                    selected_model = native_models[0] if native_models else self._effective_image_model()
                    last_transient_error: Exception | None = None
                    for model_index, native_model in enumerate(native_models or [self._effective_image_model()]):
                        read_timeout = self._native_read_timeout(
                            model_index=model_index,
                            model_count=len(native_models) or 1,
                        )
                        try:
                            async with httpx.AsyncClient(
                                timeout=httpx.Timeout(connect=10.0, read=read_timeout, write=120.0, pool=10.0),
                                follow_redirects=True,
                                trust_env=False,
                            ) as client:
                                candidate_response = await client.post(
                                    self._native_generation_request_url_for_model(native_model),
                                    json=native_payload,
                                    headers=self._native_headers(),
                                )
                        except (httpx.TimeoutException, httpx.ConnectError) as exc:
                            last_transient_error = exc
                            if model_index < len(native_models) - 1:
                                logger.warning(
                                    "Wenwen native model transient error on %s; trying configured candidate %s: %s",
                                    native_model,
                                    native_models[model_index + 1],
                                    exc,
                                )
                                continue
                            raise

                        if candidate_response.status_code in {400, 422, 500, 503}:
                            lowered = candidate_response.text.lower()
                            model_unavailable = (
                                "model_not_found" in lowered
                                or "no available channel" in lowered
                                or "not supported model for image generation" in lowered
                            )
                            if model_unavailable and model_index < len(native_models) - 1:
                                logger.warning(
                                    "Wenwen native model unavailable on %s; trying configured candidate %s: %s",
                                    native_model,
                                    native_models[model_index + 1],
                                    candidate_response.status_code,
                                )
                                continue

                        response = candidate_response
                        selected_model = native_model
                        break

                    if response is None:
                        if last_transient_error:
                            raise last_transient_error
                        raise RuntimeError("wenwen_response_missing")

                    if response.status_code in {401, 403}:
                        raise RuntimeError(f"wenwen_auth_failed:{response.status_code}")
                    if response.status_code in {402, 429}:
                        raise RuntimeError(f"wenwen_quota_rejected:{response.status_code}:{response.text[:200]}")
                    if response.status_code in {400, 422, 500, 503}:
                        lowered = response.text.lower()
                        if "model_not_found" in lowered or "no available channel" in lowered or "not supported model for image generation" in lowered:
                            raise RuntimeError(
                                f"wenwen_model_unavailable:{selected_model}:{response.status_code}:{response.text[:240]}"
                            )
                        if response.status_code >= 500 and attempt < max_retries:
                            logger.warning("Wenwen 5xx (attempt %d/%d): %s", attempt + 1, 1 + max_retries, response.status_code)
                            await asyncio.sleep(min(2 ** attempt, 8))
                            continue
                    response.raise_for_status()

                    submission = response.json()
                    task_id = self._extract_task_id(submission) or f"inline-{order_id}-{attempt + 1}"

                    await self._update_order_generating(
                        order_uuid,
                        task_id=task_id,
                        payload={**native_payload, "_provider_model": selected_model},
                        prompt_text=prompt_text,
                        negative_prompt=negative_prompt,
                    )

                    content = ((submission.get("candidates") or [{}])[0] or {}).get("content") or {}
                    parts = content.get("parts") or []
                    delivered_urls = await self._persist_inline_outputs_to_storage(parts, order_uuid)
                    if not delivered_urls:
                        raise RuntimeError("wenwen_outputs_missing")
                    await self._record_native_raw_outputs(
                        order_uuid,
                        delivered_urls=delivered_urls,
                        provider_payload_keys=list(native_payload.keys()),
                        qa_attempt_count=attempt + 1,
                    )

                    primary_image_url = delivered_urls[0]
                    await self._update_generation_stage(order_uuid, "qa_checking", detail=f"attempt_{attempt + 1}")
                    qa_verdict = await output_verdict(
                        primary_image_url,
                        is_couple=is_couple,
                        source_image_urls=[str(url) for url in user_images if url],
                    )
                    qa_ok = bool(qa_verdict.get("passed"))
                    qa_reasons = list(qa_verdict.get("reasons") or [])
                    qa_issues = [issue for issue in (qa_verdict.get("issues") or []) if isinstance(issue, dict)]
                    if not qa_ok:
                        await self._record_qa_failure(
                            order_uuid,
                            attempt=attempt + 1,
                            reasons=qa_reasons,
                            candidate_url=primary_image_url,
                            issues=qa_issues,
                        )
                        if settings.is_vercel_runtime and self._should_retry_qa(qa_reasons, attempt + 1):
                            await self._mark_qa_retry_pending(
                                order_uuid,
                                attempt=attempt + 1,
                                reasons=qa_reasons,
                                candidate_url=primary_image_url,
                            )
                            logger.warning(
                                "Wenwen QA failed; queued automatic retry (attempt %d/%d): %s",
                                attempt + 1,
                                self.QA_MAX_ATTEMPTS,
                                qa_reasons,
                            )
                            return
                        if attempt < max_retries:
                            logger.warning("Wenwen QA failed; regenerating (attempt %d/%d): %s", attempt + 1, 1 + max_retries, qa_reasons)
                            continue
                        raise ValueError(f"QA failed: {','.join(qa_reasons)}")

                    await self._complete_order(
                        order_uuid,
                        delivered_urls=delivered_urls,
                        provider_urls=[],
                        qa_attempt_count=attempt + 1,
                        is_couple=is_couple,
                        subject_count=subject_count,
                        couple_flow=couple_flow,
                    )
                    break
                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    if attempt < max_retries:
                        logger.warning("Wenwen transient error (attempt %d/%d): %s", attempt + 1, 1 + max_retries, exc)
                        await asyncio.sleep(min(2 ** attempt, 8))
                        continue
                    raise
            else:
                raise RuntimeError("wenwen_generation_exhausted")

            await self._queue_completion_email(order_uuid)

            self._consecutive_failures = 0
            self._circuit_open_until = 0
        except Exception as exc:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.CIRCUIT_FAILURE_THRESHOLD:
                self._circuit_open_until = time.monotonic() + self.CIRCUIT_COOLDOWN_SECONDS
                logger.error("%s circuit breaker OPEN after %d consecutive failures", self.PROVIDER, self._consecutive_failures)
            logger.error("%s generation failed: %s", self.PROVIDER, exc)
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except ImportError:
                pass
            error_text = str(exc).strip() or type(exc).__name__
            await self._fail_order(order_uuid, error_text, self._classify_error(exc))

    async def generate_live_portrait(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("wenwen_live_portrait_unsupported")
