import { expect, test, type Page } from '@playwright/test';


const CAPABILITIES_OFF = {
  google_auth: false,
  authenticated_upload: false,
  generation: false,
  credit_pack_checkout: false,
  subscription_billing: false,
  private_download: false,
  partner_invite: false,
};

async function mockPublicConfig(
  page: Page,
  support: { available: boolean; email: string; url: string },
) {
  await page.addInitScript(() => window.localStorage.setItem('aws_locale', 'en'));
  await page.route('**/api/v1/ops/public_config*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        support,
        capabilities: CAPABILITIES_OFF,
      }),
    });
  });
}

test('refund page hides a support CTA and shows recovery steps without a verified channel', async ({ page }) => {
  await mockPublicConfig(page, { available: false, email: '', url: '' });

  await page.goto('/pages/legal/refund');

  await expect(page.getByRole('heading', { name: 'Refunds & Support' })).toBeVisible();
  await expect(page.locator('a.support-link')).toHaveCount(0);
  await expect(page.getByText('No verified, monitored support channel is currently available.')).toBeVisible();
  await expect(page.getByText(/Keep the order ID, provider receipt, failure timestamp/)).toBeVisible();
});

test('legal pages render only the runtime-confirmed HTTPS support URL', async ({ page }) => {
  await mockPublicConfig(page, {
    available: true,
    email: '',
    url: 'https://support.example.com/tickets?source=vowpic',
  });

  await page.goto('/pages/legal/privacy');

  const supportLink = page.locator('a.support-link');
  await expect(supportLink).toHaveCount(1);
  await expect(supportLink).toHaveAttribute(
    'href',
    'https://support.example.com/tickets?source=vowpic',
  );
  await expect(supportLink).toHaveAttribute('rel', 'noopener noreferrer');
});
