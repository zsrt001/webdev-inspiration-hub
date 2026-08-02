import type { Page } from '@playwright/test';

const GOOGLE_OAUTH_ENDPOINT = /^https:\/\/accounts\.google\.com\/o\/oauth2\/v2\/auth(?:\?|$)/;
const GOOGLE_IDENTITY_EMAIL = /^[^\s"\\]+@[^\s"\\]+$/;

export function rewritePreauthenticatedGoogleOAuthUrl(
  requestUrl: string,
  identityEmail: string,
): string {
  if (!GOOGLE_IDENTITY_EMAIL.test(identityEmail)) {
    throw new Error('GOOGLE_IDENTITY_EMAIL_INVALID');
  }
  if (!GOOGLE_OAUTH_ENDPOINT.test(requestUrl)) {
    throw new Error('GOOGLE_OAUTH_ENDPOINT_INVALID');
  }
  const url = new URL(requestUrl);
  for (const parameter of ['client_id', 'redirect_uri', 'response_type', 'scope', 'state']) {
    if (!url.searchParams.get(parameter)) {
      throw new Error(`GOOGLE_OAUTH_PARAMETER_MISSING:${parameter}`);
    }
  }
  url.searchParams.set('prompt', 'none');
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
    GOOGLE_OAUTH_ENDPOINT,
    async (route) => {
      await route.continue({
        url: rewritePreauthenticatedGoogleOAuthUrl(route.request().url(), identityEmail),
      });
    },
    { times: 1 },
  );
}
