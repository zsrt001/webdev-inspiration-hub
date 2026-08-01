from __future__ import annotations

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/supabase-management-token-promotion.yml"


class SupabaseManagementTokenPromotionWorkflowTest(unittest.TestCase):
    def test_existing_preview_token_is_verified_and_promoted_without_value_output(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        payload = yaml.load(source, Loader=yaml.BaseLoader)
        job = payload["jobs"]["promote"]
        run = "\n".join(step.get("run") or "" for step in job["steps"])

        self.assertEqual(job["environment"], "preview-commercial")
        self.assertIn("SUPABASE_MANAGEMENT_TOKEN", run)
        self.assertIn("/config/auth", run)
        self.assertIn('test "$GITHUB_SHA" = "$REQUIRED_MAIN_SHA"', run)
        self.assertIn("gh secret set SUPABASE_AUTH_CONFIG_TOKEN", run)
        self.assertIn("--env production", run)
        self.assertIn(
            "gh secret delete ONE_TIME_SUPABASE_SECRET_PUBLISH_TOKEN",
            run,
        )
        self.assertIn("--env preview-commercial", run)
        self.assertNotIn("echo $SUPABASE_MANAGEMENT_TOKEN", run)
        self.assertNotIn("--body", run)


if __name__ == "__main__":
    unittest.main()
