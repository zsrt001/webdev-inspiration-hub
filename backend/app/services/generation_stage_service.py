"""Customer-visible generation progress stages."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


GENERATION_STAGES = (
    "queued",
    "identity_refs_ready",
    "provider_submitted",
    "qa_checking",
    "repairing",
    "postprocessing",
    "completed",
    "failed",
)


def merge_generation_stage(params: dict[str, Any] | None, stage: str, *, detail: str | None = None) -> dict[str, Any]:
    """Merge a generation stage into order params with a compact append-only history."""
    next_params = dict(params) if isinstance(params, dict) else {}
    clean_stage = str(stage or "").strip()
    if clean_stage not in GENERATION_STAGES:
        clean_stage = "queued"
    history = next_params.get("generation_stage_history")
    if not isinstance(history, list):
        history = []
    entry: dict[str, Any] = {
        "stage": clean_stage,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if detail:
        entry["detail"] = str(detail)[:120]
    if not history or history[-1].get("stage") != clean_stage:
        history.append(entry)
    next_params["generation_stage"] = clean_stage
    next_params["generation_stage_history"] = history[-12:]
    return next_params
