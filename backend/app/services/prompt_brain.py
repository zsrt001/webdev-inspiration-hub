"""Prompt Brain - deterministic prompt builder for ComfyUI pipeline."""

from typing import Optional
from app.schemas.template import Template


PHOTO_PROTOCOL = (
    "premium bridal studio finish, print-ready high-end wedding portrait, realistic skin texture, natural pores, "
    "controlled softbox key light, gentle fill light, subtle rim light, visible catchlights, "
    "face correctly exposed and slightly brighter than the background, preserved highlight detail, "
    "natural shadow rolloff, balanced contrast, crisp couture fabric and embroidery details, "
    "commercial bridal retouching with clean but non-plastic skin, sharp eyes, realistic hair strands, "
    "luxury studio-grade color grading, polished background separation"
)

IDENTITY_LOCK_PROTOCOL = (
    "Identity lock is mandatory: use the uploaded portrait reference as the identity anchor, preserve the same "
    "face shape, eye shape and spacing, nose bridge and tip, mouth shape, jawline, chin, skin undertone, and natural "
    "facial expression. Change only clothing, pose, lighting, background, hairstyle styling, and bridal makeup. "
    "Do not beautify the subject into a different person, do not replace the face with a generic model face, "
    "do not reshape facial structure"
)

COUPLE_IDENTITY_LOCK_PROTOCOL = (
    "For couples, reference image 1 must remain subject A and reference image 2 must remain subject B; preserve each "
    "person's separate facial identity, age impression, face geometry, and expression. Do not swap identities, merge "
    "faces, average faces, or make the two subjects look like unrelated generic models"
)

STUDIO_LIGHTING_GUARDRAILS = (
    "Hard studio-quality requirements: do not use harsh outdoor backlight as the primary light; "
    "do not leave the face in shadow; do not blow out sky, windows, or dress highlights; "
    "use large softbox-style key light plus gentle fill light on every face; "
    "keep the lighting controlled and studio-grade even when the requested scene is outdoors; "
    "for indoor or studio templates, keep the scene indoors and do not add mountain vistas, open sky, or outdoor balconies; "
    "keep facial exposure natural, luminous, and slightly brighter than the background; "
    "preserve realistic skin tone and refined bridal-retouch texture; avoid tourist-photo lighting, AI-glossy skin, "
    "fantasy-game styling, and cheap composited background"
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

COUPLE_STUDIO_GUARDRAILS = (
    "Both subjects must receive flattering studio fill light, both faces must be correctly exposed, "
    "both full outfits must remain visible in the 3:4 frame, both identities must remain recognizable, "
    "and the result must read as a paid bridal-studio portrait"
)

NEGATIVE_PROMPT = (
    "smooth skin, airbrushed, wax, plastic, 3d render, cgi, makeup filter, "
    "generic model face, different person, changed face shape, altered eyes, altered nose, altered mouth, "
    "identity drift, face replacement, face swap, over-beautified face, uncanny face, "
    "bright flat lighting, headless, cropped head, phantom limbs, fused bodies, merged limbs, "
    "duplicate person, duplicated face, shared torso, merged shoulders, fused arms, conjoined bodies, "
    "harsh backlight, face in shadow, blown-out sky, crushed shadows, overfilled frame, "
    "cropped dress, cut-off gown train, low-end snapshot, tourist snapshot, fantasy game costume, "
    "cheap composite, over-smoothed bridal ad, unrequested mountain vista, unrequested open sky, outdoor travel snapshot"
)


def get_studio_guardrails(*, is_couple: bool = False) -> str:
    parts = [STUDIO_LIGHTING_GUARDRAILS, FULL_LENGTH_COMPOSITION]
    if is_couple:
        parts.extend([COUPLE_COMPOSITION, COUPLE_STUDIO_GUARDRAILS])
    return ". ".join(parts)


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
        IDENTITY_LOCK_PROTOCOL,
        f"A professional wedding portrait of {clothing}",
        f"Scene: {scene}",
        FULL_LENGTH_COMPOSITION,
        STUDIO_LIGHTING_GUARDRAILS,
    ]
    if is_couple:
        parts.append(COUPLE_IDENTITY_LOCK_PROTOCOL)
        parts.append(COUPLE_COMPOSITION)
        parts.append(COUPLE_STUDIO_GUARDRAILS)
    if user_text:
        parts.append(user_text)
    if blocks:
        parts.append(", ".join(blocks))
    parts.append(PHOTO_PROTOCOL)

    return ". ".join(parts) + "."


def get_negative_prompt() -> str:
    return NEGATIVE_PROMPT
