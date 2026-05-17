"""Identity consistency policy for generation and QA decisions."""

from __future__ import annotations

from typing import Any

from app.services.qa_rules import normalize_qa_reason


IDENTITY_QA_GRADES = {"identity_pass", "minor_drift", "major_mismatch", "role_swap"}
BLOCKING_IDENTITY_QA_GRADES = {"major_mismatch", "role_swap"}


def classify_identity_qa(
    reasons: list[str] | None,
    issues: list[dict[str, Any]] | None = None,
    *,
    is_couple: bool = False,
) -> str:
    """Classify identity risk separately from generic QA reasons."""

    normalized = {normalize_qa_reason(str(reason)) for reason in (reasons or []) if str(reason or "").strip()}
    if "identity_swap" in normalized:
        return "role_swap"
    if normalized & {
        "face_distortion",
        "fused_faces",
        "subject_missing",
        "headless",
        "identity_similarity_low",
        "identity_averaging",
        "identity_face_missing",
    }:
        return "major_mismatch"
    if normalized & {"identity_mismatch", "identity_margin_low"}:
        return "major_mismatch"

    saw_identity_issue = False
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        code = normalize_qa_reason(str(issue.get("code") or ""))
        category = str(issue.get("category") or "").strip().lower()
        target = str(issue.get("target") or "").strip().lower()
        severity = str(issue.get("severity") or "").strip().lower()
        if code == "identity_swap" or (is_couple and "role" in target and "swap" in target):
            return "role_swap"
        if code in {
            "face_distortion",
            "fused_faces",
            "subject_missing",
            "headless",
            "identity_similarity_low",
            "identity_averaging",
            "identity_face_missing",
        }:
            return "major_mismatch"
        if code in {"identity_mismatch", "identity_margin_low"} or category == "identity" or "identity" in target:
            saw_identity_issue = True
            if severity in {"critical", "major", "blocking"}:
                return "major_mismatch"

    if saw_identity_issue:
        return "minor_drift"
    return "identity_pass"


def identity_qa_requires_forced_repair(identity_grade: str | None) -> bool:
    """Return true when an identity QA grade must trigger repair or failure."""

    return str(identity_grade or "").strip() in BLOCKING_IDENTITY_QA_GRADES
