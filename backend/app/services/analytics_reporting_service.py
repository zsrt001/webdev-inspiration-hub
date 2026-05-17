from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.click_stat import ClickStat
from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus
from app.models.lead import Lead
from app.models.live_portrait_job import LivePortraitJob, LivePortraitStatus
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.template_service import get_template_by_id

IDENTITY_GRADES = ("identity_pass", "minor_drift", "major_mismatch", "role_swap")
IDENTITY_GRADE_RANK = {grade: index for index, grade in enumerate(IDENTITY_GRADES)}
QUALITY_REASON_GROUPS = {
    "identity": {
        "identity_mismatch",
        "identity_swap",
        "identity_similarity_low",
        "identity_margin_low",
        "identity_averaging",
        "identity_face_missing",
        "identity_embedding_unavailable",
        "face_distortion",
        "fused_faces",
        "subject_missing",
        "headless",
    },
    "lighting": {
        "poor_studio_quality",
        "face_underexposed",
        "flat_lighting",
        "no_catchlights",
        "oily_skin_highlight",
        "dress_highlights_blown",
        "mixed_color_temperature",
        "poor_subject_separation",
        "background_brighter_than_face",
        "harsh_backlight",
    },
    "composition": {
        "subject_too_small",
        "face_too_small",
        "background_dominates",
        "excessive_headroom",
        "awkward_crop",
        "dress_cropped",
        "flat_centered_pose",
        "weak_couple_interaction",
    },
    "anatomy": {"bad_hands", "extra_limbs", "body_fusion"},
    "wardrobe": {"dress_exposure_error"},
    "technical": {"black_or_blank", "watermark_or_text", "severe_artifacts", "low_resolution", "too_blurry"},
    "safety": {"nsfw"},
    "ops": {"vision_error", "identity_embedding_unavailable"},
}


def _parse_lead_notes(notes: str | None) -> dict[str, str]:
    meta: dict[str, str] = {}
    if not notes:
        return meta
    for part in notes.split(" | "):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            meta[key] = value
    return meta


def _daterange(days: int) -> list[date]:
    days = max(1, min(90, int(days)))
    end_day = date.today()
    start_day = end_day - timedelta(days=days - 1)
    return [start_day + timedelta(days=index) for index in range(days)]


def _identity_grade_from_value(value: Any) -> str | None:
    grade = str(value or "").strip()
    return grade if grade in IDENTITY_GRADE_RANK else None


def _worse_identity_grade(current: str | None, candidate: Any) -> str | None:
    grade = _identity_grade_from_value(candidate)
    if not grade:
        return current
    if not current or IDENTITY_GRADE_RANK[grade] > IDENTITY_GRADE_RANK[current]:
        return grade
    return current


def _identity_grade_from_params(params: dict[str, Any]) -> str | None:
    worst = _worse_identity_grade(None, params.get("identity_grade"))
    debug = (params.get("debug") or {}) if isinstance(params.get("debug"), dict) else {}
    rounds = debug.get("image_edit_rounds")
    if isinstance(rounds, list):
        for item in rounds:
            if not isinstance(item, dict):
                continue
            worst = _worse_identity_grade(worst, item.get("identity_grade"))
            scores = item.get("candidate_scores")
            if isinstance(scores, list):
                for score in scores:
                    if isinstance(score, dict):
                        worst = _worse_identity_grade(worst, score.get("identity_grade"))
    return worst


def _quality_reason_group(reason: str) -> str:
    value = str(reason or "").strip()
    for group, reasons in QUALITY_REASON_GROUPS.items():
        if value in reasons:
            return group
    return "other"


def _clean_reason_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    reasons: list[str] = []
    seen: set[str] = set()
    for item in value:
        reason = str(item or "").strip()
        if reason and reason not in seen:
            seen.add(reason)
            reasons.append(reason)
    return reasons


def _rounds_from_params(params: dict[str, Any]) -> list[dict[str, Any]]:
    debug = (params.get("debug") or {}) if isinstance(params.get("debug"), dict) else {}
    rounds = debug.get("image_edit_rounds")
    if not isinstance(rounds, list):
        return []
    return [item for item in rounds if isinstance(item, dict)]


