"""Shared constants, settings, and rate limiters used across auth sub-modules."""

import re

from app.core.config import get_settings
from app.core.rate_limit import InMemoryRateLimiter

settings = get_settings()

ALGORITHM = "HS256"
DEFAULT_OAUTH_RETURN_PATH = "/pages/account/index"

NEW_ACCOUNT_IP_LIMITER = InMemoryRateLimiter(limit=settings.new_account_ip_limit_per_hour, window_seconds=3600)
NEW_ACCOUNT_DEVICE_LIMITER = InMemoryRateLimiter(limit=settings.new_account_device_limit_per_hour, window_seconds=3600)
