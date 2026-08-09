import { expect, test } from '@playwright/test';
import { writeFileSync } from 'node:fs';

const baseURL = String(process.env.PREVIEW_BASE_URL || '').trim().replace(/\/$/, '');
const reportPath = String(process.env.PREVIEW_GOOGLE_HANDOFF_REPORT_PATH || '').trim();
const sourceSha = String(process.env.SOURCE_SHA || '').trim().toLowerCase();
const workflowRunId = String(process.env.GITHUB_RUN_ID || '').trim();
const workflowAttempt = String(process.env.GITHUB_RUN_ATTEMPT || '').trim();
const runtimeBundleId = String(process.env.PREVIEW_RUNTIME_BUNDLE_ID || '').trim();
const deploymentId = String(process.env.PREVIEW_API_DEPLOYMENT_ID || '').trim();

const expectedCapabilities = [
  'authenticated_upload',
  'credit_pack_checkout',
  'generation',
  'google_auth',
  'partner_invite',
  'private_download',
  'subscription_billing',
];

test.skip(
  process.env.RUN_GOOGLE_HANDOFF_E2E !== '1',
  'source-free Google OAuth handoff readiness runs only in its bounded Preview mode',
);
test.use({ serviceWorkers: 'block' });

test('Google PKCE handoff is ready without contacting the Google login UI', async ({ context, page, request }) => {
  expect(sourceSha).toMatch(/^[0-9a-f]{40}$/);
  expect(workflowRunId).toMatch(/^[1-9][0-9]{0,19}$/);
  expect(workflowAttempt).toMatch(/^[1-9][0-9]{0,9}$/);
  expect(runtimeBundleId).toMatch(/^rtb_[0-9a-f]{64}$/);
  expect(deploymentId).toMatch(/^dpl_[A-Za-z0-9_-]{3,156}$/);
  expect(reportPath).toBeTruthy();

  const publicResponse = await request.get(`${baseURL}/api/v1/ops/public_config`, {
    maxRedirects: 0,
  });
  expect(publicResponse.status()).toBe(200);
  const publicConfig = await publicResponse.json();
  const capabilities = publicConfig?.capabilities || {};
  expect(Object.keys(capabilities).sort()).toEqual(expectedCapabilities);
  expect(capabilities.google_auth).toBe(true);
  for (const capability of expectedCapabilities.filter((name) => name !== 'google_auth')) {
    expect(capabilities[capability], `${capability} must remain OFF`).toBe(false);
  }
  expect(publicConfig?.auth?.google_oauth_enabled).toBe(true);
  const supabaseOrigin = new URL(String(publicConfig?.auth?.supabase_url || '')).origin;

  const callbackResponse = await request.get(`${baseURL}/pages/auth/callback`, {
    maxRedirects: 0,
  });
  expect(callbackResponse.status()).toBe(200);
  const sessionResponse = await request.post(`${baseURL}/api/v1/auth/supabase/session`, {
    headers: { Origin: baseURL, 'Content-Type': 'application/json' },
    data: { access_token: 'invalid-readiness-token', intent_token: 'invalid-readiness-intent' },
    maxRedirects: 0,
  });
  expect(sessionResponse.status()).toBe(401);

  const googleRequest = context.waitForEvent('request', { predicate: (outgoing) => {
    const url = new URL(outgoing.url());
    return (
      outgoing.resourceType() === 'document'
      && url.origin === 'https://accounts.google.com'
      && url.pathname === '/o/oauth2/v2/auth'
    );
  } });
  let oauthDocumentAborted = false;
  const googleResponses: string[] = [];
  context.on('response', (response) => {
    if (new URL(response.url()).origin === 'https://accounts.google.com') {
      googleResponses.push(response.url());
    }
  });
  await context.route('https://accounts.google.com/**', async (route) => {
    const outgoing = route.request();
    const url = new URL(outgoing.url());
    if (
      outgoing.resourceType() === 'document'
      && url.pathname === '/o/oauth2/v2/auth'
    ) {
      oauthDocumentAborted = true;
    }
    await route.abort('blockedbyclient');
  });
  const authorizeRequest = page.waitForRequest((outgoing) => {
    const url = new URL(outgoing.url());
    return url.origin === supabaseOrigin && url.pathname === '/auth/v1/authorize';
  });

  await page.goto('/pages/auth/login');
  const googleButton = page.locator('.google-button');
  await expect(googleButton).toBeVisible();
  await expect(googleButton).toBeEnabled();
  await googleButton.click();
  const authorizeURL = new URL((await authorizeRequest).url());
  const googleURL = new URL((await googleRequest).url());
  await expect.poll(() => oauthDocumentAborted).toBe(true);
  expect(googleResponses).toEqual([]);

  expect(authorizeURL.origin).toBe(supabaseOrigin);
  expect(authorizeURL.pathname).toBe('/auth/v1/authorize');
  expect(authorizeURL.searchParams.get('provider')).toBe('google');
  expect(authorizeURL.searchParams.get('redirect_to')).toBe(`${baseURL}/pages/auth/callback`);
  expect(authorizeURL.searchParams.get('code_challenge')).toMatch(/^[A-Za-z0-9_-]{32,128}$/);
  expect(authorizeURL.searchParams.get('code_challenge_method')?.toLowerCase()).toBe('s256');

  expect(googleURL.origin).toBe('https://accounts.google.com');
  expect(googleURL.pathname).toBe('/o/oauth2/v2/auth');
  expect(googleURL.searchParams.get('client_id')).toBeTruthy();
  expect(googleURL.searchParams.get('redirect_uri')).toBe(`${supabaseOrigin}/auth/v1/callback`);
  expect(googleURL.searchParams.get('response_type')).toBe('code');
  expect(googleURL.searchParams.get('state')).toBeTruthy();
  const scope = new Set(String(googleURL.searchParams.get('scope') || '').split(/\s+/));
  for (const requiredScope of ['openid', 'email', 'profile']) {
    expect(scope.has(requiredScope)).toBe(true);
  }

  const receipt = {
    schema: 'vowpic.preview-google-handoff-readiness.v1',
    passed: true,
    source_sha: sourceSha,
    workflow_run_id: workflowRunId,
    workflow_attempt: Number(workflowAttempt),
    runtime_bundle_id: runtimeBundleId,
    deployment_id: deploymentId,
    callback_path: '/pages/auth/callback',
    supabase_authorize_observed: true,
    google_request_intercepted_before_network: true,
    app_session_fail_closed: true,
    capabilities: Object.fromEntries(
      expectedCapabilities.map((capability) => [capability, capability === 'google_auth']),
    ),
    real_google_identity_proof: 'deferred_to_production_google_only',
  };
  writeFileSync(reportPath, `${JSON.stringify(receipt, null, 2)}\n`, {
    encoding: 'utf8',
    flag: 'wx',
  });
});
