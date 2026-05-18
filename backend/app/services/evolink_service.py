"""Evolink generation provider adapter for the production order pipeline."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.models.order import Order, OrderStatus
from app.services.trial_access_service import prepare_delivered_image_urls, is_trial_order
from app.services.generation_stage_service import merge_generation_stage
from app.services.provider_workflow import GenerationProviderWorkflow


logger = logging.getLogger(__name__)
settings = get_settings()


class EvolinkService(GenerationProviderWorkflow):
    """Evolink provider adapter bound to the shared workflow and policy."""

    PROVIDER = "evolink"
    # EvoLink's Nano Banana 2 endpoint documents prompt max length as 2000.
    # Keep a little headroom because production prompts contain dense guardrails.
    PROMPT_CHAR_LIMIT = 1900

    @staticmethod
    def _base_url() -> str:
        return (settings.evolink_api_base_url or "").rstrip("/")

    @classmethod
    def _image_generation_url(cls) -> str:
        return f"{cls._base_url()}/v1/images/generations"

    @classmethod
    def _task_url(cls, task_id: str) -> str:
        return f"{cls._base_url()}/v1/tasks/{task_id}"

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.evolink_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _effective_image_model() -> str:
        return str(settings.evolink_image_model or "").strip()

    @classmethod
    def _effective_image_edit_model(cls) -> str:
        return cls._effective_image_model()

    @staticmethod
    def _image_edit_uses_native_model(model: str | None) -> bool:
        return False

    @classmethod
    def _image_edit_model_candidates(cls, model: str) -> list[str]:
        candidate = str(model or "").strip()
        if candidate and cls._allowed_image_model(candidate):
            return [candidate]
        return []

    def validate_runtime_requirements(self, *, force: bool = False) -> None:
        if self._runtime_validation_ok and not force:
            return
        errors: list[str] = []
        if not settings.evolink_api_key:
            errors.append("EVOLINK_API_KEY is required")
        if not settings.evolink_api_base_url:
            errors.append("EVOLINK_API_BASE_URL is required")
        if not self._effective_image_edit_model():
            errors.append("EVOLINK_IMAGE_MODEL is required")
        if self._effective_image_edit_model() and not settings.generation_image_model_allowed(
            self._effective_image_edit_model()
        ):
            errors.append(f"EVOLINK_IMAGE_MODEL is not allowed: {self._effective_image_edit_model()}")
        fallback_models = [
            item.strip()
            for item in (settings.evolink_image_fallback_models or "").split(",")
            if item.strip()
        ]
        if fallback_models:
            errors.append("EVOLINK_IMAGE_FALLBACK_MODELS must be empty; run explicit model comparisons instead")
        if errors:
            raise ValueError("; ".join(errors))
        self._runtime_validation_ok = True

    async def ping_runtime(self) -> tuple[bool, str]:
        self.validate_runtime_requirements(force=True)
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, trust_env=False) as client:
            response = await client.get(f"{self._base_url()}/v1/models", headers=self._headers())
        if response.status_code in {200, 404, 405}:
            return True, f"http_{response.status_code}"
        if response.status_code in {401, 403}:
            raise RuntimeError(f"evolink_auth_failed:{response.status_code}")
        return True, f"http_{response.status_code}"

    @staticmethod
    def _evolink_size() -> str:
        return str(settings.evolink_image_size or "3:4").strip() or "3:4"

    @staticmethod
    def _evolink_quality() -> str:
        return str(settings.evolink_image_quality or "2K").strip() or "2K"

    @classmethod
    def _compact_prompt_for_evolink(cls, prompt: str, *, is_couple: bool = False) -> str:
        """Keep Evolink prompts under provider limits without dropping hard gates."""
        cleaned = "\n".join(line.strip() for line in str(prompt or "").splitlines() if line.strip())
        if len(cleaned) <= cls.PROMPT_CHAR_LIMIT:
            return cleaned

        sentences = [item.strip() for item in re.split(r"(?<=[.!?。！？])\s+", cleaned) if item.strip()]
        deduped: list[str] = []
        seen: set[str] = set()
        for sentence in sentences:
            normalized = re.sub(r"\s+", " ", sentence).strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(sentence)

        subject_count_hint = (
            "Couple rule: output exactly two primary wedding subjects in the same frame, bride/person A and groom/person B. "
            "Never create a solo portrait, never omit either subject, and preserve each identity separately with no role swap, face merge, duplicate identity, or body fusion. "
            if is_couple
            else "Single rule: output exactly one primary human subject, the uploaded source person only. Never add a spouse, partner, groom, bride, guest, duplicate body, second face, or background person. "
        )
        hard_header = (
            "Identity-preserving image edit using image_urls as source references. "
            "Do not do text-only generation. Preserve exact face shape, eyes, nose bridge and tip, nostrils, mouth, jawline, side-profile silhouette, age impression, skin undertone, and natural expression. Never enlarge or stylize the nose. "
            f"{subject_count_hint}"
            "Negative: generic face, identity change, enlarged nose, altered nose, altered side-profile silhouette, copied source outfit/background against user text, plastic or oily skin, bad hands, extra limbs, fused bodies, harsh backlight, blown white dress, mixed color temperature, background brighter than face, watermark, text. "
            "Template style lock: obey the selected template's WARDROBE, SCENE, and TEMPLATE STYLE NOTES clauses exactly unless the user provided explicit director overrides. "
            "When USER DIRECTION, scene text, outfit text, or prompt_override is present, follow that user text as the creative brief and use the template only for unspecified details. "
            "In user-text mode, image_urls are identity references only: do not copy the source photo's original background, location, dress, suit, bouquet, veil, or color palette when they conflict with user text. "
            "Do not switch style family, wedding role, outfit concept, indoor/outdoor setting, or background concept. If the template says indoor studio, never generate an outdoor garden, terrace, balcony, travel, mountain, or landscape scene. "
            "Commercial wedding deliverable: professional studio/on-location lighting, natural skin texture, catchlights, face correctly exposed, complete wedding wardrobe, readable face, 3:4 vertical composition, no awkward crop. "
            "Use the shot-library direction: primary shot, commercial subject scale, readable face, intentional headroom, complete gown/suit boundaries, and professional pose family. "
            "Use simple professional hand posing with bouquet, veil, sleeve, or gown fabric covering difficult fingers; preserve full gown hem, shoes, veil/train, and bottom breathing room. "
            "Use sun only as rim/ambient light outdoors; keep soft frontal fill."
        ).strip()

        selected: list[str] = [hard_header]
        current_len = len(hard_header)

        priority_groups = (
            ("round", "qa reasons", "qa notes", "targeted repair", "relight", "polish"),
            ("user text priority", "user direction", "creative brief", "prompt override"),
            ("template style lock", "wardrobe:", "scene:", "template style notes"),
            ("scene", "outdoor", "garden", "castle", "studio", "window", "architecture", "background"),
            ("shot library", "primary shot", "candidate shot", "pose family", "must show"),
            ("lighting", "soft key", "fill light", "rim light", "catchlight", "exposure", "backlight"),
            ("composition", "canvas", "full body", "headroom", "face readable", "crop", "subject"),
            ("wardrobe", "wedding gown", "dress", "veil", "suit", "fabric", "lace"),
            ("negative prompt", "forbid", "avoid", "no "),
        )

        def try_add(sentence: str) -> None:
            nonlocal current_len
            compact_sentence = re.sub(r"\s+", " ", sentence).strip()
            if not compact_sentence:
                return
            sentence_len = len(compact_sentence) + 1
            if current_len + sentence_len > cls.PROMPT_CHAR_LIMIT:
                return
            selected.append(compact_sentence)
            current_len += sentence_len

        used: set[int] = set()
        for group in priority_groups:
            for index, sentence in enumerate(deduped):
                if index in used:
                    continue
                lower = sentence.lower()
                if not any(marker in lower for marker in group):
                    continue
                before = current_len
                try_add(sentence)
                if current_len != before:
                    used.add(index)

        compacted = " ".join(selected)
        if len(compacted) <= cls.PROMPT_CHAR_LIMIT:
            return compacted

        clipped = compacted[: cls.PROMPT_CHAR_LIMIT]
        return clipped.rsplit(" ", 1)[0] or clipped

    @staticmethod
    def _evolink_usage(payload: Any, usage: dict[str, float] | None = None) -> dict[str, float]:
        usage = usage if usage is not None else {}
        if isinstance(payload, list):
            for item in payload:
                EvolinkService._evolink_usage(item, usage)
            return usage
        if not isinstance(payload, dict):
            return usage
        for key, value in payload.items():
            lower = str(key).lower()
            if lower in {"credits_reserved", "credits_used", "cost", "total_cost"}:
                try:
                    usage[lower] = float(value)
                except Exception:
                    pass
            elif isinstance(value, (dict, list)):
                EvolinkService._evolink_usage(value, usage)
        return usage

    def _evolink_reference_entries(
        self,
        *,
        identity_refs: list[str],
        style_refs: list[str],
        current_result_refs: list[str],
        identity_reference_pack: dict | None,
        include_previous_result: bool,
        is_couple: bool,
        user_text_mode: bool = False,
    ) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(label: str, url: Any) -> None:
            value = str(url or "").strip()
            if not value or value in seen:
                return
            seen.add(value)
            entries.append((label, value))

        subjects = self._identity_pack_subjects(identity_reference_pack)
        if subjects:
            for subject in subjects[:2]:
                role = str(subject.get("role") or subject.get("identity_label") or "subject")
                # Face crop FIRST (highest priority for flash native image-edit),
                # then original portrait as full-body anchor
                add(f"{role} face reference - IDENTITY ANCHOR", subject.get("face_crop_url"))
                if user_text_mode:
                    continue
                if not (is_couple and include_previous_result):
                    add(f"{role} full reference", subject.get("original_url"))
                if not is_couple and not include_previous_result:
                    add(f"{role} upper-body reference", subject.get("upper_body_crop_url"))
        else:
            identity_limit = 1 if user_text_mode and not is_couple else 2
            for index, url in enumerate(identity_refs[:identity_limit], start=1):
                add(f"identity anchor image {index}", url)

        if include_previous_result:
            for url in current_result_refs[:1]:
                add("previous result canvas - composition reference only", url)

        for index, url in enumerate(style_refs, start=1):
            add(f"style reference image {index}", url)

        # Nano Banana 2 accepts up to 14 image_urls, but its docs cap real-person
        # reference images at 4. Couple repair rounds already include a generated
        # candidate with two people, so keep identity anchors concise there.
        max_entries = 4 if is_couple else 5
        return entries[:max_entries]

    async def _poll_task(self, client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + max(30, int(settings.evolink_poll_timeout or 720))
        latest: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = await client.get(self._task_url(task_id), headers=self._headers())
            if response.status_code in {401, 403}:
                raise RuntimeError(f"evolink_auth_failed:{response.status_code}")
            if response.status_code in {402, 429}:
                raise RuntimeError(f"evolink_quota_rejected:{response.status_code}:{response.text[:200]}")
            response.raise_for_status()
            latest = response.json() if response.content else {}
            status = str(self._extract_status_value(latest) or "").strip().lower()
            if status in {"completed", "succeeded", "success", "failed", "error", "cancelled", "canceled"}:
                return latest
            await asyncio.sleep(max(1.0, float(settings.evolink_poll_interval or 5.0)))
        raise TimeoutError(f"evolink_task_timeout:{task_id}:{str(latest)[:500]}")

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
        stage = self._image_edit_round_stage(round_number)
        include_previous = bool(current_result_refs) and self._should_include_previous_edit_result(qa_reasons)
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
        edit_prompt = self._compact_prompt_for_evolink(edit_prompt, is_couple=is_couple)
        user_text_mode = "USER TEXT PRIORITY" in str(prompt_text or "") or "USER DIRECTION:" in str(prompt_text or "")
        reference_entries = self._evolink_reference_entries(
            identity_refs=identity_refs,
            style_refs=style_refs,
            current_result_refs=current_result_refs,
            identity_reference_pack=identity_reference_pack,
            include_previous_result=include_previous,
            is_couple=is_couple,
            user_text_mode=user_text_mode,
        )
        if self._identity_edit_required(user_images) and not reference_entries:
            raise RuntimeError("evolink_identity_refs_missing")

        payload = {
            "model": model,
            "prompt": edit_prompt,
            "image_urls": [url for _label, url in reference_entries],
            "size": self._evolink_size(),
            "quality": self._evolink_quality(),
            "model_params": {"web_search": False},
        }
        await self._update_order_generating(
            order_uuid,
            task_id=f"evolink-image-edit-{order_uuid}-round-{round_number}",
            payload={
                **payload,
                "_provider_model": model,
                "_requested_model": configured_model or model,
                "_reference_labels": [label for label, _url in reference_entries],
                "_round": int(round_number),
                "_stage": stage,
                "_billable": False,
                "_extra_credits_charged": 0,
            },
            prompt_text=edit_prompt,
            negative_prompt=negative_prompt,
            generation_mode="evolink_image_edit_multi_round",
        )

        timeout = httpx.Timeout(connect=10.0, read=45.0, write=45.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
            response = await client.post(self._image_generation_url(), json=payload, headers=self._headers())
            if response.status_code in {401, 403}:
                raise RuntimeError(f"evolink_auth_failed:{response.status_code}")
            if response.status_code in {402, 429}:
                raise RuntimeError(f"evolink_quota_rejected:{response.status_code}:{response.text[:200]}")
            response_body = response.text[:500]
            if response.status_code in {400, 404, 422, 500, 503}:
                lowered = response.text.lower()
                if "model" in lowered and any(token in lowered for token in ("not", "missing", "available", "support")):
                    raise RuntimeError(f"evolink_model_unavailable:{model}:{response.status_code}:{response.text[:240]}")
            if response.status_code in {400, 422}:
                raise RuntimeError(f"evolink_request_rejected:{response.status_code}:{response_body}")
            response.raise_for_status()
            created = response.json() if response.content else {}
            task_id = self._extract_task_id(created)
            if not task_id:
                raise RuntimeError(f"evolink_missing_task_id:{str(created)[:500]}")
            final = await self._poll_task(client, task_id)

        provider_urls = self._extract_output_urls(final)
        if not provider_urls:
            raise RuntimeError(f"evolink_outputs_missing:{task_id}:{str(final)[:500]}")
        delivered_urls = await self._persist_outputs_to_storage(provider_urls, order_uuid)
        if not delivered_urls:
            raise RuntimeError("evolink_storage_outputs_missing")

        return await self._finalize_image_edit_round_candidates(
            order_uuid,
            round_number=round_number,
            stage=stage,
            delivered_urls=delivered_urls,
            provider_urls=provider_urls,
            provider_model=model,
            configured_model=configured_model or model,
            fallback_used=model != (configured_model or model),
            provider_endpoint=self._image_generation_url(),
            native_image_edit=False,
            user_images=user_images,
            is_couple=is_couple,
            include_previous=include_previous,
        )

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
            if not order or order.status == OrderStatus.COMPLETED:
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
                    "evolink_submit_payload_keys": sorted(payload.keys()),
                    "evolink_submit_reference_count": len(payload.get("image_urls") or []),
                    "evolink_submit_prompt_chars": len(str(payload.get("prompt") or "")),
                    "evolink_submit_size": str(payload.get("size") or ""),
                    "evolink_submit_quality": str(payload.get("quality") or ""),
                    "evolink_model": self._effective_image_model(),
                    "evolink_configured_model": settings.evolink_image_model,
                    "evolink_requested_image_edit_model": requested_model,
                    "evolink_actual_image_edit_model": payload_model,
                    "evolink_image_edit_fallback_used": fallback_used,
                    "evolink_submitted_at": self._utc_now_iso(),
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
                params["evolink_task_id"] = provider_task_id
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
            debug["evolink_output_urls"] = provider_urls
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


evolink_service = EvolinkService()
