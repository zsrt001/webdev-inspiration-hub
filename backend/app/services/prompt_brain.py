"""Prompt Brain - deterministic prompt builder for ComfyUI pipeline."""

from typing import Optional
from app.schemas.template import Template


PHOTO_PROTOCOL = (
    "high-end studio wedding portrait, realistic skin texture, natural pores, "
    "subtle film grain, balanced contrast, cinematic lighting, crisp details"
)

COUPLE_COMPOSITION = (
    "two-person couple portrait, both faces visible, stable framing, natural anatomy, "
    "clear separation between two subjects, balanced spacing, bride and groom both fully readable, "
    "independent shoulders and arms, no fused bodies, no merged limbs, no shared torso"
)

NEGATIVE_PROMPT = (
    "smooth skin, airbrushed, wax, plastic, 3d render, cgi, makeup filter, "
    "bright flat lighting, headless, cropped head, phantom limbs, fused bodies, merged limbs, "
    "duplicate person, duplicated face, shared torso, merged shoulders, fused arms, conjoined bodies"
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
