"""Prove the exact Production application-login and identity SQL surfaces."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


RUNTIME_LOGIN = "vowpic_app_runtime"
WRITER_LOGIN = "vowpic_control_writer_login"
RUNTIME_GROUP = "vowpic_runtime"
WRITER_GROUP = "vowpic_control_writer"
OBSERVATION_READER_LOGIN = "vowpic_observation_reader_login"
OBSERVATION_WRITER_LOGIN = "vowpic_observation_writer_login"
OBSERVATION_READER_GROUP = "vowpic_observation_reader"
OBSERVATION_WRITER_GROUP = "vowpic_observation_writer"
IDENTITY_SERVICE_GROUP = "vowpic_identity_service"
MIGRATION_LOGIN = "vowpic_migration_login"
MIGRATION_OWNER = "vowpic_migration_owner"
SAFE_BASELINE_SCHEMA = "20260712_0014"
IDENTITY_TABLE_PRIVILEGES = {
    "user_identities": ("SELECT", "INSERT", "UPDATE"),
    "oauth_login_intents": ("SELECT", "INSERT", "UPDATE"),
    "auth_sessions": ("SELECT", "INSERT", "UPDATE"),
    "auth_refresh_tokens": ("SELECT", "INSERT", "UPDATE"),
    "account_claim_proofs": ("SELECT", "INSERT", "UPDATE"),
    "identity_email_conflicts": ("SELECT", "INSERT", "UPDATE"),
    "user_account_merges": ("SELECT", "INSERT"),
    "account_tombstones": ("SELECT", "INSERT", "UPDATE"),
}
RUNTIME_SCHEMA_READINESS_PRIVILEGES = {
    "alembic_version": ("SELECT",),
}
CONTROL_PLANE_TABLES = (
    "release_observation_samples",
    "release_observation_runs",
    "data_migration_checkpoints",
    "data_migration_runs",
    "ops_feature_flag_audits",
    "ops_feature_flags",
    "acceptance_identity_bindings",
    "release_activations",
)
MIGRATION_REFERENCE_TABLES = (
    "acceptance_identity_bindings",
    "release_activations",
    "release_observation_runs",
)
MIGRATION_PREREQUISITE_ROLES = (
    "vowpic_identity_owner",
    "vowpic_identity_service",
    "vowpic_media_service",
    "vowpic_generation_service",
    "vowpic_partner_service",
)
ALLOWED_RUNTIME_PRIVILEGES = {"SELECT", "INSERT", "UPDATE"}
OBSERVATION_READ_TABLES = (
    "release_activations",
    "release_observation_runs",
    "release_observation_samples",
)


def _prove_business_tables(
    role_url: str,
    role_name: str,
    business_privileges: dict[str, tuple[str, ...]],
) -> dict[str, dict[str, Any]]:
    proof: dict[str, dict[str, Any]] = {}
    with psycopg2.connect(role_url, cursor_factory=RealDictCursor) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            for table, expected_verbs in business_privileges.items():
                cursor.execute(
                    """
                    SELECT relation.relrowsecurity AS row_security_enabled,
                           COALESCE((
                               SELECT array_agg(policy.cmd ORDER BY policy.cmd)
                               FROM pg_policies policy
                               WHERE policy.schemaname = 'public'
                                 AND policy.tablename = %s
                                 AND %s = ANY(policy.roles)
                           ), ARRAY[]::text[]) AS runtime_policy_commands,
                           COALESCE((
                               SELECT array_agg(policy.policyname ORDER BY policy.policyname)
                               FROM pg_policies policy
                               WHERE policy.schemaname = 'public'
                                 AND policy.tablename = %s
                                 AND %s = ANY(policy.roles)
                           ), ARRAY[]::text[]) AS runtime_policy_names,
                           has_table_privilege(current_user, %s, 'SELECT') AS can_select,
                           has_table_privilege(current_user, %s, 'INSERT') AS can_insert,
                           has_table_privilege(current_user, %s, 'UPDATE') AS can_update,
                           has_table_privilege(current_user, %s, 'DELETE') AS can_delete,
                           has_table_privilege(current_user, %s, 'TRUNCATE') AS can_truncate,
                           has_table_privilege(current_user, %s, 'REFERENCES') AS can_references,
                           has_table_privilege(current_user, %s, 'TRIGGER') AS can_trigger
                    FROM pg_class relation
                    WHERE relation.oid = to_regclass(%s)
                    """,
                    (
                        table,
                        RUNTIME_GROUP,
                        table,
                        RUNTIME_GROUP,
                        *([f"public.{table}"] * 7),
                        f"public.{table}",
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(f"database role proof is missing {table}")
                table_facts = dict(row)
                expected = set(expected_verbs) if role_name == RUNTIME_LOGIN else set()
                actual = {
                    privilege
                    for privilege, key in (
                        ("SELECT", "can_select"),
                        ("INSERT", "can_insert"),
                        ("UPDATE", "can_update"),
                        ("DELETE", "can_delete"),
                        ("TRUNCATE", "can_truncate"),
                        ("REFERENCES", "can_references"),
                        ("TRIGGER", "can_trigger"),
                    )
                    if table_facts[key]
                }
                policy_commands = set(table_facts["runtime_policy_commands"] or [])
                policy_names = set(table_facts["runtime_policy_names"] or [])
                expected_policy_names = {
                    f"{table}_vowpic_runtime_{command.lower()}"
                    for command in expected_verbs
                }
                if (
                    actual != expected
                    or policy_commands != set(expected_verbs)
                    or policy_names != expected_policy_names
                    or not table_facts["row_security_enabled"]
                ):
                    raise ValueError(
                        f"database login {role_name} has an invalid {table} privilege set"
                    )
                proof[table] = table_facts
    return proof


def _prove_login(
    *,
    role_name: str,
    role_url: str,
    required_group: str,
    forbidden_group: str,
    additional_required_groups: tuple[str, ...],
    business_privileges: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    with psycopg2.connect(role_url, cursor_factory=RealDictCursor) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user,
                       role.rolsuper AS superuser,
                       role.rolinherit AS inherit_privileges,
                       role.rolcreatedb AS create_db,
                       role.rolcreaterole AS create_role,
                       role.rolreplication AS replication,
                       role.rolbypassrls AS bypass_rls,
                       pg_has_role(current_user, %s, 'MEMBER') AS required_group_member,
                       pg_has_role(current_user, %s, 'MEMBER') AS forbidden_group_member,
                       (
                           SELECT COALESCE(
                               bool_and(pg_has_role(current_user, required.name, 'MEMBER')),
                               true
                           )
                           FROM unnest(%s::text[]) AS required(name)
                       ) AS additional_required_groups_member,
                       COALESCE((
                           SELECT array_agg(parent.rolname ORDER BY parent.rolname)
                           FROM pg_auth_members membership
                           JOIN pg_roles parent ON parent.oid = membership.roleid
                           WHERE membership.member = role.oid
                       ), ARRAY[]::name[]) AS direct_memberships,
                       pg_get_userbyid(control.relowner) = current_user AS owns_control_table,
                       has_table_privilege(current_user, 'public.users', 'SELECT') AS users_select,
                       has_table_privilege(current_user, 'public.users', 'UPDATE') AS users_update,
                       has_table_privilege(current_user, 'public.users', 'DELETE') AS users_delete,
                       has_table_privilege(current_user, 'public.ops_feature_flags', 'SELECT') AS flags_select,
                       has_table_privilege(current_user, 'public.ops_feature_flags', 'UPDATE') AS flags_update,
                       has_table_privilege(current_user, 'public.alembic_version', 'SELECT') AS schema_revision_select,
                       has_table_privilege(current_user, 'public.alembic_version', 'INSERT') AS schema_revision_insert,
                       has_table_privilege(current_user, 'public.alembic_version', 'UPDATE') AS schema_revision_update,
                       has_table_privilege(current_user, 'public.alembic_version', 'DELETE') AS schema_revision_delete
                FROM pg_roles role
                JOIN pg_class control ON control.oid = 'public.ops_feature_flags'::regclass
                WHERE role.rolname = current_user
                """,
                (required_group, forbidden_group, list(additional_required_groups)),
            )
            schema_revisions: tuple[str, ...] = ()
            row = cursor.fetchone()
            if row is not None and role_name == RUNTIME_LOGIN:
                cursor.execute("SELECT version_num FROM public.alembic_version")
                schema_revisions = tuple(
                    sorted(str(result["version_num"]) for result in cursor.fetchall())
                )
    if row is None:
        raise ValueError(f"database role proof is missing {role_name}")
    facts = dict(row)
    if facts["current_user"] != role_name:
        raise ValueError(f"database authenticated {role_name} as an unexpected role")
    forbidden = (
        "superuser",
        "create_db",
        "create_role",
        "replication",
        "bypass_rls",
        "forbidden_group_member",
        "owns_control_table",
        "users_delete",
    )
    if (
        any(facts[key] for key in forbidden)
        or not facts["inherit_privileges"]
        or not facts["required_group_member"]
        or not facts["additional_required_groups_member"]
        or set(facts["direct_memberships"] or [])
        != {required_group, *additional_required_groups}
    ):
        raise ValueError(f"database login {role_name} violates the least-privilege contract")
    if role_name == RUNTIME_LOGIN:
        if not (facts["users_select"] and facts["users_update"] and facts["flags_select"]):
            raise ValueError("runtime login is missing its reviewed SQL surface")
        if (
            facts["flags_update"]
            or not facts["schema_revision_select"]
            or facts["schema_revision_insert"]
            or facts["schema_revision_update"]
            or facts["schema_revision_delete"]
            or not schema_revisions
        ):
            raise ValueError("runtime login has an invalid schema/control SQL surface")
    elif (
        facts["users_select"]
        or facts["users_update"]
        or not facts["flags_update"]
        or facts["schema_revision_select"]
        or facts["schema_revision_insert"]
        or facts["schema_revision_update"]
        or facts["schema_revision_delete"]
    ):
        raise ValueError("control-writer login has an invalid business/control privilege split")
    return {
        **facts,
        "schema_revisions": schema_revisions,
        "business_tables": _prove_business_tables(
            role_url,
            role_name,
            business_privileges,
        ),
    }


