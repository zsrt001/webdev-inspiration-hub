from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import unittest

import httpx

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "provision_preview_database_logins.py"
WORKFLOW = ROOT / ".github" / "workflows" / "preview-database-login-repair.yml"
SPEC = importlib.util.spec_from_file_location("provision_preview_database_logins", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Preview database login repair module cannot be loaded")
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


class PreviewDatabaseLoginRepairTests(unittest.TestCase):
    def test_sealed_delivery_round_trips_without_exposing_plaintext(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        public_key, fingerprint = repair.load_delivery_public_key(
            base64.b64encode(public_der).decode("ascii")
        )
        source_sha = "a" * 40
        runtime_url = "postgresql://runtime.example:secret@host/postgres"
        writer_url = "postgresql://writer.example:secret@host/postgres"
        envelope = repair.seal_credentials(
            public_key=public_key,
            public_key_sha256=fingerprint,
            source_sha=source_sha,
            runtime_url=runtime_url,
            writer_url=writer_url,
        )
        self.assertNotIn(runtime_url, json.dumps(envelope))
        self.assertNotIn(writer_url, json.dumps(envelope))
        encrypted_key = base64.b64decode(envelope["encrypted_key_b64"])
        key = private_key.decrypt(
            encrypted_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        plaintext = AESGCM(key).decrypt(
            base64.b64decode(envelope["nonce_b64"]),
            base64.b64decode(envelope["ciphertext_b64"]),
            base64.b64decode(envelope["associated_data_b64"]),
        )
        credentials = json.loads(plaintext)
        self.assertEqual(credentials["runtime_url"], runtime_url)
        self.assertEqual(credentials["writer_url"], writer_url)
        self.assertEqual(credentials["source_sha"], source_sha)

    def test_delivery_rejects_an_undersized_key(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        with self.assertRaisesRegex(ValueError, "RSA-3072"):
            repair.load_delivery_public_key(base64.b64encode(public_der).decode("ascii"))

    def test_management_rotation_uses_parameters_and_always_drops_the_helper(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.headers["Authorization"], "Bearer management-token")
            return httpx.Response(201, json=[])

        runtime_password = "r" * 64
        writer_password = "w" * 64
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            proof = repair.rotate_preview_logins_via_management_api(
                client=client,
                token="management-token",
                project_ref="abcdefghijklmnopqrst",
                source_sha="a" * 40,
                runtime_password=runtime_password,
                writer_password=writer_password,
            )
        self.assertEqual(proof["helper_dropped"], True)
        self.assertEqual(len(requests), 3)
        bodies = [json.loads(request.content) for request in requests]
        create, rotate, drop = bodies
        self.assertIn("SECURITY DEFINER", create["query"])
        self.assertIn("REVOKE ALL ON FUNCTION", create["query"])
        self.assertNotIn(runtime_password, create["query"])
        self.assertNotIn(writer_password, create["query"])
        self.assertIn("$1::text", rotate["query"])
        self.assertIn("$2::text", rotate["query"])
        self.assertNotIn(runtime_password, rotate["query"])
        self.assertNotIn(writer_password, rotate["query"])
        self.assertEqual(rotate["parameters"], [runtime_password, writer_password])
        self.assertRegex(drop["query"], r"^DROP FUNCTION IF EXISTS public\.")

    def test_management_preflight_uses_the_official_read_only_endpoint(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(201, json=[{"ok": 1}])

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            proof = repair.probe_management_database(
                client=client,
                token="management-token",
                project_ref="abcdefghijklmnopqrst",
            )

        self.assertEqual(proof, {"schema": repair.PREFLIGHT_SCHEMA, "state": "AVAILABLE"})
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0].url.path.endswith("/database/query/read-only"))
        body = json.loads(requests[0].content)
        self.assertEqual(body, {"query": "SELECT 1 AS ok"})

    def test_management_status_reads_only_the_exact_project(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={"ref": "abcdefghijklmnopqrst", "status": "INACTIVE"},
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            status = repair.read_management_project_status(
                client=client,
                token="management-token",
                project_ref="abcdefghijklmnopqrst",
            )

        self.assertEqual(status["project_healthy"], False)
        self.assertEqual(status["project_status"], "INACTIVE")
        self.assertTrue(requests[0].url.path.endswith("/projects/abcdefghijklmnopqrst"))
        self.assertEqual(str(requests[0].url.query), "b''")
        self.assertEqual(requests[0].method, "GET")

    def test_management_recovery_restarts_preview_and_proves_query_channel(self) -> None:
        health_reads = 0
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal health_reads
            requests.append(request)
            if request.method == "GET" and request.url.path.endswith("/projects/abcdefghijklmnopqrst"):
                health_reads += 1
                status = "UNHEALTHY" if health_reads == 1 else "ACTIVE_HEALTHY"
                return httpx.Response(
                    200,
                    json={"ref": "abcdefghijklmnopqrst", "status": status},
                )
            if request.url.path.endswith("/restart"):
                return httpx.Response(200, json={})
            if request.url.path.endswith("/database/query/read-only"):
                return httpx.Response(201, json=[{"ok": 1}])
            return httpx.Response(404, json={})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            recovery = repair.recover_management_database(
                client=client,
                token="management-token",
                project_ref="abcdefghijklmnopqrst",
                attempts=2,
                interval_seconds=0,
                sleep=lambda _: None,
            )

        self.assertEqual(recovery["state"], "RECOVERED")
        self.assertEqual(recovery["project_status_before"], "UNHEALTHY")
        self.assertEqual(recovery["project_status_after"], "ACTIVE_HEALTHY")
        self.assertEqual(recovery["read_only_probe"], "AVAILABLE")
        self.assertEqual([request.method for request in requests], ["GET", "POST", "GET", "POST"])

    def test_management_recovery_does_not_restart_an_already_healthy_project(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET" and request.url.path.endswith("/projects/abcdefghijklmnopqrst"):
                return httpx.Response(
                    200,
                    json={"ref": "abcdefghijklmnopqrst", "status": "ACTIVE_HEALTHY"},
                )
            if request.url.path.endswith("/database/query/read-only"):
                return httpx.Response(201, json=[{"ok": 1}])
            return httpx.Response(500, json={})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            recovery = repair.recover_management_database(
                client=client,
                token="management-token",
                project_ref="abcdefghijklmnopqrst",
                attempts=1,
                interval_seconds=0,
                sleep=lambda _: None,
            )

        self.assertEqual(recovery["state"], "ALREADY_HEALTHY")
        self.assertEqual(recovery["restart_requested"], False)
        self.assertEqual([request.method for request in requests], ["GET", "POST"])

    def test_management_rotation_drops_the_helper_after_a_failed_call(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 2:
                return httpx.Response(500, json={"error": "rejected"})
            return httpx.Response(201, json=[])

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(ValueError, "HTTP 500"):
                repair.rotate_preview_logins_via_management_api(
                    client=client,
                    token="management-token",
                    project_ref="abcdefghijklmnopqrst",
                    source_sha="a" * 40,
                    runtime_password="r" * 64,
                    writer_password="w" * 64,
                )
        self.assertEqual(len(requests), 3)
        self.assertIn(
            "DROP FUNCTION IF EXISTS",
            json.loads(requests[-1].content)["query"],
        )

    def test_workflow_is_manual_main_only_preview_only_and_google_free(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$")
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)
        self.assertIn("environment: preview-identity", workflow)
        self.assertIn("provision_preview_database_logins.py", workflow)
        self.assertIn("always() && inputs.operation == 'repair'", workflow)
        self.assertIn("delete_unbound_preview_deployment.py", workflow)
        self.assertIn("delivery_public_key_b64", workflow)
        self.assertIn("operation:", workflow)
        self.assertIn("--management-preflight-only", workflow)
        self.assertIn("--project-status-only", workflow)
        self.assertIn("--restart-unhealthy-project", workflow)
        self.assertIn("database channel without writes", workflow)
        self.assertIn("SUPABASE_MANAGEMENT_TOKEN", workflow)
        self.assertIn("PREVIEW_CONTROL_READ_DATABASE_URL", workflow)
        self.assertNotIn("PREVIEW_GOOGLE_", workflow)
        self.assertNotIn("PREVIEW_MIGRATION_DATABASE_URL", workflow)
        self.assertNotIn("PRODUCTION_RUNTIME_DATABASE_URL", workflow)
        self.assertNotIn("PRODUCTION_MIGRATION_DATABASE_URL", workflow)
        self.assertNotIn("pull_request:", workflow)


if __name__ == "__main__":
    unittest.main()
