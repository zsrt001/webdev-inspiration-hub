"""Reconnect through each application login and prove its exact SQL surface."""

from __future__ import annotations

from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


RUNTIME_LOGIN = "vowpic_app_runtime"
WRITER_LOGIN = "vowpic_control_writer_login"
RUNTIME_GROUP = "vowpic_runtime"
WRITER_GROUP = "vowpic_control_writer"
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
ALLOWED_RUNTIME_PRIVILEGES = {"SELECT", "INSERT", "UPDATE"}


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
                       pg_get_userbyid(control.relowner) = current_user AS owns_control_table,
                       has_table_privilege(current_user, 'public.users', 'SELECT') AS users_select,
                       has_table_privilege(current_user, 'public.users', 'UPDATE') AS users_update,
                       has_table_privilege(current_user, 'public.users', 'DELETE') AS users_delete,
                       has_table_privilege(current_user, 'public.ops_feature_flags', 'SELECT') AS flags_select,
                       has_table_privilege(current_user, 'public.ops_feature_flags', 'UPDATE') AS flags_update
                FROM pg_roles role
                JOIN pg_class control ON control.oid = 'public.ops_feature_flags'::regclass
                WHERE role.rolname = current_user
                """,
                (required_group, forbidden_group),
            )
            row = cursor.fetchone()
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
    ):
        raise ValueError(f"database login {role_name} violates the least-privilege contract")
    if role_name == RUNTIME_LOGIN:
        if not (facts["users_select"] and facts["users_update"] and facts["flags_select"]):
            raise ValueError("runtime login is missing its reviewed SQL surface")
        if facts["flags_update"]:
            raise ValueError("runtime login can mutate control-plane flags")
    elif facts["users_select"] or facts["users_update"] or not facts["flags_update"]:
        raise ValueError("control-writer login has an invalid business/control privilege split")
    return {
        **facts,
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
            business_privileges=business_privileges,
        )
        for role_name, role_url, required_group, forbidden_group in (
            (RUNTIME_LOGIN, runtime_url, RUNTIME_GROUP, WRITER_GROUP),
            (WRITER_LOGIN, writer_url, WRITER_GROUP, RUNTIME_GROUP),
        )
    }
