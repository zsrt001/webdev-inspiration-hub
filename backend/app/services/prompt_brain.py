"""Prompt Brain - deterministic prompt builder for identity-preserving wedding edits."""

from typing import Optional
from app.schemas.template import Template


PHOTO_PROTOCOL = (
    "premium bridal studio finish, print-ready high-end wedding portrait, realistic skin texture, natural pores, "
    "sharp eyes, realistic hair strands, crisp couture fabric and embroidery details, "
    "commercial bridal retouching with clean but non-plastic semi-matte skin, controlled natural highlights, "
    "no oily shine on forehead, nose, cheeks, or chin, luxury studio-grade color grading, "
    "polished background separation, paid bridal-studio deliverable quality"
)

IDENTITY_LOCK_PROTOCOL = (
    "Identity lock is mandatory and highest priority: this is an image edit of the uploaded real person, not "
    "text-to-image character creation. Preserve the exact identity: preserve the same face shape, eye shape "
    "and spacing, brow structure, nose bridge and tip, mouth shape, jawline, chin, age impression, skin undertone, "
    "facial proportions, and "
    "natural expression from the identity reference. Makeup and hairstyle styling are allowed only if the person "
    "still reads as the same individual. Do not beautify the subject into a different person, do not replace the "
    "face with a generic model face, and do not reshape facial structure"
)

EDIT_SCOPE_PROTOCOL = (
    "Allowed edit scope: change wedding clothing, accessories, pose, body styling, background, set design, "
    "professional lighting, color grading, hairstyle styling, and bridal makeup. Keep the identity face anchored "
    "to the source references. If style, scene, clothing, or beauty conflicts with identity preservation, identity wins"
)

COUPLE_IDENTITY_LOCK_PROTOCOL = (
    "For couples, reference image 1 must remain subject A/bride and reference image 2 must remain subject B/groom; "
    "preserve each person's separate facial identity, age impression, face geometry, expression, and role. Never "
    "swap identities, merge faces, average faces, duplicate one subject, or make both subjects share the same AI face"
)

STUDIO_QUALITY_PROTOCOL = (
    "Professional bridal-studio quality: controlled softbox key light, gentle fill light, subtle rim light, visible "
    "catchlights, face correctly exposed and slightly brighter than the background, preserved highlight detail in "
    "skin and dress fabric, natural shadow rolloff, balanced contrast, refined skin texture, soft powder-finish "
    "semi-matte skin, controlled specular highlights, clean but realistic retouching, couture-level fabric detail, "
    "polished composition, and premium wedding album color grading"
)

OUTDOOR_PRO_LIGHTING_PROTOCOL = (
    "Outdoor professional lighting protocol: outdoor scenes are allowed only as studio-grade on-location bridal "
    "photography. Use balanced ambient light with controlled off-camera softbox or strobe fill, accurate facial "
    "exposure, visible catchlights, preserved sky and dress highlights, natural skin tone, elegant background "
    "separation, and refined wedding-photographer color grading. Do not use harsh outdoor backlight as the primary "
    "light. Outdoor must never look like a tourist snapshot, phone photo, harsh noon sun photo, backlit silhouette, "
    "or casual travel image"
)

INDOOR_SCENE_BOUNDARY_PROTOCOL = (
    "Indoor/studio template boundary: for indoor or studio templates, keep the scene indoors and do not add "
    "mountain vistas, open sky, outdoor balconies, beach backgrounds, forest backgrounds, or unrelated travel scenery"
)

STUDIO_LIGHTING_GUARDRAILS = (
    f"{STUDIO_QUALITY_PROTOCOL}. {OUTDOOR_PRO_LIGHTING_PROTOCOL}. {INDOOR_SCENE_BOUNDARY_PROTOCOL}. "
    "Hard studio-quality requirements: do not use harsh outdoor backlight as the primary light; "
    "do not leave the face in shadow; do not blow out sky, windows, or dress highlights; "
    "use large softbox-style key light plus gentle fill light on every face; "
    "keep facial exposure natural and slightly brighter than the background without wet or greasy shine; "
    "avoid tourist-photo lighting, AI-glossy skin, oily skin, waxy specular highlights, fantasy-game styling, "
    "and cheap composited background"
)

FULL_LENGTH_COMPOSITION = (
    "full-length 3:4 vertical editorial composition, complete gown and dress train visible, "
    "no cropped hem, elegant headroom, subject not overfilled, enough breathing room around the body, "
    "luxury bridal studio posing with refined posture"
)

