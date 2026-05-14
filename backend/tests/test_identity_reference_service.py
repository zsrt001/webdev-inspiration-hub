"""Identity reference pack tests."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import sys
import unittest

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import identity_reference_service as service  # noqa: E402


def _portrait_data_url(color: tuple[int, int, int]) -> str:
    image = Image.new("RGB", (800, 1200), color)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


class IdentityReferenceServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_couple_pack_records_roles_and_crop_urls(self) -> None:
        uploads: list[dict] = []
        original_upload = service.storage_service.upload_file

        def fake_upload_file(file_content, filename, content_type="image/jpeg", folder="uploads"):
            data = file_content.read()
            uploads.append(
                {
                    "filename": filename,
                    "content_type": content_type,
                    "folder": folder,
                    "data": data,
                }
            )
            return f"https://blob.example.com/{folder}/{filename}"

        service.storage_service.upload_file = fake_upload_file
        try:
            pack = await service.build_identity_reference_pack(
                [_portrait_data_url((220, 180, 170)), _portrait_data_url((120, 140, 180))],
                is_couple_request=True,
                couple_flow="remote",
            )
        finally:
            service.storage_service.upload_file = original_upload

        self.assertEqual(pack["version"], service.IDENTITY_REFERENCE_PACK_VERSION)
        self.assertEqual(pack["kind"], "couple_remote")
        self.assertEqual(pack["role_order"], ["bride", "groom"])
        self.assertEqual(pack["identity_order"], ["person_a", "person_b"])
        self.assertEqual(pack["subject_count"], 2)

        first, second = pack["subjects"]
        self.assertEqual(first["role"], "bride")
        self.assertEqual(second["role"], "groom")
        self.assertEqual(first["identity_label"], "person_a")
        self.assertTrue(first["face_crop_url"].endswith("/person_a-face.jpg"))
        self.assertTrue(first["upper_body_crop_url"].endswith("/person_a-upper_body.jpg"))
        self.assertEqual(first["source_metrics"]["width"], 800)
        self.assertEqual(first["source_metrics"]["height"], 1200)
        self.assertIn("face", first["crop_boxes"])
        self.assertIn("upper_body", first["crop_boxes"])

        self.assertEqual(len(uploads), 4)
        self.assertTrue(all(upload["folder"] == service.IDENTITY_REFERENCE_FOLDER for upload in uploads))
        self.assertTrue(all(upload["content_type"] == "image/jpeg" for upload in uploads))
        with Image.open(BytesIO(uploads[0]["data"])) as uploaded_crop:
            self.assertGreater(uploaded_crop.size[0], 0)
            self.assertGreater(uploaded_crop.size[1], 0)


if __name__ == "__main__":
    unittest.main()
