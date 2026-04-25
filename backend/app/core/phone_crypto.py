"""Lead phone encryption helpers."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

settings = get_settings()
_fernet = Fernet(settings.phone_fernet_key.encode("utf-8"))
_PREFIX = "enc:v1:"


def encrypt_phone(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    token = _fernet.encrypt(text.encode("utf-8")).decode("utf-8")
    return f"{_PREFIX}{token}"


def decrypt_phone(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if not text.startswith(_PREFIX):
        return text
    token = text[len(_PREFIX):]
    try:
        return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""