HAND_POSE_SAFETY_PROTOCOL = (
    "Use simple professional bridal hand posing: relaxed hands, one bouquet or veil touch at waist level, "
    "fingers mostly covered by bouquet, sleeves, veil, or dress fabric when possible. Avoid interlaced fingers, "
    "spread fingers, complex hand gestures, hands close to the face, duplicated bouquets, and exposed tiny fingers. "
    "If hand anatomy is uncertain, simplify or partially hide the hands while preserving a natural paid-studio pose"
)

SINGLE_CANVAS_PROPORTION_PROTOCOL = (
    "Commercial single-subject framing: the bride or groom should occupy about 72-86% of the canvas height "
    "for full-length or near full-length portraits; outdoor environmental portraits may be slightly wider but "
    "the subject must never fall below 55% of canvas height. The face should remain large enough to read identity, "
    "about 8-15% of canvas height. Keep headroom tight and intentional, about 3-7% above the head, and keep "
    "4-9% breathing room below the shoes, gown hem, or dress train. Eyes should sit near the upper third. "
    "Do not crop at joints, fingertips, ankles, knees, wrists, gown hem, veil, or train"
)

COUPLE_CANVAS_PROPORTION_PROTOCOL = (
    "Commercial couple framing: the couple group should occupy about 68-84% of the canvas height and about "
    "52-78% of the canvas width. Both faces must be clearly visible and large enough to read identity, about "
    "6-12% of canvas height per face. Keep both subjects at believable scale, with readable separation between "
    "faces, shoulders, arms, outfits, and body silhouettes. Maintain intentional headroom and bottom room for "
    "shoes, suit hem, gown hem, veil, and dress train. Avoid flat side-by-side tourist-photo blocking; use slight "
    "staggering, gentle interaction, and professional wedding pose direction"
)

DELIVERY_GATE_PROTOCOL = (
    "Delivery gate: a candidate is deliverable only if identity remains recognizable, face geometry is natural, "
    "commercial canvas proportion is correct, the face is large and sharp enough to read, the gown/suit/veil/train "
    "are complete, the crop avoids joints and hems, lighting looks professionally controlled, and the image reads "
    "as a paid bridal-studio wedding photograph. If identity, face readability, subject scale, crop, or lighting "
    "conflicts with style, delivery quality wins"
)

CANDIDATE_SELECTION_PROTOCOL = (
    "Candidate generation protocol: when multiple candidates are requested, vary only pose nuance, subject placement, "
    "camera distance within the commercial framing range, lighting polish, and background depth. Do not vary identity, "
    "role order, face structure, person count, or the requested wedding concept. Each candidate must be independently "
    "deliverable and suitable for automated QA ranking"
)

COUPLE_COMPOSITION = (
    "two-person full-length couple portrait, both faces visible, stable 3:4 vertical framing, natural anatomy, "
    "clear separation between two subjects, balanced spacing, bride and groom both fully readable, "
    "complete outfits visible, independent shoulders and arms, no fused bodies, no merged limbs, no shared torso, "
    "simple readable hand placement with no interlaced fingers or hidden fused hands"
)

COUPLE_STUDIO_GUARDRAILS = (
    "Both subjects must receive flattering studio fill light, both faces must be correctly exposed, "
    "both full outfits must remain visible in the 3:4 frame, both identities must remain recognizable, "
    "and the result must read as a paid bridal-studio portrait"
)

NEGATIVE_PROMPT = (
    "Identity failures: generic model face, different person, changed face shape, altered eyes, altered nose, "
    "altered mouth, altered jawline, identity drift, face replacement, face swap, over-beautified face, uncanny face, "
    "same AI face for both people, role swap; "
    "Skin and realism failures: smooth skin, airbrushed, wax, plastic, makeup filter, oily skin, greasy shine, "
    "wet glossy skin, over-shiny forehead, over-shiny nose, over-shiny cheeks, 3d render, cgi, "
    "over-smoothed bridal ad; "
    "Anatomy failures: headless, cropped head, phantom limbs, fused bodies, merged limbs, duplicate person, "
    "duplicated face, shared torso, merged shoulders, fused arms, conjoined bodies, bad hands, extra fingers; "
    "Lighting failures: bright flat lighting, harsh backlight, face in shadow, no catchlights, blown-out sky, "
    "blown-out dress, crushed shadows, uncontrolled mixed light; "
    "Composition failures: subject too small, face too small, background dominates the subject, excessive headroom, "
    "awkward crop, cropped dress, dress cutoff, cut-off gown train, missing full outfit, flat centered pose, "
    "weak couple interaction, poor subject separation, low-end snapshot, tourist snapshot, phone photo, "
    "outdoor travel snapshot; "
    "Scene failures: fantasy game costume, cheap composite, unrequested mountain vista, unrequested open sky, "
    "unrequested beach, unrequested forest, unrelated travel background"
)


