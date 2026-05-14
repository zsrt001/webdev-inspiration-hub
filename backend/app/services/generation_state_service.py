"""Shared order generation state helpers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.core.database import async_session_maker
from app.models.order import Order


def build_qa_history_entry(
    *,
    attempt: int,
    reasons: list[str],
    candidate_url: str,
    engine: str,
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entry = {
        "attempt": int(attempt),
        "reasons": [str(reason) for reason in reasons],
        "candidate_url": str(candidate_url or ""),
        "engine": str(engine or "unknown"),
    }
    if issues:
        entry["issues"] = issues
    return entry


def merge_qa_failure_state(
    params: dict[str, Any],
    *,
    attempt: int,
    reasons: list[str],
    candidate_url: str,
    engine: str,
    extra_params: dict[str, Any] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    next_params = dict(params or {})
    debug = dict(next_params.get("debug")) if isinstance(next_params.get("debug"), dict) else {}
    qa_history = debug.get("qa_history") if isinstance(debug.get("qa_history"), list) else []
    qa_history.append(
        build_qa_history_entry(
            attempt=attempt,
            reasons=reasons,
            candidate_url=candidate_url,
            engine=engine,
            issues=issues,
        )
    )
    debug["qa_history"] = qa_history[-8:]
    next_params["debug"] = debug
    next_params["qa_last_reasons"] = [str(reason) for reason in reasons]
    if issues:
        next_params["qa_last_issues"] = issues
    next_params["qa_attempt_count"] = int(attempt)
    if extra_params:
        next_params.update(extra_params)
    return next_params


async def record_generation_qa_failure(
    order_uuid: uuid.UUID,
    *,
    attempt: int,
    reasons: list[str],
    candidate_url: str,
    engine: str,
    extra_params: dict[str, Any] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> None:
    async with async_session_maker() as db:
        result = await db.execute(select(Order).where(Order.id == order_uuid))
        order = result.scalar_one_or_none()
        if not order:
            return
        params = order.generation_params if isinstance(order.generation_params, dict) else {}
        order.generation_params = merge_qa_failure_state(
            params,
            attempt=attempt,
            reasons=reasons,
            candidate_url=candidate_url,
            engine=engine,
            extra_params=extra_params,
            issues=issues,
        )
        await db.commit()
