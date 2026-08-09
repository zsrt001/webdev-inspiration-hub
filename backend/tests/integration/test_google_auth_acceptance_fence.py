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
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
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
            cursor.execute(
                "DELETE FROM release_activations WHERE api_deployment_id = ANY(%s)",
                (deployments,),
            )
            cursor.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", ([str(v) for v in self.user_ids],))

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
            cursor.execute(
                """
                UPDATE release_activations
                SET reservation_expires_at=CURRENT_TIMESTAMP - INTERVAL '1 second'
                WHERE api_deployment_id=%s
                """,
                (deployment_id,),
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
