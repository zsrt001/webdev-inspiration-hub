import { expect, test } from '@playwright/test';

test('@a11y anonymous public navigation does not probe session endpoints', async ({ page }) => {
  const sessionRequests: string[] = [];

  await page.route('**/api/v1/ops/public_config*', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      placements: {},
      support: { available: false },
      capabilities: {
        google_auth: true,
        authenticated_upload: false,
        generation: false,
        credit_pack_checkout: false,
        subscription_billing: false,
        private_download: false,
        partner_invite: false,
      },
    }),
  }));
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname === '/api/v1/auth/me' || pathname === '/api/v1/auth/refresh') {
      sessionRequests.push(pathname);
    }
  });

  await page.goto('/', { waitUntil: 'networkidle' });

  await expect(page.locator('[role="navigation"]')).toHaveCount(1);
  expect(sessionRequests).toEqual([]);
});
