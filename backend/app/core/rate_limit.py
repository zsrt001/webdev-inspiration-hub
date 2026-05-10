"""Simple in-process request rate limiting.

This is a first application-layer guard. In production it should be paired with
Vercel Firewall/WAF or an external shared limiter because serverless instances do
not share memory.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    path_prefixes: tuple[str, ...]
    limit: int
    window_seconds: int


class InMemoryRateLimiter:
    """Sliding-window limiter keyed by caller/path bucket."""

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = max(1, int(window_seconds))
        self._events: dict[str, Deque[float]] = defaultdict(deque)

    def is_limited(self, key: str, *, now: float | None = None) -> bool:
        current = float(now if now is not None else time.monotonic())
        cutoff = current - self.window_seconds
        events = self._events[key]
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= self.limit:
            return True
        events.append(current)
        return False

    def check_only(self, key: str) -> bool:
        """Check if key is rate limited without recording a new event."""
        current = time.monotonic()
        cutoff = current - self.window_seconds
        events = self._events[key]
        while events and events[0] <= cutoff:
            events.popleft()
        return len(events) >= self.limit

    def record(self, key: str) -> None:
        """Record an event without checking the limit."""
        self._events[key].append(time.monotonic())


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI/Starlette middleware for coarse per-IP rate limits."""

    def __init__(self, app) -> None:
        super().__init__(app)
        settings = get_settings()
        self.enabled = bool(settings.rate_limit_enabled)
        self.exempt_prefixes = tuple(settings.rate_limit_exempt_path_list)
        self.default_rule = RateLimitRule(
            name="default",
            path_prefixes=("/api/v1/",),
            limit=settings.rate_limit_default_requests,
            window_seconds=settings.rate_limit_default_window_seconds,
        )
        self.sensitive_rule = RateLimitRule(
            name="sensitive",
            path_prefixes=(
                "/api/v1/auth",
                "/api/v1/orders",
                "/api/v1/payments",
                "/api/v1/subscriptions",
                "/api/v1/session",
                "/api/v1/upload",
                "/api/v1/live_portrait",
            ),
            limit=settings.rate_limit_sensitive_requests,
            window_seconds=settings.rate_limit_sensitive_window_seconds,
        )
        self.limiters = {
            "default": InMemoryRateLimiter(
                limit=self.default_rule.limit,
                window_seconds=self.default_rule.window_seconds,
            ),
            "sensitive": InMemoryRateLimiter(
                limit=self.sensitive_rule.limit,
                window_seconds=self.sensitive_rule.window_seconds,
            ),
        }

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self.enabled or self._is_exempt(request.url.path):
            return await call_next(request)

        rule = self._match_rule(request.url.path)
        if not rule:
            return await call_next(request)

        client_ip = self._client_ip(request)
        bucket = self._bucket_for_path(rule, request.url.path)
        key = f"{client_ip}:{request.method}:{rule.name}:{bucket}"
        if self.limiters[rule.name].is_limited(key):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(rule.window_seconds)},
            )
        return await call_next(request)

    def _is_exempt(self, path: str) -> bool:
        return path.startswith(("/static", "/style-previews")) or any(
            path.startswith(prefix) for prefix in self.exempt_prefixes
        )

    def _match_rule(self, path: str) -> RateLimitRule | None:
        if any(path.startswith(prefix) for prefix in self.sensitive_rule.path_prefixes):
            return self.sensitive_rule
        if any(path.startswith(prefix) for prefix in self.default_rule.path_prefixes):
            return self.default_rule
        return None

    @staticmethod
    def _client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip() or "unknown"
        return request.client.host if request.client else "unknown"

    @staticmethod
    def _bucket_for_path(rule: RateLimitRule, path: str) -> str:
        for prefix in rule.path_prefixes:
            if path.startswith(prefix):
                return prefix
        return path
