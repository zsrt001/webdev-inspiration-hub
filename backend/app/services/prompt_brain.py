"""Prompt Brain - deterministic prompt builder for identity-preserving wedding edits."""

import re
from typing import Optional
from app.schemas.template import Template


def is_golden_anniversary_template(template: object | None) -> bool:
    if not template:
        return False
    tags = getattr(template, "tags", None) or []
    tag_text = " ".join(str(tag) for tag in tags) if isinstance(tags, list) else str(tags)
    haystack = " ".join(
        str(value or "")
        for value in [
            getattr(template, "id", ""),
            getattr(template, "style_family", ""),
            getattr(template, "category", ""),
            getattr(template, "recommended_for", ""),
            tag_text,
        ]
    ).lower()
    return any(token in haystack for token in ("golden", "anniversary", "parents_and_elders"))


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

EYE_EXPRESSION_PROTOCOL = (
    "Commercial eye and expression requirement: eyes must be alive, naturally aligned, and emotionally believable "
    "for a paid wedding portrait. Preserve the source person's eye shape, eyelid fold, pupil direction, brow rhythm, "
    "mouth shape, and expression family, but polish the moment so the gaze feels intentional rather than blank, "
    "cross-eyed, side-glancing by accident, startled, sleepy, waxy, doll-like, or over-posed. Use natural catchlights "
    "that match the key or fill light, relaxed eyelids, realistic iris detail, and a calm confident bridal expression "
    "or gentle authentic smile. For profile or three-quarter poses, the eye-line must point coherently into the pose "
    "or toward the partner; never let the face read as a mannequin, beauty-filter mask, pasted-on smile, or dead-eyed "
    "AI portrait. Commercial expression hierarchy: use a camera-readable near-frontal or soft three-quarter face "
    "with gentle wedding warmth as the default paid deliverable; both eyes, or both eye corners, should remain "
    "visible enough to judge identity and emotion. A full side profile that hides one eye, a detached runway "
    "profile, or a cold fashion-beauty stare is a quality failure unless explicitly requested by the user. The "
    "eyes and mouth must agree emotionally: no mouth-only smile, no cold distant stare with a bridal gown, no "
    "standardized advertising grin, no rigid pageant smile. Single bridal outputs should feel serene, warm, and "
    "present; couple outputs should feel relaxed and connected, with both partners sharing a coherent emotional "
    "moment rather than one person looking detached while the other smiles"
)

PHOTO_REALISM_PROTOCOL = (
    "output a photorealistic editorial wedding photograph shot on a Hasselblad H6D with 100mm f/2.2 lens, "
    "Portra 400 film stock color science, natural film grain barely visible, Zeiss Otus-level micro-contrast, "
    "professional color grading with gentle warm tone curve, lifted blacks at 5%, subtle S-curve contrast, "
    "no HDR tone-mapping, no over-sharpened digital edges, no smartphone computational-photography look, "
    "print-ready 300dpi bridal-studio deliverable"
)

BACKGROUND_DETAIL_PROTOCOL = (
    "Commercial wedding background detail: keep the venue or studio set recognizable and valuable, with readable "
    "architecture, garden texture, drapery, floor lines, arches, columns, windows, floral styling, or painted studio "
    "backdrop details where they exist. Use natural optical depth falloff and subject separation, but do not smear "
    "the background into creamy color blocks, melted bokeh, foggy mush, or an unidentifiable blur. The background "
    "should be secondary to the faces while still proving this is a premium wedding location. Keep mid-frequency "
    "venue texture readable at print size: stone, fabric, flowers, window frames, floor edges, and set decoration "
    "should remain identifiable. Use a commercial editorial depth of field, closer to f/4-f/5.6 background "
    "readability than phone portrait-mode blur; avoid shallow fake-bokeh that erases the location. Background "
    "clarity v3: preserve enough micro-detail to recognize premium venue materials and styling, including masonry "
    "joints, carved edges, window mullions, flower clusters, garden layers, drapery folds, and floor seams, while "
    "keeping faces, eyes, hair, and couture fabric visibly sharper than the background. Do not make the background "
    "tack-sharp or busy; make it commercially readable, gently lower contrast, and naturally behind the subject"
)

