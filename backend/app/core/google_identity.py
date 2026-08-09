"""Conservative normalization for Google identity email assertions."""

from __future__ import annotations


_ASCII_TRIM = " \t\r\n\f\v"


def normalize_google_email(value: object) -> str:
    """Return one canonical ASCII mailbox without provider-specific alias folding."""

    if not isinstance(value, str):
        raise ValueError("Google identity email must be text")
    clean = value.strip(_ASCII_TRIM)
    if not clean or len(clean) > 320 or not clean.isascii() or clean.count("@") != 1:
        raise ValueError("Google identity email is invalid")
    local, domain = clean.split("@", 1)
    if not local or not domain or len(local) > 64 or len(domain) > 255:
        raise ValueError("Google identity email is invalid")
    if local[0] == "." or local[-1] == "." or ".." in local:
        raise ValueError("Google identity email is invalid")
    if domain[0] in ".-" or domain[-1] in ".-" or ".." in domain:
        raise ValueError("Google identity email is invalid")
    allowed_local = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.!#$%&'*+-/=?^_`{|}~"
    )
    allowed_domain = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
    )
    if any(character not in allowed_local for character in local):
        raise ValueError("Google identity email is invalid")
    if any(character not in allowed_domain for character in domain):
        raise ValueError("Google identity email is invalid")
    return f"{local.lower()}@{domain.lower()}"
