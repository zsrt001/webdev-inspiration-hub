"""Wenwen OpenAI-compatible image generation provider."""

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
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.models.order import Order, OrderStatus
from app.services.trial_access_service import prepare_delivered_image_urls, is_trial_order
from app.models.credit_transaction import CreditTransactionType
from app.services import llm_service
from app.services.credit_service import COST_PER_GENERATION, add_credits_async
from app.services.prompt_brain import build_prompt, get_negative_prompt
from app.services.qa_service import output_passes
from app.services.storage import storage_service
from app.services.template_service import get_template_by_id

logger = logging.getLogger(__name__)
settings = get_settings()


class WenwenService:
    """Best-effort provider for Wenwen's hosted Gemini image models."""

    CIRCUIT_FAILURE_THRESHOLD = 5
    CIRCUIT_COOLDOWN_SECONDS = 60
    QA_MAX_ATTEMPTS = 2
    QA_RETRY_REASONS = {
        "bad_hands",
        "extra_limbs",
        "face_distortion",
        "cropped_face",
        "fused_faces",
        "body_fusion",
        "severe_artifacts",
        "dress_exposure_error",
        "identity_mismatch",
        "identity_swap",
        "subject_missing",
        "headless",
        "other",
    }

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
    def _native_generation_url(cls) -> str:
        template = str(settings.wenwen_native_image_generate_path_template or "").strip()
        if not template:
            template = "/v1beta/models/{model}:generateContent"
        path = template.replace("{model}", cls._effective_image_model())
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{cls._origin_url()}{cls._normalize_path(path)}"

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

    @staticmethod
    def _supports_provider_task_submission() -> bool:
        model = WenwenService._effective_image_model().lower()
        return bool(model and not model.startswith("gemini"))

    @staticmethod
    def _effective_image_model() -> str:
        return str(settings.wenwen_image_model or "").strip()

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.wenwen_api_key}",
            "Content-Type": "application/json",
        }

    def validate_runtime_requirements(self, *, force: bool = False) -> None:
        if self._runtime_validation_ok and not force:
            return
        errors: list[str] = []
        if not settings.wenwen_api_key:
            errors.append("WENWEN_API_KEY is required")
        if not settings.wenwen_api_base_url:
            errors.append("WENWEN_API_BASE_URL is required")
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

    async def _coerce_remote_image_ref(self, image_url: str) -> str:
        raw = str(image_url or "").strip()
        if not raw:
            return raw
        if raw.startswith("data:"):
            return raw
        if not raw.startswith(("http://", "https://")):
            return raw
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, trust_env=False) as client:
            response = await client.get(raw)
            response.raise_for_status()
            content_type = response.headers.get("content-type") or "image/jpeg"
            encoded = base64.b64encode(response.content).decode("utf-8")
            return f"data:{content_type};base64,{encoded}"

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
                items.extend(WenwenService._walk_values(value, current_key=str(key)))
        elif isinstance(payload, list):
            for value in payload:
                items.extend(WenwenService._walk_values(value, current_key=current_key))
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
        if int(attempt or 0) >= cls.QA_MAX_ATTEMPTS:
            return False
        normalized = {str(reason or "").strip() for reason in reasons if str(reason or "").strip()}
        if not normalized:
            return True
        return bool(normalized & cls.QA_RETRY_REASONS)

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
        qa_ok, qa_reasons = await output_passes(
            primary_image_url,
            is_couple=is_couple,
            source_image_urls=[str(url) for url in user_images if url],
        )
        if not qa_ok:
            await self._record_qa_failure(
                order_uuid,
                attempt=qa_attempt_count,
                reasons=qa_reasons,
                candidate_url=primary_image_url,
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
    ) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            debug = dict(params.get("debug")) if isinstance(params.get("debug"), dict) else {}
            retry_history = debug.get("qa_retry_history") if isinstance(debug.get("qa_retry_history"), list) else []
            retry_history.append(
                {
                    "attempt": int(attempt),
                    "reasons": list(reasons),
                    "candidate_url": candidate_url,
                    "queued_at": self._utc_now_iso(),
                }
            )
            debug["qa_retry_history"] = retry_history[-8:]
            params["debug"] = debug
            params["qa_retry_pending"] = True
            params["qa_retry_in_progress"] = False
            params["qa_retry_next_attempt"] = int(attempt) + 1
            params["qa_retry_max_attempts"] = self.QA_MAX_ATTEMPTS
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
        normalized = str(configured or "").strip().lower().replace("x", ":").replace("/", ":")
        if not is_couple and normalized == "4:5":
            return "3:4"
        return str(configured or "").strip() or ("3:2" if is_couple else "3:4")

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
        scene_image_url: str | None,
        clothing_image_url: str | None,
        couple_flow: str | None,
        prompt_enrichment: bool = True,
    ) -> tuple[dict[str, Any], str, str]:
        is_couple = bool(subject_count and int(subject_count) >= 2)
        prompt_text = build_prompt(
            template=template,
            user_text=prompt_override or global_style_text,
            scene_text=scene_text,
            clothing_text=outfit_text,
            is_couple=is_couple,
        )
        negative_prompt = get_negative_prompt()

        if prompt_enrichment:
            prompt_text = await llm_service.optimize_generation_prompt(prompt_text, is_couple=is_couple)
            subject_hints = await self._build_subject_hints(user_images)
            if subject_hints:
                prompt_text = f"{prompt_text} Identity guidance: {' '.join(subject_hints)}."

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
            scene_image_url=scene_image_url,
            clothing_image_url=clothing_image_url,
            couple_flow=couple_flow,
            prompt_enrichment=prompt_enrichment,
        )
        refs = list(payload.get("images") or [])
        parts: list[dict[str, Any]] = [{"text": f"{prompt_text}\nNegative prompt: {negative_prompt}"}]
        for ref in refs[:3]:
            coerced = await self._coerce_remote_image_ref(ref)
            parts.append(self._data_url_to_inline_part(coerced))
        native_payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "temperature": 0.8,
                "imageConfig": {
                    "aspectRatio": self._build_size(bool(subject_count and int(subject_count) >= 2)),
                },
            },
        }
        return native_payload, prompt_text, negative_prompt

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

    async def _persist_inline_outputs_to_storage(self, parts: list[dict[str, Any]], order_uuid: uuid.UUID) -> list[str]:
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
            filename = f"order_{order_uuid}_media_{index + 1}{ext}"
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
            order.status = OrderStatus.GENERATING
            order.task_id = task_id
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            debug = dict(params.get("debug")) if isinstance(params.get("debug"), dict) else {}
            debug.update(
                {
                    "wenwen_submit_payload_keys": sorted(payload.keys()),
                    "wenwen_model": self._effective_image_model(),
                    "wenwen_configured_model": settings.wenwen_image_model,
                    "wenwen_submitted_at": self._utc_now_iso(),
                }
            )
            params.update(
                {
                    "engine": "wenwen",
                    "provider": "wenwen",
                    "prompt": prompt_text,
                    "negative_prompt": negative_prompt,
                    "debug": debug,
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
    ) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return
            order.status = OrderStatus.COMPLETED
            params = order.generation_params if isinstance(order.generation_params, dict) else {}
            preview_urls, final_urls, preview_meta = await prepare_delivered_image_urls(
                delivered_urls,
                trial_preview=is_trial_order(params),
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
            params["qa_attempt_count"] = qa_attempt_count
            params["qa_retry_pending"] = False
            params["qa_retry_in_progress"] = False
            params["couple_guardrails"] = {
                "is_couple": bool(is_couple),
                "subject_count": subject_count,
                "couple_flow": couple_flow,
            }
            order.generation_params = params
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
                        )
                    )
        except Exception as mail_exc:
            logger.debug("Order completion email skipped: %s", mail_exc)

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
            refund_amount = COST_PER_GENERATION
            clean_error_message = str(error_message or "").strip() or failure_code or "unknown_generation_error"
            if order and isinstance(order.generation_params, dict):
                params = order.generation_params
                try:
                    if "credits_cost" in params:
                        refund_amount = max(0, int(params.get("credits_cost") or 0))
                except Exception:
                    refund_amount = COST_PER_GENERATION
            if order and order.user_id and refund_amount:
                await add_credits_async(
                    db,
                    order.user_id,
                    refund_amount,
                    transaction_type=CreditTransactionType.GENERATION_REFUND,
                    source="order",
                    source_id=str(order.id),
                    description=f"Generation failed: {failure_code}",
                )
            if order:
                order.status = OrderStatus.CREATED
                order.error_message = clean_error_message
                params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
                params["failure_code"] = failure_code
                params["failure_provider"] = "wenwen"
                if refund_amount:
                    params["refunded_credits"] = refund_amount
                order.generation_params = params
                await db.commit()

    async def _record_qa_failure(
        self,
        order_uuid: uuid.UUID,
        *,
        attempt: int,
        reasons: list[str],
        candidate_url: str,
    ) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return
            params = order.generation_params if isinstance(order.generation_params, dict) else {}
            debug = params.get("debug") if isinstance(params.get("debug"), dict) else {}
            qa_history = debug.get("qa_history") if isinstance(debug.get("qa_history"), list) else []
            qa_history.append(
                {
                    "attempt": int(attempt),
                    "reasons": list(reasons),
                    "candidate_url": candidate_url,
                    "engine": "wenwen",
                }
            )
            debug["qa_history"] = qa_history[-8:]
            params["debug"] = debug
            params["qa_last_reasons"] = list(reasons)
            params["qa_attempt_count"] = int(attempt)
            order.generation_params = params
            await db.commit()

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
        async with async_session_maker() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if not order:
                return False
            status_value = order.status.value if isinstance(order.status, OrderStatus) else str(order.status or "")
            if status_value != OrderStatus.GENERATING.value:
                return False
            params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
            if bool(params.get("qa_retry_pending")):
                retry_started_at = params.get("qa_retry_started_at")
                if bool(params.get("qa_retry_in_progress")) and (self._seconds_since(retry_started_at) or 0) < 20 * 60:
                    return False
                retry_context = await self._load_order_generation_context(order_uuid)
                if not retry_context:
                    await self._fail_order(order_uuid, "qa_retry_missing_generation_context", "qa_reject")
                    return True
                await self._mark_qa_retry_started(order_uuid)
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
            task_id = str(params.get("provider_task_id") or params.get("wenwen_task_id") or "").strip()
            if retry_context is not None:
                task_id = "__qa_retry__"
            if not task_id:
                if str(params.get("execution_mode") or "").strip() == "inline":
                    age_seconds = self._seconds_since(order.updated_at)
                    if age_seconds is not None and age_seconds > 20 * 60:
                        await self._fail_order(
                            order_uuid,
                            "generation_runtime_interrupted_before_output",
                            "generation_timeout",
                        )
                        return True
                return False
            debug = dict(params.get("debug")) if isinstance(params.get("debug"), dict) else {}
            if retry_context is None and self._recently_polled(debug):
                return False
            user_images = self._order_source_images(order)
            try:
                subject_count = int(params.get("subject_count") or len(user_images) or 0) or None
            except Exception:
                subject_count = len(user_images) or None
            couple_flow = str(params.get("couple_flow") or "") or None
            qa_attempt_count = int(params.get("qa_attempt_count") or 1)

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
        scene_image_url: str | None = None,
        clothing_image_url: str | None = None,
        **_: Any,
    ) -> None:
        order_uuid = uuid.UUID(str(order_id))
        try:
            if time.monotonic() < self._circuit_open_until:
                raise RuntimeError(
                    f"wenwen_circuit_open: provider unavailable, retry after {int(self._circuit_open_until - time.monotonic())}s"
                )
            self.validate_runtime_requirements()
            template = get_template_by_id(template_id)
            if not template:
                raise ValueError("template_not_found")

            if settings.is_vercel_runtime and self._supports_provider_task_submission():
                task_id, output_urls, provider_payload, prompt_text, negative_prompt = await self._submit_provider_task(
                    template=template,
                    user_images=list(user_images or []),
                    subject_count=subject_count,
                    prompt_override=prompt_override,
                    global_style_text=global_style_text,
                    scene_text=scene_text,
                    outfit_text=outfit_text,
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

            native_payload, prompt_text, negative_prompt = await self._build_native_payload(
                template=template,
                user_images=list(user_images or []),
                subject_count=subject_count,
                prompt_override=prompt_override,
                global_style_text=global_style_text,
                scene_text=scene_text,
                outfit_text=outfit_text,
                scene_image_url=scene_image_url,
                clothing_image_url=clothing_image_url,
                couple_flow=couple_flow,
                prompt_enrichment=not settings.is_vercel_runtime,
            )

            max_retries = max(0, settings.wenwen_max_retries)
            is_couple = bool(subject_count and int(subject_count) >= 2)
            delivered_urls: list[str] = []
            for attempt in range(1 + max_retries):
                try:
                    read_timeout = 285.0 if settings.is_vercel_runtime else max(120.0, float(settings.wenwen_poll_timeout or 240))
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(connect=10.0, read=read_timeout, write=30.0, pool=10.0),
                        follow_redirects=True,
                        trust_env=False,
                    ) as client:
                        response = await client.post(
                            self._native_generation_url(), json=native_payload, headers=self._headers(),
                        )
                    if response.status_code in {401, 403}:
                        raise RuntimeError(f"wenwen_auth_failed:{response.status_code}")
                    if response.status_code in {402, 429}:
                        raise RuntimeError(f"wenwen_quota_rejected:{response.status_code}:{response.text[:200]}")
                    if response.status_code in {400, 422, 500, 503}:
                        lowered = response.text.lower()
                        if "model_not_found" in lowered or "no available channel" in lowered or "not supported model for image generation" in lowered:
                            raise RuntimeError(
                                f"wenwen_model_unavailable:{self._effective_image_model()}:{response.status_code}:{response.text[:240]}"
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
                        payload=native_payload,
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
                    qa_ok, qa_reasons = await output_passes(
                        primary_image_url,
                        is_couple=is_couple,
                        source_image_urls=[str(url) for url in user_images if url],
                    )
                    if not qa_ok:
                        await self._record_qa_failure(
                            order_uuid,
                            attempt=attempt + 1,
                            reasons=qa_reasons,
                            candidate_url=primary_image_url,
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
                logger.error("Wenwen circuit breaker OPEN after %d consecutive failures", self._consecutive_failures)
            logger.error("Wenwen generation failed: %s", exc)
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except ImportError:
                pass
            error_text = str(exc).strip() or type(exc).__name__
            await self._fail_order(order_uuid, error_text, self._classify_error(exc))

    async def generate_live_portrait(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("wenwen_live_portrait_unsupported")


wenwen_service = WenwenService()
