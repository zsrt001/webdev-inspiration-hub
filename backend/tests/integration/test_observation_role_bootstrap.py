"""Real PostgreSQL proof for the additive observation-role bootstrap."""

from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
from urllib.parse import urlsplit

import psycopg2
from psycopg2 import sql


ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP = (
    ROOT / "scripts" / "release" / "bootstrap_observation_database_roles.sql"
)
MIGRATION_PASSWORD = "observation-bootstrap-migration-test-password"
READER_PASSWORD = "reader-" + "a" * 72
WRITER_PASSWORD = "writer-" + "b" * 72
BASE_GROUPS = (
    "vowpic_runtime",
    "vowpic_control_writer",
    "vowpic_identity_service",
)
OBSERVATION_ROLES = (
    "vowpic_observation_reader_login",
    "vowpic_observation_writer_login",
    "vowpic_observation_reader",
    "vowpic_observation_writer",
)
TEST_ROLES = OBSERVATION_ROLES + (
    "vowpic_migration_login",
    "vowpic_migration_owner",
) + BASE_GROUPS


def _dsn_with_user(database_url: str, username: str, password: str) -> str:
    parsed = urlsplit(database_url)
    return (
        f"postgresql://{username}:{password}@{parsed.hostname or '127.0.0.1'}:"
        f"{parsed.port or 5432}/{parsed.path.lstrip('/')}"
    )