def prove_database_logins(
    runtime_url: str,
    writer_url: str,
    business_privileges: dict[str, tuple[str, ...]],
) -> dict[str, dict[str, Any]]:
    return {
        role_name: _prove_login(
            role_name=role_name,
            role_url=role_url,
            required_group=required_group,
            forbidden_group=forbidden_group,
            additional_required_groups=additional_required_groups,
            business_privileges=business_privileges,
        )
        for (
            role_name,
            role_url,
            required_group,
            forbidden_group,
            additional_required_groups,
        ) in (
            (
                RUNTIME_LOGIN,
                runtime_url,
                RUNTIME_GROUP,
                WRITER_GROUP,
                (IDENTITY_SERVICE_GROUP,),
            ),
            (WRITER_LOGIN, writer_url, WRITER_GROUP, RUNTIME_GROUP, ()),
        )
    }


def _validate_observation_login_facts(
    facts: dict[str, Any],
    *,
    role_name: str,
    required_group: str,
    expect_read_only: bool,
) -> dict[str, Any]:
    common_forbidden = (
        "superuser",
        "create_db",
        "create_role",
        "replication",
        "bypass_rls",
        "owns_objects",
        "samples_update",
        "samples_delete",
        "runs_insert",
        "runs_table_update",
        "cleanup_hash_update",
        "run_version_update",
        "run_state_update",
        "runs_delete",
        "users_select",
        "flags_select",
        "flags_update",
        "recoveries_select",
    )
    if (
        facts["current_user"] != role_name
        or not facts["inherit_privileges"]
        or not facts["schema_usage"]
        or not facts["required_group_member"]
        or set(facts["direct_memberships"] or []) != {required_group}
        or any(facts[key] for key in common_forbidden)
        or not all(
            facts[key]
            for key in ("activations_select", "runs_select", "samples_select")
        )
    ):
        raise ValueError(
            f"database login {role_name} violates the observation least-privilege contract"
        )
    default_read_only = str(facts["default_read_only"]).lower() == "on"
    if default_read_only is not expect_read_only:
        raise ValueError(
            f"database login {role_name} has an invalid read-only transaction default"
        )
    if role_name == OBSERVATION_READER_LOGIN:
        if (
            facts["samples_insert"]
            or not facts["metrics_execute"]
        ):
            raise ValueError("observation reader has an invalid SQL surface")
    elif role_name == OBSERVATION_WRITER_LOGIN:
        if (
            not facts["samples_insert"]
            or facts["metrics_execute"]
        ):
            raise ValueError("observation writer has an invalid SQL surface")
    else:  # pragma: no cover - callers use fixed role constants.
        raise ValueError("unknown observation database login")
    return facts


