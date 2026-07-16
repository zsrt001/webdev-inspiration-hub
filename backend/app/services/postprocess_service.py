"""Generated image post-processing and delivery variants."""

from __future__ import annotations

import io
from dataclasses import dataclass
from hashlib import sha256 as sha256_digest
from types import MappingProxyType
import uuid

from app.core.config import get_settings

settings = get_settings()


@dataclass(frozen=True, slots=True)
class ValidatedPrivateImage:
    """Validated image bytes tied to a private MediaAsset identity."""

    asset_id: uuid.UUID
    image_bytes: bytes
    mime_type: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, uuid.UUID):
            raise TypeError("private image asset_id must be a UUID")
        payload = bytes(self.image_bytes)
        if not payload or len(payload) > 50 * 1024 * 1024:
            raise ValueError("private image bytes are empty or exceed the safety limit")
        if self.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("unsupported private image mime type")
        checksum = sha256_digest(payload).hexdigest()
        if self.sha256 != checksum:
            raise ValueError("private image checksum mismatch")
        object.__setattr__(self, "image_bytes", payload)


class PrivatePostprocessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PostprocessedPrivateImage:
    parent_asset_id: uuid.UUID
    image_bytes: bytes
    mime_type: str
    sha256: str
    width: int
    height: int

    def __post_init__(self) -> None:
        payload = bytes(self.image_bytes)
        if not payload or self.mime_type != "image/jpeg":
            raise ValueError("invalid private postprocess payload")
        if sha256_digest(payload).hexdigest() != self.sha256:
            raise ValueError("private postprocess checksum mismatch")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("private postprocess dimensions are invalid")
        object.__setattr__(self, "image_bytes", payload)


@dataclass(frozen=True)
class DeliveryVariant:
    suffix: str
    aspect_ratio: float | None
    label: str


@dataclass(frozen=True)
class PostprocessProfile:
    name: str
    highlight_protection: float = 0.52
    face_sharpen: float = 1.0
    skin_tone_unify: float = 0.42
    shadow_denoise: float = 0.34
    grain: float = 0.028
    contrast: float = 1.035
    color: float = 1.04
    black_white: bool = False


VARIANT_MAP: dict[str, DeliveryVariant] = {
    "2x3": DeliveryVariant("portrait_2x3", 2 / 3, "2:3 portrait"),
    "3x2": DeliveryVariant("print_3x2", 3 / 2, "3:2 print"),
    "3x4": DeliveryVariant("xhs_3x4", 3 / 4, "3:4 social"),
    "4x5": DeliveryVariant("portrait_4x5", 4 / 5, "4:5 portrait"),
    "9x16": DeliveryVariant("wallpaper_9x16", 9 / 16, "9:16 wallpaper"),
    "1x1": DeliveryVariant("square_1x1", 1, "1:1 square"),
}

PAID_VARIANT_RATIOS: dict[str, tuple[int, int]] = {
    "2:3": (2, 3),
    "3:2": (3, 2),
    "3:4": (3, 4),
    "4:5": (4, 5),
    "9:16": (9, 16),
    "1:1": (1, 1),
}
_PAID_VARIANT_INTERNAL_KEYS = MappingProxyType(
    {
        "2:3": "2x3",
        "3:2": "3x2",
        "3:4": "3x4",
        "4:5": "4x5",
        "9:16": "9x16",
        "1:1": "1x1",
    }
)


@dataclass(frozen=True, slots=True)
class RenderedPrivateArtifact:
    name: str
    role: "MediaAssetRole"
    image_bytes: bytes
    mime_type: str
    sha256: str
    width: int
    height: int

    def __post_init__(self) -> None:
        from app.models.media_asset import MediaAssetRole

        payload = bytes(self.image_bytes)
        if (
            not self.name
            or self.role not in {MediaAssetRole.FINAL_MASTER, MediaAssetRole.DELIVERY_VARIANT}
            or self.mime_type != "image/jpeg"
            or not payload
            or sha256_digest(payload).hexdigest() != self.sha256
            or self.width <= 0
            or self.height <= 0
        ):
            raise ValueError("delivery_artifact_invalid")
        object.__setattr__(self, "image_bytes", payload)


@dataclass(frozen=True, slots=True)
class RenderedPrivateDeliverySet:
    master: RenderedPrivateArtifact
    variants: dict[str, RenderedPrivateArtifact]

    @property
    def is_complete(self) -> bool:
        return (
            self.master.role.value == "final_master"
            and set(self.variants) == set(PAID_VARIANT_RATIOS)
            and all(item.role.value == "delivery_variant" for item in self.variants.values())
        )


