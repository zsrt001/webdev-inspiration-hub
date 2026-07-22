import { expect, test, type Browser, type Page } from '@playwright/test';

import { completeCreemCheckout } from './helpers/creem-hosted-checkout';
import {
  api,
  completeGoogleLogin,
  exactKeys,
  idempotencyKey,
  pollJson,
  readProtectedAction,
  readSignedAcceptanceReport,
  releaseBinding,
  requiredCoordinate,
  requiredPositiveInteger,
  requiredString,
  resolveProtectedAsset,
  uploadProtectedAsset,
  userSubjectHmac,
  writeBrowserObservation,
  type JsonObject,
} from './helpers/linked-acceptance';


const selectedPhase = String(process.env.LINKED_ACCEPTANCE_PHASE || '').trim();

test.skip(
  process.env.RUN_PRODUCTION_E2E !== '1',
  'linked Production acceptance is protected-only',
);

function productionBaseOrigin(): string {
  return new URL(requiredString(
    process.env.PRODUCTION_BASE_URL,
    'Production base URL',
    2048,
  )).origin;
}

function value(object: JsonObject, name: string): string {
  return requiredCoordinate(object[name], name);
}

async function currentUser(page: Page): Promise<JsonObject> {
  const result = await api<JsonObject>(page, '/api/v1/auth/me');
  return result.body;
}

function action(phase: string, fields: readonly string[]): JsonObject {
  return exactKeys(
    readProtectedAction(phase),
    ['schema', 'phase', ...fields],
    `${phase} action`,
  );
}

function orderBody(templateId: string, assetIds: string[], styleText?: string): JsonObject {
  return {
    template_id: templateId,
    asset_ids: assetIds,
    legal_accepted: true,
    ...(styleText ? { scene_text: styleText } : {}),
  };
}

async function createOrder(
  page: Page,
  templateId: string,
  assetIds: string[],
  styleText?: string,
): Promise<string> {
  const result = await api<JsonObject>(page, '/api/v1/orders/create', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey('acceptance-order') },
    body: orderBody(templateId, assetIds, styleText),
    expected: [202],
  });
  return value(result.body, 'order_id');
}

async function readyOrder(page: Page, orderId: string, timeout: number): Promise<JsonObject> {
  return pollJson<JsonObject>(
    page,
    `/api/v1/orders/${orderId}`,
    (body) => body.status === 'READY',
    timeout,
    `order ${orderId}`,
  );
}

function assetForRole(order: JsonObject, roles: readonly string[]): string {
  const assets = Array.isArray(order.assets) ? order.assets as JsonObject[] : [];
  const asset = assets.find((candidate) => roles.includes(String(candidate.role || '')));
  return value(asset || {}, 'id');
}

async function buyCredits(
  page: Page,
  productCode: string,
  instrument: JsonObject,
  timeout: number,
): Promise<{ purchaseId: string; status: JsonObject }> {
  const baseOrigin = productionBaseOrigin();
  const checkout = await api<JsonObject>(page, '/api/v1/payments/checkout', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey('acceptance-credit-checkout') },
    body: { product_code: productCode, return_url: `${baseOrigin}/` },
    expected: [201],
  });
  const purchaseId = value(checkout.body, 'purchase_id');
  await completeCreemCheckout(
    page,
    checkout.body.checkout_url,
    instrument,
    baseOrigin,
    timeout,
  );
  const status = await pollJson<JsonObject>(
    page,
    `/api/v1/payments/status/${purchaseId}`,
    (body) => body.state === 'PAID',
    timeout,
    `credit purchase ${purchaseId}`,
  );
  return { purchaseId, status };
}

