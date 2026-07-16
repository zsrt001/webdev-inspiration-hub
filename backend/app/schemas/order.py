"""Order Pydantic schemas."""

import math
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.order import OrderStatus


class OrderCreate(BaseModel):
    """Schema for creating an order."""

    model_config = ConfigDict(extra="forbid")

    template_id: str
    asset_ids: list[UUID] = Field(min_length=1, max_length=2)
    legal_accepted: bool = False
    director_mode: bool | None = None
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


class AcceptedOrder(BaseModel):
    """Minimal 202 response; Provider and correlation facts stay private."""

    model_config = ConfigDict(extra="forbid", strict=True)

    order_id: UUID
    status: str = Field(pattern="^QUEUED$")
    status_url: str


class OrderAssetRead(BaseModel):
    """Opaque private-asset identity with an authenticated streaming path."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: UUID
    role: str = Field(pattern="^(preview_watermarked|final_master|delivery_variant)$")
    status: str = Field(pattern="^ACTIVE$")
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    download_path: str = Field(pattern=r"^/api/v1/orders/[0-9a-f-]+/assets/[0-9a-f-]+/download$")


class OrderRead(BaseModel):
    """Customer order projection with no URL, object-key, or Provider surface."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    user_id: UUID
    status: OrderStatus
    template_id: str | None = None
    assets: list[OrderAssetRead] = Field(default_factory=list)
    can_download: bool = False
    entitlement_status: str | None = None
    access_tier: str | None = None
    settlement_status: Literal["NOT_CHARGED", "CAPTURED", "REFUNDED", "RECONCILING"]
    delivery_status: str
    source_images_expires_at: datetime | None = None
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    storage_cleanup_status: str | None = None
    price_cents: int
    paid_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("settlement_status", mode="before")
    @classmethod
    def _project_public_settlement(cls, value: object) -> str:
        internal = str(value or "UNSETTLED").strip().upper()
        projected = {
            "UNSETTLED": "NOT_CHARGED",
            "RESERVED": "NOT_CHARGED",
            "RELEASED": "NOT_CHARGED",
            "NOT_CHARGED": "NOT_CHARGED",
            "CAPTURED": "CAPTURED",
            "REFUNDED": "REFUNDED",
            "RECONCILING": "RECONCILING",
        }.get(internal)
        if projected is None:
            raise ValueError("unsupported order settlement status")
        return projected

    @field_validator("delivery_status", mode="before")
    @classmethod
    def _default_unpersisted_delivery(cls, value: object) -> str:
        return str(value or "PENDING")


class OrderFundingAllocationRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    amount: int = Field(gt=0)
    root_transaction_id: UUID
    root_kind: str = Field(
        pattern="^(WELCOME|PURCHASE|SUBSCRIPTION|ADMIN|REFUND|LEGACY_POOL)$"
    )


class OrderFundingRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    reservation_id: UUID
    reservation_status: str = Field(pattern="^(RESERVED|CAPTURED|RELEASED|EXPIRED)$")
    amount: int = Field(gt=0)
    allocations: tuple[OrderFundingAllocationRead, ...]
    entitlement_status: str | None = Field(default=None, pattern="^(ACTIVE|REVOKED)$")
    unlock_root_transaction_id: UUID | None = None
    unlock_root_kind: str | None = Field(default=None, pattern="^(PURCHASE|SUBSCRIPTION)$")


class TrialUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    root_transaction_id: UUID


class TrialUnlockRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    entitlement_id: UUID
    order_id: UUID
    status: str = Field(pattern="^ACTIVE$")
    access_tier: str = Field(pattern="^trial_unlocked$")
    expires_at: datetime