def resolve_postprocess_profile(template_id: str | None = None) -> PostprocessProfile:
    """Resolve deterministic template-level finishing parameters."""
    key = str(template_id or "").strip().lower()
    if "classic_bw" in key or key in {"classic", "bw", "black_white"}:
        return PostprocessProfile(
            name="classic_bw_contrast",
            highlight_protection=0.58,
            face_sharpen=1.08,
            skin_tone_unify=0.32,
            shadow_denoise=0.40,
            grain=0.035,
            contrast=1.13,
            color=0.0,
            black_white=True,
        )
    if "korean" in key or "minimal" in key:
        return PostprocessProfile(
            name="korean_clean_low_grain",
            highlight_protection=0.50,
            face_sharpen=0.92,
            skin_tone_unify=0.50,
            shadow_denoise=0.42,
            grain=0.012,
            contrast=1.015,
            color=1.025,
        )
    if "castle" in key or "royal" in key:
        return PostprocessProfile(
            name="royal_castle_highlight_guard",
            highlight_protection=0.78,
            face_sharpen=1.0,
            skin_tone_unify=0.42,
            shadow_denoise=0.36,
            grain=0.026,
            contrast=1.04,
            color=1.035,
        )
    if "xiuhe" in key or "chinese" in key:
        return PostprocessProfile(
            name="xiuhe_rich_fabric",
            highlight_protection=0.56,
            face_sharpen=1.02,
            skin_tone_unify=0.38,
            shadow_denoise=0.30,
            grain=0.022,
            contrast=1.055,
            color=1.065,
        )
    return PostprocessProfile(name="balanced_bridal_finish")


def postprocess_private_candidate(
    candidate: ValidatedPrivateImage,
    *,
    template_id: str | None = None,
) -> PostprocessedPrivateImage:
    """Enhance validated private bytes; failures never return input bytes."""

    from PIL import Image

    if not isinstance(candidate, ValidatedPrivateImage):
        raise TypeError("candidate must be validated private image bytes")
    try:
        with Image.open(io.BytesIO(candidate.image_bytes)) as source:
            source.load()
            enhanced = _enhance_master(
                source.convert("RGB"),
                profile=resolve_postprocess_profile(template_id),
            )
        output = io.BytesIO()
        quality = max(75, min(96, int(settings.postprocess_jpeg_quality)))
        enhanced.save(output, format="JPEG", quality=quality, optimize=True)
        payload = output.getvalue()
    except Exception as exc:
        raise PrivatePostprocessError("private_postprocess_failed") from exc
    if not payload or sha256_digest(payload).hexdigest() == candidate.sha256:
        raise PrivatePostprocessError("private_postprocess_failed")
    return PostprocessedPrivateImage(
        parent_asset_id=candidate.asset_id,
        image_bytes=payload,
        mime_type="image/jpeg",
        sha256=sha256_digest(payload).hexdigest(),
        width=enhanced.width,
        height=enhanced.height,
    )


def _enhance_master(image, *, profile: PostprocessProfile | None = None):
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    profile = profile or resolve_postprocess_profile(None)
    img = image.convert("RGB")
    img = _resize_long_edge(img, _target_master_long_edge(img))

    # Preserve venue/set detail; use only a light outer falloff for subject separation.
    img = ImageOps.autocontrast(_apply_subtle_background_falloff(img), cutoff=0.35)
    img = _protect_white_gown_highlights(img, strength=profile.highlight_protection)
    img = _apply_studio_tone_balance(img, profile=profile)
    img = _unify_mixed_color_temperature(img)
    img = _unify_skin_tone(img, strength=profile.skin_tone_unify)
    img = _reduce_oily_skin_highlights(img)
    img = _apply_face_micro_retouch(img, strength=profile.face_sharpen)
    img = _denoise_shadows(img, strength=profile.shadow_denoise)

    smooth = img.filter(ImageFilter.SMOOTH_MORE)
    img = Image.blend(img, smooth, 0.055)
    if profile.black_white:
        img = ImageOps.grayscale(img).convert("RGB")
    else:
        img = ImageEnhance.Color(img).enhance(profile.color)
    img = ImageEnhance.Contrast(img).enhance(profile.contrast)
    img = ImageEnhance.Brightness(img).enhance(1.01)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.1, percent=round(82 * profile.face_sharpen), threshold=3))
    return _inject_subtle_film_grain(img, amount=profile.grain)


