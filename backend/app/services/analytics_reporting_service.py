"""Analytics derived from normalized commercial and generation facts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.click_stat import ClickStat
from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus
from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJob
from app.models.lead import Lead
from app.models.order import Order, OrderStatus
from app.models.qa_verdict import QaDecision, QaVerdict
from app.models.user import User
from app.services.template_service import get_template_by_id


IDENTITY_GRADES = ("identity_pass", "minor_drift", "major_mismatch", "role_swap")
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
        "background_over_blurred",
        "harsh_backlight",
    },
    "composition": {
        "subject_too_small",
        "face_too_small",
        "unexpected_extra_subject",
        "background_dominates",
        "excessive_headroom",
        "awkward_crop",
        "dress_cropped",
        "flat_centered_pose",
        "weak_couple_interaction",
    },
    "anatomy": {"bad_hands", "extra_limbs", "body_fusion"},
    "wardrobe": {"dress_exposure_error"},
    "technical": {
        "black_or_blank",
        "watermark_or_text",
        "severe_artifacts",
        "low_resolution",
        "too_blurry",
    },
    "safety": {"nsfw"},
    "ops": {
        "vision_error",
        "vision_schema_invalid",
        "qa_strict_runtime_disabled",
        "qa_local_checker_unavailable",
        "qa_source_identity_missing",
        "identity_embedding_unavailable",
        "photometric_qa_unavailable",
    },
}


class AnalyticsFactError(RuntimeError):
    """A normalized fact has a shape that cannot be reported truthfully."""


@dataclass(frozen=True, slots=True)
class NormalizedQualityFact:
    order_id: uuid.UUID
    created_at: datetime
    template_id: str
    order_status: OrderStatus | str
    attempt_number: int
    attempt_kind: GenerationAttemptKind | str
    attempt_status: GenerationAttemptStatus | str
    cost_minor_units: int | None
    cost_currency: str | None
    decision: QaDecision | str | None
    reasons: tuple[str, ...]
    scores: dict[str, float]


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _daterange(days: int) -> list[date]:
    bounded = max(1, min(90, int(days)))
    end_day = date.today()
    start_day = end_day - timedelta(days=bounded - 1)
    return [start_day + timedelta(days=index) for index in range(bounded)]


def _rate(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _avg(total: float, count: int) -> float:
    if not count:
        return 0.0
    return round(float(total) / float(count), 2)


def _top_counts(
    counts: dict[str, int],
    *,
    key_name: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows = [
        {key_name: str(key), "count": int(value)}
        for key, value in counts.items()
        if str(key).strip() and int(value or 0) > 0
    ]
    rows.sort(key=lambda item: (-item["count"], item[key_name]))
    return rows[: max(1, int(limit))]


def _parse_lead_notes(notes: str | None) -> dict[str, str]:
    meta: dict[str, str] = {}
    for part in str(notes or "").split(" | "):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() and value.strip():
            meta[key.strip()] = value.strip()
    return meta


def _quality_reason_group(reason: str) -> str:
    value = str(reason or "").strip()
    for group, reasons in QUALITY_REASON_GROUPS.items():
        if value in reasons:
            return group
    return "other"


def _normalized_reasons(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise AnalyticsFactError("qa_verdict_reasons_invalid")
    reasons = tuple(str(item).strip() for item in value)
    if (
        len(reasons) > 16
        or any(not reason for reason in reasons)
        or len(set(reasons)) != len(reasons)
    ):
        raise AnalyticsFactError("qa_verdict_reasons_invalid")
    return reasons


def _normalized_scores(value: object) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AnalyticsFactError("qa_verdict_metrics_invalid")
    raw_scores = value.get("scores")
    if raw_scores is None:
        return {}
    if not isinstance(raw_scores, dict):
        raise AnalyticsFactError("qa_verdict_metrics_invalid")
    scores: dict[str, float] = {}
    for name, raw in raw_scores.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise AnalyticsFactError("qa_verdict_metrics_invalid")
        score = float(raw)
        if score < 0 or score > 1:
            raise AnalyticsFactError("qa_verdict_metrics_invalid")
        scores[str(name)] = score
    return scores


def _quality_fact_from_row(row: tuple[Any, ...]) -> NormalizedQualityFact:
    (
        order_id,
        created_at,
        template_id,
        order_status,
        attempt_number,
        attempt_kind,
        attempt_status,
        cost_minor_units,
        cost_currency,
        decision,
        reasons,
        metrics,
    ) = row
    if not isinstance(order_id, uuid.UUID) or not isinstance(created_at, datetime):
        raise AnalyticsFactError("generation_analytics_lineage_invalid")
    try:
        normalized_kind = GenerationAttemptKind(attempt_kind)
        normalized_status = GenerationAttemptStatus(attempt_status)
        normalized_decision = None if decision is None else QaDecision(decision)
    except (TypeError, ValueError) as exc:
        raise AnalyticsFactError("generation_analytics_enum_invalid") from exc
    amount = None if cost_minor_units is None else int(cost_minor_units)
    currency = None if cost_currency is None else str(cost_currency).strip().upper()
    if (amount is None) != (currency is None) or (amount is not None and amount < 0):
        raise AnalyticsFactError("generation_analytics_cost_invalid")
    return NormalizedQualityFact(
        order_id=order_id,
        created_at=created_at,
        template_id=str(template_id or "unknown").strip() or "unknown",
        order_status=order_status,
        attempt_number=int(attempt_number),
        attempt_kind=normalized_kind,
        attempt_status=normalized_status,
        cost_minor_units=amount,
        cost_currency=currency,
        decision=normalized_decision,
        reasons=_normalized_reasons(reasons),
        scores=_normalized_scores(metrics),
    )


async def _load_quality_facts(
    db: AsyncSession,
    *,
    start_dt: datetime,
    order_ids: Iterable[uuid.UUID] | None = None,
) -> list[NormalizedQualityFact]:
    statement = (
        select(
            Order.id,
            Order.created_at,
            Order.template_id,
            Order.status,
            GenerationAttempt.attempt_number,
            GenerationAttempt.kind,
            GenerationAttempt.status,
            GenerationAttempt.cost_minor_units,
            GenerationAttempt.cost_currency,
            QaVerdict.decision,
            QaVerdict.reasons,
            QaVerdict.metrics,
        )
        .join(GenerationJob, GenerationJob.order_id == Order.id)
        .join(GenerationAttempt, GenerationAttempt.job_id == GenerationJob.id)
        .outerjoin(QaVerdict, QaVerdict.attempt_id == GenerationAttempt.id)
        .where(Order.created_at >= start_dt)
        .order_by(Order.created_at.desc(), Order.id, GenerationAttempt.attempt_number)
    )
    bounded_ids = tuple(order_ids or ())
    if bounded_ids:
        statement = statement.where(Order.id.in_(bounded_ids))
    rows = (await db.execute(statement)).all()
    return [_quality_fact_from_row(tuple(row)) for row in rows]


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
        "in_progress_orders": 0,
        "qa_failed_orders": 0,
        "identity_failed_orders": 0,
        "lighting_failed_orders": 0,
        "composition_failed_orders": 0,
        "repair_round_sum": 0,
        "repair_round_count": 0,
        "reason_counts": {},
        "provider_cost_minor_units": {},
        "relight_attempts": 0,
        "relight_successes": 0,
    }


def _add_currency_amount(target: dict[str, int], currency: str | None, amount: int | None) -> None:
    if currency is None or amount is None:
        return
    target[currency] = int(target.get(currency, 0)) + int(amount)


def _build_quality_dashboard_from_facts(
    facts: list[NormalizedQualityFact],
) -> dict[str, Any]:
    by_order: dict[uuid.UUID, list[NormalizedQualityFact]] = defaultdict(list)
    for fact in facts:
        by_order[fact.order_id].append(fact)

    templates: dict[str, dict[str, Any]] = {}
    reasons: dict[str, dict[str, Any]] = {}
    rounds: dict[int, dict[str, Any]] = {}
    modes: dict[str, dict[str, Any]] = {}
    totals: dict[str, Any] = {
        "orders": 0,
        "completed_orders": 0,
        "failed_orders": 0,
        "in_progress_orders": 0,
        "qa_failed_orders": 0,
        "identity_failed_orders": 0,
        "lighting_failed_orders": 0,
        "composition_failed_orders": 0,
        "repair_round_sum": 0,
        "repair_round_count": 0,
        "provider_cost_minor_units": {},
        "relight_attempts": 0,
        "relight_successes": 0,
    }

    for order_facts in by_order.values():
        order_facts.sort(key=lambda item: item.attempt_number)
        first = order_facts[0]
        template_id = first.template_id
        template = templates.setdefault(template_id, _quality_template_bucket(template_id))
        totals["orders"] += 1
        template["orders"] += 1
        status = _enum_value(first.order_status)
        if status in {OrderStatus.READY.value, OrderStatus.COMPLETED.value}:
            totals["completed_orders"] += 1
            template["completed_orders"] += 1
        elif status in {OrderStatus.FAILED.value, OrderStatus.CANCELLED.value}:
            totals["failed_orders"] += 1
            template["failed_orders"] += 1
        else:
            totals["in_progress_orders"] += 1
            template["in_progress_orders"] += 1

        order_reasons = tuple(
            dict.fromkeys(reason for fact in order_facts for reason in fact.reasons)
        )
        decisions = {
            _enum_value(fact.decision)
            for fact in order_facts
            if fact.decision is not None
        }
        if decisions.intersection({QaDecision.REPAIR.value, QaDecision.REJECT.value}):
            totals["qa_failed_orders"] += 1
            template["qa_failed_orders"] += 1
        groups = {_quality_reason_group(reason) for reason in order_reasons}
        for name, group in (
            ("identity_failed_orders", "identity"),
            ("lighting_failed_orders", "lighting"),
            ("composition_failed_orders", "composition"),
        ):
            if group in groups:
                totals[name] += 1
                template[name] += 1

        repair_count = sum(
            _enum_value(fact.attempt_kind) == GenerationAttemptKind.REPAIR.value
            for fact in order_facts
        )
        totals["repair_round_sum"] += repair_count
        totals["repair_round_count"] += 1
        template["repair_round_sum"] += repair_count
        template["repair_round_count"] += 1

        for reason in order_reasons:
            template["reason_counts"][reason] = int(
                template["reason_counts"].get(reason, 0)
            ) + 1
            reason_bucket = reasons.setdefault(
                reason,
                {
                    "reason": reason,
                    "group": _quality_reason_group(reason),
                    "count": 0,
                    "templates": {},
                    "rounds": {},
                },
            )
            reason_bucket["count"] += 1
            reason_bucket["templates"][template_id] = int(
                reason_bucket["templates"].get(template_id, 0)
            ) + 1

        for fact in order_facts:
            _add_currency_amount(
                totals["provider_cost_minor_units"],
                fact.cost_currency,
                fact.cost_minor_units,
            )
            _add_currency_amount(
                template["provider_cost_minor_units"],
                fact.cost_currency,
                fact.cost_minor_units,
            )
            round_bucket = rounds.setdefault(
                fact.attempt_number,
                {
                    "round": fact.attempt_number,
                    "attempts": 0,
                    "successes": 0,
                    "failures": 0,
                    "pending": 0,
                    "identity_score_sum": 0.0,
                    "identity_score_count": 0,
                    "reason_counts": {},
                    "repair_modes": {},
                    "provider_cost_minor_units": {},
                },
            )
            round_bucket["attempts"] += 1
            decision = _enum_value(fact.decision) if fact.decision is not None else None
            if decision == QaDecision.PASS.value:
                round_bucket["successes"] += 1
            elif decision in {QaDecision.REPAIR.value, QaDecision.REJECT.value} or _enum_value(
                fact.attempt_status
            ) == GenerationAttemptStatus.FAILED.value:
                round_bucket["failures"] += 1
            else:
                round_bucket["pending"] += 1
            identity_score = fact.scores.get("identity")
            if identity_score is not None:
                round_bucket["identity_score_sum"] += float(identity_score)
                round_bucket["identity_score_count"] += 1
            mode = (
                "targeted_repair"
                if _enum_value(fact.attempt_kind) == GenerationAttemptKind.REPAIR.value
                else "initial_generation"
            )
            round_bucket["repair_modes"][mode] = int(
                round_bucket["repair_modes"].get(mode, 0)
            ) + 1
            mode_bucket = modes.setdefault(
                mode,
                {"repair_mode": mode, "attempts": 0, "successes": 0, "failures": 0},
            )
            mode_bucket["attempts"] += 1
            if decision == QaDecision.PASS.value:
                mode_bucket["successes"] += 1
            elif decision in {QaDecision.REPAIR.value, QaDecision.REJECT.value}:
                mode_bucket["failures"] += 1
            _add_currency_amount(
                round_bucket["provider_cost_minor_units"],
                fact.cost_currency,
                fact.cost_minor_units,
            )
            for reason in fact.reasons:
                round_bucket["reason_counts"][reason] = int(
                    round_bucket["reason_counts"].get(reason, 0)
                ) + 1
                reasons[reason]["rounds"][str(fact.attempt_number)] = int(
                    reasons[reason]["rounds"].get(str(fact.attempt_number), 0)
                ) + 1

    template_rows = []
    for item in templates.values():
        item["completion_rate"] = _rate(item["completed_orders"], item["orders"])
        item["qa_failure_rate"] = _rate(item["qa_failed_orders"], item["orders"])
        item["identity_failure_rate"] = _rate(item["identity_failed_orders"], item["orders"])
        item["lighting_failure_rate"] = _rate(item["lighting_failed_orders"], item["orders"])
        item["composition_failure_rate"] = _rate(item["composition_failed_orders"], item["orders"])
        item["avg_repair_rounds"] = _avg(
            item["repair_round_sum"], item["repair_round_count"]
        )
        item["relight_success_rate"] = 0.0
        item["top_reasons"] = _top_counts(
            item["reason_counts"], key_name="reason", limit=8
        )
        template_rows.append(item)

    round_rows = []
    for item in rounds.values():
        item["success_rate"] = _rate(item["successes"], item["attempts"])
        item["avg_identity_score"] = _avg(
            item.pop("identity_score_sum"), item.pop("identity_score_count")
        )
        item["top_reasons"] = _top_counts(
            item["reason_counts"], key_name="reason", limit=8
        )
        round_rows.append(item)

    reason_rows = []
    for item in reasons.values():
        item["top_templates"] = _top_counts(
            item["templates"], key_name="template_id", limit=6
        )
        item["round_counts"] = dict(
            sorted(item["rounds"].items(), key=lambda pair: int(pair[0]))
        )
        reason_rows.append(item)

    mode_rows = []
    for item in modes.values():
        item["success_rate"] = _rate(item["successes"], item["attempts"])
        mode_rows.append(item)

    totals["completion_rate"] = _rate(totals["completed_orders"], totals["orders"])
    totals["qa_failure_rate"] = _rate(totals["qa_failed_orders"], totals["orders"])
    totals["identity_failure_rate"] = _rate(
        totals["identity_failed_orders"], totals["orders"]
    )
    totals["lighting_failure_rate"] = _rate(
        totals["lighting_failed_orders"], totals["orders"]
    )
    totals["composition_failure_rate"] = _rate(
        totals["composition_failed_orders"], totals["orders"]
    )
    totals["avg_repair_rounds"] = _avg(
        totals["repair_round_sum"], totals["repair_round_count"]
    )
    totals["relight_success_rate"] = 0.0

    template_rows.sort(key=lambda item: (-item["qa_failed_orders"], -item["orders"], item["template_id"]))
    reason_rows.sort(key=lambda item: (-item["count"], item["reason"]))
    round_rows.sort(key=lambda item: item["round"])
    mode_rows.sort(key=lambda item: (-item["attempts"], item["repair_mode"]))
    return {
        "totals": totals,
        "templates": template_rows,
        "failure_reasons": reason_rows,
        "repair_rounds": round_rows,
        "repair_modes": mode_rows,
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
        if bucket is None:
            continue
        mapped = event_map.get(str(event_type or ""))
        if mapped:
            bucket[mapped] += int(count or 0)
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
        if day_value.isoformat() in daily:
            daily[day_value.isoformat()]["registered"] += int(count or 0)

    order_rows = (
        await db.execute(
            select(func.date(Order.created_at), Order.status, func.count(Order.id))
            .where(Order.created_at >= start_dt)
            .group_by(func.date(Order.created_at), Order.status)
        )
    ).all()
    for day_value, status, count in order_rows:
        bucket = daily.get(day_value.isoformat())
        if bucket is None:
            continue
        bucket["order_created"] += int(count or 0)
        if _enum_value(status) in {OrderStatus.READY.value, OrderStatus.COMPLETED.value}:
            bucket["order_completed"] += int(count or 0)

    facts = await _load_quality_facts(db, start_dt=start_dt)
    facts_by_order: dict[uuid.UUID, list[NormalizedQualityFact]] = defaultdict(list)
    for fact in facts:
        facts_by_order[fact.order_id].append(fact)
    for order_facts in facts_by_order.values():
        first = order_facts[0]
        bucket = daily.get(first.created_at.date().isoformat())
        if bucket is None:
            continue
        decisions = {
            _enum_value(fact.decision)
            for fact in order_facts
            if fact.decision is not None
        }
        reasons = {reason for fact in order_facts for reason in fact.reasons}
        if decisions.intersection({QaDecision.REPAIR.value, QaDecision.REJECT.value}):
            bucket["qa_failed_orders"] += 1
        if any(_quality_reason_group(reason) == "identity" for reason in reasons):
            bucket["identity_failed_orders"] += 1
        bucket["repair_round_sum"] += sum(
            _enum_value(fact.attempt_kind) == GenerationAttemptKind.REPAIR.value
            for fact in order_facts
        )
        bucket["repair_round_count"] += 1

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
        if bucket is not None:
            bucket["payments_completed"] += int(count or 0)
            bucket["payment_revenue_usd"] += round(int(cents or 0) / 100.0, 2)

    rows = list(daily.values())
    for row in rows:
        row["upload_success_rate"] = _rate(row["upload_completed"], row["upload_started"])
        row["generation_success_rate"] = _rate(row["order_completed"], row["order_created"])
        row["qa_failure_rate"] = _rate(row["qa_failed_orders"], row["order_created"])
        row["identity_failure_rate"] = _rate(row["identity_failed_orders"], row["order_created"])
        row["payment_conversion_rate"] = _rate(
            row["payments_completed"], row["download_locked_clicked"]
        )
        row["avg_upload_duration_ms"] = _avg(
            row["upload_duration_ms_sum"], row["upload_duration_count"]
        )
        row["avg_upload_quality_score"] = _avg(
            row["upload_quality_score_sum"], row["upload_quality_score_count"]
        )
        row["upload_quality_warning_rate"] = _rate(
            row["upload_quality_warning"], row["upload_quality_scored"]
        )
        row["avg_repair_rounds"] = _avg(
            row["repair_round_sum"], row["repair_round_count"]
        )

    totals = {
        key: sum(row[key] for row in rows)
        for key in (
            "registered",
            "upload_started",
            "upload_completed",
            "upload_quality_scored",
            "upload_quality_warning",
            "upload_quality_poor",
            "order_created",
            "order_completed",
            "result_viewed",
            "download_locked_clicked",
            "payments_completed",
            "download_success",
            "qa_failed_orders",
            "identity_failed_orders",
        )
    }
    totals["identity_grade_counts"] = {grade: 0 for grade in IDENTITY_GRADES}
    totals["payment_revenue_usd"] = round(
        sum(row["payment_revenue_usd"] for row in rows), 2
    )
    totals["upload_success_rate"] = _rate(totals["upload_completed"], totals["upload_started"])
    totals["generation_success_rate"] = _rate(totals["order_completed"], totals["order_created"])
    totals["qa_failure_rate"] = _rate(totals["qa_failed_orders"], totals["order_created"])
    totals["identity_failure_rate"] = _rate(
        totals["identity_failed_orders"], totals["order_created"]
    )
    totals["payment_conversion_rate"] = _rate(
        totals["payments_completed"], totals["download_locked_clicked"]
    )
    totals["avg_upload_duration_ms"] = _avg(
        sum(row["upload_duration_ms_sum"] for row in rows),
        sum(row["upload_duration_count"] for row in rows),
    )
    totals["avg_upload_quality_score"] = _avg(
        sum(row["upload_quality_score_sum"] for row in rows),
        sum(row["upload_quality_score_count"] for row in rows),
    )
    totals["upload_quality_warning_rate"] = _rate(
        totals["upload_quality_warning"], totals["upload_quality_scored"]
    )
    totals["avg_repair_rounds"] = _avg(
        sum(row["repair_round_sum"] for row in rows),
        sum(row["repair_round_count"] for row in rows),
    )
    return {"days": len(days_list), "daily": rows, "totals": totals}


async def get_quality_dashboard(
    db: AsyncSession,
    *,
    days: int = 30,
    limit: int = 50,
) -> dict[str, Any]:
    days_list = _daterange(days)
    start_dt = datetime.combine(days_list[0], datetime.min.time())
    max_orders = max(1, min(5000, int(limit or 50) * 100))
    order_ids = list(
        (
            await db.scalars(
                select(Order.id)
                .where(Order.created_at >= start_dt)
                .order_by(Order.created_at.desc(), Order.id.desc())
                .limit(max_orders)
            )
        ).all()
    )
    facts = (
        await _load_quality_facts(db, start_dt=start_dt, order_ids=order_ids)
        if order_ids
        else []
    )
    dashboard = _build_quality_dashboard_from_facts(facts)
    output_limit = max(1, min(200, int(limit)))
    return {
        "days": len(days_list),
        "sampled_orders": len({fact.order_id for fact in facts}),
        **dashboard,
        "templates": dashboard["templates"][:output_limit],
        "failure_reasons": dashboard["failure_reasons"][:output_limit],
    }


async def get_template_ranking(
    db: AsyncSession,
    *,
    days: int = 30,
    limit: int = 20,
) -> list[dict[str, Any]]:
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

    for event_type, field in (
        ("template_click", "clicks"),
        ("download_success", "downloads"),
        ("ab_variant_selected", "ab_picks"),
    ):
        rows = (
            await db.execute(
                select(ClickStat.template_id, func.sum(ClickStat.count))
                .where(
                    ClickStat.day >= start_day,
                    ClickStat.event_type == event_type,
                    ClickStat.template_id != "na",
                )
                .group_by(ClickStat.template_id)
            )
        ).all()
        for template_id, count in rows:
            key = str(template_id or "").strip()
            if key:
                scores[key]["template_id"] = key
                scores[key][field] += int(count or 0)

    order_rows = (
        await db.execute(
            select(Order.template_id, Order.status, func.count(Order.id))
            .where(Order.created_at >= start_dt, Order.template_id.is_not(None))
            .group_by(Order.template_id, Order.status)
        )
    ).all()
    for template_id, status, count in order_rows:
        key = str(template_id or "").strip()
        if not key:
            continue
        scores[key]["template_id"] = key
        scores[key]["orders"] += int(count or 0)
        if _enum_value(status) in {OrderStatus.READY.value, OrderStatus.COMPLETED.value}:
            scores[key]["completed_orders"] += int(count or 0)

    lead_rows = (
        await db.execute(select(Lead.notes).where(Lead.created_at >= start_dt))
    ).scalars().all()
    for notes in lead_rows:
        key = str(_parse_lead_notes(notes).get("template_id") or "").strip()
        if key:
            scores[key]["template_id"] = key
            scores[key]["leads"] += 1

    result = []
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
        result.append(
            {
                **item,
                "ranking_score": ranking_score,
                "template_title": getattr(template, "marketing_title", None)
                or getattr(template, "title", None)
                or item["template_id"],
                "style_family": getattr(template, "style_family", None)
                or item["template_id"],
                "order_conversion_rate": _rate(item["orders"], item["clicks"]),
                "completion_rate": _rate(item["completed_orders"], item["orders"]),
                "download_conversion_rate": _rate(
                    item["downloads"], item["completed_orders"]
                ),
            }
        )
    result.sort(
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
    return result[: max(1, min(100, int(limit)))]


async def get_city_ranking(
    db: AsyncSession,
    *,
    days: int = 30,
    limit: int = 20,
) -> list[dict[str, Any]]:
    days_list = _daterange(days)
    start_dt = datetime.combine(days_list[0], datetime.min.time())
    result = await db.execute(
        select(Lead.city, func.count(Lead.id))
        .where(Lead.created_at >= start_dt)
        .group_by(Lead.city)
        .order_by(func.count(Lead.id).desc(), Lead.city.asc())
        .limit(max(1, min(100, int(limit))))
    )
    return [
        {"city": city or "unknown", "leads": int(count or 0)}
        for city, count in result.all()
    ]
