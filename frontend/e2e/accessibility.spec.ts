import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const publicRoutes = [
  '/',
  '/pages/auth/login',
  '/pages/auth/register',
  '/pages/legal/privacy',
  '/pages/legal/refund',
  '/pages/legal/terms',
] as const;

const routesWithPrimaryNavigation = new Set([
  '/',
  '/pages/legal/privacy',
  '/pages/legal/refund',
  '/pages/legal/terms',
]);

for (const route of publicRoutes) {
  test(`@a11y ${route} has no serious or critical accessibility violations`, async ({ page }) => {
    await page.goto(route, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toBeVisible();
    await expect(page.locator('[role="main"]')).toHaveCount(1);
    await expect(page.locator('[role="heading"][aria-level="1"]')).toHaveCount(1);
    if (routesWithPrimaryNavigation.has(route)) {
      await expect(page.locator('[role="navigation"]')).toHaveCount(1);
    }
    const report = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    const blocking = report.violations.filter(
      (violation) => violation.impact === 'serious' || violation.impact === 'critical',
    );
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
  });
}