def _protect_white_gown_highlights(image, *, strength: float = 0.52):
    from PIL import Image, ImageEnhance

    strength = max(0.0, min(1.0, float(strength)))
    ycbcr = image.convert("YCbCr")
    y, cb, cr = ycbcr.split()
    mask = Image.new("L", image.size, 0)
    src_y = y.load()
    src_cb = cb.load()
    src_cr = cr.load()
    dst = mask.load()
    width, height = image.size
    for py in range(height):
        for px in range(width):
            lum = src_y[px, py]
            blue = src_cb[px, py]
            red = src_cr[px, py]
            if lum >= 204 and 112 <= blue <= 146 and 112 <= red <= 150:
                dst[px, py] = min(225, round((lum - 194) * 4.6 * strength))
    protected = ImageEnhance.Brightness(image).enhance(1.0 - 0.10 * strength)
    protected = ImageEnhance.Contrast(protected).enhance(1.0 - 0.05 * strength)
    return Image.composite(protected, image, mask)


def _apply_studio_tone_balance(image, *, profile: PostprocessProfile | None = None):
    from PIL import Image, ImageEnhance

    profile = profile or resolve_postprocess_profile(None)
    # Lift the central subject area gently while compressing harsh highlights.
    lut: list[int] = []
    for value in range(256):
        if value < 72:
            mapped = value * (1.04 + 0.08 * profile.shadow_denoise) + 4
        elif value > 206:
            mapped = 206 + (value - 206) * (0.82 - 0.20 * profile.highlight_protection)
        else:
            mapped = value
        lut.append(max(0, min(255, round(mapped))))
    balanced = image.point(lut * 3)

    fill_mask = _subject_fill_mask(image.size)
    filled = ImageEnhance.Brightness(balanced).enhance(1.045)
    filled = ImageEnhance.Contrast(filled).enhance(0.985)
    return Image.composite(filled, balanced, fill_mask)


def _unify_mixed_color_temperature(image, *, strength: float = 0.32):
    from PIL import Image, ImageStat

    strength = max(0.0, min(0.6, float(strength)))
    img = image.convert("RGB")
    means = ImageStat.Stat(img).mean[:3]
    if len(means) != 3 or min(means) <= 1:
        return img
    target = sum(means) / 3.0
    channels = img.split()
    balanced = []
    for channel, mean in zip(channels, means):
        scale = 1.0 + ((target / max(1.0, mean)) - 1.0) * strength
        scale = max(0.82, min(1.18, scale))
        balanced.append(channel.point(lambda value, s=scale: max(0, min(255, round(value * s)))))
    return Image.merge("RGB", balanced)


def _unify_skin_tone(image, *, strength: float = 0.42):
    from PIL import Image, ImageEnhance, ImageFilter

    strength = max(0.0, min(1.0, float(strength)))
    ycbcr = image.convert("YCbCr")
    y, cb, cr = ycbcr.split()
    mask = Image.new("L", image.size, 0)
    src_y = y.load()
    src_cb = cb.load()
    src_cr = cr.load()
    dst = mask.load()
    width, height = image.size
    for py in range(height):
        for px in range(width):
            blue = src_cb[px, py]
            red = src_cr[px, py]
            lum = src_y[px, py]
            if 82 <= blue <= 138 and 128 <= red <= 184 and lum >= 72:
                dst[px, py] = round(130 * strength)
    mask = Image.composite(mask, Image.new("L", image.size, 0), _face_detail_mask(image.size, opacity=210))
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(3, int(min(image.size) * 0.012))))
    softened = image.filter(ImageFilter.SMOOTH)
    softened = ImageEnhance.Color(softened).enhance(1.0 - 0.08 * strength)
    return Image.composite(softened, image, mask)


def _reduce_oily_skin_highlights(image):
    from PIL import Image, ImageEnhance, ImageFilter

    ycbcr = image.convert("YCbCr")
    y, cb, cr = ycbcr.split()
    skin_mask = Image.new("L", image.size, 0)
    src_y = y.load()
    src_cb = cb.load()
    src_cr = cr.load()
    dst = skin_mask.load()
    width, height = image.size
    for py in range(height):
        for px in range(width):
            lum = src_y[px, py]
            blue = src_cb[px, py]
            red = src_cr[px, py]
            if 84 <= blue <= 136 and 128 <= red <= 184 and lum >= 154:
                dst[px, py] = int(min(205, max(0, round((lum - 140) * 2.4))))
    face_window = _face_detail_mask(image.size, opacity=180)
    skin_mask = Image.composite(skin_mask, Image.new("L", image.size, 0), face_window)
    skin_mask = skin_mask.filter(ImageFilter.GaussianBlur(radius=max(3, int(min(image.size) * 0.01))))
    softened = ImageEnhance.Brightness(image).enhance(0.955)
    softened = ImageEnhance.Contrast(softened).enhance(0.972)
    softened = ImageEnhance.Color(softened).enhance(0.985)
    return Image.composite(softened, image, skin_mask)


