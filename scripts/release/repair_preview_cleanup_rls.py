#!/usr/bin/env python3
"""Install the bounded Preview-cleanup RLS surface without changing schema revision."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for location in (ROOT, BACKEND):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from app.core.database import normalize_database_url  # noqa: E402
from backend.scripts.verify_preview_database_isolation import (  # noqa: E402
    ENVIRONMENTS as PREVIEW_DATABASE_ENVIRONMENTS,
    prove_preview_database_isolation,
)


TARGET_REVISION = "20260710_0020"
MIGRATION_OWNER = "vowpic_migration_owner"
PREVIEW_MIGRATION_DATABASE_URL_ENV = "PREVIEW_MIGRATION_DATABASE_URL"
TRUSTED_WORKFLOWS = frozenset(
    {
        ".github/workflows/integration.yml",
        ".github/workflows/preview-identity-recovery.yml",
    }
)

# These are the only cleanup-read tables that have FORCE RLS in schema 0020.
# Other business lineage tables are intentionally left untouched.
PREVIEW_USER_TABLES = (
    "user_credits",
    "credit_transactions",
    "credit_purchases",
    "orders",
    "live_portrait_jobs",
    "user_subscriptions",
    "subscription_credit_grants",
)
PREVIEW_MUTABLE_TABLES = (
    "auth_sessions",
    "auth_refresh_tokens",
    "media_assets",
)


def _preview_user_predicate(table: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM public.acceptance_identity_bindings AS binding "
        "WHERE binding.environment = 'preview' "
        "AND binding.consumed_user_id = user_id)"
    )


POLICY_SPECS: dict[str, dict[str, str]] = {
    **{
        table: {"SELECT": _preview_user_predicate(table)}
        for table in PREVIEW_USER_TABLES
    },
    "auth_sessions": {
        "SELECT": (
            "EXISTS (SELECT 1 FROM public.acceptance_identity_bindings AS binding "
            "WHERE binding.environment = 'preview' "
            "AND binding.id = acceptance_binding_id)"
        ),
        "UPDATE": (
            "EXISTS (SELECT 1 FROM public.acceptance_identity_bindings AS binding "
            "WHERE binding.environment = 'preview' "
            "AND binding.id = acceptance_binding_id)"
        ),
    },
    "auth_refresh_tokens": {
        "SELECT": (
            "EXISTS (SELECT 1 FROM public.auth_sessions AS session "
            "JOIN public.acceptance_identity_bindings AS binding "
            "ON binding.id = session.acceptance_binding_id "
            "WHERE binding.environment = 'preview' "
            "AND session.id = auth_refresh_tokens.session_id)"
        ),
        "UPDATE": (
            "EXISTS (SELECT 1 FROM public.auth_sessions AS session "
            "JOIN public.acceptance_identity_bindings AS binding "
            "ON binding.id = session.acceptance_binding_id "
            "WHERE binding.environment = 'preview' "
            "AND session.id = auth_refresh_tokens.session_id)"
        ),
    },
    "media_assets": {
        "SELECT": (
            "EXISTS (SELECT 1 FROM public.acceptance_identity_bindings AS binding "
            "WHERE binding.environment = 'preview' "
            "AND binding.consumed_user_id = owner_user_id)"
        ),
        "UPDATE": (
            "EXISTS (SELECT 1 FROM public.acceptance_identity_bindings AS binding "
            "WHERE binding.environment = 'preview' "
            "AND binding.consumed_user_id = owner_user_id)"
        ),
    },
}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _policy_name(table: str, command: str) -> str:
    return f"{table}_preview_cleanup_migration_{command.lower()}"


def _validate_policy_specs() -> None:
    if set(POLICY_SPECS) != set(PREVIEW_USER_TABLES) | set(PREVIEW_MUTABLE_TABLES):
        raise RuntimeError("Preview cleanup RLS table set is not exact")
    for table, commands in POLICY_SPECS.items():
        expected = {"SELECT", "UPDATE"} if table in PREVIEW_MUTABLE_TABLES else {"SELECT"}
        if set(commands) != expected:
            raise RuntimeError(f"Preview cleanup RLS commands are not exact for {table}")
        for predicate in commands.values():
            normalized = predicate.lower()
            if (
                "acceptance_identity_bindings" not in normalized
                or "environment = 'preview'" not in normalized
                or "using (true)" in normalized
            ):
                raise RuntimeError(f"Preview cleanup RLS predicate is unbounded for {table}")


async def _inspect(db: AsyncSession) -> dict[str, Any]:
    revision = str(
        await db.scalar(text("SELECT version_num FROM public.alembic_version"))
        or "unknown"
    )
    role_row = (
        await db.execute(
            text(
                "SELECT session_user AS session_role, current_user AS active_role, "
                "role.rolcanlogin, role.rolsuper, role.rolcreatedb, "
                "role.rolcreaterole, role.rolreplication, role.rolbypassrls, "
                "role.rolinherit FROM pg_catalog.pg_roles AS role "
                "WHERE role.rolname = :role"
            ),
            {"role": MIGRATION_OWNER},
        )
    ).mappings().one_or_none()
    role_ok = bool(
        role_row
        and role_row["session_role"] == "vowpic_migration_login"
        and role_row["active_role"] == MIGRATION_OWNER
        and not any(
            bool(role_row[name])
            for name in (
                "rolcanlogin",
                "rolsuper",
                "rolcreatedb",
                "rolcreaterole",
                "rolreplication",
                "rolbypassrls",
            )
        )
        and bool(role_row["rolinherit"])
    )

    table_facts: dict[str, dict[str, Any]] = {}
    for table, commands in POLICY_SPECS.items():
        relation = (
            await db.execute(
                text(
                    "SELECT pg_get_userbyid(class.relowner) AS owner, "
                    "class.relrowsecurity AS rls_enabled, "
                    "class.relforcerowsecurity AS force_rls "
                    "FROM pg_catalog.pg_class AS class "
                    "JOIN pg_catalog.pg_namespace AS namespace "
                    "ON namespace.oid = class.relnamespace "
                    "WHERE namespace.nspname = 'public' "
                    "AND class.relname = :table"
                ),
                {"table": table},
            )
        ).mappings().one_or_none()
        policies = (
            await db.execute(
                text(
                    "SELECT policy.polname, policy.polcmd, "
                    "ARRAY(SELECT role.rolname "
                    "FROM unnest(policy.polroles) AS policy_role(oid) "
                    "JOIN pg_catalog.pg_roles AS role ON role.oid = policy_role.oid "
                    "ORDER BY role.rolname) AS roles, "
                    "COALESCE(pg_get_expr(policy.polqual, policy.polrelid), '') AS using_expr, "
                    "COALESCE(pg_get_expr(policy.polwithcheck, policy.polrelid), '') AS check_expr "
                    "FROM pg_catalog.pg_policy AS policy "
                    "WHERE policy.polrelid = to_regclass(:qualified_table) "
                    "ORDER BY policy.polname"
                ),
                {
                    "qualified_table": f"public.{table}",
                },
            )
        ).mappings().all()
        policy_map = {str(row["polname"]): row for row in policies}
        exact_policies = True
        policy_hashes: dict[str, str] = {}
        for command in commands:
            name = _policy_name(table, command)
            row = policy_map.get(name)
            expected_cmd = "r" if command == "SELECT" else "w"
            if row is None:
                exact_policies = False
                policy_hashes[name] = _sha256("")
                continue
            using_expr = str(row["using_expr"])
            check_expr = str(row["check_expr"])
            roles = list(row["roles"] or [])
            exact_policies = exact_policies and (
                str(row["polcmd"]) == expected_cmd
                and roles == [MIGRATION_OWNER]
                and "acceptance_identity_bindings" in using_expr
                and "'preview'::text" in using_expr
                and (
                    command == "SELECT"
                    or (
                        "acceptance_identity_bindings" in check_expr
                        and "'preview'::text" in check_expr
                    )
                )
            )
            policy_hashes[name] = _sha256(
                json.dumps(
                    {
                        "command": row["polcmd"],
                        "roles": roles,
                        "using": using_expr,
                        "check": check_expr,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        table_facts[table] = {
            "exists": relation is not None,
            "owner_sha256": _sha256(str(relation["owner"] if relation else "")),
            "owner_is_migration_role": bool(
                relation and relation["owner"] == MIGRATION_OWNER
            ),
            "rls_enabled": bool(relation and relation["rls_enabled"]),
            "force_rls": bool(relation and relation["force_rls"]),
            "exact_policies": exact_policies,
            "policy_hashes": policy_hashes,
        }

    passed = (
        revision == TARGET_REVISION
        and role_ok
        and all(
            fact["exists"]
            and fact["owner_is_migration_role"]
            and fact["rls_enabled"]
            and fact["force_rls"]
            and fact["exact_policies"]
            for fact in table_facts.values()
        )
    )
    return {
        "revision": revision,
        "role_contract_passed": role_ok,
        "table_count": len(table_facts),
        "passed": passed,
        "tables": table_facts,
    }


async def repair_preview_cleanup_rls(db: AsyncSession) -> dict[str, Any]:
    _validate_policy_specs()
    await db.execute(text("SET LOCAL lock_timeout = '5s'"))
    await db.execute(text("SET LOCAL statement_timeout = '60s'"))
    await db.execute(
        text(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended('vowpic-preview-cleanup-rls-v1', 0))"
        )
    )
    before = await _inspect(db)
    if before["revision"] != TARGET_REVISION:
        raise ValueError(
            f"Preview cleanup RLS repair requires schema revision {TARGET_REVISION}"
        )
    if not before["role_contract_passed"]:
        raise ValueError("Preview cleanup RLS repair requires the migration login contract")
    for table, fact in before["tables"].items():
        if not (
            fact["exists"]
            and fact["owner_is_migration_role"]
            and fact["rls_enabled"]
            and fact["force_rls"]
        ):
            raise ValueError(f"Preview cleanup RLS table contract is invalid for {table}")

    for table, commands in POLICY_SPECS.items():
        for command, predicate in commands.items():
            name = _policy_name(table, command)
            await db.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))
            clause = f"USING ({predicate})"
            if command == "UPDATE":
                clause += f" WITH CHECK ({predicate})"
            await db.execute(
                text(
                    f"CREATE POLICY {name} ON public.{table} "
                    f"FOR {command} TO {MIGRATION_OWNER} {clause}"
                )
            )

    after = await _inspect(db)
    if not after["passed"]:
        raise RuntimeError("Preview cleanup RLS repair did not verify")
    ownership_preserved = all(
        before["tables"][table]["owner_sha256"]
        == after["tables"][table]["owner_sha256"]
        for table in POLICY_SPECS
    )
    if not ownership_preserved:
        raise RuntimeError("Preview cleanup RLS repair changed table ownership")
    return {
        "schema": "vowpic.preview-cleanup-rls-repair.v1",
        "before": before,
        "after": after,
        "ownership_preserved": ownership_preserved,
        "changed": any(
            before["tables"][table]["policy_hashes"]
            != after["tables"][table]["policy_hashes"]
            for table in POLICY_SPECS
        ),
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def _require_trusted_preview_workflow() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    workflow_ref = os.environ.get("GITHUB_WORKFLOW_REF", "").strip()
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "").strip()
    prefix = f"{repository}/"
    workflow_path = workflow_ref.removeprefix(prefix).split("@", 1)[0]
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or not repository
        or not workflow_ref.startswith(prefix)
        or workflow_path not in TRUSTED_WORKFLOWS
        or not run_id.isdigit()
        or not run_attempt.isdigit()
        or int(run_id) < 1
        or int(run_attempt) < 1
    ):
        raise ValueError(
            "Preview cleanup RLS repair requires an authenticated trusted workflow"
        )
    return workflow_ref


async def _run(args: argparse.Namespace) -> None:
    workflow_ref = _require_trusted_preview_workflow()
    preview_urls = {
        kind: os.environ.get(name, "").strip()
        for kind, name in PREVIEW_DATABASE_ENVIRONMENTS.items()
    }
    preview_ref = os.environ.get("SUPABASE_PROJECT_REF", "").strip()
    production_url = os.environ.get("PRODUCTION_SUPABASE_URL", "").strip()
    if (
        any(not value for value in preview_urls.values())
        or not preview_ref
        or not production_url
    ):
        raise ValueError("Preview isolation inputs are required")
    isolation_proof = prove_preview_database_isolation(
        preview_urls,
        expected_preview_project_ref=preview_ref,
        production_supabase_url=production_url,
    )
    isolation_sha256 = _sha256(
        json.dumps(isolation_proof, sort_keys=True, separators=(",", ":"))
    )
    database_url = preview_urls["migration"]
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(
        normalized_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        async with factory() as db:
            async with db.begin():
                report = await repair_preview_cleanup_rls(db)
        report["environment"] = "preview"
        report["preview_isolation_proof_sha256"] = isolation_sha256
        report["workflow_ref_sha256"] = _sha256(workflow_ref)
        _write_create_once(Path(args.output), report)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