def _round_reasons(round_item: dict[str, Any]) -> list[str]:
    reasons = _clean_reason_list(round_item.get("qa_reasons"))
    for score in round_item.get("candidate_scores") if isinstance(round_item.get("candidate_scores"), list) else []:
        if not isinstance(score, dict):
            continue
        reasons.extend(_clean_reason_list(score.get("reasons")))
        reasons.extend(_clean_reason_list(score.get("hard_gate_reasons")))
    seen: set[str] = set()
    ordered: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            ordered.append(reason)
    return ordered


def _all_order_quality_reasons(params: dict[str, Any]) -> list[str]:
    reasons = _clean_reason_list(params.get("qa_last_reasons"))
    issues = params.get("qa_last_issues") if isinstance(params.get("qa_last_issues"), list) else []
    for issue in issues:
        if isinstance(issue, dict):
            reasons.append(str(issue.get("code") or "").strip())
    for round_item in _rounds_from_params(params):
        reasons.extend(_round_reasons(round_item))
    seen: set[str] = set()
    ordered: list[str] = []
    for reason in reasons:
        if reason and reason not in seen:
            seen.add(reason)
            ordered.append(reason)
    return ordered


def _round_number(round_item: dict[str, Any], fallback: int) -> int:
    try:
        return max(1, int(round_item.get("round") or fallback))
    except Exception:
        return max(1, int(fallback))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _inc_reason_bucket(
    buckets: dict[str, dict[str, Any]],
    reason: str,
    *,
    template_id: str | None = None,
    round_number: int | None = None,
) -> None:
    clean = str(reason or "").strip()
    if not clean:
        return
    bucket = buckets.setdefault(
        clean,
        {
            "reason": clean,
            "group": _quality_reason_group(clean),
            "count": 0,
            "templates": {},
            "rounds": {},
        },
    )
    bucket["count"] += 1
    if template_id:
        template_counts = bucket["templates"]
        template_counts[template_id] = int(template_counts.get(template_id, 0)) + 1
    if round_number is not None:
        round_key = str(int(round_number))
        round_counts = bucket["rounds"]
        round_counts[round_key] = int(round_counts.get(round_key, 0)) + 1


def _quality_template_bucket(template_id: str) -> dict[str, Any]:
    template = get_template_by_id(template_id)
    return {
        "template_id": template_id,
        "template_title": getattr(template, "marketing_title", None)
        or getattr(template, "title", None)
        or template_id,
        "style_family": getattr(template, "style_family", None) or template_id,
        "orders": 0,
        "completed_orders": 0,
        "failed_orders": 0,
        "qa_failed_orders": 0,
        "identity_failed_orders": 0,
        "lighting_failed_orders": 0,
        "composition_failed_orders": 0,
        "repair_round_sum": 0,
        "repair_round_count": 0,
        "reason_counts": {},
        "round_attempts": {},
        "round_successes": {},
        "relight_attempts": 0,
        "relight_successes": 0,
    }


