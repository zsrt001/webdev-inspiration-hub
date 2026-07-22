#!/usr/bin/env python3
"""Identity-bound final account-deletion acceptance facts."""

from __future__ import annotations

from typing import Any

from scripts.release._acceptance_phase_facts import (
    _coordinate,
    _derived_coordinate,
    _one,
    _require_true,
)


def collect_commercial_finalize(
    cursor,
    *,
    browser: dict[str, Any],
    identity_report: dict[str, Any],
    storage_absence_report: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    observations = browser.get("observations")
    if not isinstance(observations, dict):
        raise ValueError("account finalization browser observations are missing")
    _require_true(
        observations,
        (
            "account_close_response",
            "post_close_session_denied",
        ),
    )
    links = browser.get("links")
    if not isinstance(links, dict) or set(links) != {"user_id"}:
        raise ValueError("account finalization browser links are not exact")
    user_id = _coordinate(links["user_id"], "closed user")
    if (
        identity_report.get("links", {}).get("user_id") != user_id
        or storage_absence_report.get("passed") is not True
        or storage_absence_report.get("user_subject_hmac_sha256")
        != browser.get("user_subject_hmac_sha256")
        or storage_absence_report.get("storage_read_outcome") != "NOT_FOUND"
        or int(storage_absence_report.get("verified_asset_count") or 0) < 1
    ):
        raise ValueError("account finalization prior chain binding mismatch")
    closed = _one(
        cursor,
        """
        SELECT u.id AS user_id, u.status, t.closed_at,
               t.media_cleanup_pending, t.audit_request_id,
               (SELECT count(*) FROM auth_sessions s
                WHERE s.user_id = u.id AND s.revoked_at IS NULL) AS active_sessions,
               (SELECT count(*) FROM media_assets m
                WHERE m.owner_user_id = u.id
                  AND (m.status <> 'DELETED' OR m.read_revoked_at IS NULL)
               ) AS accessible_assets,
               (SELECT count(*) FROM acceptance_identity_bindings b
                WHERE b.consumed_user_id = u.id
                  AND b.deployment_id = %s AND b.revoked_at IS NULL
               ) AS active_acceptance_bindings
        FROM users u
        JOIN account_tombstones t ON t.user_id = u.id
        WHERE u.id = %s
        """,
        (browser["deployment_id"], user_id),
        "closed account",
    )
    if (
        closed["status"] != "closed"
        or closed["media_cleanup_pending"] is not False
        or int(closed["active_sessions"]) != 0
        or int(closed["accessible_assets"]) != 0
        or int(closed["active_acceptance_bindings"]) != 0
    ):
        raise ValueError("closed account still has session/media/binding residue")
    account_close_id = _derived_coordinate(
        "account-close",
        [user_id, closed["closed_at"], closed["audit_request_id"]],
    )
    deletion_batch_id = _derived_coordinate(
        "deletion-batch",
        [user_id, closed["closed_at"], closed["accessible_assets"]],
    )
    cost_cap = int(browser.get("cost_cap_minor_units") or 0)
    payload = {
        "schema": "vowpic.commercial-acceptance-input.v1",
        "phase": "commercial-finalize-delete",
        "source_sha": browser["source_sha"],
        "runtime_bundle_id": browser["runtime_bundle_id"],
        "deployment_id": browser["deployment_id"],
        "manifest_sha256": browser["manifest_sha256"],
        "user_subject_hmac_sha256": browser["user_subject_hmac_sha256"],
        "currency": str(browser.get("currency") or ""),
        "cost_minor_units": 0,
        "cost_cap_minor_units": cost_cap,
        "assertions": {
            "prior_identity_chain_passed": True,
            "account_closed": True,
            "sessions_revoked": True,
            "private_objects_deleted": True,
            "private_store_read_after_delete_not_found": True,
            "no_acceptance_binding_residue": True,
            "no_admin_or_test_bypass": True,
        },
        "links": {
            "user_id": user_id,
            "account_close_id": account_close_id,
            "deletion_batch_id": deletion_batch_id,
        },
    }
    return payload, {
        "closed": closed,
        "identity_report_sha256": _derived_coordinate(
            "identity-report", identity_report
        ),
        "storage_absence_report_sha256": _derived_coordinate(
            "storage-absence-report", storage_absence_report
        ),
    }
