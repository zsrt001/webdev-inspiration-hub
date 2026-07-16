"""Real private-store deletion and idempotency gate."""

from __future__ import annotations

import os
import secrets
import unittest

from app.core.config import get_settings
from app.services.storage import DeleteResult, StorageService


@unittest.skipUnless(
    os.environ.get("RUN_PRIVATE_STORAGE_INTEGRATION") == "1",
    "NOT_RUN: set RUN_PRIVATE_STORAGE_INTEGRATION=1 with private-store credentials",
)
class PrivateStorageDeletionIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        settings = get_settings()
        provider = settings.effective_storage_provider
        if provider == "local":
            self.fail("NOT_RUN: private storage deletion forbids the local debug provider")
        if provider == "vercel" and not settings.blob_token_effective:
            self.fail("NOT_RUN: BLOB_READ_WRITE_TOKEN for a Private Blob store is missing")
        if provider == "s3" and not (
            settings.aws_access_key_id
            and settings.aws_secret_access_key
            and settings.aws_s3_bucket
        ):
            self.fail("NOT_RUN: private S3 credentials or bucket are missing")

    def test_delete_removes_bytes_and_second_delete_is_not_found(self) -> None:
        store = StorageService()
        object_key = f"integration/deletion/{secrets.token_hex(16)}.bin"
        payload = secrets.token_bytes(128)
        stored = False
        try:
            store.put_private(object_key, payload, "application/octet-stream")
            stored = True
            self.assertEqual(store.read_private(object_key), payload)
            self.assertEqual(store.delete_private(object_key), DeleteResult.DELETED)
            stored = False
            with self.assertRaises(FileNotFoundError):
                store.read_private(object_key)
            self.assertEqual(store.delete_private(object_key), DeleteResult.NOT_FOUND)
        finally:
            if stored:
                store.delete_private(object_key)


if __name__ == "__main__":
    unittest.main()
