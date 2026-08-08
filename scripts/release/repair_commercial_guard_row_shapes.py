#!/usr/bin/env python3
"""Repair schema-0020 cross-table guards without widening database privileges."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

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


TARGET_REVISION = "20260710_0021"
PREVIEW_MIGRATION_DATABASE_URL_ENV = "PREVIEW_MIGRATION_DATABASE_URL"
FUNCTION_SIGNATURES = {
    "commercial_reservation_allocation_guard": (
        "public.commercial_reservation_allocation_guard()"
    ),
    "commercial_entitlement_funding_guard": (
        "public.commercial_entitlement_funding_guard()"
    ),
}
GENERATION_GUARD_SIGNATURES = {
    "guard_generation_job_transition": (
        "public.guard_generation_job_transition()"
    ),
    "guard_generation_attempt_transition": (
        "public.guard_generation_attempt_transition()"
    ),
    "qa_verdict_append_only_guard": (
        "public.qa_verdict_append_only_guard()"
    ),
}

RESERVATION_ALLOCATION_GUARD_SQL = """
CREATE OR REPLACE FUNCTION public.commercial_reservation_allocation_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $commercial_reservation_allocation_guard$
DECLARE
    target_id uuid;
    required_amount integer;
    allocated_amount bigint;
BEGIN
    IF TG_TABLE_NAME = 'credit_reservations' THEN
        target_id := COALESCE(NEW.id, OLD.id);
    ELSIF TG_TABLE_NAME = 'credit_reservation_allocations' THEN
        target_id := COALESCE(NEW.reservation_id, OLD.reservation_id);
    ELSE
        RAISE EXCEPTION 'unexpected reservation allocation guard table: %',
            TG_TABLE_NAME USING ERRCODE = '23514';
    END IF;
    SELECT amount INTO required_amount
    FROM public.credit_reservations
    WHERE id = target_id;
    IF required_amount IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT COALESCE(SUM(amount), 0) INTO allocated_amount
    FROM public.credit_reservation_allocations
    WHERE reservation_id = target_id;
    IF allocated_amount <> required_amount THEN
        RAISE EXCEPTION 'reservation allocation sum mismatch'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$commercial_reservation_allocation_guard$;
"""

ENTITLEMENT_FUNDING_GUARD_SQL = """
CREATE OR REPLACE FUNCTION public.commercial_entitlement_funding_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $commercial_entitlement_funding_guard$
DECLARE
    target_id uuid;
    target_reservation_id uuid;
    reservation_status text;
    reservation_amount integer;
    funding_amount bigint;
    mismatch_count bigint;
BEGIN
    IF TG_TABLE_NAME = 'order_entitlements' THEN
        target_id := COALESCE(NEW.id, OLD.id);
    ELSIF TG_TABLE_NAME = 'order_entitlement_fundings' THEN
        target_id := COALESCE(NEW.entitlement_id, OLD.entitlement_id);
    ELSE
        RAISE EXCEPTION 'unexpected entitlement funding guard table: %',
            TG_TABLE_NAME USING ERRCODE = '23514';
    END IF;
    SELECT reservation_id INTO target_reservation_id
    FROM public.order_entitlements
    WHERE id = target_id;
    IF target_reservation_id IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT status, amount INTO reservation_status, reservation_amount
    FROM public.credit_reservations
    WHERE id = target_reservation_id;
    SELECT COALESCE(SUM(amount), 0) INTO funding_amount
    FROM public.order_entitlement_fundings
    WHERE entitlement_id = target_id;
    SELECT COUNT(*) INTO mismatch_count
    FROM public.order_entitlement_fundings funding
    LEFT JOIN public.credit_reservation_allocations allocation
      ON allocation.id = funding.reservation_allocation_id
     AND allocation.reservation_id = target_reservation_id
     AND allocation.grant_lot_id = funding.grant_lot_id
     AND allocation.amount = funding.amount
    WHERE funding.entitlement_id = target_id
      AND allocation.id IS NULL;
    IF reservation_status <> 'CAPTURED'
       OR funding_amount <> reservation_amount
       OR mismatch_count <> 0 THEN
        RAISE EXCEPTION 'entitlement funding does not reproduce capture'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$commercial_entitlement_funding_guard$;