def _apply_face_micro_retouch(image, *, strength: float = 1.0):
    from PIL import Image, ImageEnhance, ImageFilter

    strength = max(0.25, min(1.6, float(strength)))
    face_mask = _face_detail_mask(image.size, opacity=round(120 * strength))
    detail = image.filter(ImageFilter.UnsharpMask(radius=0.75, percent=round(72 * strength), threshold=4))
    img = Image.composite(detail, image, face_mask)

    eye_mask = _eye_detail_mask(image.size)
    eye_detail = ImageEnhance.Contrast(detail).enhance(1.04)
    eye_detail = eye_detail.filter(ImageFilter.UnsharpMask(radius=0.55, percent=round(95 * strength), threshold=3))
    img = Image.composite(eye_detail, img, eye_mask)

    smile_mask = _smile_polish_mask(image.size)
    smile = ImageEnhance.Brightness(img).enhance(1.018)
    smile = ImageEnhance.Color(smile).enhance(0.985)
    return Image.composite(smile, img, smile_mask)


def _denoise_shadows(image, *, strength: float = 0.34):
    from PIL import Image, ImageFilter

    strength = max(0.0, min(1.0, float(strength)))
    ycbcr = image.convert("YCbCr")
    y = ycbcr.split()[0]
    mask = y.point(lambda value: max(0, min(180, round((96 - value) * 2.4 * strength))) if value < 112 else 0)
    denoised = image.filter(ImageFilter.MedianFilter(size=3)).filter(ImageFilter.SMOOTH)
    return Image.composite(denoised, image, mask)


def _apply_subtle_background_falloff(image):
    from PIL import Image, ImageFilter, ImageOps

    blur_radius = max(0.24, min(image.size) / 1800)
    softened_edges = Image.composite(
        image,
        image.filter(ImageFilter.GaussianBlur(radius=blur_radius)),
        _foreground_mask(image.size),
    )
    background_mask = ImageOps.invert(_foreground_mask(image.size))
    background_detail = softened_edges.filter(ImageFilter.UnsharpMask(radius=0.55, percent=18, threshold=6))
    balanced = Image.composite(background_detail, softened_edges, background_mask)
    return Image.blend(image, balanced, 0.18)


def _inject_subtle_film_grain(image, *, amount: float = 0.028):
    from PIL import Image, ImageChops, ImageEnhance

    amount = max(0.0, min(0.08, float(amount)))
    if amount <= 0:
        return image
    width, height = image.size
    noise = Image.effect_noise((width, height), 4.0 + amount * 80).convert("L")
    noise = ImageEnhance.Contrast(noise).enhance(0.32)
    neutral = Image.new("L", image.size, 128)
    grain_delta = ImageChops.subtract(noise, neutral, scale=1.0, offset=128)
    grain_rgb = Image.merge("RGB", (grain_delta, grain_delta, grain_delta))
    return Image.blend(image, grain_rgb, amount)


def _face_detail_mask(size: tuple[int, int], *, opacity: int = 150):
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    left = int(width * 0.30)
    top = int(height * 0.12)
    right = int(width * 0.70)
    bottom = int(height * 0.47)
    draw.ellipse((left, top, right, bottom), fill=max(0, min(255, int(opacity))))
    return mask.filter(ImageFilter.GaussianBlur(radius=max(8, int(min(width, height) * 0.025))))


def _eye_detail_mask(size: tuple[int, int]):
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    top = int(height * 0.22)
    bottom = int(height * 0.32)
    draw.ellipse((int(width * 0.34), top, int(width * 0.48), bottom), fill=135)
    draw.ellipse((int(width * 0.52), top, int(width * 0.66), bottom), fill=135)
    return mask.filter(ImageFilter.GaussianBlur(radius=max(4, int(min(width, height) * 0.012))))


def _smile_polish_mask(size: tuple[int, int]):
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse(
        (int(width * 0.39), int(height * 0.34), int(width * 0.61), int(height * 0.43)),
        fill=42,
    )
    return mask.filter(ImageFilter.GaussianBlur(radius=max(5, int(min(width, height) * 0.016))))


def _subject_fill_mask(size: tuple[int, int]):
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    left = int(width * 0.22)
    top = int(height * 0.10)
    right = int(width * 0.78)
    bottom = int(height * 0.70)
    draw.ellipse((left, top, right, bottom), fill=142)
    return mask.filter(ImageFilter.GaussianBlur(radius=max(18, int(min(width, height) * 0.06))))


