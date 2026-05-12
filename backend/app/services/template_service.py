"""
Template Service - commercial wedding style library.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from app.schemas.template import Template

logger = logging.getLogger(__name__)


def _portrait_pair(
    *,
    style_family: str,
    single_id: str,
    single_title: str,
    single_image_url: str,
    single_tags: list[str],
    single_clothing_prompt: str,
    single_background_prompt: str,
    couple_id: str,
    couple_title: str,
    couple_image_url: str,
    couple_tags: list[str],
    couple_clothing_prompt: str,
    couple_background_prompt: str,
    single_prompt_blocks: dict | None = None,
    couple_prompt_blocks: dict | None = None,
) -> list[Template]:
    return [
        Template(
            id=single_id,
            category="single",
            title=single_title,
            image_url=single_image_url,
            style_family=style_family,
            tags=single_tags,
            clothing_prompt=single_clothing_prompt,
            default_background_prompt=single_background_prompt,
            prompt_blocks=single_prompt_blocks,
        ),
        Template(
            id=couple_id,
            category="couple",
            title=couple_title,
            image_url=couple_image_url,
            style_family=style_family,
            tags=couple_tags,
            clothing_prompt=couple_clothing_prompt,
            default_background_prompt=couple_background_prompt,
            prompt_blocks=couple_prompt_blocks,
        ),
    ]


def _build_templates() -> list[Template]:
    templates: list[Template] = []

    templates.extend(
        _portrait_pair(
            style_family="chn_xiuhe",
            single_id="solo_chn_xiuhe",
            single_title="Chinese Xiuhe",
            single_image_url="/style-previews/solo_chn_xiuhe.jpg",
            single_tags=["chinese", "xiuhe", "solo"],
            single_clothing_prompt="a person in traditional red xiuhe wedding attire, ornate gold headwear",
            single_background_prompt="ancient stone courtyard",
            couple_id="chn_xiuhe",
            couple_title="Chinese Xiuhe",
            couple_image_url="/style-previews/couple_chn_xiuhe.jpg",
            couple_tags=["chinese", "xiuhe", "couple"],
            couple_clothing_prompt="couple in traditional red xiuhe wedding attire, ornate gold headwear",
            couple_background_prompt="ancient stone courtyard",
        )
    )
    templates.extend(
        _portrait_pair(
            style_family="korean_minimal",
            single_id="solo_korean_minimal",
            single_title="Korean Minimal",
            single_image_url="/style-previews/solo_korean_minimal.jpg",
            single_tags=["korean", "minimal", "solo"],
            single_clothing_prompt="a person in minimalist white silk wedding gown, clean lines, elegant posture",
            single_background_prompt="white minimalist art gallery",
            couple_id="korean_minimal",
            couple_title="Korean Minimal",
            couple_image_url="/style-previews/couple_korean_minimal.jpg",
            couple_tags=["korean", "minimal", "couple"],
            couple_clothing_prompt="couple in minimalist white wedding dress and slim black suit",
            couple_background_prompt="white minimalist art gallery",
        )
    )
    templates.extend(
        _portrait_pair(
            style_family="royal_castle",
            single_id="solo_royal_castle",
            single_title="Royal Castle",
            single_image_url="/style-previews/solo_royal_castle.jpg",
            single_tags=["castle", "royal", "solo"],
            single_clothing_prompt="a person in regal embroidered wedding attire with couture volume",
            single_background_prompt="medieval stone balcony overlooking mountains",
            couple_id="royal_castle",
            couple_title="Royal Castle",
            couple_image_url="/style-previews/couple_royal_castle.jpg",
            couple_tags=["castle", "royal", "couple"],
            couple_clothing_prompt="couple in royal-style embroidered wedding attire with heirloom styling",
            couple_background_prompt="medieval stone balcony overlooking mountains",
            single_prompt_blocks={
                "composition": "full-length bridal portrait with complete couture gown and train visible, upright 3:4 framing, refined headroom, no overfilled crop",
                "lighting": "bridal studio lighting blended into castle ambience: large soft key light on the face, gentle fill light, subtle rim light, controlled sky highlights, no silhouette",
                "texture": "luxury embroidered fabric detail, natural skin texture, clean retouch without plastic smoothing",
                "style": "high-end bridal magazine portrait rather than outdoor snapshot",
            },
            couple_prompt_blocks={
                "composition": "full-length couple portrait with both outfits complete and readable, upright 3:4 framing, balanced spacing, no overfilled crop",
                "lighting": "bridal studio lighting blended into castle ambience: large soft key light on both faces, gentle fill light, subtle rim light, controlled sky highlights, no silhouette",
                "texture": "luxury embroidered fabric detail, natural skin texture, clean retouch without plastic smoothing",
                "style": "high-end bridal magazine portrait rather than outdoor snapshot",
            },
        )
    )
    templates.extend(
        _portrait_pair(
            style_family="old_money",
            single_id="solo_old_money",
            single_title="Old Money",
            single_image_url="/style-previews/solo_old_money.jpg",
            single_tags=["old_money", "classic", "solo"],
            single_clothing_prompt="a person in understated luxury wedding attire, silk and linen textures",
            single_background_prompt="european garden estate",
            couple_id="old_money",
            couple_title="Old Money",
            couple_image_url="/style-previews/couple_old_money.jpg",
            couple_tags=["old_money", "classic", "couple"],
            couple_clothing_prompt="couple in understated old-money wedding fashion, elegant and restrained",
            couple_background_prompt="european garden estate",
        )
    )
    templates.extend(
        _portrait_pair(
            style_family="gothic_romance",
            single_id="solo_gothic_romance",
            single_title="Gothic Romance",
            single_image_url="/style-previews/solo_gothic_romance_v2.png",
            single_tags=["gothic", "dramatic", "solo"],
            single_clothing_prompt="a person in black lace wedding gown, dramatic veil, gothic elegance",
            single_background_prompt="gothic cathedral with candlelight",
            couple_id="gothic_romance",
            couple_title="Gothic Romance",
            couple_image_url="/style-previews/couple_gothic_romance.jpg",
            couple_tags=["gothic", "dramatic", "couple"],
            couple_clothing_prompt="couple in black lace bridal gown and black tuxedo",
            couple_background_prompt="gothic cathedral with candlelight",
        )
    )
    templates.extend(
        _portrait_pair(
            style_family="beach_sunset",
            single_id="solo_beach_sunset",
            single_title="Beach Sunset",
            single_image_url="/style-previews/solo_beach_sunset.jpg",
            single_tags=["beach", "sunset", "solo"],
            single_clothing_prompt="a person in boho wedding dress, natural smile, wind-swept veil",
            single_background_prompt="sand beach at golden hour",
            couple_id="beach_sunset",
            couple_title="Beach Sunset",
            couple_image_url="/style-previews/couple_beach_sunset.jpg",
            couple_tags=["beach", "sunset", "couple"],
            couple_clothing_prompt="couple in boho beach wedding attire with flowing veil",
            couple_background_prompt="sand beach at golden hour",
        )
    )
    templates.extend(
        _portrait_pair(
            style_family="hk_retro",
            single_id="solo_hk_retro",
            single_title="Hong Kong Retro",
            single_image_url="/style-previews/hk_retro.jpg",
            single_tags=["hong_kong", "retro", "solo"],
            single_clothing_prompt="a person in vintage 90s hong kong wedding styling with cinematic tailoring",
            single_background_prompt="neon street signs after rain",
            couple_id="hk_retro",
            couple_title="Hong Kong Retro",
            couple_image_url="/style-previews/couple_hk_retro_v2.png",
            couple_tags=["hong_kong", "retro", "couple"],
            couple_clothing_prompt="couple in vintage 90s hong kong wedding styling",
            couple_background_prompt="neon street signs after rain",
        )
    )
    templates.extend(
        _portrait_pair(
            style_family="twilight_forest",
            single_id="solo_twilight_forest",
            single_title="Twilight Forest",
            single_image_url="/style-previews/twilight_forest.jpg",
            single_tags=["forest", "dreamy", "solo"],
            single_clothing_prompt="a person in fairy-style wedding outfit with soft romantic movement",
            single_background_prompt="misty pine forest at dusk",
            couple_id="twilight_forest",
            couple_title="Twilight Forest",
            couple_image_url="/style-previews/couple_twilight_forest.jpg",
            couple_tags=["forest", "dreamy", "couple"],
            couple_clothing_prompt="couple in fairy-style wedding outfits with natural romantic styling",
            couple_background_prompt="misty pine forest at dusk",
        )
    )
    templates.extend(
        _portrait_pair(
            style_family="japanese_shiromuku",
            single_id="solo_japanese_shiromuku",
            single_title="Japanese Shiromuku",
            single_image_url="/style-previews/japanese_shiromuku.jpg",
            single_tags=["japanese", "shiromuku", "solo"],
            single_clothing_prompt="a person in white shiromuku kimono with ceremonial elegance",
            single_background_prompt="zen garden with maple trees",
            couple_id="japanese_shiromuku",
            couple_title="Japanese Shiromuku",
            couple_image_url="/style-previews/couple_japanese_shiromuku.jpg",
            couple_tags=["japanese", "shiromuku", "couple"],
            couple_clothing_prompt="couple in white shiromuku kimono and black hakama",
            couple_background_prompt="zen garden with maple trees",
        )
    )
    templates.extend(
        _portrait_pair(
            style_family="cyberpunk_city",
            single_id="solo_cyberpunk_city",
            single_title="Cyberpunk City",
            single_image_url="/style-previews/cyberpunk_city.jpg",
            single_tags=["cyberpunk", "city", "solo"],
            single_clothing_prompt="a person in futuristic wedding couture with reflective fabrics and neon accents",
            single_background_prompt="neon-lit cyberpunk city street",
            couple_id="cyberpunk_city",
            couple_title="Cyberpunk City",
            couple_image_url="/style-previews/couple_cyberpunk_city_v2.png",
            couple_tags=["cyberpunk", "city", "couple"],
            couple_clothing_prompt="couple in futuristic wedding attire with neon reflections",
            couple_background_prompt="neon-lit cyberpunk city street",
        )
    )
    templates.extend(
        _portrait_pair(
            style_family="school_days",
            single_id="solo_school_days",
            single_title="School Days",
            single_image_url="/style-previews/school_days.jpg",
            single_tags=["youth", "campus", "solo"],
            single_clothing_prompt="a person in school-inspired pre-wedding styling, warm and nostalgic",
            single_background_prompt="sun-drenched classroom with nostalgic mood",
            couple_id="school_days",
            couple_title="School Days",
            couple_image_url="/style-previews/couple_school_days.jpg",
            couple_tags=["youth", "campus", "couple"],
            couple_clothing_prompt="couple in school-inspired pre-wedding styling, natural and warm",
            couple_background_prompt="sun-drenched classroom with nostalgic mood",
        )
    )
    templates.extend(
        _portrait_pair(
            style_family="classic_bw",
            single_id="solo_classic_bw",
            single_title="Classic B&W",
            single_image_url="/style-previews/classic_bw.jpg",
            single_tags=["black_white", "timeless", "solo"],
            single_clothing_prompt="a person in classic editorial wedding styling, monochrome film mood",
            single_background_prompt="minimalist dark studio with black-and-white film grain treatment",
            couple_id="classic_bw",
            couple_title="Classic B&W",
            couple_image_url="/style-previews/couple_classic_bw.jpg",
            couple_tags=["black_white", "timeless", "couple"],
            couple_clothing_prompt="couple in classic tuxedo and elegant bridal gown, monochrome editorial style",
            couple_background_prompt="minimalist dark studio with black-and-white film grain treatment",
        )
    )

    templates.extend(
        [
            Template(
                id="golden_vintage_studio_8090",
                category="vintage",
                title="Golden Anniversary - 80s/90s Studio",
                image_url="/style-previews/golden_vintage_studio_8090.jpg",
                style_family="golden_vintage_studio_8090",
                tags=["golden_anniversary", "vintage", "studio"],
                marketing_title="Recreate a classic wedding portrait for parents",
                marketing_subtitle="80s/90s studio mood with modern restoration quality",
                recommended_for="parents_and_elders",
                clothing_prompt="elderly couple in classic 80s/90s formal wedding attire, modest and dignified",
                default_background_prompt="classic 80s/90s studio painted backdrop, soft warm tones",
                prompt_blocks={
                    "composition": "stable framing, respectful pose, authentic expression",
                    "lighting": "warm key light, soft fill, subtle rim light, realistic shadows",
                    "texture": "authentic elderly skin texture, subtle film grain, no over-smoothing",
                    "style": "vintage studio photography with restoration-grade clarity",
                },
            ),
            Template(
                id="golden_chinese_courtyard",
                category="vintage",
                title="Golden Anniversary - Chinese Courtyard",
                image_url="/style-previews/golden_chinese_courtyard.jpg",
                style_family="golden_chinese_courtyard",
                tags=["golden_anniversary", "chinese", "courtyard"],
                marketing_title="Traditional courtyard keepsake for elders",
                marketing_subtitle="Warm red-gold palette with realistic skin detail",
                recommended_for="parents_and_elders",
                clothing_prompt="elderly couple in traditional chinese wedding attire with red and gold accents",
                default_background_prompt="vintage chinese courtyard with warm lantern lighting",
                prompt_blocks={
                    "composition": "balanced spacing, respectful posture, stable portrait structure",
                    "lighting": "warm ambient light, soft shadows, realistic skin tone",
                    "texture": "natural wrinkles preserved, subtle grain, no plastic skin",
                    "style": "traditional chinese vintage wedding portrait with restrained saturation",
                },
            ),
            Template(
                id="golden_modern_remake",
                category="vintage",
                title="Golden Anniversary - Modern Remake",
                image_url="/style-previews/golden_modern_remake.jpg",
                style_family="golden_modern_remake",
                tags=["golden_anniversary", "modern", "minimal"],
                marketing_title="Modern remake for milestone memories",
                marketing_subtitle="Clean composition and premium soft lighting",
                recommended_for="parents_and_elders",
                clothing_prompt="elderly couple in elegant modern wedding attire, clean lines and minimal accessories",
                default_background_prompt="minimal indoor studio, soft window light, neutral decor",
                prompt_blocks={
                    "composition": "clean negative space, magazine-like framing, balanced portrait proportions",
                    "lighting": "soft natural light, realistic highlights, controlled contrast",
                    "texture": "authentic skin texture and wrinkles, subtle grain, no beauty-filter look",
                    "style": "modern editorial wedding style with premium realism",
                },
            ),
            Template(
                id="custom",
                category="custom",
                title="Custom Mode (Bespoke)",
                image_url="/style-previews/custom_mode.jpg",
                style_family="custom_mode",
                tags=["custom", "bespoke"],
                clothing_prompt="person or couple in high fashion wedding attire, editorial styling",
                default_background_prompt="luxurious scene tailored to user prompt",
                is_custom=True,
            ),
        ]
    )

    return templates


def _deduplicate_templates(templates: list[Template]) -> list[Template]:
    unique: list[Template] = []
    seen_ids: set[str] = set()

    for item in templates:
        item_id = item.id.strip()
        if item_id in seen_ids:
            logger.warning("template_duplicate_skipped: duplicate id=%s", item.id)
            continue
        unique.append(item)
        seen_ids.add(item_id)

    return unique


_templates: List[Template] = _deduplicate_templates(_build_templates())


def get_all_templates() -> List[Template]:
    """Return the full list of available styles."""
    return list(_templates)


def get_template_by_id(template_id: str) -> Optional[Template]:
    """Find a specific template by ID."""
    return next((template for template in _templates if template.id == template_id), None)
