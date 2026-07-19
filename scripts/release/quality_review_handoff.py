#!/usr/bin/env python3
"""Prepare and sign the explicit human handoff for six Production quality cases."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release._acceptance_evidence import (
    canonical,
    canonical_sha256,
    read_private_json,
    reject_sensitive_evidence,
    signing_key,
    validate_release_binding,
    verify_signed_report,
    write_private_create_once,
)
from scripts.release.collect_production_acceptance_input import _database_url


REQUEST_SCHEMA = "vowpic.quality-human-review-request.v1"
DRAFT_SCHEMA = "vowpic.quality-human-review-draft.v1"
REVIEW_SCHEMA = "vowpic.quality-human-review.v1"


def _utc(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_utc(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} is invalid")
    return parsed.astimezone(timezone.utc)


def _sign(payload: dict[str, Any], key: bytes) -> dict[str, Any]:
    reject_sensitive_evidence(payload)
    signature = hmac.new(
        key,
        canonical(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {**payload, "signature": f"hmac-sha256:{signature}"}


def _coordinate(value: Any, label: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 180:
        raise ValueError(f"{label} is invalid")
    return clean


def build_request(
    *,
    browser: dict[str, Any],
    cases_contract: dict[str, Any],
    rows: list[dict[str, Any]],
    key: bytes,
    produced_at: datetime | None = None,
) -> dict[str, Any]:
    validate_release_binding(browser)
    if cases_contract.get("schema") != "vowpic.quality-cases.v1":
        raise ValueError("quality cases contract is invalid")
    expected_ids = [item.get("id") for item in cases_contract.get("cases", [])]
    browser_cases = browser.get("cases")
    if (
        len(expected_ids) != 6
        or len(set(expected_ids)) != 6
        or not isinstance(browser_cases, list)
        or len(browser_cases) != 6
    ):
        raise ValueError("quality review request requires exactly six cases")
    browser_by_id = {
        item.get("id"): item for item in browser_cases if isinstance(item, dict)
    }
    rows_by_order = {str(row.get("order_id")): row for row in rows}
    if (
        set(browser_by_id) != set(expected_ids)
        or len(rows) != 6
        or len(rows_by_order) != 6
    ):
        raise ValueError("quality browser cases are not exact")
    request_cases: list[dict[str, str]] = []
    for case_id in expected_ids:
        browser_case = browser_by_id[case_id]
        if set(browser_case) != {"id", "order_id"}:
            raise ValueError(f"quality browser case {case_id} fields are not exact")
        row = rows_by_order.get(str(browser_case["order_id"]))
        if (
            not row
            or str(row.get("user_id")) != str(browser.get("user_id"))
            or row.get("order_status") != "READY"
            or row.get("job_status") != "FINISHED"
            or row.get("candidate_status") != "ACTIVE"
            or row.get("candidate_role") != "candidate"
            or row.get("review_asset_status") != "ACTIVE"
            or row.get("review_asset_role") != "final_master"
        ):
            raise ValueError(f"quality review request case {case_id} is not reviewable")
        request_cases.append(
            {
                "id": _coordinate(case_id, "quality case ID"),
                "order_id": _coordinate(row["order_id"], f"{case_id} order"),
                "job_id": _coordinate(row["job_id"], f"{case_id} job"),
                "selected_candidate_id": _coordinate(
                    row["selected_candidate_id"],
                    f"{case_id} candidate",
                ),
                "review_asset_id": _coordinate(
                    row["review_asset_id"],
                    f"{case_id} review asset",
                ),
            }
        )
    created = produced_at or datetime.now(timezone.utc)
    unsigned = {
        "schema": REQUEST_SCHEMA,
        "passed": True,
        "source_sha": browser["source_sha"],
        "runtime_bundle_id": browser["runtime_bundle_id"],
        "deployment_id": browser["deployment_id"],
        "manifest_sha256": browser["manifest_sha256"],
        "user_subject_hmac_sha256": browser["user_subject_hmac_sha256"],
        "user_id": _coordinate(browser["user_id"], "quality user"),
        "cases_contract_sha256": canonical_sha256(cases_contract),
        "cases": request_cases,
        "produced_at": _utc(created),
        "expires_at": _utc(created + timedelta(seconds=7200)),
    }
    return _sign(unsigned, key)


def build_draft_template(request: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
    if rubric.get("schema") != "vowpic.quality-rubric.v1":
        raise ValueError("quality rubric is invalid")
    dimensions = list(rubric.get("dimensions") or [])
    if not dimensions:
        raise ValueError("quality rubric dimensions are missing")
    return {
        "schema": DRAFT_SCHEMA,
        "review_complete": False,
        "request_sha256": canonical_sha256(request),
        "cases": [
            {
                "id": item["id"],
                "reviewer_ref": "",
                "scores": {dimension: 0 for dimension in dimensions},
                "hard_defects": [],
                "reviewed_at": "",
            }
            for item in request["cases"]
        ],
    }


def build_signed_review(
    *,
    request: dict[str, Any],
    draft: dict[str, Any],
    rubric: dict[str, Any],
    acceptance_key: bytes,
    review_key: bytes,
    produced_at: datetime | None = None,
) -> dict[str, Any]:
    request_unsigned = verify_signed_report(
        request,
        key=acceptance_key,
        expected={"schema": REQUEST_SCHEMA, "passed": True},
        label="quality review request",
    )
    validate_release_binding(request_unsigned)
    review_time = (produced_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    request_start = _parse_utc(
        request_unsigned.get("produced_at"),
        "quality review request time",
    )
    request_expiry = _parse_utc(
        request_unsigned.get("expires_at"),
        "quality review request expiry",
    )
    if review_time < request_start or review_time > request_expiry:
        raise ValueError("quality review request has expired")
    if (
        draft.get("schema") != DRAFT_SCHEMA
        or draft.get("review_complete") is not True
        or draft.get("request_sha256") != canonical_sha256(request)
        or rubric.get("schema") != "vowpic.quality-rubric.v1"
    ):
        raise ValueError("quality human review draft is incomplete or misbound")
    dimensions = list(rubric.get("dimensions") or [])
    allowed_defects = set(rubric.get("hard_defects") or [])
    request_by_id = {item["id"]: item for item in request_unsigned["cases"]}
    draft_cases = draft.get("cases")
    if not isinstance(draft_cases, list):
        raise ValueError("quality human review draft cases are missing")
    draft_by_id = {
        item.get("id"): item for item in draft_cases if isinstance(item, dict)
    }
    if set(draft_by_id) != set(request_by_id):
        raise ValueError("quality human review draft case set is not exact")
    reviews: list[dict[str, Any]] = []
    for case_id, coordinates in request_by_id.items():
        entry = draft_by_id[case_id]
        if set(entry) != {
            "id",
            "reviewer_ref",
            "scores",
            "hard_defects",
            "reviewed_at",
        }:
            raise ValueError(f"quality review draft case {case_id} fields are not exact")
        scores = entry.get("scores")
        hard_defects = entry.get("hard_defects")
        reviewed_at = _parse_utc(entry.get("reviewed_at"), f"{case_id} reviewed_at")
        if (
            not isinstance(scores, dict)
            or set(scores) != set(dimensions)
            or not all(
                isinstance(scores[name], int)
                and rubric["score_minimum"] <= scores[name] <= rubric["score_maximum"]
                for name in dimensions
            )
            or not isinstance(hard_defects, list)
            or any(defect not in allowed_defects for defect in hard_defects)
            or not str(entry.get("reviewer_ref") or "").strip()
            or len(str(entry.get("reviewer_ref"))) > 180
            or reviewed_at < request_start
            or reviewed_at > review_time
        ):
            raise ValueError(f"quality review draft case {case_id} is invalid")
        reviews.append(
            {
                "id": case_id,
                "order_id": coordinates["order_id"],
                "job_id": coordinates["job_id"],
                "selected_candidate_id": coordinates["selected_candidate_id"],
                "review_asset_id": coordinates["review_asset_id"],
                "reviewer_ref": str(entry["reviewer_ref"]),
                "scores": scores,
                "hard_defects": hard_defects,
                "reviewed_at": str(entry["reviewed_at"]),
            }
        )
    unsigned = {
        "schema": REVIEW_SCHEMA,
        "review_complete": True,
        "source_sha": request_unsigned["source_sha"],
        "runtime_bundle_id": request_unsigned["runtime_bundle_id"],
        "deployment_id": request_unsigned["deployment_id"],
        "manifest_sha256": request_unsigned["manifest_sha256"],
        "user_subject_hmac_sha256": request_unsigned["user_subject_hmac_sha256"],
        "user_id": request_unsigned["user_id"],
        "request_sha256": canonical_sha256(request),
        "cases": reviews,
        "produced_at": _utc(review_time),
    }
    return _sign(unsigned, review_key)


def _prepare(args: argparse.Namespace) -> None:
    key = signing_key(args.signing_key_env)
    browser, _ = read_private_json(Path(args.browser_report), label="quality browser report")
    browser_unsigned = verify_signed_report(
        browser,
        key=key,
        expected={
            "schema": "vowpic.acceptance-browser-observation.v1",
            "phase": "quality",
            "passed": True,
        },
        label="quality browser report",
    )
    cases_contract = json.loads(Path(args.quality_cases).read_text(encoding="utf-8"))
    rubric = json.loads(Path(args.quality_rubric).read_text(encoding="utf-8"))
    import psycopg2
    from psycopg2.extras import RealDictCursor

    order_ids = [
        str(item.get("order_id"))
        for item in browser_unsigned.get("cases", [])
        if isinstance(item, dict)
    ]
    with psycopg2.connect(_database_url(os.environ.get(args.database_url_env, ""))) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT o.id AS order_id, o.user_id, o.status AS order_status,
                       j.id AS job_id, j.status AS job_status,
                       q.candidate_asset_id AS selected_candidate_id,
                       ma.status AS candidate_status, ma.role AS candidate_role,
                       review_asset.id AS review_asset_id,
                       review_asset.status AS review_asset_status,
                       review_asset.role AS review_asset_role
                FROM orders o
                JOIN generation_jobs j ON j.id = o.generation_job_id
                JOIN qa_verdicts q ON q.job_id = j.id AND q.decision = 'PASS'
                JOIN media_assets ma ON ma.id = q.candidate_asset_id
                JOIN media_assets review_asset
                 ON review_asset.parent_asset_id = q.candidate_asset_id
                 AND review_asset.owner_user_id = o.user_id
                 AND review_asset.role = 'final_master'
                 AND review_asset.status = 'ACTIVE'
                 AND o.final_asset_ids @> jsonb_build_array(review_asset.id::text)
                WHERE o.id = ANY(%s::uuid[]) AND o.user_id = %s
                """,
                (order_ids, browser_unsigned["user_id"]),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        connection.rollback()
    request = build_request(
        browser=browser_unsigned,
        cases_contract=cases_contract,
        rows=rows,
        key=key,
    )
    write_private_create_once(Path(args.output), request)
    write_private_create_once(
        Path(args.draft_output),
        build_draft_template(request, rubric),
    )