def _build_quality_dashboard_from_orders(order_rows: list[tuple[Any, Any, Any, Any]]) -> dict[str, Any]:
    templates: dict[str, dict[str, Any]] = {}
    reasons: dict[str, dict[str, Any]] = {}
    rounds: dict[str, dict[str, Any]] = {}
    repair_modes: dict[str, dict[str, Any]] = {}
    totals = {
        "orders": 0,
        "completed_orders": 0,
        "failed_orders": 0,
        "qa_failed_orders": 0,
        "identity_failed_orders": 0,
        "lighting_failed_orders": 0,
        "composition_failed_orders": 0,
        "repair_round_sum": 0,
        "repair_round_count": 0,
        "relight_attempts": 0,
        "relight_successes": 0,
    }

    for template_id_value, status, params, error_message in order_rows:
        template_id = str(template_id_value or "unknown").strip() or "unknown"
        params = params if isinstance(params, dict) else {}
        bucket = templates.setdefault(template_id, _quality_template_bucket(template_id))
        totals["orders"] += 1
        bucket["orders"] += 1
        is_completed = str(status) == OrderStatus.COMPLETED.value
        if is_completed:
            totals["completed_orders"] += 1
            bucket["completed_orders"] += 1
        else:
            totals["failed_orders"] += 1
            bucket["failed_orders"] += 1

        order_reasons = _all_order_quality_reasons(params)
        failure_code = str(params.get("failure_code") or "")
        if order_reasons or failure_code == "qa_reject":
            totals["qa_failed_orders"] += 1
            bucket["qa_failed_orders"] += 1

        reason_groups = {_quality_reason_group(reason) for reason in order_reasons}
        if "identity" in reason_groups:
            totals["identity_failed_orders"] += 1
            bucket["identity_failed_orders"] += 1
        if "lighting" in reason_groups:
            totals["lighting_failed_orders"] += 1
            bucket["lighting_failed_orders"] += 1
        if "composition" in reason_groups:
            totals["composition_failed_orders"] += 1
            bucket["composition_failed_orders"] += 1
        for reason in order_reasons:
            reason_counts = bucket["reason_counts"]
            reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1
            _inc_reason_bucket(reasons, reason, template_id=template_id)

        round_items = _rounds_from_params(params)
        if round_items:
            repair_rounds = max(0, len(round_items) - 1)
            totals["repair_round_sum"] += repair_rounds
            totals["repair_round_count"] += 1
            bucket["repair_round_sum"] += repair_rounds
            bucket["repair_round_count"] += 1

        for index, round_item in enumerate(round_items, start=1):
            round_no = _round_number(round_item, index)
            round_key = str(round_no)
            round_bucket = rounds.setdefault(
                round_key,
                {
                    "round": round_no,
                    "attempts": 0,
                    "successes": 0,
                    "failures": 0,
                    "avg_selected_score_sum": 0.0,
                    "avg_selected_score_count": 0,
                    "reason_counts": {},
                    "repair_modes": {},
                },
            )
            round_bucket["attempts"] += 1
            bucket["round_attempts"][round_key] = int(bucket["round_attempts"].get(round_key, 0)) + 1
            qa_passed = bool(round_item.get("qa_passed"))
            if qa_passed:
                round_bucket["successes"] += 1
                bucket["round_successes"][round_key] = int(bucket["round_successes"].get(round_key, 0)) + 1
            else:
                round_bucket["failures"] += 1

            repair_mode = str(round_item.get("repair_mode") or round_item.get("stage") or "unknown").strip() or "unknown"
            round_bucket["repair_modes"][repair_mode] = int(round_bucket["repair_modes"].get(repair_mode, 0)) + 1
            mode_bucket = repair_modes.setdefault(
                repair_mode,
                {"repair_mode": repair_mode, "attempts": 0, "successes": 0, "failures": 0},
            )
            mode_bucket["attempts"] += 1
            if qa_passed:
                mode_bucket["successes"] += 1
            else:
                mode_bucket["failures"] += 1
            if repair_mode == "relight_edit_only":
                totals["relight_attempts"] += 1
                bucket["relight_attempts"] += 1
                if qa_passed:
                    totals["relight_successes"] += 1
                    bucket["relight_successes"] += 1

            selected_index = int(round_item.get("selected_candidate_index") or 0)
            scores = round_item.get("candidate_scores") if isinstance(round_item.get("candidate_scores"), list) else []
            for score in scores:
                if not isinstance(score, dict) or int(score.get("index") or 0) != selected_index:
                    continue
                round_bucket["avg_selected_score_sum"] += _safe_float(score.get("score"))
                round_bucket["avg_selected_score_count"] += 1
                break

            for reason in _round_reasons(round_item):
                round_reason_counts = round_bucket["reason_counts"]
                round_reason_counts[reason] = int(round_reason_counts.get(reason, 0)) + 1
                _inc_reason_bucket(reasons, reason, template_id=template_id, round_number=round_no)

        if not round_items and error_message and failure_code:
            _inc_reason_bucket(reasons, failure_code, template_id=template_id)

    template_rows: list[dict[str, Any]] = []
    for item in templates.values():
        item["completion_rate"] = _rate(item["completed_orders"], item["orders"])
        item["qa_failure_rate"] = _rate(item["qa_failed_orders"], item["orders"])
        item["identity_failure_rate"] = _rate(item["identity_failed_orders"], item["orders"])
        item["lighting_failure_rate"] = _rate(item["lighting_failed_orders"], item["orders"])
        item["composition_failure_rate"] = _rate(item["composition_failed_orders"], item["orders"])
        item["avg_repair_rounds"] = _avg(item["repair_round_sum"], item["repair_round_count"])
        item["relight_success_rate"] = _rate(item["relight_successes"], item["relight_attempts"])
        item["top_reasons"] = _top_counts(item["reason_counts"], key_name="reason", limit=8)
        item["round_success_rates"] = {
            key: _rate(int(item["round_successes"].get(key, 0)), int(attempts))
            for key, attempts in item["round_attempts"].items()
        }
        template_rows.append(item)

    round_rows: list[dict[str, Any]] = []
    for item in rounds.values():
        item["success_rate"] = _rate(item["successes"], item["attempts"])
        item["avg_selected_score"] = _avg(item["avg_selected_score_sum"], item["avg_selected_score_count"])
        item["top_reasons"] = _top_counts(item["reason_counts"], key_name="reason", limit=8)
        round_rows.append(item)

    reason_rows: list[dict[str, Any]] = []
    for item in reasons.values():
        item["top_templates"] = _top_counts(item["templates"], key_name="template_id", limit=6)
        item["round_counts"] = dict(sorted(item["rounds"].items(), key=lambda pair: int(pair[0])))
        reason_rows.append(item)

    repair_mode_rows: list[dict[str, Any]] = []
    for item in repair_modes.values():
        item["success_rate"] = _rate(item["successes"], item["attempts"])
        repair_mode_rows.append(item)

    totals["completion_rate"] = _rate(totals["completed_orders"], totals["orders"])
    totals["qa_failure_rate"] = _rate(totals["qa_failed_orders"], totals["orders"])
    totals["identity_failure_rate"] = _rate(totals["identity_failed_orders"], totals["orders"])
    totals["lighting_failure_rate"] = _rate(totals["lighting_failed_orders"], totals["orders"])
    totals["composition_failure_rate"] = _rate(totals["composition_failed_orders"], totals["orders"])
    totals["avg_repair_rounds"] = _avg(totals["repair_round_sum"], totals["repair_round_count"])
    totals["relight_success_rate"] = _rate(totals["relight_successes"], totals["relight_attempts"])

    template_rows.sort(key=lambda item: (-item["qa_failed_orders"], -item["orders"], item["template_id"]))
    reason_rows.sort(key=lambda item: (-item["count"], item["reason"]))
    round_rows.sort(key=lambda item: item["round"])
    repair_mode_rows.sort(key=lambda item: (-item["attempts"], item["repair_mode"]))

    return {
        "totals": totals,
        "templates": template_rows,
        "failure_reasons": reason_rows,
        "repair_rounds": round_rows,
        "repair_modes": repair_mode_rows,
    }


