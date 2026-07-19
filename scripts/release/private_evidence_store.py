#!/usr/bin/env python3
"""Create-once access to one exact Vercel Private Blob evidence store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any, Callable
from urllib.parse import urlsplit


_STORE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,127}$")
_OBJECT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}$")


def validated_store_id(value: str) -> str:
    clean = str(value or "").strip()
    if not _STORE_ID.fullmatch(clean):
        raise ValueError("Private evidence store ID is invalid")
    return clean


def validated_object_key(value: str) -> str:
    clean = str(value or "").strip()
    path = PurePosixPath(clean)
    if (
        not _OBJECT_KEY.fullmatch(clean)
        or clean.startswith("/")
        or "\\" in clean
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("Private evidence object key is invalid")
    return clean


def _value(payload: object, *names: str) -> Any:
    for name in names:
        if isinstance(payload, dict) and name in payload:
            return payload.get(name)
        if hasattr(payload, name):
            return getattr(payload, name)
    return None


@dataclass(frozen=True)
class StoredEvidence:
    state: str
    object_key: str
    size: int


class PrivateBlobEvidenceStore:
    def __init__(
        self,
        *,
        store_id: str,
        token: str,
        putter: Callable[..., object] | None = None,
        getter: Callable[..., object] | None = None,
    ) -> None:
        self.store_id = validated_store_id(store_id)
        self._token = str(token or "").strip()
        if len(self._token) < 32:
            raise ValueError("Private evidence write token is missing or too short")
        if putter is None or getter is None:
            from vercel.blob import get, put

            putter = put
            getter = get
        self._putter = putter
        self._getter = getter

    def _verify_put_result(self, result: object, object_key: str) -> None:
        pathname = str(_value(result, "pathname") or "")
        raw_url = str(_value(result, "url") or "")
        parsed = urlsplit(raw_url)
        expected_host = f"{self.store_id}.private.blob.vercel-storage.com"
        if (
            pathname != object_key
            or parsed.scheme != "https"
            or parsed.hostname != expected_host
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.lstrip("/") != object_key
        ):
            raise ValueError("Private Blob response did not prove the exact store and key")

    def read(self, object_key: str) -> bytes:
        key = validated_object_key(object_key)
        result = self._getter(
            key,
            access="private",
            token=self._token,
            timeout=30.0,
            use_cache=False,
        )
        status = int(_value(result, "status_code", "statusCode") or 0)
        content = bytes(_value(result, "content") or b"")
        if status != 200 or not content:
            raise FileNotFoundError("Private evidence object is unavailable")
        return content

    def put_create_once(
        self,
        object_key: str,
        payload: bytes,
        *,
        content_type: str = "application/json",
    ) -> StoredEvidence:
        key = validated_object_key(object_key)
        data = bytes(payload)
        if not data or len(data) > 10_000_000:
            raise ValueError("Private evidence payload size is invalid")
        state = "STORED"
        try:
            result = self._putter(
                key,
                data,
                access="private",
                content_type=content_type,
                add_random_suffix=False,
                overwrite=False,
                token=self._token,
            )
        except Exception as exc:
            try:
                existing = self.read(key)
            except Exception:
                raise RuntimeError("Private evidence create-once write failed") from exc
            if existing != data:
                raise RuntimeError("Private evidence object exists with different bytes") from exc
            state = "ALREADY_STORED"
        else:
            self._verify_put_result(result, key)
        if self.read(key) != data:
            raise RuntimeError("Private evidence read-back mismatch")
        return StoredEvidence(state=state, object_key=key, size=len(data))