GEMINI_FLASH_EDIT_PROTOCOL = (
    "Gemini 3.1 Flash image-edit protocol: treat every uploaded image as visual evidence, not optional style "
    "inspiration. The first identity face crop is the primary facial-geometry anchor, the original portrait is the "
    "body scale and skin-undertone anchor, not a style lock. When user text specifies scene or outfit, the uploaded "
    "source photo's original background, location, clothing, bouquet, veil, and color palette must be ignored as "
    "style evidence and replaced by the requested text. Style references may influence only scene, wardrobe, "
    "lighting, and color. Keep edits local and photographic: preserve source face geometry, skin undertone, age "
    "impression, expression family, and asymmetry while changing only the requested wedding styling. Prefer realistic "
    "camera optics, subtle analog film grain, Kodak Portra-like color response, natural lens falloff, and coherent "
    "softbox lighting. Avoid the Gemini failure modes of invented beauty-model faces, smeared pores, glossy AI skin, "
    "over-clean hair, plastic white dresses, text artifacts, and decorative background hallucinations"
)

SOURCE_PRIORITY_PROTOCOL = (
    "Director source priority: identity upload is the immutable person lock, and the selected subject mode "
    "(single, couple, or golden anniversary) is the immutable person-count lock. For scene and outfit domains, "
    "uploaded reference images are binding when present; user text may refine compatible mood, lens, lighting, "
    "color, and texture but must not overturn an uploaded scene or outfit reference. When no uploaded reference "
    "exists for a domain, user text is the primary creative instruction for that domain. Template presets fill only "
    "unspecified details, and random choices are last-resort defaults. The uploaded identity image remains only a "
    "face, body-scale, age, skin-tone, and expression-family reference; never copy the source portrait's original "
    "background or clothing unless it is explicitly provided as the active scene/outfit reference"
)

PHOTO_PROTOCOL = (
    "premium bridal studio finish, print-ready high-end wedding portrait, realistic skin texture, natural pores, "
    "sharp eyes, realistic hair strands, crisp couture fabric and embroidery details, "
    "commercial bridal retouching with clean but non-plastic semi-matte skin, controlled natural highlights, "
    "no oily shine on forehead, nose, cheeks, or chin, luxury studio-grade color grading, "
    "polished background separation, recognizable venue or studio-set detail, paid bridal-studio deliverable quality"
)

ANTI_AI_ARTIFACTS_PROTOCOL = (
    "Strictly avoid all AI-generated-image artifacts and tells: no overly symmetrical face, no generic AI face, "
    "no plastic skin, no waxy highlights, no beauty-filter texture, no CGI rendering, no 3D-render look, "
    "no game-engine lighting, no fantasy concept-art styling, no unnatural eye sharpness, no uncanny-valley "
    "expression, no dead eyes, no blank stare, no cross-eyed gaze, no mismatched eye-line, no forced waxy smile, "
    "no over-perfect symmetry in facial features, no identical couple faces, no AI-hallucinated background details, "
    "no over-sharpened fabric, no chromatic aberration on edges. "
    "The image must pass as a human-shot professional wedding photograph."
)

IDENTITY_LOCK_PROTOCOL = (
    "Identity lock is mandatory and highest priority: this is an image edit of the uploaded real person, not "
    "text-to-image character creation. Preserve the exact identity: preserve the same face shape, eye shape "
    "and spacing, brow structure, nose bridge and tip, mouth shape, jawline, chin, age impression, skin undertone, "
    "facial proportions, side-profile silhouette, nose-to-mouth distance, philtrum length, lip volume, cheek contour, "
    "and natural expression from the identity reference. Nose size is identity-critical: do not enlarge, sharpen, "
    "lengthen, westernize, stylize, or reshape the nose bridge, nose tip, nostrils, or side-profile projection. "
    "Makeup and hairstyle styling are allowed only if the person still reads as the same individual. Do not beautify "
    "the subject into a different person, do not replace the face with a generic model face, and do not reshape "
    "facial structure"
)

EDIT_SCOPE_PROTOCOL = (
    "Allowed edit scope: change wedding clothing, accessories, pose, body styling, background, set design, "
    "professional lighting, color grading, hairstyle styling, and bridal makeup. Keep the identity face anchored "
    "to the source references. If style, scene, clothing, or beauty conflicts with identity preservation, identity wins"
)

