"""Bounded private-object grants for Evolink generation inputs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest
import uuid

import httpx
from fastapi import FastAPI

from app.models.asset_access_grant import AssetAccessGrant
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.services import media_asset_service
from app.services.media_asset_service import AssetAccessError
from app.core.error_response import SensitivePathLogFilter, redact_sensitive_path
from app.core.config import Settings
from app.core.security_headers import (
    is_authenticated_provider_probe,
    is_exact_provider_grant_read,
    is_provider_grant_origin_request,
    web_security_middleware,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _asset() -> MediaAsset:
    return MediaAsset(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        role=MediaAssetRole.SOURCE,
        storage_provider="s3",
        object_key="private/source.jpg",
        sha256="a" * 64,
        mime_type="image/jpeg",
        byte_size=4,
        width=10,
        height=12,
        access_level="private",
        policy_version="source-v1",
        expires_at=NOW + timedelta(hours=1),
        status=MediaAssetStatus.ACTIVE,
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _GrantDb:
    def __init__(self, grant, asset):
        self.results = [_ScalarResult(grant), _ScalarResult(asset)]
        self.commit = AsyncMock()

    async def execute(self, _statement):
        return self.results.pop(0)


class _Store:
    def __init__(self):
        self.reads: list[str] = []

    def read_private(self, object_key: str) -> bytes:
        self.reads.append(object_key)
        return b"data"


class ProviderAssetGrantTest(unittest.IsolatedAsyncioTestCase):
    def test_explicit_provider_origin_exposes_token_read_and_authenticated_probe_only(self) -> None:
        probe_secret = "provider-probe-secret-" + "x" * 32
        settings = Settings(
            _env_file=None,
            runtime_environment="preview",
            provider_grant_origin="https://provider-grant-preview.vercel.app",
            provider_grant_probe_secret=probe_secret,
            webhook_base_url="https://webhooks.example.com",
        )
        self.assertEqual(
            settings.effective_provider_grant_origin,
            "https://provider-grant-preview.vercel.app",
        )
        self.assertTrue(
            is_provider_grant_origin_request(
                host_header="provider-grant-preview.vercel.app",
                request_scheme="https",
                settings_obj=settings,
            )
        )
        self.assertFalse(
            is_provider_grant_origin_request(
                host_header="preview-site.vercel.app",
                request_scheme="https",
                settings_obj=settings,
            )
        )
        token_path = "/api/v1/media/grants/" + "a" * 43
        self.assertTrue(is_exact_provider_grant_read(method="GET", path=token_path))
        self.assertTrue(
            is_authenticated_provider_probe(
                method="GET",
                path="/api/v1/version",
                probe_secret=probe_secret,
                settings_obj=settings,
            )
        )
        for method, path, supplied_secret in (
            ("GET", "/api/v1/version", ""),
            ("GET", "/api/v1/version", "wrong-" + "x" * 32),
            ("POST", "/api/v1/version", probe_secret),
            ("GET", "/health", probe_secret),
        ):
            with self.subTest(method=method, path=path, supplied_secret=supplied_secret):
                self.assertFalse(
                    is_authenticated_provider_probe(
                        method=method,
                        path=path,
                        probe_secret=supplied_secret,
                        settings_obj=settings,
                    )
                )
        for method, path in (
            ("POST", token_path),
            ("GET", "/api/v1/version"),
            ("GET", "/api/v1/media/grants/not-a-token"),
            ("GET", token_path + "/extra"),
        ):
            with self.subTest(method=method, path=path):
                self.assertFalse(is_exact_provider_grant_read(method=method, path=path))

    async def test_provider_version_probe_is_404_without_the_exact_app_secret(self) -> None:
        probe_secret = "provider-probe-secret-" + "x" * 32
        settings = Settings(
            _env_file=None,
            runtime_environment="preview",
            provider_grant_origin="https://provider-grant-preview.vercel.app",
            provider_grant_probe_secret=probe_secret,
            webhook_base_url="https://webhooks.example.com",
        )
        app = FastAPI()
        app.middleware("http")(web_security_middleware)

        @app.get("/api/v1/version")
        async def version():
            return {"deployment_id": "dpl_preview"}

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        with patch("app.core.security_headers.get_settings", return_value=settings):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="https://provider-grant-preview.vercel.app",
            ) as client:
                missing = await client.get("/api/v1/version")
                wrong = await client.get(
                    "/api/v1/version",
                    headers={"x-vowpic-provider-probe": "wrong-" + "x" * 32},
                )
                allowed = await client.get(
                    "/api/v1/version",
                    headers={"x-vowpic-provider-probe": probe_secret},
                )

        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(wrong.status_code, 404, wrong.text)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json(), {"deployment_id": "dpl_preview"})

    def test_alternate_host_or_non_https_origin_is_rejected_in_protected_runtime(self) -> None:
        with (
            patch.object(media_asset_service.settings, "runtime_environment", "production"),
            patch.object(
                media_asset_service.settings,
                "webhook_base_url",
                "https://api.vowpic.example",
            ),
        ):
            media_asset_service.validate_provider_grant_origin(
                host_header="api.vowpic.example",
                request_scheme="https",
            )
            for host, scheme in (
                ("evil.example", "https"),
                ("api.vowpic.example", "http"),
                ("api.vowpic.example,evil.example", "https"),
            ):
                with self.subTest(host=host, scheme=scheme), self.assertRaises(AssetAccessError):
                    media_asset_service.validate_provider_grant_origin(
                        host_header=host,
                        request_scheme=scheme,
                    )

    def test_access_log_filter_never_emits_raw_grant_token(self) -> None:
        import logging

        token = "s" * 43
        path = f"/api/v1/media/grants/{token}?ignored=1"
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1", "GET", path, "1.1", 200),
            None,
        )

        self.assertTrue(SensitivePathLogFilter().filter(record))
        self.assertNotIn(token, record.getMessage())
        self.assertEqual(
            redact_sensitive_path(path),
            "/api/v1/media/grants/[REDACTED]?ignored=1",
        )

    async def test_generation_grant_requires_exact_provider_purpose_and_lineage(self) -> None:
        asset = _asset()
        job_id = uuid.uuid4()
        attempt_id = uuid.uuid4()
        db = SimpleNamespace(add=lambda _row: None, commit=AsyncMock(), flush=AsyncMock())

        issued = await media_asset_service.create_provider_grant(
            db,
            asset=asset,
            provider="evolink",
            purpose="generation-input",
            job_id=job_id,
            attempt_id=attempt_id,
            now=NOW,
        )

        self.assertEqual(issued.grant.provider, "evolink")
        self.assertEqual(issued.grant.purpose, "generation-input")
        self.assertEqual(issued.grant.job_id, job_id)
        self.assertEqual(issued.grant.attempt_id, attempt_id)
        self.assertEqual(issued.grant.max_reads, 3)
        self.assertEqual(issued.grant.expires_at, NOW + timedelta(seconds=600))
        for provider, purpose, bound_job, bound_attempt in (
            ("wenwen", "generation-input", job_id, attempt_id),
            ("evolink", "qa-input", job_id, attempt_id),
            ("evolink", "generation-input", None, attempt_id),
            ("evolink", "generation-input", job_id, None),
        ):
            with self.subTest(provider=provider, purpose=purpose), self.assertRaises(ValueError):
                await media_asset_service.create_provider_grant(
                    db,
                    asset=asset,
                    provider=provider,
                    purpose=purpose,
                    job_id=bound_job,
                    attempt_id=bound_attempt,
                    now=NOW,
                )

    async def test_fourth_read_revocation_and_tampered_binding_fail_before_storage(self) -> None:
        asset = _asset()
        token = "t" * 43
        token_hash = __import__("hashlib").sha256(token.encode("utf-8")).hexdigest()
        cases = (
            {"used_count": 3},
            {"revoked_at": NOW},
            {"provider": "wenwen"},
            {"purpose": "qa-input"},
            {"job_id": None},
            {"attempt_id": None},
        )
        for override in cases:
            values = dict(
                id=uuid.uuid4(),
                asset_id=asset.id,
                token_hash=token_hash,
                provider="evolink",
                purpose="generation-input",
                job_id=uuid.uuid4(),
                attempt_id=uuid.uuid4(),
                runtime_bundle_id="development",
                target_api_deployment_id="development",
                serving_deployment_role="DEVELOPMENT",
                expires_at=NOW + timedelta(minutes=10),
                max_reads=3,
                used_count=0,
            )
            values.update(override)
            grant = AssetAccessGrant(**values)
            store = _Store()
            with (
                patch.object(media_asset_service.settings, "runtime_environment", "development"),
                self.assertRaises(AssetAccessError),
            ):
                await media_asset_service.stream_provider_grant(
                    _GrantDb(grant, asset),
                    token=token,
                    object_store=store,
                    now=NOW,
                )
            self.assertEqual(store.reads, [])


if __name__ == "__main__":
    unittest.main()