def _sign_review(args: argparse.Namespace) -> None:
    request, _ = read_private_json(Path(args.request), label="quality review request")
    draft, _ = read_private_json(Path(args.draft), label="quality review draft")
    rubric = json.loads(Path(args.quality_rubric).read_text(encoding="utf-8"))
    review = build_signed_review(
        request=request,
        draft=draft,
        rubric=rubric,
        acceptance_key=signing_key(args.acceptance_signing_key_env),
        review_key=signing_key(args.review_signing_key_env),
    )
    write_private_create_once(Path(args.output), review)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--browser-report", required=True)
    prepare.add_argument("--quality-cases", required=True)
    prepare.add_argument("--quality-rubric", required=True)
    prepare.add_argument("--database-url-env", required=True)
    prepare.add_argument(
        "--signing-key-env",
        default="ACCEPTANCE_EVIDENCE_SIGNING_KEY",
    )
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--draft-output", required=True)
    sign = subparsers.add_parser("sign")
    sign.add_argument("--request", required=True)
    sign.add_argument("--draft", required=True)
    sign.add_argument("--quality-rubric", required=True)
    sign.add_argument(
        "--acceptance-signing-key-env",
        default="ACCEPTANCE_EVIDENCE_SIGNING_KEY",
    )
    sign.add_argument(
        "--review-signing-key-env",
        default="QUALITY_REVIEW_SIGNING_KEY",
    )
    sign.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            _prepare(args)
        else:
            _sign_review(args)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
