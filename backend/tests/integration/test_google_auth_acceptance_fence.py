"""Real PostgreSQL interleavings for the Google-only admission/cleanup fence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from uuid import uuid4

import psycopg2
from psycopg2.extras import RealDictCursor


FENCE = "vowpic-production-capability-activation"
ROOT = Path(__file__).resolve().parents[3]
TRIGGER_TABLES = {
    "trg_acceptance_identity_bindings_no_delete": "acceptance_identity_bindings",
    "trg_release_activations_no_delete": "release_activations",
    "trg_release_activation_regression": "release_activations",
}


def _release_module(name: str):
    path = ROOT / "scripts" / "release" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"integration_{name}", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(
    os.environ.get("RUN_POSTGRES_INTEGRATION") == "1",
    "set RUN_POSTGRES_INTEGRATION=1 with GOOGLE_AUTH_FENCE_TEST_DATABASE_URL",
)
class GoogleAuthAcceptanceFenceIntegrationTest(unittest.TestCase):
    @staticmethod
    def _trigger_states(cursor: object, trigger_names: tuple[str, ...]) -> dict[str, str]:
        cursor.execute(
            """
            SELECT trigger.tgname, trigger.tgenabled
            FROM pg_trigger AS trigger
            WHERE trigger.tgisinternal = false
              AND trigger.tgname = ANY(%s)
            ORDER BY trigger.tgname
            """,
            (list(trigger_names),),
        )
        states = {str(name): str(enabled) for name, enabled in cursor.fetchall()}
        if set(states) != set(trigger_names):
            raise AssertionError("expected named PostgreSQL trigger is missing")
        return states

    @staticmethod
    def _set_trigger(cursor: object, trigger_name: str, *, enabled: bool) -> None:
        table_name = TRIGGER_TABLES.get(trigger_name)
        if table_name is None:
            raise AssertionError("test attempted to alter a non-allowlisted trigger")
        action = "ENABLE" if enabled else "DISABLE"
        cursor.execute(
            f'ALTER TABLE "{table_name}" {action} TRIGGER "{trigger_name}"'
        )

    @staticmethod
    def _control_plane_counts(cursor: object, deployments: list[str]) -> dict[str, int]:
        cursor.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE deployment_id = ANY(%s))::integer AS binding_target,
              COUNT(*) FILTER (
                WHERE deployment_id IS NULL OR NOT (deployment_id = ANY(%s))
              )::integer AS binding_other
            FROM acceptance_identity_bindings
            """,
            (deployments, deployments),
        )
        binding_counts = cursor.fetchone()
        cursor.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE api_deployment_id = ANY(%s))::integer AS activation_target,
              COUNT(*) FILTER (
                WHERE api_deployment_id IS NULL OR NOT (api_deployment_id = ANY(%s))
              )::integer AS activation_other
            FROM release_activations
            """,
            (deployments, deployments),
        )
        activation_counts = cursor.fetchone()
        return {
            "binding_target": int(binding_counts[0]),
            "binding_other": int(binding_counts[1]),
            "activation_target": int(activation_counts[0]),
            "activation_other": int(activation_counts[1]),
        }

    def setUp(self) -> None:
        self.database_url = os.environ.get("GOOGLE_AUTH_FENCE_TEST_DATABASE_URL", "").strip()
        if not self.database_url:
            raise unittest.SkipTest("GOOGLE_AUTH_FENCE_TEST_DATABASE_URL is missing")
        self.deployment_id = f"dpl_test_{uuid4().hex}"
        self.user_ids = (uuid4(), uuid4())
        self.binding_id = uuid4()
        self.activation_id = uuid4()
        self.approval = f"integration-{uuid4().hex}"
        self.extra_deployments: set[str] = set()
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            for index, user_id in enumerate(self.user_ids):
                cursor.execute(
                    "INSERT INTO users (id, email, role, status) VALUES (%s, %s, 'user', 'active')",
                    (str(user_id), f"fence-{index}-{uuid4().hex}@example.com"),
                )
            cursor.execute(
                """
                INSERT INTO release_activations (
                    id, environment, kind, source_sha, runtime_bundle_id,
                    manifest_sha256, api_deployment_id, api_deployment_url,
                    api_role, workflow_run_id, workflow_attempt, phase,
                    phase_rank, version, approval, reservation_expires_at
                ) VALUES (
                    %s, 'production', 'GOOGLE_AUTH_ONLY', %s, %s, %s, %s,
                    'https://integration.invalid', 'COMMERCIAL_7A', %s, 1,
                    'ACCEPTANCE_READY', 1, 1, %s, %s
                )
                """,
                (
                    str(self.activation_id),
                    uuid4().hex + uuid4().hex[:8],
                    "rtb_" + uuid4().hex + uuid4().hex,
                    uuid4().hex + uuid4().hex,
                    self.deployment_id,
                    f"integration-{uuid4().hex}",
                    self.approval,
                    datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
            cursor.execute(
                """
                INSERT INTO acceptance_identity_bindings (
                    id, provider, subject_hmac, environment, deployment_id,
                    expires_at, actor, reason
                ) VALUES (%s, 'google', %s, 'production', %s, %s, 'integration', 'fence proof')
                """,
                (
                    str(self.binding_id),
                    uuid4().hex + uuid4().hex,
                    self.deployment_id,
                    datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )

    def tearDown(self) -> None:
        if not getattr(self, "database_url", ""):
            return
        deployments = [self.deployment_id, *sorted(self.extra_deployments)]
        cleanup_triggers = (
            "trg_acceptance_identity_bindings_no_delete",
            "trg_release_activations_no_delete",
        )
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            before_states = self._trigger_states(cursor, cleanup_triggers)
            self.assertEqual(set(before_states.values()), {"O"})
            before_counts = self._control_plane_counts(cursor, deployments)
            for trigger_name in cleanup_triggers:
                self._set_trigger(cursor, trigger_name, enabled=False)
            cursor.execute(
                """
                DELETE FROM auth_refresh_tokens
                WHERE session_id IN (
                    SELECT session.id FROM auth_sessions AS session
                    JOIN acceptance_identity_bindings AS binding
                      ON binding.id=session.acceptance_binding_id
                    WHERE binding.deployment_id = ANY(%s)
                )
                """,
                (deployments,),
            )
            cursor.execute(
                """
                DELETE FROM auth_sessions
                WHERE acceptance_binding_id IN (
                    SELECT id FROM acceptance_identity_bindings
                    WHERE deployment_id = ANY(%s)
                )
                """,
                (deployments,),
            )
            cursor.execute(
                "DELETE FROM acceptance_identity_bindings WHERE deployment_id = ANY(%s)",
                (deployments,),
            )
            self.assertEqual(cursor.rowcount, before_counts["binding_target"])
            cursor.execute(
                "DELETE FROM release_activations WHERE api_deployment_id = ANY(%s)",
                (deployments,),
            )
            self.assertEqual(cursor.rowcount, before_counts["activation_target"])
            cursor.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", ([str(v) for v in self.user_ids],))
            for trigger_name in reversed(cleanup_triggers):
                self._set_trigger(cursor, trigger_name, enabled=True)
            self.assertEqual(self._trigger_states(cursor, cleanup_triggers), before_states)

        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            self.assertEqual(self._trigger_states(cursor, cleanup_triggers), before_states)
            after_counts = self._control_plane_counts(cursor, deployments)
        self.assertEqual(after_counts["binding_target"], 0)
        self.assertEqual(after_counts["activation_target"], 0)
        self.assertEqual(after_counts["binding_other"], before_counts["binding_other"])
        self.assertEqual(after_counts["activation_other"], before_counts["activation_other"])

    def _expire_activation_for_watchdog(self, *, deployment_id: str, source_sha: str) -> None:
        trigger_names = ("trg_release_activation_regression",)
        time_shift = timedelta(minutes=31)
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            before_states = self._trigger_states(cursor, trigger_names)
            self.assertEqual(before_states, {trigger_names[0]: "O"})
            cursor.execute(
                """
                SELECT id,
                       md5((to_jsonb(activation) - 'created_at' - 'reservation_expires_at')::text),
                       created_at,
                       reservation_expires_at,
                       reservation_expires_at - created_at
                FROM release_activations AS activation
                WHERE api_deployment_id=%s AND source_sha=%s
                """,
                (deployment_id, source_sha),
            )
            before_row = cursor.fetchone()
            self.assertIsNotNone(before_row)
            activation_id, before_fingerprint, before_created_at, before_expiry, before_ttl = before_row
            self.assertGreater(before_ttl, timedelta(0))
            self.assertLessEqual(before_ttl, timedelta(hours=2))
            cursor.execute(
                """
                SELECT COUNT(*),
                       md5(COALESCE(jsonb_agg(to_jsonb(activation) ORDER BY id), '[]'::jsonb)::text)
                FROM release_activations AS activation
                WHERE id <> %s
                """,
                (activation_id,),
            )
            before_other_rows = cursor.fetchone()
            self._set_trigger(cursor, trigger_names[0], enabled=False)
            cursor.execute(
                """
                UPDATE release_activations
                SET created_at=created_at - INTERVAL '31 minutes',
                    reservation_expires_at=reservation_expires_at - INTERVAL '31 minutes'
                WHERE id=%s AND api_deployment_id=%s AND source_sha=%s
                  AND created_at=%s
                  AND reservation_expires_at=%s
                """,
                (activation_id, deployment_id, source_sha, before_created_at, before_expiry),
            )
            self.assertEqual(cursor.rowcount, 1)
            self._set_trigger(cursor, trigger_names[0], enabled=True)
            self.assertEqual(self._trigger_states(cursor, trigger_names), before_states)

            cursor.execute(
                """
                SELECT md5((to_jsonb(activation) - 'created_at' - 'reservation_expires_at')::text),
                       created_at,
                       reservation_expires_at,
                       reservation_expires_at - created_at,
                       reservation_expires_at <= CURRENT_TIMESTAMP
                FROM release_activations AS activation
                WHERE id=%s AND api_deployment_id=%s AND source_sha=%s
                """,
                (activation_id, deployment_id, source_sha),
            )
            after_fingerprint, after_created_at, after_expiry, after_ttl, is_expired = cursor.fetchone()
            self.assertEqual(after_fingerprint, before_fingerprint)
            self.assertEqual(before_created_at - after_created_at, time_shift)
            self.assertEqual(before_expiry - after_expiry, time_shift)
            self.assertEqual(before_created_at - after_created_at, before_expiry - after_expiry)
            self.assertEqual(after_ttl, before_ttl)
            self.assertGreater(after_ttl, timedelta(0))
            self.assertLessEqual(after_ttl, timedelta(hours=2))
            self.assertTrue(is_expired)
            cursor.execute(
                """
                SELECT COUNT(*),
                       md5(COALESCE(jsonb_agg(to_jsonb(activation) ORDER BY id), '[]'::jsonb)::text)
                FROM release_activations AS activation
                WHERE id <> %s
                """,
                (activation_id,),
            )
            self.assertEqual(cursor.fetchone(), before_other_rows)

        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            self.assertEqual(self._trigger_states(cursor, trigger_names), before_states)

    def test_cleaned_google_runtime_bundle_allows_one_new_active_attempt(self) -> None:
        runtime_bundle_id = "rtb_" + uuid4().hex + uuid4().hex
        cleaned_deployment = f"dpl_test_{uuid4().hex}"
        active_deployment = f"dpl_test_{uuid4().hex}"
        self.extra_deployments.update({cleaned_deployment, active_deployment})
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pg_get_expr(index.indpred, index.indrelid)
                FROM pg_index AS index
                JOIN pg_class AS relation ON relation.oid=index.indexrelid
                WHERE relation.relname='uq_release_activation_runtime_bundle'
                """
            )
            predicate = str(cursor.fetchone()[0])
            self.assertIn("GOOGLE_AUTH_ONLY", predicate)
            self.assertIn("CLEANED", predicate)
            cursor.execute(
                """
                INSERT INTO release_activations (
                    id, environment, kind, source_sha, runtime_bundle_id,
                    manifest_sha256, report_sha256, api_deployment_id,
                    api_deployment_url, api_role, workflow_run_id,
                    workflow_attempt, phase, phase_rank, version, approval,
                    reservation_expires_at
                ) VALUES (
                    %s, 'production', 'GOOGLE_AUTH_ONLY', %s, %s, %s, %s, %s,
                    'https://integration.invalid', 'COMMERCIAL_7A', %s, 1,
                    'CLEANED', 2, 2, %s, %s
                )
                """,
                (
                    str(uuid4()),
                    uuid4().hex + uuid4().hex[:8],
                    runtime_bundle_id,
                    uuid4().hex + uuid4().hex,
                    uuid4().hex + uuid4().hex,
                    cleaned_deployment,
                    f"integration-{uuid4().hex}",
                    self.approval,
                    datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
            cursor.execute(
                """
                INSERT INTO release_activations (
                    id, environment, kind, source_sha, runtime_bundle_id,
                    manifest_sha256, api_deployment_id, api_deployment_url,
                    api_role, workflow_run_id, workflow_attempt, phase,
                    phase_rank, version, approval, reservation_expires_at
                ) VALUES (
                    %s, 'production', 'GOOGLE_AUTH_ONLY', %s, %s, %s, %s,
                    'https://integration.invalid', 'COMMERCIAL_7A', %s, 1,
                    'ACCEPTANCE_READY', 1, 1, %s, %s
                )
                """,
                (
                    str(uuid4()),
                    uuid4().hex + uuid4().hex[:8],
                    runtime_bundle_id,
                    uuid4().hex + uuid4().hex,
                    active_deployment,
                    f"integration-{uuid4().hex}",
                    self.approval,
                    datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
            cursor.execute(
                """
                SELECT phase
                FROM release_activations
                WHERE runtime_bundle_id=%s
                ORDER BY phase
                """,
                (runtime_bundle_id,),
            )
            self.assertEqual(
                [row[0] for row in cursor.fetchall()],
                ["ACCEPTANCE_READY", "CLEANED"],
            )

    def test_named_trigger_bypass_rolls_back_on_failure(self) -> None:
        trigger_names = tuple(TRIGGER_TABLES)
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            before_states = self._trigger_states(cursor, trigger_names)
        self.assertEqual(set(before_states.values()), {"O"})

        for trigger_name in trigger_names:
            with self.subTest(trigger_name=trigger_name):
                connection = psycopg2.connect(self.database_url)
                try:
                    with connection.cursor() as cursor:
                        self._set_trigger(cursor, trigger_name, enabled=False)
                        raise RuntimeError("fault injection after trigger disable")
                except RuntimeError:
                    connection.rollback()
                finally:
                    connection.close()
                with psycopg2.connect(self.database_url) as check_connection, check_connection.cursor() as cursor:
                    self.assertEqual(
                        self._trigger_states(cursor, trigger_names),
                        before_states,
                    )

    def _row(self) -> dict:
        with psycopg2.connect(self.database_url) as connection, connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
            cursor.execute(
                "SELECT consumed_user_id, consumed_at, revoked_at FROM acceptance_identity_bindings WHERE id=%s",
                (str(self.binding_id),),
            )
            return dict(cursor.fetchone())

    def _consume(self, user_id: object, started: threading.Event | None = None) -> bool:
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (FENCE,),
            )
            if started is not None:
                started.set()
            cursor.execute(
                """
                SELECT id FROM acceptance_identity_bindings
                WHERE id=%s AND consumed_at IS NULL AND revoked_at IS NULL
                  AND expires_at > CURRENT_TIMESTAMP
                FOR UPDATE
                """,
                (str(self.binding_id),),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                "UPDATE acceptance_identity_bindings SET consumed_user_id=%s, consumed_at=CURRENT_TIMESTAMP WHERE id=%s",
                (str(user_id), str(self.binding_id)),
            )
            return True

    def _revoke(self, started: threading.Event | None = None) -> bool:
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (FENCE,),
            )
            if started is not None:
                started.set()
            cursor.execute(
                """
                UPDATE acceptance_identity_bindings SET revoked_at=CURRENT_TIMESTAMP
                WHERE id=%s AND consumed_at IS NULL AND consumed_user_id IS NULL AND revoked_at IS NULL
                """,
                (str(self.binding_id),),
            )
            return cursor.rowcount == 1

    def test_two_concurrent_admissions_consume_exactly_once(self) -> None:
        barrier = threading.Barrier(2)
        results: list[bool] = []

        def worker(user_id: object) -> None:
            barrier.wait(timeout=5)
            results.append(self._consume(user_id))

        threads = [threading.Thread(target=worker, args=(user_id,)) for user_id in self.user_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(results), [False, True])
        row = self._row()
        self.assertIsNotNone(row["consumed_at"])
        self.assertIsNone(row["revoked_at"])

    def test_admission_first_prevents_cleanup_from_revoking_consumed_binding(self) -> None:
        started = threading.Event()
        result: list[bool] = []
        thread = threading.Thread(target=lambda: result.append(self._consume(self.user_ids[0], started)))
        thread.start()
        self.assertTrue(started.wait(timeout=5))
        revoked = self._revoke()
        thread.join(timeout=10)
        self.assertEqual(result, [True])
        self.assertFalse(revoked)
        self.assertIsNone(self._row()["revoked_at"])

    def test_cleanup_first_prevents_late_admission(self) -> None:
        self.assertTrue(self._revoke())
        self.assertFalse(self._consume(self.user_ids[0]))
        row = self._row()
        self.assertIsNone(row["consumed_at"])
        self.assertIsNotNone(row["revoked_at"])

    def test_real_cleanup_revokes_session_and_refresh_idempotently(self) -> None:
        session_id = uuid4()
        refresh_id = uuid4()
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE acceptance_identity_bindings SET consumed_user_id=%s, consumed_at=CURRENT_TIMESTAMP WHERE id=%s",
                (str(self.user_ids[0]), str(self.binding_id)),
            )
            cursor.execute(
                """
                INSERT INTO auth_sessions (
                    id, user_id, acceptance_binding_id, family_id,
                    token_version, csrf_token_hash, expires_at
                ) VALUES (%s, %s, %s, %s, 1, %s, %s)
                """,
                (
                    str(session_id),
                    str(self.user_ids[0]),
                    str(self.binding_id),
                    str(uuid4()),
                    uuid4().hex + uuid4().hex,
                    datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
            cursor.execute(
                """
                INSERT INTO auth_refresh_tokens (
                    id, session_id, generation, token_hash, status, expires_at
                ) VALUES (%s, %s, 1, %s, 'ACTIVE', %s)
                """,
                (
                    str(refresh_id),
                    str(session_id),
                    uuid4().hex + uuid4().hex,
                    datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )

        cleanup = _release_module("cleanup_google_auth_sessions")
        first = cleanup.cleanup_sessions(
            self.database_url,
            deployment_id=self.deployment_id,
            approval=self.approval,
        )
        second = cleanup.cleanup_sessions(
            self.database_url,
            deployment_id=self.deployment_id,
            approval=self.approval,
        )
        self.assertTrue(first["passed"])
        self.assertEqual(first["revoked_now"], 1)
        self.assertEqual(first["after_unrevoked"], 0)
        self.assertTrue(second["passed"])
        self.assertEqual(second["revoked_now"], 0)
        with psycopg2.connect(self.database_url) as connection, connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
            cursor.execute(
                "SELECT revoked_at, token_version FROM auth_sessions WHERE id=%s",
                (str(session_id),),
            )
            session = dict(cursor.fetchone())
            cursor.execute(
                "SELECT status, revoked_at FROM auth_refresh_tokens WHERE id=%s",
                (str(refresh_id),),
            )
            refresh = dict(cursor.fetchone())
        self.assertIsNotNone(session["revoked_at"])
        self.assertEqual(session["token_version"], 2)
        self.assertEqual(refresh["status"], "REVOKED")
        self.assertIsNotNone(refresh["revoked_at"])

    def test_forced_runner_death_is_reaped_by_real_watchdog_idempotently(self) -> None:
        deployment_id = f"dpl_forced_death_{uuid4().hex}"
        self.extra_deployments.add(deployment_id)
        source_sha = uuid4().hex + uuid4().hex[:8]
        approval = f"forced-death-{uuid4().hex}"
        coordinates = {
            "source_sha": source_sha,
            "runtime_bundle_id": "rtb_" + uuid4().hex + uuid4().hex,
            "deployment_id": deployment_id,
            "api_deployment_url": "https://forced-death.integration.invalid",
            "release_role": "COMMERCIAL_7A",
            "runtime_environment": "production",
            "schema_revision": "20260710_0021",
        }
        child = r"""
import importlib.util
import json
import os
from pathlib import Path
import time

root = Path(os.environ["GOOGLE_AUTH_TEST_ROOT"])
path = root / "scripts/release/manage_google_auth_only_activation.py"
spec = importlib.util.spec_from_file_location("forced_death_manage", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
report = module.reserve_activation(
    os.environ["GOOGLE_AUTH_FENCE_TEST_DATABASE_URL"],
    coordinates=json.loads(os.environ["GOOGLE_AUTH_TEST_COORDINATES"]),
    approval=os.environ["GOOGLE_AUTH_TEST_APPROVAL"],
    workflow_run_id="forced-runner-death",
    workflow_attempt=1,
)
module._write_create_once(Path(os.environ["GOOGLE_AUTH_TEST_MARKER"]), report)
time.sleep(600)
"""
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "reservation.json"
            environment = {
                **os.environ,
                "GOOGLE_AUTH_TEST_ROOT": str(ROOT),
                "GOOGLE_AUTH_TEST_COORDINATES": json.dumps(coordinates, sort_keys=True),
                "GOOGLE_AUTH_TEST_APPROVAL": approval,
                "GOOGLE_AUTH_TEST_MARKER": str(marker),
                "GOOGLE_AUTH_FENCE_TEST_DATABASE_URL": self.database_url,
            }
            process = subprocess.Popen(
                [sys.executable, "-c", child],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 20
            while not marker.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    self.fail("interactive activation runner exited before forced termination")
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "interactive activation runner did not reserve")
            reservation = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(reservation["deployment_id"], deployment_id)
            self.assertEqual(reservation["phase"], "ACCEPTANCE_READY")
            process.kill()
            process.wait(timeout=10)
            if process.stderr is not None:
                process.stderr.close()
            self.assertNotEqual(process.returncode, 0)

        consumed_binding = uuid4()
        unused_binding = uuid4()
        session_id = uuid4()
        refresh_id = uuid4()
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO acceptance_identity_bindings (
                    id, provider, subject_hmac, environment, deployment_id,
                    expires_at, actor, reason, consumed_user_id, consumed_at
                ) VALUES
                  (%s, 'google', %s, 'production', %s, %s, 'integration',
                   'forced runner death consumed', %s, CURRENT_TIMESTAMP),
                  (%s, 'google_email', %s, 'production', %s, %s, 'integration',
                   'forced runner death unused', NULL, NULL)
                """,
                (
                    str(consumed_binding),
                    uuid4().hex + uuid4().hex,
                    deployment_id,
                    datetime.now(timezone.utc) + timedelta(minutes=5),
                    str(self.user_ids[0]),
                    str(unused_binding),
                    uuid4().hex + uuid4().hex,
                    deployment_id,
                    datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
            cursor.execute(
                """
                INSERT INTO auth_sessions (
                    id, user_id, acceptance_binding_id, family_id,
                    token_version, csrf_token_hash, expires_at
                ) VALUES (%s, %s, %s, %s, 1, %s, %s)
                """,
                (
                    str(session_id),
                    str(self.user_ids[0]),
                    str(consumed_binding),
                    str(uuid4()),
                    uuid4().hex + uuid4().hex,
                    datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
            cursor.execute(
                """
                INSERT INTO auth_refresh_tokens (
                    id, session_id, generation, token_hash, status, expires_at
                ) VALUES (%s, %s, 1, %s, 'ACTIVE', %s)
                """,
                (
                    str(refresh_id),
                    str(session_id),
                    uuid4().hex + uuid4().hex,
                    datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        self._expire_activation_for_watchdog(
            deployment_id=deployment_id,
            source_sha=source_sha,
        )

        manage = _release_module("manage_google_auth_only_activation")
        activation_plan = json.loads(
            (ROOT / "release/activation-plan.json").read_text(encoding="utf-8")
        )
        first = manage.reap_expired_activations(
            self.database_url,
            activation_plan=activation_plan,
        )
        second = manage.reap_expired_activations(
            self.database_url,
            activation_plan=activation_plan,
        )
        self.assertTrue(first["passed"])
        self.assertEqual(first["reaped_count"], 1)
        self.assertEqual(first["reaped"], [{"source_sha": source_sha, "deployment_id": deployment_id}])
        self.assertTrue(second["passed"])
        self.assertEqual(second["reaped_count"], 0)

        with psycopg2.connect(self.database_url) as connection, connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
            cursor.execute(
                "SELECT phase FROM release_activations WHERE api_deployment_id=%s",
                (deployment_id,),
            )
            self.assertEqual(cursor.fetchone()["phase"], "CLEANED")
            cursor.execute(
                """
                SELECT COUNT(*)::integer AS invalid
                FROM ops_feature_flags
                WHERE environment='production' AND (
                    state <> 'OFF' OR deployment_id IS NOT NULL
                    OR runtime_bundle_id IS NOT NULL OR worker_image_digest IS NOT NULL
                    OR release_activation_id IS NOT NULL OR target_manifest_sha256 IS NOT NULL
                    OR expires_at IS NOT NULL
                )
                """
            )
            self.assertEqual(cursor.fetchone()["invalid"], 0)
            cursor.execute(
                """
                SELECT COUNT(*)::integer AS active
                FROM auth_sessions AS session
                JOIN acceptance_identity_bindings AS binding
                  ON binding.id=session.acceptance_binding_id
                WHERE binding.deployment_id=%s AND session.revoked_at IS NULL
                """,
                (deployment_id,),
            )
            self.assertEqual(cursor.fetchone()["active"], 0)
            cursor.execute(
                """
                SELECT COUNT(*)::integer AS active
                FROM auth_refresh_tokens AS refresh
                JOIN auth_sessions AS session ON session.id=refresh.session_id
                JOIN acceptance_identity_bindings AS binding
                  ON binding.id=session.acceptance_binding_id
                WHERE binding.deployment_id=%s AND refresh.status='ACTIVE'
                """,
                (deployment_id,),
            )
            self.assertEqual(cursor.fetchone()["active"], 0)
            cursor.execute(
                """
                SELECT COUNT(*)::integer AS live
                FROM acceptance_identity_bindings
                WHERE deployment_id=%s AND consumed_at IS NULL AND revoked_at IS NULL
                """,
                (deployment_id,),
            )
            self.assertEqual(cursor.fetchone()["live"], 0)