TEMPLATE_STYLE_LOCK_PROTOCOL = (
    "Selected-template style lock: the template wardrobe and scene are hard style anchors for generation and QA. "
    "Use the exact template clothing family, background family, lighting environment, and prompt-block style unless "
    "the user explicitly provides director-mode text or reference overrides. Active uploaded scene/outfit references "
    "outrank text for their domain; when no reference exists, User-written scene, outfit, or overall style direction "
    "is a higher-priority creative brief than template defaults. The template fills only unspecified details. "
    "Do not switch style families, do not turn an indoor studio template into an outdoor garden, balcony, "
    "terrace, travel, or landscape scene unless the user explicitly asked for that scene, and do not replace a "
    "requested bridal gown, groom suit, couple wardrobe, cultural attire, or royal embroidered styling with an "
    "unrelated outfit. Identity preservation may refine face and body realism, but it must not erase the selected "
    "template's clothing and background concept or the user's explicit text direction"
)

COUPLE_IDENTITY_LOCK_PROTOCOL = (
    "For couples, reference image 1 must remain subject A/bride and reference image 2 must remain subject B/groom; "
    "the output must contain exactly two primary wedding subjects in the same frame. Preserve each person's separate "
    "facial identity, age impression, face geometry, expression, and role. Never create a solo portrait, omit either "
    "subject, swap identities, merge faces, average faces, duplicate one subject, or make both subjects share the same AI face"
)

GOLDEN_ANNIVERSARY_LOCK_PROTOCOL = (
    "Golden anniversary mode is a parents-and-elders keepsake, not a newlywed fashion shoot. The output must "
    "contain exactly two mature or elderly primary subjects with dignified anniversary styling. Preserve each "
    "uploaded person's age impression, mature facial structure, skin texture, wrinkles, smile lines, under-eye "
    "texture, hairline, and life-stage cues; do not de-age, glamour-model, beauty-filter, or turn either person "
    "into a young bride or groom. If the uploaded portraits are already mature or elderly, visible age character "
    "must remain. If the uploaded portraits are less mature than the intended product, still keep the tone as a "
    "respectful milestone anniversary portrait rather than a youthful luxury wedding ad. Prioritize warm family "
    "memory, stable posture, modest formal wardrobe, authentic expression, and restoration-grade realism"
)

SINGLE_SUBJECT_LOCK_PROTOCOL = (
    "For single-subject orders, the output must contain exactly one primary human subject: the uploaded person only. "
    "Do not add a spouse, partner, groom, bride, bridesmaid, guest, duplicate body, second face, background person, "
    "or any extra human figure. The source identity must be transformed into a solo bridal or groom portrait, never "
    "a couple portrait"
)

INDOOR_STUDIO_LIGHTING_PROTOCOL = (
    "Indoor bridal-studio lighting: use a large soft key light at about 45 degrees from the subject, weak fill "
    "light on the shadow side, and a subtle rim or hair light for separation. Keep the face naturally exposed and "
    "slightly brighter than the scene, with the background about 0.3 to 0.8 stops darker than the face. Preserve "
    "soft shadow rolloff, visible catchlights, semi-matte skin texture, and detailed white dress highlights. "
    "Commercial ratio target: key light cleanly models the face, fill sits about 1 to 2 stops under the key, rim "
    "light separates hair/veil/shoulders without outlining them unnaturally, and the background stays controlled "
    "rather than flat or brighter than the face. Do not set key and fill to equal power unless a deliberately high-key "
    "commercial look is requested; preserve gentle facial dimension"
)

OUTDOOR_PRO_LIGHTING_PROTOCOL = (
    "Outdoor professional lighting protocol: outdoor scenes are allowed only as studio-grade on-location bridal "
    "photography. The sun may act only as rim light, hair light, or ambient background light, never as the harsh "
    "primary light on the face. Use frontal softbox-style fill or bounced fill so every face is correctly exposed "
    "with visible catchlights. Do not use harsh outdoor backlight as the primary light. Preserve sky detail, window "
    "detail, and white gown highlights; do not allow the face to fall into shadow, and never let the sky or dress "
    "blow out. Keep the face exposure about 0.3 to 0.7 stops brighter than the background while retaining readable "
    "garden, architecture, water, or skyline detail. Match the fill color temperature to the ambient scene so skin, "
    "dress, and background feel lit by one coherent photographer-controlled setup"
)

