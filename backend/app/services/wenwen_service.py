"""Wenwen OpenAI-compatible image generation provider adapter."""

from __future__ import annotations

from app.services.provider_workflow import GenerationProviderWorkflow, settings


class WenwenService(GenerationProviderWorkflow):
    """Wenwen provider adapter bound to the shared image generation workflow."""

    PROVIDER = "wenwen"


wenwen_service = WenwenService()
