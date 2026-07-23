import { createHmac, randomUUID, timingSafeEqual } from 'node:crypto';
import {
  chmodSync,
  lstatSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from 'node:fs';
import path from 'node:path';
import { expect, type Page } from '@playwright/test';

const SENSITIVE_KEY = /(email|token|cookie|password|secret|authorization|raw_url|object_key|permanent_url|card|cvc)/i;
const EMAIL = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i;
const JWT = /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/;
const SHA40 = /^[0-9a-f]{40}$/;
const SHA64 = /^[0-9a-f]{64}$/;
const RUNTIME_ID = /^rtb_[0-9a-f]{64}$/;
const COORDINATE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$/;

export type JsonObject = Record<string, unknown>;

export interface ReleaseBinding {
  source_sha: string;
  runtime_bundle_id: string;
  deployment_id: string;
  manifest_sha256: string;
}

export interface ApiResult<T = JsonObject> {
  status: number;
  contentType: string;
  body: T;
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    const object = value as JsonObject;
    return `{${Object.keys(object).sort().map(
      (key) => `${JSON.stringify(key)}:${canonical(object[key])}`,
    ).join(',')}}`;
  }
  return JSON.stringify(value);
}

function rejectSensitive(value: unknown, location = 'report'): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => rejectSensitive(item, `${location}.${index}`));
    return;
  }
  if (value && typeof value === 'object') {
    Object.entries(value as JsonObject).forEach(([key, item]) => {
      const denial = item === true && key.endsWith('_denied');
      if (SENSITIVE_KEY.test(key) && !denial) {
        throw new Error(`sensitive browser evidence key is forbidden: ${location}.${key}`);
      }
      rejectSensitive(item, `${location}.${key}`);
    });
    return;
  }
  if (typeof value === 'string' && (EMAIL.test(value) || JWT.test(value))) {
    throw new Error(`sensitive browser evidence value is forbidden: ${location}`);
  }
}