WINDOW_ARCHITECTURAL_LIGHTING_PROTOCOL = (
    "Window and architectural lighting: use window light as the directional key light, with the face turned toward "
    "the soft window source and gentle fill controlling shadow density. Darken the background slightly so the "
    "person separates from architecture, columns, curtains, arches, or walls. Preserve dimensional face modeling, "
    "natural skin tone, and refined editorial wedding color; do not flatten the face into even phone-photo light. "
    "Architectural detail must remain legible, not dissolved into a generic blurred backdrop"
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
    "clean but realistic retouching, couture-level fabric detail, readable premium venue detail, polished "
    "composition, and premium wedding album color grading. The finished image should read like a paid commercial "
    "bridal sample: dimensional face light, fill that opens shadows without flattening features, controlled background "
    "brightness, clean rim separation, and print-readable scene texture without phone portrait-mode smear"
)

INDOOR_SCENE_BOUNDARY_PROTOCOL = (
    "Indoor/studio template boundary: for indoor or studio templates, keep the scene indoors and do not add "
    "mountain vistas, open sky, outdoor balconies, terrace overlooks, exterior castle walls, beach backgrounds, "
    "forest backgrounds, or unrelated travel scenery. If a castle style is requested for an indoor studio template, "
    "render it as a controlled bridal-studio set with carved arches, painted architectural backdrop, polished floor, "
    "and studio lighting, not an outdoor travel balcony"
)

STUDIO_LIGHTING_GUARDRAILS = (
    f"{STUDIO_QUALITY_PROTOCOL}. {INDOOR_STUDIO_LIGHTING_PROTOCOL}. {OUTDOOR_PRO_LIGHTING_PROTOCOL}. "
    f"{WINDOW_ARCHITECTURAL_LIGHTING_PROTOCOL}. {NIGHT_LOW_LIGHTING_PROTOCOL}. {INDOOR_SCENE_BOUNDARY_PROTOCOL}. "
    "Hard studio-quality requirements: select one coherent lighting plan from the scene type and execute it "
    "clearly; do not use harsh outdoor backlight, bright window glare, or sun flare as the primary light; do not leave "
    "the face in shadow; do not blow out sky, windows, or dress highlights; use large softbox-style key light plus gentle fill light on every "
    "face; keep facial exposure natural and slightly brighter than the background without wet or greasy shine; "
    "use subtle rim or hair light to separate veil, hair, shoulders, and dark suit edges; keep background detail "
    "commercially readable but lower priority than the face, with real venue texture rather than smooth blur; "
    "avoid tourist-photo lighting, AI-glossy skin, oily skin, waxy specular highlights, fantasy-game styling, "
    "phone-flash lighting, direct on-camera flash, uncontrolled mixed color temperature, and cheap composited "
    "background"
)

FULL_LENGTH_COMPOSITION = (
    "full-length 3:4 vertical editorial composition, complete gown and dress train visible, "
    "no cropped hem, elegant headroom, subject not overfilled, enough breathing room around the body, "
    "main delivery must not be a headshot, bust portrait, waist-up portrait, half-body crop, square crop, "
    "horizontal banner, or a portrait dominated by empty sky/ceiling above the subject. "
    "luxury bridal studio posing with refined posture. For outdoor bridal portraits, keep the same full-gown "
    "delivery standard: no cropped shoes, gown hem, veil, or train, and use simple waist-level bouquet/veil/gown "
    "hand posing so difficult fingers are naturally covered"
)

HAND_POSE_SAFETY_PROTOCOL = (
    "Use simple professional bridal hand posing: relaxed hands, one bouquet or veil touch at waist level, "
    "fingers mostly covered by bouquet, sleeves, veil, or dress fabric when possible. Avoid interlaced fingers, "
    "spread fingers, complex hand gestures, hands close to the face, duplicated bouquets, and exposed tiny fingers. "
    "If hand anatomy is uncertain, simplify or partially hide the hands while preserving a natural paid-studio pose"
)

