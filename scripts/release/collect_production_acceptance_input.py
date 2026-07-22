#!/usr/bin/env python3
"""Build signed acceptance inputs from browser observations and read-only DB facts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release._acceptance_evidence import (
    canonical_sha256,
    read_private_json,
    seal_collected_input,
    signing_key,
    validate_release_binding,
    verify_signed_report,
    write_private_create_once,
)
from scripts.release._acceptance_phase_facts import collect_commercial_before_delete
from scripts.release._acceptance_subscription_facts import collect_subscription
from scripts.release._acceptance_provider_facts import collect_commercial_finalize
from scripts.release._acceptance_quality_facts import collect_quality


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("acceptance read-only database URL is invalid")
    return clean


def _same_binding(first: dict, second: dict, *, label: str) -> None:
    for field in (
        "source_sha",
        "runtime_bundle_id",
        "deployment_id",
        "manifest_sha256",
        "user_subject_hmac_sha256",
    ):
        if first.get(field) != second.get(field):
            raise ValueError(f"{label} {field} mismatch")


def collect(
    *,
    phase: str,
    browser_report_path: Path,
    auth_report_path: Path | None,
    database_url: str,
    key: bytes,
    commercial_report_path: Path | None = None,
    storage_absence_report_path: Path | None = None,
    quality_review_request_path: Path | None = None,
    quality_review_path: Path | None = None,
    quality_review_key: bytes | None = None,
    quality_cases_path: Path | None = None,
    quality_rubric_path: Path | None = None,
) -> tuple[dict, str, dict]:
    browser, browser_raw = read_private_json(
        browser_report_path, label="acceptance browser report"
    )
    validate_release_binding(browser)
    browser_unsigned = verify_signed_report(
        browser,
        key=key,
        expected={
            "schema": "vowpic.acceptance-browser-observation.v1",
            "phase": phase,
            "passed": True,
        },
        label="acceptance browser report",
    )

    auth_unsigned = None
    if auth_report_path is not None:
        auth, _auth_raw = read_private_json(
            auth_report_path, label="signed auth acceptance report"
        )
        auth_unsigned = verify_signed_report(
            auth,
            key=key,
            expected={
                "schema": "vowpic.linked-commercial-acceptance.v1",
                "phase": "first-login-and-auth-security",
                "passed": True,
                "source_sha": browser_unsigned["source_sha"],
                "runtime_bundle_id": browser_unsigned["runtime_bundle_id"],
            },
            label="signed auth acceptance report",
        )
        _same_binding(browser_unsigned, auth_unsigned, label="auth/browser binding")

    def prior_report(
        path: Path | None,
        *,
        report_phase: str,
        label: str,
        report_schema: str = "vowpic.linked-commercial-acceptance.v1",
    ) -> dict:
        if path is None:
            raise ValueError(f"{label} is required")
        report, _raw = read_private_json(path, label=label)
        unsigned = verify_signed_report(
            report,
            key=key,
            expected={
                "schema": report_schema,
                "passed": True,
                "source_sha": browser_unsigned["source_sha"],
                "runtime_bundle_id": browser_unsigned["runtime_bundle_id"],
                **(
                    {"phase": report_phase}
                    if report_schema
                    == "vowpic.linked-commercial-acceptance.v1"
                    else {}
                ),
            },
            label=label,
        )
        _same_binding(browser_unsigned, unsigned, label=f"{label}/browser binding")
        return unsigned

    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            if phase == "commercial-before-delete":
                if auth_unsigned is None:
                    raise ValueError("commercial collection requires the signed auth report")
                payload, facts = collect_commercial_before_delete(
                    cursor,
                    browser=browser_unsigned,
                    auth=auth_unsigned,
                )
            elif phase == "subscription":
                if auth_unsigned is None:
                    raise ValueError("subscription collection requires the signed auth report")
                payload, facts = collect_subscription(
                    cursor,
                    browser=browser_unsigned,
                    auth=auth_unsigned,
                )
            elif phase == "commercial-finalize-delete":
                storage_absence = prior_report(
                    storage_absence_report_path,
                    report_phase="account-media-absence",
                    label="signed storage absence report",
                    report_schema="vowpic.acceptance-media-absence-proof.v1",
                )
                payload, facts = collect_commercial_finalize(
                    cursor,
                    browser=browser_unsigned,
                    commercial_report=prior_report(
                        commercial_report_path,
                        report_phase="commercial-before-delete",
                        label="signed commercial acceptance report",
                    ),
                    storage_absence_report=storage_absence,
                )
            elif phase == "quality":
                if auth_unsigned is None:
                    raise ValueError("quality collection requires the signed auth report")
                if (
                    quality_review_request_path is None
                    or
                    quality_review_path is None
                    or quality_review_key is None
                    or quality_cases_path is None
                    or quality_rubric_path is None
                ):
                    raise ValueError(
                        "quality collection requires request, review, cases, and rubric"
                    )
                review_request, _request_raw = read_private_json(
                    quality_review_request_path,
                    label="signed quality review request",
                )
                request_unsigned = verify_signed_report(
                    review_request,
                    key=key,
                    expected={
                        "schema": "vowpic.quality-human-review-request.v1",
                        "passed": True,
                        "source_sha": browser_unsigned["source_sha"],
                        "runtime_bundle_id": browser_unsigned["runtime_bundle_id"],
                        "deployment_id": browser_unsigned["deployment_id"],
                        "manifest_sha256": browser_unsigned["manifest_sha256"],
                        "user_subject_hmac_sha256": browser_unsigned[
                            "user_subject_hmac_sha256"
                        ],
                    },
                    label="signed quality review request",
                )
                _same_binding(
                    browser_unsigned,
                    request_unsigned,
                    label="quality request/browser binding",
                )
                try:
                    request_expiry = datetime.fromisoformat(
                        str(request_unsigned.get("expires_at") or "").replace(
                            "Z",
                            "+00:00",
                        )
                    )
                except ValueError as exc:
                    raise ValueError("quality review request expiry is invalid") from exc
                if (
                    request_expiry.tzinfo is None
                    or datetime.now(timezone.utc) > request_expiry.astimezone(timezone.utc)
                ):
                    raise ValueError("quality review request has expired")
                review, review_raw = read_private_json(
                    quality_review_path, label="signed human quality review"
                )
                review_unsigned = verify_signed_report(
                    review,
                    key=quality_review_key,
                    expected={
                        "schema": "vowpic.quality-human-review.v1",
                        "review_complete": True,
                        "source_sha": browser_unsigned["source_sha"],
                        "runtime_bundle_id": browser_unsigned["runtime_bundle_id"],
                        "deployment_id": browser_unsigned["deployment_id"],
                        "manifest_sha256": browser_unsigned["manifest_sha256"],
                        "user_subject_hmac_sha256": browser_unsigned[
                            "user_subject_hmac_sha256"
                        ],
                        "request_sha256": canonical_sha256(review_request),
                    },
                    label="signed human quality review",
                )
                _same_binding(
                    browser_unsigned,
                    review_unsigned,
                    label="quality review/browser binding",
                )
                if browser_unsigned.get("user_id") != auth_unsigned.get("links", {}).get(
                    "user_id"
                ):
                    raise ValueError("quality user does not match the signed auth chain")
                if (
                    request_unsigned.get("user_id") != browser_unsigned.get("user_id")
                    or review_unsigned.get("user_id") != browser_unsigned.get("user_id")
                ):
                    raise ValueError("quality request/review user is not exact")
                payload, facts = collect_quality(
                    cursor,
                    browser=browser_unsigned,
                    human_review=review_unsigned,
                    cases_contract=json.loads(
                        quality_cases_path.read_text(encoding="utf-8")
                    ),
                    rubric=json.loads(
                        quality_rubric_path.read_text(encoding="utf-8")
                    ),
                )
                facts["human_review_sha256"] = hashlib.sha256(review_raw).hexdigest()
            else:
                raise ValueError("acceptance collector phase is not implemented")
        connection.rollback()

    return (
        seal_collected_input(
            payload,
            phase=phase,
            browser_report_sha256=hashlib.sha256(browser_raw).hexdigest(),
            database_facts=facts,
            key=key,
        ),
        hashlib.sha256(browser_raw).hexdigest(),
        facts,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--browser-report", required=True)
    parser.add_argument("--auth-report")
    parser.add_argument("--commercial-report")
    parser.add_argument("--storage-absence-report")
    parser.add_argument("--quality-review-request")
    parser.add_argument("--quality-review")
    parser.add_argument(
        "--quality-review-signing-key-env",
        default="QUALITY_REVIEW_SIGNING_KEY",
    )
    parser.add_argument("--quality-cases")
    parser.add_argument("--quality-rubric")
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument(
        "--signing-key-env", default="ACCEPTANCE_EVIDENCE_SIGNING_KEY"
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        sealed, _browser_sha, _facts = collect(
            phase=args.phase,
            browser_report_path=Path(args.browser_report),
            auth_report_path=Path(args.auth_report) if args.auth_report else None,
            database_url=os.environ.get(args.database_url_env, ""),
            key=signing_key(args.signing_key_env),
            commercial_report_path=(
                Path(args.commercial_report) if args.commercial_report else None
            ),
            storage_absence_report_path=(
                Path(args.storage_absence_report)
                if args.storage_absence_report
                else None
            ),
            quality_review_request_path=(
                Path(args.quality_review_request)
                if args.quality_review_request
                else None
            ),
            quality_review_path=(
                Path(args.quality_review) if args.quality_review else None
            ),
            quality_review_key=(
                signing_key(args.quality_review_signing_key_env)
                if args.quality_review
                else None
            ),
            quality_cases_path=(
                Path(args.quality_cases) if args.quality_cases else None
            ),
            quality_rubric_path=(
                Path(args.quality_rubric) if args.quality_rubric else None
            ),
        )
        write_private_create_once(Path(args.output), sealed)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
