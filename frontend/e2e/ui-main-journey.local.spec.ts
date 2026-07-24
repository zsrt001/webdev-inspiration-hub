import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

import { runUiMainJourney } from './helpers/ui-main-journey';

test.skip(
  process.env.RUN_LOCAL_UI !== '1',
  'local UI contract journey is opt-in',
);

const orderId = '10000000-0000-4000-8000-000000000001';
const userId = '10000000-0000-4000-8000-000000000002';
const assetId = '10000000-0000-4000-8000-000000000003';
const finalAssetId = '10000000-0000-4000-8000-000000000004';

function order(status: 'QUEUED' | 'READY') {
  return {
    id: orderId,
    user_id: userId,
    status,
    template_id: 'solo_korean_minimal',
    assets: status === 'READY' ? [{
      id: finalAssetId,
      role: 'final_master',
      status: 'ACTIVE',
      width: 1200,
      height: 1600,
      download_path: `/api/v1/orders/${orderId}/assets/${finalAssetId}/download`,
    }] : [],
    can_download: status === 'READY',
    entitlement_status: status === 'READY' ? 'ACTIVE' : null,
    access_tier: status === 'READY' ? 'PAID' : null,
    settlement_status: status === 'READY' ? 'CAPTURED' : 'NOT_CHARGED',
    delivery_status: status === 'READY' ? 'READY' : 'PENDING',
    price_cents: 0,
    created_at: '2026-07-23T00:00:00Z',
    updated_at: '2026-07-23T00:00:01Z',
  };
}

test('real create and preview pages use the private idempotent order contract', async ({
  context,
  page,
}) => {
  const portraitPath = path.resolve(
    'src/static/style-previews/solo_korean_minimal.jpg',
  );
  const portrait = readFileSync(portraitPath);
  let orderReads = 0;

  await context.addCookies([{
    name: 'vowpic_csrf',
    value: 'local-ui-contract-csrf',
    url: 'http://127.0.0.1:4173',
  }]);
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });

    if (endpoint === '/api/v1/ops/public_config') {
      return json({
        placements: {
          home_banner: {
            enabled: true,
            title: 'VowPic Studio',
            subtitle: 'Private generation',
            cta_label: 'Start',
            secondary_cta_label: 'Browse',
            image_url: '/style-previews/couple_old_money.jpg',
          },
        },
        support: { available: false },
        capabilities: {
          google_auth: true,
          authenticated_upload: true,
          generation: true,
          credit_pack_checkout: false,
          subscription_billing: false,
          private_download: true,
          partner_invite: false,
        },
      });
    }
    if (endpoint === '/api/v1/auth/me') {
      return json({ id: userId, email: 'ui-contract@example.test' });
    }
    if (endpoint === '/api/v1/credits/balance') return json({ balance: 50 });
    if (endpoint === '/api/v1/admin/me') return json({ message: 'Forbidden' }, 403);
    if (endpoint === '/api/v1/templates') {
      return json({
        templates: [{
          id: 'solo_korean_minimal',
          category: 'single',
          title: 'Korean Minimal',
          image_url: '/style-previews/solo_korean_minimal.jpg',
          style_family: 'korean_minimal',
          stability: 'stable',
        }],
      });
    }
    if (endpoint === '/api/v1/analytics/click') return json({ accepted: true }, 201);
    if (endpoint === '/api/v1/media/uploads') {
      expect(request.method()).toBe('POST');
      expect(request.headers()['x-csrf-token']).toBe('local-ui-contract-csrf');
      return json({
        assets: [{
          asset_id: assetId,
          media_type: 'image/jpeg',
          size_bytes: portrait.length,
        }],
      }, 201);
    }
    if (endpoint === '/api/v1/orders/create') {
      expect(request.method()).toBe('POST');
      expect(request.headers()['x-csrf-token']).toBe('local-ui-contract-csrf');
      return json({
        order_id: orderId,
        status: 'QUEUED',
        status_url: `/api/v1/orders/${orderId}`,
      }, 202);
    }
    if (endpoint === `/api/v1/orders/${orderId}`) {
      orderReads += 1;
      return json(order(orderReads === 1 ? 'QUEUED' : 'READY'));
    }
    if (endpoint === `/api/v1/orders/${orderId}/progress`) {
      return json(order('READY'));
    }
    if (endpoint === `/api/v1/orders/${orderId}/assets/${finalAssetId}/download`) {
      return route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'image/jpeg',
          'Content-Disposition': 'inline; filename="vowpic-final.jpg"',
        },
        body: portrait,
      });
    }
    return json({ message: `Unhandled local UI endpoint ${endpoint}` }, 500);
  });

  const result = await runUiMainJourney(page, {
    templateId: 'solo_korean_minimal',
    assetPaths: [portraitPath],
    styleText: 'Quiet editorial light with natural skin texture.',
    mode: 'single',
    timeout: 30_000,
  });

  expect(result).toMatchObject({
    orderId,
    uploadedAssetIds: [assetId],
  });
});