SINGLE_CANVAS_PROPORTION_PROTOCOL = (
    "Commercial single-subject framing: the bride or groom should occupy about 66-78% of the canvas height "
    "for full-length or near full-length portraits; outdoor environmental portraits may be slightly wider but "
    "the subject must never fall below 58% of canvas height. The face should remain large enough to read identity "
    "without turning the result into a close portrait, about 7.5-11% of canvas height. Keep headroom intentional, "
    "about 4-7.5% above the head, and keep 7-11% breathing room below the shoes, gown hem, or dress train. "
    "Leave visible side space so the dress, veil/train, floor, and scene can breathe; do not let the face, torso, "
    "bouquet, or upper dress dominate the frame. Eyes should sit near the upper third. "
    "Do not crop at joints, fingertips, ankles, knees, wrists, gown hem, veil, or train. "
    "A beautiful upper-body crop is still a failed main wedding deliverable when the full gown/hem/train are missing"
)

COUPLE_CANVAS_PROPORTION_PROTOCOL = (
    "Commercial couple framing: the couple group should occupy about 64-76% of the canvas height and about "
    "46-68% of the canvas width. Both faces must be clearly visible and large enough to read identity without "
    "crowding the portrait, about 6-10% of canvas height per face. Keep both subjects at believable scale, with readable separation between "
    "faces, shoulders, arms, outfits, and body silhouettes. Maintain intentional headroom and bottom room for "
    "shoes, suit hem, gown hem, veil, and dress train. Leave enough scene around the couple so the location reads "
    "as a premium wedding setting; do not let faces, torsos, bouquet, or upper bodies overfill the 3:4 frame. Avoid flat side-by-side tourist-photo blocking; use slight "
    "staggering, gentle interaction, and professional wedding pose direction"
)

DELIVERY_GATE_PROTOCOL = (
    "Delivery gate: a candidate is deliverable only if identity remains recognizable, face geometry is natural, "
    "commercial canvas proportion is correct, the face is large and sharp enough to read, the gown/suit/veil/train "
    "are complete, the crop avoids joints and hems, lighting looks professionally controlled, the premium background "
    "is recognizable and commercially readable instead of smeared, and the image reads as a paid bridal-studio wedding photograph. If identity, "
    "face readability, subject scale, crop, background clarity, or lighting conflicts with style, delivery quality wins"
)

CANDIDATE_SELECTION_PROTOCOL = (
    "Candidate generation protocol: when multiple candidates are requested, vary only pose nuance, subject placement, "
    "camera distance within the commercial framing range, lighting polish, and controlled background depth/detail. "
    "Do not vary identity, role order, face structure, person count, or the requested wedding concept. Each candidate "
    "must be independently deliverable and suitable for automated QA ranking"
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
    "the background must remain recognizable enough to support the wedding location, and the result must read as "
    "a paid bridal-studio portrait"
)

