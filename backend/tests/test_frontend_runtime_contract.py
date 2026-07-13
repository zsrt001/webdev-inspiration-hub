from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class FrontendRuntimeContractTest(unittest.TestCase):
    def test_tabbar_locale_updates_do_not_call_tabbar_api_from_non_tab_pages(self) -> None:
        store_source = (ROOT / "frontend" / "src" / "stores" / "i18n.ts").read_text(
            encoding="utf-8"
        )
        app_source = (ROOT / "frontend" / "src" / "App.vue").read_text(
            encoding="utf-8"
        )

        self.assertIn("const TAB_BAR_ROUTES", store_source)
        self.assertIn("getCurrentPages()", store_source)
        self.assertIn("if (!isCurrentTabBarPage()) return", store_source)
        self.assertIn("await Promise.all", store_source)
        self.assertIn("console.warn('Failed to update localized tab bar'", store_source)
        self.assertIn("void i18nStore.applyTabBarLocale()", app_source)
        self.assertNotIn("AI Wedding Studio - Professional UI/UX Refactor", app_source)
        self.assertNotIn("console.log('App Show')", app_source)
        self.assertNotIn("console.log('App Hide')", app_source)


if __name__ == "__main__":
    unittest.main()
