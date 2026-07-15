"""Admin-only external image fetch SSRF contract tests."""

from __future__ import annotations

import asyncio
import ipaddress
import unittest
from unittest.mock import AsyncMock, patch

from app.services import external_fetch_service as service
from app.services.external_fetch_service import ExternalFetchError, FetchHop, fetch_admin_https


class ExternalFetchSecurityTest(unittest.IsolatedAsyncioTestCase):
    def test_only_https_port_443_without_credentials_or_fragment_is_allowed(self) -> None:
        for value in (
            "http://example.com/a.jpg",
            "https://example.com:444/a.jpg",
            "https://user:password@example.com/a.jpg",
            "https://example.com/a.jpg#fragment",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ExternalFetchError):
                    service.validate_external_https_url(value)

        parsed = service.validate_external_https_url("https://example.com/a.jpg?size=2")
        self.assertEqual(parsed.hostname, "example.com")
        self.assertEqual(parsed.port, None)

    def test_private_link_local_metadata_multicast_and_reserved_ips_are_blocked(self) -> None:
        blocked = (
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "224.0.0.1",
            "192.0.2.10",
            "::1",
            "fe80::1",
        )
        for raw in blocked:
            with self.subTest(raw=raw):
                with self.assertRaises(ExternalFetchError):
                    service.validate_resolved_ip(ipaddress.ip_address(raw))

    async def test_every_redirect_is_revalidated_and_reresolved(self) -> None:
        resolved_hosts: list[str] = []

        async def fake_resolve(host: str) -> tuple[str, ...]:
            resolved_hosts.append(host)
            return ("93.184.216.34",)

        hops = [
            FetchHop(status=302, headers={"location": "https://cdn.example.net/final.jpg"}, body=b""),
            FetchHop(status=200, headers={"content-type": "image/jpeg"}, body=b"jpeg"),
        ]
        with patch.object(service, "_resolve_public_ips", side_effect=fake_resolve), patch.object(
            service,
            "_fetch_pinned_hop",
            AsyncMock(side_effect=hops),
        ), patch.object(service, "validate_and_reencode_image") as decode:
            decode.return_value = object()
            result = await fetch_admin_https("https://example.com/start.jpg")

        self.assertIs(result, decode.return_value)
        self.assertEqual(resolved_hosts, ["example.com", "cdn.example.net"])
        self.assertEqual(service.EXTERNAL_FETCH_MAX_REDIRECTS, 2)
        self.assertEqual(service.EXTERNAL_FETCH_CONNECT_TIMEOUT_SECONDS, 5)
        self.assertEqual(service.EXTERNAL_FETCH_TOTAL_TIMEOUT_SECONDS, 30)

    async def test_third_redirect_and_declared_or_streamed_oversize_fail(self) -> None:
        with patch.object(
            service,
            "_resolve_public_ips",
            AsyncMock(return_value=("93.184.216.34",)),
        ), patch.object(
            service,
            "_fetch_pinned_hop",
            AsyncMock(
                return_value=FetchHop(
                    status=302,
                    headers={"location": "https://example.com/again.jpg"},
                    body=b"",
                )
            ),
        ):
            with self.assertRaises(ExternalFetchError) as redirects:
                await fetch_admin_https("https://example.com/start.jpg")
        self.assertEqual(redirects.exception.code, "external_fetch_redirect_limit")

        with self.assertRaises(ExternalFetchError) as declared:
            service.enforce_response_size(
                {"content-length": str(service.EXTERNAL_FETCH_MAX_BYTES + 1)},
                b"",
            )
        self.assertEqual(declared.exception.code, "external_fetch_too_large")

        with self.assertRaises(ExternalFetchError) as streamed:
            service.enforce_response_size(
                {},
                b"x" * (service.EXTERNAL_FETCH_MAX_BYTES + 1),
            )
        self.assertEqual(streamed.exception.code, "external_fetch_too_large")


if __name__ == "__main__":
    unittest.main()