async def get_funnel_report(db: AsyncSession, *, days: int = 7) -> dict[str, Any]:
    days_list = _daterange(days)
    start_day = days_list[0]
    start_dt = datetime.combine(start_day, datetime.min.time())

    daily: dict[str, dict[str, Any]] = {
        item.isoformat(): {
            "day": item.isoformat(),
            "registered": 0,
            "upload_started": 0,
            "upload_completed": 0,
            "upload_duration_ms_sum": 0,
            "upload_duration_count": 0,
            "upload_quality_scored": 0,
            "upload_quality_score_sum": 0,
            "upload_quality_score_count": 0,
            "order_upload_quality_score_sum": 0,
            "order_upload_quality_score_count": 0,
            "upload_quality_warning": 0,
            "upload_quality_poor": 0,
            "order_created": 0,
            "order_completed": 0,
            "result_viewed": 0,
            "download_locked_clicked": 0,
            "payments_completed": 0,
            "download_success": 0,
            "qa_failed_orders": 0,
            "identity_failed_orders": 0,
            "identity_grade_counts": {grade: 0 for grade in IDENTITY_GRADES},
            "repair_round_sum": 0,
            "repair_round_count": 0,
            "payment_revenue_usd": 0.0,
        }
        for item in days_list
    }

    click_rows = (
        await db.execute(
            select(
                ClickStat.day,
                ClickStat.event_type,
                func.sum(ClickStat.count),
                func.coalesce(func.sum(ClickStat.value_sum), 0),
                func.coalesce(func.sum(ClickStat.value_count), 0),
            )
            .where(ClickStat.day >= start_day)
            .group_by(ClickStat.day, ClickStat.event_type)
        )
    ).all()
    event_map = {
        "asset_upload_started": "upload_started",
        "asset_upload_completed": "upload_completed",
        "asset_upload_quality_scored": "upload_quality_scored",
        "asset_upload_quality_warning": "upload_quality_warning",
        "asset_upload_quality_poor": "upload_quality_poor",
        "generation_result_ready_viewed": "result_viewed",
        "download_locked_clicked": "download_locked_clicked",
        "download_success": "download_success",
    }
    for day_value, event_type, count, value_sum, value_count in click_rows:
        bucket = daily.get(day_value.isoformat())
        if not bucket:
            continue
        current = int(count or 0)
        mapped = event_map.get(str(event_type or ""))
        if mapped:
            bucket[mapped] += current
        if event_type == "asset_upload_completed":
            bucket["upload_duration_ms_sum"] += int(value_sum or 0)
            bucket["upload_duration_count"] += int(value_count or 0)
        if event_type == "asset_upload_quality_scored":
            bucket["upload_quality_score_sum"] += int(value_sum or 0)
            bucket["upload_quality_score_count"] += int(value_count or 0)

    user_rows = (
        await db.execute(
            select(func.date(User.created_at), func.count(User.id))
            .where(User.created_at >= start_dt)
            .group_by(func.date(User.created_at))
        )
    ).all()
    for day_value, count in user_rows:
        bucket = daily.get(day_value.isoformat())
        if bucket:
            bucket["registered"] += int(count or 0)

    order_rows = (
        await db.execute(
            select(func.date(Order.created_at), Order.status, func.count(Order.id))
            .where(Order.created_at >= start_dt)
            .group_by(func.date(Order.created_at), Order.status)
        )
    ).all()
    for day_value, status, count in order_rows:
        bucket = daily.get(day_value.isoformat())
        if not bucket:
            continue
        bucket["order_created"] += int(count or 0)
        if str(status) == OrderStatus.COMPLETED.value:
            bucket["order_completed"] += int(count or 0)

    order_params = (
        await db.execute(
            select(Order.created_at, Order.status, Order.generation_params)
            .where(Order.created_at >= start_dt)
        )
    ).all()
    identity_reasons = {"identity_mismatch", "identity_swap"}
    for created_at, status, params in order_params:
        bucket = daily.get(created_at.date().isoformat()) if created_at else None
        if not bucket or not isinstance(params, dict):
            continue
        qa_reasons = params.get("qa_last_reasons") if isinstance(params.get("qa_last_reasons"), list) else []
        failure_code = str(params.get("failure_code") or "")
        debug = (params.get("debug") or {}) if isinstance(params.get("debug"), dict) else {}
        rounds = debug.get("image_edit_rounds")
        round_qa_failed = any(
            isinstance(item, dict) and item.get("qa_passed") is False
            for item in rounds
        ) if isinstance(rounds, list) else False
        if qa_reasons or failure_code == "qa_reject" or round_qa_failed:
            bucket["qa_failed_orders"] += 1
        round_identity_reasons: set[str] = set()
        if isinstance(rounds, list):
            for item in rounds:
                if not isinstance(item, dict):
                    continue
                for key in ("qa_reasons", "hard_gate_reasons"):
                    values = item.get(key)
                    if isinstance(values, list):
                        round_identity_reasons.update(str(reason) for reason in values)
                scores = item.get("candidate_scores")
                if isinstance(scores, list):
                    for score in scores:
                        values = score.get("hard_gate_reasons") if isinstance(score, dict) else None
                        if isinstance(values, list):
                            round_identity_reasons.update(str(reason) for reason in values)
        if identity_reasons & ({str(reason) for reason in qa_reasons} | round_identity_reasons):
            bucket["identity_failed_orders"] += 1
        explicit_attempts = params.get("qa_attempt_count")
        if isinstance(rounds, list) and rounds:
            bucket["repair_round_sum"] += max(0, len(rounds) - 1)
            bucket["repair_round_count"] += 1
        elif isinstance(explicit_attempts, int) and explicit_attempts > 0:
            bucket["repair_round_sum"] += max(0, explicit_attempts - 1)
            bucket["repair_round_count"] += 1
        identity_grade = _identity_grade_from_params(params)
        if identity_grade:
            bucket["identity_grade_counts"][identity_grade] += 1
        quality_summary = params.get("upload_quality_summary")
        if isinstance(quality_summary, dict):
            try:
                count = int(quality_summary.get("count") or 0)
                avg_score = float(quality_summary.get("avg_score") or 0)
            except Exception:
                count = 0
                avg_score = 0.0
            if count > 0:
                bucket["order_upload_quality_score_sum"] += int(round(avg_score * count))
                bucket["order_upload_quality_score_count"] += count

    payment_rows = (
        await db.execute(
            select(
                func.date(CreditPurchase.completed_at),
                func.count(CreditPurchase.id),
                func.coalesce(func.sum(CreditPurchase.price_cents), 0),
            )
            .where(
                CreditPurchase.status == CreditPurchaseStatus.PAID.value,
                CreditPurchase.completed_at.is_not(None),
                CreditPurchase.completed_at >= start_dt,
            )
            .group_by(func.date(CreditPurchase.completed_at))
        )
    ).all()
    for day_value, count, cents in payment_rows:
        bucket = daily.get(day_value.isoformat())
        if not bucket:
            continue
        bucket["payments_completed"] += int(count or 0)
        bucket["payment_revenue_usd"] += round((int(cents or 0)) / 100.0, 2)

    rows = list(daily.values())
    for row in rows:
        row["upload_success_rate"] = _rate(row["upload_completed"], row["upload_started"])
        row["generation_success_rate"] = _rate(row["order_completed"], row["order_created"])
        row["qa_failure_rate"] = _rate(row["qa_failed_orders"], row["order_created"])
        row["identity_failure_rate"] = _rate(row["identity_failed_orders"], row["order_created"])
        row["payment_conversion_rate"] = _rate(row["payments_completed"], row["download_locked_clicked"])
        row["avg_upload_duration_ms"] = _avg(row["upload_duration_ms_sum"], row["upload_duration_count"])
        quality_sum = row["upload_quality_score_sum"] or row["order_upload_quality_score_sum"]
        quality_count = row["upload_quality_score_count"] or row["order_upload_quality_score_count"]
        row["avg_upload_quality_score"] = _avg(quality_sum, quality_count)
        row["upload_quality_warning_rate"] = _rate(row["upload_quality_warning"], row["upload_quality_scored"])
        row["avg_repair_rounds"] = _avg(row["repair_round_sum"], row["repair_round_count"])
    totals = {
        "registered": sum(row["registered"] for row in rows),
        "upload_started": sum(row["upload_started"] for row in rows),
        "upload_completed": sum(row["upload_completed"] for row in rows),
        "upload_quality_scored": sum(row["upload_quality_scored"] for row in rows),
        "upload_quality_warning": sum(row["upload_quality_warning"] for row in rows),
        "upload_quality_poor": sum(row["upload_quality_poor"] for row in rows),
        "order_created": sum(row["order_created"] for row in rows),
        "order_completed": sum(row["order_completed"] for row in rows),
        "result_viewed": sum(row["result_viewed"] for row in rows),
        "download_locked_clicked": sum(row["download_locked_clicked"] for row in rows),
        "payments_completed": sum(row["payments_completed"] for row in rows),
        "download_success": sum(row["download_success"] for row in rows),
        "qa_failed_orders": sum(row["qa_failed_orders"] for row in rows),
        "identity_failed_orders": sum(row["identity_failed_orders"] for row in rows),
        "identity_grade_counts": {
            grade: sum(row["identity_grade_counts"].get(grade, 0) for row in rows)
            for grade in IDENTITY_GRADES
        },
        "payment_revenue_usd": round(sum(row["payment_revenue_usd"] for row in rows), 2),
    }
    totals["upload_success_rate"] = _rate(totals["upload_completed"], totals["upload_started"])
    totals["generation_success_rate"] = _rate(totals["order_completed"], totals["order_created"])
    totals["qa_failure_rate"] = _rate(totals["qa_failed_orders"], totals["order_created"])
    totals["identity_failure_rate"] = _rate(totals["identity_failed_orders"], totals["order_created"])
    totals["payment_conversion_rate"] = _rate(totals["payments_completed"], totals["download_locked_clicked"])
    totals["avg_upload_duration_ms"] = _avg(
        sum(row["upload_duration_ms_sum"] for row in rows),
        sum(row["upload_duration_count"] for row in rows),
    )
    upload_quality_score_sum = sum(row["upload_quality_score_sum"] for row in rows)
    upload_quality_score_count = sum(row["upload_quality_score_count"] for row in rows)
    if not upload_quality_score_count:
        upload_quality_score_sum = sum(row["order_upload_quality_score_sum"] for row in rows)
        upload_quality_score_count = sum(row["order_upload_quality_score_count"] for row in rows)
    totals["avg_upload_quality_score"] = _avg(upload_quality_score_sum, upload_quality_score_count)
    totals["upload_quality_warning_rate"] = _rate(
        totals["upload_quality_warning"],
        totals["upload_quality_scored"],
    )
    totals["avg_repair_rounds"] = _avg(
        sum(row["repair_round_sum"] for row in rows),
        sum(row["repair_round_count"] for row in rows),
    )
    return {"days": len(days_list), "daily": rows, "totals": totals}


