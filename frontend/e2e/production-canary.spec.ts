import { createHash, createHmac } from 'node:crypto';
import { chmodSync, lstatSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

const capability = String(process.env.PRODUCTION_CANARY_CAPABILITY || '').trim();
const outputPath = String(process.env.PRODUCTION_CANARY_OUTPUT_PATH || '').trim();
const inputRoot = String(process.env.PRODUCTION_CANARY_INPUT_ROOT || '').trim();
const signingKey = String(process.env.ACCEPTANCE_EVIDENCE_SIGNING_KEY || '');
const baseURL = String(process.env.PRODUCTION_BASE_URL || '').trim().replace(/\/$/, '');

const routes: Record<string, { method: string; pattern: RegExp }> = {
  google_auth: { method: 'GET', pattern: /^\/api\/v1\/auth\/me$/ },
  authenticated_upload: { method: 'POST', pattern: /^\/api\/v1\/media\/uploads$/ },
  credit_pack_checkout: { method: 'POST', pattern: /^\/api\/v1\/payments\/checkout$/ },
  subscription_billing: { method: 'POST', pattern: /^\/api\/v1\/subscriptions\/(checkout|cancel)$/ },
  generation: { method: 'POST', pattern: /^\/api\/v1\/orders\/create$/ },
  private_download: { method: 'GET', pattern: /^\/api\/v1\/(orders\/[0-9a-f-]+\/assets\/[0-9a-f-]+\/download|media\/grants\/[A-Za-z0-9_-]+)$/ },
  partner_invite: { method: 'POST', pattern: /^\/api\/v1\/partner-invites$/ },
};

test.skip(process.env.RUN_PRODUCTION_E2E !== '1', 'Production canary is protected-only');

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function sha256(value: Buffer | string) {
  return createHash('sha256').update(value).digest('hex');
}

function privatePath(candidate: string, label: string) {
  const root = realpathSync(String(process.env.RUNNER_TEMP || ''));
  const resolved = path.resolve(candidate);
  expect(resolved.startsWith(`${root}${path.sep}`), `${label} must stay in RUNNER_TEMP`).toBeTruthy();
  return resolved;
}

test(`@capability:${capability} exact live Production capability probe`, async ({ page }) => {
  expect(process.env.RUN_PRODUCTION_E2E).toBe('1');
  expect(routes[capability], 'capability must be allowlisted').toBeTruthy();
  expect(signingKey.length).toBeGreaterThanOrEqual(32);
  expect(baseURL).toMatch(/^https:\/\/[^/]+$/);
  const inputPath = privatePath(path.join(inputRoot, `${capability}.json`), 'canary input');
  const stat = lstatSync(inputPath);
  expect(stat.isFile()).toBeTruthy();
  if (process.platform !== 'win32') expect(stat.mode & 0o077).toBe(0);
  const raw = readFileSync(inputPath);
  expect(raw.length).toBeGreaterThan(2);
  expect(raw.length).toBeLessThan(1_000_000);
  const input = JSON.parse(raw.toString('utf8')) as Record<string, any>;
  expect(Object.keys(input).sort()).toEqual([
    'capability', 'expected_status', 'headers', 'json_body', 'method', 'multipart',
    'path', 'required_response_keys', 'schema',
  ].sort());
  expect(input.schema).toBe('vowpic.production-capability-canary-input.v1');
  expect(input.capability).toBe(capability);
  expect(input.method).toBe(routes[capability].method);
  expect(String(input.path)).toMatch(routes[capability].pattern);
  expect(Number.isInteger(input.expected_status)).toBeTruthy();
  expect(input.expected_status).toBeGreaterThanOrEqual(200);
  expect(input.expected_status).toBeLessThan(300);
  expect(Array.isArray(input.required_response_keys)).toBeTruthy();
  expect(Object.keys(input.headers || {}).every((key) => key.toLowerCase() === 'idempotency-key')).toBeTruthy();

  await page.goto('/pages/account/index');
  const cookies = await page.context().cookies();
  const csrf = cookies.find((item) => item.name === 'vowpic_csrf')?.value || '';
  if (input.method !== 'GET') expect(csrf).toBeTruthy();
  const headers = { ...(input.headers || {}) } as Record<string, string>;
  if (input.method !== 'GET') headers['X-CSRF-Token'] = csrf;

  let multipart: Record<string, any> | undefined;
  if (input.multipart !== null) {
    expect(input.json_body).toBeNull();
    expect(input.multipart && typeof input.multipart === 'object').toBeTruthy();
    multipart = {};
    for (const [name, value] of Object.entries(input.multipart)) {
      if (value && typeof value === 'object' && 'file_path' in (value as Record<string, unknown>)) {
        const item = value as Record<string, string>;
        const candidate = path.isAbsolute(item.file_path)
          ? item.file_path
          : path.join(inputRoot, item.file_path);
        const filePath = privatePath(candidate, 'canary upload');
        multipart[name] = {
          name: item.name,
          mimeType: item.mime_type,
          buffer: readFileSync(filePath),
        };
      } else {
        multipart[name] = String(value);
      }
    }
  }
  const response = await page.request.fetch(String(input.path), {
    method: input.method,
    headers,
    data: input.json_body === null ? undefined : input.json_body,
    multipart,
    failOnStatusCode: false,
  });
  expect(response.status()).toBe(input.expected_status);
  const responseBytes = await response.body();
  let responseJson: Record<string, unknown> | null = null;
  try {
    responseJson = JSON.parse(responseBytes.toString('utf8'));
  } catch {
    responseJson = null;
  }
  for (const key of input.required_response_keys) {
    expect(responseJson && Object.hasOwn(responseJson, key), `response key ${key}`).toBeTruthy();
  }

  const unsigned = {
    schema: 'vowpic.production-capability-canary.v1',
    passed: true,
    capability,
    source_sha: String(process.env.PRODUCTION_SOURCE_SHA || ''),
    runtime_bundle_id: String(process.env.PRODUCTION_RUNTIME_BUNDLE_ID || ''),
    deployment_id: String(process.env.PRODUCTION_DEPLOYMENT_ID || ''),
    manifest_sha256: String(process.env.PRODUCTION_MANIFEST_SHA256 || ''),
    before_snapshot_hash: String(process.env.PRODUCTION_CANARY_BEFORE_SNAPSHOT_HASH || ''),
    after_snapshot_hash: String(process.env.PRODUCTION_CANARY_AFTER_SNAPSHOT_HASH || ''),
    request_input_sha256: sha256(raw),
    request_path_sha256: sha256(String(input.path)),
    response_sha256: sha256(responseBytes),
    response_status: response.status(),
    produced_at: new Date().toISOString(),
  };
  expect(unsigned.source_sha).toMatch(/^[0-9a-f]{40}$/);
  expect(unsigned.runtime_bundle_id).toMatch(/^rtb_[0-9a-f]{64}$/);
  expect(unsigned.manifest_sha256).toMatch(/^[0-9a-f]{64}$/);
  expect(unsigned.before_snapshot_hash).toMatch(/^[0-9a-f]{64}$/);
  expect(unsigned.after_snapshot_hash).toMatch(/^[0-9a-f]{64}$/);
  const signature = createHmac('sha256', signingKey).update(canonical(unsigned)).digest('hex');
  const target = privatePath(outputPath, 'canary output');
  writeFileSync(target, `${canonical({ ...unsigned, signature: `hmac-sha256:${signature}` })}\n`, {
    encoding: 'utf8', flag: 'wx', mode: 0o600,
  });
  chmodSync(target, 0o600);
});
