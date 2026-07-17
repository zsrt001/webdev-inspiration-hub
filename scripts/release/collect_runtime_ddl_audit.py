#!/usr/bin/env python3
"""Collect a signed, deployment-bound runtime DDL audit from PostgreSQL stats."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

import httpx
import psycopg2


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.verify_safe_baseline import (  # noqa: E402
    DDL_AUDIT_COVERAGE,
    _parse_protected_header,
    _verify_http,
    compute_evidence_hmac,
)


def _sync_database_url(value: str) -> str:
    return value.strip().replace("postgresql+asyncpg://", "postgresql://", 1)


def _database_audit_counts(database_url: str) -> tuple[int, int]:
    with psycopg2.connect(_sync_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT session_user, current_user,
                       role.rolsuper, role.rolcreatedb, role.rolcreaterole,
                       role.rolreplication, role.rolbypassrls,
                       pg_has_role(session_user, 'vowpic_migration_owner', 'MEMBER')
                FROM pg_roles role
                WHERE role.rolname = session_user
                """
            )
            role = cursor.fetchone()
            if role is None or role[0] != "vowpic_migration_login":
                raise ValueError("runtime DDL audit requires the dedicated migration login")
            if role[1] != "vowpic_migration_owner" or any(role[2:7]) or role[7] is not True:
                raise ValueError("runtime DDL audit migration authority is not least privilege")
            cursor.execute(
                "SELECT statement_count, ddl_statement_count "
                "FROM public.vowpic_runtime_statement_audit()"
            )
            counts = cursor.fetchone()
    if counts is None or any(type(value) is not int for value in counts):
        raise ValueError("runtime DDL statement audit returned invalid counts")
    return int(counts[0]), int(counts[1])


def collect_runtime_ddl_audit(
    *,
    base_url: str,
    protected_headers: dict[str, str],
    cleanup_token: str,
    database_url: str,
    source_sha: str,
    runtime_bundle_id: str,
    deployment_id: str,
    workflow_run_id: str,
    workflow_attempt: int,
    hmac_key: bytes,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    before_statements, before_ddl = _database_audit_counts(database_url)
    if before_ddl != 0:
        raise ValueError("runtime login already has recorded DDL statements")
    http_evidence = _verify_http(
        base_url,
        protected_headers=protected_headers,
        cleanup_token=cleanup_token,
        expected_source_sha=source_sha,
        expected_runtime_bundle_id=runtime_bundle_id,
        expected_deployment_id=deployment_id,
    )
    with httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=20.0,
        follow_redirects=False,
        headers={"User-Agent": "vowpic-runtime-ddl-auditor/1", **protected_headers},
    ) as client:
        readiness = client.get("/api/v1/ops/readiness")
    if readiness.status_code not in {200, 503}:
        raise ValueError(f"runtime readiness probe returned {readiness.status_code}")
    after_statements, after_ddl = _database_audit_counts(database_url)
    observed_statements = after_statements - before_statements
    if observed_statements <= 0:
        raise ValueError("runtime statement recorder observed no deployment statements")
    if after_ddl != 0:
        raise ValueError("runtime statement recorder observed DDL")
    report: dict[str, Any] = {
        "schema_version": "vowpic.runtime-ddl-audit.v1",
        "passed": True,
        "source_sha": source_sha,
        "runtime_bundle_id": runtime_bundle_id,
        "deployment_id": deployment_id,
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": workflow_attempt,
        "generated_at": generated_at.isoformat(),
        "expires_at": (generated_at + timedelta(minutes=45)).isoformat(),
        "coverage": sorted(DDL_AUDIT_COVERAGE),
        "statement_count": observed_statements,
        "ddl_statement_count": 0,
        "readiness_status": readiness.status_code,
        "route_statuses": {
            "guarded": {
                item["name"]: item["status"] for item in http_evidence["guarded_routes"]
            },
            "retired": {
                item["name"]: item["status"] for item in http_evidence["retired_routes"]
            },
            "webhook": http_evidence["invalid_webhook_status"],
            "logout": http_evidence["logout_status"],
            "cleanup": http_evidence["cleanup_status"],
        },
    }
    report["signature_hmac_sha256"] = compute_evidence_hmac(report, hmac_key)
    return report


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--deployment-bypass-header-env")
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--cleanup-token-env", default="CLEANUP_CRON_TOKEN")
    parser.add_argument("--hmac-key-env", default="RUNTIME_AUDIT_HMAC_KEY")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--runtime-bundle-id", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True, type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    database_url = os.environ.get(args.database_url_env, "").strip()
    cleanup_token = os.environ.get(args.cleanup_token_env, "").strip()
    hmac_key = os.environ.get(args.hmac_key_env, "").encode("utf-8")
    if not database_url or not cleanup_token or len(hmac_key) < 32:
        print("NOT_RUN: protected database, cleanup, and audit inputs are required", file=sys.stderr)
        return 3
    try:
        report = collect_runtime_ddl_audit(
            base_url=args.base_url,
            protected_headers=_parse_protected_header(args.deployment_bypass_header_env),
            cleanup_token=cleanup_token,
            database_url=database_url,
            source_sha=args.source_sha,
            runtime_bundle_id=args.runtime_bundle_id,
            deployment_id=args.deployment_id,
            workflow_run_id=args.workflow_run_id,
            workflow_attempt=args.workflow_attempt,
            hmac_key=hmac_key,
        )
        _write_create_once(Path(args.output), report)
        print(json.dumps({
            "state": "PASS",
            "statement_count": report["statement_count"],
            "ddl_statement_count": report["ddl_statement_count"],
        }))
        return 0
    except (ValueError, OSError, psycopg2.Error, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
