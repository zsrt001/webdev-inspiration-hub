import { describe, expect, it } from 'vitest';

import { rewritePreauthenticatedGoogleOAuthUrl } from '../../e2e/helpers/google-oauth-acceptance';

function providerUrl(overrides: Record<string, string> = {}): string {
  const url = new URL('https://accounts.google.com/o/oauth2/v2/auth');
  const parameters = {
    client_id: 'client.apps.googleusercontent.com',
    redirect_uri: 'https://project.supabase.co/auth/v1/callback',
    response_type: 'code',
    scope: 'openid email profile',
    state: 'opaque-state',
    prompt: 'select_account',
    ...overrides,
  };
  for (const [key, value] of Object.entries(parameters)) url.searchParams.set(key, value);
  return url.toString();
}

describe('protected Google OAuth acceptance navigation', () => {
  it('uses a silent exact-identity redirect without changing the PKCE request binding', () => {
    const original = new URL(providerUrl());
    const rewritten = new URL(
      rewritePreauthenticatedGoogleOAuthUrl(original.toString(), 'acceptance@example.com'),
    );

    expect(rewritten.searchParams.get('prompt')).toBe('none');
    expect(rewritten.searchParams.get('login_hint')).toBe('acceptance@example.com');
    for (const parameter of ['client_id', 'redirect_uri', 'response_type', 'scope', 'state']) {
      expect(rewritten.searchParams.get(parameter)).toBe(original.searchParams.get(parameter));
    }
  });

  it('fails closed for an unrelated endpoint, invalid identity, or incomplete OAuth request', () => {
    expect(() => rewritePreauthenticatedGoogleOAuthUrl(
      'https://accounts.google.com/v3/signin/rejected',
      'acceptance@example.com',
    )).toThrow('GOOGLE_OAUTH_ENDPOINT_INVALID');
    expect(() => rewritePreauthenticatedGoogleOAuthUrl(providerUrl(), 'not-an-email'))
      .toThrow('GOOGLE_IDENTITY_EMAIL_INVALID');
    expect(() => rewritePreauthenticatedGoogleOAuthUrl(
      providerUrl({ state: '' }),
      'acceptance@example.com',
    )).toThrow('GOOGLE_OAUTH_PARAMETER_MISSING:state');
  });
});