async function runCommercial(page: Page): Promise<void> {
  const input = action('commercial', [
    'currency',
    'cost_cap_minor_units',
    'source_asset',
    'template_id',
    'credit_product_code',
    'second_credit_product_code',
    'payment_instrument',
    'timeout_seconds',
  ]);
  const timeout = requiredPositiveInteger(input.timeout_seconds, 'commercial timeout', 7_200);
  const user = await currentUser(page);
  const userId = value(user, 'id');
  const upload = await uploadProtectedAsset(page, resolveProtectedAsset(input.source_asset));
  const source = exactKeys(
    (upload.assets as JsonObject[])[0],
    ['asset_id', 'width', 'height', 'mime_type', 'byte_size', 'expires_at'],
    'commercial source asset',
  );
  const sourceId = value(source, 'asset_id');
  const sourceRead = await api(page, `/api/v1/media/${sourceId}`);

  const trialOrderId = await createOrder(
    page,
    requiredString(input.template_id, 'commercial template'),
    [sourceId],
  );
  const trial = await readyOrder(page, trialOrderId, timeout);
  const previewId = assetForRole(trial, ['preview_watermarked']);
  const preview = await api(page, `/api/v1/orders/${trialOrderId}/assets/${previewId}/download`);

  const firstPurchase = await buyCredits(
    page,
    requiredString(input.credit_product_code, 'credit product'),
    exactKeys(input.payment_instrument, [
      'card_number',
      'expiry',
      'cvc',
      'cardholder_name',
      'country_code',
      'postal_code',
    ], 'commercial payment instrument'),
    timeout,
  );
  const paidOrderId = await createOrder(
    page,
    requiredString(input.template_id, 'commercial template'),
    [sourceId],
  );
  const paid = await readyOrder(page, paidOrderId, timeout);
  const finalId = assetForRole(paid, ['final_master', 'delivery_variant']);
  const finalDownload = await api(
    page,
    `/api/v1/orders/${paidOrderId}/assets/${finalId}/download`,
  );

  const refundRequest = await api<JsonObject>(
    page,
    `/api/v1/payments/${firstPurchase.purchaseId}/refund`,
    {
    method: 'POST',
      expected: [409],
    },
  );
  const refundDetail = exactKeys(
    refundRequest.body.detail,
    ['code', 'message'],
    'refund support response',
  );
  expect(refundDetail.code).toBe('refund_requires_support');
  process.stdout.write(
    `Creem Dashboard test-mode refund required for purchase ${firstPurchase.purchaseId}\n`,
  );
  await pollJson<JsonObject>(
    page,
    `/api/v1/payments/status/${firstPurchase.purchaseId}`,
    (body) => body.state === 'REVERSED',
    timeout,
    `credit refund ${firstPurchase.purchaseId}`,
  );
  const secondPurchase = await buyCredits(
    page,
    requiredString(input.second_credit_product_code, 'second credit product'),
    exactKeys(input.payment_instrument, [
      'card_number',
      'expiry',
      'cvc',
      'cardholder_name',
      'country_code',
      'postal_code',
    ], 'commercial payment instrument'),
    timeout,
  );
  const exported = await api<JsonObject>(page, '/api/v1/account/export');

  writeBrowserObservation('commercial-before-delete', {
    user_subject_hmac_sha256: userSubjectHmac(userId),
    currency: requiredString(input.currency, 'commercial currency', 3),
    cost_cap_minor_units: requiredPositiveInteger(
      input.cost_cap_minor_units,
      'commercial cost cap',
      1_000_000,
    ),
    observations: {
      private_upload_response: sourceRead.status === 200,
      watermarked_preview_response: preview.status === 200,
      private_final_download_response: finalDownload.status === 200,
      account_export_response: exported.status === 200,
    },
    links: {
      user_id: userId,
      upload_asset_id: sourceId,
      trial_order_id: trialOrderId,
      trial_preview_asset_id: previewId,
      purchase_id: firstPurchase.purchaseId,
      paid_order_id: paidOrderId,
      paid_final_asset_id: finalId,
      second_purchase_id: secondPurchase.purchaseId,
      account_export_id: value(exported.body, 'export_id'),
    },
  });
}

