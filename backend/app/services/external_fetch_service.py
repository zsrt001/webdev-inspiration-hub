"""Admin-only HTTPS image fetch with DNS pinning and redirect revalidation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import ParseResult, urljoin, urlparse

from app.core.config import get_settings
from app.services.media_asset_service import ValidatedImageBytes, validate_and_reencode_image


settings = get_settings()
EXTERNAL_FETCH_MAX_REDIRECTS = 2
EXTERNAL_FETCH_CONNECT_TIMEOUT_SECONDS = 5
EXTERNAL_FETCH_TOTAL_TIMEOUT_SECONDS = 30
EXTERNAL_FETCH_MAX_BYTES = 10_485_760
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class ExternalFetchError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FetchHop:
    status: int
    headers: dict[str, str]
    body: bytes


def validate_external_https_url(value: str) -> ParseResult:
    raw = str(value or "").strip()
    if not raw or len(raw) > 2_048 or any(ord(character) < 32 for character in raw):
        raise ExternalFetchError("external_fetch_url_invalid")
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ExternalFetchError("external_fetch_url_invalid") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ExternalFetchError("external_fetch_url_forbidden")
    return parsed


def validate_resolved_ip(value: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        not value.is_global
        or value.is_multicast
        or value.is_unspecified
        or value.is_loopback
        or value.is_link_local
        or value.is_private
        or value.is_reserved
    ):
        raise ExternalFetchError("external_fetch_ip_forbidden")


async def _resolve_public_ips(host: str) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    try:
        records = await asyncio.wait_for(
            loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM),
            timeout=min(
                EXTERNAL_FETCH_CONNECT_TIMEOUT_SECONDS,
                int(settings.external_fetch_connect_timeout_seconds),
            ),
        )
    except (asyncio.TimeoutError, OSError, socket.gaierror) as exc:
        raise ExternalFetchError("external_fetch_dns_failed") from exc
    addresses: set[str] = set()
    for _family, _type, _protocol, _canonical_name, sockaddr in records:
        address = ipaddress.ip_address(sockaddr[0])
        validate_resolved_ip(address)
        addresses.add(address.compressed)
    if not addresses:
        raise ExternalFetchError("external_fetch_dns_failed")
    return tuple(sorted(addresses))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, pinned_ip: str, *, timeout: float) -> None:
        super().__init__(
            host=host,
            port=443,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._pinned_ip, 443),
            timeout=self.timeout,
            source_address=self.source_address,
        )
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


def enforce_response_size(headers: dict[str, str], body: bytes) -> None:
    maximum = min(EXTERNAL_FETCH_MAX_BYTES, int(settings.external_fetch_max_bytes))
    raw_length = str(headers.get("content-length") or "").strip()
    if raw_length:
        try:
            declared = int(raw_length)
        except ValueError as exc:
            raise ExternalFetchError("external_fetch_length_invalid") from exc
        if declared < 0 or declared > maximum:
            raise ExternalFetchError("external_fetch_too_large")
    if len(body) > maximum:
        raise ExternalFetchError("external_fetch_too_large")


def _fetch_pinned_sync(url: str, pinned_ip: str, timeout: float) -> FetchHop:
    parsed = validate_external_https_url(url)
    host = parsed.hostname or ""
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    connection = _PinnedHTTPSConnection(host, pinned_ip, timeout=timeout)
    try:
        connection.request(
            "GET",
            target,
            headers={
                "Host": host,
                "Accept": "image/jpeg,image/png,image/webp",
                "Accept-Encoding": "identity",
                "User-Agent": "VowPic-Admin-Image-Probe/1.0",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        headers = {str(name).lower(): str(value) for name, value in response.getheaders()}
        maximum = min(EXTERNAL_FETCH_MAX_BYTES, int(settings.external_fetch_max_bytes))
        raw_length = str(headers.get("content-length") or "").strip()
        if raw_length:
            try:
                declared = int(raw_length)
            except ValueError as exc:
                raise ExternalFetchError("external_fetch_length_invalid") from exc
            if declared < 0 or declared > maximum:
                raise ExternalFetchError("external_fetch_too_large")
        body = bytearray()
        while True:
            chunk = response.read(min(65_536, maximum + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > maximum:
                raise ExternalFetchError("external_fetch_too_large")
        return FetchHop(status=int(response.status), headers=headers, body=bytes(body))
    except ExternalFetchError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        raise ExternalFetchError("external_fetch_request_failed") from exc
    finally:
        connection.close()


async def _fetch_pinned_hop(url: str, ips: tuple[str, ...], timeout: float) -> FetchHop:
    last_error: ExternalFetchError | None = None
    for pinned_ip in ips:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_fetch_pinned_sync, url, pinned_ip, timeout),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            last_error = ExternalFetchError("external_fetch_timeout")
            last_error.__cause__ = exc
        except ExternalFetchError as exc:
            last_error = exc
    raise last_error or ExternalFetchError("external_fetch_request_failed")


async def fetch_admin_https(url: str) -> ValidatedImageBytes:
    """Fetch one Admin-provided URL without allowing DNS rebinding or redirect escape."""

    maximum_redirects = min(
        EXTERNAL_FETCH_MAX_REDIRECTS,
        int(settings.external_fetch_max_redirects),
    )
    total_timeout = min(
        EXTERNAL_FETCH_TOTAL_TIMEOUT_SECONDS,
        int(settings.external_fetch_total_timeout_seconds),
    )
    connect_timeout = min(
        EXTERNAL_FETCH_CONNECT_TIMEOUT_SECONDS,
        int(settings.external_fetch_connect_timeout_seconds),
    )
    deadline = time.monotonic() + total_timeout
    current_url = str(url or "")
    redirects = 0
    while True:
        parsed = validate_external_https_url(current_url)
        ips = await _resolve_public_ips(parsed.hostname or "")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ExternalFetchError("external_fetch_timeout")
        try:
            hop = await asyncio.wait_for(
                _fetch_pinned_hop(current_url, ips, min(float(connect_timeout), remaining)),
                timeout=remaining,
            )
        except asyncio.TimeoutError as exc:
            raise ExternalFetchError("external_fetch_timeout") from exc

        if hop.status in _REDIRECT_STATUSES:
            location = str(hop.headers.get("location") or "").strip()
            if not location or redirects >= maximum_redirects:
                raise ExternalFetchError("external_fetch_redirect_limit")
            current_url = urljoin(current_url, location)
            redirects += 1
            continue
        if hop.status != 200:
            raise ExternalFetchError("external_fetch_upstream_status")
        enforce_response_size(hop.headers, hop.body)
        return validate_and_reencode_image(
            hop.body,
            declared_content_type=hop.headers.get("content-type"),
        )
