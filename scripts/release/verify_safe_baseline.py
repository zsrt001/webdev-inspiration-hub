#!/usr/bin/env python3
"""Verify a deployed safe baseline without allowing business side effects."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[2]
NOT_RUN_EXIT = 3
MAX_EVIDENCE_LIFETIME = timedelta(hours=1)
MIN_EVIDENCE_REMAINING = timedelta(minutes=15)
EXPECTED_CAPABILITIES = {
    "google_auth",
    "authenticated_upload",
    "generation",
    "credit_pack_checkout",
    "subscription_billing",
    "private_download",
    "partner_invite",
}
MUTATION_TABLES = (
    "users",
    "orders",
    "user_credits",
    "credit_transactions",
    "credit_reservations",
    "idempotency_records",
    "generation_jobs",
    "generation_attempts",
    "qa_verdicts",
    "outbox_events",
    "credit_purchases",
    "user_subscriptions",
    "subscription_credit_grants",
    "live_portrait_jobs",
    "leads",
    "remote_join_sessions",
)
INVALID_WEBHOOK_ALLOWED_STATUSES = {400, 401}
LOGOUT_ALLOWED_STATUSES = {200, 204, 401, 404, 405}
DDL_AUDIT_COVERAGE = {
    "cold_start",
    "auth",
    "admin",
    "credit",
    "webhook",
    "logout",
    "reconciliation",
    "readiness",
}
EDGE_ROUTE_GROUPS = {
    "auth_upload",
    "generation",
    "credit_checkout",
    "subscription",
    "partner_invite",
    "retired_addons",
    "leads_recommendations",
}
EDGE_APPLICATION_GUARDS = {
    "auth_upload": (410, "auth_method_retired"),
    "generation": (410, "admin_generation_execution_retired"),
    "credit_checkout": (410, "legacy_credit_mutation_retired"),
    "subscription": (401, "session_missing"),
    "partner_invite": (410, "partner_session_retired"),
    "retired_addons": (410, "live_portrait_retired"),
    "leads_recommendations": (410, "local_recommendations_retired"),
}


class SafeBaselineVerificationError(RuntimeError):
    pass


def _canonical_unsigned_evidence(payload: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "signature_hmac_sha256"}
    return json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def compute_evidence_hmac(payload: dict[str, Any], hmac_key: bytes) -> str:
    if len(hmac_key) < 32:
        raise ValueError("external evidence HMAC key must contain at least 32 bytes")
    return hmac.new(hmac_key, _canonical_unsigned_evidence(payload), hashlib.sha256).hexdigest()


def verify_evidence_hmac(payload: dict[str, Any], hmac_key: bytes, *, label: str) -> None:
    signature = str(payload.get("signature_hmac_sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        raise SafeBaselineVerificationError(f"{label} signature is missing or invalid")
    try:
        expected = compute_evidence_hmac(payload, hmac_key)
    except ValueError as exc:
        raise SafeBaselineVerificationError(str(exc)) from exc
    if not hmac.compare_digest(signature, expected):
        raise SafeBaselineVerificationError(f"{label} signature mismatch")


def _parse_evidence_datetime(value: Any, *, label: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise SafeBaselineVerificationError(
            f"{label} {field} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise SafeBaselineVerificationError(f"{label} {field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _validate_evidence_window(
    payload: dict[str, Any],
    *,
    label: str,
    now: datetime | None,
) -> tuple[datetime, datetime]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at = _parse_evidence_datetime(
        payload.get("generated_at"),
        label=label,
        field="generated_at",
    )
    expires_at = _parse_evidence_datetime(
        payload.get("expires_at"),
        label=label,
        field="expires_at",
    )
    if generated_at > current + timedelta(minutes=5):
        raise SafeBaselineVerificationError(f"{label} is dated in the future")
    if expires_at - current < MIN_EVIDENCE_REMAINING:
        raise SafeBaselineVerificationError(
            f"{label} has less than fifteen minutes remaining"
        )
    if expires_at <= generated_at or expires_at - generated_at > MAX_EVIDENCE_LIFETIME:
        raise SafeBaselineVerificationError(f"{label} lifetime exceeds one hour")
    return generated_at, expires_at


def _formal_domain_host(value: str) -> str:
    clean = str(value or "").strip().lower()
    parsed = urlsplit(clean if "://" in clean else f"https://{clean}")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise SafeBaselineVerificationError(
            "formal domain must be one HTTPS hostname without a path"
        )
    return parsed.hostname


class RouteProbe:
    def __init__(
        self,
        name: str,
        method: str,
        path: str,
        expected_status: int,
        *,
        json_body: dict[str, Any] | None = None,
        auth_kind: str | None = None,
        multipart: bool = False,
        multipart_field: str = "file",
        params: dict[str, str] | None = None,
        form_body: dict[str, str] | None = None,
        expected_code: str | None = None,
        include_origin: bool = False,
    ) -> None:
        self.name = name
        self.method = method
        self.path = path
        self.expected_status = expected_status
        self.json_body = json_body
        self.auth_kind = auth_kind
        self.multipart = multipart
        self.multipart_field = multipart_field
        self.params = params
        self.form_body = form_body
        self.include_origin = include_origin
        self.expected_code = (
            expected_code
            if expected_code is not None
            else ("capability_disabled" if expected_status == 503 else None)
        )


GUARDED_ROUTE_PROBES = (
    RouteProbe(
        "google_oauth_intent",
        "POST",
        "/api/v1/auth/oauth-intents",
        503,
        json_body={"next_path": "/pages/account/index"},
        include_origin=True,
    ),
    RouteProbe(
        "google_exchange",
        "POST",
        "/api/v1/auth/supabase/session",
        503,
        json_body={"access_token": "x" * 16, "intent_token": "i" * 32},
        include_origin=True,
    ),
    RouteProbe(
        "private_media_upload",
        "POST",
        "/api/v1/media/uploads",
        401,
        multipart=True,
        auth_kind="retired_bearer",
        expected_code="session_missing",
    ),
    RouteProbe(
        "gatekeeper",
        "POST",
        "/api/v1/gatekeeper/check",
        401,
        json_body={"asset_id": "00000000-0000-0000-0000-000000000000"},
        auth_kind="retired_bearer",
        expected_code="session_missing",
    ),
    RouteProbe(
        "order_create",
        "POST",
        "/api/v1/orders/create",
        401,
        json_body={
            "template_id": "solo_royal_castle",
            "asset_ids": ["00000000-0000-4000-8000-000000000001"],
            "legal_accepted": True,
        },
        auth_kind="retired_bearer",
        expected_code="session_missing",
    ),
    RouteProbe(
        "order_delete_paused",
        "DELETE",
        "/api/v1/orders/00000000-0000-0000-0000-000000000000",
        503,
        auth_kind="retired_bearer",
        expected_code="cleanup_paused",
    ),
    RouteProbe(
        "credit_catalog_paused",
        "GET",
        "/api/v1/credits/packages",
        503,
        expected_code="credit_catalog_unavailable",
    ),
    RouteProbe(
        "credit_checkout",
        "POST",
        "/api/v1/payments/checkout",
        401,
        json_body={"package_id": "pack_50", "return_url": "https://example.invalid/return"},
        auth_kind="retired_bearer",
        expected_code="session_missing",
    ),
    RouteProbe(
        "subscription_checkout",
        "POST",
        "/api/v1/subscriptions/checkout",
        401,
        json_body={"plan_code": "starter", "return_url": "https://example.invalid/return"},
        auth_kind="retired_bearer",
        expected_code="session_missing",
    ),
    RouteProbe(
        "subscription_cancel",
        "POST",
        "/api/v1/subscriptions/cancel",
        401,
        auth_kind="retired_bearer",
        expected_code="session_missing",
    ),
    RouteProbe(
        "admin_creem_product_check",
        "GET",
        "/api/v1/admin/creem_product_check",
        401,
        auth_kind="retired_admin_header",
        expected_code="session_missing",
    ),
    RouteProbe(
        "admin_creem_checkout_probe",
        "POST",
        "/api/v1/admin/creem_checkout_probe",
        401,
        auth_kind="retired_admin_header",
        expected_code="session_missing",
    ),
    RouteProbe(
        "admin_generation_probe",
        "POST",
        "/api/v1/admin/generation_probe",
        410,
        json_body={"image_url": "https://example.invalid/a.jpg"},
        auth_kind="retired_admin_header",
        expected_code="admin_generation_execution_retired",
    ),
    RouteProbe(
        "admin_grant_credits",
        "POST",
        "/api/v1/admin/grant_credits",
        401,
        json_body={"user_id": "00000000-0000-0000-0000-000000000000", "amount": 1},
        auth_kind="retired_admin_header",
        expected_code="session_missing",
    ),
    RouteProbe(
        "admin_regenerate",
        "POST",
        "/api/v1/admin/orders/00000000-0000-0000-0000-000000000000/regenerate",
        410,
        json_body={"reason": "safe baseline probe"},
        auth_kind="retired_admin_header",
        expected_code="admin_generation_execution_retired",
    ),
    RouteProbe(
        "admin_cleanup_paused",
        "POST",
        "/api/v1/admin/cleanup_expired_assets",
        401,
        auth_kind="retired_admin_header",
        expected_code="session_missing",
    ),
    RouteProbe(
        "ops_poll_pending_post",
        "POST",
        "/api/v1/ops/poll_pending_orders",
        410,
        auth_kind="cleanup",
        expected_code="legacy_order_poller_retired",
    ),
)

RETIRED_ROUTE_PROBES = (
    RouteProbe(
        "manual_checkout_page_removed",
        "GET",
        "/api/v1/payments/manual/checkout",
        404,
        params={"purchase_id": "safe-baseline", "token": "safe-baseline"},
        expected_code="not_found",
    ),
    RouteProbe(
        "manual_checkout_submit_removed",
        "POST",
        "/api/v1/payments/manual/submit",
        404,
        form_body={"purchase_id": "safe-baseline", "token": "safe-baseline"},
        expected_code="not_found",
    ),
    RouteProbe(
        "manual_checkout_complete_removed",
        "POST",
        "/api/v1/payments/manual/admin/complete",
        404,
        json_body={"purchase_id": "safe-baseline"},
        auth_kind="retired_admin_header",
        expected_code="not_found",
    ),
    RouteProbe(
        "manual_checkout_fail_removed",
        "POST",
        "/api/v1/payments/manual/admin/fail",
        404,
        json_body={"purchase_id": "safe-baseline", "reason": "safe baseline probe"},
        auth_kind="retired_admin_header",
        expected_code="not_found",
    ),
    RouteProbe(
        "auth_method_retired",
        "POST",
        "/api/v1/auth/login",
        410,
        json_body={"code": "safe-baseline"},
        expected_code="auth_method_retired",
    ),
    RouteProbe(
        "public_upload_retired",
        "POST",
        "/api/v1/upload",
        410,
        multipart=True,
        expected_code="public_upload_retired",
    ),
    RouteProbe(
        "public_multi_upload_retired",
        "POST",
        "/api/v1/upload/multiple",
        410,
        multipart=True,
        multipart_field="files",
        expected_code="public_upload_retired",
    ),
    RouteProbe(
        "public_url_delete_retired",
        "POST",
        "/api/v1/upload/delete",
        410,
        json_body={"url": "https://example.invalid/probe.jpg"},
        expected_code="public_upload_retired",
    ),
    RouteProbe(
        "partner_session_create_retired",
        "POST",
        "/api/v1/session/create",
        410,
        json_body={"template_id": "safe-baseline-probe"},
        expected_code="partner_session_retired",
    ),
    RouteProbe("partner_session_status_retired", "GET", "/api/v1/session/safe-baseline-probe/status", 410, expected_code="partner_session_retired"),
    RouteProbe(
        "partner_session_upload_host_retired",
        "POST",
        "/api/v1/session/safe-baseline-probe/upload/host",
        410,
        params={"image_url": "https://example.invalid/host.jpg"},
        expected_code="partner_session_retired",
    ),
    RouteProbe(
        "partner_session_upload_guest_retired",
        "POST",
        "/api/v1/session/safe-baseline-probe/upload/guest",
        410,
        params={"image_url": "https://example.invalid/partner.jpg"},
        expected_code="partner_session_retired",
    ),
    RouteProbe("partner_session_images_retired", "GET", "/api/v1/session/safe-baseline-probe/images", 410, expected_code="partner_session_retired"),
    RouteProbe("partner_session_share_meta_retired", "GET", "/api/v1/session/safe-baseline-probe/share_meta", 410, expected_code="partner_session_retired"),
    RouteProbe("partner_session_processing_retired", "POST", "/api/v1/session/safe-baseline-probe/processing", 410, expected_code="partner_session_retired"),
    RouteProbe("partner_session_complete_retired", "POST", "/api/v1/session/safe-baseline-probe/complete", 410, expected_code="partner_session_retired"),
    RouteProbe(
        "partner_session_bind_order_retired",
        "POST",
        "/api/v1/session/safe-baseline-probe/bind_order",
        410,
        json_body={"order_id": "00000000-0000-0000-0000-000000000000"},
        expected_code="partner_session_retired",
    ),
    RouteProbe("legacy_user_create_retired", "POST", "/api/v1/users/", 410, json_body={"openid": "legacy"}),
    RouteProbe("legacy_user_read_retired", "GET", "/api/v1/users/00000000-0000-0000-0000-000000000000", 410),
    RouteProbe("legacy_user_patch_retired", "PATCH", "/api/v1/users/00000000-0000-0000-0000-000000000000", 410, json_body={"nickname": "legacy"}),
    RouteProbe("legacy_credit_purchase_retired", "POST", "/api/v1/credits/purchase", 410, json_body={"package_id": "pack_50"}),
    RouteProbe("legacy_credit_deduct_retired", "POST", "/api/v1/credits/deduct", 410),
    RouteProbe("legacy_credit_add_retired", "POST", "/api/v1/credits/add", 410, json_body={"user_id": "00000000-0000-0000-0000-000000000000", "amount": 1}),
    RouteProbe(
        "live_portrait_generate_retired",
        "POST",
        "/api/v1/live_portrait/generate",
        410,
        json_body={"image_url": "https://example.invalid/probe.jpg", "seconds": 5},
    ),
    RouteProbe("live_portrait_list_retired", "GET", "/api/v1/live_portrait/list", 410),
    RouteProbe("live_portrait_detail_retired", "GET", "/api/v1/live_portrait/00000000-0000-0000-0000-000000000000", 410),
    RouteProbe("recommendations_retired", "GET", "/api/v1/recommendations/local_studios", 410),
    RouteProbe(
        "leads_submit_retired",
        "POST",
        "/api/v1/leads/submit",
        410,
        json_body={
            "name": "Retired lead",
            "phone": "5551234567",
            "city": "New York",
            "privacy_accepted": True,
        },
    ),
    RouteProbe("leads_list_retired", "GET", "/api/v1/leads/list", 410),
    RouteProbe("leads_export_retired", "GET", "/api/v1/leads/export.csv", 410),
    RouteProbe(
        "admin_crm_preview_retired",
        "GET",
        "/api/v1/admin/crm_preview",
        410,
        auth_kind="retired_admin_header",
        expected_code="leads_retired",
    ),
    RouteProbe(
        "admin_crm_push_retired",
        "POST",
        "/api/v1/admin/crm_push",
        410,
        auth_kind="retired_admin_header",
        expected_code="leads_retired",
    ),
    RouteProbe(
        "admin_crm_history_retired",
        "GET",
        "/api/v1/admin/crm_push_history",
        410,
        auth_kind="retired_admin_header",
        expected_code="leads_retired",
    ),
)


def compare_no_side_effect_snapshot(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    keys = ("table_counts", "public_reference_checksum")
    mismatches = {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in keys
        if before.get(key) != after.get(key)
    }
    return {"matches": not mismatches, "mismatches": mismatches}


def _sync_database_url(value: str) -> str:
    clean = value.strip()
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if clean.startswith(prefix):
            return "postgresql+psycopg2://" + clean.removeprefix(prefix)
    if clean.startswith("postgresql+psycopg2://"):
        return clean
    raise ValueError("safe-baseline database URL must use PostgreSQL")


def _normalize_reference(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
    return value.split("?", 1)[0].split("#", 1)[0]


def _walk_strings(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, str) and value.strip():
        yield value.strip()


def _reference_checksum(connection, tables: set[str]) -> str:
    hashes: list[str] = []
    if "orders" in tables:
        rows = connection.execute(
            text("SELECT source_image_urls, preview_image_urls, final_image_urls FROM orders")
        )
        for row in rows:
            for payload in row:
                if payload is None:
                    continue
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        pass
                for reference in _walk_strings(payload):
                    hashes.append(hashlib.sha256(_normalize_reference(reference).encode()).hexdigest())
    scalar_queries = {
        "users": "SELECT avatar_url FROM users",
        "live_portrait_jobs": "SELECT source_image_url, video_url FROM live_portrait_jobs",
        "credit_purchases": "SELECT checkout_url FROM credit_purchases",
    }
    for table_name, statement in scalar_queries.items():
        if table_name not in tables:
            continue
        for row in connection.execute(text(statement)):
            for reference in row:
                if reference:
                    hashes.append(
                        hashlib.sha256(_normalize_reference(str(reference)).encode()).hexdigest()
                    )
    canonical = json.dumps(sorted(hashes), separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _snapshot_database(
    database_url: str,
    *,
    expected_schema: str,
    expected_source_sha: str,
    expected_runtime_bundle_id: str,
) -> dict[str, Any]:
    engine = create_engine(_sync_database_url(database_url), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                transaction_read_only = str(
                    connection.execute(text("SHOW transaction_read_only")).scalar_one()
                ).lower() == "on"
                default_read_only = str(
                    connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
                ).lower() == "on"
                if not transaction_read_only or not default_read_only:
                    raise SafeBaselineVerificationError("verification database role is not read-only")
                revision = str(connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one())
                if revision != expected_schema:
                    raise SafeBaselineVerificationError(
                        f"schema mismatch: expected {expected_schema}, observed {revision}"
                    )
                table_names = set(
                    connection.execute(
                        text(
                            """
                            SELECT table_name FROM information_schema.tables
                            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                            """
                        )
                    ).scalars()
                )
                table_counts: dict[str, int] = {}
                for table_name in MUTATION_TABLES:
                    if table_name in table_names:
                        if not re.fullmatch(r"[a-z_]+", table_name):
                            raise SafeBaselineVerificationError("unexpected table identifier")
                        table_counts[table_name] = int(
                            connection.execute(text(f'SELECT count(*) FROM "{table_name}"')).scalar_one()
                        )
                flags = connection.execute(
                    text(
                        """
                        SELECT capability, state FROM ops_feature_flags
                        WHERE environment = 'production' ORDER BY capability
                        """
                    )
                ).mappings().all()
                observed_capabilities = {str(row["capability"]) for row in flags}
                if observed_capabilities != EXPECTED_CAPABILITIES or any(row["state"] != "OFF" for row in flags):
                    raise SafeBaselineVerificationError("Production capability rows are not the exact all-OFF set")
                activations = connection.execute(
                    text(
                        """
                        SELECT source_sha, runtime_bundle_id, api_deployment_id, api_deployment_url,
                               phase, manifest_sha256
                        FROM release_activations
                        WHERE environment = 'production' AND kind = 'SAFE_BASELINE_INSTALL'
                        """
                    )
                ).mappings().all()
                if len(activations) != 1:
                    raise SafeBaselineVerificationError("exactly one safe-baseline activation is required")
                activation = dict(activations[0])
                if activation["source_sha"] != expected_source_sha:
                    raise SafeBaselineVerificationError("activation source SHA mismatch")
                if activation["runtime_bundle_id"] != expected_runtime_bundle_id:
                    raise SafeBaselineVerificationError("activation runtime bundle mismatch")
                snapshot = {
                    "schema_revision": revision,
                    "table_counts": table_counts,
                    "public_reference_checksum": _reference_checksum(connection, table_names),
                    "flags": {str(row["capability"]): str(row["state"]) for row in flags},
                    "activation": activation,
                    "read_only": {
                        "transaction_read_only": transaction_read_only,
                        "default_transaction_read_only": default_read_only,
                    },
                }
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
        snapshot["snapshot_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
        return snapshot
    finally:
        engine.dispose()


def _parse_protected_header(env_name: str | None) -> dict[str, str]:
    if not env_name:
        return {}
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        raise SafeBaselineVerificationError(f"protected header variable {env_name} is missing")
    name, separator, value = raw.partition(":")
    if not separator or not name.strip() or not value.strip() or "\n" in raw or "\r" in raw:
        raise ValueError(f"{env_name} must contain one 'Header-Name: value' pair")
    return {name.strip(): value.strip()}


def _response_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("code") or "")
    return str(payload.get("code") or "") if isinstance(payload, dict) else ""


def _run_route_probe(
    client: httpx.Client,
    base_headers: dict[str, str],
    probe: RouteProbe,
    *,
    cleanup_token: str,
    request_origin: str,
) -> dict[str, Any]:
    headers = dict(base_headers)
    if probe.auth_kind == "retired_bearer":
        headers["Authorization"] = "Bearer retired-safe-baseline-probe"
    elif probe.auth_kind == "retired_admin_header":
        headers["X-Admin-Token"] = "retired-safe-baseline-probe"
    elif probe.auth_kind == "cleanup":
        headers["Authorization"] = f"Bearer {cleanup_token}"
    if probe.include_origin:
        headers["Origin"] = request_origin
    kwargs: dict[str, Any] = {"headers": headers}
    if probe.params is not None:
        kwargs["params"] = probe.params
    if probe.json_body is not None:
        kwargs["json"] = probe.json_body
    if probe.form_body is not None:
        kwargs["data"] = probe.form_body
    if probe.multipart:
        kwargs["files"] = {
            probe.multipart_field: ("probe.png", b"safe-baseline", "image/png")
        }
    response = client.request(probe.method, probe.path, **kwargs)
    code = _response_code(response)
    if response.status_code != probe.expected_status:
        raise SafeBaselineVerificationError(
            f"{probe.name} expected {probe.expected_status}, observed {response.status_code}"
        )
    if probe.expected_code is not None and code != probe.expected_code:
        raise SafeBaselineVerificationError(
            f"{probe.name} expected code {probe.expected_code}, observed {code or 'missing'}"
        )
    return {"name": probe.name, "status": response.status_code, "code": code}


def _verify_runtime_identity(
    client: httpx.Client,
    headers: dict[str, str],
    *,
    expected_source_sha: str,
    expected_runtime_bundle_id: str,
    expected_deployment_id: str,
) -> dict[str, str]:
    health = client.get("/health", headers=headers)
    if health.status_code != 200:
        raise SafeBaselineVerificationError(f"liveness returned {health.status_code}")
    if health.json() != {
        "status": "healthy",
        "kind": "liveness",
        "readiness": "/health/ready",
    }:
        raise SafeBaselineVerificationError(
            "liveness response contains non-process state or is unhealthy"
        )

    version = client.get("/version", headers=headers)
    if version.status_code != 200:
        raise SafeBaselineVerificationError(
            f"runtime version attestation returned {version.status_code}"
        )
    try:
        version_payload = version.json()
    except ValueError as exc:
        raise SafeBaselineVerificationError(
            "runtime version attestation returned an invalid payload"
        ) from exc
    if not isinstance(version_payload, dict):
        raise SafeBaselineVerificationError(
            "runtime version attestation returned an invalid payload"
        )
    observed_source_sha = str(version_payload.get("source_sha") or "")
    if re.fullmatch(r"[0-9a-f]{40,64}", observed_source_sha) is None:
        raise SafeBaselineVerificationError(
            "runtime source_sha is missing or invalid"
        )
    expected_coordinates = {
        "source_sha": expected_source_sha,
        "runtime_bundle_id": expected_runtime_bundle_id,
        "deployment_id": expected_deployment_id,
    }
    for key, expected in expected_coordinates.items():
        if str(version_payload.get(key) or "") != expected:
            raise SafeBaselineVerificationError(f"runtime {key} mismatch")
    return expected_coordinates


def _verify_http(
    base_url: str,
    *,
    protected_headers: dict[str, str],
    cleanup_token: str,
    request_origin: str,
    expected_source_sha: str,
    expected_runtime_bundle_id: str,
    expected_deployment_id: str,
) -> dict[str, Any]:
    normalized_request_origin = f"https://{_formal_domain_host(request_origin)}"
    headers = {"User-Agent": "vowpic-safe-baseline-verifier/1", **protected_headers}
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=20.0, follow_redirects=False) as client:
        expected_coordinates = _verify_runtime_identity(
            client,
            headers,
            expected_source_sha=expected_source_sha,
            expected_runtime_bundle_id=expected_runtime_bundle_id,
            expected_deployment_id=expected_deployment_id,
        )

        guarded = [
            _run_route_probe(
                client,
                headers,
                probe,
                cleanup_token=cleanup_token,
                request_origin=normalized_request_origin,
            )
            for probe in GUARDED_ROUTE_PROBES
        ]
        retired = [
            _run_route_probe(
                client,
                headers,
                probe,
                cleanup_token=cleanup_token,
                request_origin=normalized_request_origin,
            )
            for probe in RETIRED_ROUTE_PROBES
        ]
        webhook = client.post(
            "/api/v1/payments/webhook/creem",
            headers={**headers, "creem-signature": "invalid-safe-baseline-signature"},
            json={"id": "invalid-safe-baseline-event"},
        )
        if webhook.status_code not in INVALID_WEBHOOK_ALLOWED_STATUSES:
            raise SafeBaselineVerificationError(
                f"invalid signed webhook did not reach signature validation: {webhook.status_code}"
            )
        logout = client.post("/api/v1/auth/logout", headers=headers)
        if logout.status_code not in LOGOUT_ALLOWED_STATUSES:
            raise SafeBaselineVerificationError(f"logout path was unexpectedly blocked: {logout.status_code}")
        cleanup = client.post(
            "/api/v1/ops/cleanup_expired_assets",
            headers={**headers, "Authorization": f"Bearer {cleanup_token}"},
        )
        if cleanup.status_code != 503 or _response_code(cleanup) != "cleanup_paused":
            raise SafeBaselineVerificationError("cleanup is not explicitly paused")
        public_config = client.get("/api/v1/ops/public_config", headers=headers)
        if public_config.status_code != 200:
            raise SafeBaselineVerificationError("public capability config is unavailable")
        capabilities = public_config.json().get("capabilities")
        if set(capabilities or {}) != EXPECTED_CAPABILITIES or any(bool(value) for value in capabilities.values()):
            raise SafeBaselineVerificationError("public capability config is not exact all-OFF")
    return {
        "runtime_coordinates": expected_coordinates,
        "guarded_routes": guarded,
        "retired_routes": retired,
        "invalid_webhook_status": webhook.status_code,
        "logout_status": logout.status_code,
        "logout_semantics": "NOT_APPLICABLE_PRE_TASK7" if logout.status_code == 404 else "REACHABLE",
        "cleanup_status": cleanup.status_code,
        "capabilities": capabilities,
    }


def _static_runtime_ddl_hits() -> list[str]:
    forbidden = re.compile(
        r"Base\.metadata\.create_all|\bCREATE\s+(?:TABLE|INDEX|SCHEMA|TRIGGER|FUNCTION)\b|"
        r"\bALTER\s+TABLE\b|\bDROP\s+(?:TABLE|INDEX|SCHEMA|TRIGGER|FUNCTION)\b",
        re.IGNORECASE,
    )
    hits: list[str] = []
    for root in (ROOT / "backend" / "app", ROOT / "api"):
        for path in root.rglob("*.py"):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if forbidden.search(line):
                    hits.append(f"{path.relative_to(ROOT).as_posix()}:{line_number}")
    return hits


def _validate_runtime_ddl_audit(
    path: Path,
    *,
    source_sha: str,
    runtime_bundle_id: str,
    deployment_id: str,
    workflow_run_id: str,
    workflow_attempt: int,
    hmac_key: bytes,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    verify_evidence_hmac(payload, hmac_key, label="runtime DDL audit")
    required_coordinates = {
        "source_sha": source_sha,
        "runtime_bundle_id": runtime_bundle_id,
        "deployment_id": deployment_id,
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": workflow_attempt,
    }
    if payload.get("schema_version") != "vowpic.runtime-ddl-audit.v1" or payload.get("passed") is not True:
        raise SafeBaselineVerificationError("runtime DDL audit report is not a PASS v1 report")
    for key, expected in required_coordinates.items():
        actual = payload.get(key)
        if key == "workflow_attempt" and type(actual) is not int:
            raise SafeBaselineVerificationError("runtime DDL audit workflow_attempt mismatch")
        if actual != expected:
            raise SafeBaselineVerificationError(f"runtime DDL audit {key} mismatch")
    _, expires_at = _validate_evidence_window(
        payload,
        label="runtime DDL audit",
        now=now,
    )
    statement_count = payload.get("statement_count")
    if type(statement_count) is not int or statement_count <= 0:
        raise SafeBaselineVerificationError(
            "runtime database statement recorder observed no statements"
        )
    if type(payload.get("ddl_statement_count")) is not int or payload["ddl_statement_count"] != 0:
        raise SafeBaselineVerificationError("runtime database statement recorder observed DDL")
    if set(payload.get("coverage") or []) != DDL_AUDIT_COVERAGE:
        raise SafeBaselineVerificationError("runtime DDL audit coverage is incomplete")
    return {
        "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "statement_count": statement_count,
        "ddl_statement_count": 0,
        "coverage": sorted(DDL_AUDIT_COVERAGE),
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": workflow_attempt,
        "report_expires_at": expires_at.isoformat(),
    }


def _validate_edge_handoff(
    path: Path,
    *,
    source_sha: str,
    runtime_bundle_id: str,
    deployment_id: str,
    workflow_run_id: str,
    workflow_attempt: int,
    project_id: str,
    formal_domain: str,
    expected_lockdown_after_config_sha256: str | None,
    expected_lockdown_baseline_config_sha256: str | None,
    hmac_key: bytes,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    verify_evidence_hmac(payload, hmac_key, label="edge handoff")
    if payload.get("schema_version") != "vowpic.edge-handoff.v1" or payload.get("passed") is not True:
        raise SafeBaselineVerificationError("edge handoff report is not a PASS v1 report")
    expected_coordinates: dict[str, Any] = {
        "source_sha": source_sha,
        "runtime_bundle_id": runtime_bundle_id,
        "deployment_id": deployment_id,
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": workflow_attempt,
        "project_id": project_id,
        "formal_domain": _formal_domain_host(formal_domain),
    }
    for key, expected in expected_coordinates.items():
        actual = payload.get(key)
        if key == "workflow_attempt" and type(actual) is not int:
            raise SafeBaselineVerificationError("edge handoff workflow_attempt mismatch")
        if key == "formal_domain":
            actual = _formal_domain_host(str(actual or ""))
        if actual != expected:
            raise SafeBaselineVerificationError(f"edge handoff {key} mismatch")
    _, expires_at = _validate_evidence_window(payload, label="edge handoff", now=now)
    groups = payload.get("route_groups") if isinstance(payload.get("route_groups"), dict) else {}
    if set(groups) != EDGE_ROUTE_GROUPS:
        raise SafeBaselineVerificationError("edge handoff route-group coverage is incomplete")
    for name, result in groups.items():
        if (
            not isinstance(result, dict)
            or result.get("rule_removed") is not True
            or result.get("read_back") is not True
        ):
            raise SafeBaselineVerificationError(f"edge route group {name} was not removed/read back")
        expected_status, expected_code = EDGE_APPLICATION_GUARDS[name]
        if (
            result.get("no_side_effects") is not True
            or type(result.get("application_status")) is not int
            or result["application_status"] != expected_status
            or result.get("application_code") != expected_code
        ):
            raise SafeBaselineVerificationError(f"edge route group {name} lacks application guard evidence")
    if payload.get("runner_bypass_removed") is not True:
        raise SafeBaselineVerificationError("temporary edge runner bypass was not removed")
    for field in ("before_config_sha256", "after_config_sha256"):
        value = str(payload.get(field) or "")
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise SafeBaselineVerificationError(f"edge handoff {field} is invalid")
    lockdown_hash = str(payload.get("lockdown_after_config_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", lockdown_hash):
        raise SafeBaselineVerificationError("edge handoff lockdown config hash is invalid")
    if payload["before_config_sha256"] != lockdown_hash:
        raise SafeBaselineVerificationError(
            "edge handoff does not start from its signed lockdown config"
        )
    if (
        expected_lockdown_after_config_sha256
        and lockdown_hash != expected_lockdown_after_config_sha256
    ):
        raise SafeBaselineVerificationError(
            "edge handoff does not match the verified lockdown config"
        )
    baseline_hash = str(payload.get("lockdown_baseline_config_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", baseline_hash):
        raise SafeBaselineVerificationError("edge handoff baseline config hash is invalid")
    if payload["after_config_sha256"] != baseline_hash:
        raise SafeBaselineVerificationError(
            "edge handoff did not restore the signed pre-lockdown baseline"
        )
    if (
        expected_lockdown_baseline_config_sha256
        and baseline_hash != expected_lockdown_baseline_config_sha256
    ):
        raise SafeBaselineVerificationError(
            "edge handoff does not match the verified baseline config"
        )
    return {
        "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "route_groups": sorted(groups),
        "runner_bypass_removed": True,
        "before_config_sha256": payload["before_config_sha256"],
        "after_config_sha256": payload["after_config_sha256"],
        "lockdown_after_config_sha256": lockdown_hash,
        "lockdown_baseline_config_sha256": baseline_hash,
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": workflow_attempt,
        "project_id": project_id,
        "formal_domain": expected_coordinates["formal_domain"],
        "report_expires_at": expires_at.isoformat(),
    }


def _write_create_once(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--request-origin", required=True)
    header_group = parser.add_mutually_exclusive_group()
    header_group.add_argument("--deployment-bypass-header-env")
    header_group.add_argument("--edge-bypass-header-env")
    parser.add_argument("--database-url-env", default="PRODUCTION_READ_ONLY_DATABASE_URL")
    parser.add_argument("--cleanup-token-env", default="CLEANUP_CRON_TOKEN")
    parser.add_argument("--runtime-ddl-audit-report", required=True)
    parser.add_argument("--edge-handoff-report")
    parser.add_argument("--runtime-audit-hmac-key-env", default="RUNTIME_AUDIT_HMAC_KEY")
    parser.add_argument("--edge-evidence-hmac-key-env", default="EDGE_EVIDENCE_HMAC_KEY")
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-runtime-bundle-id", required=True)
    parser.add_argument("--expected-schema", required=True)
    parser.add_argument("--expected-deployment-url")
    parser.add_argument("--expected-deployment-id")
    parser.add_argument("--expected-workflow-run-id", required=True)
    parser.add_argument("--expected-workflow-attempt", required=True, type=int)
    parser.add_argument("--expected-project-id")
    parser.add_argument("--expected-formal-domain")
    parser.add_argument("--expected-lockdown-after-config-sha256")
    parser.add_argument("--expected-lockdown-baseline-config-sha256")
    parser.add_argument("--require-platform-deployment-id", action="store_true")
    parser.add_argument("--expected-layer", choices=("app",), default="app")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        database_url = os.environ.get(args.database_url_env, "").strip()
        cleanup_token = os.environ.get(args.cleanup_token_env, "").strip()
        runtime_audit_hmac_key = os.environ.get(args.runtime_audit_hmac_key_env, "").encode("utf-8")
        edge_evidence_hmac_key = os.environ.get(args.edge_evidence_hmac_key_env, "").encode("utf-8")
        if not all((database_url, cleanup_token)):
            print("NOT_RUN: read-only DB and the cleanup service credential are required", file=sys.stderr)
            return NOT_RUN_EXIT
        if len(runtime_audit_hmac_key) < 32:
            print("NOT_RUN: authenticated runtime DDL audit evidence is required", file=sys.stderr)
            return NOT_RUN_EXIT
        if args.edge_handoff_report and len(edge_evidence_hmac_key) < 32:
            print("NOT_RUN: authenticated edge handoff evidence is required", file=sys.stderr)
            return NOT_RUN_EXIT
        source_sha = str(args.expected_source_sha).strip().lower()
        runtime_bundle_id = str(args.expected_runtime_bundle_id).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40,64}", source_sha):
            raise ValueError("expected source SHA is invalid")
        if not re.fullmatch(r"rtb_[0-9a-f]{64}", runtime_bundle_id):
            raise ValueError("expected runtime bundle ID is invalid")
        if not args.expected_workflow_run_id.strip() or args.expected_workflow_attempt < 1:
            raise ValueError("expected workflow run ID and positive attempt are required")
        before = _snapshot_database(
            database_url,
            expected_schema=args.expected_schema,
            expected_source_sha=source_sha,
            expected_runtime_bundle_id=runtime_bundle_id,
        )
        activation = before["activation"]
        expected_deployment_id = str(args.expected_deployment_id or activation["api_deployment_id"] or "")
        if args.require_platform_deployment_id and not expected_deployment_id:
            raise SafeBaselineVerificationError("platform deployment ID is required")
        if args.expected_deployment_url and activation["api_deployment_url"] != args.expected_deployment_url:
            raise SafeBaselineVerificationError("activation deployment URL mismatch")
        protected_headers = _parse_protected_header(
            args.deployment_bypass_header_env or args.edge_bypass_header_env
        )
        static_ddl_hits = _static_runtime_ddl_hits()
        if static_ddl_hits:
            raise SafeBaselineVerificationError(f"runtime DDL source hits: {static_ddl_hits}")
        ddl_audit = _validate_runtime_ddl_audit(
            Path(args.runtime_ddl_audit_report),
            source_sha=source_sha,
            runtime_bundle_id=runtime_bundle_id,
            deployment_id=expected_deployment_id,
            workflow_run_id=args.expected_workflow_run_id,
            workflow_attempt=args.expected_workflow_attempt,
            hmac_key=runtime_audit_hmac_key,
        )
        edge_handoff = None
        if args.edge_handoff_report:
            if not args.expected_project_id or not args.expected_formal_domain:
                raise SafeBaselineVerificationError(
                    "edge handoff requires expected Vercel project and formal domain"
                )
            edge_handoff = _validate_edge_handoff(
                Path(args.edge_handoff_report),
                source_sha=source_sha,
                runtime_bundle_id=runtime_bundle_id,
                deployment_id=expected_deployment_id,
                workflow_run_id=args.expected_workflow_run_id,
                workflow_attempt=args.expected_workflow_attempt,
                project_id=args.expected_project_id,
                formal_domain=args.expected_formal_domain,
                expected_lockdown_after_config_sha256=(
                    args.expected_lockdown_after_config_sha256 or None
                ),
                expected_lockdown_baseline_config_sha256=(
                    args.expected_lockdown_baseline_config_sha256 or None
                ),
                hmac_key=edge_evidence_hmac_key,
            )
        http_evidence = _verify_http(
            args.base_url,
            protected_headers=protected_headers,
            cleanup_token=cleanup_token,
            request_origin=args.request_origin,
            expected_source_sha=source_sha,
            expected_runtime_bundle_id=runtime_bundle_id,
            expected_deployment_id=expected_deployment_id,
        )
        after = _snapshot_database(
            database_url,
            expected_schema=args.expected_schema,
            expected_source_sha=source_sha,
            expected_runtime_bundle_id=runtime_bundle_id,
        )
        comparison = compare_no_side_effect_snapshot(before, after)
        if not comparison["matches"]:
            raise SafeBaselineVerificationError("safe-baseline probes mutated protected business state")
        generated_at = datetime.now(timezone.utc).isoformat()
        report = {
            "schema_version": "vowpic.safe-baseline-verification.v1",
            "passed": True,
            "generated_at": generated_at,
            "created_at": generated_at,
            "expected_layer": args.expected_layer,
            "base_host": urlsplit(args.base_url).hostname,
            "source_sha": source_sha,
            "runtime_bundle_id": runtime_bundle_id,
            "deployment_id": expected_deployment_id,
            "before_snapshot_sha256": before["snapshot_sha256"],
            "after_snapshot_sha256": after["snapshot_sha256"],
            "no_side_effects": comparison,
            "runtime_ddl": {"static_hits": 0, **ddl_audit},
            "edge_handoff": edge_handoff,
            "http": http_evidence,
        }
        _write_create_once(Path(args.output), report)
        print(json.dumps({"passed": True, "output": args.output}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, httpx.HTTPError, SafeBaselineVerificationError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
