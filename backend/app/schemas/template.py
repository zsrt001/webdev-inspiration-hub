"""Template Pydantic schemas."""

from pydantic import BaseModel


class Template(BaseModel):
    """Schema for a wedding photo template."""

    id: str
    category: str
    title: str
    image_url: str
    style_family: str | None = None
    tags: list[str] = []
    clothing_prompt: str = ""
    default_background_prompt: str = ""
    is_custom: bool = False
    marketing_title: str | None = None
    marketing_subtitle: str | None = None
    recommended_for: str | None = None
    clothing_ref_image_url: str | None = None
    scene_ref_image_url: str | None = None
    prompt_blocks: dict | None = None


class TemplateListResponse(BaseModel):
    """Schema for template list response."""

    templates: list[Template]
