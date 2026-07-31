import { createHash, createHmac } from 'node:crypto';
import {
  chmodSync,
  existsSync,
  lstatSync,
  readFileSync,
  realpathSync,
  renameSync,
  writeFileSync,
} from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';

import {
  api,
  completeGoogleLogin,
  idempotencyKey,
  pollActionJson,
  releaseBinding,
  requiredCoordinate,
  requiredString,
  resolveProtectedAsset,
  uploadProtectedAsset,
  userSubjectHmac,
  type JsonObject,
  type ReleaseBinding,
} from './helpers/linked-acceptance';


const capability = String(process.env.PRODUCTION_CANARY_CAPABILITY || '').trim();
const outputPath = String(process.env.PRODUCTION_CANARY_OUTPUT_PATH || '').trim();
const cleanupOutputPath = String(
  process.env.PRODUCTION_CANARY_CLEANUP_OUTPUT_PATH || '',
).trim();
const signingKey = String(process.env.ACCEPTANCE_EVIDENCE_SIGNING_KEY || '');
const baseURL = String(process.env.PRODUCTION_BASE_URL || '').trim().replace(/\/$/, '');

const routes: Record<string, { method: string; path: string }> = {
  google_auth: { method: 'GET', path: '/api/v1/auth/me' },
  authenticated_upload: { method: 'POST', path: '/api/v1/media/uploads' },
  credit_pack_checkout: { method: 'POST', path: '/api/v1/payments/checkout' },
  subscription_billing: { method: 'POST', path: '/api/v1/subscriptions/checkout' },
  generation: { method: 'POST', path: '/api/v1/orders/create' },
  private_download: { method: 'GET', path: '/api/v1/orders/:order/assets/:asset/download' },
  partner_invite: { method: 'POST', path: '/api/v1/partner-invites' },
};

interface CanaryState extends ReleaseBinding {
  schema: 'vowpic.production-canary-runtime-state.v1';
  user_id: string;
  upload_asset_id?: string;
  order_id?: string;
  download_asset_id?: string;
}

interface CapabilityResult {
  request: JsonObject;
  response: unknown;
  responseStatus: number;
  requestPath: string;
}

test.skip(process.env.RUN_PRODUCTION_E2E !== '1', 'Production canary is protected-only');

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`
    )).join(',')}}`;
  }
  return JSON.stringify(value);
}

function sha256(value: Buffer | string): string {
  return createHash('sha256').update(value).digest('hex');
}

function privatePath(candidate: string, label: string, mustExist = false): string {
  const root = realpathSync(String(process.env.RUNNER_TEMP || ''));
  const resolved = mustExist ? realpathSync(candidate) : path.resolve(candidate);
  expect(resolved.startsWith(`${root}${path.sep}`), `${label} must stay in RUNNER_TEMP`).toBeTruthy();
  return resolved;
}

function statePath(): string {
  return privatePath(
    requiredString(process.env.PRODUCTION_CANARY_STATE_PATH, 'canary state path', 4096),
    'canary state',
  );
}

function validateState(raw: unknown): CanaryState {
  const state = raw as CanaryState;
  const binding = releaseBinding();
  expect(state && typeof state === 'object' && !Array.isArray(state)).toBeTruthy();
  expect(state.schema).toBe('vowpic.production-canary-runtime-state.v1');
  for (const [name, value] of Object.entries(binding)) {
    expect(state[name as keyof ReleaseBinding], `canary state ${name}`).toBe(value);
  }
  requiredCoordinate(state.user_id, 'canary user ID');
  for (const [name, value] of Object.entries(state)) {
    if (name.endsWith('_id') && value !== undefined) {
      requiredCoordinate(value, `canary state ${name}`);
    }
  }
  return state;
}

function readState(): CanaryState {
  const candidate = statePath();
  expect(existsSync(candidate), 'run-bound canary state must already exist').toBeTruthy();
  const stat = lstatSync(candidate);
  expect(stat.isFile() && !stat.isSymbolicLink() && stat.size > 1 && stat.size < 64_000).toBeTruthy();
  return validateState(JSON.parse(readFileSync(candidate, 'utf8')));
}

function writeState(state: CanaryState): void {
  validateState(state);
  const target = statePath();
  const temporary = `${target}.next`;
  expect(existsSync(temporary), 'stale canary state update must not exist').toBeFalsy();
  writeFileSync(temporary, `${canonical(state)}\n`, {
    encoding: 'utf8',
    flag: 'wx',
    mode: 0o600,
  });
  chmodSync(temporary, 0o600);
  renameSync(temporary, target);
  chmodSync(target, 0o600);
}

