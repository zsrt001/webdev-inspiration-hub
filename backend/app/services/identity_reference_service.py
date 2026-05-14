"""Identity reference pack creation for wedding photo generation.

The pack is the durable contract between upload preflight and generation:
each subject keeps the original portrait, a face-focused crop, an upper-body
crop, and explicit role labels. Generation providers can then use the pack as
identity anchors instead of guessing from the raw upload list.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import httpx

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - runtime dependency guard
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]

from app.services.storage import storage_service


IDENTITY_REFERENCE_PACK_VERSION = 1
IDENTITY_CROP_STRATEGY = "centered_portrait_v1"
IDENTITY_REFERENCE_FOLDER = "identity-references"


@dataclass(frozen=True, slots=True)
class _PreparedCrop:
    kind: str
    content: bytes
    content_type: str
    box: dict[str, Any]


def _identity_label(index: int) -> str:
    if index == 0:
        return "person_a"
    if index == 1:
        return "person_b"
    return f"person_{index + 1}"


def _role_for_subject(index: int, *, is_couple_request: bool) -> str:
    if not is_couple_request:
        return "subject"
    if index == 0:
        return "bride"
    if index == 1:
        return "groom"
    return "supporting_subject"


def _flow_kind(*, is_couple_request: bool, couple_flow: str | None) -> str:
    if not is_couple_request:
        return "single"
    normalized = str(couple_flow or "").strip().lower()
    if normalized == "remote":
        return "couple_remote"
    return "couple_local"


async def _fetch_image_bytes(image_url: str) -> tuple[bytes, str]:
    raw = str(image_url or "").strip()
    if not raw:
        raise ValueError("identity_reference_empty_image_url")

    if raw.startswith("data:image/"):
        header, encoded = raw.split(",", 1)
        content_type = header[5:].split(";", 1)[0] or "image/jpeg"
        return base64.b64decode(encoded), content_type

    if not raw.startswith(("http://", "https://")):
        raise ValueError("identity_reference_requires_remote_image")

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, trust_env=False) as client:
        response = await client.get(raw)
        response.raise_for_status()
        content_type = response.headers.get("content-type") or "image/jpeg"
        return response.content, content_type.split(";", 1)[0].strip() or "image/jpeg"


def _clamp_box(
    *,
    width: int,
    height: int,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> tuple[int, int, int, int]:
    x1 = max(0, min(width - 1, int(round(left))))
    y1 = max(0, min(height - 1, int(round(top))))
    x2 = max(x1 + 1, min(width, int(round(right))))
    y2 = max(y1 + 1, min(height, int(round(bottom))))
    return x1, y1, x2, y2


def _crop_box_for_kind(width: int, height: int, kind: str) -> tuple[int, int, int, int]:
    if height >= width:
        if kind == "face":
            return _clamp_box(
                width=width,
                height=height,
                left=width * 0.16,
                top=height * 0.02,
                right=width * 0.84,
                bottom=height * 0.54,
            )
        return _clamp_box(
            width=width,
            height=height,
            left=width * 0.05,
            top=height * 0.00,
            right=width * 0.95,
            bottom=height * 0.74,
        )

    if width >= height * 1.35:
        if kind == "face":
            return _clamp_box(
                width=width,
                height=height,
                left=width * 0.31,
                top=height * 0.02,
                right=width * 0.69,
                bottom=height * 0.74,
            )
        return _clamp_box(
            width=width,
            height=height,
            left=width * 0.20,
            top=height * 0.00,
            right=width * 0.80,
            bottom=height * 0.96,
        )

    if kind == "face":
        return _clamp_box(
            width=width,
            height=height,
            left=width * 0.22,
            top=height * 0.03,
            right=width * 0.78,
            bottom=height * 0.66,
        )
    return _clamp_box(
        width=width,
        height=height,
        left=width * 0.12,
        top=height * 0.00,
        right=width * 0.88,
        bottom=height * 0.88,
    )


def _encode_crop(image: Any, box: tuple[int, int, int, int], *, kind: str) -> _PreparedCrop:
    cropped = image.crop(box)
    largest_edge = max(cropped.size)
    max_edge = 1500 if kind == "upper_body" else 1400
    if largest_edge > max_edge:
        scale = max_edge / float(largest_edge)
        next_size = (
            max(1, round(cropped.size[0] * scale)),
            max(1, round(cropped.size[1] * scale)),
        )
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        cropped = cropped.resize(next_size, resampling)

    buffer = BytesIO()
    cropped.save(buffer, format="JPEG", quality=95, optimize=True, progressive=True)
    x1, y1, x2, y2 = box
    width, height = image.size
    return _PreparedCrop(
        kind=kind,
        content=buffer.getvalue(),
        content_type="image/jpeg",
        box={
            "strategy": IDENTITY_CROP_STRATEGY,
            "kind": kind,
            "pixels": {"left": x1, "top": y1, "right": x2, "bottom": y2},
            "normalized": {
                "left": round(x1 / max(1, width), 4),
                "top": round(y1 / max(1, height), 4),
                "right": round(x2 / max(1, width), 4),
                "bottom": round(y2 / max(1, height), 4),
            },
        },
    )


def _prepare_identity_crops(content: bytes) -> tuple[dict[str, Any], list[_PreparedCrop]]:
    if Image is None or ImageOps is None:
        raise RuntimeError("identity_reference_pillow_unavailable")

    with Image.open(BytesIO(content)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        width, height = image.size
        if width < 80 or height < 80:
            raise ValueError("identity_reference_image_too_small")

        crops = [
            _encode_crop(image, _crop_box_for_kind(width, height, "face"), kind="face"),
            _encode_crop(image, _crop_box_for_kind(width, height, "upper_body"), kind="upper_body"),
        ]
        metrics = {
            "width": width,
            "height": height,
            "aspect_ratio": round(width / max(1, height), 4),
        }
        return metrics, crops


async def _upload_crop(crop: _PreparedCrop, *, identity_label: str) -> str:
    filename = f"{identity_label}-{crop.kind}.jpg"
    return await asyncio.to_thread(
        storage_service.upload_file,
        file_content=BytesIO(crop.content),
        filename=filename,
        content_type=crop.content_type,
        folder=IDENTITY_REFERENCE_FOLDER,
    )


async def build_identity_reference_pack(
    user_images: list[str],
    *,
    is_couple_request: bool,
    couple_flow: str | None = None,
) -> dict[str, Any]:
    """Build and persist identity references for each uploaded subject image."""
    subjects: list[dict[str, Any]] = []
    source_images = [str(url).strip() for url in (user_images or []) if str(url).strip()]
    for index, image_url in enumerate(source_images):
        content, content_type = await _fetch_image_bytes(image_url)
        metrics, crops = _prepare_identity_crops(content)
        identity_label = _identity_label(index)
        role = _role_for_subject(index, is_couple_request=is_couple_request)

        crop_urls: dict[str, str] = {}
        crop_boxes: dict[str, dict[str, Any]] = {}
        for crop in crops:
            crop_urls[crop.kind] = await _upload_crop(crop, identity_label=identity_label)
            crop_boxes[crop.kind] = crop.box

        subjects.append(
            {
                "slot": index + 1,
                "identity_label": identity_label,
                "role": role,
                "original_url": image_url,
                "face_crop_url": crop_urls.get("face"),
                "upper_body_crop_url": crop_urls.get("upper_body"),
                "source_content_type": content_type,
                "source_metrics": metrics,
                "crop_boxes": crop_boxes,
            }
        )

    return {
        "version": IDENTITY_REFERENCE_PACK_VERSION,
        "kind": _flow_kind(is_couple_request=is_couple_request, couple_flow=couple_flow),
        "crop_strategy": IDENTITY_CROP_STRATEGY,
        "subject_count": len(subjects),
        "role_order": [subject["role"] for subject in subjects],
        "identity_order": [subject["identity_label"] for subject in subjects],
        "subjects": subjects,
    }
