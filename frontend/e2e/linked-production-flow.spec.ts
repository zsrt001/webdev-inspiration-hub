import { expect, test, type Browser, type Page } from '@playwright/test';

import {
  api,
  completeGoogleLogin,
  exactKeys,
  idempotencyKey,
  pollJson,
  readProtectedAction,
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
  const userId = value(await currentUser(page), 'id');
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
    case 'quality':
      await runQuality(page, browser);
      return;
    case 'account_finalize':
      await runAccountFinalize(page);
      return;
    default:
      throw new Error('LINKED_ACCEPTANCE_PHASE is not allowlisted');
  }
});