@unittest.skipUnless(
    os.environ.get("RUN_OBSERVATION_ROLE_BOOTSTRAP_INTEGRATION") == "1",
    "set RUN_OBSERVATION_ROLE_BOOTSTRAP_INTEGRATION=1 with the test database URL",
)
class ObservationRoleBootstrapIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.admin_url = os.environ.get(
            "OBSERVATION_ROLE_BOOTSTRAP_TEST_DATABASE_URL", ""
        ).strip()
        if not cls.admin_url:
            raise unittest.SkipTest(
                "OBSERVATION_ROLE_BOOTSTRAP_TEST_DATABASE_URL is missing"
            )
        parsed = urlsplit(cls.admin_url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("observation-role integration requires local PostgreSQL")
        if parsed.path.lstrip("/") != "postgres":
            raise RuntimeError(
                "observation-role integration requires the isolated postgres database"
            )
        cls.source = BOOTSTRAP.read_text(encoding="utf-8")
        cls._assert_isolated_server()
        cls._cleanup()
        cls._setup()

    @classmethod
    def _assert_isolated_server(cls) -> None:
        with psycopg2.connect(cls.admin_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT datname FROM pg_database "
                    "WHERE datallowconn AND NOT datistemplate ORDER BY datname"
                )
                databases = {row[0] for row in cursor.fetchall()}
                unexpected = databases - {"postgres", "vowpic_rls_test"}
                if unexpected:
                    raise RuntimeError(
                        "observation-role integration refused a shared PostgreSQL server"
                    )
                cursor.execute(
                    "SELECT count(*) FROM pg_class relation "
                    "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
                    "WHERE namespace.nspname = 'public' "
                    "AND relation.relname <> 'alembic_version'"
                )
                unexpected_relations = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT count(*) FROM pg_proc procedure "
                    "JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace "
                    "WHERE namespace.nspname = 'public' "
                    "AND procedure.proname <> "
                    "'vowpic_rotate_observation_database_logins'"
                )
                unexpected_routines = cursor.fetchone()[0]
                if unexpected_relations or unexpected_routines:
                    raise RuntimeError(
                        "observation-role integration requires an empty public schema"
                    )

    @classmethod
    def _cleanup(cls) -> None:
        with psycopg2.connect(cls.admin_url) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "DROP FUNCTION IF EXISTS "
                    "public.vowpic_rotate_observation_database_logins(text,text)"
                )
                cursor.execute(
                    "SELECT member.rolname, parent.rolname "
                    "FROM pg_auth_members membership "
                    "JOIN pg_roles member ON member.oid = membership.member "
                    "JOIN pg_roles parent ON parent.oid = membership.roleid "
                    "WHERE member.rolname = ANY(%s)",
                    (list(TEST_ROLES),),
                )
                for member, parent in cursor.fetchall():
                    cursor.execute(
                        sql.SQL("REVOKE {} FROM {}").format(
                            sql.Identifier(parent), sql.Identifier(member)
                        )
                    )
                for role in TEST_ROLES:
                    cursor.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
                    )
                cursor.execute("DROP TABLE IF EXISTS public.alembic_version")

    @classmethod
    def _setup(cls) -> None:
        with psycopg2.connect(cls.admin_url) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("CREATE TABLE public.alembic_version (version_num text)")
                cursor.execute(
                    "INSERT INTO public.alembic_version VALUES ('20260712_0014')"
                )
                for role in BASE_GROUPS:
                    cursor.execute(
                        sql.SQL(
                            "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"
                        ).format(sql.Identifier(role))
                    )
                cursor.execute(
                    "CREATE ROLE vowpic_migration_owner NOLOGIN NOSUPERUSER "
                    "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"
                )
                cursor.execute(
                    "CREATE ROLE vowpic_migration_login LOGIN PASSWORD %s "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                    "NOBYPASSRLS INHERIT",
                    (MIGRATION_PASSWORD,),
                )
                cursor.execute(
                    "GRANT vowpic_migration_owner TO vowpic_migration_login"
                )

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "admin_url", ""):
            cls._cleanup()

    def _execute_bootstrap(self) -> dict[str, object]:
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(self.source)
            row = cursor.fetchone()
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return payload

    def test_bootstrap_is_additive_idempotent_and_migration_only(self) -> None:
        first = self._execute_bootstrap()
        second = self._execute_bootstrap()
        self.assertEqual(first["state"], "READY")
        self.assertEqual(second["state"], "READY")
        self.assertEqual(first["schema_revision"], "20260712_0014")
        self.assertEqual(len(first["roles"]), 4)
        self.assertTrue(
            all(
                not role[flag]
                for role in first["roles"]
                for flag in (
                    "superuser",
                    "create_db",
                    "create_role",
                    "replication",
                    "bypass_rls",
                )
            )
        )
        self.assertTrue(all(not role["can_login"] for role in first["roles"]))

        with psycopg2.connect(self.admin_url) as connection:
            connection.autocommit = True
            cursor = connection.cursor()
            with self.assertRaisesRegex(
                psycopg2.errors.RaiseException,
                "requires the migration login",
            ):
                cursor.execute(
                    "SELECT public.vowpic_rotate_observation_database_logins(%s,%s)",
                    (READER_PASSWORD, WRITER_PASSWORD),
                )
            cursor.close()
        migration_url = _dsn_with_user(
            self.admin_url,
            "vowpic_migration_login",
            MIGRATION_PASSWORD,
        )
        with psycopg2.connect(migration_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.vowpic_rotate_observation_database_logins(%s,%s)",
                (READER_PASSWORD, WRITER_PASSWORD),
            )

        expected = {
            "vowpic_observation_reader_login": (
                READER_PASSWORD,
                "vowpic_observation_reader",
                "on",
            ),
            "vowpic_observation_writer_login": (
                WRITER_PASSWORD,
                "vowpic_observation_writer",
                "off",
            ),
        }
        for login, (password, group, read_only) in expected.items():
            with psycopg2.connect(
                _dsn_with_user(self.admin_url, login, password)
            ) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_user, current_setting("
                    "'default_transaction_read_only'), "
                    "pg_has_role(current_user, %s, 'MEMBER')",
                    (group,),
                )
                self.assertEqual(cursor.fetchone(), (login, read_only, True))

        active = self._execute_bootstrap()
        active_roles = {role["name"]: role for role in active["roles"]}
        self.assertEqual(
            active_roles["vowpic_observation_reader_login"]["memberships"],
            ["vowpic_observation_reader"],
        )
        self.assertEqual(
            active_roles["vowpic_observation_writer_login"]["memberships"],
            ["vowpic_observation_writer"],
        )
        for login, (password, group, read_only) in expected.items():
            with psycopg2.connect(
                _dsn_with_user(self.admin_url, login, password)
            ) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_user, current_setting("
                    "'default_transaction_read_only'), "
                    "pg_has_role(current_user, %s, 'MEMBER')",
                    (group,),
                )
                self.assertEqual(cursor.fetchone(), (login, read_only, True))
