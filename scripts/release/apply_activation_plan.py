#!/usr/bin/env python3
"""Apply one audited production capability cohort or emergency-OFF phase."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID, uuid4


CAPABILITIES = (
    "google_auth",
    "authenticated_upload",
    "credit_pack_checkout",
    "subscription_billing",
    "generation",
    "private_download",
    "partner_invite",
)
PHASES = ("google-auth-only", "staged-user-cohort", "formal-cohort", "emergency-off")
ACTIVATION_KINDS = ("COMMERCIAL_7A", "GOOGLE_AUTH_ONLY")
GOOGLE_AUTH_ONLY_PHASES = frozenset({"google-auth-only", "emergency-off"})
SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("activation plan database URL is invalid")
    return clean


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _load_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("activation plan input must be a JSON object")
    return payload


def _verify_signed_acceptance(
    report: dict[str, Any], *, expected_phase: str, signing_key: bytes
) -> None:
    signature = str(report.get("signature") or "")
    if len(signing_key) < 32 or not re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", signature):
        raise ValueError("activation acceptance signature is invalid")
    unsigned = dict(report)
    unsigned.pop("signature", None)
    wanted = hmac.new(signing_key, _canonical(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.split(":", 1)[1], wanted):
        raise ValueError("activation acceptance signature mismatch")
    if (
        report.get("schema") != "vowpic.linked-commercial-acceptance.v1"
        or report.get("phase") != expected_phase
        or report.get("passed") is not True
    ):
        raise ValueError("activation acceptance report identity is invalid")


def _snapshot_hash(capability: str, row: dict[str, Any]) -> str:
    payload = {
        "capability": capability,
        "environment": row.get("environment"),
        "state": row.get("state", "OFF"),
        "deployment_id": row.get("deployment_id"),
        "runtime_bundle_id": row.get("runtime_bundle_id"),
        "worker_image_digest": row.get("worker_image_digest"),
        "release_activation_id": str(row["release_activation_id"])
        if row.get("release_activation_id")
        else None,
        "target_manifest_sha256": row.get("target_manifest_sha256"),
        "cohort_user_ids": sorted(str(value) for value in (row.get("cohort_user_ids") or [])),
        "verified_identity_hashes": sorted(
            str(value) for value in (row.get("verified_identity_hashes") or [])
        ),
        "expires_at": row["expires_at"].astimezone(timezone.utc).isoformat()
        if isinstance(row.get("expires_at"), datetime)
        else row.get("expires_at"),
        "version": int(row.get("version") or 0),
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def validate_plan(plan: dict[str, Any]) -> None:
    if (
        set(plan) != {"schema", "flag_order", "target_snapshot", "rollback_snapshot"}
        or plan.get("schema") != "vowpic.activation-plan.v1"
        or tuple(plan.get("flag_order") or ()) != CAPABILITIES
        or plan.get("target_snapshot") != {name: "ON" for name in CAPABILITIES}
        or plan.get("rollback_snapshot") != {name: "OFF" for name in CAPABILITIES}
    ):
        raise ValueError("activation plan contract is invalid")


def _activation(
    cursor: Any,
    *,
    deployment_id: str | None,
    source_sha: str | None,
    approval: str,
    kind: str = "COMMERCIAL_7A",
    for_update: bool = True,
) -> dict[str, Any]:
    if kind not in ACTIVATION_KINDS:
        raise ValueError("activation plan kind is invalid")
    filters = ["environment = 'production'", "kind = %s"]
    values: list[object] = [kind]
    if deployment_id:
        filters.append("api_deployment_id = %s")
        values.append(deployment_id)
    if source_sha:
        filters.append("source_sha = %s")
        values.append(source_sha)
    lock_clause = " FOR UPDATE" if for_update else ""
    cursor.execute(
        f"SELECT * FROM release_activations WHERE {' AND '.join(filters)} "
        f"ORDER BY updated_at DESC LIMIT 2{lock_clause}",
        tuple(values),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    if len(rows) != 1:
        raise ValueError(f"exactly one {kind} activation is required")
    activation = rows[0]
    if str(activation.get("approval") or "") != approval:
        raise ValueError("activation plan approval does not match the release")
    if activation.get("phase") in {"FAILED", "CLEANED"}:
        raise ValueError("activation plan release is not active")
    return activation


def _binding_report(path: str, *, activation: dict[str, Any]) -> dict[str, Any]:
    report = _load_object(path)
    if (
        report.get("schema") != "vowpic.acceptance-identity-bindings.v1"
        or report.get("passed") is not True
        or report.get("environment") != "production"
        or report.get("deployment_id") != activation["api_deployment_id"]
        or not isinstance(report.get("count"), int)
        or report["count"] < 1
    ):
        raise ValueError("acceptance identity binding report is invalid")
    if activation.get("kind") == "GOOGLE_AUTH_ONLY":
        provider_counts = report.get("provider_counts")
        if (
            report["count"] != 2
            or not isinstance(provider_counts, dict)
            or set(provider_counts) != {"google", "google_email"}
            or any(not isinstance(value, int) or value < 0 for value in provider_counts.values())
            or sum(provider_counts.values()) != 2
        ):
            raise ValueError("GOOGLE_AUTH_ONLY requires exactly two protected Google bindings")
    expires_at = datetime.fromisoformat(str(report.get("expires_at") or ""))
    if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
        raise ValueError("acceptance identity bindings are expired")
    reservation_expiry = activation.get("reservation_expires_at")
    if (
        activation.get("kind") == "GOOGLE_AUTH_ONLY"
        and (
            not isinstance(reservation_expiry, datetime)
            or reservation_expiry.tzinfo is None
            or reservation_expiry <= datetime.now(timezone.utc)
            or expires_at > reservation_expiry
        )
    ):
        raise ValueError("GOOGLE_AUTH_ONLY binding lease exceeds the activation reservation")
    return {**report, "expires_at": expires_at}


def _cohort_user(
    path: str,
    *,
    activation: dict[str, Any],
    signing_key: bytes,
) -> tuple[UUID, dict[str, Any]]:
    report = _load_object(path)
    _verify_signed_acceptance(
        report, expected_phase="first-login-and-auth-security", signing_key=signing_key
    )
    expected = {
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "deployment_id": activation["api_deployment_id"],
        "manifest_sha256": activation["manifest_sha256"],
    }
    if any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("acceptance user report release binding mismatch")
    links = report.get("links")
    if not isinstance(links, dict):
        raise ValueError("acceptance user links are missing")
    return UUID(str(links.get("user_id") or "")), report


def _desired_row(
    *,
    capability: str,
    phase: str,
    activation: dict[str, Any],
    expires_at: datetime | None,
    cohort_user_id: UUID | None = None,
    cohort_user_ids: tuple[UUID, ...] | None = None,
) -> dict[str, Any]:
    state = "OFF"
    if phase == "google-auth-only" and capability == "google_auth":
        state = "ACCEPTANCE_COHORT"
    elif phase in {"staged-user-cohort", "formal-cohort"}:
        state = "ACCEPTANCE_COHORT"
    if phase == "emergency-off":
        state = "OFF"
    if state == "OFF":
        return {
            "state": state,
            "deployment_id": None,
            "runtime_bundle_id": None,
            "worker_image_digest": None,
            "release_activation_id": None,
            "target_manifest_sha256": None,
            "cohort_user_ids": [],
            "verified_identity_hashes": [],
            "expires_at": None,
        }
    cohort = cohort_user_ids or ((cohort_user_id,) if cohort_user_id else ())
    return {
        "state": state,
        "deployment_id": activation["api_deployment_id"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "worker_image_digest": activation["worker_image_digest"],
        "release_activation_id": activation["id"],
        "target_manifest_sha256": activation["manifest_sha256"],
        "cohort_user_ids": [str(user_id) for user_id in cohort],
        "verified_identity_hashes": [],
        "expires_at": expires_at,
    }


def apply_phase(
    database_url: str,
    *,
    phase: str,
    plan: dict[str, Any],
    approval: str,
    kind: str = "COMMERCIAL_7A",
    deployment_id: str | None,
    source_sha: str | None,
    binding_report: dict[str, Any] | None,
    cohort_user_id: UUID | None = None,
    cohort_user_ids: tuple[UUID, ...] | None = None,
) -> dict[str, Any]:
    validate_plan(plan)
    if kind == "GOOGLE_AUTH_ONLY" and phase not in GOOGLE_AUTH_ONLY_PHASES:
        raise ValueError("GOOGLE_AUTH_ONLY permits only Google auth acceptance or emergency OFF")
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-capability-activation",),
            )
            activation = _activation(
                cursor,
                deployment_id=deployment_id,
                source_sha=source_sha,
                approval=approval,
                kind=kind,
            )
            if phase != "emergency-off" and activation.get("phase") not in {
                "ACCEPTANCE_READY",
                "TARGET_ACCEPTED",
                "TARGET_PROMOTED",
                "PUBLIC_INVALIDATED",
            }:
                raise ValueError("activation plan phase is not ready for capability mutation")
            expiry = binding_report.get("expires_at") if binding_report else None
            cohort = cohort_user_ids or (
                (cohort_user_id,) if cohort_user_id is not None else ()
            )
            if phase in {"staged-user-cohort", "formal-cohort"}:
                if not cohort or len(set(cohort)) != len(cohort):
                    raise ValueError("full cohort requires distinct canonical users")
                expiries: list[datetime] = []
                for user_id in cohort:
                    cursor.execute(
                        """
                        SELECT expires_at FROM acceptance_identity_bindings
                        WHERE environment = 'production' AND deployment_id = %s
                          AND consumed_user_id = %s AND consumed_at IS NOT NULL
                          AND revoked_at IS NULL
                        ORDER BY expires_at DESC LIMIT 2
                        """,
                        (activation["api_deployment_id"], str(user_id)),
                    )
                    rows = cursor.fetchall()
                    if (
                        len(rows) != 1
                        or rows[0]["expires_at"] <= datetime.now(timezone.utc)
                    ):
                        raise ValueError(
                            "canonical user acceptance binding is missing or expired"
                        )
                    expiries.append(rows[0]["expires_at"])
                expiry = min(expiries)
            snapshots: dict[str, dict[str, str]] = {}
            for capability in CAPABILITIES:
                cursor.execute(
                    "SELECT * FROM ops_feature_flags WHERE environment = 'production' "
                    "AND capability = %s FOR UPDATE",
                    (capability,),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        """
                        INSERT INTO ops_feature_flags (
                          id, environment, capability, state, cohort_user_ids,
                          verified_identity_hashes, version
                        ) VALUES (%s, 'production', %s, 'OFF', '[]'::jsonb, '[]'::jsonb, 1)
                        RETURNING *
                        """,
                        (str(uuid4()), capability),
                    )
                    row = cursor.fetchone()
                current = dict(row)
                desired = _desired_row(
                    capability=capability,
                    phase=phase,
                    activation=activation,
                    cohort_user_id=cohort_user_id,
                    cohort_user_ids=cohort,
                    expires_at=expiry,
                )
                old_hash = _snapshot_hash(capability, current)
                unchanged = all(
                    current.get(key) == value for key, value in desired.items()
                )
                if unchanged:
                    snapshots[capability] = {"old": old_hash, "new": old_hash}
                    continue
                new_version = int(current["version"]) + 1
                updated = {**current, **desired, "version": new_version}
                new_hash = _snapshot_hash(capability, updated)
                cursor.execute(
                    """
                    UPDATE ops_feature_flags
                    SET state=%s, deployment_id=%s, runtime_bundle_id=%s,
                        worker_image_digest=%s, release_activation_id=%s,
                        target_manifest_sha256=%s, cohort_user_ids=%s,
                        verified_identity_hashes=%s, expires_at=%s, version=%s
                    WHERE id=%s AND version=%s
                    RETURNING id
                    """,
                    (
                        desired["state"], desired["deployment_id"],
                        desired["runtime_bundle_id"], desired["worker_image_digest"],
                        desired["release_activation_id"], desired["target_manifest_sha256"],
                        Json(desired["cohort_user_ids"]), Json(desired["verified_identity_hashes"]),
                        desired["expires_at"], new_version, current["id"], current["version"],
                    ),
                )
                if cursor.fetchone() is None:
                    raise ValueError("feature flag CAS lost its version fence")
                cursor.execute(
                    """
                    INSERT INTO ops_feature_flag_audits (
                      id, feature_flag_id, environment, capability, actor, reason,
                      old_state, new_state, old_snapshot_hash, new_snapshot_hash,
                      deployment_id, runtime_bundle_id, target_manifest_sha256, details_json
                    ) VALUES (%s,%s,'production',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        str(uuid4()), current["id"], capability,
                        f"release:{approval}", f"{kind} {phase}",
                        current["state"], desired["state"], old_hash, new_hash,
                        desired["deployment_id"], desired["runtime_bundle_id"],
                        desired["target_manifest_sha256"],
                        Json({"phase": phase, "version": new_version}),
                    ),
                )
                snapshots[capability] = {"old": old_hash, "new": new_hash}
    target = {
        capability: (
            "OFF"
            if phase == "emergency-off"
            else "ACCEPTANCE_COHORT"
            if phase in {"staged-user-cohort", "formal-cohort"}
            or (phase == "google-auth-only" and capability == "google_auth")
            else "OFF"
        )
        for capability in CAPABILITIES
    }
    return {
        "schema": "vowpic.activation-plan-report.v1",
        "passed": True,
        "phase": phase,
        "activation_id": str(activation["id"]),
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "deployment_id": activation["api_deployment_id"],
        "manifest_sha256": activation["manifest_sha256"],
        "target_states": target,
        "snapshot_hashes": snapshots,
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }


def transition_one_capability(
    database_url: str,
    *,
    activation_id: str,
    capability: str,
    expected_state: str,
    target_state: str,
    approval: str,
    reason: str,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor

    if capability not in CAPABILITIES or target_state not in {"ON", "OFF"}:
        raise ValueError("single-capability transition is invalid")
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-capability-activation",),
            )
            cursor.execute(
                "SELECT * FROM release_activations WHERE id = %s FOR UPDATE",
                (activation_id,),
            )
            activation_row = cursor.fetchone()
            if activation_row is None:
                raise ValueError("single-capability activation is missing")
            activation = dict(activation_row)
            if (
                activation.get("environment") != "production"
                or activation.get("kind") != "COMMERCIAL_7A"
                or activation.get("approval") != approval
                or activation.get("phase") not in {"PUBLIC_INVALIDATED", "ACTIVATED"}
            ):
                raise ValueError("single-capability activation coordinates are invalid")
            cursor.execute(
                "SELECT * FROM ops_feature_flags WHERE environment='production' "
                "AND capability=%s FOR UPDATE",
                (capability,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("single-capability flag row is missing")
            current = dict(row)
            if current.get("state") == target_state:
                return {
                    "capability": capability,
                    "old_state": target_state,
                    "new_state": target_state,
                    "old_snapshot_hash": _snapshot_hash(capability, current),
                    "new_snapshot_hash": _snapshot_hash(capability, current),
                }
            if current.get("state") != expected_state:
                raise ValueError("single-capability prior state is invalid")
            if target_state == "ON":
                expected_coordinates = {
                    "deployment_id": activation["api_deployment_id"],
                    "runtime_bundle_id": activation["runtime_bundle_id"],
                    "worker_image_digest": activation["worker_image_digest"],
                    "release_activation_id": activation["id"],
                    "target_manifest_sha256": activation["manifest_sha256"],
                }
                if any(current.get(key) != value for key, value in expected_coordinates.items()):
                    raise ValueError("single-capability cohort binding drifted")
                desired = {
                    **expected_coordinates,
                    "state": "ON",
                    "cohort_user_ids": [],
                    "verified_identity_hashes": [],
                    "expires_at": None,
                }
            else:
                desired = {
                    "state": "OFF",
                    "deployment_id": None,
                    "runtime_bundle_id": None,
                    "worker_image_digest": None,
                    "release_activation_id": None,
                    "target_manifest_sha256": None,
                    "cohort_user_ids": [],
                    "verified_identity_hashes": [],
                    "expires_at": None,
                }
            old_hash = _snapshot_hash(capability, current)
            new_version = int(current["version"]) + 1
            updated = {**current, **desired, "version": new_version}
            new_hash = _snapshot_hash(capability, updated)
            cursor.execute(
                """
                UPDATE ops_feature_flags
                SET state=%s, deployment_id=%s, runtime_bundle_id=%s,
                    worker_image_digest=%s, release_activation_id=%s,
                    target_manifest_sha256=%s, cohort_user_ids=%s,
                    verified_identity_hashes=%s, expires_at=%s, version=%s
                WHERE id=%s AND version=%s RETURNING id
                """,
                (
                    desired["state"], desired["deployment_id"],
                    desired["runtime_bundle_id"], desired["worker_image_digest"],
                    desired["release_activation_id"], desired["target_manifest_sha256"],
                    Json(desired["cohort_user_ids"]), Json(desired["verified_identity_hashes"]),
                    desired["expires_at"], new_version, current["id"], current["version"],
                ),
            )
            if cursor.fetchone() is None:
                raise ValueError("single-capability CAS lost its version fence")
            cursor.execute(
                """
                INSERT INTO ops_feature_flag_audits (
                  id, feature_flag_id, environment, capability, actor, reason,
                  old_state, new_state, old_snapshot_hash, new_snapshot_hash,
                  deployment_id, runtime_bundle_id, target_manifest_sha256, details_json
                ) VALUES (%s,%s,'production',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    str(uuid4()), current["id"], capability, f"release:{approval}", reason,
                    current["state"], desired["state"], old_hash, new_hash,
                    desired["deployment_id"], desired["runtime_bundle_id"],
                    desired["target_manifest_sha256"],
                    Json({"single_capability": True, "version": new_version}),
                ),
            )
    return {
        "capability": capability,
        "old_state": current["state"],
        "new_state": target_state,
        "old_snapshot_hash": old_hash,
        "new_snapshot_hash": new_hash,
    }


def _write_create_once(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=PHASES)
    parser.add_argument("--kind", choices=ACTIVATION_KINDS, default="COMMERCIAL_7A")
    parser.add_argument("--activation-plan", default="release/activation-plan.json")
    parser.add_argument("--manifest")
    parser.add_argument("--deployment-id")
    parser.add_argument("--source-sha")
    parser.add_argument("--binding-report")
    parser.add_argument("--canonical-users-report")
    parser.add_argument(
        "--additional-canonical-users-report",
        action="append",
        default=[],
    )
    parser.add_argument("--release-resolution-report")
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--signing-key-env", default="ACCEPTANCE_EVIDENCE_SIGNING_KEY")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        approval = os.environ.get(args.approval_id_env, "").strip()
        if not approval or len(approval) > 160:
            raise ValueError("activation plan approval is required")
        source_sha = str(args.source_sha or "").strip() or None
        deployment_id = args.deployment_id
        manifest = None
        if args.manifest:
            manifest = _load_object(args.manifest)
            manifest_source_sha = str(manifest.get("source_sha") or "")
            if source_sha and source_sha != manifest_source_sha:
                raise ValueError("activation plan source SHA conflicts with the manifest")
            source_sha = manifest_source_sha
            if (
                manifest.get("schema") != "vowpic.bundle-manifest.v1"
                or manifest.get("release_role") != "COMMERCIAL_7A"
                or manifest.get("staged_target_deployment_id") != deployment_id
            ):
                raise ValueError("activation plan manifest binding is invalid")
        elif args.release_resolution_report:
            resolution = _load_object(args.release_resolution_report)
            resolution_source_sha = str(resolution.get("source_sha") or "")
            if source_sha and source_sha != resolution_source_sha:
                raise ValueError("activation plan source SHA conflicts with the resolution")
            source_sha = resolution_source_sha
            deployment_id = str(resolution.get("api_deployment_id") or "")
        elif args.kind == "GOOGLE_AUTH_ONLY":
            if not source_sha or not deployment_id:
                raise ValueError("GOOGLE_AUTH_ONLY requires exact source and deployment coordinates")
        elif args.phase != "emergency-off":
            raise ValueError("activation plan manifest is required")

        if args.kind == "GOOGLE_AUTH_ONLY" and args.phase not in GOOGLE_AUTH_ONLY_PHASES:
            raise ValueError("GOOGLE_AUTH_ONLY permits only Google auth acceptance or emergency OFF")

        binding = None
        cohort_user_ids: tuple[UUID, ...] = ()
        if args.phase == "google-auth-only":
            if not args.binding_report:
                raise ValueError("Google auth cohort requires a binding report")
        signing_key = os.environ.get(args.signing_key_env, "").encode("utf-8")

        plan = _load_object(args.activation_plan)
        database_url = os.environ.get(args.database_url_env, "")
        if args.phase == "google-auth-only":
            # Activation is loaded once inside the transaction. Validate the report there
            # after obtaining the exact deployment coordinates.
            pass
        if args.phase in {"staged-user-cohort", "formal-cohort"}:
            if not args.canonical_users_report:
                raise ValueError("full cohort requires a canonical user report")

        # Read the exact activation once for report validation without granting authority.
        import psycopg2
        from psycopg2.extras import RealDictCursor
        with psycopg2.connect(_database_url(database_url)) as connection:
            connection.set_session(readonly=True, autocommit=False)
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                activation = _activation(
                    cursor,
                    deployment_id=deployment_id,
                    source_sha=source_sha,
                    approval=approval,
                    kind=args.kind,
                    for_update=False,
                )
        if manifest is not None and (
            activation.get("manifest_sha256")
            != hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest()
        ):
            raise ValueError("activation plan manifest SHA-256 mismatch")
        if args.phase == "google-auth-only":
            binding = _binding_report(args.binding_report, activation=activation)
        elif args.phase in {"staged-user-cohort", "formal-cohort"}:
            reports = [
                args.canonical_users_report,
                *args.additional_canonical_users_report,
            ]
            cohort_user_ids = tuple(
                _cohort_user(
                    report_path,
                    activation=activation,
                    signing_key=signing_key,
                )[0]
                for report_path in reports
            )
            if len(set(cohort_user_ids)) != len(cohort_user_ids):
                raise ValueError("canonical user reports must identify distinct users")

        report = apply_phase(
            database_url,
            phase=args.phase,
            plan=plan,
            approval=approval,
            kind=args.kind,
            deployment_id=deployment_id,
            source_sha=source_sha,
            binding_report=binding,
            cohort_user_ids=cohort_user_ids,
        )
        _write_create_once(Path(args.output), report)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
