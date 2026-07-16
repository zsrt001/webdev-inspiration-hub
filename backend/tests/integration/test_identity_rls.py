"""Real PostgreSQL proof for Task 6 identity/session constraints and RLS."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from threading import Barrier
import unittest
import uuid
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config
import psycopg2
from psycopg2 import sql
from sqlalchemy import create_engine


ROOT = Path(__file__).resolve().parents[3]
AUTHENTICATED_LOGIN = "vowpic_authenticated_login_test"
IDENTITY_SERVICE_LOGIN = "vowpic_identity_service_login_test"
LOGIN_PASSWORD = "identity-test-password"

CANONICAL_USER_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
OTHER_USER_ID = uuid.UUID("10000000-0000-0000-0000-000000000002")
LEGACY_USER_ID = uuid.UUID("10000000-0000-0000-0000-000000000003")

IDENTITY_TABLES = (
    "user_identities",
    "oauth_login_intents",
    "auth_sessions",
    "auth_refresh_tokens",
    "account_claim_proofs",
    "identity_email_conflicts",
    "user_account_merges",
    "account_tombstones",
)

USER_OWNED_TABLES_FOR_DOWNGRADE = (
    "users",
    "user_credits",
    "credit_transactions",
    "credit_purchases",
    "orders",
    "live_portrait_jobs",
    "user_subscriptions",
    "subscription_credit_grants",
)

RETAINED_FACT_TABLES_FOR_DOWNGRADE = (
    "credit_transactions",
    "credit_purchases",
    "orders",
    "subscription_credit_grants",
    "user_subscriptions",
    "user_credits",
    "live_portrait_jobs",
)


def _dsn_with_user(database_url: str, username: str, password: str) -> str:
    parsed = urlsplit(database_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    database = parsed.path.lstrip("/")
    return f"postgresql://{username}:{password}@{host}:{port}/{database}"


@unittest.skipUnless(
    os.environ.get("RUN_POSTGRES_INTEGRATION") == "1",
    "set RUN_POSTGRES_INTEGRATION=1 with IDENTITY_RLS_TEST_DATABASE_URL",
)
class IdentityRlsIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.admin_url = os.environ.get("IDENTITY_RLS_TEST_DATABASE_URL", "").strip()
        if not cls.admin_url:
            raise unittest.SkipTest("IDENTITY_RLS_TEST_DATABASE_URL is missing")
        parsed = urlsplit(cls.admin_url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("identity RLS integration requires a local PostgreSQL host")
        if not parsed.path.lstrip("/").endswith("_test"):
            raise RuntimeError("identity RLS integration requires a *_test database")

        cls._reset_database_and_roles()
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
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(
                    sql.Identifier(AUTHENTICATED_LOGIN)
                ),
                (LOGIN_PASSWORD,),
            )
            cursor.execute(sql.SQL("GRANT authenticated TO {}").format(sql.Identifier(AUTHENTICATED_LOGIN)))
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(
                    sql.Identifier(IDENTITY_SERVICE_LOGIN)
                ),
                (LOGIN_PASSWORD,),
            )
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname='vowpic_identity_service'")
            if cursor.fetchone() is not None:
                cursor.execute(
                    sql.SQL("GRANT vowpic_identity_service TO {}").format(
                        sql.Identifier(IDENTITY_SERVICE_LOGIN)
                    )
                )

        cls.authenticated_url = _dsn_with_user(
            cls.admin_url, AUTHENTICATED_LOGIN, LOGIN_PASSWORD
        )
        cls.identity_service_url = _dsn_with_user(
            cls.admin_url, IDENTITY_SERVICE_LOGIN, LOGIN_PASSWORD
        )

    @classmethod
    def _reset_database_and_roles(cls) -> None:
        with psycopg2.connect(cls.admin_url) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("DROP SCHEMA IF EXISTS attacker CASCADE")
                cursor.execute("DROP SCHEMA IF EXISTS auth CASCADE")
                cursor.execute("DROP SCHEMA IF EXISTS public CASCADE")
                cursor.execute("CREATE SCHEMA public")
                cursor.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
                for role in (AUTHENTICATED_LOGIN, IDENTITY_SERVICE_LOGIN):
                    cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname='authenticated'")
                if cursor.fetchone() is None:
                    cursor.execute(
                        "CREATE ROLE authenticated NOLOGIN NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    )
                cursor.execute("CREATE SCHEMA auth")
                cursor.execute(
                    "CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE "
                    "AS $$ SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid $$"
                )
                cursor.execute("GRANT USAGE ON SCHEMA auth TO authenticated")
                cursor.execute("GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated")

    @classmethod
    def tearDownClass(cls) -> None:
        if not getattr(cls, "admin_url", ""):
            return
        with psycopg2.connect(cls.admin_url) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                for role in (AUTHENTICATED_LOGIN, IDENTITY_SERVICE_LOGIN):
                    cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))

    def setUp(self) -> None:
        if self._missing_identity_tables():
            return
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE TABLE " + ", ".join(IDENTITY_TABLES) + ", users CASCADE"
            )
            cursor.execute("SELECT to_regclass('public.identity_legacy_fallback_uses_seq')")
            if cursor.fetchone()[0] is not None:
                cursor.execute("ALTER SEQUENCE identity_legacy_fallback_uses_seq RESTART WITH 1")
            cursor.execute(
                "INSERT INTO users (id, email, auth_provider, auth_subject) VALUES "
                "(%s, 'shared@example.com', 'supabase', 'subject-one'), "
                "(%s, 'other@example.com', NULL, NULL), "
                "(%s, 'shared@example.com', 'supabase', 'legacy-subject')",
                (str(CANONICAL_USER_ID), str(OTHER_USER_ID), str(LEGACY_USER_ID)),
            )
            cursor.execute(
                "INSERT INTO user_identities "
                "(id, user_id, provider, subject, verified_email_snapshot) VALUES "
                "(%s, %s, 'supabase', 'subject-one', 'shared@example.com'), "
                "(%s, %s, 'supabase', 'subject-two', 'other@example.com')",
                (str(uuid.uuid4()), str(CANONICAL_USER_ID), str(uuid.uuid4()), str(OTHER_USER_ID)),
            )

    def _missing_identity_tables(self) -> list[str]:
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename = ANY(%s)",
                (list(IDENTITY_TABLES),),
            )
            existing = {row[0] for row in cursor.fetchall()}
        return sorted(set(IDENTITY_TABLES) - existing)

    def _require_identity_schema(self) -> None:
        self.assertEqual(
            self._missing_identity_tables(),
            [],
            "Task 6 identity/session tables are missing",
        )

    def _current_revision(self) -> str:
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            return str(cursor.fetchone()[0])

    def _fallback_counter_state(self) -> tuple[int, bool]:
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT last_value, is_called FROM identity_legacy_fallback_uses_seq"
            )
            value, is_called = cursor.fetchone()
        return int(value), bool(is_called)

    def _insert_claim_proof(
        self,
        cursor,
        *,
        canonical_user_id: uuid.UUID,
        legacy_user_id: uuid.UUID,
    ) -> uuid.UUID:
        proof_id = uuid.uuid4()
        cursor.execute(
            "INSERT INTO account_claim_proofs "
            "(id, canonical_user_id, legacy_user_id, proof_type, "
            " external_reference_hash, verifier_actor, verified_at, expires_at, audit_request_id) "
            "VALUES (%s, %s, %s, 'VERIFIED_PAYMENT', %s, 'payment-service', "
            " now(), now() + interval '10 minutes', %s)",
            (
                str(proof_id),
                str(canonical_user_id),
                str(legacy_user_id),
                uuid.uuid4().hex * 2,
                f"audit-{uuid.uuid4()}",
            ),
        )
        return proof_id

    @staticmethod
    def _set_claims(cursor, *, subject: str, provider: str = "google") -> None:
        claims = json.dumps({"sub": subject, "app_metadata": {"provider": provider}})
        cursor.execute("SELECT set_config('request.jwt.claims', %s, true)", (claims,))

    @staticmethod
    def _set_raw_claims(cursor, claims: object) -> None:
        cursor.execute(
            "SELECT set_config('request.jwt.claims', %s, true)",
            (json.dumps(claims),),
        )

    def test_identity_tables_exist_and_force_service_only_rls(self) -> None:
        self._require_identity_schema()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relnamespace='public'::regnamespace AND relname = ANY(%s) "
                "ORDER BY relname",
                (list(IDENTITY_TABLES),),
            )
            rows = cursor.fetchall()
            self.assertEqual(len(rows), len(IDENTITY_TABLES))
            self.assertTrue(all(enabled and forced for _, enabled, forced in rows))
            cursor.execute(
                "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname IN ('vowpic_identity_owner', 'vowpic_identity_service') "
                "ORDER BY rolname"
            )
            roles = cursor.fetchall()
            self.assertEqual(len(roles), 2)
            self.assertTrue(
                all(not can_login and not superuser and not bypass for _, can_login, superuser, bypass in roles)
            )
            expected_comments = {
                "openid": "Legacy identity alias; internal compatibility only",
                "username": "Legacy username/profile alias; public password login is retired",
                "password": "Legacy password hash pending identity contract migration",
                "auth_provider": "External auth provider, e.g. supabase/google",
                "auth_subject": "External provider subject/user id",
                "email": "Non-authoritative profile email; identity is provider plus subject",
                "unionid": "Legacy provider identity alias pending contract migration",
            }
            cursor.execute(
                "SELECT attname, col_description(attrelid, attnum) FROM pg_attribute "
                "WHERE attrelid='public.users'::regclass AND attname = ANY(%s)",
                (list(expected_comments),),
            )
            self.assertEqual(dict(cursor.fetchall()), expected_comments)
            privileges = ("SELECT", "INSERT", "UPDATE", "DELETE")
            cursor.execute(
                "SELECT table_name, privilege, "
                "has_table_privilege(%s, format('public.%%I', table_name), privilege) "
                "FROM unnest(%s::text[]) AS table_name "
                "CROSS JOIN unnest(%s::text[]) AS privilege "
                "ORDER BY table_name, privilege",
                (AUTHENTICATED_LOGIN, list(IDENTITY_TABLES), list(privileges)),
            )
            self.assertTrue(all(not allowed for _, _, allowed in cursor.fetchall()))
            cursor.execute(
                "SELECT table_name, privilege, "
                "has_table_privilege(%s, format('public.%%I', table_name), privilege) "
                "FROM unnest(%s::text[]) AS table_name "
                "CROSS JOIN unnest(%s::text[]) AS privilege "
                "ORDER BY table_name, privilege",
                (IDENTITY_SERVICE_LOGIN, list(IDENTITY_TABLES), list(privileges)),
            )
            service_privileges = {
                (table_name, privilege): allowed
                for table_name, privilege, allowed in cursor.fetchall()
            }
            for table_name in IDENTITY_TABLES:
                self.assertTrue(service_privileges[(table_name, "SELECT")])
                self.assertTrue(service_privileges[(table_name, "INSERT")])
                self.assertEqual(
                    service_privileges[(table_name, "UPDATE")],
                    table_name != "user_account_merges",
                )
                self.assertFalse(service_privileges[(table_name, "DELETE")])
            cursor.execute(
                "SELECT tablename FROM pg_policies WHERE schemaname='public' "
                "AND policyname = tablename || '_identity_service_all' "
                "AND 'vowpic_identity_service' = ANY(roles) AND cmd='ALL'"
            )
            self.assertEqual(
                {row[0] for row in cursor.fetchall()},
                set(IDENTITY_TABLES),
            )
            cursor.execute(
                "SELECT owner.rolname, proc.prosecdef, proc.provolatile, proc.proconfig, "
                "has_function_privilege(%s, 'public.app_current_user_id()', 'EXECUTE'), "
                "has_function_privilege(%s, 'public.app_current_user_id()', 'EXECUTE') "
                "FROM pg_proc proc JOIN pg_namespace ns ON ns.oid=proc.pronamespace "
                "JOIN pg_roles owner ON owner.oid=proc.proowner "
                "WHERE ns.nspname='public' AND proc.proname='app_current_user_id'",
                (AUTHENTICATED_LOGIN, IDENTITY_SERVICE_LOGIN),
            )
            owner, security_definer, volatility, config, auth_exec, service_exec = cursor.fetchone()
            self.assertEqual(owner, "vowpic_identity_owner")
            self.assertTrue(security_definer)
            self.assertEqual(volatility, "s")
            self.assertIn("search_path=pg_catalog, public", config)
            self.assertTrue(auth_exec)
            self.assertFalse(service_exec)

    def test_duplicate_subject_email_profile_and_session_family_constraints(self) -> None:
        self._require_identity_schema()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM users WHERE email='shared@example.com'")
            self.assertEqual(cursor.fetchone()[0], 2)
            with self.assertRaises(psycopg2.IntegrityError):
                cursor.execute(
                    "INSERT INTO user_identities (id, user_id, provider, subject) "
                    "VALUES (%s, %s, 'supabase', 'subject-one')",
                    (str(uuid.uuid4()), str(LEGACY_USER_ID)),
                )
            connection.rollback()

        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            with self.assertRaises(psycopg2.IntegrityError):
                cursor.execute(
                    "INSERT INTO user_identities (id, user_id, provider, subject) "
                    "VALUES (%s, %s, 'google', 'provider-must-be-supabase')",
                    (str(uuid.uuid4()), str(LEGACY_USER_ID)),
                )

        for unsafe_redirect in (r"/\evil.example", "/create\nnext"):
            with self.subTest(redirect=repr(unsafe_redirect)):
                with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
                    with self.assertRaises(psycopg2.IntegrityError):
                        cursor.execute(
                            "INSERT INTO oauth_login_intents "
                            "(id, token_hash, browser_binding_hash, redirect_path, expires_at) "
                            "VALUES (%s, %s, %s, %s, now() + interval '10 minutes')",
                            (
                                str(uuid.uuid4()),
                                uuid.uuid4().hex * 2,
                                uuid.uuid4().hex * 2,
                                unsafe_redirect,
                            ),
                        )

        family_id = uuid.uuid4()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO auth_sessions "
                "(id, user_id, family_id, token_version, csrf_token_hash, expires_at) "
                "VALUES (%s, %s, %s, 1, %s, now() + interval '30 days')",
                (str(uuid.uuid4()), str(CANONICAL_USER_ID), str(family_id), "a" * 64),
            )
            with self.assertRaises(psycopg2.IntegrityError):
                cursor.execute(
                    "INSERT INTO auth_sessions "
                    "(id, user_id, family_id, token_version, csrf_token_hash, expires_at) "
                    "VALUES (%s, %s, %s, 1, %s, now() + interval '30 days')",
                    (str(uuid.uuid4()), str(OTHER_USER_ID), str(family_id), "b" * 64),
                )

    def test_claim_proof_is_exact_single_use_and_merge_graph_rejects_cycles(self) -> None:
        self._require_identity_schema()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            proof_id = self._insert_claim_proof(
                cursor,
                canonical_user_id=CANONICAL_USER_ID,
                legacy_user_id=LEGACY_USER_ID,
            )
            wrong_merge_id = uuid.uuid4()
            with self.assertRaises(psycopg2.IntegrityError):
                cursor.execute(
                    "INSERT INTO user_account_merges "
                    "(id, canonical_user_id, legacy_user_id, claim_proof_id, audit_request_id) "
                    "VALUES (%s, %s, %s, %s, 'wrong-binding')",
                    (
                        str(wrong_merge_id),
                        str(CANONICAL_USER_ID),
                        str(OTHER_USER_ID),
                        str(proof_id),
                    ),
                )
            connection.rollback()

        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            proof_id = self._insert_claim_proof(
                cursor,
                canonical_user_id=CANONICAL_USER_ID,
                legacy_user_id=LEGACY_USER_ID,
            )
            merge_id = uuid.uuid4()
            cursor.execute(
                "INSERT INTO user_account_merges "
                "(id, canonical_user_id, legacy_user_id, claim_proof_id, audit_request_id) "
                "VALUES (%s, %s, %s, %s, 'correct-binding')",
                (
                    str(merge_id),
                    str(CANONICAL_USER_ID),
                    str(LEGACY_USER_ID),
                    str(proof_id),
                ),
            )
            cursor.execute(
                "SELECT consumed_at IS NOT NULL, consumed_by_merge_id FROM account_claim_proofs WHERE id=%s",
                (str(proof_id),),
            )
            consumed, consumed_by = cursor.fetchone()
            self.assertTrue(consumed)
            self.assertEqual(uuid.UUID(str(consumed_by)), merge_id)

            cycle_proof = self._insert_claim_proof(
                cursor,
                canonical_user_id=LEGACY_USER_ID,
                legacy_user_id=CANONICAL_USER_ID,
            )
            with self.assertRaises(psycopg2.IntegrityError):
                cursor.execute(
                    "INSERT INTO user_account_merges "
                    "(id, canonical_user_id, legacy_user_id, claim_proof_id, audit_request_id) "
                    "VALUES (%s, %s, %s, %s, 'cycle')",
                    (
                        str(uuid.uuid4()),
                        str(LEGACY_USER_ID),
                        str(CANONICAL_USER_ID),
                        str(cycle_proof),
                    ),
                )

    def test_duplicate_legacy_merge_is_rejected(self) -> None:
        self._require_identity_schema()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            first_proof = self._insert_claim_proof(
                cursor,
                canonical_user_id=CANONICAL_USER_ID,
                legacy_user_id=LEGACY_USER_ID,
            )
            cursor.execute(
                "INSERT INTO user_account_merges "
                "(id, canonical_user_id, legacy_user_id, claim_proof_id, audit_request_id) "
                "VALUES (%s, %s, %s, %s, 'first-merge')",
                (
                    str(uuid.uuid4()),
                    str(CANONICAL_USER_ID),
                    str(LEGACY_USER_ID),
                    str(first_proof),
                ),
            )

        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            duplicate_proof = self._insert_claim_proof(
                cursor,
                canonical_user_id=OTHER_USER_ID,
                legacy_user_id=LEGACY_USER_ID,
            )
            with self.assertRaises(psycopg2.IntegrityError):
                cursor.execute(
                    "INSERT INTO user_account_merges "
                    "(id, canonical_user_id, legacy_user_id, claim_proof_id, audit_request_id) "
                    "VALUES (%s, %s, %s, %s, 'duplicate-legacy')",
                    (
                        str(uuid.uuid4()),
                        str(OTHER_USER_ID),
                        str(LEGACY_USER_ID),
                        str(duplicate_proof),
                    ),
                )

    def test_concurrent_merges_for_one_legacy_user_only_commit_once(self) -> None:
        self._require_identity_schema()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            canonical_proof = self._insert_claim_proof(
                cursor,
                canonical_user_id=CANONICAL_USER_ID,
                legacy_user_id=LEGACY_USER_ID,
            )
            other_proof = self._insert_claim_proof(
                cursor,
                canonical_user_id=OTHER_USER_ID,
                legacy_user_id=LEGACY_USER_ID,
            )

        start = Barrier(2)

        def attempt_merge(canonical_user_id: uuid.UUID, proof_id: uuid.UUID) -> str:
            connection = psycopg2.connect(self.admin_url)
            try:
                with connection.cursor() as cursor:
                    start.wait(timeout=10)
                    cursor.execute(
                        "INSERT INTO user_account_merges "
                        "(id, canonical_user_id, legacy_user_id, claim_proof_id, audit_request_id) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            str(uuid.uuid4()),
                            str(canonical_user_id),
                            str(LEGACY_USER_ID),
                            str(proof_id),
                            f"concurrent-{canonical_user_id}",
                        ),
                    )
                connection.commit()
                return "committed"
            except psycopg2.IntegrityError:
                connection.rollback()
                return "rejected"
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda attempt: attempt_merge(*attempt),
                    (
                        (CANONICAL_USER_ID, canonical_proof),
                        (OTHER_USER_ID, other_proof),
                    ),
                )
            )

        self.assertCountEqual(results, ["committed", "rejected"])
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM user_account_merges WHERE legacy_user_id=%s",
                (str(LEGACY_USER_ID),),
            )
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_refresh_token_hash_and_generation_are_immutable(self) -> None:
        self._require_identity_schema()
        session_id = uuid.uuid4()
        refresh_id = uuid.uuid4()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO auth_sessions "
                "(id, user_id, family_id, token_version, csrf_token_hash, expires_at) "
                "VALUES (%s, %s, %s, 1, %s, now() + interval '30 days')",
                (
                    str(session_id),
                    str(CANONICAL_USER_ID),
                    str(uuid.uuid4()),
                    "e" * 64,
                ),
            )
            cursor.execute(
                "INSERT INTO auth_refresh_tokens "
                "(id, session_id, generation, token_hash, status, expires_at) "
                "VALUES (%s, %s, 1, %s, 'ACTIVE', now() + interval '30 days')",
                (str(refresh_id), str(session_id), "f" * 64),
            )

        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            with self.assertRaises(psycopg2.IntegrityError):
                cursor.execute(
                    "UPDATE auth_refresh_tokens SET token_hash=%s WHERE id=%s",
                    ("0" * 64, str(refresh_id)),
                )

    def test_consumed_revoked_and_resolved_identity_states_cannot_move_backward(self) -> None:
        self._require_identity_schema()
        intent_id = uuid.uuid4()
        session_id = uuid.uuid4()
        refresh_id = uuid.uuid4()
        conflict_id = uuid.uuid4()
        merge_id = uuid.uuid4()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO oauth_login_intents "
                "(id, token_hash, browser_binding_hash, redirect_path, expires_at) "
                "VALUES (%s, %s, %s, '/create', now() + interval '10 minutes')",
                (str(intent_id), "1" * 64, "2" * 64),
            )
            cursor.execute(
                "INSERT INTO auth_sessions "
                "(id, user_id, family_id, token_version, csrf_token_hash, expires_at) "
                "VALUES (%s, %s, %s, 1, %s, now() + interval '30 days')",
                (str(session_id), str(CANONICAL_USER_ID), str(uuid.uuid4()), "3" * 64),
            )
            cursor.execute(
                "INSERT INTO auth_refresh_tokens "
                "(id, session_id, generation, token_hash, status, expires_at) "
                "VALUES (%s, %s, 1, %s, 'ACTIVE', now() + interval '30 days')",
                (str(refresh_id), str(session_id), "4" * 64),
            )
            cursor.execute(
                "INSERT INTO identity_email_conflicts "
                "(id, canonical_user_id, legacy_user_id, email_hmac, discovery_source) "
                "VALUES (%s, %s, %s, %s, 'LOGIN')",
                (
                    str(conflict_id),
                    str(CANONICAL_USER_ID),
                    str(LEGACY_USER_ID),
                    "5" * 64,
                ),
            )
            cursor.execute(
                "INSERT INTO account_tombstones "
                "(user_id, closure_reason, closed_at, audit_request_id) "
                "VALUES (%s, 'USER_REQUEST', now(), 'state-guard-close')",
                (str(OTHER_USER_ID),),
            )
            proof_id = self._insert_claim_proof(
                cursor,
                canonical_user_id=CANONICAL_USER_ID,
                legacy_user_id=LEGACY_USER_ID,
            )
            cursor.execute(
                "INSERT INTO user_account_merges "
                "(id, canonical_user_id, legacy_user_id, claim_proof_id, audit_request_id) "
                "VALUES (%s, %s, %s, %s, 'state-guard-merge')",
                (
                    str(merge_id),
                    str(CANONICAL_USER_ID),
                    str(LEGACY_USER_ID),
                    str(proof_id),
                ),
            )

        with psycopg2.connect(self.identity_service_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE user_identities SET revoked_at=now() WHERE subject='subject-one'"
            )
            cursor.execute(
                "UPDATE oauth_login_intents SET consumed_at=now() WHERE id=%s",
                (str(intent_id),),
            )
            cursor.execute(
                "UPDATE auth_sessions SET token_version=2, csrf_token_hash=%s, revoked_at=now() "
                "WHERE id=%s",
                ("6" * 64, str(session_id)),
            )
            cursor.execute(
                "UPDATE auth_refresh_tokens SET status='USED', used_at=now() WHERE id=%s",
                (str(refresh_id),),
            )
            cursor.execute(
                "UPDATE identity_email_conflicts SET status='RESOLVED_DISTINCT', "
                "resolved_at=now(), resolution_audit_id='state-guard-resolution' WHERE id=%s",
                (str(conflict_id),),
            )
            cursor.execute(
                "UPDATE account_tombstones SET media_cleanup_pending=false WHERE user_id=%s",
                (str(OTHER_USER_ID),),
            )

        rejected_updates = (
            ("UPDATE user_identities SET revoked_at=NULL WHERE subject='subject-one'", ()),
            ("UPDATE oauth_login_intents SET consumed_at=NULL WHERE id=%s", (str(intent_id),)),
            (
                "UPDATE auth_sessions SET token_version=1, revoked_at=NULL WHERE id=%s",
                (str(session_id),),
            ),
            (
                "UPDATE auth_refresh_tokens SET status='ACTIVE', used_at=NULL WHERE id=%s",
                (str(refresh_id),),
            ),
            (
                "UPDATE account_claim_proofs SET consumed_at=NULL, consumed_by_merge_id=NULL "
                "WHERE id=%s",
                (str(proof_id),),
            ),
            (
                "UPDATE identity_email_conflicts SET status='OPEN', resolved_at=NULL, "
                "resolution_audit_id=NULL WHERE id=%s",
                (str(conflict_id),),
            ),
            (
                "UPDATE account_tombstones SET media_cleanup_pending=true WHERE user_id=%s",
                (str(OTHER_USER_ID),),
            ),
        )
        for statement, params in rejected_updates:
            with self.subTest(statement=statement):
                connection = psycopg2.connect(self.identity_service_url)
                try:
                    with connection.cursor() as cursor:
                        with self.assertRaises(psycopg2.IntegrityError):
                            cursor.execute(statement, params)
                    connection.rollback()
                finally:
                    connection.close()

    def test_self_merge_is_rejected(self) -> None:
        self._require_identity_schema()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            self_proof = self._insert_claim_proof(
                cursor,
                canonical_user_id=OTHER_USER_ID,
                legacy_user_id=OTHER_USER_ID,
            )
            with self.assertRaises(psycopg2.IntegrityError):
                cursor.execute(
                    "INSERT INTO user_account_merges "
                    "(id, canonical_user_id, legacy_user_id, claim_proof_id, audit_request_id) "
                    "VALUES (%s, %s, %s, %s, 'self')",
                    (
                        str(uuid.uuid4()),
                        str(OTHER_USER_ID),
                        str(OTHER_USER_ID),
                        str(self_proof),
                    ),
                )

    def test_user_delete_is_restricted_for_tombstone_and_retained_facts(self) -> None:
        self._require_identity_schema()
        retained_tables = (
            "credit_transactions",
            "credit_purchases",
            "orders",
            "subscription_credit_grants",
            "user_subscriptions",
            "user_credits",
            "live_portrait_jobs",
        )
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.conrelid::regclass::text, pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c WHERE c.contype='f' AND c.confrelid='users'::regclass "
                "AND c.conrelid::regclass::text = ANY(%s)",
                (list(retained_tables),),
            )
            definitions = dict(cursor.fetchall())
            self.assertEqual(set(definitions), set(retained_tables))
            self.assertTrue(all("ON DELETE RESTRICT" in value for value in definitions.values()))
            cursor.execute(
                "INSERT INTO account_tombstones "
                "(user_id, closure_reason, closed_at, audit_request_id) "
                "VALUES (%s, 'USER_REQUEST', now(), 'close-test')",
                (str(OTHER_USER_ID),),
            )
            with self.assertRaises(psycopg2.IntegrityError):
                cursor.execute("DELETE FROM users WHERE id=%s", (str(OTHER_USER_ID),))

    def test_authenticated_role_has_own_row_access_without_identity_table_access(self) -> None:
        self._require_identity_schema()
        order_one = uuid.uuid4()
        order_two = uuid.uuid4()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO orders (id, user_id, status, price_cents) VALUES "
                "(%s, %s, 'CREATED', 0), (%s, %s, 'CREATED', 0)",
                (str(order_one), str(CANONICAL_USER_ID), str(order_two), str(OTHER_USER_ID)),
            )
            cursor.execute(
                "INSERT INTO user_identities (id, user_id, provider, subject) "
                "VALUES (%s, %s, 'supabase', '123')",
                (str(uuid.uuid4()), str(OTHER_USER_ID)),
            )
            cursor.execute("CREATE SCHEMA IF NOT EXISTS attacker")
            cursor.execute(
                "CREATE OR REPLACE FUNCTION attacker.app_current_user_id() RETURNS uuid "
                "LANGUAGE sql AS $$ SELECT %s::uuid $$",
                (str(OTHER_USER_ID),),
            )
            cursor.execute("GRANT USAGE ON SCHEMA attacker TO authenticated")
            cursor.execute("GRANT EXECUTE ON FUNCTION attacker.app_current_user_id() TO authenticated")

        with psycopg2.connect(self.authenticated_url) as connection, connection.cursor() as cursor:
            self._set_claims(cursor, subject="subject-one")
            cursor.execute("SET LOCAL search_path = attacker, public")
            cursor.execute("SELECT public.app_current_user_id()")
            self.assertEqual(uuid.UUID(str(cursor.fetchone()[0])), CANONICAL_USER_ID)
            cursor.execute("SELECT id FROM public.orders ORDER BY id")
            visible_order_ids = [uuid.UUID(str(row[0])) for row in cursor.fetchall()]
            self.assertEqual(visible_order_ids, [order_one])
            with self.assertRaises(psycopg2.Error):
                cursor.execute("SELECT subject FROM public.user_identities")
            connection.rollback()

        with psycopg2.connect(self.authenticated_url) as connection, connection.cursor() as cursor:
            self._set_claims(cursor, subject="subject-one")
            with self.assertRaises(psycopg2.Error):
                cursor.execute(
                    "INSERT INTO public.user_identities (id, user_id, provider, subject) "
                    "VALUES (%s, %s, 'supabase', 'forbidden-subject')",
                    (str(uuid.uuid4()), str(CANONICAL_USER_ID)),
                )
            connection.rollback()

        with psycopg2.connect(self.authenticated_url) as connection, connection.cursor() as cursor:
            self._set_claims(cursor, subject="legacy-subject")
            cursor.execute("SELECT public.app_current_user_id()")
            self.assertEqual(uuid.UUID(str(cursor.fetchone()[0])), LEGACY_USER_ID)
            self._set_claims(cursor, subject="subject-one", provider="github")
            cursor.execute("SELECT public.app_current_user_id()")
            self.assertIsNone(cursor.fetchone()[0])
            self._set_raw_claims(
                cursor,
                {
                    "sub": "subject-one",
                    "app_metadata": {"provider": "google"},
                    "is_anonymous": False,
                },
            )
            cursor.execute("SELECT public.app_current_user_id()")
            self.assertEqual(uuid.UUID(str(cursor.fetchone()[0])), CANONICAL_USER_ID)
            for malformed_claims in (
                {
                    "sub": "subject-one",
                    "app_metadata": {"provider": "google"},
                    "is_anonymous": "false",
                },
                {
                    "sub": "subject-one",
                    "app_metadata": {"provider": "google"},
                    "is_anonymous": None,
                },
                {"sub": 123, "app_metadata": {"provider": "google"}},
            ):
                with self.subTest(claims=malformed_claims):
                    self._set_raw_claims(cursor, malformed_claims)
                    cursor.execute("SELECT public.app_current_user_id()")
                    self.assertIsNone(cursor.fetchone()[0])

    def test_legacy_fallback_usage_is_counted_without_exposing_the_counter(self) -> None:
        self._require_identity_schema()
        _, initial_is_called = self._fallback_counter_state()
        self.assertFalse(initial_is_called)

        with psycopg2.connect(self.authenticated_url) as connection, connection.cursor() as cursor:
            self._set_claims(cursor, subject="subject-one")
            cursor.execute("SELECT public.app_current_user_id()")
            self.assertEqual(uuid.UUID(str(cursor.fetchone()[0])), CANONICAL_USER_ID)
        _, normalized_is_called = self._fallback_counter_state()
        self.assertFalse(normalized_is_called)

        with psycopg2.connect(self.authenticated_url) as connection, connection.cursor() as cursor:
            self._set_claims(cursor, subject="legacy-subject")
            cursor.execute("SELECT public.app_current_user_id()")
            self.assertEqual(uuid.UUID(str(cursor.fetchone()[0])), LEGACY_USER_ID)
        fallback_value, fallback_is_called = self._fallback_counter_state()
        self.assertTrue(fallback_is_called)
        self.assertGreaterEqual(fallback_value, 1)

        with psycopg2.connect(self.authenticated_url) as connection, connection.cursor() as cursor:
            with self.assertRaises(psycopg2.Error):
                cursor.execute(
                    "SELECT last_value, is_called FROM identity_legacy_fallback_uses_seq"
                )
            connection.rollback()

        with psycopg2.connect(self.identity_service_url) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT last_value, is_called FROM identity_legacy_fallback_uses_seq")
            self.assertEqual(cursor.fetchone(), (fallback_value, True))

    def test_separately_authorized_identity_service_can_use_identity_tables(self) -> None:
        self._require_identity_schema()
        with psycopg2.connect(self.identity_service_url) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM user_identities")
            self.assertEqual(cursor.fetchone()[0], 2)
            cursor.execute(
                "INSERT INTO oauth_login_intents "
                "(id, token_hash, browser_binding_hash, redirect_path, expires_at) "
                "VALUES (%s, %s, %s, '/create', now() + interval '10 minutes')",
                (str(uuid.uuid4()), "c" * 64, "d" * 64),
            )

    def test_y1_downgrade_rejects_legacy_incompatible_identity_data_atomically(self) -> None:
        self._require_identity_schema()
        revision_before_downgrade = self._current_revision()
        alembic = Config(str(ROOT / "backend" / "alembic.ini"))
        alembic.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
        engine = create_engine(self.admin_url, pool_pre_ping=True)
        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "downgrade is unsafe with NULL openid or duplicate profile emails",
            ):
                with engine.begin() as connection:
                    alembic.attributes["connection"] = connection
                    command.downgrade(alembic, "20260712_0014")
        finally:
            engine.dispose()

        self.assertEqual(self._current_revision(), revision_before_downgrade)
        self.assertEqual(self._missing_identity_tables(), [])

    def test_y2_downgrade_rejects_nonempty_identity_facts_atomically(self) -> None:
        self._require_identity_schema()
        revision_before_downgrade = self._current_revision()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET openid='legacy-' || id::text, "
                "email=id::text || '@example.test'"
            )

        alembic = Config(str(ROOT / "backend" / "alembic.ini"))
        alembic.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
        engine = create_engine(self.admin_url, pool_pre_ping=True)
        try:
            with self.assertRaisesRegex(RuntimeError, "retained identity/session facts"):
                with engine.begin() as connection:
                    alembic.attributes["connection"] = connection
                    command.downgrade(alembic, "20260712_0014")
        finally:
            engine.dispose()

        self.assertEqual(self._current_revision(), revision_before_downgrade)
        self.assertEqual(self._missing_identity_tables(), [])

    def test_z_downgrade_restores_the_previous_schema_contract(self) -> None:
        self._require_identity_schema()
        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE users CASCADE")
            cursor.execute(
                "INSERT INTO users (id, openid, email) VALUES (%s, 'legacy-safe', 'safe@example.com')",
                (str(CANONICAL_USER_ID),),
            )

        alembic = Config(str(ROOT / "backend" / "alembic.ini"))
        alembic.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
        engine = create_engine(self.admin_url, pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                alembic.attributes["connection"] = connection
                command.downgrade(alembic, "20260712_0014")
        finally:
            engine.dispose()

        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cursor.fetchone()[0], "20260712_0014")
            self.assertEqual(self._missing_identity_tables(), sorted(IDENTITY_TABLES))
            cursor.execute(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND p.proname='app_current_user_id'"
            )
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute(
                "SELECT tablename FROM pg_policies WHERE schemaname='public' "
                "AND policyname LIKE '%_select_own' ORDER BY tablename"
            )
            restored_policy_tables = {row[0] for row in cursor.fetchall()}
            self.assertTrue(set(USER_OWNED_TABLES_FOR_DOWNGRADE).issubset(restored_policy_tables))
            cursor.execute(
                "SELECT c.conrelid::regclass::text, pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c WHERE c.contype='f' AND c.confrelid='users'::regclass "
                "AND c.conrelid::regclass::text = ANY(%s)",
                (list(RETAINED_FACT_TABLES_FOR_DOWNGRADE),),
            )
            self.assertTrue(
                all("ON DELETE CASCADE" in definition for _, definition in cursor.fetchall())
            )

        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute("ALTER ROLE vowpic_identity_owner LOGIN BYPASSRLS")

        alembic = Config(str(ROOT / "backend" / "alembic.ini"))
        alembic.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
        engine = create_engine(self.admin_url, pool_pre_ping=True)
        try:
            with self.assertRaisesRegex(RuntimeError, "unsafe pre-existing identity role"):
                with engine.begin() as connection:
                    alembic.attributes["connection"] = connection
                    command.upgrade(alembic, "20260710_0014")
        finally:
            engine.dispose()
            with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "ALTER ROLE vowpic_identity_owner NOLOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                )

        with psycopg2.connect(self.admin_url) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cursor.fetchone()[0], "20260712_0014")
        self.assertEqual(self._missing_identity_tables(), sorted(IDENTITY_TABLES))


if __name__ == "__main__":
    unittest.main()
