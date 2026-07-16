"""Real private-store gate for the complete rendered delivery artifact set."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import os
import secrets
import unittest
import uuid

from PIL import Image

from app.core.config import get_settings
from app.services.postprocess_service import (
    PAID_VARIANT_RATIOS,
    ValidatedPrivateImage,
    render_private_delivery_set,
)
from app.services.storage import DeleteResult, StorageService
from app.services.trial_access_service import build_trial_watermark_bytes


@unittest.skipUnless(
    os.environ.get("RUN_PRIVATE_STORAGE_INTEGRATION") == "1",
    "NOT_RUN: set RUN_PRIVATE_STORAGE_INTEGRATION=1 with private-store credentials",
)
class PrivateDeliveryIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        settings = get_settings()
        provider = settings.effective_storage_provider
        if provider == "local":
            self.fail("NOT_RUN: private delivery integration forbids the local debug provider")
        if provider == "vercel" and not settings.blob_token_effective:
            self.fail("NOT_RUN: BLOB_READ_WRITE_TOKEN for a Private Blob store is missing")
        if provider == "s3" and not (
            settings.aws_access_key_id
            and settings.aws_secret_access_key
            and settings.aws_s3_bucket
        ):
            self.fail("NOT_RUN: private S3 credentials or bucket are missing")

    @staticmethod
    def _candidate() -> ValidatedPrivateImage:
        image = Image.new("RGB", (900, 1200), (78, 105, 132))
        output = BytesIO()
        image.save(output, format="JPEG", quality=94)
        payload = output.getvalue()
        return ValidatedPrivateImage(
            asset_id=uuid.uuid4(),
            image_bytes=payload,
            mime_type="image/jpeg",
            sha256=sha256(payload).hexdigest(),
        )

    def test_complete_set_round_trips_lists_decodes_and_deletes(self) -> None:
        store = StorageService()
        prefix = f"integration/private-delivery/{secrets.token_hex(16)}/"
        rendered = render_private_delivery_set(self._candidate())
        master_candidate = ValidatedPrivateImage(
            asset_id=uuid.uuid4(),
            image_bytes=rendered.master.image_bytes,
            mime_type=rendered.master.mime_type,
            sha256=rendered.master.sha256,
        )
        preview = build_trial_watermark_bytes(master_candidate)
        artifacts = {
            "master.jpg": rendered.master,
            **{
                f"variant-{ratio.replace(':', 'x')}.jpg": artifact
                for ratio, artifact in rendered.variants.items()
            },
            "trial-preview.jpg": preview,
        }
        self.assertEqual(len(artifacts), 8)
        stored_keys: list[str] = []
        try:
            for name, artifact in artifacts.items():
                key = f"{prefix}{name}"
                store.put_private(key, artifact.image_bytes, artifact.mime_type)
                stored_keys.append(key)
                content = store.read_private(key)
                self.assertEqual(sha256(content).hexdigest(), artifact.sha256)
                with Image.open(BytesIO(content)) as decoded:
                    decoded.verify()
                    self.assertEqual(decoded.size, (artifact.width, artifact.height))
            self.assertEqual(set(store.list_private(prefix, limit=16)), set(stored_keys))
            for ratio, artifact in rendered.variants.items():
                width, height = PAID_VARIANT_RATIOS[ratio]
                self.assertEqual(artifact.width * height, artifact.height * width)
        finally:
            for key in stored_keys:
                self.assertIn(
                    store.delete_private(key),
                    {DeleteResult.DELETED, DeleteResult.NOT_FOUND},
                )


if __name__ == "__main__":
    unittest.main()