def _prove_observation_login(
    role_url: str,
    *,
    role_name: str,
    required_group: str,
    expect_read_only: bool,
) -> dict[str, Any]:
    with psycopg2.connect(role_url, cursor_factory=RealDictCursor) as connection:
        connection.set_session(readonly=expect_read_only, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user,
                       role.rolsuper AS superuser,
                       role.rolinherit AS inherit_privileges,
                       role.rolcreatedb AS create_db,
                       role.rolcreaterole AS create_role,
                       role.rolreplication AS replication,
                       role.rolbypassrls AS bypass_rls,
                       current_setting('default_transaction_read_only') AS default_read_only,
                       has_schema_privilege(current_user, 'public', 'USAGE') AS schema_usage,
                       pg_has_role(current_user, %s, 'MEMBER') AS required_group_member,
                       COALESCE((
                           SELECT array_agg(parent.rolname ORDER BY parent.rolname)
                           FROM pg_auth_members AS membership
                           JOIN pg_roles AS parent ON parent.oid = membership.roleid
                           WHERE membership.member = role.oid
                       ), ARRAY[]::name[]) AS direct_memberships,
                       EXISTS (
                           SELECT 1 FROM pg_database AS database
                           WHERE database.datdba = role.oid
                       ) OR EXISTS (
                           SELECT 1 FROM pg_namespace AS namespace
                           WHERE namespace.nspowner = role.oid
                       ) OR EXISTS (
                           SELECT 1 FROM pg_class AS relation
                           WHERE relation.relowner = role.oid
                       ) OR EXISTS (
                           SELECT 1 FROM pg_proc AS routine
                           WHERE routine.proowner = role.oid
                       ) AS owns_objects,
                       has_table_privilege(
                           current_user, 'public.release_activations', 'SELECT'
                       ) AS activations_select,
                       has_table_privilege(
                           current_user, 'public.release_observation_runs', 'SELECT'
                       ) AS runs_select,
                       has_table_privilege(
                           current_user, 'public.release_observation_samples', 'SELECT'
                       ) AS samples_select,
                       has_table_privilege(
                           current_user, 'public.release_observation_samples', 'INSERT'
                       ) AS samples_insert,
                       has_table_privilege(
                           current_user, 'public.release_observation_samples', 'UPDATE'
                       ) AS samples_update,
                       has_table_privilege(
                           current_user, 'public.release_observation_samples', 'DELETE'
                       ) AS samples_delete,
                       has_table_privilege(
                           current_user, 'public.release_observation_runs', 'INSERT'
                       ) AS runs_insert,
                       has_table_privilege(
                           current_user, 'public.release_observation_runs', 'UPDATE'
                       ) AS runs_table_update,
                       has_column_privilege(
                           current_user,
                           'public.release_observation_runs',
                           'cleanup_cycle_sha256',
                           'UPDATE'
                       ) AS cleanup_hash_update,
                       has_column_privilege(
                           current_user,
                           'public.release_observation_runs',
                           'version',
                           'UPDATE'
                       ) AS run_version_update,
                       has_column_privilege(
                           current_user,
                           'public.release_observation_runs',
                           'state',
                           'UPDATE'
                       ) AS run_state_update,
                       has_table_privilege(
                           current_user, 'public.release_observation_runs', 'DELETE'
                       ) AS runs_delete,
                       has_table_privilege(
                           current_user, 'public.users', 'SELECT'
                       ) AS users_select,
                       has_table_privilege(
                           current_user, 'public.ops_feature_flags', 'SELECT'
                       ) AS flags_select,
                       has_table_privilege(
                           current_user, 'public.ops_feature_flags', 'UPDATE'
                       ) AS flags_update,
                       has_table_privilege(
                           current_user,
                           'public.release_observation_recoveries',
                           'SELECT'
                       ) AS recoveries_select,
                       has_function_privilege(
                           current_user,
                           'public.read_release_observation_metrics_v1(uuid)',
                           'EXECUTE'
                       ) AS metrics_execute
                FROM pg_roles AS role
                WHERE role.rolname = current_user
                """,
                (required_group,),
            )
            row = cursor.fetchone()
    if row is None:
        raise ValueError(f"database role proof is missing {role_name}")
    return _validate_observation_login_facts(
        dict(row),
        role_name=role_name,
        required_group=required_group,
        expect_read_only=expect_read_only,
    )


def prove_observation_database_logins(
    reader_url: str,
    writer_url: str,
) -> dict[str, dict[str, Any]]:
    return {
        OBSERVATION_READER_LOGIN: _prove_observation_login(
            reader_url,
            role_name=OBSERVATION_READER_LOGIN,
            required_group=OBSERVATION_READER_GROUP,
            expect_read_only=True,
        ),
        OBSERVATION_WRITER_LOGIN: _prove_observation_login(
            writer_url,
            role_name=OBSERVATION_WRITER_LOGIN,
            required_group=OBSERVATION_WRITER_GROUP,
            expect_read_only=False,
        ),
    }


def _validate_runtime_identity_grant(
    authority: dict[str, Any],
    runtime_role: dict[str, Any],
    identity_tables: dict[str, dict[str, Any]],
    *,
    identity_schema_required: bool = True,
) -> None:
    reference_privileges = authority.get("migration_reference_privileges") or {}
    prerequisite_roles = authority.get("migration_prerequisite_roles") or {}
    if (
        authority.get("session_user") != MIGRATION_LOGIN
        or authority.get("current_user") != MIGRATION_OWNER
        or not authority.get("migration_owner_member")
        or not authority.get("identity_owner_schema_create")
        or set(reference_privileges) != set(MIGRATION_REFERENCE_TABLES)
        or not all(reference_privileges.values())
        or set(prerequisite_roles) != set(MIGRATION_PREREQUISITE_ROLES)
    ):
        raise ValueError("identity grant proof requires the scoped migration authority")
    for role_name, facts in prerequisite_roles.items():
        if (
            facts.get("role_name") != role_name
            or facts.get("can_login")
            or facts.get("superuser")
            or facts.get("create_db")
            or facts.get("create_role")
            or facts.get("replication")
            or facts.get("bypass_rls")
            or not facts.get("inherit_privileges")
            or not facts.get("schema_usage")
            or bool(facts.get("schema_create"))
            != (role_name == "vowpic_identity_owner")
            or facts.get("memberships")
        ):
            raise ValueError(
                "identity grant proof requires safe migration prerequisite roles"
            )
    forbidden_role_flags = (
        "superuser",
        "create_db",
        "create_role",
        "replication",
        "bypass_rls",
        "owns_objects",
    )
    if (
        runtime_role.get("role_name") != RUNTIME_LOGIN
        or not runtime_role.get("can_login")
        or not runtime_role.get("inherit_privileges")
        or any(runtime_role.get(key) for key in forbidden_role_flags)
        or set(runtime_role.get("memberships") or [])
        != {RUNTIME_GROUP, IDENTITY_SERVICE_GROUP}
    ):
        raise ValueError("runtime identity membership violates the least-privilege contract")
    if not identity_schema_required:
        if identity_tables:
            raise ValueError(
                "safe-baseline identity membership proof found out-of-order identity tables"
            )
        return
    if set(identity_tables) != set(IDENTITY_TABLE_PRIVILEGES):
        raise ValueError("runtime identity grant proof is missing reviewed tables")
    for table, expected_privileges in IDENTITY_TABLE_PRIVILEGES.items():
        facts = identity_tables[table]
        actual_privileges = {
            privilege
            for privilege, key in (
                ("SELECT", "can_select"),
                ("INSERT", "can_insert"),
                ("UPDATE", "can_update"),
                ("DELETE", "can_delete"),
                ("TRUNCATE", "can_truncate"),
                ("REFERENCES", "can_references"),
                ("TRIGGER", "can_trigger"),
            )
            if facts.get(key)
        }
        if (
            actual_privileges != set(expected_privileges)
            or facts.get("direct_privileges")
            or not facts.get("row_security_enabled")
            or not facts.get("row_security_forced")
            or set(facts.get("identity_policy_names") or [])
            != {f"{table}_identity_service_all"}
            or set(facts.get("identity_policy_commands") or []) != {"ALL"}
        ):
            raise ValueError(
                f"runtime identity grant has an invalid {table} privilege or RLS surface"
            )


def prove_runtime_identity_grant(
    database_url: str,
    *,
    expected_schema: str,
) -> dict[str, Any]:
    normalized = database_url.strip().replace(
        "postgresql+asyncpg://",
        "postgresql://",
        1,
    )
    identity_tables: dict[str, dict[str, Any]] = {}
    with psycopg2.connect(normalized, cursor_factory=RealDictCursor) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT session_user,
                       current_user,
                       current_database() AS database,
                       pg_has_role(
                           session_user,
                           'vowpic_migration_owner',
                           'MEMBER'
                       ) AS migration_owner_member,
                       has_schema_privilege(
                           'vowpic_identity_owner',
                           'public',
                           'CREATE'
                       ) AS identity_owner_schema_create
                """
            )
            authority = dict(cursor.fetchone() or {})
            cursor.execute(
                """
                SELECT role.rolname AS role_name,
                       role.rolcanlogin AS can_login,
                       role.rolinherit AS inherit_privileges,
                       role.rolsuper AS superuser,
                       role.rolcreatedb AS create_db,
                       role.rolcreaterole AS create_role,
                       role.rolreplication AS replication,
                       role.rolbypassrls AS bypass_rls,
                       has_schema_privilege(
                           role.rolname, 'public', 'USAGE'
                       ) AS schema_usage,
                       has_schema_privilege(
                           role.rolname, 'public', 'CREATE'
                       ) AS schema_create,
                       COALESCE((
                           SELECT array_agg(parent.rolname ORDER BY parent.rolname)
                           FROM pg_auth_members membership
                           JOIN pg_roles parent ON parent.oid = membership.roleid
                           WHERE membership.member = role.oid
                       ), ARRAY[]::name[]) AS memberships
                FROM pg_roles role
                WHERE role.rolname = ANY(%s)
                ORDER BY role.rolname
                """,
                (list(MIGRATION_PREREQUISITE_ROLES),),
            )
            authority["migration_prerequisite_roles"] = {
                str(row["role_name"]): dict(row) for row in cursor.fetchall()
            }
            cursor.execute(
                """
                SELECT required.table_name,
                       has_table_privilege(
                           current_user,
                           format('public.%%I', required.table_name),
                           'REFERENCES'
                       ) AS can_references
                FROM unnest(%s::text[]) AS required(table_name)
                ORDER BY required.table_name
                """,
                (list(MIGRATION_REFERENCE_TABLES),),
            )
            authority["migration_reference_privileges"] = {
                str(row["table_name"]): bool(row["can_references"])
                for row in cursor.fetchall()
            }
            cursor.execute("SELECT version_num FROM public.alembic_version")
            schema_revisions = tuple(
                sorted(str(row["version_num"]) for row in cursor.fetchall())
            )
            if schema_revisions != (expected_schema,):
                raise ValueError("runtime identity proof observed an unexpected schema")
            cursor.execute(
                """
                SELECT role.rolname AS role_name,
                       role.rolcanlogin AS can_login,
                       role.rolinherit AS inherit_privileges,
                       role.rolsuper AS superuser,
                       role.rolcreatedb AS create_db,
                       role.rolcreaterole AS create_role,
                       role.rolreplication AS replication,
                       role.rolbypassrls AS bypass_rls,
                       COALESCE((
                           SELECT array_agg(parent.rolname ORDER BY parent.rolname)
                           FROM pg_auth_members membership
                           JOIN pg_roles parent ON parent.oid = membership.roleid
                           WHERE membership.member = role.oid
                       ), ARRAY[]::name[]) AS memberships,
                       EXISTS (
                           SELECT 1 FROM pg_database database
                           WHERE database.datdba = role.oid
                       ) OR EXISTS (
                           SELECT 1 FROM pg_namespace namespace
                           WHERE namespace.nspowner = role.oid
                       ) OR EXISTS (
                           SELECT 1 FROM pg_class relation
                           WHERE relation.relowner = role.oid
                       ) OR EXISTS (
                           SELECT 1 FROM pg_proc routine
                           WHERE routine.proowner = role.oid
                       ) AS owns_objects
                FROM pg_roles role
                WHERE role.rolname = %s
                """,
                (RUNTIME_LOGIN,),
            )
            runtime_role = dict(cursor.fetchone() or {})
            cursor.execute(
                """
                SELECT relation.relname
                FROM pg_class relation
                JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relname = ANY(%s)
                ORDER BY relation.relname
                """,
                (list(IDENTITY_TABLE_PRIVILEGES),),
            )
            existing_identity_tables = tuple(
                str(row["relname"]) for row in cursor.fetchall()
            )
            identity_schema_required = expected_schema != SAFE_BASELINE_SCHEMA
            if (
                identity_schema_required
                and set(existing_identity_tables) != set(IDENTITY_TABLE_PRIVILEGES)
            ):
                raise ValueError("runtime identity proof is missing reviewed identity tables")
            if not identity_schema_required and existing_identity_tables:
                raise ValueError(
                    "safe-baseline identity membership proof found out-of-order identity tables"
                )
            for table in existing_identity_tables:
                qualified = f"public.{table}"
                cursor.execute(
                    """
                    SELECT relation.relrowsecurity AS row_security_enabled,
                           relation.relforcerowsecurity AS row_security_forced,
                           COALESCE((
                               SELECT array_agg(policy.policyname ORDER BY policy.policyname)
                               FROM pg_policies policy
                               WHERE policy.schemaname = 'public'
                                 AND policy.tablename = %s
                                 AND %s = ANY(policy.roles)
                           ), ARRAY[]::text[]) AS identity_policy_names,
                           COALESCE((
                               SELECT array_agg(policy.cmd ORDER BY policy.cmd)
                               FROM pg_policies policy
                               WHERE policy.schemaname = 'public'
                                 AND policy.tablename = %s
                                 AND %s = ANY(policy.roles)
                           ), ARRAY[]::text[]) AS identity_policy_commands,
                           COALESCE((
                               SELECT array_agg(
                                   privilege.privilege_type
                                   ORDER BY privilege.privilege_type
                               )
                               FROM aclexplode(
                                   COALESCE(relation.relacl, ARRAY[]::aclitem[])
                               ) privilege
                               WHERE privilege.grantee = %s::regrole
                           ), ARRAY[]::text[]) AS direct_privileges,
                           has_table_privilege(%s, %s, 'SELECT') AS can_select,
                           has_table_privilege(%s, %s, 'INSERT') AS can_insert,
                           has_table_privilege(%s, %s, 'UPDATE') AS can_update,
                           has_table_privilege(%s, %s, 'DELETE') AS can_delete,
                           has_table_privilege(%s, %s, 'TRUNCATE') AS can_truncate,
                           has_table_privilege(%s, %s, 'REFERENCES') AS can_references,
                           has_table_privilege(%s, %s, 'TRIGGER') AS can_trigger
                    FROM pg_class relation
                    WHERE relation.oid = to_regclass(%s)
                    """,
                    (
                        table,
                        IDENTITY_SERVICE_GROUP,
                        table,
                        IDENTITY_SERVICE_GROUP,
                        RUNTIME_LOGIN,
                        *([RUNTIME_LOGIN, qualified] * 7),
                        qualified,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(f"runtime identity grant proof is missing {table}")
                identity_tables[table] = dict(row)
    _validate_runtime_identity_grant(
        authority,
        runtime_role,
        identity_tables,
        identity_schema_required=identity_schema_required,
    )
    return {
        "state": (
            "RUNTIME_IDENTITY_GRANT_VERIFIED"
            if identity_schema_required
            else "RUNTIME_IDENTITY_MEMBERSHIP_VERIFIED"
        ),
        "database": authority["database"],
        "schema_revision": expected_schema,
        "identity_schema_state": (
            "PRESENT_AND_VERIFIED"
            if identity_schema_required
            else "ABSENT_BY_SAFE_BASELINE_CONTRACT"
        ),
        "authority": {
            "session_user": authority["session_user"],
            "current_user": authority["current_user"],
            "identity_owner_schema_create": authority[
                "identity_owner_schema_create"
            ],
            "migration_prerequisite_roles": sorted(
                authority["migration_prerequisite_roles"]
            ),
            "migration_reference_tables": sorted(
                table
                for table, allowed in authority[
                    "migration_reference_privileges"
                ].items()
                if allowed
            ),
        },
        "runtime_role": runtime_role,
        "identity_tables": identity_tables,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="PRODUCTION_MIGRATION_DATABASE_URL",
    )
    parser.add_argument("--expected-schema", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    database_url = os.environ.get(args.database_url_env, "").strip()
    if not database_url:
        print("ERROR: protected migration database URL is required", file=sys.stderr)
        return 1
    try:
        proof = prove_runtime_identity_grant(
            database_url,
            expected_schema=args.expected_schema,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(proof, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
        print(json.dumps({"state": proof["state"], "database": proof["database"]}))
        return 0
    except (ValueError, OSError, psycopg2.Error, json.JSONDecodeError) as exc:
        detail = str(exc).replace(database_url, "[REDACTED]")
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
