"""Encrypted build-artifact envelope contracts for the public repository."""

from __future__ import annotations

import base64
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "build_artifact_crypto.py"


def _module():
    if not SCRIPT.exists():
        raise AssertionError("build-artifact encryption helper is missing")
    spec = importlib.util.spec_from_file_location("build_artifact_crypto", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildArtifactCryptoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _module()
        self.key = os.urandom(32)
        self.key_b64 = base64.b64encode(self.key).decode("ascii")
        self.aad = "vowpic-safe-baseline-build:v1:" + "a" * 40 + ":123:1:tar"

    def test_streaming_envelope_round_trips_without_plaintext_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "safe-baseline-build.tar"
            encrypted = root / "safe-baseline-build.tar.enc"
            restored = root / "restored.tar"
            payload = (b"private-prebuilt-output\x00" * 100_000) + os.urandom(4096)
            source.write_bytes(payload)

            self.module.encrypt_file(
                source,
                encrypted,
                key_b64=self.key_b64,
                associated_data=self.aad,
            )
            ciphertext = encrypted.read_bytes()
            self.assertNotIn(b"private-prebuilt-output", ciphertext)
            self.assertTrue(ciphertext.startswith(self.module.MAGIC))

            self.module.decrypt_file(
                encrypted,
                restored,
                key_b64=self.key_b64,
                associated_data=self.aad,
            )
            self.assertEqual(restored.read_bytes(), payload)

    def test_wrong_key_or_associated_data_fails_closed_without_partial_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "manifest.sha256"
            encrypted = root / "manifest.sha256.enc"
            source.write_text("b" * 64 + "\n", encoding="ascii")
            self.module.encrypt_file(
                source,
                encrypted,
                key_b64=self.key_b64,
                associated_data=self.aad,
            )

            for suffix, key_b64, aad in (
                ("wrong-key", base64.b64encode(os.urandom(32)).decode("ascii"), self.aad),
                ("wrong-aad", self.key_b64, self.aad + ":tampered"),
            ):
                with self.subTest(suffix=suffix):
                    output = root / f"{suffix}.txt"
                    with self.assertRaises(self.module.BuildArtifactCryptoError):
                        self.module.decrypt_file(
                            encrypted,
                            output,
                            key_b64=key_b64,
                            associated_data=aad,
                        )
                    self.assertFalse(output.exists())

    def test_invalid_key_existing_output_and_truncated_envelope_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input"
            source.write_bytes(b"payload")
            existing = root / "existing.enc"
            existing.write_bytes(b"do-not-overwrite")
            with self.assertRaises(self.module.BuildArtifactCryptoError):
                self.module.encrypt_file(
                    source,
                    existing,
                    key_b64=self.key_b64,
                    associated_data=self.aad,
                )
            self.assertEqual(existing.read_bytes(), b"do-not-overwrite")

            with self.assertRaises(self.module.BuildArtifactCryptoError):
                self.module.encrypt_file(
                    source,
                    root / "bad-key.enc",
                    key_b64=base64.b64encode(b"short").decode("ascii"),
                    associated_data=self.aad,
                )

            truncated = root / "truncated.enc"
            truncated.write_bytes(self.module.MAGIC + b"short")
            output = root / "truncated.out"
            with self.assertRaises(self.module.BuildArtifactCryptoError):
                self.module.decrypt_file(
                    truncated,
                    output,
                    key_b64=self.key_b64,
                    associated_data=self.aad,
                )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
