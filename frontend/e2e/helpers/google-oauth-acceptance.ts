import type { Page } from '@playwright/test';

const SUPABASE_GOOGLE_OAUTH_ENDPOINT = (
  /^https:\/\/[a-z0-9-]+\.supabase\.co\/auth\/v1\/authorize(?:\?|$)/i
);
const GOOGLE_IDENTITY_EMAIL = /^[^\s"\\]+@[^\s"\\]+$/;
const PKCE_CODE_CHALLENGE = /^[A-Za-z0-9_-]{43,128}$/;

export function rewritePreauthenticatedSupabaseGoogleOAuthUrl(
  requestUrl: string,
  identityEmail: string,
): string {
  if (!GOOGLE_IDENTITY_EMAIL.test(identityEmail)) {
    throw new Error('GOOGLE_IDENTITY_EMAIL_INVALID');
  }
  if (!SUPABASE_GOOGLE_OAUTH_ENDPOINT.test(requestUrl)) {
    throw new Error('SUPABASE_GOOGLE_OAUTH_ENDPOINT_INVALID');
  }
  const url = new URL(requestUrl);
  if (url.searchParams.get('provider') !== 'google') {
    throw new Error('SUPABASE_GOOGLE_OAUTH_PROVIDER_INVALID');
  }
  let redirectTo: URL;
  try {
    redirectTo = new URL(url.searchParams.get('redirect_to') || '');
  } catch {
    throw new Error('SUPABASE_GOOGLE_OAUTH_REDIRECT_INVALID');
  }
  if (redirectTo.protocol !== 'https:' || redirectTo.username || redirectTo.password) {
    throw new Error('SUPABASE_GOOGLE_OAUTH_REDIRECT_INVALID');
  }
  const codeChallenge = url.searchParams.get('code_challenge') || '';
  if (
    !PKCE_CODE_CHALLENGE.test(codeChallenge)
    || url.searchParams.get('code_challenge_method') !== 's256'
  ) {
    throw new Error('SUPABASE_GOOGLE_OAUTH_PKCE_INVALID');
  }
  url.searchParams.set('prompt', 'select_account');
  url.searchParams.set('login_hint', identityEmail);
  return url.toString();
}

export async function preparePreauthenticatedGoogleOAuth(
  page: Page,
  identityEmail: string,
): Promise<void> {
  if (!GOOGLE_IDENTITY_EMAIL.test(identityEmail)) {
    throw new Error('GOOGLE_IDENTITY_EMAIL_INVALID');
  }
  await page.route(
    SUPABASE_GOOGLE_OAUTH_ENDPOINT,
    async (route) => {
      await route.continue({
        url: rewritePreauthenticatedSupabaseGoogleOAuthUrl(
          route.request().url(),
          identityEmail,
        ),
      });
    },
    { times: 1 },
  );
}
