"""Prompt Brain - deterministic prompt builder for ComfyUI pipeline."""

from typing import Optional
from app.schemas.template import Template


PHOTO_PROTOCOL = (
    "premium bridal studio finish, print-ready high-end wedding portrait, realistic skin texture, natural pores, "
    "controlled softbox key light, gentle fill light, subtle rim light, visible catchlights, "
    "face correctly exposed and slightly brighter than the background, preserved highlight detail, "
    "natural shadow rolloff, balanced contrast, crisp couture fabric and embroidery details"
)

FULL_LENGTH_COMPOSITION = (
    "full-length 3:4 vertical editorial composition, complete gown and dress train visible, "
    "no cropped hem, elegant headroom, subject not overfilled, enough breathing room around the body, "
    "luxury bridal studio posing with refined posture"
)

COUPLE_COMPOSITION = (
    "two-person full-length couple portrait, both faces visible, stable 3:4 vertical framing, natural anatomy, "
    "clear separation between two subjects, balanced spacing, bride and groom both fully readable, "
    "complete outfits visible, independent shoulders and arms, no fused bodies, no merged limbs, no shared torso"
)

NEGATIVE_PROMPT = (
    "smooth skin, airbrushed, wax, plastic, 3d render, cgi, makeup filter, "
    "bright flat lighting, headless, cropped head, phantom limbs, fused bodies, merged limbs, "
    "duplicate person, duplicated face, shared torso, merged shoulders, fused arms, conjoined bodies, "
    "harsh backlight, face in shadow, blown-out sky, crushed shadows, overfilled frame, "
    "cropped dress, cut-off gown train, low-end snapshot"
)


def build_prompt(
    template: Template,
    user_text: Optional[str] = None,
    scene_text: Optional[str] = None,
    clothing_text: Optional[str] = None,
    is_couple: bool = False,
) -> str:
    clothing = clothing_text or template.clothing_prompt
    scene = scene_text or template.default_background_prompt

    blocks = []
    if template.prompt_blocks:
        for key in ("lighting", "texture", "composition", "style"):
            val = template.prompt_blocks.get(key)
            if val:
                blocks.append(val)

    parts = [
        f"A professional wedding portrait of {clothing}",
        f"Scene: {scene}",
        FULL_LENGTH_COMPOSITION,
    ]
    if is_couple:
        parts.append(COUPLE_COMPOSITION)
    if user_text:
        parts.append(user_text)
    if blocks:
        parts.append(", ".join(blocks))
    parts.append(PHOTO_PROTOCOL)

    return ". ".join(parts) + "."


def get_negative_prompt() -> str:
    return NEGATIVE_PROMPT
