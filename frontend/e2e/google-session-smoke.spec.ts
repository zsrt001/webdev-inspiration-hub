import { expect, request, test, type Page } from '@playwright/test';
import { writeFileSync } from 'node:fs';

const baseURL = String(process.env.PREVIEW_BASE_URL || '').trim().replace(/\/$/, '');
const identityEmail = String(process.env.PREVIEW_GOOGLE_EMAIL || '').trim();
const secondIdentityEmail = String(process.env.PREVIEW_SECOND_GOOGLE_EMAIL || '').trim();
const secondStorageState = String(
  process.env.PREVIEW_SECOND_GOOGLE_STORAGE_STATE_PATH || '',
).trim();
const privateMediaReportPath = String(process.env.PREVIEW_PRIVATE_MEDIA_REPORT_PATH || '').trim();
const failureBoundary = String(process.env.PREVIEW_FAILURE_BOUNDARY || 'none').trim();

function injectFailure(
  boundary: 'login' | 'upload' | 'owner-read' | 'cross-user' | 'delete' | 'refresh' | 'logout',
) {
  if (failureBoundary === boundary) {
    throw new Error(`PREVIEW_FAILURE_INJECTION:${boundary}`);
  }
}

async function cookieValue(page: Page, name: string) {
  const cookies = await page.context().cookies();
  return cookies.find((cookie: { name: string }) => cookie.name === name)?.value || '';
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

async function completeGoogleLogin(page: Page, email = identityEmail) {
  if (!/^[^\s"\\]+@[^\s"\\]+$/.test(email)) {
    throw new Error('GOOGLE_IDENTITY_EMAIL_INVALID');
  }
  await page.goto('/pages/auth/login');
  const googleButton = page.locator('.google-button');
  try {
    await expect(googleButton).toBeVisible({ timeout: 30_000 });
  } catch (error) {
    const availability = await page.evaluate(async () => {
      try {
        const response = await fetch('/api/v1/ops/public_config', {
          credentials: 'include',
          signal: AbortSignal.timeout(15_000),
        });
        const payload = await response.json().catch(() => ({}));
        return {
          status: response.status,
          google_auth: payload?.capabilities?.google_auth === true,
          google_oauth_enabled: payload?.auth?.google_oauth_enabled === true,
          supabase_url_present: Boolean(payload?.auth?.supabase_url),
          publishable_key_present: Boolean(payload?.auth?.supabase_publishable_key),
          page_path: window.location.pathname,
        };
      } catch (requestError) {
        return {
          request_error: requestError instanceof Error
            ? requestError.name
            : 'UnknownError',
          page_path: window.location.pathname,
        };
      }
    });
    throw new Error(
      `GOOGLE_ENTRY_UNAVAILABLE: ${JSON.stringify(availability)}`,
      { cause: error },
    );
  }
  await googleButton.click();
  await page.waitForURL(
    (url) => (
      url.hostname.endsWith('google.com')
      || (url.origin === baseURL && !url.pathname.endsWith('/auth/login'))
    ),
    { timeout: 60_000 },
  );
  if (new URL(page.url()).hostname.endsWith('google.com')) {
    const escapedEmail = email.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
    const account = page.locator(`[data-identifier="${escapedEmail}"]`);
    const accountVisible = await account.waitFor({ state: 'visible', timeout: 15_000 })
      .then(() => true)
      .catch(() => false);
    if (accountVisible) {
      const accountCount = await account.count();
      if (accountCount !== 1) {
        throw new Error('GOOGLE_ACCOUNT_SELECTOR_AMBIGUOUS');
      }
      await account.click();
    }
    const consentButtons = page.getByRole('button', {
      name: /^(continue|allow|confirm|继续|允许|同意|确认)$/i,
    });
    const consentVisible = await consentButtons.last()
      .waitFor({ state: 'visible', timeout: 5_000 })
      .then(() => true)
      .catch(() => false);
    if (consentVisible) {
      const consentButtonCount = await consentButtons.count();
      for (let index = consentButtonCount - 1; index >= 0; index -= 1) {
        const candidate = consentButtons.nth(index);
        if (await candidate.isVisible().catch(() => false)) {
          await candidate.click();
          break;
        }
      }
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

test('real Google PKCE and private media enforce two-user isolation', async ({ page, browser }) => {
  expect(secondIdentityEmail).toBeTruthy();
  expect(secondStorageState).toBeTruthy();
  expect(privateMediaReportPath).toBeTruthy();
  const appAuthorizationHeaders: string[] = [];
  page.on('request', (outgoing) => {
    if (new URL(outgoing.url()).origin !== baseURL) return;
    const authorization = outgoing.headers()['authorization'];
    if (authorization) appAuthorizationHeaders.push(authorization);
  });
  await completeGoogleLogin(page);
  expect(page.url()).not.toContain('access_token');
  expect(page.url()).not.toContain('#');

  const meBefore = await page.evaluate(async () => {
    const response = await fetch('/api/v1/auth/me', { credentials: 'include' });
    return { status: response.status, body: await response.json() };
  });
  expect(meBefore.status).toBe(200);
  expect(String(meBefore.body.email || '').toLowerCase()).toBe(identityEmail.toLowerCase());
  const firstWelcome = await welcomeSnapshot(page);
  expect(firstWelcome.balanceStatus).toBe(200);
  expect(firstWelcome.transactionsStatus).toBe(200);
  expect(firstWelcome.balance.balance).toBe(2);
  expect(firstWelcome.transactions.transactions).toHaveLength(1);
  expect(firstWelcome.transactions.transactions[0]).toMatchObject({
    transaction_type: 'WELCOME_BONUS',
    amount: 2,
  });
  injectFailure('login');

  const oldAccess = await cookieValue(page, 'vowpic_access');
  const oldRefresh = await cookieValue(page, 'vowpic_refresh');
  const oldCsrf = await cookieValue(page, 'vowpic_csrf');
  expect(oldAccess).toBeTruthy();
  expect(oldRefresh).toBeTruthy();
  expect(oldCsrf).toBeTruthy();

  const upload = await page.evaluate(async ({ csrf, imageBase64 }) => {
    const bytes = Uint8Array.from(atob(imageBase64), (value) => value.charCodeAt(0));
    const form = new FormData();
    form.append('file', new Blob([bytes], { type: 'image/png' }), 'preview-smoke.png');
    const response = await fetch('/api/v1/media/uploads', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': csrf },
      body: form,
    });
    return { status: response.status, body: await response.json() };
  }, {
    csrf: oldCsrf,
    imageBase64: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR42mNk+M/wHwAEAQH/7pVxWQAAAABJRU5ErkJggg==',
  });
  expect(upload.status).toBe(200);
  expect(upload.body).toMatchObject({
    batch_id: expect.any(String),
    assets: [expect.objectContaining({ asset_id: expect.any(String) })],
  });
  const assetId = String(upload.body.assets[0].asset_id);
  injectFailure('upload');

  const ownerRead = await page.evaluate(async (id) => {
    const response = await fetch(`/api/v1/media/${id}`, { credentials: 'include' });
    const bytes = await response.arrayBuffer();
    return {
      status: response.status,
      bytes: bytes.byteLength,
      contentType: response.headers.get('content-type'),
      cacheControl: response.headers.get('cache-control'),
    };
  }, assetId);
  expect(ownerRead).toMatchObject({
    status: 200,
    contentType: 'image/jpeg',
    cacheControl: expect.stringContaining('private'),
  });
  expect(ownerRead.bytes).toBeGreaterThan(0);
  injectFailure('owner-read');

  const secondContext = await browser.newContext({ storageState: secondStorageState });
  try {
    const secondPage = await secondContext.newPage();
    await completeGoogleLogin(secondPage, secondIdentityEmail);
    const secondMe = await secondPage.evaluate(async () => {
      const response = await fetch('/api/v1/auth/me', { credentials: 'include' });
      return { status: response.status, body: await response.json() };
    });
    expect(secondMe.status).toBe(200);
    expect(String(secondMe.body.email || '').toLowerCase()).toBe(secondIdentityEmail.toLowerCase());
    const crossUser = await secondPage.evaluate(async (id) => {
      const response = await fetch(`/api/v1/media/${id}`, { credentials: 'include' });
      return response.status;
    }, assetId);
    expect(crossUser, 'cross-user private asset read must be denied').toBe(403);
    injectFailure('cross-user');
  } finally {
    await secondContext.close();
  }

  const deletion = await page.evaluate(async ({ id, csrf }) => {
    const response = await fetch(`/api/v1/media/${id}`, {
      method: 'DELETE',
      credentials: 'include',
      headers: { 'X-CSRF-Token': csrf },
    });
    return { status: response.status, body: await response.json() };
  }, { id: assetId, csrf: oldCsrf });
  expect(deletion.status).toBe(200);
  expect(deletion.body.asset_id).toBe(assetId);
  const deletedRead = await page.evaluate(async (id) => {
    const response = await fetch(`/api/v1/media/${id}`, { credentials: 'include' });
    return response.status;
  }, assetId);
  expect(deletedRead).toBe(404);
  writeFileSync(privateMediaReportPath, `${JSON.stringify({
    schema: 'vowpic.preview-private-media.v1',
    batch_id: upload.body.batch_id,
    asset_id: assetId,
    owner_read_status: ownerRead.status,
    cross_user_status: 403,
    delete_status: deletion.status,
    deleted_read_status: deletedRead,
  }, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  injectFailure('delete');

  const refreshStatus = await page.evaluate(async (csrf) => {
    const response = await fetch('/api/v1/auth/refresh', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': csrf },
    });
    return response.status;
  }, oldCsrf);
  expect(refreshStatus).toBe(204);

  const newAccess = await cookieValue(page, 'vowpic_access');
  const newRefresh = await cookieValue(page, 'vowpic_refresh');
  const newCsrf = await cookieValue(page, 'vowpic_csrf');
  expect(newAccess).not.toBe(oldAccess);
  expect(newRefresh).not.toBe(oldRefresh);
  expect(newCsrf).not.toBe(oldCsrf);

  const meAfterRefresh = await page.evaluate(async () => {
    const response = await fetch('/api/v1/auth/me', { credentials: 'include' });
    return response.status;
  });
  expect(meAfterRefresh).toBe(200);
  injectFailure('refresh');

  const browserStorage = await page.evaluate(() => ({
    local: Object.fromEntries(Object.entries(localStorage)),
    session: Object.fromEntries(Object.entries(sessionStorage)),
  }));
  const serializedStorage = JSON.stringify(browserStorage);
  expect(serializedStorage).not.toContain('access_token');
  expect(serializedStorage).not.toMatch(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
  expect(appAuthorizationHeaders).toEqual([]);

  const logoutStatus = await page.evaluate(async (csrf) => {
    const response = await fetch('/api/v1/auth/logout', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': csrf },
    });
    return response.status;
  }, newCsrf);
  expect(logoutStatus).toBe(204);

  const meAfterLogout = await page.evaluate(async () => {
    const response = await fetch('/api/v1/auth/me', { credentials: 'include' });
    return response.status;
  });
  expect(meAfterLogout).toBe(401);
  injectFailure('logout');

  await completeGoogleLogin(page);
  const secondWelcome = await welcomeSnapshot(page);
  expect(secondWelcome).toEqual(firstWelcome);
  const secondCsrf = await cookieValue(page, 'vowpic_csrf');
  const secondLogout = await page.evaluate(async (csrf) => {
    const response = await fetch('/api/v1/auth/logout', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': csrf },
    });
    return response.status;
  }, secondCsrf);
  expect(secondLogout).toBe(204);

  const replayClient = await request.newContext({
    baseURL,
    extraHTTPHeaders: {
      Origin: baseURL,
      Cookie: `vowpic_refresh=${oldRefresh}; vowpic_csrf=${oldCsrf}`,
      'X-CSRF-Token': oldCsrf,
    },
  });
  const replay = await replayClient.post('/api/v1/auth/refresh');
  expect(replay.status()).toBe(401);
  await replayClient.dispose();
});