function updateState(userId: string, values: Partial<CanaryState> = {}): CanaryState {
  const binding = releaseBinding();
  const prior = existsSync(statePath()) ? readState() : undefined;
  if (prior) expect(prior.user_id).toBe(userId);
  const next: CanaryState = {
    schema: 'vowpic.production-canary-runtime-state.v1',
    ...binding,
    ...(prior || {}),
    ...values,
    user_id: requiredCoordinate(userId, 'canary user ID'),
  };
  writeState(next);
  return next;
}

function writeSigned(output: string, unsigned: JsonObject, label: string): void {
  expect(signingKey.length).toBeGreaterThanOrEqual(32);
  const target = privatePath(requiredString(output, label, 4096), label);
  const signature = createHmac('sha256', signingKey).update(canonical(unsigned)).digest('hex');
  writeFileSync(target, `${canonical({ ...unsigned, signature: `hmac-sha256:${signature}` })}\n`, {
    encoding: 'utf8',
    flag: 'wx',
    mode: 0o600,
  });
  chmodSync(target, 0o600);
}

async function currentUser(page: Page): Promise<JsonObject> {
  return (await api<JsonObject>(page, '/api/v1/auth/me')).body;
}

function value(object: JsonObject, name: string): string {
  return requiredCoordinate(object[name], name);
}

function downloadableAsset(order: JsonObject): string {
  const assets = Array.isArray(order.assets) ? order.assets as JsonObject[] : [];
  const asset = assets.find((candidate) => (
    candidate.role === 'final_master' || candidate.role === 'delivery_variant'
  )) || assets.find((candidate) => candidate.role === 'preview_watermarked');
  return value(asset || {}, 'id');
}

async function login(page: Page): Promise<string> {
  await completeGoogleLogin(
    page,
    requiredString(process.env.PRODUCTION_GOOGLE_EMAIL, 'Production canary Google email', 320),
  );
  return value(await currentUser(page), 'id');
}

async function executeCapability(page: Page, userId: string): Promise<CapabilityResult> {
  const route = routes[capability];
  expect(route, 'capability must be allowlisted').toBeTruthy();

  if (capability === 'google_auth') {
    updateState(userId);
    const response = await api<JsonObject>(page, route.path);
    expect(value(response.body, 'id')).toBe(userId);
    return {
      request: { method: route.method, path: route.path },
      response: response.body,
      responseStatus: response.status,
      requestPath: route.path,
    };
  }

  const state = readState();
  expect(state.user_id).toBe(userId);

  if (capability === 'authenticated_upload') {
    const assetPath = resolveProtectedAsset('assets/primary_woman.jpg');
    const response = await uploadProtectedAsset(page, assetPath);
    const assets = Array.isArray(response.assets) ? response.assets as JsonObject[] : [];
    expect(assets).toHaveLength(1);
    updateState(userId, { upload_asset_id: value(assets[0], 'asset_id') });
    return {
      request: { method: route.method, path: route.path, asset_sha256: sha256(readFileSync(assetPath)) },
      response,
      responseStatus: 200,
      requestPath: route.path,
    };
  }

  if (capability === 'credit_pack_checkout') {
    const body = { product_code: 'pack_50', return_url: `${baseURL}/` };
    const response = await api<JsonObject>(page, route.path, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey('production-canary-credit') },
      body,
      expected: [201],
    });
    value(response.body, 'purchase_id');
    expect(response.body.provider).toBe('creem');
    expect(response.body.status).toBe('READY');
    expect(String(response.body.checkout_url || '')).toMatch(/^https:\/\//);
    return { request: { method: route.method, path: route.path, body }, response: response.body, responseStatus: response.status, requestPath: route.path };
  }

  if (capability === 'subscription_billing') {
    const body = { plan_code: 'starter_monthly', return_url: `${baseURL}/` };
    const response = await api<JsonObject>(page, route.path, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey('production-canary-subscription') },
      body,
    });
    expect(response.body.provider).toBe('creem');
    expect(response.body.status).toBe('READY');
    expect(String(response.body.checkout_url || '')).toMatch(/^https:\/\//);
    return { request: { method: route.method, path: route.path, body }, response: response.body, responseStatus: response.status, requestPath: route.path };
  }

  if (capability === 'generation') {
    const assetId = requiredCoordinate(state.upload_asset_id, 'canary upload asset ID');
    const body = {
      template_id: 'solo_royal_castle',
      asset_ids: [assetId],
      legal_accepted: true,
      scene_text: 'Production release canary portrait with natural identity and studio lighting.',
    };
    const created = await api<JsonObject>(page, route.path, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey('production-canary-generation') },
      body,
      expected: [202],
    });
    const orderId = value(created.body, 'order_id');
    await pollActionJson<JsonObject>(
      page,
      `/api/v1/orders/${orderId}/progress`,
      (bodyValue) => bodyValue.status === 'READY',
      780,
      `Production canary order ${orderId}`,
    );
    const order = (await api<JsonObject>(page, `/api/v1/orders/${orderId}`)).body;
    expect(order.status).toBe('READY');
    updateState(userId, { order_id: orderId, download_asset_id: downloadableAsset(order) });
    return { request: { method: route.method, path: route.path, body }, response: created.body, responseStatus: created.status, requestPath: route.path };
  }

  if (capability === 'private_download') {
    const orderId = requiredCoordinate(state.order_id, 'canary order ID');
    const assetId = requiredCoordinate(state.download_asset_id, 'canary download asset ID');
    const requestPath = `/api/v1/orders/${orderId}/assets/${assetId}/download`;
    const response = await api(page, requestPath);
    return { request: { method: route.method, path: requestPath }, response: response.body, responseStatus: response.status, requestPath };
  }

  const body = { template_id: 'classic_bw' };
  const response = await api<JsonObject>(page, route.path, {
    method: 'POST',
    body,
    expected: [201],
  });
  value(response.body, 'token');
  const invite = response.body.invite as JsonObject;
  value(invite || {}, 'id');
  return { request: { method: route.method, path: route.path, body }, response: response.body, responseStatus: response.status, requestPath: route.path };
}

