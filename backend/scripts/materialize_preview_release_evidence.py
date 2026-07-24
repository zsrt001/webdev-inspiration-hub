#!/usr/bin/env python3
"""Convert one signed Preview Commercial browser run plus read-only DB facts into gates."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release._acceptance_evidence import (  # noqa: E402
    canonical,
    read_private_json,
    signing_key,
    validate_release_binding,
    verify_signed_report,
)


CASE_IDS = (
    "preview_creem_test_contract",
    "preview_main_journey",
    "preview_account_export",
    "preview_account_delete",
    "preview_partner_invite",
    "preview_accessibility_visual",
)
REQUIRED_OBSERVATIONS = {
    "account_delete": "preview_account_delete",
    "account_export": "preview_account_export",
    "a11y_zero_serious_or_critical": "preview_accessibility_visual",
    "creem_checkout_paid": "preview_creem_test_contract",
    "creem_refund_webhook_applied": "preview_creem_test_contract",
    "generation_ready": "preview_main_journey",
    "partner_invite_completed": "preview_partner_invite",
    "private_download": "preview_main_journey",
    "subscription_active": "preview_creem_test_contract",
    "subscription_scheduled_cancel": "preview_creem_test_contract",
}
LINK_FIELDS = (
    "primary_user_id",
    "partner_user_id",
    "purchase_id",
    "subscription_id",
    "main_order_id",
    "partner_invite_id",
    "partner_order_id",
)


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://",
        "postgresql://",
        1,
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("Preview read-only database URL is invalid")
    return clean


def _uuid(value: object, label: str) -> str:
    clean = str(value or "").strip().lower()
    try:
        parsed = UUID(clean)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if str(parsed) != clean:
        raise ValueError(f"{label} is invalid")
    return clean


def _load_contract(path: Path) -> tuple[dict[str, Any], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "vowpic.gates.v1":
        raise ValueError("gate contract is invalid")
    known = {item.get("id") for item in payload.get("cases", []) if isinstance(item, dict)}
    if not set(CASE_IDS).issubset(known):
        raise ValueError("gate contract omits Preview Commercial cases")
    return payload, hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_database_facts(
    database_url: str,
    links: dict[str, str],
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT p.user_id, p.status, p.captured_minor_units,
                       p.refunded_minor_units,
                       EXISTS (
                         SELECT 1 FROM payment_capture_facts c
                         WHERE c.purchase_id = p.id
                       ) AS has_capture,
                       EXISTS (
                         SELECT 1
                         FROM payment_refund_facts r
                         JOIN payment_events e ON e.id = r.payment_event_id
                         WHERE r.purchase_id = p.id
                           AND e.event_type = 'refund.created'
                           AND e.processing_state = 'APPLIED'
                       ) AS has_signed_refund
                FROM credit_purchases p WHERE p.id = %s
                """,
                (links["purchase_id"],),
            )
            purchase = cursor.fetchone()
            cursor.execute(
                """
                SELECT s.user_id, s.normalized_status, s.cancel_at_period_end,
                       s.provider_subscription_id,
                       EXISTS (
                         SELECT 1 FROM subscription_cancel_intents c
                         WHERE c.subscription_id = s.id AND c.state = 'CONFIRMED'
                       ) AS has_confirmed_cancel
                FROM user_subscriptions s WHERE s.id = %s
                """,
                (links["subscription_id"],),
            )
            subscription = cursor.fetchone()
            cursor.execute(
                """
                SELECT user_id, status,
                       generation_job_id IS NOT NULL AS has_generation_job,
                       COALESCE(jsonb_array_length(final_asset_ids), 0) > 0
                         AS has_final_asset
                FROM orders WHERE id = %s
                """,
                (links["main_order_id"],),
            )
            main_order = cursor.fetchone()
            cursor.execute(
                """
                SELECT host_user_id, partner_user_id, status, order_id
                FROM partner_invites WHERE id = %s
                """,
                (links["partner_invite_id"],),
            )
            partner_invite = cursor.fetchone()
            cursor.execute(
                """
                SELECT user_id, status
                FROM orders WHERE id = %s
                """,
                (links["partner_order_id"],),
            )
            partner_order = cursor.fetchone()
            cursor.execute(
                """
                SELECT user_id, media_cleanup_pending
                FROM account_tombstones
                WHERE user_id = ANY(%s::uuid[])
                """,
                ([links["primary_user_id"], links["partner_user_id"]],),
            )
            tombstones = list(cursor.fetchall())
        connection.rollback()

    if (
        purchase is None
        or str(purchase["user_id"]) != links["primary_user_id"]
        or str(purchase["status"]).lower() != "refunded"
        or int(purchase["captured_minor_units"] or 0) <= 0
        or int(purchase["refunded_minor_units"] or 0) <= 0
        or purchase["has_capture"] is not True
        or purchase["has_signed_refund"] is not True
    ):
        raise ValueError("Preview Creem capture/refund database facts are incomplete")
    if (
        subscription is None
        or str(subscription["user_id"]) != links["primary_user_id"]
        or str(subscription["normalized_status"]) not in {
            "CANCEL_REQUESTED",
            "CANCELED",
        }
        or subscription["cancel_at_period_end"] is not True
        or subscription["has_confirmed_cancel"] is not True
        or not str(subscription["provider_subscription_id"] or "").strip()
    ):
        raise ValueError("Preview subscription cancellation facts are incomplete")
    if (
        main_order is None
        or str(main_order["user_id"]) != links["primary_user_id"]
        or str(main_order["status"]) not in {"READY", "DELETED"}
        or main_order["has_generation_job"] is not True
        or main_order["has_final_asset"] is not True
    ):
        raise ValueError("Preview main generation facts are incomplete")
    if (
        partner_invite is None
        or str(partner_invite["host_user_id"]) != links["primary_user_id"]
        or str(partner_invite["partner_user_id"]) != links["partner_user_id"]
        or str(partner_invite["status"]) != "COMPLETED"
        or str(partner_invite["order_id"]) != links["partner_order_id"]
        or partner_order is None
        or str(partner_order["user_id"]) != links["primary_user_id"]
        or str(partner_order["status"]) not in {"READY", "DELETED"}
    ):
        raise ValueError("Preview partner journey facts are incomplete")
    if {str(row["user_id"]) for row in tombstones} != {
        links["primary_user_id"],
        links["partner_user_id"],
    }:
        raise ValueError("both Preview Commercial identities must be closed")

    return {
        "purchase_refunded": True,
        "signed_refund_event_applied": True,
        "subscription_cancel_confirmed": True,
        "main_generation_terminal": True,
        "partner_generation_terminal": True,
        "closed_identity_count": 2,
    }


