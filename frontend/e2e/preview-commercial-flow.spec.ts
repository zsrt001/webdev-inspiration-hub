import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Browser, type Page } from '@playwright/test';

import { completeCreemCheckout } from './helpers/creem-hosted-checkout';
import {
  api,
  completeGoogleLogin,
  exactKeys,
  idempotencyKey,
  pollActionJson,
  pollJson,
  readProtectedAction,
  requiredCoordinate,
  requiredPositiveInteger,
  requiredString,
  resolveProtectedAsset,
  uploadProtectedAsset,
  userSubjectHmac,
  writeBrowserObservation,
  type JsonObject,
} from './helpers/linked-acceptance';
import { runUiMainJourney } from './helpers/ui-main-journey';


test.skip(
  process.env.RUN_PREVIEW_COMMERCIAL_E2E !== '1',
  'Preview Commercial acceptance is protected-only',
);

const successfulTestInstrument: JsonObject = {
  // Public Creem Test Mode card; it cannot create a live charge.
  card_number: '4242 4242 4242 4242',
  expiry: '12/35',
  cvc: '123',
  cardholder_name: 'VowPic Preview',
  country_code: 'US',
  postal_code: '10001',
};

function previewBaseOrigin(): string {
  return new URL(
    requiredString(process.env.PREVIEW_BASE_URL, 'Preview base URL', 2048),
  ).origin;
}

function value(object: JsonObject, name: string): string {
  return requiredCoordinate(object[name], name);
}

async function currentUser(page: Page): Promise<JsonObject> {
  return (await api<JsonObject>(page, '/api/v1/auth/me')).body;
}

function qualityCases(): { cases: JsonObject[]; timeout: number } {
  const action = exactKeys(
    readProtectedAction('quality'),
    ['schema', 'phase', 'cases', 'timeout_seconds'],
    'quality action',
  );
  const cases = Array.isArray(action.cases) ? action.cases as JsonObject[] : [];
  return {
    cases,
    timeout: requiredPositiveInteger(
      action.timeout_seconds,
      'Preview Commercial timeout',
      7_200,
    ),
  };
}

async function createOrder(
  page: Page,
  templateId: string,
  assetIds: string[],
  styleText: string,
): Promise<string> {
  const response = await api<JsonObject>(page, '/api/v1/orders/create', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey('preview-commercial-order') },
    body: {
      template_id: templateId,
      asset_ids: assetIds,
      legal_accepted: true,
      scene_text: styleText,
    },
    expected: [202],
  });
  return value(response.body, 'order_id');
}

async function readyOrder(
  page: Page,
  orderId: string,
  timeout: number,
): Promise<JsonObject> {
  return pollActionJson<JsonObject>(
    page,
    `/api/v1/orders/${orderId}/progress`,
    (body) => body.status === 'READY',
    timeout,
    `order ${orderId}`,
  );
}

function finalAsset(order: JsonObject): string {
  const assets = Array.isArray(order.assets) ? order.assets as JsonObject[] : [];
  const asset = assets.find((candidate) => (
    candidate.role === 'final_master' || candidate.role === 'delivery_variant'
  ));
  return value(asset || {}, 'id');
}

async function buyCredits(
  page: Page,
  timeout: number,
): Promise<string> {
  const baseOrigin = previewBaseOrigin();
  const checkout = await api<JsonObject>(page, '/api/v1/payments/checkout', {
    method: 'POST',
    headers: {
      'Idempotency-Key': idempotencyKey('preview-commercial-credit-checkout'),
    },
    body: {
      product_code: 'pack_50',
      return_url: `${baseOrigin}/`,
    },
    expected: [201],
  });
  const purchaseId = value(checkout.body, 'purchase_id');
  await completeCreemCheckout(
    page,
    checkout.body.checkout_url,
    successfulTestInstrument,
    baseOrigin,
    timeout,
  );
  await pollJson<JsonObject>(
    page,
    `/api/v1/payments/status/${purchaseId}`,
    (body) => body.state === 'PAID',
    timeout,
    `Creem Test Mode purchase ${purchaseId}`,
  );
  return purchaseId;
}

