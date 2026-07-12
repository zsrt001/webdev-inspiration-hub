"""Public legal and commercial policy copy."""

from __future__ import annotations

from datetime import date

from app.services.retention_service import (
    FREE_ORDER_RETENTION_DAYS,
    PAID_ORDER_RETENTION_DAYS,
    SOURCE_IMAGE_RETENTION_DAYS,
    STUDIO_ORDER_RETENTION_DAYS,
    SUBSCRIPTION_ORDER_RETENTION_DAYS,
)
from app.core.config import get_settings


def get_legal_policies() -> dict:
    settings = get_settings()
    support_contact = settings.support_contact_url or settings.support_contact_email or settings.manual_payment_contact
    return {
        "last_updated": date(2026, 4, 26).isoformat(),
        "retention": {
            "source_images_days": SOURCE_IMAGE_RETENTION_DAYS,
            "free_generated_days": FREE_ORDER_RETENTION_DAYS,
            "paid_generated_days": PAID_ORDER_RETENTION_DAYS,
            "subscription_generated_days": SUBSCRIPTION_ORDER_RETENTION_DAYS,
            "studio_generated_days": STUDIO_ORDER_RETENTION_DAYS,
            "summary": (
                "The listed periods are the scheduled retention targets for source and generated assets. "
                "Automated deletion is temporarily paused until the auditable, retryable cleanup flow is verified."
            ),
        },
        "pricing": {
            "single": 2,
            "director": 2,
            "couple_local": 3,
            "couple_remote": 3,
            "golden_anniversary": 3,
            "premium_scene": 5,
            "live_portrait_5s": 6,
            "live_portrait_extra_5s": 4,
            "summary": "Credits are deducted only after a generation job is accepted into the queue and are refunded if queueing fails.",
        },
        "disclaimers": [
            "Generated AI images may not be perfectly accurate and should be reviewed before commercial use.",
            "Users must only upload portraits and materials they own or are authorized to use.",
            "The service does not store card numbers, CVV, bank data, or other raw payment credentials.",
            "Refunds and failure compensation are recorded as ledger adjustments rather than rewriting historical transactions.",
        ],
        "refunds": {
            "support_contact": support_contact,
            "refund_policy_url": settings.refund_policy_url or "/pages/legal/refund",
            "summary": (
                "Refunds are reviewed through support. Duplicate payments, unrecoverable provider failures, and confirmed "
                "platform-side delivery failures can be compensated by refund or credit adjustment after ledger review."
            ),
        },
        "embed_points": [
            "Google sign-in and account creation",
            "Image upload and generation consent",
            "Payment and subscription checkout consent",
            "Account image history and retention notice",
            "Footer Privacy Policy and Terms of Service links",
            "Footer Refund and Support link",
        ],
    }
