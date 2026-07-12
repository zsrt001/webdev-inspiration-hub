#!/usr/bin/env python3
"""Encrypt or decrypt a release build artifact with a streaming AES-GCM envelope."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from pathlib import Path
import sys

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b"VOWPIC-AESGCM-V1\n"
NONCE_BYTES = 12
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
MAX_AAD_BYTES = 1024


class BuildArtifactCryptoError(RuntimeError):
    """Raised when an artifact envelope cannot be produced or authenticated."""


def _decode_key(value: str) -> bytes:
    clean = str(value or "").strip()
    if not clean:
        raise BuildArtifactCryptoError("build-artifact encryption key is required")
    try:
        key = base64.b64decode(clean, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise BuildArtifactCryptoError(
            "build-artifact encryption key must be strict base64"
        ) from exc
    if len(key) != 32:
        raise BuildArtifactCryptoError(
            "build-artifact encryption key must decode to exactly 32 bytes"
        )
    return key


def _associated_data(value: str) -> bytes:
    try:
        encoded = str(value or "").encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BuildArtifactCryptoError("associated data must be valid UTF-8") from exc
    if not encoded or len(encoded) > MAX_AAD_BYTES or b"\x00" in encoded:
        raise BuildArtifactCryptoError(
            "associated data must be 1-1024 UTF-8 bytes without NUL"
        )
    return encoded


def _paths(source: Path, output: Path) -> tuple[Path, Path]:
    source_path = Path(source)
    output_path = Path(output)
    if not source_path.is_file():
        raise BuildArtifactCryptoError("input must be an existing regular file")
    try:
        if source_path.resolve(strict=True) == output_path.resolve(strict=False):
            raise BuildArtifactCryptoError("input and output must be different files")
    except OSError as exc:
        raise BuildArtifactCryptoError("unable to resolve artifact paths") from exc
    if not output_path.parent.is_dir():
        raise BuildArtifactCryptoError("output parent directory must already exist")
    return source_path, output_path


def _remove_created_output(output: Path, *, created: bool) -> None:
    if created:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass


def encrypt_file(
    source: Path,
    output: Path,
    *,
    key_b64: str,
    associated_data: str,
) -> None:
    """Create a non-overwriting AES-256-GCM envelope from *source*."""
    source_path, output_path = _paths(source, output)
    key = _decode_key(key_b64)
    aad = _associated_data(associated_data)
    nonce = os.urandom(NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(aad)
    created = False
    try:
        with source_path.open("rb") as source_handle, output_path.open("xb") as output_handle:
            created = True
            output_handle.write(MAGIC)
            output_handle.write(nonce)
            while chunk := source_handle.read(CHUNK_BYTES):
                output_handle.write(encryptor.update(chunk))
            output_handle.write(encryptor.finalize())
            output_handle.write(encryptor.tag)
            output_handle.flush()
            os.fsync(output_handle.fileno())
    except FileExistsError as exc:
        raise BuildArtifactCryptoError("output already exists; refusing to overwrite") from exc
    except (BuildArtifactCryptoError, OSError, ValueError) as exc:
        _remove_created_output(output_path, created=created)
        if isinstance(exc, BuildArtifactCryptoError):
            raise
        raise BuildArtifactCryptoError("build-artifact encryption failed") from exc


def decrypt_file(
    source: Path,
    output: Path,
    *,
    key_b64: str,
    associated_data: str,
) -> None:
    """Authenticate and decrypt one envelope without retaining partial plaintext."""
    source_path, output_path = _paths(source, output)
    key = _decode_key(key_b64)
    aad = _associated_data(associated_data)
    minimum_size = len(MAGIC) + NONCE_BYTES + TAG_BYTES
    try:
        envelope_size = source_path.stat().st_size
    except OSError as exc:
        raise BuildArtifactCryptoError("unable to inspect encrypted artifact") from exc
    if envelope_size < minimum_size:
        raise BuildArtifactCryptoError("encrypted artifact is truncated")

    ciphertext_bytes = envelope_size - len(MAGIC) - NONCE_BYTES - TAG_BYTES
    created = False
    try:
        with source_path.open("rb") as source_handle:
            if source_handle.read(len(MAGIC)) != MAGIC:
                raise BuildArtifactCryptoError("encrypted artifact has an unknown format")
            nonce = source_handle.read(NONCE_BYTES)
            source_handle.seek(-TAG_BYTES, os.SEEK_END)
            tag = source_handle.read(TAG_BYTES)
            source_handle.seek(len(MAGIC) + NONCE_BYTES)
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(aad)
            remaining = ciphertext_bytes
            with output_path.open("xb") as output_handle:
                created = True
                while remaining:
                    chunk = source_handle.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise BuildArtifactCryptoError("encrypted artifact is truncated")
                    remaining -= len(chunk)
                    output_handle.write(decryptor.update(chunk))
                output_handle.write(decryptor.finalize())
                output_handle.flush()
                os.fsync(output_handle.fileno())
    except FileExistsError as exc:
        raise BuildArtifactCryptoError("output already exists; refusing to overwrite") from exc
    except InvalidTag as exc:
        _remove_created_output(output_path, created=created)
        raise BuildArtifactCryptoError(
            "encrypted artifact authentication failed"
        ) from exc
    except (BuildArtifactCryptoError, OSError, ValueError) as exc:
        _remove_created_output(output_path, created=created)
        if isinstance(exc, BuildArtifactCryptoError):
            raise
        raise BuildArtifactCryptoError("build-artifact decryption failed") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("encrypt", "decrypt"))
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--associated-data", required=True)
    args = parser.parse_args()
    try:
        key_b64 = os.environ.get(args.key_env, "")
        operation = encrypt_file if args.action == "encrypt" else decrypt_file
        operation(
            Path(args.input),
            Path(args.output),
            key_b64=key_b64,
            associated_data=args.associated_data,
        )
        print(json.dumps({"action": args.action, "output": args.output}, sort_keys=True))
        return 0
    except (BuildArtifactCryptoError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
