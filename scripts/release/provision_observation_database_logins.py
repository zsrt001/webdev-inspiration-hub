#!/usr/bin/env python3
"""Provision, prove, and publish the two dedicated observation database logins."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit

import psycopg2
from psycopg2.extras import RealDictCursor


RELEASE_DIR = Path(__file__).resolve().parent
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

from production_database_login_proof import (  # noqa: E402
    OBSERVATION_READER_GROUP,
    OBSERVATION_READER_LOGIN,
    OBSERVATION_WRITER_GROUP,
    OBSERVATION_WRITER_LOGIN,
    prove_observation_database_logins,
)
from provision_production_database_logins import (  # noqa: E402
    POOLER_AUTH_RETRY_DELAYS_SECONDS,
    _is_pooler_password_propagation_failure,
    _provision_login,
    _revoke_direct_login_privileges,
    _sync_database_url,
    _validate_group,
    database_url_for_login,
)


OBSERVATION_GITHUB_SECRETS = {
    "OBSERVATION_READ_DATABASE_URL": OBSERVATION_READER_LOGIN,
    "OBSERVATION_WRITE_DATABASE_URL": OBSERVATION_WRITER_LOGIN,
}
OBSERVATION_SCHEMA = "20260710_0020"


def _require_observation_schema(cursor: RealDictCursor) -> str:
    cursor.execute("SELECT version_num FROM public.alembic_version")
    row = cursor.fetchone() or {}
    schema_revision = str(row.get("version_num") or "")
    if schema_revision != OBSERVATION_SCHEMA:
        raise ValueError(
            "observation database login provisioning requires schema "
            f"{OBSERVATION_SCHEMA}; current schema is "
            f"{schema_revision or 'missing'}"
        )
    return schema_revision


def _resolve_cli(value: str) -> str:
    candidate = Path(value)
    if candidate.is_file():
        return str(candidate)
    resolved = shutil.which(value)
    if not resolved:
        raise ValueError("GitHub CLI does not exist")
    return resolved


def _run_gh(
    args: list[str],
    *,
    stdin: str | None = None,
    redact: tuple[str, ...] = (),
) -> str:
    completed = subprocess.run(
        args,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return completed.stdout
    detail = (completed.stderr or completed.stdout or "GitHub CLI failed").strip()
    for secret in redact:
        if secret:
            detail = detail.replace(secret, "[REDACTED]")
    raise ValueError(
        f"GitHub CLI failed with exit {completed.returncode}: {detail[:500]}"
    )


def publish_github_observation_database_urls(
    *,
    github_cli: str,
    repository: str,
    environment: str,
    reader_url: str,
    writer_url: str,
) -> dict[str, dict[str, str]]:
    cli = _resolve_cli(github_cli)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("GitHub repository is invalid")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", environment):
        raise ValueError("GitHub environment is invalid")
    redactions = (reader_url, writer_url)
    for name, value in (
        ("OBSERVATION_READ_DATABASE_URL", reader_url),
        ("OBSERVATION_WRITE_DATABASE_URL", writer_url),
    ):
        _run_gh(
            [
                cli,
                "secret",
                "set",
                name,
                "--repo",
                repository,
                "--env",
                environment,
            ],
            stdin=value,
            redact=redactions,
        )
    raw = _run_gh(
        [
            cli,
            "secret",
            "list",
            "--repo",
            repository,
            "--env",
            environment,
            "--json",
            "name,updatedAt",
        ],
        redact=redactions,
    )
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("GitHub Environment secret metadata is invalid")
    evidence: dict[str, dict[str, str]] = {}
    for name in OBSERVATION_GITHUB_SECRETS:
        matches = [item for item in payload if item.get("name") == name]
        if len(matches) != 1 or not str(matches[0].get("updatedAt") or ""):
            raise ValueError(
                f"GitHub Environment did not return exactly one {name}"
            )
        evidence[name] = {
            "environment": environment,
            "updated_at": str(matches[0]["updatedAt"]),
        }
    return evidence


def prove_observation_logins_after_pooler_propagation(
    reader_url: str,
    writer_url: str,
) -> dict[str, dict[str, Any]]:
    role_urls = (reader_url, writer_url)
    for attempt, delay in enumerate(POOLER_AUTH_RETRY_DELAYS_SECONDS):
        if delay:
            time.sleep(delay)
        try:
            return prove_observation_database_logins(reader_url, writer_url)
        except psycopg2.OperationalError as exc:
            is_last_attempt = attempt + 1 == len(
                POOLER_AUTH_RETRY_DELAYS_SECONDS
            )
            if is_last_attempt or not _is_pooler_password_propagation_failure(
                exc,
                role_urls,
            ):
                raise
    raise AssertionError("unreachable pooler propagation retry boundary")


def provision_observation_database_logins(
    database_url: str,
) -> tuple[str, str, dict[str, Any]]:
    reader_password = secrets.token_urlsafe(48)
    writer_password = secrets.token_urlsafe(48)
    normalized = _sync_database_url(database_url)
    with psycopg2.connect(
        normalized,
        cursor_factory=RealDictCursor,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT session_user, current_user, current_database(), "
                "role.rolsuper AS is_superuser "
                "FROM pg_roles AS role WHERE role.rolname = session_user"
            )
            authority = dict(cursor.fetchone() or {})
            if authority.get("session_user") in {
                OBSERVATION_READER_LOGIN,
                OBSERVATION_WRITER_LOGIN,
            }:
                raise ValueError(
                    "observation provisioning requires an independent authority"
                )
            schema_revision = _require_observation_schema(cursor)
            _validate_group(cursor, OBSERVATION_READER_GROUP)
            _validate_group(cursor, OBSERVATION_WRITER_GROUP)
            cursor.execute(
                "SELECT to_regprocedure("
                "'public.vowpic_rotate_observation_database_logins(text,text)')"
            )
            rotation_function = cursor.fetchone()["to_regprocedure"]
            if (
                rotation_function is not None
                and authority.get("session_user") == "vowpic_migration_login"
            ):
                cursor.execute(
                    "SELECT public.vowpic_rotate_observation_database_logins(%s, %s)",
                    (reader_password, writer_password),
                )
                credential_rotation = "scoped_security_definer"
            elif authority.get("is_superuser"):
                _provision_login(
                    cursor,
                    login=OBSERVATION_READER_LOGIN,
                    password=reader_password,
                    required_groups=(OBSERVATION_READER_GROUP,),
                )
                _provision_login(
                    cursor,
                    login=OBSERVATION_WRITER_LOGIN,
                    password=writer_password,
                    required_groups=(OBSERVATION_WRITER_GROUP,),
                )
                cursor.execute(
                    "ALTER ROLE vowpic_observation_reader_login "
                    "SET default_transaction_read_only = on"
                )
                cursor.execute(
                    "ALTER ROLE vowpic_observation_reader_login "
                    "SET statement_timeout = '30s'"
                )
                cursor.execute(
                    "ALTER ROLE vowpic_observation_writer_login "
                    "RESET default_transaction_read_only"
                )
                cursor.execute(
                    "ALTER ROLE vowpic_observation_writer_login "
                    "SET statement_timeout = '30s'"
                )
                credential_rotation = "superuser_test_fallback"
            else:
                raise ValueError(
                    "scoped observation database login rotation is missing"
                )
            for login in (
                OBSERVATION_READER_LOGIN,
                OBSERVATION_WRITER_LOGIN,
            ):
                _revoke_direct_login_privileges(
                    cursor,
                    login=login,
                    business_tables=(),
                )
    reader_url = database_url_for_login(
        normalized,
        OBSERVATION_READER_LOGIN,
        reader_password,
    )
    writer_url = database_url_for_login(
        normalized,
        OBSERVATION_WRITER_LOGIN,
        writer_password,
    )
    proof = {
        "database": authority["current_database"],
        "schema_revision": schema_revision,
        "credential_rotation": credential_rotation,
        "roles": prove_observation_logins_after_pooler_propagation(
            reader_url,
            writer_url,
        ),
    }
    return reader_url, writer_url, proof


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="PRODUCTION_MIGRATION_DATABASE_URL",
    )
    parser.add_argument("--github-cli", default="gh")
    parser.add_argument(
        "--github-repository",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
    )
    parser.add_argument(
        "--github-environment",
        default="production-observation",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    migration_url = os.environ.get(args.database_url_env, "").strip()
    if not migration_url or not args.github_repository:
        print(
            "ERROR: protected migration database URL and GitHub repository are required",
            file=sys.stderr,
        )
        return 1
    reader_url = writer_url = ""
    reader_password = writer_password = ""
    try:
        reader_url, writer_url, proof = provision_observation_database_logins(
            migration_url
        )
        reader_password = str(urlsplit(reader_url).password or "")
        writer_password = str(urlsplit(writer_url).password or "")
        github = publish_github_observation_database_urls(
            github_cli=args.github_cli,
            repository=args.github_repository,
            environment=args.github_environment,
            reader_url=reader_url,
            writer_url=writer_url,
        )
        report = {
            "schema": "vowpic.observation-database-logins.v1",
            "state": "PROVISIONED",
            "database": proof["database"],
            "schema_revision": proof["schema_revision"],
            "credential_rotation": proof["credential_rotation"],
            "roles": proof["roles"],
            "github": github,
        }
        _write_create_once(Path(args.output), report)
        print(
            json.dumps(
                {
                    "state": "PROVISIONED",
                    "roles": sorted(proof["roles"]),
                    "github_secrets": sorted(github),
                }
            )
        )
        return 0
    except (
        ValueError,
        OSError,
        psycopg2.Error,
        json.JSONDecodeError,
    ) as exc:
        detail = str(exc)
        for secret in (
            migration_url,
            reader_url,
            writer_url,
            reader_password,
            writer_password,
        ):
            if secret:
                detail = detail.replace(secret, "[REDACTED]")
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
