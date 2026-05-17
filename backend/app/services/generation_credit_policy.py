"""Credit policy helpers for generation orders."""

from __future__ import annotations

from typing import Any


CREDIT_POLICY_VERSION = 1
REFUNDABLE_GENERATION_FAILURE_CODES = {
    "delivery_error",
    "generation_timeout",
    "provider_auth_failed",
    "provider_model_unavailable",
    "provider_request_rejected",
    "provider_quota_exhausted",
    "qa_reject",
    "unknown_error",
}


def _safe_non_negative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return max(0, int(default or 0))


def build_generation_credit_policy(*, credits_cost: int) -> dict[str, Any]:
    charged_credits = _safe_non_negative_int(credits_cost)
    return {
        "version": CREDIT_POLICY_VERSION,
        "charge_once_per_order": True,
        "charged_credits": charged_credits,
        "automatic_repair_rounds_included": True,
        "automatic_repair_extra_charge": 0,
        "no_extra_debit_for_image_edit_rounds": True,
        "refund_on_failed_generation": True,
        "refund_on_blocking_qa_failure": True,
        "refund_on_identity_failure": True,
        "refund_on_provider_failure": True,
        "refund_idempotent_by_order": True,
        "precharge_failures_not_billed": [
            "content_policy_reject",
            "duplicate_subject_images",
            "gatekeeper_reject",
            "identity_reference_pack_failed",
            "insufficient_credits",
            "trial_daily_limit_reached",
            "trial_mode_requires_top_up",
        ],
        "refund_failure_codes": sorted(REFUNDABLE_GENERATION_FAILURE_CODES),
    }


def _policy_from_params(params: dict[str, Any]) -> dict[str, Any]:
    policy = params.get("credit_policy")
    return policy if isinstance(policy, dict) else {}


def refundable_generation_failure(failure_code: str | None, params: dict[str, Any] | None = None) -> bool:
    params = params if isinstance(params, dict) else {}
    policy = _policy_from_params(params)
    if policy.get("refund_on_failed_generation") is False:
        return False
    code = str(failure_code or "unknown_error").strip() or "unknown_error"
    configured_codes = policy.get("refund_failure_codes")
    if isinstance(configured_codes, list) and configured_codes:
        return code in {str(item) for item in configured_codes}
    return code in REFUNDABLE_GENERATION_FAILURE_CODES


def resolve_generation_refund_amount(
    params: dict[str, Any] | None,
    *,
    fallback_amount: int,
    failure_code: str | None,
) -> int:
    params = params if isinstance(params, dict) else {}
    if _safe_non_negative_int(params.get("refunded_credits")) > 0:
        return 0
    if not refundable_generation_failure(failure_code, params):
        return 0
    if "credits_cost" in params:
        return _safe_non_negative_int(params.get("credits_cost"))
    policy = _policy_from_params(params)
    if "charged_credits" in policy:
        return _safe_non_negative_int(policy.get("charged_credits"))
    return _safe_non_negative_int(fallback_amount)


def generation_refund_metadata(
    params: dict[str, Any] | None,
    *,
    failure_code: str,
    failure_provider: str,
    error_message: str,
) -> dict[str, Any]:
    params = params if isinstance(params, dict) else {}
    policy = _policy_from_params(params)
    metadata: dict[str, Any] = {
        "failure_code": str(failure_code or "unknown_error"),
        "failure_provider": str(failure_provider or "unknown"),
        "credit_policy_version": policy.get("version", CREDIT_POLICY_VERSION),
        "automatic_repair_extra_charge": _safe_non_negative_int(policy.get("automatic_repair_extra_charge")),
        "image_edit_round_current": params.get("image_edit_round_current"),
        "image_edit_selected_round": params.get("image_edit_selected_round"),
        "qa_attempt_count": params.get("qa_attempt_count"),
        "error_message": str(error_message or "")[:500],
    }
    if isinstance(params.get("qa_last_reasons"), list):
        metadata["qa_last_reasons"] = [str(reason) for reason in params["qa_last_reasons"]]
    if isinstance(params.get("qa_last_issues"), list):
        metadata["qa_last_issues"] = [issue for issue in params["qa_last_issues"] if isinstance(issue, dict)]
    return metadata


def merge_generation_refund_state(
    params: dict[str, Any] | None,
    *,
    refund_amount: int,
    refund_applied: bool,
    refund_already_recorded: bool = False,
    failure_code: str,
    failure_provider: str,
) -> dict[str, Any]:
    next_params = dict(params or {})
    next_params["failure_code"] = failure_code
    next_params["failure_provider"] = failure_provider
    next_params["automatic_repair_extra_charge"] = 0
    next_params["credit_refund"] = {
        "eligible": refund_amount > 0,
        "applied": bool(refund_applied),
        "already_recorded": bool(refund_already_recorded),
        "amount": int(refund_amount or 0),
        "failure_code": failure_code,
        "provider": failure_provider,
        "idempotency_scope": "order",
    }
    if (refund_applied or refund_already_recorded) and refund_amount > 0:
        next_params["refunded_credits"] = int(refund_amount)
    return next_params


def billable_generation_credits(params: dict[str, Any] | None) -> int:
    params = params if isinstance(params, dict) else {}
    credits_cost = _safe_non_negative_int(params.get("credits_cost"))
    refunded_credits = _safe_non_negative_int(params.get("refunded_credits"))
    return max(0, credits_cost - refunded_credits)