def _rate(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _avg(total: int, count: int) -> float:
    if not count:
        return 0.0
    return round(float(total) / float(count), 2)


def _top_counts(counts: dict[str, int], *, key_name: str, limit: int = 8) -> list[dict[str, Any]]:
    rows = [
        {key_name: str(key), "count": int(value)}
        for key, value in counts.items()
        if str(key).strip() and int(value or 0) > 0
    ]
    rows.sort(key=lambda item: (-item["count"], item[key_name]))
    return rows[: max(1, int(limit))]


async def get_quality_dashboard(db: AsyncSession, *, days: int = 30, limit: int = 50) -> dict[str, Any]:
    days_list = _daterange(days)
    start_dt = datetime.combine(days_list[0], datetime.min.time())
    max_rows = max(1, min(5000, int(limit or 50) * 100))
    rows = (
        await db.execute(
            select(Order.template_id, Order.status, Order.generation_params, Order.error_message)
            .where(Order.created_at >= start_dt)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(max_rows)
        )
    ).all()
    dashboard = _build_quality_dashboard_from_orders(rows)
    return {
        "days": len(days_list),
        "sampled_orders": len(rows),
        **dashboard,
        "templates": dashboard["templates"][: max(1, min(200, int(limit)))],
        "failure_reasons": dashboard["failure_reasons"][: max(1, min(200, int(limit)))],
    }


async def get_template_ranking(db: AsyncSession, *, days: int = 30, limit: int = 20) -> list[dict[str, Any]]:
    days_list = _daterange(days)
    start_day = days_list[0]
    start_dt = datetime.combine(start_day, datetime.min.time())
    scores: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "template_id": "",
            "clicks": 0,
            "orders": 0,
            "completed_orders": 0,
            "downloads": 0,
            "leads": 0,
            "ab_picks": 0,
        }
    )

    click_rows = (
        await db.execute(
            select(ClickStat.template_id, func.sum(ClickStat.count))
            .where(ClickStat.day >= start_day, ClickStat.event_type == "template_click", ClickStat.template_id != "na")
            .group_by(ClickStat.template_id)
        )
    ).all()
    for template_id, count in click_rows:
        key = (template_id or "").strip()
        if not key:
            continue
        scores[key]["template_id"] = key
        scores[key]["clicks"] += int(count or 0)

    download_rows = (
        await db.execute(
            select(ClickStat.template_id, func.sum(ClickStat.count))
            .where(ClickStat.day >= start_day, ClickStat.event_type == "download_success", ClickStat.template_id != "na")
            .group_by(ClickStat.template_id)
        )
    ).all()
    for template_id, count in download_rows:
        key = (template_id or "").strip()
        if not key:
            continue
        scores[key]["template_id"] = key
        scores[key]["downloads"] += int(count or 0)

    ab_rows = (
        await db.execute(
            select(ClickStat.template_id, func.sum(ClickStat.count))
            .where(ClickStat.day >= start_day, ClickStat.event_type == "ab_variant_selected", ClickStat.template_id != "na")
            .group_by(ClickStat.template_id)
        )
    ).all()
    for template_id, count in ab_rows:
        key = (template_id or "").strip()
        if not key:
            continue
        scores[key]["template_id"] = key
        scores[key]["ab_picks"] += int(count or 0)

    order_rows = (
        await db.execute(
            select(Order.template_id, Order.status, func.count(Order.id))
            .where(Order.created_at >= start_dt, Order.template_id.is_not(None))
            .group_by(Order.template_id, Order.status)
        )
    ).all()
    for template_id, status, count in order_rows:
        key = (template_id or "").strip()
        if not key:
            continue
        scores[key]["template_id"] = key
        scores[key]["orders"] += int(count or 0)
        if str(status) == OrderStatus.COMPLETED.value:
            scores[key]["completed_orders"] += int(count or 0)

    lead_rows = (
        await db.execute(select(Lead.notes).where(Lead.created_at >= start_dt))
    ).scalars().all()
    for notes in lead_rows:
        meta = _parse_lead_notes(notes)
        key = (meta.get("template_id") or "").strip()
        if not key:
            continue
        scores[key]["template_id"] = key
        scores[key]["leads"] += 1

    rows: list[dict[str, Any]] = []
    for item in scores.values():
        template = get_template_by_id(item["template_id"])
        ranking_score = (
            int(item["leads"]) * 8
            + int(item["ab_picks"]) * 5
            + int(item["completed_orders"]) * 4
            + int(item["downloads"]) * 3
            + int(item["orders"]) * 2
            + int(item["clicks"])
        )
        rows.append(
            {
                **item,
                "ranking_score": ranking_score,
                "template_title": getattr(template, "marketing_title", None)
                or getattr(template, "title", None)
                or item["template_id"],
                "style_family": getattr(template, "style_family", None) or item["template_id"],
                "order_conversion_rate": _rate(int(item["orders"]), int(item["clicks"])),
                "completion_rate": _rate(int(item["completed_orders"]), int(item["orders"])),
                "download_conversion_rate": _rate(int(item["downloads"]), int(item["completed_orders"])),
            }
        )

    rows.sort(
        key=lambda item: (
            -item["ranking_score"],
            -item["ab_picks"],
            -item["leads"],
            -item["completed_orders"],
            -item["orders"],
            -item["clicks"],
            item["template_id"],
        )
    )
    return rows[: max(1, min(100, int(limit)))]


async def get_city_ranking(db: AsyncSession, *, days: int = 30, limit: int = 20) -> list[dict[str, Any]]:
    days_list = _daterange(days)
    start_dt = datetime.combine(days_list[0], datetime.min.time())
    result = await db.execute(
        select(Lead.city, func.count(Lead.id))
        .where(Lead.created_at >= start_dt)
        .group_by(Lead.city)
        .order_by(func.count(Lead.id).desc(), Lead.city.asc())
        .limit(max(1, min(100, int(limit))))
    )
    return [{"city": city or "unknown", "leads": int(count or 0)} for city, count in result.all()]
