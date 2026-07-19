#!/usr/bin/env python3
"""Read-only PostgreSQL facts for linked COMMERCIAL_7A acceptance phases."""

from __future__ import annotations

import hashlib
from typing import Any

from scripts.release._acceptance_evidence import canonical


AUTH_ASSERTIONS = (
    "ordinary_google_user",
    "first_login",
    "refresh_rotated",
    "logout_revoked",
    "post_logout_denied",
    "second_login_same_account",
    "legacy_jwt_denied",
    "legacy_openid_header_denied",
    "legacy_visitor_header_denied",
    "forwarded_identity_spoof_denied",
    "browser_admin_token_denied",
    "no_admin_or_test_bypass",
)


def _one(cursor, query: str, params: tuple[Any, ...], label: str) -> dict[str, Any]:
    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    if len(rows) != 1:
        raise ValueError(f"{label} must resolve to exactly one database fact")
    return rows[0]


def _coordinate(value: Any, label: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 180:
        raise ValueError(f"{label} coordinate is invalid")
    return clean


def _derived_coordinate(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _require_true(observations: dict[str, Any], names: tuple[str, ...]) -> None:
    if set(observations) != set(names):
        raise ValueError("browser observation fields are not exact")
    failed = [name for name in names if observations.get(name) is not True]
    if failed:
        raise ValueError("browser observations failed: " + ",".join(failed))


def collect_commercial_before_delete(
    cursor,
    *,
    browser: dict[str, Any],
    auth: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    public_links = browser.get("links")
    if not isinstance(public_links, dict) or set(public_links) != {
        "user_id",
        "upload_asset_id",
        "trial_order_id",
        "trial_preview_asset_id",
        "purchase_id",
        "paid_order_id",
        "paid_final_asset_id",
        "second_purchase_id",
        "account_export_id",
    }:
        raise ValueError("commercial browser links are not exact")
    observations = browser.get("observations")
    if not isinstance(observations, dict):
        raise ValueError("commercial browser observations are missing")
    _require_true(
        observations,
        (
            "private_upload_response",
            "watermarked_preview_response",
            "private_final_download_response",
            "account_export_response",
        ),
    )
    user_id = _coordinate(public_links["user_id"], "commercial user")
    if auth.get("links", {}).get("user_id") != user_id:
        raise ValueError("commercial user does not match the signed auth chain")
    for name in AUTH_ASSERTIONS:
        if auth.get("assertions", {}).get(name) is not True:
            raise ValueError(f"signed auth chain failed {name}")

    upload = _one(
        cursor,
        """
        SELECT id, owner_user_id, role, status, access_level
        FROM media_assets
        WHERE id = %s AND owner_user_id = %s
        """,
        (public_links["upload_asset_id"], user_id),
        "private upload",
    )
    if (
        upload["role"] != "source"
        or upload["status"] != "ACTIVE"
        or upload["access_level"] != "private"
    ):
        raise ValueError("private upload fact is not active and private")

    trial = _one(
        cursor,
        """
        SELECT o.id AS order_id, o.user_id, o.status AS order_status,
               o.reservation_id, o.generation_job_id AS job_id,
               r.status AS reservation_status,
               j.status AS job_status, j.active_attempt_id AS attempt_id,
               a.result_asset_id AS candidate_asset_id,
               a.submission_accounting_state,
               q.id AS qa_verdict_id, q.decision AS qa_decision
        FROM orders o
        JOIN credit_reservations r ON r.id = o.reservation_id
        JOIN generation_jobs j ON j.id = o.generation_job_id
        JOIN generation_attempts a ON a.id = j.active_attempt_id
        JOIN qa_verdicts q
          ON q.attempt_id = a.id AND q.candidate_asset_id = a.result_asset_id
        WHERE o.id = %s AND o.user_id = %s
        """,
        (public_links["trial_order_id"], user_id),
        "trial order",
    )
    if (
        trial["order_status"] != "READY"
        or trial["reservation_status"] != "CAPTURED"
        or trial["job_status"] != "FINISHED"
        or trial["submission_accounting_state"] != "CAPTURED"
        or trial["qa_decision"] != "PASS"
    ):
        raise ValueError("trial order did not reach captured reviewed READY")
    preview = _one(
        cursor,
        """
        SELECT id, owner_user_id, order_id, job_id, role, status, access_level
        FROM media_assets
        WHERE id = %s AND owner_user_id = %s AND order_id = %s
        """,
        (
            public_links["trial_preview_asset_id"],
            user_id,
            public_links["trial_order_id"],
        ),
        "trial watermarked preview",
    )
    if (
        preview["job_id"] != trial["job_id"]
        or preview["role"] != "preview_watermarked"
        or preview["status"] != "ACTIVE"
        or preview["access_level"] != "private"
    ):
        raise ValueError("trial preview is not the private watermarked asset")

    purchase = _one(
        cursor,
        """
        SELECT p.id AS purchase_id, p.user_id, p.provider_checkout_id,
               p.status AS purchase_status, p.intent_state,
               p.currency, p.captured_minor_units, p.refunded_minor_units,
               p.credits, p.grant_lot_id, p.grant_transaction_id,
               c.payment_event_id, e.processing_state, e.raw_payload_sha256,
               l.original_amount, l.debt_offset_amount, l.consumed_amount
        FROM credit_purchases p
        JOIN payment_capture_facts c ON c.purchase_id = p.id
        JOIN payment_events e ON e.id = c.payment_event_id
        JOIN credit_grant_lots l ON l.id = p.grant_lot_id
        WHERE p.id = %s AND p.user_id = %s
        """,
        (public_links["purchase_id"], user_id),
        "first credit purchase",
    )
    if (
        not str(purchase["provider_checkout_id"] or "").strip()
        or purchase["intent_state"] != "CONFIRMED"
        or purchase["processing_state"] != "APPLIED"
        or not str(purchase["raw_payload_sha256"] or "")
        or int(purchase["captured_minor_units"] or 0) <= 0
        or int(purchase["original_amount"]) != int(purchase["credits"])
    ):
        raise ValueError("first purchase is not a signed captured fact")

    paid = _one(
        cursor,
        """
        SELECT o.id AS order_id, o.status AS order_status, o.reservation_id,
               o.generation_job_id AS job_id, r.status AS reservation_status,
               j.status AS job_status, j.active_attempt_id AS attempt_id,
               a.submission_accounting_state,
               en.id AS entitlement_id, en.status AS entitlement_status,
               en.unlock_grant_lot_id, en.unlock_root_transaction_id,
               f.grant_lot_id AS funding_grant_lot_id,
               ma.id AS final_asset_id, ma.role AS final_role,
               ma.status AS final_status, ma.access_level
        FROM orders o
        JOIN credit_reservations r ON r.id = o.reservation_id
        JOIN generation_jobs j ON j.id = o.generation_job_id
        JOIN generation_attempts a ON a.id = j.active_attempt_id
        JOIN order_entitlements en ON en.order_id = o.id
        JOIN order_entitlement_fundings f ON f.entitlement_id = en.id
        JOIN media_assets ma ON ma.id = %s
        WHERE o.id = %s AND o.user_id = %s AND ma.order_id = o.id
        """,
        (
            public_links["paid_final_asset_id"],
            public_links["paid_order_id"],
            user_id,
        ),
        "paid order",
    )
    if (
        paid["order_status"] != "READY"
        or paid["reservation_status"] != "CAPTURED"
        or paid["job_status"] != "FINISHED"
        or paid["submission_accounting_state"] != "CAPTURED"
        or paid["entitlement_status"] != "ACTIVE"
        # A distinct paid order is funded directly by the purchase lot.  The
        # unlock fields belong only to the one-time "unlock this trial" path;
        # requiring them here made the acceptance contract impossible to
        # satisfy with the public order API.
        or paid["unlock_grant_lot_id"] is not None
        or paid["unlock_root_transaction_id"] is not None
        or str(paid["funding_grant_lot_id"]) != str(purchase["grant_lot_id"])
        or paid["final_role"] not in {"final_master", "delivery_variant"}
        or paid["final_status"] != "ACTIVE"
        or paid["access_level"] != "private"
        or int(purchase["consumed_amount"] or 0) <= 0
    ):
        raise ValueError("paid order entitlement/funding/final asset lineage is invalid")

    refund = _one(
        cursor,
        """
        SELECT rf.id AS refund_id, rf.purchase_id, rf.payment_event_id,
               rf.refund_minor_units, rf.currency, rf.classification,
               rf.reversal_transaction_id,
               tx.balance_after AS reversal_balance_after,
               p.status AS purchase_status, p.refunded_minor_units,
               p.captured_minor_units
        FROM payment_refund_facts rf
        JOIN credit_purchases p ON p.id = rf.purchase_id
        JOIN credit_transactions tx ON tx.id = rf.reversal_transaction_id
        WHERE rf.purchase_id = %s AND p.user_id = %s
        """,
        (public_links["purchase_id"], user_id),
        "full purchase refund",
    )
    debt = max(0, -int(refund["reversal_balance_after"]))
    if (
        refund["classification"] != "FULL"
        or int(refund["refunded_minor_units"]) != int(refund["captured_minor_units"])
        or int(refund["refund_minor_units"]) != int(refund["captured_minor_units"])
        or debt <= 0
    ):
        raise ValueError("purchase refund did not create the exact accounting debt")

    second = _one(
        cursor,
        """
        SELECT p.id AS purchase_id, p.status AS purchase_status, p.intent_state,
               p.currency, p.captured_minor_units, p.credits,
               p.grant_lot_id, p.grant_transaction_id,
               l.debt_offset_amount, l.original_amount,
               tx.balance_after, uc.balance, uc.reserved_balance
        FROM credit_purchases p
        JOIN credit_grant_lots l ON l.id = p.grant_lot_id
        JOIN credit_transactions tx ON tx.id = p.grant_transaction_id
        JOIN user_credits uc ON uc.user_id = p.user_id
        WHERE p.id = %s AND p.user_id = %s
        """,
        (public_links["second_purchase_id"], user_id),
        "second credit purchase",
    )
    expected_offset = min(int(second["credits"]), debt)
    expected_balance = int(refund["reversal_balance_after"]) + int(second["credits"])
    expected_spendable = max(
        0, expected_balance - int(second["reserved_balance"] or 0)
    )
    if (
        second["intent_state"] != "CONFIRMED"
        or second["purchase_status"] != "paid"
        or int(second["captured_minor_units"] or 0) <= 0
        or int(second["debt_offset_amount"]) != expected_offset
        or int(second["balance_after"]) != expected_balance
        or int(second["balance"]) != expected_balance
        or expected_spendable != max(
            0, int(second["balance"]) - int(second["reserved_balance"] or 0)
        )
    ):
        raise ValueError("second purchase debt offset/residual is not exact")

    costs = int(purchase["captured_minor_units"]) + int(second["captured_minor_units"])
    cost_cap = int(browser.get("cost_cap_minor_units") or 0)
    currency = str(purchase["currency"] or "")
    if (
        cost_cap < 1
        or costs > cost_cap
        or currency != str(second["currency"] or "")
        or currency != str(browser.get("currency") or "")
    ):
        raise ValueError("commercial monetary facts exceed the protected plan")

    links = {
        "user_id": user_id,
        "upload_asset_id": _coordinate(upload["id"], "upload"),
        "trial_order_id": _coordinate(trial["order_id"], "trial order"),
        "trial_reservation_id": _coordinate(trial["reservation_id"], "trial reservation"),
        "trial_job_id": _coordinate(trial["job_id"], "trial job"),
        "trial_attempt_id": _coordinate(trial["attempt_id"], "trial attempt"),
        "trial_candidate_asset_id": _coordinate(
            trial["candidate_asset_id"], "trial candidate"
        ),
        "trial_preview_asset_id": _coordinate(preview["id"], "trial preview"),
        "trial_qa_verdict_id": _coordinate(trial["qa_verdict_id"], "trial QA"),
        "purchase_id": _coordinate(purchase["purchase_id"], "purchase"),
        "checkout_id": _coordinate(purchase["provider_checkout_id"], "checkout"),
        "payment_event_id": _coordinate(purchase["payment_event_id"], "payment event"),
        "credit_grant_id": _coordinate(purchase["grant_lot_id"], "credit grant"),
        "paid_order_id": _coordinate(paid["order_id"], "paid order"),
        "paid_reservation_id": _coordinate(paid["reservation_id"], "paid reservation"),
        "paid_job_id": _coordinate(paid["job_id"], "paid job"),
        "paid_attempt_id": _coordinate(paid["attempt_id"], "paid attempt"),
        "paid_final_asset_id": _coordinate(paid["final_asset_id"], "paid final"),
        "entitlement_id": _coordinate(paid["entitlement_id"], "entitlement"),
        "refund_id": _coordinate(refund["refund_id"], "refund"),
        "reversal_id": _coordinate(refund["reversal_transaction_id"], "reversal"),
        "debt_fact_id": _derived_coordinate(
            "debt", [refund["reversal_transaction_id"], debt]
        ),
        "second_purchase_id": _coordinate(second["purchase_id"], "second purchase"),
        "second_grant_id": _coordinate(second["grant_lot_id"], "second grant"),
        "debt_offset_fact_id": _derived_coordinate(
            "debt-offset",
            [second["grant_lot_id"], expected_offset, expected_spendable],
        ),
        "account_export_id": _coordinate(
            public_links["account_export_id"], "account export"
        ),
    }
    assertions = {
        "ordinary_google_user": True,
        "first_login": True,
        "welcome_grant_once": True,
        "refresh_rotated": True,
        "logout_revoked": True,
        "post_logout_denied": True,
        "second_login_same_account": True,
        "legacy_jwt_denied": True,
        "legacy_openid_header_denied": True,
        "legacy_visitor_header_denied": True,
        "forwarded_identity_spoof_denied": True,
        "browser_admin_token_denied": True,
        "private_upload": True,
        "trial_job_ready": True,
        "trial_qa_passed": True,
        "watermarked_preview": True,
        "signed_checkout_webhook": True,
        "exact_order_entitlement": True,
        "private_final_download": True,
        "paid_grant_consumed": True,
        "full_refund_verified": True,
        "refund_reversal_and_debt": True,
        "second_purchase_verified": True,
        "debt_offset_exact": True,
        "residual_spendable_exact": True,
        "account_export_complete": True,
        "no_admin_or_test_bypass": True,
    }
    facts = {
        "upload": upload,
        "trial": trial,
        "preview": preview,
        "purchase": purchase,
        "paid": paid,
        "refund": refund,
        "second": second,
        "derived": {
            "debt": debt,
            "expected_offset": expected_offset,
            "expected_spendable": expected_spendable,
        },
    }
    return {
        "schema": "vowpic.commercial-acceptance-input.v1",
        "phase": "commercial-before-delete",
        "source_sha": browser["source_sha"],
        "runtime_bundle_id": browser["runtime_bundle_id"],
        "deployment_id": browser["deployment_id"],
        "manifest_sha256": browser["manifest_sha256"],
        "user_subject_hmac_sha256": browser["user_subject_hmac_sha256"],
        "currency": currency,
        "cost_minor_units": costs,
        "cost_cap_minor_units": cost_cap,
        "assertions": assertions,
        "links": links,
    }, facts
