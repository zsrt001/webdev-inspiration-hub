"""Real private object-store integration gate.

Set RUN_PRIVATE_STORAGE_INTEGRATION=1 and configure one non-local provider. When
the gate is requested, missing credentials are a hard NOT_RUN failure rather
than a synthetic pass.
"""

from __future__ import annotations

import os
import secrets
import unittest
from urllib.parse import quote

import httpx

from app.core.config import get_settings
from app.services.storage import DeleteResult, StorageService


@unittest.skipUnless(
    os.environ.get("RUN_PRIVATE_STORAGE_INTEGRATION") == "1",
    "NOT_RUN: set RUN_PRIVATE_STORAGE_INTEGRATION=1 with private-store credentials",
)
class PrivateStorageIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = get_settings()
        self.provider = self.settings.effective_storage_provider
        if self.provider == "local":
            self.fail("NOT_RUN: private storage integration forbids the local debug provider")
        if self.provider == "vercel" and not self.settings.blob_token_effective:
            self.fail("NOT_RUN: BLOB_READ_WRITE_TOKEN for a Private Blob store is missing")
        if self.provider == "s3" and not (
            self.settings.aws_access_key_id
            and self.settings.aws_secret_access_key
            and self.settings.aws_s3_bucket
        ):
            self.fail("NOT_RUN: private S3 credentials or bucket are missing")

    def _unauthenticated_url(self, store: StorageService, object_key: str) -> str:
        if self.provider == "vercel":
            from vercel.blob import head

            metadata = head(object_key, token=self.settings.blob_token_effective)
            url = str(store._blob_value(metadata, "url") or "")
            self.assertIn(".private.blob.vercel-storage.com/", url)
            return url

        escaped = quote(object_key, safe="/")
        endpoint = self.settings.aws_s3_endpoint.rstrip("/")
        if endpoint:
            return f"{endpoint}/{self.settings.aws_s3_bucket}/{escaped}"
        return (
            f"https://{self.settings.aws_s3_bucket}.s3."
            f"{self.settings.aws_region}.amazonaws.com/{escaped}"
        )

    def test_private_put_read_direct_denial_and_delete(self) -> None:
        store = StorageService()
        object_key = f"integration/private/{secrets.token_hex(16)}.txt"
        payload = secrets.token_bytes(128)
        stored = False
        try:
            store.put_private(object_key, payload, "application/octet-stream")
            stored = True
            self.assertEqual(store.read_private(object_key), payload)

            with httpx.Client(trust_env=False, follow_redirects=False, timeout=15.0) as client:
                response = client.get(self._unauthenticated_url(store, object_key))
            self.assertNotEqual(
                response.status_code,
                200,
                "private object was readable without authenticated provider access",
            )
            self.assertIn(response.status_code, {401, 403, 404})
        finally:
            if stored:
                self.assertIn(
                    store.delete_private(object_key),
                    {DeleteResult.DELETED, DeleteResult.NOT_FOUND},
                )


if __name__ == "__main__":
    unittest.main()
