"""Canonical browser-user dependencies.

Browser authentication is Cookie-only. There is intentionally no Bearer, OpenID,
email, or caller-selected identity fallback in this module.
"""

from app.core.session_auth import get_optional_session_user, get_session_user

__all__ = ["get_optional_session_user", "get_session_user"]
