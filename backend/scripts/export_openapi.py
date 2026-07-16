"""Write the canonical public OpenAPI snapshot used by the Web client."""

from __future__ import annotations

import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
OUTPUT_PATH = REPOSITORY_ROOT / "openapi" / "openapi.json"
sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app


VOLATILE_KEYS = frozenset({"x-generated-at"})


def canonicalize(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: canonicalize(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [canonicalize(item) for item in value]
    return value


def render_openapi() -> bytes:
    payload = json.dumps(
        canonicalize(app.openapi()),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return f"{payload}\n".encode("utf-8")


def export_openapi(output: Path = OUTPUT_PATH) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(render_openapi())
    return output


if __name__ == "__main__":
    export_openapi()
