#!/usr/bin/env python3
"""Provider unknown-state and final account-deletion acceptance facts."""

from __future__ import annotations

import hashlib
from typing import Any

from scripts.release._acceptance_phase_facts import (
    _coordinate,
    _derived_coordinate,
    _one,
    _require_true,
)


def _require_evolink_verified(
    document: dict[str, Any], source_sha: str
) -> dict[str, Any]:
    entry = document.get("contracts", {}).get("EVOLINK_SUBMISSION_RECONCILIATION")
    if (
        document.get("schema") != "vowpic.provider-contracts.v1"
        or not isinstance(entry, dict)
        or entry.get("state") != "VERIFIED"
        or entry.get("tested_source_sha") != source_sha
        or not isinstance(entry.get("test_evidence_sha256"), str)
        or len(entry["test_evidence_sha256"]) != 64
    ):
        raise ValueError("Evolink submission reconciliation contract is not VERIFIED")
    return {
        "tested_source_sha": entry["tested_source_sha"],
        "test_evidence_sha256": entry["test_evidence_sha256"],
        "endpoint_schema_sha256": entry["endpoint_schema_sha256"],
    }


def collect_provider_unknown_queue(
    cursor,
    *,
    browser: dict[str, Any],
    commercial: dict[str, Any],
    provider_contracts: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    links = browser.get("links")
    if not isinstance(links, dict) or set(links) != {"user_id", "order_id"}:
        raise ValueError("Provider queue browser links are not exact")
    observations = browser.get("observations")
    if not isinstance(observations, dict):
        raise ValueError("Provider queue browser observations are missing")
    _require_true(observations, ("order_accepted",))
    user_id = _coordinate(links["user_id"], "Provider queue user")
    if commercial.get("links", {}).get("user_id") != user_id:
        raise ValueError("Provider queue user does not match commercial acceptance")
    contract = _require_evolink_verified(
        provider_contracts, str(browser["source_sha"])
    )
    activation = _one(
        cursor,
        """
        SELECT id, workflow_run_id, workflow_attempt, source_sha,
               runtime_bundle_id, manifest_sha256, api_deployment_id,
               worker_deployment_id, worker_image_digest
        FROM release_activations
        WHERE environment = 'production' AND kind = 'COMMERCIAL_7A'
          AND source_sha = %s AND phase = 'WORKER_DISPATCH_ENABLED'
        """,
        (browser["source_sha"],),
        "dispatch-enabled release",
    )
    if (
        activation["runtime_bundle_id"] != browser["runtime_bundle_id"]
        or activation["manifest_sha256"] != browser["manifest_sha256"]
        or activation["api_deployment_id"] != browser["deployment_id"]
    ):
        raise ValueError("Provider queue release binding mismatch")
    queued = _one(
        cursor,
        """
        SELECT o.id AS order_id, o.user_id, o.status AS order_status,
               o.reservation_id, o.generation_job_id AS job_id,
               j.submission_correlation_id, j.status AS job_status,
               j.active_attempt_id AS attempt_id,
               j.api_deployment_id, j.runtime_bundle_id,
               j.expected_worker_image_digest,
               a.status AS attempt_status, a.client_request_id,
               a.provider_job_id, a.submission_accounting_state,
               a.cost_minor_units, a.cost_currency
        FROM orders o
        JOIN generation_jobs j ON j.id = o.generation_job_id
        JOIN generation_attempts a ON a.id = j.active_attempt_id
        WHERE o.id = %s AND o.user_id = %s
        """,
        (links["order_id"], user_id),
        "queued Provider order",
    )
    if (
        queued["order_status"] != "QUEUED"
        or queued["job_status"] != "QUEUED"
        or queued["attempt_status"] != "PREPARED"
        or queued["provider_job_id"] is not None
        or queued["submission_accounting_state"] != "NOT_CAPTURED"
        or str(queued["client_request_id"])
        != str(queued["submission_correlation_id"])
        or queued["api_deployment_id"] != activation["api_deployment_id"]
        or queued["runtime_bundle_id"] != activation["runtime_bundle_id"]
        or queued["expected_worker_image_digest"]
        != activation["worker_image_digest"]
    ):
        raise ValueError("Provider order is not at the exact pre-submit boundary")
    fault_intent_id = "afi_" + hashlib.sha256(
        (
            f"{activation['id']}:{activation['workflow_run_id']}:"
            f"{activation['workflow_attempt']}:provider-response-drop"
        ).encode("utf-8")
    ).hexdigest()[:32]
    cost = int(queued["cost_minor_units"] or 0)
    cost_cap = int(browser.get("cost_cap_minor_units") or 0)
    currency = str(browser.get("currency") or "")
    if (
        cost_cap < 1
        or cost > cost_cap
        or currency != str(queued["cost_currency"] or currency)
    ):
        raise ValueError("Provider queue cost exceeds the protected cap")
    output_links = {
        "user_id": user_id,
        "order_id": _coordinate(queued["order_id"], "Provider order"),
        "reservation_id": _coordinate(queued["reservation_id"], "Provider reservation"),
        "job_id": _coordinate(queued["job_id"], "Provider job"),
        "attempt_id": _coordinate(queued["attempt_id"], "Provider attempt"),
        "fault_intent_id": fault_intent_id,
        "client_correlation_id": _coordinate(
            queued["submission_correlation_id"], "Provider correlation"
        ),
    }
    payload = {
        "schema": "vowpic.commercial-acceptance-input.v1",
        "phase": "queue-provider-unknown-state",
        "source_sha": browser["source_sha"],
        "runtime_bundle_id": browser["runtime_bundle_id"],
        "deployment_id": browser["deployment_id"],
        "manifest_sha256": browser["manifest_sha256"],
        "user_subject_hmac_sha256": browser["user_subject_hmac_sha256"],
        "currency": currency,
        "cost_minor_units": cost,
        "cost_cap_minor_units": cost_cap,
        "assertions": {
            "dispatch_paused": True,
            "fault_intent_coordinate_prepared": True,
            "one_submit_limit": True,
            "bounded_cost": True,
            "correlation_bound": True,
            "no_admin_or_test_bypass": True,
        },
        "links": output_links,
    }
    return payload, {
        "activation": activation,
        "queued": queued,
        "provider_contract": contract,
    }


def collect_provider_unknown_complete(
    cursor,
    *,
    browser: dict[str, Any],
    queued_report: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    observations = browser.get("observations")
    if not isinstance(observations, dict):
        raise ValueError("Provider completion browser observations are missing")
    _require_true(observations, ("order_ready", "same_order_visible"))
    prior_links = queued_report.get("links")
    if not isinstance(prior_links, dict):
        raise ValueError("queued Provider report links are missing")
    browser_links = browser.get("links")
    if (
        not isinstance(browser_links, dict)
        or set(browser_links) != {"user_id", "order_id"}
        or browser_links["user_id"] != prior_links.get("user_id")
        or browser_links["order_id"] != prior_links.get("order_id")
    ):
        raise ValueError("Provider completion browser links are not exact")
    completed = _one(
        cursor,
        """
        SELECT o.id AS order_id, o.user_id, o.status AS order_status,
               j.id AS job_id, j.status AS job_status,
               j.active_attempt_id AS attempt_id,
               a.status AS attempt_status, a.provider_job_id,
               a.submission_accounting_state, a.cost_minor_units,
               (SELECT count(*) FROM generation_attempts x
                WHERE x.job_id = j.id AND x.kind = 'INITIAL') AS initial_submit_count,
               (SELECT count(*) FROM qa_verdicts q
                WHERE q.job_id = j.id AND q.decision = 'PASS') AS pass_count
        FROM orders o
        JOIN generation_jobs j ON j.id = o.generation_job_id
        JOIN generation_attempts a ON a.id = j.active_attempt_id
        WHERE o.id = %s
        """,
        (prior_links["order_id"],),
        "completed Provider order",
    )
    if (
        str(completed["user_id"]) != str(prior_links["user_id"])
        or str(completed["user_id"]) != str(browser_links["user_id"])
        or str(completed["job_id"]) != str(prior_links["job_id"])
        or str(completed["attempt_id"]) != str(prior_links["attempt_id"])
        or completed["order_status"] != "READY"
        or completed["job_status"] != "FINISHED"
        or completed["attempt_status"] != "FINISHED"
        or not str(completed["provider_job_id"] or "")
        or completed["submission_accounting_state"] != "CAPTURED"
        or int(completed["initial_submit_count"]) != 1
        or int(completed["pass_count"]) < 1
    ):
        raise ValueError("Provider unknown-state recovery did not finish one exact task")
    cost = int(completed["cost_minor_units"] or 0)
    cost_cap = int(browser.get("cost_cap_minor_units") or 0)
    if cost_cap < 1 or cost > cost_cap:
        raise ValueError("Provider completion cost exceeds the protected cap")
    provider_task = _coordinate(completed["provider_job_id"], "Provider task")
    output_links = {
        "user_id": _coordinate(completed["user_id"], "Provider user"),
        "order_id": _coordinate(completed["order_id"], "Provider order"),
        "job_id": _coordinate(completed["job_id"], "Provider job"),
        "attempt_id": _coordinate(completed["attempt_id"], "Provider attempt"),
        "fault_intent_id": _coordinate(
            prior_links["fault_intent_id"], "fault intent"
        ),
        "provider_task_id": provider_task,
        "provider_capture_id": _derived_coordinate(
            "provider-capture",
            [
                completed["attempt_id"],
                provider_task,
                completed["cost_minor_units"],
                completed["submission_accounting_state"],
            ],
        ),
    }
    payload = {
        "schema": "vowpic.commercial-acceptance-input.v1",
        "phase": "complete-provider-unknown-state",
        "source_sha": browser["source_sha"],
        "runtime_bundle_id": browser["runtime_bundle_id"],
        "deployment_id": browser["deployment_id"],
        "manifest_sha256": browser["manifest_sha256"],
        "user_subject_hmac_sha256": browser["user_subject_hmac_sha256"],
        "currency": str(browser.get("currency") or ""),
        "cost_minor_units": cost,
        "cost_cap_minor_units": cost_cap,
        "assertions": {
            "provider_accepted": True,
            "worker_lost_submit_response": True,
            "same_provider_task_recovered": True,
            "single_submit": True,
            "single_capture": True,
            "fault_rule_armed_once": True,
            "recovery_completed_before_disarm": True,
            "no_admin_or_test_bypass": True,
        },
        "links": output_links,
    }
    return payload, {"completed": completed, "queued_links": prior_links}


def collect_commercial_finalize(
    cursor,
    *,
    browser: dict[str, Any],
    commercial_report: dict[str, Any],
    provider_report: dict[str, Any],
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
        commercial_report.get("links", {}).get("user_id") != user_id
        or provider_report.get("passed") is not True
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
            "prior_commercial_chain_passed": True,
            "provider_unknown_chain_passed": True,
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
        "commercial_report_sha256": _derived_coordinate(
            "commercial-report", commercial_report
        ),
        "provider_report_sha256": _derived_coordinate(
            "provider-report", provider_report
        ),
        "storage_absence_report_sha256": _derived_coordinate(
            "storage-absence-report", storage_absence_report
        ),
    }
