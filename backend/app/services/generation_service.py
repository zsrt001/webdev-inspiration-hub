"""Exact single-Provider image-generation runtime facade."""

from __future__ import annotations

from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import async_session_maker
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

    async def probe_backend_capability(self) -> tuple[bool, str]:
        if not settings.using_backend_generation_execution:
            raise RuntimeError("generation_backend_execution_disabled")
        if settings.runtime_coordinate_errors:
            raise RuntimeError("generation_backend_runtime_coordinates_invalid")
        if not settings.backend_executor_digest:
            raise RuntimeError("generation_backend_executor_digest_invalid")

        from app.services import generation_executor_service as executor

        executor.backend_runtime_coordinates()
        required_entrypoints = (
            executor.execute_generation_job,
            executor.reconcile_generation_job,
            executor.run_backend_generation_maintenance,
        )
        if not all(callable(entrypoint) for entrypoint in required_entrypoints):
            raise RuntimeError("generation_backend_entrypoint_unavailable")

        async with async_session_maker() as db:
            await db.execute(text("SELECT id FROM public.generation_jobs LIMIT 0"))
            await db.execute(text("SELECT id FROM public.generation_attempts LIMIT 0"))

        return True, "website_backend_generation_contract_v1"


generation_service = GenerationService()
