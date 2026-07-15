"""Exact single-Provider image-generation runtime facade."""

from __future__ import annotations

from app.core.config import get_settings
from app.services.evolink_service import evolink_service


settings = get_settings()


class GenerationService:
    provider_name = "evolink"

    @staticmethod
    def _provider():
        if settings.generation_engine != "evolink":
            raise RuntimeError("generation_engine_must_be_exact_evolink")
        return evolink_service

    def validate_runtime_requirements(self, *, force: bool = False) -> None:
        self._provider().validate_runtime_requirements(force=force)

    async def ping_runtime(self) -> tuple[bool, str]:
        return await self._provider().ping_runtime()

    async def probe_queue_capability(self) -> tuple[bool, str]:
        return True, "durable_generation_job_v1"


generation_service = GenerationService()