test(`@capability:${capability} exact live Production capability probe`, async ({ page }) => {
  test.setTimeout(840_000);
  expect(process.env.RUN_PRODUCTION_E2E).toBe('1');
  expect(routes[capability], 'capability must be allowlisted').toBeTruthy();
  expect(signingKey.length).toBeGreaterThanOrEqual(32);
  expect(baseURL).toMatch(/^https:\/\/[^/]+$/);
  const binding = releaseBinding();
  const userId = await login(page);
  const result = await executeCapability(page, userId);
  expect(result.responseStatus).toBeGreaterThanOrEqual(200);
  expect(result.responseStatus).toBeLessThan(300);

  const unsigned = {
    schema: 'vowpic.production-capability-canary.v1',
    passed: true,
    capability,
    ...binding,
    before_snapshot_hash: String(process.env.PRODUCTION_CANARY_BEFORE_SNAPSHOT_HASH || ''),
    after_snapshot_hash: String(process.env.PRODUCTION_CANARY_AFTER_SNAPSHOT_HASH || ''),
    request_input_sha256: sha256(canonical(result.request)),
    request_path_sha256: sha256(result.requestPath),
    response_sha256: sha256(canonical(result.response)),
    response_status: result.responseStatus,
    produced_at: new Date().toISOString(),
  };
  expect(unsigned.before_snapshot_hash).toMatch(/^[0-9a-f]{64}$/);
  expect(unsigned.after_snapshot_hash).toMatch(/^[0-9a-f]{64}$/);
  writeSigned(outputPath, unsigned, 'canary output');
});

test('@cleanup close the exact Production canary account', async ({ page }) => {
  test.setTimeout(180_000);
  const binding = releaseBinding();
  const state = readState();
  const userId = await login(page);
  expect(userId).toBe(state.user_id);
  const closed = await api<JsonObject>(page, '/api/v1/users/me/close', {
    method: 'POST',
    body: { confirmation: 'CLOSE MY ACCOUNT' },
  });
  expect(closed.body.media_cleanup_pending).toBe(true);
  const denied = await api(page, '/api/v1/auth/me', { expected: [401] });
  expect(denied.status).toBe(401);
  writeSigned(cleanupOutputPath, {
    schema: 'vowpic.production-canary-cleanup.v1',
    passed: true,
    ...binding,
    user_id: userId,
    user_subject_hmac_sha256: userSubjectHmac(userId),
    observations: {
      account_closed: true,
      media_cleanup_requested: true,
      post_close_session_denied: true,
    },
    produced_at: new Date().toISOString(),
  }, 'canary cleanup output');
});
