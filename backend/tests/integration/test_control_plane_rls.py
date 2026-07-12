"""Real PostgreSQL proof for the Stage-1 control-plane privilege boundary."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from urllib.parse import urlsplit

import psycopg2
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from app.core.runtime_checks import validate_database_role_proof


ROOT = Path(__file__).resolve().parents[3]
RUNTIME_LOGIN = "vowpic_runtime_login_test"
WRITER_LOGIN = "vowpic_control_login_test"


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
        with psycopg2.connect(cls.admin_url) as connection, connection.cursor() as cursor:
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
        cls.runtime_url = _dsn_with_user(cls.admin_url, RUNTIME_LOGIN, "runtime-test-password")
        cls.writer_url = _dsn_with_user(cls.admin_url, WRITER_LOGIN, "writer-test-password")

    @classmethod
    def tearDownClass(cls) -> None:
        if not getattr(cls, "admin_url", ""):
            return
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
                           role.rolsuper AS role_superuser,
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


if __name__ == "__main__":
    unittest.main()