async function subscribeAndScheduleCancel(
  page: Page,
  timeout: number,
): Promise<string> {
  const baseOrigin = previewBaseOrigin();
  const checkout = await api<JsonObject>(page, '/api/v1/subscriptions/checkout', {
    method: 'POST',
    headers: {
      'Idempotency-Key': idempotencyKey('preview-commercial-subscription'),
    },
    body: {
      plan_code: 'starter_monthly',
      return_url: `${baseOrigin}/`,
    },
  });
  await completeCreemCheckout(
    page,
    checkout.body.checkout_url,
    successfulTestInstrument,
    baseOrigin,
    timeout,
  );
  const active = await pollJson<JsonObject>(
    page,
    '/api/v1/subscriptions/me',
    (body) => body.status === 'ACTIVE',
    timeout,
    'Creem Test Mode subscription activation',
  );
  const subscriptionId = value(active, 'subscription_id');
  await api<JsonObject>(page, '/api/v1/subscriptions/cancel', {
    method: 'POST',
    headers: {
      'Idempotency-Key': idempotencyKey('preview-commercial-cancel'),
    },
  });
  await pollJson<JsonObject>(
    page,
    '/api/v1/subscriptions/me',
    (body) => (
      body.cancel_at_period_end === true
      && ['CANCEL_REQUESTED', 'CANCELED'].includes(String(body.status || ''))
    ),
    timeout,
    'scheduled subscription cancellation',
  );
  return subscriptionId;
}

async function authenticatedPartner(
  browser: Browser,
): Promise<{ page: Page; dispose: () => Promise<void> }> {
  const context = await browser.newContext({
    storageState: requiredString(
      process.env.PREVIEW_SECOND_GOOGLE_STORAGE_STATE_PATH,
      'partner Preview Google storage state',
      4096,
    ),
  });
  const page = await context.newPage();
  await completeGoogleLogin(
    page,
    requiredString(
      process.env.PREVIEW_SECOND_GOOGLE_EMAIL,
      'partner Preview Google email',
      320,
    ),
  );
  return { page, dispose: () => context.close() };
}

async function closeAcceptanceAccount(page: Page): Promise<boolean> {
  try {
    const result = await api(page, '/api/v1/users/me/close', {
      method: 'POST',
      body: { confirmation: 'CLOSE MY ACCOUNT' },
      expected: [200, 401, 409],
    });
    return result.status === 200 || result.status === 401;
  } catch {
    return false;
  }
}

async function runPartnerJourney(
  page: Page,
  browser: Browser,
  qualityCase: JsonObject,
  timeout: number,
): Promise<{
  partnerUserId: string;
  inviteId: string;
  orderId: string;
}> {
  const assetPaths = Array.isArray(qualityCase.asset_paths)
    ? qualityCase.asset_paths
    : [];
  expect(assetPaths).toHaveLength(2);
  const hostUpload = await uploadProtectedAsset(
    page,
    resolveProtectedAsset(assetPaths[0]),
  );
  const hostAssetId = value(
    (hostUpload.assets as JsonObject[])[0],
    'asset_id',
  );
  const invite = await api<JsonObject>(page, '/api/v1/partner-invites', {
    method: 'POST',
    body: {
      template_id: requiredString(qualityCase.template_id, 'partner template'),
    },
    expected: [201],
  });
  const inviteId = value(invite.body, 'id');
  const partner = await authenticatedPartner(browser);
  let partnerClosed = false;
  try {
    const partnerUserId = value(await currentUser(partner.page), 'id');
    const partnerUpload = await uploadProtectedAsset(
      partner.page,
      resolveProtectedAsset(assetPaths[1]),
    );
    const accepted = await api<JsonObject>(
      partner.page,
      '/api/v1/partner-invites/accept',
      {
        method: 'POST',
        body: {
          token: requiredString(invite.body.token, 'partner invite token', 128),
        },
      },
    );
    const consent = await api<JsonObject>(
      partner.page,
      `/api/v1/partner-invites/${inviteId}/consent`,
      {
        method: 'POST',
        body: {
          expected_version: accepted.body.version,
          order_intent_id: accepted.body.order_intent_id,
          order_intent_hash: accepted.body.order_intent_hash,
          partner_asset_id: value(
            (partnerUpload.assets as JsonObject[])[0],
            'asset_id',
          ),
        },
      },
    );
    const order = await api<JsonObject>(
      page,
      `/api/v1/partner-invites/${inviteId}/order`,
      {
        method: 'POST',
        headers: {
          'Idempotency-Key': idempotencyKey('preview-commercial-partner-order'),
        },
        body: {
          expected_version: consent.body.version,
          host_asset_id: hostAssetId,
          consent_event_id: consent.body.consent_event_id,
        },
        expected: [202],
      },
    );
    const orderId = value(order.body, 'order_id');
    await readyOrder(page, orderId, timeout);
    await pollJson<JsonObject>(
      page,
      `/api/v1/partner-invites/${inviteId}`,
      (body) => body.status === 'COMPLETED',
      timeout,
      `partner invite ${inviteId}`,
    );
    partnerClosed = await closeAcceptanceAccount(partner.page);
    expect(partnerClosed, 'partner acceptance account is closed').toBe(true);
    return { partnerUserId, inviteId, orderId };
  } finally {
    if (!partnerClosed) {
      await closeAcceptanceAccount(partner.page);
    }
    await partner.dispose();
  }
}

