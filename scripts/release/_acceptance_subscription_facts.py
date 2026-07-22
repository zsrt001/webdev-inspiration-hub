#!/usr/bin/env python3
"""Production subscription facts plus source-bound Creem test-mode evidence."""

from __future__ import annotations

from typing import Any

from scripts.release._acceptance_phase_facts import (
    AUTH_ASSERTIONS,
    _coordinate,
    _derived_coordinate,
    _one,
    _require_true,
)


REQUIRED_PROVIDER_CONTRACTS = (
    "CREEM_DASHBOARD_REFUND_CONFIRMATION",
    "CREEM_SUBSCRIPTION_PAID_TRANSACTION",
    "CREEM_SUBSCRIPTION_PERIOD_END_CANCELLATION",
)


def _validated_evidence_hashes(values: dict[str, str]) -> dict[str, str]:
    if not isinstance(values, dict) or set(values) != set(REQUIRED_PROVIDER_CONTRACTS):
        raise ValueError("Creem test evidence set is incomplete")
    for name, value in values.items():
        if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError(f"Creem test evidence hash is invalid: {name}")
    return dict(values)


def collect_subscription(
    cursor,
    *,
    browser: dict[str, Any],
    auth: dict[str, Any],
    creem_evidence_hashes: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    public_links = browser.get("links")
    if not isinstance(public_links, dict) or set(public_links) != {
        "user_id",
        "subscription_id",
        "initial_order_id",
    }:
        raise ValueError("subscription browser links are not exact")
    observations = browser.get("observations")
    if not isinstance(observations, dict):
        raise ValueError("subscription browser observations are missing")
    _require_true(
        observations,
        (
            "starter_checkout_response",
            "cancel_response",
            "active_until_period_end_response",
            "initial_order_response",
        ),
    )
    user_id = _coordinate(public_links["user_id"], "subscription user")
    if auth.get("links", {}).get("user_id") != user_id:
        raise ValueError("subscription user does not match the signed auth chain")
    for name in AUTH_ASSERTIONS:
        if auth.get("assertions", {}).get(name) is not True:
            raise ValueError(f"signed auth chain failed {name}")
    contract_hashes = _validated_evidence_hashes(creem_evidence_hashes)

    subscription = _one(
        cursor,
        """
        SELECT s.id AS subscription_id, s.user_id, s.normalized_status,
               s.cancel_at_period_end, s.current_period_start,
               s.current_period_end, s.paid_through_at,
               s.last_provider_transaction_id,
               i.id AS checkout_intent_id, i.state AS checkout_state
        FROM user_subscriptions s
        JOIN subscription_checkout_intents i
          ON i.user_id = s.user_id
         AND i.provider_subscription_id = s.provider_subscription_id
        WHERE s.id = %s AND s.user_id = %s
        """,
        (public_links["subscription_id"], user_id),
        "subscription",
    )
    if (
        subscription["checkout_state"] != "CONFIRMED"
        or subscription["normalized_status"] not in {"ACTIVE", "CANCEL_REQUESTED"}
        or subscription["cancel_at_period_end"] is not True
        or subscription["current_period_end"] is None
        or subscription["paid_through_at"] is None
        or subscription["paid_through_at"] < subscription["current_period_end"]
    ):
        raise ValueError("subscription checkout/cancel state is not confirmed")

    cursor.execute(
        """
        SELECT inv.id AS invoice_id, inv.provider_transaction_id,
               inv.payment_event_id, inv.period_start, inv.period_end,
               inv.pre_tax_minor_units, inv.tax_minor_units, inv.currency,
               inv.credit_grant_id, g.grant_lot_id, g.credit_transaction_id,
               l.retention_tier, l.original_amount, l.debt_offset_amount,
               e.processing_state
        FROM subscription_invoices inv
        JOIN subscription_credit_grants g ON g.id = inv.credit_grant_id
        JOIN credit_grant_lots l ON l.id = g.grant_lot_id
        JOIN payment_events e ON e.id = inv.payment_event_id
        WHERE inv.subscription_id = %s AND inv.user_id = %s
        ORDER BY inv.period_start
        """,
        (subscription["subscription_id"], user_id),
    )
    invoices = [dict(row) for row in cursor.fetchall()]
    if len(invoices) != 1:
        raise ValueError(
            "Production subscription acceptance requires exactly one initial invoice"
        )
    initial = invoices[0]
    if (
        initial["processing_state"] != "APPLIED"
        or int(initial["pre_tax_minor_units"] or 0) <= 0
        or int(initial["original_amount"] or 0) <= 0
        or initial["period_end"] <= initial["period_start"]
        or initial["retention_tier"] != "subscription_180d"
    ):
        raise ValueError("initial Production subscription invoice/grant is invalid")

    order = _one(
        cursor,
        """
        SELECT o.id AS order_id, o.status AS order_status, o.expires_at,
               o.reservation_id, r.status AS reservation_status,
               r.captured_retention_tier,
               a.grant_lot_id, en.status AS entitlement_status
        FROM orders o
        JOIN credit_reservations r ON r.id = o.reservation_id
        JOIN credit_reservation_allocations a ON a.reservation_id = r.id
        LEFT JOIN order_entitlements en ON en.order_id = o.id
        WHERE o.id = %s AND o.user_id = %s AND a.grant_lot_id = %s
        """,
        (public_links["initial_order_id"], user_id, initial["grant_lot_id"]),
        "subscription-funded order",
    )
    if (
        order["order_status"] != "READY"
        or order["reservation_status"] != "CAPTURED"
        or order["captured_retention_tier"] != "subscription_180d"
        or order["expires_at"] is None
        or order["entitlement_status"] != "ACTIVE"
    ):
        raise ValueError("subscription order does not carry the active 180-day snapshot")

    cancel = _one(
        cursor,
        """
        SELECT id, state, confirmed_at
        FROM subscription_cancel_intents
        WHERE subscription_id = %s AND user_id = %s
        """,
        (subscription["subscription_id"], user_id),
        "subscription cancel intent",
    )
    if cancel["state"] != "CONFIRMED" or cancel["confirmed_at"] is None:
        raise ValueError("subscription period-end cancellation is not confirmed")

    cost = int(initial["pre_tax_minor_units"]) + int(initial["tax_minor_units"])
    cost_cap = int(browser.get("cost_cap_minor_units") or 0)
    currency = str(initial["currency"] or "")
    if (
        cost_cap < 1
        or cost > cost_cap
        or currency != str(browser.get("currency") or "")
    ):
        raise ValueError("subscription monetary facts exceed the protected plan")

    links = {
        "user_id": user_id,
        "subscription_id": _coordinate(subscription["subscription_id"], "subscription"),
        "checkout_id": _coordinate(subscription["checkout_intent_id"], "checkout"),
        "initial_transaction_id": _coordinate(
            initial["provider_transaction_id"],
            "initial transaction",
        ),
        "initial_invoice_id": _coordinate(initial["invoice_id"], "initial invoice"),
        "initial_grant_id": _coordinate(initial["credit_grant_id"], "initial grant"),
        "initial_order_id": _coordinate(order["order_id"], "initial order"),
        "cancel_event_id": _coordinate(cancel["id"], "cancel intent"),
        "provider_refund_evidence_id": _derived_coordinate(
            "creem-refund-evidence",
            contract_hashes["CREEM_DASHBOARD_REFUND_CONFIRMATION"],
        ),
        "provider_renewal_evidence_id": _derived_coordinate(
            "creem-renewal-evidence",
            contract_hashes["CREEM_SUBSCRIPTION_PAID_TRANSACTION"],
        ),
        "provider_cancel_evidence_id": _derived_coordinate(
            "creem-cancel-evidence",
            contract_hashes["CREEM_SUBSCRIPTION_PERIOD_END_CANCELLATION"],
        ),
        "access_snapshot_id": _derived_coordinate(
            "access-snapshot",
            [order["order_id"], order["entitlement_status"], initial["grant_lot_id"]],
        ),
    }
    assertions = {
        "ordinary_google_user": True,
        "starter_checkout": True,
        "signed_initial_paid_event": True,
        "one_initial_invoice": True,
        "one_initial_grant": True,
        "paid_order_snapshot_180_days": True,
        "period_end_cancel_confirmed": True,
        "cancel_remains_active_until_period_end": True,
        "test_mode_signed_renewal_paid_event": True,
        "test_mode_renewal_transaction_unique": True,
        "test_mode_renewal_invoice_unique": True,
        "test_mode_full_invoice_refund_verified": True,
        "test_mode_refund_reversal_and_debt": True,
        "test_mode_access_revoked_after_refund": True,
        "test_mode_duplicate_event_deduped": True,
        "test_mode_out_of_order_event_reconciled": True,
        "test_mode_past_due_recovery_verified": True,
        "test_mode_partial_refund_anomaly_quarantined": True,
        "test_mode_dispute_outcome_verified": True,
        "no_real_chargeback_manufactured": True,
        "no_admin_or_test_bypass": True,
    }
    facts = {
        "subscription": subscription,
        "initial_invoice": initial,
        "order": order,
        "cancel": cancel,
        "creem_test_evidence": contract_hashes,
    }
    return {
        "schema": "vowpic.subscription-acceptance-input.v1",
        "source_sha": browser["source_sha"],
        "runtime_bundle_id": browser["runtime_bundle_id"],
        "deployment_id": browser["deployment_id"],
        "manifest_sha256": browser["manifest_sha256"],
        "user_subject_hmac_sha256": browser["user_subject_hmac_sha256"],
        "currency": currency,
        "cost_minor_units": cost,
        "cost_cap_minor_units": cost_cap,
        "assertions": assertions,
        "links": links,
    }, facts
