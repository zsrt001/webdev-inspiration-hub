from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import unittest

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

    def test_workflow_is_manual_main_only_preview_only_and_google_free(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$")
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)
        self.assertIn("environment: preview-identity", workflow)
        self.assertIn("provision_preview_database_logins.py", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("delete_unbound_preview_deployment.py", workflow)
        self.assertIn("delivery_public_key_b64", workflow)
        self.assertNotIn("PREVIEW_GOOGLE_", workflow)
        self.assertNotIn("PRODUCTION_RUNTIME_DATABASE_URL", workflow)
        self.assertNotIn("PRODUCTION_MIGRATION_DATABASE_URL", workflow)
        self.assertNotIn("pull_request:", workflow)


if __name__ == "__main__":
    unittest.main()