test('full Preview Commercial SaaS journey', async ({ page, browser }) => {
  test.setTimeout(3_600_000);
  const { cases, timeout } = qualityCases();
  const single = cases.find((item) => (
    item.id !== 'partner_invite_remote_couple'
    && Array.isArray(item.asset_paths)
    && item.asset_paths.length === 1
  ));
  const couple = cases.find((item) => item.id === 'partner_invite_remote_couple');
  if (!single || !couple) {
    throw new Error('reviewed single and partner quality actions are required');
  }

  await completeGoogleLogin(
    page,
    requiredString(process.env.PREVIEW_GOOGLE_EMAIL, 'Preview Google email', 320),
  );
  const primaryUserId = value(await currentUser(page), 'id');
  let primaryClosed = false;
  try {
    const purchaseId = await buyCredits(page, timeout);
    const subscriptionId = await subscribeAndScheduleCancel(page, timeout);

    const uiJourney = await runUiMainJourney(page, {
      templateId: requiredString(single.template_id, 'main template'),
      assetPaths: [
        resolveProtectedAsset((single.asset_paths as unknown[])[0]),
      ],
      styleText: requiredString(single.style_text, 'main style text', 512),
      mode: 'single',
      timeout,
    });
    const mainOrderId = uiJourney.orderId;
    const mainOrder = (await api<JsonObject>(
      page,
      `/api/v1/orders/${mainOrderId}`,
    )).body;
    expect(mainOrder.status).toBe('READY');
    const finalAssetId = finalAsset(mainOrder);
    const download = await api(
      page,
      `/api/v1/orders/${mainOrderId}/assets/${finalAssetId}/download`,
    );

    const partner = await runPartnerJourney(page, browser, couple, timeout);
    const exported = await api<JsonObject>(page, '/api/v1/account/export');
    expect(exported.body.schema_version).toBe('account-export.v1');

    await page.goto('/');
    const axe = await new AxeBuilder({ page }).analyze();
    const seriousOrCritical = axe.violations.filter((violation) => (
      violation.impact === 'serious' || violation.impact === 'critical'
    ));
    expect(seriousOrCritical).toEqual([]);

    const reviewOnly = await api(page, `/api/v1/payments/${purchaseId}/refund`, {
      method: 'POST',
      expected: [409],
    });
    expect(reviewOnly.status).toBe(409);
    console.log(`CREEM_TEST_REFUND_PURCHASE_ID=${purchaseId}`);
    await pollJson<JsonObject>(
      page,
      `/api/v1/payments/status/${purchaseId}`,
      (body) => body.state === 'REVERSED',
      1_800,
      `Creem Test Mode dashboard refund ${purchaseId}`,
    );

    primaryClosed = await closeAcceptanceAccount(page);
    expect(primaryClosed, 'primary acceptance account is closed').toBe(true);
    await api(page, '/api/v1/auth/me', { expected: [401] });

    writeBrowserObservation(
      'preview-commercial-complete',
      {
        user_subject_hmac_sha256: userSubjectHmac(primaryUserId),
        observations: {
          creem_checkout_paid: true,
          creem_refund_webhook_applied: true,
          subscription_active: true,
          subscription_scheduled_cancel: true,
          generation_ready: mainOrder.status === 'READY',
          private_download: download.status === 200,
          create_preview_download_ui_journey: true,
          account_export: exported.status === 200,
          account_delete: true,
          partner_invite_completed: true,
          a11y_zero_serious_or_critical: seriousOrCritical.length === 0,
        },
        links: {
          primary_user_id: primaryUserId,
          partner_user_id: partner.partnerUserId,
          purchase_id: purchaseId,
          subscription_id: subscriptionId,
          main_order_id: mainOrderId,
          partner_invite_id: partner.inviteId,
          partner_order_id: partner.orderId,
        },
      },
      'PREVIEW_COMMERCIAL_BROWSER_REPORT',
    );
  } finally {
    if (!primaryClosed) {
      await closeAcceptanceAccount(page);
    }
  }
});
