"""Order Pydantic schemas."""

import math
from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, model_validator

from app.models.order import OrderStatus


PUBLIC_GENERATION_PARAM_KEYS = {
    "credits_cost",
    "refunded_credits",
    "access_tier",
    "download_locked",
    "remote_join",
    "couple_flow",
    "subject_count",
    "director_mode",
    "effective_scene_source",
    "effective_outfit_source",
    "ignored_inputs",
    "effective_scene_preset_id",
    "effective_outfit_preset_id",
    "effective_scene_preset_title",
    "effective_outfit_preset_title",
    "effective_scene_ip_weight",
    "effective_outfit_ip_weight",
    "director_decision_hints",
    "qa_last_reasons",
    "qa_attempt_count",
    "failure_code",
    "failure_provider",
    "commercial_standard_version",
    "generation_stage",
    "generation_stage_history",
    "upload_quality_summary",
}


def public_source_image_urls(value: Any) -> dict[str, Any] | None:
    """Return only user-owned upload URLs; hide identity crops and debug packs."""
    if not isinstance(value, dict):
        return None
    images = value.get("images")
    if not isinstance(images, list):
        return None
    public_images = [str(image) for image in images if str(image or "").strip()]
    return {"images": public_images}


def _public_credit_refund(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    public: dict[str, Any] = {}
    for key in ("amount", "applied", "failure_code"):
        if key in value:
            public[key] = value[key]
    return public or None


def public_generation_params(value: Any) -> dict[str, Any] | None:
    """Whitelist order metadata that is safe for the customer-facing API."""
    if not isinstance(value, dict):
        return None
    public = {key: value[key] for key in PUBLIC_GENERATION_PARAM_KEYS if key in value}
    credit_refund = _public_credit_refund(value.get("credit_refund"))
    if credit_refund:
        public["credit_refund"] = credit_refund
    return public


class OrderBase(BaseModel):
    """Base order schema."""

    style_template: str | None = None
    generation_params: dict | None = None


class OrderCreate(BaseModel):
    """Schema for creating an order."""

    template_id: str
    user_images: list[str]
    legal_accepted: bool = False
    director_mode: bool | None = None
    remote_join: bool | None = None
    global_style_text: str | None = None
    scene_text: str | None = None
    outfit_text: str | None = None
    scene_preset_id: str | None = None
    clothing_preset_id: str | None = None
    prompt_override: str | None = None
    scene_image_url: str | None = None
    clothing_image_url: str | None = None
    pose_image_url: str | None = None
    depth_image_url: str | None = None
    normal_image_url: str | None = None
    scene_ip_weight: float | None = None
    clothing_ip_weight: float | None = None
    face_ip_weight: float | None = None
    pose_cn_weight: float | None = None
    depth_cn_weight: float | None = None
    normal_cn_weight: float | None = None
    pose_cn_start: float | None = None
    pose_cn_end: float | None = None
    depth_cn_start: float | None = None
    depth_cn_end: float | None = None
    normal_cn_start: float | None = None
    normal_cn_end: float | None = None
    upload_quality: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def _normalize_director_inputs(self):
        def _clean_text(value: str | None) -> str | None:
            if value is None:
                return None
            cleaned = str(value).strip()
            return cleaned or None

        def _clean_scalar(value: str | None) -> str | None:
            if value is None:
                return None
            cleaned = str(value).strip()
            return cleaned or None

        def _clamp_unit(value: float | None) -> float | None:
            if value is None:
                return None
            try:
                numeric = float(value)
            except Exception:
                return None
            return max(0.0, min(1.0, numeric))

        def _normalize_range(start: float | None, end: float | None) -> tuple[float | None, float | None]:
            start_value = _clamp_unit(start)
            end_value = _clamp_unit(end)
            if start_value is not None and end_value is not None and start_value > end_value:
                start_value, end_value = end_value, start_value
            return start_value, end_value

        def _clean_string_list(value: Any, limit: int) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(item).strip()[:80] for item in value if str(item).strip()][:limit]

        def _safe_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except Exception:
                return default

        def _normalize_upload_quality(items: Any) -> list[dict[str, Any]] | None:
            if not isinstance(items, list):
                return None
            normalized: list[dict[str, Any]] = []
            for item in items[:4]:
                if not isinstance(item, dict):
                    continue
                try:
                    score = int(round(float(item.get("quality_score", 0))))
                except Exception:
                    score = 0
                score = max(0, min(100, score))
                level = str(item.get("quality_level") or "good").strip().lower()
                if level not in {"good", "warning", "poor"}:
                    level = "good" if score >= 70 else "warning" if score >= 45 else "poor"
                metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
                clean_metrics: dict[str, float] = {}
                for key, value in list(metrics.items())[:20]:
                    try:
                        numeric = float(value)
                    except Exception:
                        continue
                    if math.isfinite(numeric):
                        clean_metrics[str(key).strip()[:48]] = round(numeric, 4)
                normalized.append(
                    {
                        "slot_index": _safe_int(item.get("slot_index"), len(normalized)),
                        "role": str(item.get("role") or "").strip()[:24] or None,
                        "image_url": _clean_scalar(item.get("image_url")),
                        "quality_score": score,
                        "quality_level": level,
                        "reasons": _clean_string_list(item.get("reasons"), 12),
                        "risk_flags": _clean_string_list(item.get("risk_flags"), 12),
                        "metrics": clean_metrics,
                    }
                )
            return normalized or None

        self.template_id = str(self.template_id).strip()
        self.user_images = [str(item).strip() for item in (self.user_images or []) if str(item).strip()]

        self.global_style_text = _clean_text(self.global_style_text)
        self.scene_text = _clean_text(self.scene_text)
        self.outfit_text = _clean_text(self.outfit_text)
        self.prompt_override = _clean_text(self.prompt_override)
        self.scene_preset_id = _clean_scalar(self.scene_preset_id)
        self.clothing_preset_id = _clean_scalar(self.clothing_preset_id)
        self.scene_image_url = _clean_scalar(self.scene_image_url)
        self.clothing_image_url = _clean_scalar(self.clothing_image_url)
        self.pose_image_url = _clean_scalar(self.pose_image_url)
        self.depth_image_url = _clean_scalar(self.depth_image_url)
        self.normal_image_url = _clean_scalar(self.normal_image_url)

        self.scene_ip_weight = _clamp_unit(self.scene_ip_weight)
        self.clothing_ip_weight = _clamp_unit(self.clothing_ip_weight)
        self.face_ip_weight = _clamp_unit(self.face_ip_weight)
        self.pose_cn_weight = _clamp_unit(self.pose_cn_weight)
        self.depth_cn_weight = _clamp_unit(self.depth_cn_weight)
        self.normal_cn_weight = _clamp_unit(self.normal_cn_weight)

        self.pose_cn_start, self.pose_cn_end = _normalize_range(self.pose_cn_start, self.pose_cn_end)
        self.depth_cn_start, self.depth_cn_end = _normalize_range(self.depth_cn_start, self.depth_cn_end)
        self.normal_cn_start, self.normal_cn_end = _normalize_range(self.normal_cn_start, self.normal_cn_end)
        self.upload_quality = _normalize_upload_quality(self.upload_quality)
        return self