async function runSubscription(page: Page): Promise<void> {
  const baseOrigin = productionBaseOrigin();
  const input = action('subscription', [
    'currency',
    'cost_cap_minor_units',
    'source_asset',
    'template_id',
    'plan_code',
    'payment_instrument',
    'timeout_seconds',
  ]);
  const timeout = requiredPositiveInteger(input.timeout_seconds, 'subscription timeout', 7_200);
  const userId = value(await currentUser(page), 'id');
  const checkout = await api<JsonObject>(page, '/api/v1/subscriptions/checkout', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey('acceptance-subscription-checkout') },
    body: {
      plan_code: requiredString(input.plan_code, 'subscription plan code'),
      return_url: `${baseOrigin}/`,
    },
  });
  await completeCreemCheckout(
    page,
    checkout.body.checkout_url,
    exactKeys(input.payment_instrument, [
      'card_number',
      'expiry',
      'cvc',
      'cardholder_name',
      'country_code',
      'postal_code',
    ], 'subscription payment instrument'),
    baseOrigin,
    timeout,
  );
  const active = await pollJson<JsonObject>(
    page,
    '/api/v1/subscriptions/me',
    (body) => body.status === 'ACTIVE',
    timeout,
    'initial subscription activation',
  );
  const upload = await uploadProtectedAsset(page, resolveProtectedAsset(input.source_asset));
  const source = (upload.assets as JsonObject[])[0];
  const initialOrderId = await createOrder(
    page,
    requiredString(input.template_id, 'subscription template'),
    [value(source, 'asset_id')],
  );
  await readyOrder(page, initialOrderId, timeout);
  const cancel = await api<JsonObject>(page, '/api/v1/subscriptions/cancel', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey('acceptance-subscription-cancel') },
  });
  const canceled = await pollJson<JsonObject>(
    page,
    '/api/v1/subscriptions/me',
    (body) => (
      body.cancel_at_period_end === true
      && ['ACTIVE', 'CANCEL_REQUESTED'].includes(String(body.status || ''))
    ),
    timeout,
    'period-end subscription cancellation',
  );
  writeBrowserObservation('subscription', {
    user_subject_hmac_sha256: userSubjectHmac(userId),
    currency: requiredString(input.currency, 'subscription currency', 3),
    cost_cap_minor_units: requiredPositiveInteger(
      input.cost_cap_minor_units,
      'subscription cost cap',
      1_000_000,
    ),
    observations: {
      starter_checkout_response: checkout.status === 200,
      cancel_response: cancel.body.cancel_at_period_end === true,
      active_until_period_end_response: canceled.cancel_at_period_end === true,
      initial_order_response: true,
    },
    links: {
      user_id: userId,
      subscription_id: value(active, 'subscription_id'),
      initial_order_id: initialOrderId,
    },
  });
}

async function authenticatedPartner(
  browser: Browser,
): Promise<{ page: Page; dispose: () => Promise<void> }> {
  const context = await browser.newContext({
    storageState: requiredString(
      process.env.PRODUCTION_GOOGLE_PARTNER_STORAGE_STATE_PATH,
      'partner Google storage state',
      4096,
    ),
  });
  const page = await context.newPage();
  await completeGoogleLogin(
    page,
    requiredString(process.env.PRODUCTION_GOOGLE_PARTNER_EMAIL, 'partner Google email', 320),
  );
  return { page, dispose: () => context.close() };
}

