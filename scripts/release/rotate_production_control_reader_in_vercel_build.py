"""Rotate one fixed control-reader login inside an unaliased Vercel build.

This module is intentionally build-only.  Vercel supplies the existing
Production ``DATABASE_URL`` to the remote build without disclosing it to the
calling GitHub job.  The caller supplies the three protected application
credentials as build variables, and the shared repair contract verifies the
database target and least-privilege role before changing the password.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import re
import sys
from typing import Mapping, NamedTuple
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request

import psycopg2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from repair_production_control_reader_credential import (
    encrypt_secret,
    prove_control_reader_after_pooler_propagation,
    recover_control_reader_url,
    rotate_control_reader_password,
)
from private_evidence_store import validated_object_key, validated_store_id


SCHEMA = "vowpic.vercel-build-control-reader-repair.v1"
CREDENTIAL_ENVELOPE_SCHEMA = "vowpic.encrypted-control-reader-credential.v1"
DELIVERY_SCHEMA = "vowpic.control-reader-repair-delivery.v1"
ADMIN_DATABASE_URL_ENV = "DATABASE_URL"
RUNTIME_DATABASE_URL_ENV = "PRODUCTION_RUNTIME_DATABASE_URL"
CONTROL_WRITER_DATABASE_URL_ENV = "PRODUCTION_CONTROL_PLANE_DATABASE_URL"
CONTROL_READER_SECRET_ENV = "PRODUCTION_CONTROL_READ_DATABASE_URL"
RECIPIENT_PUBLIC_KEY_B64_ENV = "CONTROL_READER_RECIPIENT_PUBLIC_KEY_B64"
DELIVERY_OBJECT_KEY_ENV = "CONTROL_READER_DELIVERY_OBJECT_KEY"
DELIVERY_PUT_URL_ENV = "CONTROL_READER_DELIVERY_PUT_URL"
PRIVATE_BLOB_STORE_ID_ENV = "VOWPIC_PRIVATE_BLOB_STORE_ID"
BUILD_OUTPUT_DIRECTORY = Path(".vowpic-control-reader-repair-output")
MAX_DELIVERY_BYTES = 10_000
_DELIVERY_OBJECT_KEY = re.compile(
    r"^control-reader-repair/[1-9][0-9]*/delivery\.json$"
)
_PUT_QUERY_KEYS = {
    "pathname",
    "vercel-blob-add-random-suffix",
    "vercel-blob-allow-overwrite",
    "vercel-blob-allowed-content-types",
    "vercel-blob-delegation",
    "vercel-blob-maximum-size-in-bytes",
    "vercel-blob-signature",
    "vercel-blob-valid-until",
}


class DeliveryReceipt(NamedTuple):
    state: str
    size: int


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _open_without_redirects(request: Request, *, timeout: int):
    return build_opener(_RejectRedirects()).open(request, timeout=timeout)


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = str(environment.get(name) or "").strip()
    if not value:
        raise ValueError(f"required protected build variable is missing: {name}")
    return value


def recipient_public_key(environment: Mapping[str, str]) -> bytes:
    encoded = _required_environment(environment, RECIPIENT_PUBLIC_KEY_B64_ENV)
    try:
        public_key_pem = base64.b64decode(encoded, validate=True)
        public_key = serialization.load_pem_public_key(public_key_pem)
    except (binascii.Error, TypeError, ValueError) as exc:
        raise ValueError("recipient public key is invalid") from exc
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 3072:
        raise ValueError("recipient public key must be RSA with at least 3072 bits")
    return public_key_pem


def rotate_and_prove(
    environment: Mapping[str, str],
    public_key_pem: bytes,
) -> tuple[bytes, dict[str, object]]:
    admin_url = _required_environment(environment, ADMIN_DATABASE_URL_ENV)
    runtime_url = _required_environment(environment, RUNTIME_DATABASE_URL_ENV)
    writer_url = _required_environment(
        environment,
        CONTROL_WRITER_DATABASE_URL_ENV,
    )
    reader_secret = _required_environment(environment, CONTROL_READER_SECRET_ENV)
    reader_url = recover_control_reader_url(runtime_url, writer_url, reader_secret)
    encrypted_reader_url = encrypt_secret(reader_url, public_key_pem)
    password = unquote(urlsplit(reader_url).password or "")
    rotate_control_reader_password(
        admin_url,
        runtime_url,
        writer_url,
        password,
    )
    proof = prove_control_reader_after_pooler_propagation(
        runtime_url,
        writer_url,
        reader_url,
    )
    try:
        if proof.get("passed") is not True:
            raise ValueError("Production credential proof did not pass")
        credentials = {
            kind: dict(facts)
            for kind, facts in dict(proof["credentials"]).items()
        }
        if set(credentials) != {"runtime", "control_writer", "control_reader"}:
            raise ValueError("Production credential proof set is invalid")
        database = str(proof["database"])
        if not database:
            raise ValueError("Production credential proof database is missing")
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Production credential proof shape is invalid") from exc
    return encrypted_reader_url, {
        "schema": SCHEMA,
        "state": "PASSED",
        "credential_rotation": "unaliased_vercel_production_build",
        "database": database,
        "credentials": credentials,
    }


def _safe_failure_reason(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, OSError):
        return "private build output could not be written"
    if isinstance(exc, RuntimeError):
        return "private delivery storage failed"
    return "database operation failed"


def delivery_payload(
    encrypted_reader_url: bytes,
    proof: Mapping[str, object],
) -> bytes:
    if not encrypted_reader_url:
        raise ValueError("encrypted reader URL is empty")
    credential = {
        "algorithm": "RSA-OAEP-SHA256",
        "ciphertext_b64": base64.b64encode(encrypted_reader_url).decode(
            "ascii"
        ),
        "schema": CREDENTIAL_ENVELOPE_SCHEMA,
    }
    return json.dumps(
        {
            "credential": credential,
            "proof": proof,
            "schema": DELIVERY_SCHEMA,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def validate_delivery_payload(
    payload: bytes,
    public_key_pem: bytes,
) -> tuple[bytes, dict[str, object]]:
    try:
        delivery = json.loads(bytes(payload).decode("utf-8"))
        public_key = serialization.load_pem_public_key(public_key_pem)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("private repair delivery is invalid") from exc
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise ValueError("private repair recipient key is not RSA")
    if not isinstance(delivery, dict) or set(delivery) != {
        "credential",
        "proof",
        "schema",
    }:
        raise ValueError("private repair delivery envelope is invalid")
    if delivery.get("schema") != DELIVERY_SCHEMA:
        raise ValueError("private repair delivery envelope schema is invalid")
    credential = delivery.get("credential")
    if not isinstance(credential, dict) or set(credential) != {
        "algorithm",
        "ciphertext_b64",
        "schema",
    }:
        raise ValueError("private repair credential envelope is invalid")
    if credential.get("schema") != CREDENTIAL_ENVELOPE_SCHEMA:
        raise ValueError("private repair credential envelope schema is invalid")
    if credential.get("algorithm") != "RSA-OAEP-SHA256":
        raise ValueError("private repair credential envelope algorithm is invalid")
    try:
        encrypted = base64.b64decode(
            credential.get("ciphertext_b64", ""),
            validate=True,
        )
    except (binascii.Error, TypeError, ValueError) as exc:
        raise ValueError("private repair encrypted URL is invalid") from exc
    if len(encrypted) != (public_key.key_size + 7) // 8:
        raise ValueError("private repair encrypted URL has an invalid size")
    proof = delivery.get("proof")
    if not isinstance(proof, dict):
        raise ValueError("private repair build proof is invalid")
    if proof.get("schema") != SCHEMA:
        raise ValueError("private repair build proof schema is invalid")
    if proof.get("state") != "PASSED":
        raise ValueError("private repair build proof did not pass")
    if proof.get("credential_rotation") != "unaliased_vercel_production_build":
        raise ValueError("private repair build proof rotation is invalid")
    credentials = proof.get("credentials")
    if not isinstance(credentials, dict) or set(credentials) != {
        "runtime",
        "control_writer",
        "control_reader",
    }:
        raise ValueError("private repair build credential proof set is invalid")
    expected = {
        "runtime": (
            "vowpic_release_runtime_login",
            "vowpic_app_runtime",
            "off",
        ),
        "control_writer": (
            "vowpic_release_control_login",
            "vowpic_control_writer_login",
            "off",
        ),
        "control_reader": (
            "vowpic_release_control_read_login",
            "vowpic_inventory_login",
            "on",
        ),
    }
    for kind, facts in credentials.items():
        if not isinstance(facts, dict):
            raise ValueError("private repair build credential facts are invalid")
        actual = (
            str(facts.get("session_user") or ""),
            str(facts.get("current_user") or ""),
            str(facts.get("default_read_only") or "").lower(),
        )
        if actual != expected[kind]:
            raise ValueError("private repair build credential identity is invalid")
    return encrypted, dict(proof)


def _normalized_private_store_id(value: str) -> str:
    store_id = validated_store_id(value)
    if store_id.startswith("store_"):
        store_id = store_id.removeprefix("store_")
    store_id = store_id.lower()
    if re.fullmatch(r"[a-z0-9]{8,64}", store_id) is None:
        raise ValueError("private repair Blob store ID is invalid")
    return store_id


def delivery_target(
    environment: Mapping[str, str],
) -> tuple[str, str, str]:
    object_key = validated_object_key(
        _required_environment(environment, DELIVERY_OBJECT_KEY_ENV)
    )
    if _DELIVERY_OBJECT_KEY.fullmatch(object_key) is None:
        raise ValueError("private repair delivery object key is invalid")
    store_id = _normalized_private_store_id(
        _required_environment(environment, PRIVATE_BLOB_STORE_ID_ENV)
    )
    put_url = _required_environment(environment, DELIVERY_PUT_URL_ENV)
    try:
        parsed = urlsplit(put_url)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("private repair delivery PUT URL is invalid") from exc
    if (
        len(put_url) > 16_384
        or parsed.scheme != "https"
        or parsed.hostname != "vercel.com"
        or port is not None
        or parsed.username
        or parsed.password
        or parsed.path != "/api/blob/"
        or parsed.fragment
        or not set(query).issubset(_PUT_QUERY_KEYS)
        or any(len(values) != 1 for values in query.values())
        or query.get("pathname") != [object_key]
        or query.get("vercel-blob-allowed-content-types")
        != ["application/json"]
        or query.get("vercel-blob-maximum-size-in-bytes")
        != [str(MAX_DELIVERY_BYTES)]
        or query.get("vercel-blob-add-random-suffix") != ["false"]
        or query.get("vercel-blob-allow-overwrite") != ["false"]
        or not query.get("vercel-blob-delegation", [""])[0]
        or not query.get("vercel-blob-signature", [""])[0]
    ):
        raise ValueError("private repair delivery PUT URL is invalid")
    return object_key, put_url, store_id


def upload_delivery(
    object_key: str,
    payload: bytes,
    put_url: str,
    store_id: str,
) -> DeliveryReceipt:
    data = bytes(payload)
    if not data or len(data) > MAX_DELIVERY_BYTES:
        raise ValueError("private repair delivery payload size is invalid")
    request = Request(
        put_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with _open_without_redirects(request, timeout=30) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            response_payload = response.read(16_385)
    except OSError as exc:
        raise RuntimeError("private repair delivery PUT failed") from exc
    if status not in {200, 201} or not response_payload or len(response_payload) > 16_384:
        raise RuntimeError("private repair delivery PUT failed")
    try:
        result = json.loads(response_payload.decode("utf-8"))
        response_url = urlsplit(str(result.get("url") or ""))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("private repair delivery response is invalid") from exc
    if (
        result.get("pathname") != object_key
        or response_url.scheme != "https"
        or response_url.hostname != f"{store_id}.private.blob.vercel-storage.com"
        or response_url.username
        or response_url.password
        or response_url.query
        or response_url.fragment
        or response_url.path.lstrip("/") != object_key
    ):
        raise ValueError("private repair delivery response target is invalid")
    return DeliveryReceipt(state="STORED", size=len(data))


def write_build_output() -> None:
    BUILD_OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    BUILD_OUTPUT_DIRECTORY.joinpath("index.html").write_text(
        "<!doctype html><title>Private repair completed</title>\n",
        encoding="utf-8",
    )


def main() -> int:
    try:
        public_key_pem = recipient_public_key(os.environ)
        object_key, put_url, store_id = delivery_target(os.environ)
        encrypted_reader_url, result = rotate_and_prove(os.environ, public_key_pem)
        delivery = delivery_payload(encrypted_reader_url, result)
        validate_delivery_payload(delivery, public_key_pem)
        stored = upload_delivery(object_key, delivery, put_url, store_id)
        write_build_output()
    except (OSError, RuntimeError, ValueError, psycopg2.Error) as exc:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "state": "FAILED",
                    "reason": _safe_failure_reason(exc),
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                **result,
                "delivery": {
                    "size": stored.size,
                    "state": stored.state,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
