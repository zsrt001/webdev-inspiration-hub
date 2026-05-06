"""Storage Service for file uploads."""

import os
import uuid
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse, unquote

from app.core.config import get_settings

settings = get_settings()

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_STATIC_DIR = os.path.join(_BACKEND_DIR, "static")


class StorageService:
    """Service for uploading files to the configured storage backend."""

    def __init__(self):
        """Initialize S3 client."""
        self._client = None

    @property
    def client(self):
        """Lazy-load S3 client."""
        if self._client is None:
            import boto3

            config = {
                "aws_access_key_id": settings.aws_access_key_id,
                "aws_secret_access_key": settings.aws_secret_access_key,
                "region_name": settings.aws_region,
            }
            
            # Use custom endpoint for MinIO/LocalStack
            if settings.aws_s3_endpoint:
                config["endpoint_url"] = settings.aws_s3_endpoint
            
            self._client = boto3.client("s3", **config)
        
        return self._client

    def upload_file(
        self,
        file_content: BinaryIO,
        filename: str,
        content_type: str = "image/jpeg",
        folder: str = "uploads",
    ) -> str:
        """
        Upload file to storage provider.
        
        Args:
            file_content: File-like object to upload
            filename: Original filename
            content_type: MIME type of the file
            folder: Folder path in bucket
            
        Returns:
            Public URL of uploaded file
        """
        provider = settings.storage_provider.lower().strip()
        if provider == "local":
            return self._upload_local(file_content, filename, content_type, folder)
        if provider == "vercel":
            return self._upload_vercel(file_content, filename, content_type, folder)
        return self._upload_s3(file_content, filename, content_type, folder)

    def _upload_local(
        self,
        file_content: BinaryIO,
        filename: str,
        content_type: str,
        folder: str,
    ) -> str:
        """
        Local dev storage:
        - Writes under `backend/static/<folder>/...`
        - Returns a public URL served by FastAPI StaticFiles (`/static`)

        This is intended for local development. Production should use S3/MinIO or Vercel Blob.
        """
        _ = content_type  # served as static; content-type set by server

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
        if not ext.isalnum() or len(ext) > 10:
            ext = "jpg"
        unique_key = f"{folder}/{uuid.uuid4()}.{ext}"

        static_root = Path(_STATIC_DIR).resolve()
        target_path = (static_root / unique_key).resolve()
        if static_root not in target_path.parents and static_root != target_path:
            raise Exception("Invalid local storage path")
        target_path.parent.mkdir(parents=True, exist_ok=True)

        data = file_content.read()
        with open(target_path, "wb") as f:
            f.write(data)

        base = settings.effective_webhook_base_url.rstrip("/")
        rel = unique_key.replace("\\", "/")
        return f"{base}/static/{rel}"

    def _upload_s3(
        self,
        file_content: BinaryIO,
        filename: str,
        content_type: str,
        folder: str,
    ) -> str:
        from botocore.exceptions import ClientError

        # Generate unique key
        ext = filename.split(".")[-1] if "." in filename else "jpg"
        unique_key = f"{folder}/{uuid.uuid4()}.{ext}"

        try:
            self.client.upload_fileobj(
                file_content,
                settings.aws_s3_bucket,
                unique_key,
                ExtraArgs={
                    "ContentType": content_type,
                    "ACL": "public-read",
                },
            )

            # Return public URL
            return f"{settings.s3_public_url_base}/{unique_key}"

        except ClientError as e:
            raise Exception(f"Failed to upload file: {e}")

    def _upload_vercel(
        self,
        file_content: BinaryIO,
        filename: str,
        content_type: str,
        folder: str,
    ) -> str:
        token = settings.blob_token_effective
        if not token:
            raise Exception("Missing Vercel Blob token")

        ext = filename.split(".")[-1] if "." in filename else "jpg"
        unique_key = f"{folder}/{uuid.uuid4()}.{ext}"
        data = file_content.read()

        try:
            from vercel.blob import put as vercel_put

            result = vercel_put(
                unique_key,
                data,
                access="public",
                content_type=content_type,
                add_random_suffix=False,
                token=token,
            )
            url = getattr(result, "url", None)
            if not url and isinstance(result, dict):
                url = result.get("url")
            if isinstance(url, str) and url.strip():
                return url.strip()
        except ImportError as exc:
            raise Exception("Vercel Blob SDK unavailable") from exc

    def delete_file(self, file_url: str) -> bool:
        """
        Delete file from storage provider.
        
        Args:
            file_url: Public URL of the file
            
        Returns:
            True if deleted successfully
        """
        provider = settings.storage_provider.lower().strip()
        if provider == "local":
            return self._delete_local(file_url)
        if provider == "vercel":
            return self._delete_vercel(file_url)
        return self._delete_s3(file_url)

    def _delete_local(self, file_url: str) -> bool:
        try:
            parsed = urlparse(file_url)
            path = parsed.path or ""
            if not path.startswith("/static/"):
                return False
            rel = unquote(path[len("/static/") :])
            if not rel:
                return False

            static_root = Path(_STATIC_DIR).resolve()
            target_path = (static_root / rel).resolve()
            if static_root not in target_path.parents and static_root != target_path:
                return False

            if target_path.is_file():
                target_path.unlink()
                return True
            return False
        except Exception:
            return False

    def _delete_s3(self, file_url: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            key = file_url.replace(f"{settings.s3_public_url_base}/", "")
            self.client.delete_object(
                Bucket=settings.aws_s3_bucket,
                Key=key,
            )
            return True
        except ClientError:
            return False

    def _delete_vercel(self, file_url: str) -> bool:
        token = settings.blob_token_effective
        if not token:
            return False
        try:
            try:
                from vercel.blob import delete as vercel_delete

                vercel_delete(file_url, token=token)
                return True
            except ImportError:
                return False
        except Exception:
            return False


# Singleton instance
storage_service = StorageService()
