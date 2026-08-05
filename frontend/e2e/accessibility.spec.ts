import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const publicRoutes = [
  '/',
  '/pages/detail/detail?id=royal_castle',
  '/pages/orders/orders',
  '/pages/account/index',
  '/pages/create/index',
  '/pages/auth/login',
  '/pages/auth/register',
  '/pages/legal/privacy',
  '/pages/legal/refund',
  '/pages/legal/terms',
  '/admin',
] as const;

const routesWithPrimaryNavigation = new Set([
  '/',
  '/pages/detail/detail?id=royal_castle',
  '/pages/orders/orders',
  '/pages/account/index',
  '/pages/create/index',
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

test('@a11y public website controls are keyboard reachable', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' });

  const primaryNavigation = page.getByRole('navigation', { name: /Primary navigation|主导航/ });
  await expect(primaryNavigation.getByRole('link', { name: 'VowPic' })).toBeVisible();
  await expect(primaryNavigation.getByRole('link', { name: /Home|首页/ })).toBeVisible();
  await expect(primaryNavigation.getByRole('link', { name: /Orders|订单/ })).toBeVisible();
  await expect(primaryNavigation.getByRole('link', { name: /Account|账户/ })).toBeVisible();
  await expect(primaryNavigation.getByRole('button', { name: /Switch to English|切换到中文/ })).toBeVisible();

  const exploreStyles = page.getByRole('link', { name: /Explore Styles|浏览婚纱风格/ });
  await exploreStyles.focus();
  await expect(exploreStyles).toBeFocused();
  await page.keyboard.press('Enter');
  await expect.poll(async () => page.locator('#gallery').evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return rect.top < window.innerHeight && rect.bottom > 0;
  })).toBe(true);

  const legacyFilter = page.getByRole('button', { name: /Legacy|金婚纪念/ });
  await legacyFilter.focus();
  await expect(legacyFilter).toBeFocused();
  await page.keyboard.press('Space');
  await expect(legacyFilter).toHaveAttribute('aria-pressed', 'true');

  const styleLinks = page.locator('#gallery').getByRole('link', { name: /View Details|查看详情/ });
  await expect(styleLinks.first()).toBeVisible();
  await styleLinks.first().focus();
  await expect(styleLinks.first()).toBeFocused();
  await expect(styleLinks.first()).toHaveAttribute('href', /\/pages\/detail\/detail\?id=/);

  const privacyLinks = page.getByRole('link', { name: /Privacy Policy|Privacy|隐私政策/ });
  await expect(privacyLinks.first()).toBeVisible();
  await privacyLinks.first().focus();
  await expect(privacyLinks.first()).toBeFocused();
});

test('@a11y auth navigation remains keyboard reachable while Google auth is unavailable', async ({ page }) => {
  await page.goto('/pages/auth/login', { waitUntil: 'networkidle' });
  await expect(page.getByRole('link', { name: /VowPic/ })).toBeVisible();
  const loginHomeLink = page.getByRole('link', { name: /Back to home|返回首页/ });
  await loginHomeLink.focus();
  await expect(loginHomeLink).toBeFocused();

  await page.goto('/pages/auth/register', { waitUntil: 'networkidle' });
  const accountLink = page.getByRole('link', { name: /Open account|打开账户/ });
  await accountLink.focus();
  await expect(accountLink).toBeFocused();
  const registerHomeLink = page.getByRole('link', { name: /Back to home|返回首页/ });
  await registerHomeLink.focus();
  await expect(registerHomeLink).toBeFocused();
});
