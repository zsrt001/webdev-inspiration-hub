"""Generation provider facade."""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.services.comfyui_service import comfyui_service
from app.services.wenwen_service import wenwen_service

settings = get_settings()


class GenerationService:
    """Dispatch generation work to the configured provider."""

    @staticmethod
    def _provider():
        if settings.using_wenwen_generation:
            return wenwen_service
        return comfyui_service

    def validate_runtime_requirements(self, *, force: bool = False) -> None:
        self._provider().validate_runtime_requirements(force=force)

    async def ping_runtime(self) -> tuple[bool, str]:
        return await self._provider().ping_runtime()

    async def probe_queue_capability(self) -> tuple[bool, str]:
        provider = self._provider()
        probe = getattr(provider, "probe_queue_capability", None)
        if callable(probe):
            return await probe()
        return True, "not_supported"

    async def generate_photo(self, **kwargs: Any) -> None:
        await self._provider().generate_photo(**kwargs)

    def supports_live_portrait(self) -> bool:
        return not settings.using_wenwen_generation

    async def generate_live_portrait(self, **kwargs: Any) -> None:
        if not self.supports_live_portrait():
            raise NotImplementedError("live_portrait_not_supported_for_current_provider")
        await comfyui_service.generate_live_portrait(**kwargs)


generation_service = GenerationService()
