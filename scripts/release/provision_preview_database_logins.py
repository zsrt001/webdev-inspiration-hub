#!/usr/bin/env python3
"""Rotate Preview base logins and seal their URLs to one ephemeral public key."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from typing import Any
from urllib.parse import unquote, urlsplit

import asyncpg
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import psycopg2
import httpx


RELEASE_DIR = Path(__file__).resolve().parent
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

from production_database_login_proof import RUNTIME_LOGIN, WRITER_LOGIN  # noqa: E402
from provision_production_database_logins import (  # noqa: E402
    database_url_for_login,
    load_database_role_contract,
    prove_database_logins_after_pooler_propagation,
)


ENVELOPE_SCHEMA = "vowpic.preview-database-credentials-envelope.v1"
PROOF_SCHEMA = "vowpic.preview-database-login-repair-proof.v1"
PREFLIGHT_SCHEMA = "vowpic.preview-management-database-preflight.v1"
PROJECT_STATUS_SCHEMA = "vowpic.preview-management-project-status.v1"
RECOVERY_SCHEMA = "vowpic.preview-management-database-recovery.v1"
PLAINTEXT_SCHEMA = "vowpic.preview-database-credentials.v1"
TEMPLATE_LOGIN = "vowpic_inventory_login"
MANAGEMENT_API = "https://api.supabase.com/v1"
HELPER_EXPIRES_AT = "2026-08-03 00:00:00+00"


def load_delivery_public_key(value: str) -> tuple[rsa.RSAPublicKey, str]:
    try:
        raw = base64.b64decode(value.strip(), validate=True)
        key = serialization.load_der_public_key(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("delivery public key is invalid") from exc
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 3072:
        raise ValueError("delivery public key must be RSA-3072 or stronger")
    return key, hashlib.sha256(raw).hexdigest()


def seal_credentials(
    *,
    public_key: rsa.RSAPublicKey,
    public_key_sha256: str,
    source_sha: str,
    runtime_url: str,
    writer_url: str,
) -> dict[str, str]:
    header = {
        "schema": ENVELOPE_SCHEMA,
        "algorithm": "RSA-OAEP-SHA256+AES-256-GCM",
        "source_sha": source_sha,
        "public_key_sha256": public_key_sha256,
    }
    associated_data = json.dumps(
        header,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    plaintext = json.dumps(
        {
            "schema": PLAINTEXT_SCHEMA,
            "source_sha": source_sha,
            "runtime_url": runtime_url,
            "writer_url": writer_url,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data)
    encrypted_key = public_key.encrypt(
        key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return {
        **header,
        "associated_data_b64": base64.b64encode(associated_data).decode("ascii"),
        "encrypted_key_b64": base64.b64encode(encrypted_key).decode("ascii"),
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
    }


def _pooler_project_ref(url: str, expected_login: str) -> str:
    parsed = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    username = unquote(parsed.username or "")
    login, separator, project_ref = username.partition(".")
    if (
        parsed.scheme != "postgresql"
        or login != expected_login
        or separator != "."
        or not re.fullmatch(r"[a-z0-9]{20}", project_ref)
        or not (parsed.hostname or "").endswith(".pooler.supabase.com")
        or parsed.port not in {5432, 6543}
    ):
        raise ValueError(f"{expected_login} Preview pooler URL is invalid")
    return project_ref


def _helper_name(source_sha: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("source SHA is invalid")
    return f"vowpic_preview_login_repair_{source_sha[:12]}"


def _rotation_helper_sql(helper_name: str) -> str:
    if not re.fullmatch(r"vowpic_preview_login_repair_[0-9a-f]{12}", helper_name):
        raise ValueError("Preview rotation helper name is invalid")
    return f"""