def _foreground_mask(size: tuple[int, int]):
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    left = int(width * 0.12)
    top = int(height * 0.03)
    right = int(width * 0.88)
    bottom = int(height * 0.98)
    radius = int(min(width, height) * 0.28)
    draw.rounded_rectangle((left, top, right, bottom), radius=radius, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(radius=max(12, int(min(width, height) * 0.045))))


def _target_master_long_edge(image) -> int:
    current = max(image.size)
    scale = max(1, int(settings.postprocess_upscale_factor))
    max_edge = max(900, int(settings.postprocess_max_long_edge))
    return min(max_edge, max(current, current * scale))


def _resize_long_edge(image, long_edge: int):
    from PIL import Image

    width, height = image.size
    current = max(width, height)
    if current == long_edge:
        return image
    ratio = long_edge / float(current)
    size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    return image.resize(size, resample)


def _crop_to_variant(image, variant: DeliveryVariant):
    from PIL import Image, ImageOps

    if not variant.aspect_ratio:
        return image.copy()
    long_edge = max(900, int(settings.postprocess_max_long_edge))
    if variant.aspect_ratio < 1:
        target = (max(1, round(long_edge * variant.aspect_ratio)), long_edge)
    else:
        target = (long_edge, max(1, round(long_edge / variant.aspect_ratio)))
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    return ImageOps.fit(image, target, method=resample, centering=_smart_crop_centering(variant))


def _smart_crop_centering(variant: DeliveryVariant) -> tuple[float, float]:
    ratio = float(variant.aspect_ratio or 1)
    if ratio <= 0.6:
        return (0.5, 0.42)
    if ratio < 1:
        return (0.5, 0.44)
    if ratio > 1.2:
        return (0.5, 0.47)
    return (0.5, 0.43)


def _encode_delivery_jpeg(image) -> tuple[bytes, int, int]:
    output = io.BytesIO()
    quality = max(75, min(96, int(settings.postprocess_jpeg_quality)))
    image.convert("RGB").save(output, format="JPEG", quality=quality, optimize=True)
    payload = output.getvalue()
    try:
        from PIL import Image

        with Image.open(io.BytesIO(payload)) as decoded:
            decoded.verify()
            width, height = decoded.size
    except Exception as exc:
        raise PrivatePostprocessError("delivery_artifact_technical_qa_failed") from exc
    if not payload or width <= 0 or height <= 0:
        raise PrivatePostprocessError("delivery_artifact_technical_qa_failed")
    return payload, width, height


def render_private_delivery_set(
    candidate: ValidatedPrivateImage,
    *,
    template_id: str | None = None,
) -> RenderedPrivateDeliverySet:
    """Render one 3:4 master and the complete six-variant private set."""

    from PIL import Image
    from app.models.media_asset import MediaAssetRole

    processed = postprocess_private_candidate(candidate, template_id=template_id)
    try:
        with Image.open(io.BytesIO(processed.image_bytes)) as decoded:
            decoded.load()
            source = decoded.convert("RGB")
    except Exception as exc:
        raise PrivatePostprocessError("delivery_master_decode_failed") from exc

    master_image = _crop_to_variant(source, VARIANT_MAP["3x4"])
    master_bytes, master_width, master_height = _encode_delivery_jpeg(master_image)
    master = RenderedPrivateArtifact(
        name="master_3x4",
        role=MediaAssetRole.FINAL_MASTER,
        image_bytes=master_bytes,
        mime_type="image/jpeg",
        sha256=sha256_digest(master_bytes).hexdigest(),
        width=master_width,
        height=master_height,
    )
    variants: dict[str, RenderedPrivateArtifact] = {}
    for ratio_name, internal_key in _PAID_VARIANT_INTERNAL_KEYS.items():
        variant_image = _crop_to_variant(source, VARIANT_MAP[internal_key])
        payload, width, height = _encode_delivery_jpeg(variant_image)
        expected_width, expected_height = PAID_VARIANT_RATIOS[ratio_name]
        if width * expected_height != height * expected_width:
            raise PrivatePostprocessError("delivery_variant_ratio_invalid")
        variants[ratio_name] = RenderedPrivateArtifact(
            name=f"variant_{internal_key}",
            role=MediaAssetRole.DELIVERY_VARIANT,
            image_bytes=payload,
            mime_type="image/jpeg",
            sha256=sha256_digest(payload).hexdigest(),
            width=width,
            height=height,
        )
    result = RenderedPrivateDeliverySet(master=master, variants=variants)
    if not result.is_complete:
        raise PrivatePostprocessError("delivery_artifact_set_incomplete")
    return result
