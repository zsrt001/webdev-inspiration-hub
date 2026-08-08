import { createHmac } from 'node:crypto';
import { chmodSync, realpathSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { expect, request, test, type Page } from '@playwright/test';

import { preparePreauthenticatedGoogleOAuth } from './helpers/google-oauth-acceptance';

const previewRun = process.env.RUN_PREVIEW_E2E === '1';
const productionRun = process.env.RUN_PRODUCTION_E2E === '1';
const protectedRun = previewRun || productionRun;
const prefix = productionRun ? 'PRODUCTION' : 'PREVIEW';
const baseURL = String(process.env[`${prefix}_BASE_URL`] || '').trim().replace(/\/$/, '');
const identityEmail = String(process.env[`${prefix}_GOOGLE_EMAIL`] || '').trim();
const sourceSha = String(process.env[`${prefix}_SOURCE_SHA`] || '').trim().toLowerCase();
const runtimeBundleId = String(process.env[`${prefix}_RUNTIME_BUNDLE_ID`] || '').trim().toLowerCase();
const deploymentId = String(process.env[`${prefix}_DEPLOYMENT_ID`] || '').trim();
const manifestSha256 = String(process.env[`${prefix}_MANIFEST_SHA256`] || '').trim().toLowerCase();
const inputPath = String(process.env.AUTH_ACCEPTANCE_INPUT_PATH || '').trim();
const hmacKey = String(process.env.ACCEPTANCE_IDENTITY_HMAC_KEY || '');

test.skip(!protectedRun, 'protected Google acceptance only runs in Preview or Production mode');

function hmacCoordinate(value: string, label: string) {
  expect(value, `${label} must exist before it can be bound`).toBeTruthy();
  return `${label}:${createHmac('sha256', hmacKey).update(value).digest('hex')}`;
}

async function cookieValue(page: Page, name: string) {
  const cookies = await page.context().cookies();
  return cookies.find((cookie) => cookie.name === name)?.value || '';
}

async function completeGoogleLogin(page: Page) {
  await preparePreauthenticatedGoogleOAuth(page, identityEmail);
  await page.goto('/pages/auth/login');
  await expect(page.locator('.google-button')).toBeVisible();
  await page.locator('.google-button').click();
  await page.waitForURL(
    (url) => (
      url.hostname.endsWith('google.com')
      || (url.origin === baseURL && !url.pathname.endsWith('/auth/login'))
    ),
    { timeout: 60_000 },
  );
  if (new URL(page.url()).hostname.endsWith('google.com')) {
    const escapedEmail = identityEmail.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
    const account = page.locator(`[data-identifier="${escapedEmail}"]`);
    if (await account.isVisible({ timeout: 15_000 }).catch(() => false)) {
      expect(await account.count(), 'Google account selector must be unambiguous').toBe(1);
      await account.click();
    }
    const continueButton = page.getByRole('button', { name: /continue|allow/i }).last();
    if (await continueButton.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await continueButton.click();
    }
  }
  try {
    await page.waitForURL((url) => url.origin === baseURL && !url.pathname.endsWith('/auth/login'), {
      timeout: 90_000,
    });
  } catch (error) {
    const current = new URL(page.url());
    const pageState = await page.evaluate(() => ({
      account_rows: document.querySelectorAll('[data-identifier]').length,
      email_field: Boolean(document.querySelector('input[type="email"]')),
      password_field: Boolean(document.querySelector('input[type="password"]')),
      verification_code_field: Boolean(
        document.querySelector('input[autocomplete="one-time-code"], input[type="tel"]'),
      ),
    }));
    throw new Error(
      `GOOGLE_AUTH_RETURN_TIMEOUT: ${JSON.stringify({
        hostname: current.hostname,
        pathname: current.pathname,
        ...pageState,
      })}`,
      { cause: error },
    );
  }
}

async function currentUser(page: Page) {
  return page.evaluate(async () => {
    const response = await fetch('/api/v1/auth/me', { credentials: 'include' });
    return { status: response.status, body: await response.json() };
  });
}

async function welcomeSnapshot(page: Page) {
  return page.evaluate(async () => {
    const [balanceResponse, transactionsResponse] = await Promise.all([
      fetch('/api/v1/credits/balance', { credentials: 'include' }),
      fetch('/api/v1/credits/transactions', { credentials: 'include' }),
    ]);
    return {
      balanceStatus: balanceResponse.status,
      balance: await balanceResponse.json(),
      transactionsStatus: transactionsResponse.status,
      transactions: await transactionsResponse.json(),
    };
  });
}

async function expectLegacyIdentityDenied(headers: Record<string, string>) {
  const client = await request.newContext({ baseURL, extraHTTPHeaders: headers });
  try {
    const response = await client.get('/api/v1/auth/me');
    expect([401, 403]).toContain(response.status());
  } finally {
    await client.dispose();
  }
}

function writeAcceptanceInput(payload: object) {
  expect(inputPath, 'AUTH_ACCEPTANCE_INPUT_PATH is required').toBeTruthy();
  const runnerTemp = realpathSync(String(process.env.RUNNER_TEMP || ''));
  const resolvedOutput = path.resolve(inputPath);
  expect(resolvedOutput.startsWith(`${runnerTemp}${path.sep}`)).toBeTruthy();
  writeFileSync(resolvedOutput, `${JSON.stringify(payload)}\n`, {
    encoding: 'utf8',
    flag: 'wx',
    mode: 0o600,
  });
  chmodSync(resolvedOutput, 0o600);
}

test('ordinary Google auth rotates, revokes, relogs, and rejects every legacy identity path', async ({ page }) => {
  expect(hmacKey.length).toBeGreaterThanOrEqual(32);
  expect(sourceSha).toMatch(/^[0-9a-f]{40}$/);
  expect(runtimeBundleId).toMatch(/^rtb_[0-9a-f]{64}$/);
  expect(deploymentId).toMatch(/^[A-Za-z0-9_.:-]{1,180}$/);
  expect(manifestSha256).toMatch(/^[0-9a-f]{64}$/);

  const applicationAuthorizationHeaders: string[] = [];
  page.on('request', (outgoing) => {
    if (new URL(outgoing.url()).origin !== baseURL) return;
    const authorization = outgoing.headers().authorization;
    if (authorization) applicationAuthorizationHeaders.push(authorization);
  });

  await completeGoogleLogin(page);
  expect(page.url()).not.toContain('access_token');
  expect(page.url()).not.toContain('#');
  const firstUser = await currentUser(page);
  expect(firstUser.status).toBe(200);
  expect(String(firstUser.body.email || '').toLowerCase()).toBe(identityEmail.toLowerCase());
  const userId = String(firstUser.body.id || '');
  expect(userId).toBeTruthy();

  const firstWelcome = await welcomeSnapshot(page);
  expect(firstWelcome.balanceStatus).toBe(200);
  expect(firstWelcome.transactionsStatus).toBe(200);
  expect(firstWelcome.transactions.transactions).toHaveLength(1);
  expect(firstWelcome.transactions.transactions[0]).toMatchObject({
    transaction_type: 'WELCOME_BONUS',
  });

  const firstAccess = await cookieValue(page, 'vowpic_access');
  const firstRefresh = await cookieValue(page, 'vowpic_refresh');
  const firstCsrf = await cookieValue(page, 'vowpic_csrf');
  expect(firstAccess).toBeTruthy();
  expect(firstRefresh).toBeTruthy();
  expect(firstCsrf).toBeTruthy();

  const refreshStatus = await page.evaluate(async (csrf) => {
    const response = await fetch('/api/v1/auth/refresh', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': csrf },
    });
    return response.status;
  }, firstCsrf);
  expect(refreshStatus).toBe(204);
  const rotatedAccess = await cookieValue(page, 'vowpic_access');
  const rotatedRefresh = await cookieValue(page, 'vowpic_refresh');
  const rotatedCsrf = await cookieValue(page, 'vowpic_csrf');
  expect(rotatedAccess).not.toBe(firstAccess);
  expect(rotatedRefresh).not.toBe(firstRefresh);
  expect(rotatedCsrf).not.toBe(firstCsrf);

  const unsignedLegacyJwt = [
    Buffer.from('{"alg":"none","typ":"JWT"}').toString('base64url'),
    Buffer.from('{"sub":"legacy-browser"}').toString('base64url'),
    '',
  ].join('.');
  await expectLegacyIdentityDenied({ Authorization: `Bearer ${unsignedLegacyJwt}` });
  await expectLegacyIdentityDenied({ 'X-User-OpenID': 'legacy-openid-owner' });
  await expectLegacyIdentityDenied({ 'X-Visitor-Id': 'legacy-visitor-owner' });
  await expectLegacyIdentityDenied({
    'X-Forwarded-User': 'spoofed-user',
    'X-Forwarded-Email': 'spoofed-at-invalid',
  });
  await expectLegacyIdentityDenied({ 'X-Admin-Token': 'browser-supplied-admin-value' });

  const logoutStatus = await page.evaluate(async (csrf) => {
    const response = await fetch('/api/v1/auth/logout', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': csrf },
    });
    return response.status;
  }, rotatedCsrf);
  expect(logoutStatus).toBe(204);
  expect((await currentUser(page)).status).toBe(401);

  await completeGoogleLogin(page);
  const secondUser = await currentUser(page);
  expect(secondUser.status).toBe(200);
  expect(String(secondUser.body.id || '')).toBe(userId);
  expect(await welcomeSnapshot(page)).toEqual(firstWelcome);
  const secondRefresh = await cookieValue(page, 'vowpic_refresh');
  const secondCsrf = await cookieValue(page, 'vowpic_csrf');
  expect(secondRefresh).toBeTruthy();
  expect(applicationAuthorizationHeaders).toEqual([]);

  const browserStorage = await page.evaluate(() => ({
    local: Object.fromEntries(Object.entries(localStorage)),
    session: Object.fromEntries(Object.entries(sessionStorage)),
  }));
  expect(JSON.stringify(browserStorage)).not.toMatch(
    /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/,
  );

  writeAcceptanceInput({
    schema: 'vowpic.commercial-acceptance-input.v1',
    phase: 'first-login-and-auth-security',
    source_sha: sourceSha,
    runtime_bundle_id: runtimeBundleId,
    deployment_id: deploymentId,
    manifest_sha256: manifestSha256,
    user_subject_hmac_sha256: createHmac('sha256', hmacKey).update(userId).digest('hex'),
    currency: 'USD',
    cost_minor_units: 0,
    cost_cap_minor_units: 1,
    assertions: {
      ordinary_google_user: true,
      first_login: true,
      refresh_rotated: true,
      logout_revoked: true,
      post_logout_denied: true,
      second_login_same_account: true,
      legacy_jwt_denied: true,
      legacy_openid_header_denied: true,
      legacy_visitor_header_denied: true,
      forwarded_identity_spoof_denied: true,
      browser_admin_token_denied: true,
      no_admin_or_test_bypass: true,
    },
    links: {
      user_id: userId,
      first_session_id: hmacCoordinate(firstRefresh, 'session-first'),
      rotated_session_id: hmacCoordinate(rotatedRefresh, 'session-rotated'),
      second_session_id: hmacCoordinate(secondRefresh, 'session-second'),
    },
  });

  const finalLogout = await page.evaluate(async (csrf) => {
    const response = await fetch('/api/v1/auth/logout', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': csrf },
    });
    return response.status;
  }, secondCsrf);
  expect(finalLogout).toBe(204);
});
