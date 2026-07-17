"""Real PostgreSQL proof for the Stage-1 control-plane privilege boundary."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from urllib.parse import urlsplit

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from app.core.database_role_proof import validate_database_role_proof


ROOT = Path(__file__).resolve().parents[3]
RELEASE_SCRIPTS = ROOT / "scripts" / "release"
if str(RELEASE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RELEASE_SCRIPTS))

from provision_production_database_logins import (  # noqa: E402
    RUNTIME_LOGIN as PRODUCTION_RUNTIME_LOGIN,
    WRITER_LOGIN as PRODUCTION_WRITER_LOGIN,
    configure_safe_baseline_database_roles,
    load_database_role_contract,
    provision_database_logins,
)


RUNTIME_LOGIN = "vowpic_runtime_login_test"
WRITER_LOGIN = "vowpic_control_login_test"
MIGRATION_LOGIN = "vowpic_migration_login_test"
MIGRATION_PASSWORD = "migration-test-password"


def _dsn_with_user(database_url: str, username: str, password: str) -> str:
    parsed = urlsplit(database_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    database = parsed.path.lstrip("/")
    return f"postgresql://{username}:{password}@{host}:{port}/{database}"


@unittest.skipUnless(
    os.environ.get("RUN_POSTGRES_INTEGRATION") == "1",
    "set RUN_POSTGRES_INTEGRATION=1 with CONTROL_PLANE_RLS_TEST_DATABASE_URL",
)
class ControlPlaneRlsIntegrationTest(unittest.TestCase):
    @classmethod
    def _drop_migration_login(cls) -> None:
        with psycopg2.connect(cls.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)", (MIGRATION_LOGIN,))
            if cursor.fetchone()[0]:
                cursor.execute(f'REVOKE vowpic_migration_owner FROM "{MIGRATION_LOGIN}"')
                cursor.execute(f'DROP ROLE "{MIGRATION_LOGIN}"')

    @classmethod
    def _drop_production_logins(cls) -> None:
        with psycopg2.connect(cls.admin_url) as connection, connection.cursor() as cursor:
            for login, group in (
                (PRODUCTION_RUNTIME_LOGIN, "vowpic_runtime"),
                (PRODUCTION_WRITER_LOGIN, "vowpic_control_writer"),
            ):
                cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)", (login,))
                if cursor.fetchone()[0]:
                    cursor.execute(f'REVOKE "{group}" FROM "{login}"')
                    cursor.execute(f'DROP ROLE "{login}"')

    @classmethod
    def setUpClass(cls) -> None:
        cls.admin_url = os.environ.get("CONTROL_PLANE_RLS_TEST_DATABASE_URL", "").strip()
        if not cls.admin_url:
            raise unittest.SkipTest("CONTROL_PLANE_RLS_TEST_DATABASE_URL is missing")
        alembic = Config(str(ROOT / "backend" / "alembic.ini"))
        alembic.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
        engine = create_engine(cls.admin_url, pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                alembic.attributes["connection"] = connection
                command.upgrade(alembic, "head")
        finally:
            engine.dispose()
        cls._drop_migration_login()
        database_name = urlsplit(cls.admin_url).path.lstrip("/")
        with psycopg2.connect(cls.admin_url) as connection, connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
            configure_safe_baseline_database_roles(cursor, load_database_role_contract())
            cursor.execute(f'DROP ROLE IF EXISTS "{RUNTIME_LOGIN}"')
            cursor.execute(f'DROP ROLE IF EXISTS "{WRITER_LOGIN}"')
            cursor.execute(
                f'CREATE ROLE "{RUNTIME_LOGIN}" LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB '
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS",
                ("runtime-test-password",),
            )
            cursor.execute(
                f'CREATE ROLE "{WRITER_LOGIN}" LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB '
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS",
                ("writer-test-password",),
            )
            cursor.execute(f'GRANT vowpic_runtime TO "{RUNTIME_LOGIN}"')
            cursor.execute(f'GRANT vowpic_control_writer TO "{WRITER_LOGIN}"')
            cursor.execute(
                f'CREATE ROLE "{MIGRATION_LOGIN}" LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB '
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT",
                (MIGRATION_PASSWORD,),
            )
            cursor.execute(f'GRANT vowpic_migration_owner TO "{MIGRATION_LOGIN}"')
            cursor.execute(
                sql.SQL("ALTER ROLE {} IN DATABASE {} SET role TO 'vowpic_migration_owner'").format(
                    sql.Identifier(MIGRATION_LOGIN),
                    sql.Identifier(database_name),
                )
            )
        cls.runtime_url = _dsn_with_user(cls.admin_url, RUNTIME_LOGIN, "runtime-test-password")
        cls.writer_url = _dsn_with_user(cls.admin_url, WRITER_LOGIN, "writer-test-password")
        cls.migration_url = _dsn_with_user(cls.admin_url, MIGRATION_LOGIN, MIGRATION_PASSWORD)

    @classmethod
    def tearDownClass(cls) -> None:
        if not getattr(cls, "admin_url", ""):
            return
        cls._drop_migration_login()
        with psycopg2.connect(cls.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(f'REVOKE vowpic_runtime FROM "{RUNTIME_LOGIN}"')
            cursor.execute(f'REVOKE vowpic_control_writer FROM "{WRITER_LOGIN}"')
            cursor.execute(f'DROP ROLE IF EXISTS "{RUNTIME_LOGIN}"')
            cursor.execute(f'DROP ROLE IF EXISTS "{WRITER_LOGIN}"')

    def test_control_plane_tables_force_rls_and_group_roles_cannot_bypass(self) -> None:
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT relname, relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE relnamespace = 'public'::regnamespace
                  AND relname IN ('ops_feature_flags', 'release_activations', 'acceptance_identity_bindings')
                ORDER BY relname
                """
            )
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(enabled and forced for _, enabled, forced in rows))
            cursor.execute(
                "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname IN ('vowpic_runtime', 'vowpic_control_writer') ORDER BY rolname"
            )
            roles = cursor.fetchall()
            self.assertEqual(len(roles), 2)
            self.assertTrue(all(not can_login and not superuser and not bypass for _, can_login, superuser, bypass in roles))

    def test_migration_login_can_reserve_through_forced_rls_without_bypass(self) -> None:
        connection = psycopg2.connect(self.migration_url)
        try:
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
                self.assertEqual(
                    cursor.fetchone(),
                    (MIGRATION_LOGIN, "vowpic_migration_owner", False, False, False, False, False, True),
                )
                cursor.execute(
                    """
                    SELECT has_table_privilege(current_user, 'release_activations', 'INSERT'),
                           count(*) = %s
                    FROM pg_policies
                    WHERE schemaname = 'public'
                      AND policyname LIKE '%%_migration_owner_all'
                      AND 'vowpic_migration_owner' = ANY(roles)
                    """,
                    (8,),
                )
                self.assertEqual(cursor.fetchone(), (True, True))
                cursor.execute(
                    """
                    INSERT INTO release_activations (
                      id, environment, kind, source_sha, workflow_run_id, workflow_attempt,
                      phase, phase_rank, version, approval, reservation_expires_at,
                      current_snapshot_hash, private_evidence_prefix
                    ) VALUES (
                      '00000000-0000-4000-8000-000000000013', 'production',
                      'SAFE_BASELINE_INSTALL', %s, 'migration-rls-test', 1,
                      'RESERVED', 0, 1, 'integration-test',
                      CURRENT_TIMESTAMP + INTERVAL '60 minutes', %s,
                      'https://example.invalid/evidence'
                    )
                    RETURNING phase
                    """,
                    ("a" * 40, "b" * 64),
                )
                self.assertEqual(cursor.fetchone()[0], "RESERVED")
        finally:
            connection.rollback()
            connection.close()

    def test_runtime_can_read_but_cannot_mutate_flags(self) -> None:
        with psycopg2.connect(self.runtime_url) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM ops_feature_flags")
            self.assertEqual(cursor.fetchone()[0], 14)
            cursor.execute(
                "SELECT has_column_privilege(current_user, 'acceptance_identity_bindings', 'consumed_at', 'UPDATE'), "
                "has_table_privilege(current_user, 'ops_feature_flags', 'UPDATE')"
            )
            binding_update, flag_update = cursor.fetchone()
            self.assertTrue(binding_update)
            self.assertFalse(flag_update)
            with self.assertRaises(psycopg2.Error):
                cursor.execute(
                    "UPDATE ops_feature_flags SET state='ON', version=version+1 "
                    "WHERE environment='production' AND capability='generation'"
                )
            connection.rollback()

    def test_runtime_business_privileges_are_explicit_and_writer_has_none(self) -> None:
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tablename, cmd
                FROM pg_policies
                WHERE schemaname = 'public'
                  AND tablename IN ('users', 'orders', 'click_stats')
                  AND policyname LIKE '%_vowpic_runtime_%'
                  AND 'vowpic_runtime' = ANY(roles)
                ORDER BY tablename, cmd
                """
            )
            self.assertEqual(
                cursor.fetchall(),
                [
                    ("click_stats", "INSERT"),
                    ("click_stats", "SELECT"),
                    ("click_stats", "UPDATE"),
                    ("orders", "INSERT"),
                    ("orders", "SELECT"),
                    ("orders", "UPDATE"),
                    ("users", "INSERT"),
                    ("users", "SELECT"),
                    ("users", "UPDATE"),
                ],
            )
        with psycopg2.connect(self.runtime_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT has_table_privilege(current_user, 'users', 'SELECT'),
                       has_table_privilege(current_user, 'users', 'INSERT'),
                       has_table_privilege(current_user, 'users', 'UPDATE'),
                       has_table_privilege(current_user, 'users', 'DELETE'),
                       has_table_privilege(current_user, 'subscription_plans', 'SELECT'),
                       has_table_privilege(current_user, 'subscription_plans', 'UPDATE')
                """
            )
            self.assertEqual(cursor.fetchone(), (True, True, True, False, True, False))
        with psycopg2.connect(self.writer_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT has_table_privilege(current_user, 'users', 'SELECT'),
                       has_table_privilege(current_user, 'users', 'INSERT'),
                       has_table_privilege(current_user, 'users', 'UPDATE'),
                       has_table_privilege(current_user, 'users', 'DELETE')
                """
            )
            self.assertEqual(cursor.fetchone(), (False, False, False, False))

    def test_control_writer_can_apply_an_audited_off_transition(self) -> None:
        with psycopg2.connect(self.writer_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE ops_feature_flags SET state='OFF', version=version+1 "
                "WHERE environment='production' AND capability='generation' RETURNING state"
            )
            self.assertEqual(cursor.fetchone()[0], "OFF")

    def test_runtime_readiness_rejects_accidental_writer_group_membership(self) -> None:
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(f'GRANT vowpic_control_writer TO "{RUNTIME_LOGIN}"')
        try:
            with psycopg2.connect(self.runtime_url) as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT current_user AS current_user,
                           role.rolcanlogin AS role_can_login,
                           role.rolinherit AS role_inherit,
                           role.rolsuper AS role_superuser,
                           role.rolcreatedb AS role_create_db,
                           role.rolcreaterole AS role_create_role,
                           role.rolreplication AS role_replication,
                           role.rolbypassrls AS role_bypass_rls,
                           pg_get_userbyid(control.relowner) AS control_table_owner,
                           pg_has_role(current_user, 'vowpic_runtime', 'MEMBER') AS required_group_member,
                           pg_has_role(current_user, 'vowpic_control_writer', 'MEMBER') AS forbidden_group_member
                    FROM pg_roles AS role
                    JOIN pg_class AS control
                      ON control.oid = 'public.ops_feature_flags'::regclass
                    WHERE role.rolname = current_user
                    """
                )
                columns = [description.name for description in cursor.description]
                proof = dict(zip(columns, cursor.fetchone(), strict=True))
            with self.assertRaises(RuntimeError):
                validate_database_role_proof(
                    proof,
                    required_group="vowpic_runtime",
                    forbidden_group="vowpic_control_writer",
                )
        finally:
            with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
                cursor.execute(f'REVOKE vowpic_control_writer FROM "{RUNTIME_LOGIN}"')

    def test_provisioner_creates_two_fixed_non_owner_logins_and_proves_split(self) -> None:
        self._drop_production_logins()
        try:
            runtime_url, writer_url, proof = provision_database_logins(self.admin_url)
            self.assertEqual(proof["credential_rotation"], "superuser_test_fallback")
            self.assertEqual(urlsplit(runtime_url).username, PRODUCTION_RUNTIME_LOGIN)
            self.assertEqual(urlsplit(writer_url).username, PRODUCTION_WRITER_LOGIN)
            runtime = proof["roles"][PRODUCTION_RUNTIME_LOGIN]
            writer = proof["roles"][PRODUCTION_WRITER_LOGIN]
            self.assertTrue(runtime["required_group_member"])
            self.assertFalse(runtime["forbidden_group_member"])
            self.assertTrue(runtime["users_update"])
            self.assertFalse(runtime["flags_update"])
            self.assertFalse(runtime["users_delete"])
            self.assertTrue(runtime["business_tables"]["users"]["row_security_enabled"])
            self.assertEqual(
                set(runtime["business_tables"]["users"]["runtime_policy_commands"]),
                {"SELECT", "INSERT", "UPDATE"},
            )
            self.assertFalse(runtime["business_tables"]["users"]["can_delete"])
            self.assertTrue(writer["required_group_member"])
            self.assertFalse(writer["forbidden_group_member"])
            self.assertFalse(writer["users_select"])
            self.assertTrue(writer["flags_update"])
            self.assertFalse(writer["business_tables"]["users"]["can_select"])
        finally:
            self._drop_production_logins()


if __name__ == "__main__":
    unittest.main()
