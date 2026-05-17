"""Prompt Brain - deterministic prompt builder for identity-preserving wedding edits."""

import re
from typing import Optional
from app.schemas.template import Template


SKIN_REALISM_PROTOCOL = (
    "CRITICAL SKIN TEXTURE REQUIREMENT (highest priority after identity): "
    "render natural human skin with visible pores, micro-texture, subtle imperfections, and realistic subsurface "
    "scattering. Absolutely forbid: smooth plastic skin, airbrushed beauty-filter skin, oily shine on forehead "
    "nose cheeks or chin, waxy specular highlights, wet glossy skin, CGI-render texture, over-processed bridal "
    "retouching, beauty-app filter face, doll-like complexion, alpha-matte edge artifacts. "
    "The face must look like a professionally lit photograph of a real person, not a rendered or retouched image. "
    "Use semi-matte powder-finish skin with controlled highlight rolloff and just-visible pore texture. "
    "Aim for Hasselblad medium-format bridal-portrait skin rendering with subtle film-like micro-contrast"
)

PHOTO_REALISM_PROTOCOL = (
    "output a photorealistic editorial wedding photograph shot on a Hasselblad H6D with 100mm f/2.2 lens, "
    "Portra 400 film stock color science, natural film grain barely visible, Zeiss Otus-level micro-contrast, "
    "professional color grading with gentle warm tone curve, lifted blacks at 5%, subtle S-curve contrast, "
    "no HDR tone-mapping, no over-sharpened digital edges, no smartphone computational-photography look, "
    "print-ready 300dpi bridal-studio deliverable"
)

GEMINI_FLASH_EDIT_PROTOCOL = (
    "Gemini 3.1 Flash image-edit protocol: treat every uploaded image as visual evidence, not optional style "
    "inspiration. The first identity face crop is the primary facial-geometry anchor, the original portrait is the "
    "full-body and skin-undertone anchor, and style references may influence only scene, wardrobe, lighting, and "
    "color. Keep edits local and photographic: preserve source face geometry, skin undertone, age impression, "
    "expression family, and asymmetry while changing only the wedding styling. Prefer realistic camera optics, "
    "subtle analog film grain, Kodak Portra-like color response, natural lens falloff, and coherent softbox lighting. "
    "Avoid the Gemini failure modes of invented beauty-model faces, smeared pores, glossy AI skin, over-clean hair, "
    "plastic white dresses, text artifacts, and decorative background hallucinations"
)

PHOTO_PROTOCOL = (
    "premium bridal studio finish, print-ready high-end wedding portrait, realistic skin texture, natural pores, "
    "sharp eyes, realistic hair strands, crisp couture fabric and embroidery details, "
    "commercial bridal retouching with clean but non-plastic semi-matte skin, controlled natural highlights, "
    "no oily shine on forehead, nose, cheeks, or chin, luxury studio-grade color grading, "
    "polished background separation, paid bridal-studio deliverable quality"
)

ANTI_AI_ARTIFACTS_PROTOCOL = (
    "Strictly avoid all AI-generated-image artifacts and tells: no overly symmetrical face, no generic AI face, "
    "no plastic skin, no waxy highlights, no beauty-filter texture, no CGI rendering, no 3D-render look, "
    "no game-engine lighting, no fantasy concept-art styling, no unnatural eye sharpness, no uncanny-valley "
    "expression, no over-perfect symmetry in facial features, no identical couple faces, no AI-hallucinated "
    "background details, no over-sharpened fabric, no chromatic aberration on edges. "
    "The image must pass as a human-shot professional wedding photograph."
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
    "the output must contain exactly two primary wedding subjects in the same frame. Preserve each person's separate "
    "facial identity, age impression, face geometry, expression, and role. Never create a solo portrait, omit either "
    "subject, swap identities, merge faces, average faces, duplicate one subject, or make both subjects share the same AI face"
)

INDOOR_STUDIO_LIGHTING_PROTOCOL = (
    "Indoor bridal-studio lighting: use a large soft key light at about 45 degrees from the subject, weak fill "
    "light on the shadow side, and a subtle rim or hair light for separation. Keep the face naturally exposed and "
    "slightly brighter than the scene, with the background about 0.3 to 0.8 stops darker than the face. Preserve "
    "soft shadow rolloff, visible catchlights, semi-matte skin texture, and detailed white dress highlights"
)

