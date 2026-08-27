import { expect, test, type Page } from '@playwright/test';

const localRun = process.env.RUN_LOCAL_A11Y === '1';

test.skip(!localRun, 'commercial home checks run against the local production build');

async function mockPublicApis(page: Page) {
  await page.route('**/api/v1/ops/public_config', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        placements: {
          home_banner: {
            enabled: true,
            title: 'VowPic Studio',
            subtitle: 'Wedding portraits from your photos',
            cta_label: 'Start Now',
            secondary_cta_label: 'Browse Collection',
            image_url: '/style-previews/couple_old_money.jpg',
          },
        },
        support: { available: false, email: '', url: '' },
        auth: {
          google_oauth_enabled: true,
          supabase_url: 'https://example.supabase.co',
          supabase_publishable_key: 'sb_publishable_local_test',
        },
        capabilities: {
          google_auth: true,
          authenticated_upload: true,
          generation: true,
          credit_pack_checkout: true,
          subscription_billing: true,
          private_download: true,
          partner_invite: true,
        },
      }),
    });
  });
  await page.route('**/api/v1/templates', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        templates: [
          {
            id: 'royal_castle',
            category: 'single',
            title: 'Royal Castle',
            image_url: '/style-previews/royal_castle.jpg',
            style_family: 'royal_castle',
            stability: 'stable',
          },
          {
            id: 'couple_old_money',
            category: 'couple',
            title: 'Old Money',
            image_url: '/style-previews/couple_old_money.jpg',
            style_family: 'old_money',
            stability: 'stable',
          },
          {
            id: 'golden_vintage_studio_8090',
            category: 'vintage',
            title: 'Golden Anniversary',
            image_url: '/style-previews/golden_vintage_studio_8090.jpg',
            style_family: 'golden_vintage_studio_8090',
            stability: 'stable',
          },
        ],
      }),
    });
  });
  await page.route('**/api/v1/credits/packages**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        packages: [
          { id: 'pack_50', product_kind: 'credit_pack', credits: 50, price: 12.9, price_cents: 1290, currency: 'USD', display_price: '$12.90', label: 'Starter credits', popular: false },
          { id: 'pack_120', product_kind: 'credit_pack', credits: 120, price: 24.9, price_cents: 2490, currency: 'USD', display_price: '$24.90', label: 'Popular credits', popular: true },
          { id: 'pack_300', product_kind: 'credit_pack', credits: 300, price: 49.9, price_cents: 4990, currency: 'USD', display_price: '$49.90', label: 'Premium credits', popular: false },
          { id: 'creator_monthly', product_kind: 'subscription', credits: 300, price: 49, price_cents: 4900, currency: 'USD', display_price: '$49.00', label: 'creator_monthly', popular: false },
        ],
      }),
    });
  });
  await page.route('**/api/v1/subscriptions/plans', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { code: 'starter_monthly', pre_tax_minor_units: 1900, currency: 'USD', credits: 80, retention_tier: 'subscription_180d', display_price: '$19.00' },
        { code: 'creator_monthly', pre_tax_minor_units: 4900, currency: 'USD', credits: 300, retention_tier: 'subscription_180d', display_price: '$49.00' },
        { code: 'studio_monthly', pre_tax_minor_units: 12900, currency: 'USD', credits: 900, retention_tier: 'studio_365d', display_price: '$129.00' },
      ]),
    });
  });
  await page.route('**/api/v1/legal/policies', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pricing: { single: 2, couple_local: 3, golden_anniversary: 3 },
        retention: {
          source_images_days: 7,
          free_generated_days: 30,
          paid_generated_days: 90,
          subscription_generated_days: 180,
          studio_generated_days: 365,
        },
      }),
    });
  });
  await page.route('**/api/v1/credits/balance', async (route) => {
    await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
  });
  await page.route('**/api/v1/subscriptions/me', async (route) => {
    await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
  });
}

test('desktop home has the required commercial structure and clean billing copy', async ({ page }) => {
  await mockPublicApis(page);
  await page.goto('/', { waitUntil: 'networkidle' });

  const sectionIds = await page.locator('[role="main"] > section').evaluateAll((sections) =>
    sections.map((section) => section.id),
  );
  expect(sectionIds).toEqual([
    'gallery',
    'steps',
    'testimonials',
    'pricing',
    'privacy',
    'faq',
    'cta',
  ]);
  await expect(page.getByText('This deployment is browse-only')).toHaveCount(0);
  await expect(page.getByText('Studio temporarily unavailable')).toHaveCount(0);

  await page.getByRole('button', { name: 'Compare All Plans' }).click();
  await expect(page.locator('.pricing-card')).toHaveCount(3);
  await expect(page.getByText('creator_monthly')).toHaveCount(0);

  await page.getByRole('tab', { name: 'Subscriptions' }).click();
  await expect(page.getByText('Creator Monthly', { exact: true })).toBeVisible();
  await expect(page.getByText('subscription_180d')).toHaveCount(0);

  await page.getByRole('tab', { name: 'Credit packs' }).click();
  await expect(page.getByText('Buy Popular Credits', { exact: true })).toBeVisible();
  const consent = page.getByRole('checkbox', { name: 'Agree to the Privacy Policy and Terms of Service' });
  await consent.click();
  await expect(consent).toHaveAttribute('aria-checked', 'true');
});

test('390px mobile home has no horizontal overflow and keeps primary actions usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockPublicApis(page);
  await page.goto('/', { waitUntil: 'networkidle' });

  await expect(page.getByRole('link', { name: 'Start a Free Preview' })).toBeVisible();
  await expect(page.locator('#pricing')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  const buttons = page.getByRole('button', { name: 'Compare All Plans' });
  await buttons.focus();
  await expect(buttons).toBeFocused();
});