def materialize(
    *,
    browser_report_path: Path,
    database_url: str,
    key: bytes,
    activation_id: str,
    source_sha: str,
    runtime_bundle_id: str,
    deployment_id: str,
    manifest_sha256: str,
    workflow_run_id: str,
    workflow_attempt: int,
    gate_contract_path: Path,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    normalized_activation_id = _uuid(activation_id, "activation_id")
    normalized_run_id = str(workflow_run_id or "").strip()
    if (
        not re.fullmatch(r"[1-9][0-9]{0,19}", normalized_run_id)
        or type(workflow_attempt) is not int
        or workflow_attempt < 1
    ):
        raise ValueError("Preview Commercial workflow coordinates are invalid")
    report, raw = read_private_json(
        browser_report_path,
        label="Preview Commercial browser report",
    )
    validate_release_binding(report)
    unsigned = verify_signed_report(
        report,
        key=key,
        expected={
            "schema": "vowpic.acceptance-browser-observation.v1",
            "phase": "preview-commercial-complete",
            "passed": True,
        },
        label="Preview Commercial browser report",
    )
    expected_binding = {
        "source_sha": source_sha,
        "runtime_bundle_id": runtime_bundle_id,
        "deployment_id": deployment_id,
        "manifest_sha256": manifest_sha256,
    }
    for name, value in expected_binding.items():
        if unsigned.get(name) != value:
            raise ValueError(f"Preview Commercial browser {name} mismatch")
    observations = unsigned.get("observations")
    if not isinstance(observations, dict):
        raise ValueError("Preview Commercial observations are absent")
    missing = sorted(
        name for name in REQUIRED_OBSERVATIONS if observations.get(name) is not True
    )
    if missing:
        raise ValueError(
            "Preview Commercial observations failed: " + ", ".join(missing)
        )
    raw_links = unsigned.get("links")
    if not isinstance(raw_links, dict) or set(raw_links) != set(LINK_FIELDS):
        raise ValueError("Preview Commercial links are incomplete or unexpected")
    links = {name: _uuid(raw_links[name], name) for name in LINK_FIELDS}
    facts = _collect_database_facts(database_url, links)
    _contract, gate_hash = _load_contract(gate_contract_path)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Preview evidence timestamp must be timezone-aware")
    proof_hash = hashlib.sha256(
        (
            hashlib.sha256(raw).hexdigest()
            + canonical(facts)
            + canonical(observations)
        ).encode("utf-8")
    ).hexdigest()
    return [
        {
            "schema": "vowpic.gate-evidence.v1",
            "case_id": case_id,
            "status": "PASS",
            "source_sha": source_sha,
            "runtime_bundle_id": runtime_bundle_id,
            "activation_id": normalized_activation_id,
            "api_deployment_id": deployment_id,
            "manifest_sha256": manifest_sha256,
            "workflow_run_id": normalized_run_id,
            "workflow_attempt": workflow_attempt,
            "gate_contract_sha256": gate_hash,
            "produced_at": current.astimezone(timezone.utc).isoformat(),
            "execution": "completed",
            "test_count": sum(
                1
                for observation, owner in REQUIRED_OBSERVATIONS.items()
                if owner == case_id and observations[observation] is True
            ),
            "capability_enabled": True,
            "proof_sha256": proof_hash,
        }
        for case_id in CASE_IDS
    ]


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-report", required=True)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument(
        "--signing-key-env",
        default="ACCEPTANCE_EVIDENCE_SIGNING_KEY",
    )
    parser.add_argument("--activation-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--runtime-bundle-id", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True, type=int)
    parser.add_argument("--gate-contract", default="release/gates.json")
    parser.add_argument("--output-base", required=True)
    args = parser.parse_args()
    try:
        rows = materialize(
            browser_report_path=Path(args.browser_report),
            database_url=os.environ.get(args.database_url_env, ""),
            key=signing_key(args.signing_key_env),
            activation_id=args.activation_id.strip().lower(),
            source_sha=args.source_sha.strip().lower(),
            runtime_bundle_id=args.runtime_bundle_id.strip().lower(),
            deployment_id=args.deployment_id.strip(),
            manifest_sha256=args.manifest_sha256.strip().lower(),
            workflow_run_id=args.workflow_run_id.strip(),
            workflow_attempt=args.workflow_attempt,
            gate_contract_path=Path(args.gate_contract),
        )
        output = Path(args.output_base)
        for row in rows:
            _write(output / f'{row["case_id"]}.json', row)
        print(
            json.dumps(
                {"state": "MATERIALIZED", "case_ids": list(CASE_IDS)},
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
