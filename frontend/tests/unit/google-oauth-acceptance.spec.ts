import { describe, expect, it } from 'vitest';

import {
  rewritePreauthenticatedSupabaseGoogleOAuthUrl,
} from '../../e2e/helpers/google-oauth-acceptance';

function providerUrl(overrides: Record<string, string> = {}): string {
  const url = new URL('https://project-ref.supabase.co/auth/v1/authorize');
  const parameters = {
    provider: 'google',
    redirect_to: 'https://preview.example.com/pages/auth/callback',
    code_challenge: 'a'.repeat(43),
    code_challenge_method: 's256',
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
      rewritePreauthenticatedSupabaseGoogleOAuthUrl(
        original.toString(),
        'acceptance@example.com',
      ),
    );

    expect(rewritten.searchParams.get('prompt')).toBe('none');
    expect(rewritten.searchParams.get('login_hint')).toBe('acceptance@example.com');
    for (const parameter of [
      'provider',
      'redirect_to',
      'code_challenge',
      'code_challenge_method',
    ]) {
      expect(rewritten.searchParams.get(parameter)).toBe(original.searchParams.get(parameter));
    }
  });

  it('fails closed for an unrelated endpoint, invalid identity, or incomplete OAuth request', () => {
    expect(() => rewritePreauthenticatedSupabaseGoogleOAuthUrl(
      'https://accounts.google.com/v3/signin/rejected',
      'acceptance@example.com',
    )).toThrow('SUPABASE_GOOGLE_OAUTH_ENDPOINT_INVALID');
    expect(() => rewritePreauthenticatedSupabaseGoogleOAuthUrl(providerUrl(), 'not-an-email'))
      .toThrow('GOOGLE_IDENTITY_EMAIL_INVALID');
    expect(() => rewritePreauthenticatedSupabaseGoogleOAuthUrl(
      providerUrl({ provider: 'github' }),
      'acceptance@example.com',
    )).toThrow('SUPABASE_GOOGLE_OAUTH_PROVIDER_INVALID');
    expect(() => rewritePreauthenticatedSupabaseGoogleOAuthUrl(
      providerUrl({ redirect_to: 'http://preview.example.com/pages/auth/callback' }),
      'acceptance@example.com',
    )).toThrow('SUPABASE_GOOGLE_OAUTH_REDIRECT_INVALID');
    expect(() => rewritePreauthenticatedSupabaseGoogleOAuthUrl(
      providerUrl({ code_challenge: '' }),
      'acceptance@example.com',
    )).toThrow('SUPABASE_GOOGLE_OAUTH_PKCE_INVALID');
  });
});
