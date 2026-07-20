"""Prove the protected Production database credentials through Supavisor session mode."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg2

from repair_production_control_reader_credential import recover_control_reader_url
import verify_production_database_credentials as credential_proof


SCHEMA = "vowpic.production-database-session-pooler-probe.v1"
SESSION_POOLER_PORT = 5432


class SessionPoolerProbeError(ValueError):
    def __init__(self, credential: str, reason: str) -> None:
        super().__init__(reason)
        self.credential = credential
        self.reason = reason


def _session_pooler_url(value: str) -> str:
    parsed = urlsplit(credential_proof._sync_url(value))
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "postgresql"
        or not parsed.username
        or parsed.password is None
        or not host.endswith(".pooler.supabase.com")
        or parsed.port not in {5432, 6543}
    ):
        raise ValueError("Production database credential is not a supported pooler URL")
    userinfo = parsed.netloc.rsplit("@", 1)[0]
    netloc = f"{userinfo}@{host}:{SESSION_POOLER_PORT}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def probe_session_pooler(environment: dict[str, str]) -> dict[str, Any]:
    runtime_url = environment.get("PRODUCTION_RUNTIME_DATABASE_URL", "").strip()
    writer_url = environment.get("PRODUCTION_CONTROL_PLANE_DATABASE_URL", "").strip()
    reader_secret = environment.get("PRODUCTION_CONTROL_READ_DATABASE_URL", "").strip()
    if not runtime_url or not writer_url or not reader_secret:
        raise ValueError("protected Production database credential is missing")

    reader_url = recover_control_reader_url(runtime_url, writer_url, reader_secret)
    session_urls = {
        "runtime": _session_pooler_url(runtime_url),
        "control_writer": _session_pooler_url(writer_url),
        "control_reader": _session_pooler_url(reader_url),
    }
    parsed = credential_proof.validate_database_urls(session_urls)
    facts: dict[str, dict[str, Any]] = {}
    for kind in credential_proof.EXPECTED_SESSIONS:
        try:
            facts[kind] = credential_proof._connection_facts(session_urls[kind])
        except (OSError, psycopg2.Error) as exc:
            raise SessionPoolerProbeError(kind, "connection_failed") from exc
    try:
        proof = credential_proof.validate_database_facts(facts)
    except ValueError as exc:
        raise SessionPoolerProbeError("contract", "least_privilege_contract_failed") from exc
    return {
        "schema": SCHEMA,
        "state": "PASSED",
        "pooler_mode": "session",
        "pooler_port": SESSION_POOLER_PORT,
        "database": proof["database"],
        "credentials": proof["credentials"],
        "validated_urls": parsed,
    }


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    try:
        result = probe_session_pooler(dict(os.environ))
    except SessionPoolerProbeError as exc:
        _write_result(
            output,
            {
                "schema": SCHEMA,
                "state": "FAILED",
                "pooler_mode": "session",
                "pooler_port": SESSION_POOLER_PORT,
                "failure": {
                    "credential": exc.credential,
                    "reason": exc.reason,
                },
            },
        )
        print(json.dumps({"schema": SCHEMA, "state": "FAILED"}))
        return 1
    except ValueError:
        _write_result(
            output,
            {
                "schema": SCHEMA,
                "state": "FAILED",
                "pooler_mode": "session",
                "pooler_port": SESSION_POOLER_PORT,
                "failure": {
                    "credential": "configuration",
                    "reason": "credential_contract_failed",
                },
            },
        )
        print(json.dumps({"schema": SCHEMA, "state": "FAILED"}))
        return 1
    _write_result(output, result)
    print(json.dumps({"schema": SCHEMA, "state": "PASSED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
