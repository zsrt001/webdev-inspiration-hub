"""Template API routes with modular prompting (Clothing + Background split)."""

from fastapi import APIRouter, HTTPException
from typing import Optional

router = APIRouter()

from app.services.ops_config_service import apply_template_overrides, get_template_override
from app.services.template_service import get_all_templates, get_template_by_id
from app.schemas.template import Template, TemplateListResponse


def construct_final_prompt(template: Template, user_custom_scene: Optional[str] = None) -> str:
    """
    Combines clothing prompt with either user's custom scene OR default background.
    
    Args:
        template: The selected template with clothing and default background
        user_custom_scene: Optional custom scene description from user
        
    Returns:
        Complete prompt for AI generation
    """
    # Part A: The person and their outfit (Locked by Template)
    clothing_part = template.clothing_prompt
    
    # Part B: Scene - Check if user provided custom input
    if user_custom_scene and len(user_custom_scene.strip()) > 0:
        # User wants a custom background
        background_part = f"located in {user_custom_scene.strip()}"
    else:
        # Use template's default background
        background_part = template.default_background_prompt
    
    # Assemble final prompt
    final_prompt = f"A professional wedding portrait of {clothing_part}, {background_part}. 8k resolution, photorealistic, professional photography."
    
    return final_prompt


@router.get("", response_model=TemplateListResponse)
@router.get("/", response_model=TemplateListResponse)
@router.get("/list", response_model=TemplateListResponse, include_in_schema=False)
async def list_templates() -> TemplateListResponse:
    """Get list of available wedding photo templates."""
    return TemplateListResponse(templates=apply_template_overrides(get_all_templates()))


@router.get("/{template_id}", response_model=Template)
async def get_template(template_id: str) -> Template:
    """Get a single template by ID."""
    template = get_template_by_id(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    override = get_template_override(template_id)
    if bool(override.get("hidden")):
        raise HTTPException(status_code=404, detail="Template not found")
    updates = {
        key: value
        for key, value in override.items()
        if key in {
            "title",
            "image_url",
            "marketing_title",
            "marketing_subtitle",
            "recommended_for",
            "clothing_ref_image_url",
            "scene_ref_image_url",
            "default_background_prompt",
            "clothing_prompt",
        }
        and value is not None
    }
    if isinstance(override.get("tags"), list):
        updates["tags"] = [str(item).strip() for item in override["tags"] if str(item).strip()]
    return template.model_copy(update=updates)