async function runQuality(page: Page, browser: Browser): Promise<void> {
  const input = action('quality', ['cases', 'timeout_seconds']);
  const cases = Array.isArray(input.cases) ? input.cases as JsonObject[] : [];
  expect(cases).toHaveLength(6);
  const userId = value(await currentUser(page), 'id');
  const timeout = requiredPositiveInteger(input.timeout_seconds, 'quality timeout', 7_200);
  const links: JsonObject[] = [];
  for (const rawCase of cases) {
    const qualityCase = exactKeys(
      rawCase,
      ['id', 'template_id', 'asset_paths', 'style_text'],
      'quality case action',
    );
    const caseId = requiredCoordinate(qualityCase.id, 'quality case ID');
    const assetPaths = Array.isArray(qualityCase.asset_paths)
      ? qualityCase.asset_paths
      : [];
    if (assetPaths.length < 1 || assetPaths.length > 2) {
      throw new Error(`quality case ${caseId} asset count is invalid`);
    }
    if (caseId === 'partner_invite_remote_couple' && assetPaths.length !== 2) {
      throw new Error('remote partner quality case requires two distinct assets');
    }
    const uploaded: string[] = [];
    const primaryAssetPaths = caseId === 'partner_invite_remote_couple'
      ? assetPaths.slice(0, 1)
      : assetPaths;
    for (const assetPath of primaryAssetPaths) {
      const upload = await uploadProtectedAsset(page, resolveProtectedAsset(assetPath));
      uploaded.push(value((upload.assets as JsonObject[])[0], 'asset_id'));
    }
    let orderId: string;
    if (caseId === 'partner_invite_remote_couple') {
      expect(uploaded).toHaveLength(1);
      const invite = await api<JsonObject>(page, '/api/v1/partner-invites', {
        method: 'POST',
        body: { template_id: requiredString(qualityCase.template_id, 'quality template') },
        expected: [201],
      });
      const partner = await authenticatedPartner(browser);
      try {
        const partnerUpload = await uploadProtectedAsset(
          partner.page,
          resolveProtectedAsset(assetPaths[1]),
        );
        const accepted = await api<JsonObject>(partner.page, '/api/v1/partner-invites/accept', {
          method: 'POST',
          body: { token: requiredString(invite.body.token, 'partner invite token', 128) },
        });
        const consent = await api<JsonObject>(
          partner.page,
          `/api/v1/partner-invites/${value(accepted.body, 'id')}/consent`,
          {
            method: 'POST',
            body: {
              expected_version: accepted.body.version,
              order_intent_id: accepted.body.order_intent_id,
              order_intent_hash: accepted.body.order_intent_hash,
              partner_asset_id: value((partnerUpload.assets as JsonObject[])[0], 'asset_id'),
            },
          },
        );
        const order = await api<JsonObject>(
          page,
          `/api/v1/partner-invites/${value(consent.body, 'id')}/order`,
          {
            method: 'POST',
            headers: { 'Idempotency-Key': idempotencyKey('acceptance-partner-order') },
            body: {
              expected_version: consent.body.version,
              host_asset_id: uploaded[0],
              consent_event_id: consent.body.consent_event_id,
            },
            expected: [202],
          },
        );
        orderId = value(order.body, 'order_id');
      } finally {
        await partner.dispose();
      }
    } else {
      orderId = await createOrder(
        page,
        requiredString(qualityCase.template_id, 'quality template'),
        uploaded,
        requiredString(qualityCase.style_text, 'quality style text', 1000),
      );
    }
    await readyOrder(page, orderId, timeout);
    links.push({
      id: caseId,
      order_id: orderId,
    });
  }
  writeBrowserObservation('quality', {
    user_subject_hmac_sha256: userSubjectHmac(userId),
    user_id: userId,
    cases: links,
  });
}

async function runAccountFinalize(page: Page): Promise<void> {
  const input = action('account_finalize', ['currency', 'cost_cap_minor_units']);
  const commercial = readSignedAcceptanceReport(
    requiredString(
      process.env.LINKED_ACCEPTANCE_COMMERCIAL_REPORT,
      'commercial report path',
      4096,
    ),
    {
      schema: 'vowpic.linked-commercial-acceptance.v1',
      phase: 'commercial-before-delete',
    },
  );
  const userId = value(commercial.links as JsonObject, 'user_id');
  const closed = await api<JsonObject>(page, '/api/v1/users/me/close', {
    method: 'POST',
    body: { confirmation: 'CLOSE MY ACCOUNT' },
  });
  const denied = await api(page, '/api/v1/auth/me', { expected: [401] });
  writeBrowserObservation('commercial-finalize-delete', {
    user_subject_hmac_sha256: userSubjectHmac(userId),
    currency: requiredString(input.currency, 'finalization currency', 3),
    cost_cap_minor_units: requiredPositiveInteger(
      input.cost_cap_minor_units,
      'finalization cost cap',
      1_000_000,
    ),
    observations: {
      account_close_response: closed.body.media_cleanup_pending === true,
      post_close_session_denied: denied.status === 401,
    },
    links: { user_id: userId },
  });
}

test(`linked Production phase ${selectedPhase}`, async ({ page, browser }) => {
  releaseBinding();
  await completeGoogleLogin(
    page,
    requiredString(process.env.PRODUCTION_GOOGLE_EMAIL, 'primary Google email', 320),
  );
  switch (selectedPhase) {
    case 'commercial':
      await runCommercial(page);
      return;
    case 'subscription':
      await runSubscription(page);
      return;
    case 'quality':
      await runQuality(page, browser);
      return;
    case 'account-finalize':
      await runAccountFinalize(page);
      return;
    default:
      throw new Error('LINKED_ACCEPTANCE_PHASE is not allowlisted');
  }
});