OUTDOOR_PRO_LIGHTING_PROTOCOL = (
    "Outdoor professional lighting protocol: outdoor scenes are allowed only as studio-grade on-location bridal "
    "photography. The sun may act only as rim light, hair light, or ambient background light, never as the harsh "
    "primary light on the face. Use frontal softbox-style fill or bounced fill so every face is correctly exposed "
    "with visible catchlights. Do not use harsh outdoor backlight as the primary light. Preserve sky detail, window "
    "detail, and white gown highlights; do not allow the face to fall into shadow, and never let the sky or dress "
    "blow out"
)

WINDOW_ARCHITECTURAL_LIGHTING_PROTOCOL = (
    "Window and architectural lighting: use window light as the directional key light, with the face turned toward "
    "the soft window source and gentle fill controlling shadow density. Darken the background slightly so the "
    "person separates from architecture, columns, curtains, arches, or walls. Preserve dimensional face modeling, "
    "natural skin tone, and refined editorial wedding color; do not flatten the face into even phone-photo light"
)

NIGHT_LOW_LIGHTING_PROTOCOL = (
    "Night or low-light indoor protocol: use an off-camera key light with weak ambient practical light in the "
    "background. Keep color temperature controlled and coherent across skin, dress, and environment. Do not use "
    "phone-flash lighting, direct on-camera flash, muddy underexposure, mixed green/orange color casts, crushed "
    "shadows, or a background that is brighter than the subjects' faces"
)

STUDIO_QUALITY_PROTOCOL = (
    "Professional bridal-studio quality: apply the correct lighting protocol for the scene, with controlled softbox "
    "key light, gentle fill light, subtle rim separation, visible catchlights, face correctly exposed and slightly "
    "brighter than the background, preserved highlight detail in skin and dress fabric, natural shadow rolloff, "
    "balanced contrast, refined skin texture, soft powder-finish semi-matte skin, controlled specular highlights, "
    "clean but realistic retouching, couture-level fabric detail, polished composition, and premium wedding album "
    "color grading"
)

INDOOR_SCENE_BOUNDARY_PROTOCOL = (
    "Indoor/studio template boundary: for indoor or studio templates, keep the scene indoors and do not add "
    "mountain vistas, open sky, outdoor balconies, beach backgrounds, forest backgrounds, or unrelated travel scenery"
)

