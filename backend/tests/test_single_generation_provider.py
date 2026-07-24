"""Production image generation has exactly one Provider and no hidden fallback."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class SingleGenerationProviderTest(unittest.TestCase):
    def test_only_evolink_image_generation_runtime_remains(self) -> None:
        from app.services.generation_service import GenerationService

        self.assertEqual(GenerationService().provider_name, "evolink")
        source = "\n".join(
            (BACKEND_DIR / relative).read_text(encoding="utf-8")
            for relative in (
                "app/services/generation_service.py",
                "app/services/evolink_service.py",
                "app/services/generation_executor_service.py",
                "app/core/runtime_checks.py",
                "scripts/preflight_production.py",
            )
        ).lower()
        for forbidden in (
            "comfyui_service",
            "wenwen_service",
            "generation_engine=wenwen",
            "generation_engine=comfyui",
            "image_fallback_models",
            "comfyui_base_url",
            "comfy_cloud_base_url",
            'generation_engine == "wenwen"',
        ):
            self.assertNotIn(forbidden, source)

    def test_dead_image_provider_modules_and_workflows_are_deleted(self) -> None:
        forbidden = (
            "generate_assets.py",
            "app/services/comfyui_service.py",
            "app/services/wenwen_service.py",
            "app/workflows/comfyui_base.zip",
            "app/workflows/comfyui_base.json",
            "app/workflows/comfyui_couple_inpaint.json",
            "app/workflows/comfyui_cloud_base_minimal.json",
            "app/workflows/comfyui_cloud_couple_minimal.json",
            "app/workflows/comfyui_live_portrait.json",
            "scripts/validate_comfyui_workflows.py",
            "scripts/generate_all_assets.py",
            "scripts/launch_studio.py",
            "scripts/regenerate_covers.py",
            "scripts/regenerate_covers_v2.py",
            "scripts/regenerate_hero_v2.py",
            "scripts/regenerate_one.py",
            "scripts/regenerate_production_v3.py",
            "scripts/regenerate_realism_covers.py",
        )
        self.assertEqual([path for path in forbidden if (BACKEND_DIR / path).exists()], [])

    def test_generation_engine_rejects_every_non_evolink_value(self) -> None:
        from app.core.config import Settings

        for value in ("wenwen", "comfyui", "auto", "", "EVOLINK"):
            with self.subTest(value=value):
                settings = Settings(_env_file=None, generation_engine=value)
                self.assertFalse(settings.using_evolink_generation)

    def test_production_preflight_summarizes_only_live_generation_runtime(self) -> None:
        from app.core.config import Settings

        script = BACKEND_DIR / "scripts/preflight_production.py"
        spec = importlib.util.spec_from_file_location("vowpic_preflight", script)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        summary = module._build_env_summary(Settings(_env_file=None))
        names = {item["name"] for group in summary.values() for item in group}
        self.assertIn("EVOLINK_API_BASE_URL", names)
        self.assertIn("WENWEN_CHAT_API_KEY", names)
        self.assertIn("WENWEN_VISION_API_KEY", names)
        self.assertFalse(any("COMFY" in name for name in names))


if __name__ == "__main__":
    unittest.main()
