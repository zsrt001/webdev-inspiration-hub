"""Unit contracts for private object-store provider adapters."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.services.storage import DeleteResult, StorageService


class PrivateStorageAdapterTest(unittest.TestCase):
    def test_vercel_delete_calls_provider_after_existing_object_is_confirmed(self) -> None:
        store = StorageService()

        with (
            patch.object(store, "_blob_token", return_value="test-token"),
            patch("vercel.blob.head") as head,
            patch("vercel.blob.delete") as delete,
        ):
            result = store._delete_vercel("users/test/private.jpg")

        self.assertEqual(result, DeleteResult.DELETED)
        head.assert_called_once_with("users/test/private.jpg", token="test-token")
        delete.assert_called_once_with("users/test/private.jpg", token="test-token")


if __name__ == "__main__":
    unittest.main()