STUDIO_LIGHTING_GUARDRAILS = (
    f"{STUDIO_QUALITY_PROTOCOL}. {INDOOR_STUDIO_LIGHTING_PROTOCOL}. {OUTDOOR_PRO_LIGHTING_PROTOCOL}. "
    f"{WINDOW_ARCHITECTURAL_LIGHTING_PROTOCOL}. {NIGHT_LOW_LIGHTING_PROTOCOL}. {INDOOR_SCENE_BOUNDARY_PROTOCOL}. "
    "Hard studio-quality requirements: select one coherent lighting plan from the scene type and execute it "
    "clearly; do not use harsh outdoor backlight as the primary light; do not leave the face in shadow; do not "
    "blow out sky, windows, or dress highlights; use large softbox-style key light plus gentle fill light on every "
    "face; keep facial exposure natural and slightly brighter than the background without wet or greasy shine; "
    "avoid tourist-photo lighting, AI-glossy skin, oily skin, waxy specular highlights, fantasy-game styling, "
    "phone-flash lighting, direct on-camera flash, uncontrolled mixed color temperature, and cheap composited "
    "background"
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
    "exactly two-person full-length couple portrait, never solo, never missing one partner, both faces visible, "
    "stable 3:4 vertical framing, natural anatomy, clear separation between two subjects, balanced spacing, bride and groom both fully readable, "
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
    "Lighting failures: bright flat lighting, harsh backlight, sun as primary face light, missing frontal fill, "
    "face in shadow, underexposed face, background brighter than face, no catchlights, blown-out sky, blown-out "
    "window, blown-out dress, crushed shadows, uncontrolled mixed light, mixed color temperature, direct on-camera "
    "flash, phone-flash lighting, muddy night lighting; "
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
        _section("SKIN REALISM", SKIN_REALISM_PROTOCOL),
        _section("ANTI AI ARTIFACTS", ANTI_AI_ARTIFACTS_PROTOCOL),
        _section("STUDIO QUALITY", STUDIO_QUALITY_PROTOCOL),
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

    # Determine scene type — strip negated clauses to avoid false positives
    scene_clean = scene.lower()
    # Remove "no X", "no X Y", "not X" patterns that negate scene keywords
    scene_clean = re.sub(r'\bno\s+\w+(?:\s+\w+)?\b', '', scene_clean)
    scene_clean = re.sub(r'\bnot\s+\w+\b', '', scene_clean)
    is_outdoor = any(w in scene_clean for w in ("outdoor", "beach", "garden", "forest", "mountain", "sunset", "street", "courtyard", "zen garden", "maple", "neon"))
    is_night = any(w in scene_clean for w in ("night", "candle", "dusk", "twilight", "dark", "low-light"))
    is_window = any(w in scene_clean for w in ("window", "balcony"))

    parts: list[str] = []
    parts.append(
        _section(
            "PROMPT ARCHITECTURE",
            "three-layer commercial edit contract: identity layer is immutable, photography layer controls light skin lens and color, delivery layer controls crop size and anti-AI finish",
        )
    )
    parts.append(
        _section(
            "IDENTITY LAYER - IMMUTABLE",
            "highest priority, never reinterpret, never average, never beautify into another person; all later photography and delivery choices must yield to identity preservation",
        )
    )

    # Layer 1: Identity (highest priority — placed first)
    parts.append(_section("IDENTITY LOCK", IDENTITY_LOCK_PROTOCOL))
    parts.append(_section("ALLOWED EDIT SCOPE", EDIT_SCOPE_PROTOCOL))
    if is_couple:
        parts.append(_section("COUPLE IDENTITY LOCK", COUPLE_IDENTITY_LOCK_PROTOCOL))

    # Layer 2: Skin & photorealism (critical for commercial quality — early placement)
    parts.append(
        _section(
            "PHOTOGRAPHY LAYER",
            "control only lighting, skin texture, lens rendering, color science, depth separation, and studio polish while preserving the immutable identity layer",
        )
    )
    parts.append(_section("SKIN REALISM", SKIN_REALISM_PROTOCOL))
    parts.append(_section("PHOTO REALISM", PHOTO_REALISM_PROTOCOL))
    parts.append(_section("GEMINI FLASH EDIT PROTOCOL", GEMINI_FLASH_EDIT_PROTOCOL))
    # Layer 3: Scene & wardrobe
    parts.append(_section("WARDROBE", f"A professional wedding portrait of {clothing}"))
    parts.append(_section("SCENE", scene))

    # Layer 4: Smart lighting — only the relevant protocol, not all
    if is_night:
        parts.append(_section("NIGHT LIGHTING", NIGHT_LOW_LIGHTING_PROTOCOL))
    elif is_outdoor:
        parts.append(_section("OUTDOOR LIGHTING", OUTDOOR_PRO_LIGHTING_PROTOCOL))
    elif is_window:
        parts.append(_section("WINDOW LIGHTING", WINDOW_ARCHITECTURAL_LIGHTING_PROTOCOL))
    else:
        parts.append(_section("INDOOR LIGHTING", INDOOR_STUDIO_LIGHTING_PROTOCOL))

    parts.append(_section("STUDIO QUALITY", STUDIO_QUALITY_PROTOCOL))
    if not is_outdoor:
        parts.append(_section("SCENE BOUNDARY", INDOOR_SCENE_BOUNDARY_PROTOCOL))

    # Layer 5: Composition
    parts.append(
        _section(
            "DELIVERY LAYER",
            "final output must be a commercial wedding deliverable with correct 3:4 master framing, complete gown or suit, clean crop variants, protected white highlights, subtle film grain, and no visible AI tells",
        )
    )
    parts.append(_section("ANTI AI ARTIFACTS", ANTI_AI_ARTIFACTS_PROTOCOL))
    parts.append(_section("COMPOSITION", FULL_LENGTH_COMPOSITION))
    parts.append(_section("HAND AND ANATOMY SAFETY", HAND_POSE_SAFETY_PROTOCOL))
    parts.append(_section("CANVAS PROPORTION", SINGLE_CANVAS_PROPORTION_PROTOCOL))

    if is_couple:
        parts.append(_section("COUPLE COMPOSITION", COUPLE_COMPOSITION))
        parts.append(_section("COUPLE CANVAS PROPORTION", COUPLE_CANVAS_PROPORTION_PROTOCOL))
        parts.append(_section("COUPLE STUDIO QUALITY", COUPLE_STUDIO_GUARDRAILS))

    # Layer 6: User input + style notes
    if user_text:
        parts.append(_section("USER DIRECTION", user_text))
    if blocks:
        parts.append(_section("TEMPLATE STYLE NOTES", ", ".join(blocks)))

    # Layer 7: Final quality gate
    parts.append(_section("FINAL RENDER QUALITY", PHOTO_PROTOCOL))
    parts.append(_section("FORBIDDEN CONSTRAINTS", NEGATIVE_PROMPT))

    return "\n".join(part for part in parts if part)


def get_negative_prompt() -> str:
    return NEGATIVE_PROMPT