def _section(title: str, body: str | None) -> str:
    cleaned = str(body or "").strip().strip(".")
    if not cleaned:
        return ""
    return f"{title}: {cleaned}."


def get_studio_guardrails(*, is_couple: bool = False) -> str:
    parts = [
        _section("STUDIO QUALITY", STUDIO_QUALITY_PROTOCOL),
        _section("OUTDOOR PROFESSIONAL LIGHTING", OUTDOOR_PRO_LIGHTING_PROTOCOL),
        _section("SCENE BOUNDARY", INDOOR_SCENE_BOUNDARY_PROTOCOL),
        _section("COMPOSITION", FULL_LENGTH_COMPOSITION),
        _section("HAND AND ANATOMY SAFETY", HAND_POSE_SAFETY_PROTOCOL),
        _section("CANVAS PROPORTION", SINGLE_CANVAS_PROPORTION_PROTOCOL),
        _section("DELIVERY GATE", DELIVERY_GATE_PROTOCOL),
        _section("CANDIDATE SELECTION", CANDIDATE_SELECTION_PROTOCOL),
        _section("LIGHTING NEGATIVE GUARDRAILS", STUDIO_LIGHTING_GUARDRAILS),
    ]
    if is_couple:
        parts.extend(
            [
                _section("COUPLE COMPOSITION", COUPLE_COMPOSITION),
                _section("COUPLE CANVAS PROPORTION", COUPLE_CANVAS_PROPORTION_PROTOCOL),
                _section("COUPLE STUDIO QUALITY", COUPLE_STUDIO_GUARDRAILS),
            ]
        )
    return "\n".join(part for part in parts if part)


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
        _section("IDENTITY LOCK", IDENTITY_LOCK_PROTOCOL),
        _section("ALLOWED EDIT SCOPE", EDIT_SCOPE_PROTOCOL),
        _section("WARDROBE", f"A professional wedding portrait of {clothing}"),
        _section("SCENE", scene),
        _section("STUDIO QUALITY", STUDIO_QUALITY_PROTOCOL),
        _section("OUTDOOR PROFESSIONAL LIGHTING", OUTDOOR_PRO_LIGHTING_PROTOCOL),
        _section("SCENE BOUNDARY", INDOOR_SCENE_BOUNDARY_PROTOCOL),
        _section("COMPOSITION", FULL_LENGTH_COMPOSITION),
        _section("HAND AND ANATOMY SAFETY", HAND_POSE_SAFETY_PROTOCOL),
        _section("CANVAS PROPORTION", SINGLE_CANVAS_PROPORTION_PROTOCOL),
        _section("DELIVERY GATE", DELIVERY_GATE_PROTOCOL),
        _section("CANDIDATE SELECTION", CANDIDATE_SELECTION_PROTOCOL),
    ]
    if is_couple:
        parts.append(_section("COUPLE IDENTITY LOCK", COUPLE_IDENTITY_LOCK_PROTOCOL))
        parts.append(_section("COUPLE COMPOSITION", COUPLE_COMPOSITION))
        parts.append(_section("COUPLE CANVAS PROPORTION", COUPLE_CANVAS_PROPORTION_PROTOCOL))
        parts.append(_section("COUPLE STUDIO QUALITY", COUPLE_STUDIO_GUARDRAILS))
    if user_text:
        parts.append(_section("USER DIRECTION", user_text))
    if blocks:
        parts.append(_section("TEMPLATE STYLE NOTES", ", ".join(blocks)))
    parts.append(_section("FINAL RENDER QUALITY", PHOTO_PROTOCOL))
    parts.append(_section("FORBIDDEN CONSTRAINTS", NEGATIVE_PROMPT))

    return "\n".join(part for part in parts if part)


def get_negative_prompt() -> str:
    return NEGATIVE_PROMPT