BEGIN;
CREATE OR REPLACE FUNCTION public.{helper_name}(
    runtime_password text,
    writer_password text
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $repair$
BEGIN
    IF clock_timestamp() >= TIMESTAMPTZ '{HELPER_EXPIRES_AT}' THEN
        RAISE EXCEPTION 'one-time Preview database login repair has expired';
    END IF;
    IF length(runtime_password) < 64
       OR length(writer_password) < 64
       OR runtime_password = writer_password THEN
        RAISE EXCEPTION 'Preview database login repair passwords are invalid';
    END IF;
    IF (
        SELECT count(*)
        FROM pg_roles
        WHERE rolname IN (
            'vowpic_runtime',
            'vowpic_control_writer',
            'vowpic_identity_service',
            'vowpic_app_runtime',
            'vowpic_control_writer_login'
        )
    ) <> 5 THEN
        RAISE EXCEPTION 'Preview database role baseline is incomplete';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname IN (
            'vowpic_runtime',
            'vowpic_control_writer',
            'vowpic_identity_service'
        )
          AND (
              rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
              OR rolreplication OR rolbypassrls OR NOT rolinherit
          )
    ) THEN
        RAISE EXCEPTION 'Preview database group role baseline is unsafe';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname IN ('vowpic_app_runtime', 'vowpic_control_writer_login')
          AND (
              NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
              OR rolreplication OR rolbypassrls OR NOT rolinherit
          )
    ) THEN
        RAISE EXCEPTION 'Preview database login baseline is unsafe';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_auth_members membership
        JOIN pg_roles member ON member.oid = membership.member
        JOIN pg_roles parent ON parent.oid = membership.roleid
        WHERE (
            member.rolname = 'vowpic_app_runtime'
            AND parent.rolname NOT IN ('vowpic_runtime', 'vowpic_identity_service')
        ) OR (
            member.rolname = 'vowpic_control_writer_login'
            AND parent.rolname <> 'vowpic_control_writer'
        )
    ) THEN
        RAISE EXCEPTION 'Preview database login has an unexpected membership';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_roles role
        WHERE role.rolname IN ('vowpic_app_runtime', 'vowpic_control_writer_login')
          AND (
              EXISTS (SELECT 1 FROM pg_database item WHERE item.datdba = role.oid)
              OR EXISTS (SELECT 1 FROM pg_namespace item WHERE item.nspowner = role.oid)
              OR EXISTS (SELECT 1 FROM pg_class item WHERE item.relowner = role.oid)
              OR EXISTS (SELECT 1 FROM pg_proc item WHERE item.proowner = role.oid)
          )
    ) THEN
        RAISE EXCEPTION 'Preview database login owns database objects';
    END IF;
    EXECUTE format(
        'ALTER ROLE vowpic_app_runtime WITH LOGIN PASSWORD %L VALID UNTIL ''infinity''',
        runtime_password
    );
    EXECUTE format(
        'ALTER ROLE vowpic_control_writer_login WITH LOGIN PASSWORD %L VALID UNTIL ''infinity''',
        writer_password
    );
    REVOKE vowpic_control_writer FROM vowpic_app_runtime;
    REVOKE vowpic_runtime FROM vowpic_control_writer_login;
    REVOKE vowpic_identity_service FROM vowpic_control_writer_login;
    GRANT vowpic_runtime TO vowpic_app_runtime;
    GRANT vowpic_identity_service TO vowpic_app_runtime;
    GRANT vowpic_control_writer TO vowpic_control_writer_login;
    RETURN true;