"""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _definition_is_fixed(name: str, definition: str) -> bool:
    normalized = definition.lower()
    if "target_id := case" in normalized:
        return False
    if name == "commercial_reservation_allocation_guard":
        required = (
            "if tg_table_name = 'credit_reservations' then",
            "elsif tg_table_name = 'credit_reservation_allocations' then",
            "unexpected reservation allocation guard table",
        )
    elif name == "commercial_entitlement_funding_guard":
        required = (
            "if tg_table_name = 'order_entitlements' then",
            "elsif tg_table_name = 'order_entitlement_fundings' then",
            "unexpected entitlement funding guard table",
        )
    else:
        raise ValueError(f"unknown commercial guard: {name}")
    return all(item in normalized for item in required)


async def _current_revision(db: AsyncSession) -> str:
    return str(
        await db.scalar(text("SELECT version_num FROM public.alembic_version"))
        or "unknown"
    )


async def inspect_commercial_guard_row_shapes(
    db: AsyncSession,
) -> dict[str, object]:
    revision = await _current_revision(db)
    functions: dict[str, dict[str, object]] = {}
    for name, signature in FUNCTION_SIGNATURES.items():
        result = await db.execute(
            text(
                """
                SELECT
                    pg_get_functiondef(procedure.oid) AS definition,
                    pg_get_userbyid(procedure.proowner) AS owner,
                    procedure.prosecdef AS security_definer,
                    COALESCE(procedure.proconfig, ARRAY[]::text[]) AS settings,
                    COALESCE(procedure.proacl::text, '') AS acl
                FROM pg_catalog.pg_proc procedure
                WHERE procedure.oid =
                    to_regprocedure(:function_signature)::oid
                """
            ),
            {"function_signature": signature},
        )
        row = result.mappings().one_or_none()
        definition = str(row["definition"] if row is not None else "")
        settings = list(row["settings"] if row is not None else [])
        functions[name] = {
            "exists": row is not None,
            "owner_sha256": _sha256(
                str(row["owner"] if row is not None else "")
            ),
            "definition_sha256": _sha256(definition),
            "acl_sha256": _sha256(
                str(row["acl"] if row is not None else "")
            ),
            "security_definer": bool(
                row["security_definer"] if row is not None else False
            ),
            "search_path_locked": "search_path=pg_catalog, public" in settings,
            "fixed_row_shape_dispatch": _definition_is_fixed(name, definition),
        }
    generation_guards: dict[str, dict[str, object]] = {}
    for name, signature in GENERATION_GUARD_SIGNATURES.items():
        result = await db.execute(
            text(
                """
                SELECT
                    pg_get_functiondef(procedure.oid) AS definition,
                    pg_get_userbyid(procedure.proowner) AS owner,
                    procedure.prosecdef AS security_definer,
                    COALESCE(procedure.proconfig, ARRAY[]::text[]) AS settings,
                    COALESCE(procedure.proacl::text, '') AS acl
                FROM pg_catalog.pg_proc procedure
                WHERE procedure.oid =
                    to_regprocedure(:function_signature)::oid
                """
            ),
            {"function_signature": signature},
        )
        row = result.mappings().one_or_none()
        definition = str(row["definition"] if row is not None else "")
        settings = list(row["settings"] if row is not None else [])
        generation_guards[name] = {
            "exists": row is not None,
            "owner_sha256": _sha256(
                str(row["owner"] if row is not None else "")
            ),
            "definition_sha256": _sha256(definition),
            "acl_sha256": _sha256(
                str(row["acl"] if row is not None else "")
            ),
            "security_invoker": not bool(
                row["security_definer"] if row is not None else True
            ),
            "search_path_locked": "search_path=pg_catalog, public" in settings,
        }
    return {
        "revision": revision,
        "passed": revision == TARGET_REVISION
        and all(
            bool(item["exists"])
            and bool(item["security_definer"])
            and bool(item["search_path_locked"])
            and bool(item["fixed_row_shape_dispatch"])
            for item in functions.values()
        )
        and all(
            bool(item["exists"])
            and bool(item["security_invoker"])
            and bool(item["search_path_locked"])
            for item in generation_guards.values()
        ),
        "functions": functions,
        "generation_guards": generation_guards,
    }


async def repair_commercial_guard_row_shapes(
    db: AsyncSession,
) -> dict[str, object]:
    await db.execute(text("SET LOCAL lock_timeout = '5s'"))
    await db.execute(text("SET LOCAL statement_timeout = '1800s'"))
    before = await inspect_commercial_guard_row_shapes(db)
    if before["revision"] != TARGET_REVISION:
        raise ValueError(
            "commercial guard repair requires schema revision "
            f"{TARGET_REVISION}"
        )
    await db.execute(text(RESERVATION_ALLOCATION_GUARD_SQL))
    await db.execute(text(ENTITLEMENT_FUNDING_GUARD_SQL))
    for signature in GENERATION_GUARD_SIGNATURES.values():
        await db.execute(text(f"ALTER FUNCTION {signature} SECURITY INVOKER"))
    after = await inspect_commercial_guard_row_shapes(db)
    if not after["passed"]:
        raise RuntimeError("commercial guard row-shape repair did not verify")
    owner_and_acl_preserved = all(
        before[group][name]["owner_sha256"]
        == after[group][name]["owner_sha256"]
        and before[group][name]["acl_sha256"]
        == after[group][name]["acl_sha256"]
        for group, signatures in (
            ("functions", FUNCTION_SIGNATURES),
            ("generation_guards", GENERATION_GUARD_SIGNATURES),
        )
        for name in signatures
    )
    if not owner_and_acl_preserved:
        raise RuntimeError("commercial guard ownership or ACL changed")
    return {
        "schema": "vowpic.schema-0020-cross-table-guard-repair.v2",
        "before": before,
        "after": after,
        "owner_and_acl_preserved": owner_and_acl_preserved,
        "changed": any(
            before["functions"][name]["definition_sha256"]
            != after["functions"][name]["definition_sha256"]
            for name in FUNCTION_SIGNATURES
        )
        or any(
            before["generation_guards"][name]["security_invoker"]
            != after["generation_guards"][name]["security_invoker"]
            for name in GENERATION_GUARD_SIGNATURES
        ),
    }


def _write_create_once(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def _require_trusted_preview_workflow() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    workflow_ref = os.environ.get("GITHUB_WORKFLOW_REF", "").strip()
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "").strip()
    expected_workflow_prefix = (
        f"{repository}/.github/workflows/integration.yml@"
    )
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or not repository
        or not workflow_ref.startswith(expected_workflow_prefix)
        or not run_id.isdigit()
        or not run_attempt.isdigit()
        or int(run_id) < 1
        or int(run_attempt) < 1
    ):
        raise ValueError(
            "Preview guard repair requires the authenticated integration workflow"
        )
    return workflow_ref


async def _run_preview(args: argparse.Namespace) -> None:
    if args.environment != "preview":
        raise ValueError(
            "the direct repair entrypoint is Preview-only; Production must use "
            "apply_additive_migrations.py"
        )
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
    isolation_proof_sha256 = _sha256(
        json.dumps(
            isolation_proof,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    database_url = preview_urls["migration"]
    if not database_url:
        raise ValueError("Preview migration database URL is required")
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
                report = await repair_commercial_guard_row_shapes(db)
        report["environment"] = "preview"
        report["preview_isolation_proof_sha256"] = isolation_proof_sha256
        report["workflow_ref_sha256"] = _sha256(workflow_ref)
        _write_create_once(Path(args.output), report)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=("preview",), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    asyncio.run(_run_preview(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