class OrderUpdate(BaseModel):
    """Schema for updating an order."""

    status: OrderStatus | None = None
    style_template: str | None = None
    generation_params: dict | None = None
    preview_image_urls: dict | None = None
    final_image_urls: dict | None = None
    error_message: str | None = None


class OrderRead(OrderBase):
    """Schema for reading order data."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    status: OrderStatus
    template_id: str | None = None
    source_image_urls: dict | None = None
    preview_image_urls: dict | None = None
    final_image_urls: dict | None = None
    can_download: bool = False
    access_tier: str | None = None
    download_locked: bool = True
    source_images_expires_at: datetime | None = None
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    storage_cleanup_status: str | None = None
    price_cents: int
    paid_at: datetime | None = None
    error_message: str | None = None
    director_mode: bool | None = None
    remote_join: bool | None = None
    subject_count: int | None = None
    couple_flow: str | None = None
    effective_scene_source: str | None = None
    effective_outfit_source: str | None = None
    effective_scene_preset_id: str | None = None
    effective_outfit_preset_id: str | None = None
    effective_scene_preset_title: str | None = None
    effective_outfit_preset_title: str | None = None
    effective_scene_ip_weight: float | None = None
    effective_outfit_ip_weight: float | None = None
    ignored_inputs: list[str] | None = None
    director_summary: dict | None = None
    director_decision_hints: list[str] | None = None
    couple_guardrails: dict | None = None
    qa_last_reasons: list[str] | None = None
    qa_attempt_count: int | None = None
    credits_cost: int | None = None
    refunded_credits: int | None = None
    failure_code: str | None = None
    failure_provider: str | None = None
    generation_stage: str | None = None
    generation_stage_history: list[dict] | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _hydrate_effective_sources(self):
        params = self.generation_params or {}
        status_value = self.status.value if isinstance(self.status, OrderStatus) else str(self.status or "")
        is_completed = status_value == OrderStatus.COMPLETED.value
        if isinstance(params, dict):
            self.director_mode = bool(params.get("director_mode"))
            self.remote_join = bool(params.get("remote_join"))
            subject_count = params.get("subject_count")
            if subject_count is not None:
                try:
                    self.subject_count = int(subject_count)
                except Exception:
                    self.subject_count = None
            couple_flow = params.get("couple_flow")
            self.couple_flow = str(couple_flow) if couple_flow else None
            self.effective_scene_source = params.get("effective_scene_source")
            self.effective_outfit_source = params.get("effective_outfit_source")
            self.effective_scene_preset_id = params.get("effective_scene_preset_id")
            self.effective_outfit_preset_id = params.get("effective_outfit_preset_id")
            self.effective_scene_preset_title = params.get("effective_scene_preset_title")
            self.effective_outfit_preset_title = params.get("effective_outfit_preset_title")
            try:
                self.effective_scene_ip_weight = float(params.get("effective_scene_ip_weight")) if params.get("effective_scene_ip_weight") is not None else None
            except Exception:
                self.effective_scene_ip_weight = None
            try:
                self.effective_outfit_ip_weight = float(params.get("effective_outfit_ip_weight")) if params.get("effective_outfit_ip_weight") is not None else None
            except Exception:
                self.effective_outfit_ip_weight = None
            ignored_inputs = params.get("ignored_inputs")
            if isinstance(ignored_inputs, list):
                self.ignored_inputs = [str(item) for item in ignored_inputs if str(item).strip()]
            director_summary = params.get("director_summary")
            if isinstance(director_summary, dict):
                self.director_summary = director_summary
            director_decision_hints = params.get("director_decision_hints")
            if isinstance(director_decision_hints, list):
                self.director_decision_hints = [str(item) for item in director_decision_hints if str(item).strip()]
            couple_guardrails = params.get("couple_guardrails")
            if isinstance(couple_guardrails, dict):
                self.couple_guardrails = couple_guardrails
            qa_last_reasons = params.get("qa_last_reasons")
            if is_completed:
                self.qa_last_reasons = []
            elif isinstance(qa_last_reasons, list):
                self.qa_last_reasons = [str(item) for item in qa_last_reasons if str(item).strip()]
            qa_attempt_count = params.get("qa_attempt_count")
            if is_completed:
                self.qa_attempt_count = None
            elif qa_attempt_count is not None:
                try:
                    self.qa_attempt_count = int(qa_attempt_count)
                except Exception:
                    self.qa_attempt_count = None
            credits_cost = params.get("credits_cost")
            if credits_cost is not None:
                try:
                    self.credits_cost = int(credits_cost)
                except Exception:
                    self.credits_cost = None
            refunded_credits = params.get("refunded_credits")
            if refunded_credits is not None:
                try:
                    self.refunded_credits = int(refunded_credits)
                except Exception:
                    self.refunded_credits = None
            failure_code = params.get("failure_code")
            self.failure_code = str(failure_code) if failure_code else None
            failure_provider = params.get("failure_provider")
            self.failure_provider = str(failure_provider) if failure_provider else None
            generation_stage = params.get("generation_stage")
            self.generation_stage = str(generation_stage) if generation_stage else None
            generation_stage_history = params.get("generation_stage_history")
            if isinstance(generation_stage_history, list):
                self.generation_stage_history = [
                    item for item in generation_stage_history if isinstance(item, dict)
                ][-12:]
            access_tier = params.get("access_tier")
            self.access_tier = str(access_tier) if access_tier else None
        self.source_image_urls = public_source_image_urls(self.source_image_urls)
        public_params = public_generation_params(params)
        if is_completed and isinstance(public_params, dict):
            public_params.pop("qa_last_reasons", None)
            public_params.pop("qa_attempt_count", None)
        self.generation_params = public_params
        return self
