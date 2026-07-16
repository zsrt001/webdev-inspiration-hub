"""Real PostgreSQL proof for Partner Invite role and row isolation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
import uuid
from urllib.parse import urlsplit

import psycopg2
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine


ROOT = Path(__file__).resolve().parents[3]
SERVICE_LOGIN = "vowpic_partner_service_login_test"
BROWSER_LOGIN = "vowpic_partner_browser_login_test"


def _dsn_with_user(database_url: str, username: str, password: str) -> str:
    parsed = urlsplit(database_url)
    return (
        f"postgresql://{username}:{password}@{parsed.hostname or '127.0.0.1'}:"
        f"{parsed.port or 5432}/{parsed.path.lstrip('/')}"
    )


@unittest.skipUnless(
    os.environ.get("RUN_POSTGRES_INTEGRATION") == "1",
    "NOT_RUN: set RUN_POSTGRES_INTEGRATION=1 with PARTNER_INVITE_RLS_TEST_DATABASE_URL",
)
class PartnerInviteRlsIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.admin_url = os.environ.get(
            "PARTNER_INVITE_RLS_TEST_DATABASE_URL", ""
        ).strip()
        if not cls.admin_url:
            raise unittest.SkipTest("PARTNER_INVITE_RLS_TEST_DATABASE_URL is missing")
        with psycopg2.connect(cls.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                DO $roles$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                        CREATE ROLE authenticated NOLOGIN NOSUPERUSER NOCREATEDB
                            NOCREATEROLE NOREPLICATION NOBYPASSRLS;
                    END IF;
                END
                $roles$;
                """
            )
        alembic = Config(str(ROOT / "backend" / "alembic.ini"))
        alembic.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
        engine = create_engine(cls.admin_url, pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                alembic.attributes["connection"] = connection
                command.upgrade(alembic, "head")
        finally:
            engine.dispose()

        cls.host_user_id = uuid.uuid4()
        cls.partner_user_id = uuid.uuid4()
        cls.outsider_user_id = uuid.uuid4()
        cls.invite_id = uuid.uuid4()
        identities = {
            cls.host_user_id: (uuid.uuid4(), f"host-{uuid.uuid4()}"),
            cls.partner_user_id: (uuid.uuid4(), f"partner-{uuid.uuid4()}"),
            cls.outsider_user_id: (uuid.uuid4(), f"outsider-{uuid.uuid4()}"),
        }
        cls.subjects = {user_id: subject for user_id, (_, subject) in identities.items()}
        with psycopg2.connect(cls.admin_url) as connection, connection.cursor() as cursor:
            for role_name in (SERVICE_LOGIN, BROWSER_LOGIN):
                cursor.execute(f'DROP ROLE IF EXISTS "{role_name}"')
            cursor.execute(
                f'CREATE ROLE "{SERVICE_LOGIN}" LOGIN PASSWORD %s NOSUPERUSER '
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS",
                ("partner-service-test-password",),
            )
            cursor.execute(
                f'CREATE ROLE "{BROWSER_LOGIN}" LOGIN PASSWORD %s NOSUPERUSER '
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS",
                ("partner-browser-test-password",),
            )
            cursor.execute(f'GRANT vowpic_partner_service TO "{SERVICE_LOGIN}"')
            cursor.execute(f'GRANT authenticated TO "{BROWSER_LOGIN}"')
            for user_id, (identity_id, subject) in identities.items():
                cursor.execute(
                    "INSERT INTO users (id, role, status) VALUES (%s, 'user', 'active')",
                    (str(user_id),),
                )
                cursor.execute(
                    """
                    INSERT INTO user_identities
                        (id, user_id, provider, subject, verified_email_snapshot)
                    VALUES (%s, %s, 'supabase', %s, %s)
                    """,
                    (str(identity_id), str(user_id), subject, f"{subject}@example.test"),
                )
            cursor.execute(
                """
                INSERT INTO partner_invites (
                    id, host_user_id, host_identity_id, partner_user_id,
                    partner_identity_id, token_hash, purpose, order_intent_id,
                    order_intent_hash, intent_policy_version, template_id, status,
                    created_at, expires_at, accepted_at, version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'COUPLE', %s, %s,
                    'partner-consent.v1', 'couple-classic', 'ACCEPTED',
                    statement_timestamp(), statement_timestamp() + interval '1 day',
                    statement_timestamp(), 2
                )
                """,
                (
                    str(cls.invite_id),
                    str(cls.host_user_id),
                    str(identities[cls.host_user_id][0]),
                    str(cls.partner_user_id),
                    str(identities[cls.partner_user_id][0]),
                    "a" * 64,
                    str(uuid.uuid4()),
                    "b" * 64,
                ),
            )
        cls.service_url = _dsn_with_user(
            cls.admin_url,
            SERVICE_LOGIN,
            "partner-service-test-password",
        )
        cls.browser_url = _dsn_with_user(
            cls.admin_url,
            BROWSER_LOGIN,
            "partner-browser-test-password",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if not getattr(cls, "admin_url", ""):
            return
        with psycopg2.connect(cls.admin_url) as connection, connection.cursor() as cursor:
            if getattr(cls, "invite_id", None):
                cursor.execute(
                    "DELETE FROM partner_invites WHERE id = %s",
                    (str(cls.invite_id),),
                )
                cursor.execute(
                    "DELETE FROM user_identities WHERE user_id = ANY(%s::uuid[])",
                    ([str(cls.host_user_id), str(cls.partner_user_id), str(cls.outsider_user_id)],),
                )
                cursor.execute(
                    "DELETE FROM users WHERE id = ANY(%s::uuid[])",
                    ([str(cls.host_user_id), str(cls.partner_user_id), str(cls.outsider_user_id)],),
                )
            cursor.execute(f'REVOKE vowpic_partner_service FROM "{SERVICE_LOGIN}"')
            cursor.execute(f'REVOKE authenticated FROM "{BROWSER_LOGIN}"')
            cursor.execute(f'DROP ROLE IF EXISTS "{SERVICE_LOGIN}"')
            cursor.execute(f'DROP ROLE IF EXISTS "{BROWSER_LOGIN}"')

    def _browser_count(self, user_id: uuid.UUID) -> int:
        claims = {
            "sub": self.subjects[user_id],
            "app_metadata": {"provider": "google"},
            "is_anonymous": False,
        }
        with psycopg2.connect(self.browser_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('request.jwt.claims', %s, false)",
                (json.dumps(claims),),
            )
            cursor.execute("SELECT count(*) FROM partner_invites")
            return int(cursor.fetchone()[0])

    def test_tables_force_rls_and_service_role_cannot_bypass(self) -> None:
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT relname, relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE relnamespace = 'public'::regnamespace
                  AND relname IN (
                    'partner_invites', 'partner_invite_events', 'partner_consent_cases'
                  )
                ORDER BY relname
                """
            )
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(enabled and forced for _, enabled, forced in rows))
            cursor.execute(
                "SELECT rolcanlogin, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = 'vowpic_partner_service'"
            )
            self.assertEqual(cursor.fetchone(), (False, False, False))

    def test_browser_roles_see_only_their_participant_row(self) -> None:
        self.assertEqual(self._browser_count(self.host_user_id), 1)
        self.assertEqual(self._browser_count(self.partner_user_id), 1)
        self.assertEqual(self._browser_count(self.outsider_user_id), 0)

    def test_browser_cannot_read_token_or_mutate_but_service_can_read_hash(self) -> None:
        with psycopg2.connect(self.browser_url) as connection, connection.cursor() as cursor:
            claims = {
                "sub": self.subjects[self.host_user_id],
                "app_metadata": {"provider": "google"},
                "is_anonymous": False,
            }
            cursor.execute(
                "SELECT set_config('request.jwt.claims', %s, false)",
                (json.dumps(claims),),
            )
            with self.assertRaises(psycopg2.Error):
                cursor.execute("SELECT token_hash FROM partner_invites")
            connection.rollback()
            with self.assertRaises(psycopg2.Error):
                cursor.execute(
                    "UPDATE partner_invites SET status = 'REVOKED', version = version + 1 "
                    "WHERE id = %s",
                    (str(self.invite_id),),
                )
            connection.rollback()
        with psycopg2.connect(self.service_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT token_hash FROM partner_invites WHERE id = %s",
                (str(self.invite_id),),
            )
            self.assertEqual(cursor.fetchone()[0], "a" * 64)


if __name__ == "__main__":
    unittest.main()