NEGATIVE_PROMPT = (
    "Identity failures: generic model face, different person, changed face shape, altered eyes, altered nose, "
    "enlarged nose, long nose, oversized nose, sharpened nose bridge, altered side-profile silhouette, altered "
    "mouth, altered jawline, identity drift, face replacement, face swap, over-beautified face, uncanny face, "
    "same AI face for both people, role swap; "
    "Subject-count failures: extra person, unexpected second person, second face, added partner, added spouse, "
    "couple in a single-subject order, duplicate body, duplicate subject, background person; "
    "Skin and realism failures: smooth skin, airbrushed, wax, plastic, makeup filter, oily skin, greasy shine, "
    "wet glossy skin, over-shiny forehead, over-shiny nose, over-shiny cheeks, 3d render, cgi, "
    "over-smoothed bridal ad; "
    "Eye and expression failures: dead eyes, blank stare, cross-eyed gaze, mismatched eye-line, unnatural gaze, "
    "mouth-only smile, eyes not smiling, cold fashion profile, detached side profile, uncanny smile, waxy smile, "
    "full side profile as primary face, one eye hidden by profile, detached runway stare, forced advertising grin, "
    "frozen expression, mannequin expression, doll-like expression, asymmetrical eyelids, painted eyes, "
    "over-sharpened eyes, emotionless face, disconnected couple expressions; "
    "Anatomy failures: headless, cropped head, phantom limbs, fused bodies, merged limbs, duplicate person, "
    "duplicated face, shared torso, merged shoulders, fused arms, conjoined bodies, bad hands, extra fingers; "
    "Lighting failures: bright flat lighting, harsh backlight, sun as primary face light, missing frontal fill, "
    "bright window glare as primary light, flare washing over faces, face in shadow, underexposed face, background "
    "brighter than face, no catchlights, blown-out sky, blown-out window, blown-out dress, crushed shadows, uncontrolled mixed light, mixed color temperature, direct on-camera "
    "flash, phone-flash lighting, muddy night lighting; "
    "Composition failures: subject too small, subject too large, overfilled subject, face too large, bouquet too dominant, "
    "cramped portrait, face too small, background dominates the subject, excessive headroom, awkward crop, cropped dress, "
    "dress cutoff, cut-off gown train, missing full outfit, flat centered pose, "
    "weak couple interaction, poor subject separation, over-blurred background, shallow fake bokeh, phone portrait-mode "
    "blur, background smeared into color blocks, melted bokeh background, unrecognizable venue, unreadable architecture, "
    "mushy flowers, erased floor lines, low-end snapshot, "
    "tourist snapshot, phone photo, outdoor travel snapshot; "
    "Scene failures: fantasy game costume, cheap composite, unrequested mountain vista, unrequested open sky, "
    "unrequested outdoor balcony, unrequested terrace overlook, unrequested beach, unrequested forest, unrelated travel background"
)


def _section(title: str, body: str | None) -> str:
    cleaned = str(body or "").strip().strip(".")
    if not cleaned:
        return ""
    return f"{title}: {cleaned}."


def get_studio_guardrails(*, is_couple: bool = False, template: object | None = None) -> str:
    parts = [
        _section("SKIN REALISM", SKIN_REALISM_PROTOCOL),
        _section("EYES AND EXPRESSION", EYE_EXPRESSION_PROTOCOL),
        _section("ANTI AI ARTIFACTS", ANTI_AI_ARTIFACTS_PROTOCOL),
        _section("STUDIO QUALITY", STUDIO_QUALITY_PROTOCOL),
        _section("BACKGROUND DETAIL", BACKGROUND_DETAIL_PROTOCOL),
        _section("SINGLE SUBJECT LOCK", SINGLE_SUBJECT_LOCK_PROTOCOL if not is_couple else None),
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
        if is_golden_anniversary_template(template):
            parts.append(_section("GOLDEN ANNIVERSARY AGE LOCK", GOLDEN_ANNIVERSARY_LOCK_PROTOCOL))
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
    parts.append(_section("TEMPLATE STYLE LOCK", TEMPLATE_STYLE_LOCK_PROTOCOL))
    has_user_text_override = bool(user_text or scene_text or clothing_text)
    if has_user_text_override:
        parts.append(_section("DIRECTOR SOURCE PRIORITY", SOURCE_PRIORITY_PROTOCOL))
    if is_couple:
        parts.append(_section("COUPLE IDENTITY LOCK", COUPLE_IDENTITY_LOCK_PROTOCOL))
        if is_golden_anniversary_template(template):
            parts.append(_section("GOLDEN ANNIVERSARY AGE LOCK", GOLDEN_ANNIVERSARY_LOCK_PROTOCOL))
    else:
        parts.append(_section("SINGLE SUBJECT LOCK", SINGLE_SUBJECT_LOCK_PROTOCOL))

    # Layer 2: Skin & photorealism (critical for commercial quality — early placement)
    parts.append(
        _section(
            "PHOTOGRAPHY LAYER",
            "control only lighting, skin texture, lens rendering, color science, depth separation, and studio polish while preserving the immutable identity layer",
        )
    )
    parts.append(_section("SKIN REALISM", SKIN_REALISM_PROTOCOL))
    parts.append(_section("EYES AND EXPRESSION", EYE_EXPRESSION_PROTOCOL))
    parts.append(_section("PHOTO REALISM", PHOTO_REALISM_PROTOCOL))
    parts.append(_section("BACKGROUND DETAIL", BACKGROUND_DETAIL_PROTOCOL))
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
