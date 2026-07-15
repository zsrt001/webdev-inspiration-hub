"""Private object storage operations.

Provider URLs and signed URLs are intentionally not part of this interface. The
database stores only the provider name and deterministic object key.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import os
from pathlib import Path
import re
from typing import BinaryIO, Protocol

from app.core.config import get_settings


settings = get_settings()

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_LOCAL_PRIVATE_ROOT = (_BACKEND_DIR / ".private-storage").resolve()
_OBJECT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}$")


class DeleteResult(str, Enum):
    DELETED = "DELETED"
    NOT_FOUND = "NOT_FOUND"
    FAILED = "FAILED"


class PrivateObjectStore(Protocol):
    def put_private(self, object_key: str, data: bytes, content_type: str) -> None:
        raise NotImplementedError

    def read_private(self, object_key: str) -> bytes:
        raise NotImplementedError

    def delete_private(self, object_key: str) -> DeleteResult:
        raise NotImplementedError

    def list_private(self, prefix: str, *, limit: int = 1000) -> tuple[str, ...]:
        raise NotImplementedError


def _validated_object_key(value: str) -> str:
    key = str(value or "")
    if (
        not _OBJECT_KEY_PATTERN.fullmatch(key)
        or key.startswith("/")
        or "\\" in key
        or any(segment in {"", ".", ".."} for segment in key.split("/"))
    ):
        raise ValueError("invalid private object key")
    return key


def _validated_object_prefix(value: str) -> str:
    raw = str(value or "")
    trailing = raw.endswith("/")
    base = _validated_object_key(raw[:-1] if trailing else raw)
    return f"{base}/" if trailing else base


class StorageService:
    """Provider adapter whose only authority is a private object key."""

    def __init__(self) -> None:
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3

            config: dict[str, str] = {
                "aws_access_key_id": settings.aws_access_key_id,
                "aws_secret_access_key": settings.aws_secret_access_key,
                "region_name": settings.aws_region,
            }
            if settings.aws_s3_endpoint:
                config["endpoint_url"] = settings.aws_s3_endpoint
            self._client = boto3.client("s3", **config)
        return self._client

    def put_private(self, object_key: str, data: bytes, content_type: str) -> None:
        key = _validated_object_key(object_key)
        payload = bytes(data)
        mime_type = str(content_type or "").strip().lower()
        if not payload or not mime_type:
            raise ValueError("private object data and content type are required")

        provider = settings.effective_storage_provider
        if provider == "local":
            self._put_local(key, payload)
            return
        if provider == "vercel":
            self._put_vercel(key, payload, mime_type)
            return
        if settings.is_vercel_runtime and settings.aws_s3_endpoint_is_loopback:
            raise RuntimeError("production private storage endpoint is loopback")
        self._put_s3(key, payload, mime_type)

    def read_private(self, object_key: str) -> bytes:
        key = _validated_object_key(object_key)
        provider = settings.effective_storage_provider
        if provider == "local":
            return self._read_local(key)
        if provider == "vercel":
            return self._read_vercel(key)
        if settings.is_vercel_runtime and settings.aws_s3_endpoint_is_loopback:
            raise RuntimeError("production private storage endpoint is loopback")
        return self._read_s3(key)

    def delete_private(self, object_key: str) -> DeleteResult:
        try:
            key = _validated_object_key(object_key)
        except ValueError:
            return DeleteResult.FAILED
        provider = settings.effective_storage_provider
        if provider == "local":
            return self._delete_local(key)
        if provider == "vercel":
            return self._delete_vercel(key)
        if settings.is_vercel_runtime and settings.aws_s3_endpoint_is_loopback:
            return DeleteResult.FAILED
        return self._delete_s3(key)

    def list_private(self, prefix: str, *, limit: int = 1000) -> tuple[str, ...]:
        clean_prefix = _validated_object_prefix(prefix)
        bounded = max(1, min(1000, int(limit)))
        provider = settings.effective_storage_provider
        if provider == "local":
            return self._list_local(clean_prefix, limit=bounded)
        if provider == "vercel":
            return self._list_vercel(clean_prefix, limit=bounded)
        if settings.is_vercel_runtime and settings.aws_s3_endpoint_is_loopback:
            raise RuntimeError("production private storage endpoint is loopback")
        return self._list_s3(clean_prefix, limit=bounded)

    def _local_path(self, object_key: str) -> Path:
        if settings.is_vercel_runtime or not settings.debug:
            raise RuntimeError("local private storage is debug-only")
        target = (_LOCAL_PRIVATE_ROOT / object_key).resolve()
        if target != _LOCAL_PRIVATE_ROOT and _LOCAL_PRIVATE_ROOT not in target.parents:
            raise ValueError("private object path escaped its root")
        return target

    def _put_local(self, object_key: str, data: bytes) -> None:
        target = self._local_path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError("private object already exists")
        with target.open("xb") as handle:
            handle.write(data)

    def _read_local(self, object_key: str) -> bytes:
        target = self._local_path(object_key)
        if not target.is_file():
            raise FileNotFoundError("private object not found")
        return target.read_bytes()

    def _delete_local(self, object_key: str) -> DeleteResult:
        try:
            target = self._local_path(object_key)
            if not target.exists():
                return DeleteResult.NOT_FOUND
            if not target.is_file():
                return DeleteResult.FAILED
            target.unlink()
            return DeleteResult.DELETED
        except Exception:
            return DeleteResult.FAILED

    def _list_local(self, prefix: str, *, limit: int) -> tuple[str, ...]:
        base = self._local_path(prefix.rstrip("/"))
        if not base.exists():
            return ()
        if not base.is_dir():
            raise RuntimeError("private object prefix is not a directory")
        keys = [
            path.relative_to(_LOCAL_PRIVATE_ROOT).as_posix()
            for path in base.rglob("*")
            if path.is_file()
        ]
        keys.sort()
        if len(keys) > limit:
            raise RuntimeError("private object prefix exceeds reconciliation limit")
        return tuple(keys)

    def _put_s3(self, object_key: str, data: bytes, content_type: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.put_object(
                Bucket=settings.aws_s3_bucket,
                Key=object_key,
                Body=data,
                ContentType=content_type,
                IfNoneMatch="*",
            )
        except ClientError as exc:
            raise RuntimeError("private object upload failed") from exc

    def _read_s3(self, object_key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(
                Bucket=settings.aws_s3_bucket,
                Key=object_key,
            )
            return bytes(response["Body"].read())
        except ClientError as exc:
            raise FileNotFoundError("private object unavailable") from exc

    @staticmethod
    def _is_not_found_client_error(exc: Exception) -> bool:
        response = getattr(exc, "response", {})
        code = str((response.get("Error") or {}).get("Code") or "").lower()
        status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
        return status in {404, 410} or code in {"404", "nosuchkey", "notfound"}

    def _delete_s3(self, object_key: str) -> DeleteResult:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=settings.aws_s3_bucket, Key=object_key)
        except ClientError as exc:
            if self._is_not_found_client_error(exc):
                return DeleteResult.NOT_FOUND
            return DeleteResult.FAILED
        try:
            self.client.delete_object(Bucket=settings.aws_s3_bucket, Key=object_key)
            return DeleteResult.DELETED
        except ClientError:
            return DeleteResult.FAILED

    def _list_s3(self, prefix: str, *, limit: int) -> tuple[str, ...]:
        keys: list[str] = []
        token = None
        while True:
            request = {
                "Bucket": settings.aws_s3_bucket,
                "Prefix": prefix,
                "MaxKeys": min(1000, limit + 1 - len(keys)),
            }
            if token:
                request["ContinuationToken"] = token
            response = self.client.list_objects_v2(**request)
            keys.extend(str(item.get("Key") or "") for item in response.get("Contents") or [])
            if len(keys) > limit:
                raise RuntimeError("private object prefix exceeds reconciliation limit")
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")
            if not token:
                raise RuntimeError("private object listing pagination invalid")
        return tuple(sorted(key for key in keys if key))

    @staticmethod
    def _blob_value(blob: object, *names: str):
        for name in names:
            if isinstance(blob, dict) and name in blob:
                return blob.get(name)
            if hasattr(blob, name):
                return getattr(blob, name)
        return None

    def _blob_token(self) -> str:
        token = settings.blob_token_effective
        if not token:
            raise RuntimeError("private Blob token is missing")
        return token

    def _put_vercel(self, object_key: str, data: bytes, content_type: str) -> None:
        try:
            from vercel.blob import put
        except ImportError as exc:
            raise RuntimeError("Vercel Blob SDK unavailable") from exc
        try:
            result = put(
                object_key,
                data,
                access="private",
                content_type=content_type,
                add_random_suffix=False,
                overwrite=False,
                token=self._blob_token(),
            )
        except Exception as exc:
            raise RuntimeError("private Blob upload failed") from exc
        pathname = str(self._blob_value(result, "pathname") or "")
        provider_url = str(self._blob_value(result, "url") or "")
        if pathname != object_key or ".private.blob.vercel-storage.com/" not in provider_url:
            raise RuntimeError("connected Blob store did not prove private object semantics")

    def _read_vercel(self, object_key: str) -> bytes:
        try:
            from vercel.blob import BlobNotFoundError, get
        except ImportError as exc:
            raise RuntimeError("Vercel Blob SDK unavailable") from exc
        try:
            result = get(
                object_key,
                access="private",
                token=self._blob_token(),
                timeout=30.0,
                use_cache=False,
            )
        except BlobNotFoundError as exc:
            raise FileNotFoundError("private object not found") from exc
        except Exception as exc:
            raise RuntimeError("private Blob read failed") from exc
        if int(self._blob_value(result, "status_code", "statusCode") or 0) != 200:
            raise FileNotFoundError("private object not found")
        return bytes(self._blob_value(result, "content") or b"")

    def _delete_vercel(self, object_key: str) -> DeleteResult:
        try:
            from vercel.blob import BlobNotFoundError, delete, head
        except ImportError:
            return DeleteResult.FAILED
        try:
            head(object_key, token=self._blob_token())
        except BlobNotFoundError:
            return DeleteResult.NOT_FOUND
        except Exception:
            return DeleteResult.FAILED
        try:
            delete(object_key, token=self._blob_token())
            return DeleteResult.DELETED
        except BlobNotFoundError:
            return DeleteResult.NOT_FOUND
        except Exception:
            return DeleteResult.FAILED

    def _list_vercel(self, prefix: str, *, limit: int) -> tuple[str, ...]:
        try:
            from vercel.blob import iter_objects
        except ImportError as exc:
            raise RuntimeError("Vercel Blob SDK unavailable") from exc
        try:
            values = tuple(
                str(item.pathname)
                for item in iter_objects(
                    prefix=prefix,
                    limit=limit + 1,
                    token=self._blob_token(),
                )
            )
        except Exception as exc:
            raise RuntimeError("private Blob listing failed") from exc
        if len(values) > limit:
            raise RuntimeError("private object prefix exceeds reconciliation limit")
        return tuple(sorted(values))

    def upload_file(
        self,
        file_content: BinaryIO,
        filename: str,
        content_type: str = "image/jpeg",
        folder: str = "uploads",
    ) -> str:
        """Retired compatibility method; callers must migrate to MediaAsset IDs."""

        _ = (file_content, filename, content_type, folder)
        raise RuntimeError("legacy URL upload is retired; use put_private with a MediaAsset")

    def delete_file(self, file_url: str) -> bool:
        """Retired compatibility method; arbitrary URL deletion is forbidden."""

        _ = file_url
        return False

    def cleanup_generated_files_older_than(
        self,
        *,
        cutoff: datetime,
        limit: int = 200,
    ) -> dict:
        """Compatibility report until Task 11 moves retention to MediaAsset rows."""

        clean_cutoff = cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)
        return {
            "provider": settings.effective_storage_provider,
            "prefix": "generated/",
            "checked": 0,
            "matched": 0,
            "deleted_files": 0,
            "failed_files": 0,
            "freed_bytes_estimate": 0,
            "freed_mb_estimate": 0.0,
            "skipped": True,
            "reason": "media_asset_retention_required",
            "cutoff": clean_cutoff.isoformat(),
            "limit": max(1, min(1000, int(limit or 200))),
        }


storage_service = StorageService()
