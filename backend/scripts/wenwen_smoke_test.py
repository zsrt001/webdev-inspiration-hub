"""Minimal Wenwen connectivity and capability check.

Usage:
    python backend/scripts/wenwen_smoke_test.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings


def _masked(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}***{value[-4:]}"


def _sample_data_url() -> str:
    image_path = ROOT.parent / "frontend" / "src" / "static" / "solo_chn_xiuhe.jpg"
    raw = image_path.read_bytes()
    mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _native_image_url() -> str:
    base = settings.wenwen_api_base_url.rstrip("/")
    parsed = urlparse(base)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else base
    template = settings.wenwen_native_image_generate_path_template or "/v1beta/models/{model}:generateContent"
    path = template.replace("{model}", settings.wenwen_image_model)
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{origin}{path}"


def _masked_header(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def main() -> int:
    base = settings.wenwen_api_base_url.rstrip("/")
    image_headers = _masked_header(settings.wenwen_api_key)
    text_headers = _masked_header(settings.wenwen_text_api_key_effective)
    vision_headers = _masked_header(settings.wenwen_vision_api_key_effective)
    print("[wenwen] base_url =", base)
    print("[wenwen] image_api_key =", _masked(settings.wenwen_api_key))
    print("[wenwen] text_api_key =", _masked(settings.wenwen_text_api_key_effective))
    print("[wenwen] vision_api_key =", _masked(settings.wenwen_vision_api_key_effective))
    print("[wenwen] text_model =", settings.wenwen_text_model)
    print("[wenwen] vision_model =", settings.wenwen_vision_model)
    print("[wenwen] image_model =", settings.wenwen_image_model)

    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        models_resp = await client.get(f"{base}{settings.wenwen_models_path}", headers=image_headers)
        print("[wenwen] image_models_status =", models_resp.status_code)
        if models_resp.status_code != 200:
            print(models_resp.text[:800])
            return 2

        model_ids = [item.get("id") for item in models_resp.json().get("data", [])]
        print("[wenwen] image_models =", model_ids)

        if settings.wenwen_text_api_key_effective:
            text_models_resp = await client.get(f"{base}{settings.wenwen_models_path}", headers=text_headers)
            print("[wenwen] text_models_status =", text_models_resp.status_code)
            print("[wenwen] text_models_body =", text_models_resp.text[:400])
            chat_resp = await client.post(
                f"{base}{settings.wenwen_chat_path}",
                headers={**text_headers, "Content-Type": "application/json"},
                json={
                    "model": settings.wenwen_text_model,
                    "messages": [{"role": "user", "content": "Reply with exactly OK."}],
                    "max_tokens": 8,
                },
            )
            print("[wenwen] text_chat_status =", chat_resp.status_code)
            print("[wenwen] text_chat_body =", chat_resp.text[:400])

        if settings.wenwen_vision_api_key_effective:
            image_vision_resp = await client.post(
                f"{base}{settings.wenwen_chat_path}",
                headers={**vision_headers, "Content-Type": "application/json"},
                json={
                    "model": settings.wenwen_vision_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Reply with one word: person or no-person."},
                                {"type": "image_url", "image_url": {"url": _sample_data_url()}},
                            ],
                        }
                    ],
                    "max_tokens": 16,
                },
            )
            print("[wenwen] vision_status =", image_vision_resp.status_code)
            print("[wenwen] vision_body =", image_vision_resp.text[:400])

        image_resp = await client.post(
            _native_image_url(),
            headers={**image_headers, "Content-Type": "application/json"},
            json={
                "contents": [
                    {
                        "parts": [
                            {
                                "text": "Generate a studio-quality bridal portrait in soft natural light."
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {"aspectRatio": settings.wenwen_image_size_single},
                },
            },
        )
        print("[wenwen] image_status =", image_resp.status_code)
        print("[wenwen] image_body =", image_resp.text[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