export function exactKeys(value: unknown, keys: readonly string[], label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  const actual = Object.keys(value as JsonObject).sort();
  const expected = [...keys].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${label} fields are not exact`);
  }
  return value as JsonObject;
}

export function requiredString(value: unknown, label: string, maximum = 180): string {
  const clean = String(value || '').trim();
  if (!clean || clean.length > maximum) throw new Error(`${label} is invalid`);
  return clean;
}

export function requiredCoordinate(value: unknown, label: string): string {
  const clean = requiredString(value, label);
  if (!COORDINATE.test(clean)) throw new Error(`${label} is not a safe coordinate`);
  return clean;
}

export function requiredPositiveInteger(value: unknown, label: string, maximum = 86_400): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) {
    throw new Error(`${label} is invalid`);
  }
  return Number(value);
}

function runnerTemp(): string {
  const candidate = requiredString(process.env.RUNNER_TEMP, 'RUNNER_TEMP', 4096);
  return realpathSync(candidate);
}

function privatePath(candidate: string, label: string, mustExist: boolean): string {
  const root = runnerTemp();
  const resolved = mustExist ? realpathSync(candidate) : path.resolve(candidate);
  if (resolved === root || !resolved.startsWith(`${root}${path.sep}`)) {
    throw new Error(`${label} must stay below RUNNER_TEMP`);
  }
  return resolved;
}

function linkedActionRoot(): string {
  const workspace = realpathSync(
    requiredString(process.env.GITHUB_WORKSPACE, 'GITHUB_WORKSPACE', 4096),
  );
  const expected = realpathSync(path.join(workspace, 'release', 'linked-acceptance-actions'));
  const supplied = realpathSync(
    requiredString(process.env.LINKED_ACCEPTANCE_ACTION_ROOT, 'LINKED_ACCEPTANCE_ACTION_ROOT', 4096),
  );
  if (supplied !== expected) {
    throw new Error('linked acceptance action root must be the reviewed release fixture');
  }
  return supplied;
}

function linkedActionPath(root: string, relative: string, label: string): string {
  const resolved = realpathSync(path.join(root, relative));
  if (!resolved.startsWith(`${root}${path.sep}`)) {
    throw new Error(`${label} escaped the reviewed release fixture`);
  }
  return resolved;
}

export function readProtectedAction(phase: string): JsonObject {
  const root = linkedActionRoot();
  const candidate = linkedActionPath(root, `${phase}.json`, `${phase} action`);
  const stat = lstatSync(candidate);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 2 || stat.size > 1_000_000) {
    throw new Error(`${phase} action must be one bounded regular file`);
  }
  const payload = JSON.parse(readFileSync(candidate, 'utf8')) as unknown;
  const object = payload && typeof payload === 'object' && !Array.isArray(payload)
    ? payload as JsonObject
    : {};
  if (
    object.schema !== 'vowpic.linked-production-action.v1'
    || object.phase !== phase
  ) {
    throw new Error(`${phase} action identity is invalid`);
  }
  return object;
}

export function readSignedAcceptanceReport(
  candidate: string,
  expected: { schema: string; phase?: string },
): JsonObject {
  const resolved = privatePath(candidate, 'signed acceptance report', true);
  const stat = lstatSync(resolved);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 2 || stat.size > 1_000_000) {
    throw new Error('signed acceptance report must be one bounded regular file');
  }
  const report = JSON.parse(readFileSync(resolved, 'utf8')) as JsonObject;
  const signature = String(report.signature || '');
  const key = Buffer.from(String(process.env.ACCEPTANCE_EVIDENCE_SIGNING_KEY || ''), 'utf8');
  if (key.length < 32 || !/^hmac-sha256:[0-9a-f]{64}$/.test(signature)) {
    throw new Error('signed acceptance report signature is invalid');
  }
  const unsigned = { ...report };
  delete unsigned.signature;
  const wanted = createHmac('sha256', key).update(canonical(unsigned)).digest();
  const actual = Buffer.from(signature.slice('hmac-sha256:'.length), 'hex');
  if (actual.length !== wanted.length || !timingSafeEqual(actual, wanted)) {
    throw new Error('signed acceptance report signature mismatch');
  }
  if (
    unsigned.schema !== expected.schema
    || unsigned.passed !== true
    || (expected.phase !== undefined && unsigned.phase !== expected.phase)
  ) {
    throw new Error('signed acceptance report identity is invalid');
  }
  const binding = releaseBinding();
  for (const [name, value] of Object.entries(binding)) {
    if (unsigned[name] !== value) {
      throw new Error(`signed acceptance report ${name} mismatch`);
    }
  }
  rejectSensitive(unsigned);
  return unsigned;
}

export function resolveProtectedAsset(relative: unknown): string {
  const name = requiredString(relative, 'acceptance asset path', 256);
  if (!/^assets\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(name)) {
    throw new Error('acceptance asset path is not allowlisted');
  }
  const root = linkedActionRoot();
  const resolved = linkedActionPath(root, name, 'acceptance asset');
  const stat = lstatSync(resolved);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 1 || stat.size > 15 * 1024 * 1024) {
    throw new Error('acceptance asset must be one bounded regular file');
  }
  return resolved;
}

export function releaseBinding(): ReleaseBinding {
  const binding = {
    source_sha: requiredString(process.env.PRODUCTION_SOURCE_SHA, 'Production source SHA').toLowerCase(),
    runtime_bundle_id: requiredString(
      process.env.PRODUCTION_RUNTIME_BUNDLE_ID,
      'Production runtime bundle ID',
    ).toLowerCase(),
    deployment_id: requiredString(process.env.PRODUCTION_DEPLOYMENT_ID, 'Production deployment ID'),
    manifest_sha256: requiredString(
      process.env.PRODUCTION_MANIFEST_SHA256,
      'Production manifest SHA-256',
    ).toLowerCase(),
  };
  if (
    !SHA40.test(binding.source_sha)
    || !RUNTIME_ID.test(binding.runtime_bundle_id)
    || !COORDINATE.test(binding.deployment_id)
    || !SHA64.test(binding.manifest_sha256)
  ) {
    throw new Error('Production release binding is invalid');
  }
  return binding;
}

export function userSubjectHmac(userId: string): string {
  const key = Buffer.from(String(process.env.ACCEPTANCE_IDENTITY_HMAC_KEY || ''), 'utf8');
  if (key.length < 32) throw new Error('ACCEPTANCE_IDENTITY_HMAC_KEY is missing or too short');
  return createHmac('sha256', key).update(requiredCoordinate(userId, 'user ID')).digest('hex');
}

async function csrfToken(page: Page): Promise<string> {
  const cookie = (await page.context().cookies()).find((item) => item.name === 'vowpic_csrf');
  return requiredString(cookie?.value, 'CSRF cookie', 4096);
}

export async function api<T = JsonObject>(
  page: Page,
  endpoint: string,
  options: {
    method?: 'GET' | 'POST' | 'DELETE';
    body?: unknown;
    headers?: Record<string, string>;
    expected?: readonly number[];
  } = {},
): Promise<ApiResult<T>> {
  if (!endpoint.startsWith('/api/v1/')) throw new Error('acceptance API path is not allowlisted');
  const method = options.method || 'GET';
  const headers: Record<string, string> = { ...(options.headers || {}) };
  if (method !== 'GET') {
    headers['X-CSRF-Token'] = await csrfToken(page);
    headers.Origin = requiredString(process.env.PRODUCTION_BASE_URL, 'Production base URL', 2048);
  }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const result = await page.evaluate(async ({ endpoint: url, method: verb, headers: requestHeaders, body }) => {
    const response = await fetch(url, {
      method: verb,
      credentials: 'include',
      headers: requestHeaders,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const contentType = response.headers.get('content-type') || '';
    const raw = await response.text();
    let parsed: unknown = raw;
    if (contentType.includes('application/json') && raw) parsed = JSON.parse(raw);
    return { status: response.status, contentType, body: parsed };
  }, { endpoint, method, headers, body: options.body });
  const expected = options.expected || [200];
  expect(expected, `${method} ${endpoint} accepted status`).toContain(result.status);
  return result as ApiResult<T>;
}

export async function uploadProtectedAsset(
  page: Page,
  filePath: string,
  fieldName = 'file',
): Promise<JsonObject> {
  const csrf = await csrfToken(page);
  const response = await page.request.post('/api/v1/media/uploads', {
    headers: {
      'X-CSRF-Token': csrf,
      Origin: requiredString(process.env.PRODUCTION_BASE_URL, 'Production base URL', 2048),
    },
    multipart: {
      [fieldName]: {
        name: path.basename(filePath),
        mimeType: 'image/jpeg',
        buffer: readFileSync(filePath),
      },
    },
    failOnStatusCode: false,
  });
  expect(response.status(), 'protected upload status').toBe(200);
  const body = await response.json() as unknown;
  return exactKeys(body, ['batch_id', 'assets'], 'protected upload response');
}

export async function completeGoogleLogin(page: Page, identityEmail: string): Promise<void> {
  const base = new URL(
    requiredString(process.env.PRODUCTION_BASE_URL, 'Production base URL', 2048),
  );
  await page.goto('/pages/auth/login');
  const button = page.locator('button.google-button');
  await expect(button).toBeVisible();
  await button.click();
  await page.waitForURL(
    (url) => url.origin === base.origin || url.hostname.endsWith('google.com'),
    { timeout: 60_000 },
  );
  if (new URL(page.url()).hostname.endsWith('google.com')) {
    const account = page.getByText(
      requiredString(identityEmail, 'Google identity email', 320),
      { exact: false },
    ).first();
    if (await account.isVisible({ timeout: 15_000 }).catch(() => false)) {
      await account.click();
    }
    const continueButton = page.getByRole('button', { name: /continue|allow/i }).last();
    if (await continueButton.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await continueButton.click();
    }
  }
  await page.waitForURL(
    (url) => url.origin === base.origin && !url.pathname.endsWith('/auth/login'),
    { timeout: 90_000 },
  );
}

export async function pollJson<T>(
  page: Page,
  endpoint: string,
  predicate: (value: T) => boolean,
  timeoutSeconds: number,
  label: string,
): Promise<T> {
  const deadline = Date.now() + requiredPositiveInteger(timeoutSeconds, `${label} timeout`) * 1000;
  let lastStatus = 0;
  while (Date.now() < deadline) {
    const result = await api<T>(page, endpoint, { expected: [200, 404, 409, 503] });
    lastStatus = result.status;
    if (result.status === 200 && predicate(result.body)) return result.body;
    await page.waitForTimeout(2_000);
  }
  throw new Error(`${label} did not reach its required state; last HTTP status ${lastStatus}`);
}

export function idempotencyKey(prefix: string): string {
  return `${requiredCoordinate(prefix, 'idempotency prefix')}-${randomUUID()}`;
}

export function writeBrowserObservation(
  phase: string,
  payload: JsonObject,
  outputEnvironment = 'LINKED_ACCEPTANCE_BROWSER_REPORT',
): void {
  const key = Buffer.from(String(process.env.ACCEPTANCE_EVIDENCE_SIGNING_KEY || ''), 'utf8');
  if (key.length < 32) throw new Error('ACCEPTANCE_EVIDENCE_SIGNING_KEY is missing or too short');
  const output = privatePath(
    requiredString(process.env[outputEnvironment], outputEnvironment, 4096),
    'browser observation output',
    false,
  );
  const unsigned = {
    schema: 'vowpic.acceptance-browser-observation.v1',
    phase,
    passed: true,
    ...releaseBinding(),
    ...payload,
  };
  rejectSensitive(unsigned);
  const signature = createHmac('sha256', key).update(canonical(unsigned)).digest('hex');
  writeFileSync(
    output,
    `${canonical({ ...unsigned, signature: `hmac-sha256:${signature}` })}\n`,
    { encoding: 'utf8', flag: 'wx', mode: 0o600 },
  );
  chmodSync(output, 0o600);
}