END
$repair$;
REVOKE ALL ON FUNCTION public.{helper_name}(text, text) FROM PUBLIC;
COMMIT;
""".strip()


def _management_query(
    *,
    client: httpx.Client,
    token: str,
    project_ref: str,
    query: str,
    parameters: list[str],
    read_only: bool = False,
) -> Any:
    if not token.strip():
        raise ValueError("Supabase Management token is missing")
    if not re.fullmatch(r"[a-z0-9]{20}", project_ref):
        raise ValueError("Supabase Preview project ref is invalid")
    body: dict[str, Any] = {"query": query}
    if parameters:
        body["parameters"] = parameters
    endpoint = "database/query/read-only" if read_only else "database/query"
    response = client.post(
        f"{MANAGEMENT_API}/projects/{project_ref}/{endpoint}",
        headers={
            "Authorization": f"Bearer {token.strip()}",
            "Content-Type": "application/json",
        },
        json=body,
    )
    if response.status_code != 201:
        channel = "read-only" if read_only else "write"
        raise ValueError(
            "Supabase Management "
            f"{channel} database query failed with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Supabase Management database query returned invalid JSON") from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise ValueError("Supabase Management database query returned an error")
    return payload


def probe_management_database(
    *,
    client: httpx.Client,
    token: str,
    project_ref: str,
) -> dict[str, str]:
    _management_query(
        client=client,
        token=token,
        project_ref=project_ref,
        query="SELECT 1 AS ok",
        parameters=[],
        read_only=True,
    )
    return {"schema": PREFLIGHT_SCHEMA, "state": "AVAILABLE"}


def read_management_project_status(
    *,
    client: httpx.Client,
    token: str,
    project_ref: str,
) -> dict[str, Any]:
    if not token.strip():
        raise ValueError("Supabase Management token is missing")
    if not re.fullmatch(r"[a-z0-9]{20}", project_ref):
        raise ValueError("Supabase Preview project ref is invalid")
    response = client.get(
        f"{MANAGEMENT_API}/projects/{project_ref}",
        headers={
            "Authorization": f"Bearer {token.strip()}",
            "Accept": "application/json",
        },
    )
    if response.status_code != 200:
        raise ValueError(
            f"Supabase Management project status failed with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Supabase Management project status returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("ref") != project_ref:
        raise ValueError("Supabase Management project status returned an invalid payload")
    status = str(payload.get("status") or "").strip()
    if not status:
        raise ValueError("Supabase Management project status is incomplete")
    return {
        "schema": PROJECT_STATUS_SCHEMA,
        "state": "OBSERVED",
        "project_healthy": status == "ACTIVE_HEALTHY",
        "project_status": status,
    }


def recover_management_database(
    *,
    client: httpx.Client,
    token: str,
    project_ref: str,
    attempts: int = 60,
    interval_seconds: float = 10.0,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    before = read_management_project_status(
        client=client,
        token=token,
        project_ref=project_ref,
    )
    if before["project_healthy"] and before["project_status"] == "ACTIVE_HEALTHY":
        try:
            probe = probe_management_database(
                client=client,
                token=token,
                project_ref=project_ref,
            )
        except ValueError as exc:
            if "HTTP 544" not in str(exc):
                raise
        else:
            return {
                "schema": RECOVERY_SCHEMA,
                "state": "ALREADY_HEALTHY",
                "recovery_action": "none",
                "restart_requested": False,
                "project_status_before": before["project_status"],
                "project_status_after": before["project_status"],
                "read_only_probe": probe["state"],
            }
    recovery_action = "restore" if before["project_status"] == "INACTIVE" else "restart"
    response = client.post(
        f"{MANAGEMENT_API}/projects/{project_ref}/{recovery_action}",
        headers={
            "Authorization": f"Bearer {token.strip()}",
            "Content-Type": "application/json",
        },
    )
    if response.status_code != 200:
        raise ValueError(
            "Supabase Preview project "
            f"{recovery_action} failed with HTTP {response.status_code}"
        )
    last_status = f"{recovery_action}_requested"
    for attempt in range(attempts):
        if attempt:
            sleep(interval_seconds)
        try:
            after = read_management_project_status(
                client=client,
                token=token,
                project_ref=project_ref,
            )
        except ValueError as exc:
            message = str(exc)
            transient = any(
                f"HTTP {status}" in message
                for status in (500, 502, 503, 504, 544)
            )
            if not transient:
                raise
            last_status = message.rsplit(" ", 1)[-1]
            continue
        last_status = str(after["project_status"])
        if after["project_healthy"] and last_status == "ACTIVE_HEALTHY":
            probe = probe_management_database(
                client=client,
                token=token,
                project_ref=project_ref,
            )
            return {
                "schema": RECOVERY_SCHEMA,
                "state": "RECOVERED",
                "recovery_action": recovery_action,
                "restart_requested": recovery_action == "restart",
                "project_status_before": before["project_status"],
                "project_status_after": last_status,
                "read_only_probe": probe["state"],
            }
    raise ValueError(
        "Supabase Preview database did not recover within the bounded wait; "
        f"last status {last_status}"
    )


def rotate_preview_logins_via_management_api(
    *,
    client: httpx.Client,
    token: str,
    project_ref: str,
    source_sha: str,
    runtime_password: str,
    writer_password: str,
) -> dict[str, Any]:
    helper_name = _helper_name(source_sha)
    created = False
    rotated = False
    try:
        _management_query(
            client=client,
            token=token,
            project_ref=project_ref,
            query=_rotation_helper_sql(helper_name),
            parameters=[],
        )
        created = True
        _management_query(
            client=client,
            token=token,
            project_ref=project_ref,
            query=f"SELECT public.{helper_name}($1::text, $2::text) AS rotated",
            parameters=[runtime_password, writer_password],
        )
        rotated = True
    finally:
        if created:
            _management_query(
                client=client,
                token=token,
                project_ref=project_ref,
                query=f"DROP FUNCTION IF EXISTS public.{helper_name}(text, text)",
                parameters=[],
            )
    if not rotated:
        raise ValueError("Preview database login rotation did not complete")
    return {
        "credential_rotation": "supabase_management_api_parameterized_helper",
        "helper_dropped": True,
    }


async def _asyncpg_login_facts(url: str, expected_login: str) -> dict[str, Any]:
    connection = await asyncpg.connect(
        url.replace("postgresql+asyncpg://", "postgresql://", 1),
        timeout=20,
        command_timeout=20,
        statement_cache_size=0,
    )
    try:
        row = await connection.fetchrow(
            "SELECT session_user, current_user, current_database() AS database"
        )
        if row is None:
            raise ValueError(f"{expected_login} asyncpg identity is missing")
        facts = dict(row)
        if (
            facts.get("session_user") != expected_login
            or facts.get("current_user") != expected_login
        ):
            raise ValueError(f"{expected_login} asyncpg identity is unexpected")
        if expected_login == RUNTIME_LOGIN:
            revisions = await connection.fetch(
                "SELECT version_num FROM public.alembic_version"
            )
            if not revisions:
                raise ValueError("runtime asyncpg schema revision is missing")
            facts["schema_revision_count"] = len(revisions)
        return facts
    finally:
        await connection.close()


async def prove_asyncpg_logins(runtime_url: str, writer_url: str) -> dict[str, Any]:
    runtime, writer = await asyncio.gather(
        _asyncpg_login_facts(runtime_url, RUNTIME_LOGIN),
        _asyncpg_login_facts(writer_url, WRITER_LOGIN),
    )
    if runtime["database"] != writer["database"]:
        raise ValueError("Preview base logins do not target one database")
    return {
        "database": runtime["database"],
        "runtime_session_user": runtime["session_user"],
        "runtime_current_user": runtime["current_user"],
        "runtime_schema_revision_count": runtime["schema_revision_count"],
        "writer_session_user": writer["session_user"],
        "writer_current_user": writer["current_user"],
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template-url-env", default="PREVIEW_CONTROL_READ_DATABASE_URL")
    parser.add_argument("--management-token-env", default="SUPABASE_MANAGEMENT_TOKEN")
    parser.add_argument("--project-ref-env", default="SUPABASE_PROJECT_REF")
    parser.add_argument("--public-key-env", default="DELIVERY_PUBLIC_KEY_B64")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--management-preflight-only", action="store_true")
    operation.add_argument("--project-status-only", action="store_true")
    operation.add_argument("--restart-unhealthy-project", action="store_true")
    parser.add_argument("--source-sha")
    parser.add_argument("--encrypted-output", type=Path)
    parser.add_argument("--proof-output", type=Path)
    args = parser.parse_args()
    template_url = os.environ.get(args.template_url_env, "").strip()
    management_token = os.environ.get(args.management_token_env, "").strip()
    project_ref = os.environ.get(args.project_ref_env, "").strip().lower()
    public_key_value = os.environ.get(args.public_key_env, "").strip()
    runtime_url = writer_url = runtime_password = writer_password = ""
    try:
        if not management_token:
            raise ValueError("Supabase Management token is missing")
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            if args.project_status_only:
                status = read_management_project_status(
                    client=client,
                    token=management_token,
                    project_ref=project_ref,
                )
                print(json.dumps(status, sort_keys=True))
                return 0
            if args.restart_unhealthy_project:
                recovery = recover_management_database(
                    client=client,
                    token=management_token,
                    project_ref=project_ref,
                )
                print(json.dumps(recovery, sort_keys=True))
                return 0
            preflight = probe_management_database(
                client=client,
                token=management_token,
                project_ref=project_ref,
            )
        if args.management_preflight_only:
            print(json.dumps(preflight, sort_keys=True))
            return 0
        if not template_url:
            raise ValueError("Preview control-read URL template is missing")
        if not args.source_sha:
            raise ValueError("source SHA is required for credential repair")
        if args.encrypted_output is None or args.proof_output is None:
            raise ValueError("credential repair output paths are required")
        template_ref = _pooler_project_ref(template_url, TEMPLATE_LOGIN)
        if template_ref != project_ref:
            raise ValueError("Preview control-read URL and project ref do not match")
        public_key, public_key_sha256 = load_delivery_public_key(public_key_value)
        runtime_password = secrets.token_urlsafe(48)
        writer_password = secrets.token_urlsafe(48)
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            rotation = rotate_preview_logins_via_management_api(
                client=client,
                token=management_token,
                project_ref=project_ref,
                source_sha=args.source_sha,
                runtime_password=runtime_password,
                writer_password=writer_password,
            )
        runtime_url = database_url_for_login(
            template_url, RUNTIME_LOGIN, runtime_password
        )
        writer_url = database_url_for_login(
            template_url, WRITER_LOGIN, writer_password
        )
        runtime_ref = _pooler_project_ref(runtime_url, RUNTIME_LOGIN)
        writer_ref = _pooler_project_ref(writer_url, WRITER_LOGIN)
        if runtime_ref != writer_ref:
            raise ValueError("Preview base logins target different projects")
        envelope = seal_credentials(
            public_key=public_key,
            public_key_sha256=public_key_sha256,
            source_sha=args.source_sha,
            runtime_url=runtime_url,
            writer_url=writer_url,
        )
        _write_create_once(args.encrypted_output, envelope)
        business_privileges = load_database_role_contract()
        role_proof = prove_database_logins_after_pooler_propagation(
            runtime_url,
            writer_url,
            business_privileges,
        )
        asyncpg_proof = asyncio.run(prove_asyncpg_logins(runtime_url, writer_url))
        proof = {
            "schema": PROOF_SCHEMA,
            "state": "PROVISIONED",
            "source_sha": args.source_sha,
            "public_key_sha256": public_key_sha256,
            "preview_project_ref_sha256": hashlib.sha256(
                runtime_ref.encode("ascii")
            ).hexdigest(),
            "runtime_login": RUNTIME_LOGIN,
            "control_writer_login": WRITER_LOGIN,
            "database": str(asyncpg_proof["database"]),
            "credential_rotation": rotation["credential_rotation"],
            "helper_dropped": rotation["helper_dropped"],
            "database_role_contract": sorted(role_proof),
            "asyncpg": asyncpg_proof,
        }
        _write_create_once(args.proof_output, proof)
        print(json.dumps({"schema": PROOF_SCHEMA, "state": "PROVISIONED"}))
        return 0
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        psycopg2.Error,
        asyncpg.PostgresError,
        httpx.HTTPError,
    ) as exc:
        detail = str(exc)
        for secret_value in (
            template_url,
            management_token,
            runtime_url,
            writer_url,
            runtime_password,
            writer_password,
        ):
            if secret_value:
                detail = detail.replace(secret_value, "[REDACTED]")
        detail = re.sub(r"postgres(?:ql)?://\S+", "[REDACTED]", detail)
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
