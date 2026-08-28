import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchMock = vi.fn();

function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

describe('Supabase public browser config', () => {
  beforeEach(() => {
    vi.resetModules();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    window.sessionStorage.clear();
  });

  it('uses the browser fetch path and accepts one enabled public config', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {
      auth: {
        google_oauth_enabled: true,
        supabase_url: 'https://preview.supabase.co/',
        supabase_publishable_key: 'public-key',
      },
    }));
    const { refreshSupabaseConfig } = await import('../../src/utils/supabase');

    await expect(refreshSupabaseConfig(true)).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/api\/v1\/ops\/public_config$/), expect.objectContaining({
      method: 'GET',
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: expect.any(AbortSignal),
    }));
  });

  it('fails closed when OAuth is disabled or the endpoint fails', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, {
        auth: {
          google_oauth_enabled: false,
          supabase_url: 'https://preview.supabase.co/',
          supabase_publishable_key: 'public-key',
        },
      }))
      .mockResolvedValueOnce(jsonResponse(503, {}));
    const { refreshSupabaseConfig } = await import('../../src/utils/supabase');

    await expect(refreshSupabaseConfig(true)).resolves.toBe(false);
    await expect(refreshSupabaseConfig(true)).resolves.toBe(false);
  });

  it('reuses one successful public config instead of delaying the login click', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {
      auth: {
        google_oauth_enabled: true,
        supabase_url: 'https://preview.supabase.co/',
        supabase_publishable_key: 'public-key',
      },
    }));
    const { refreshSupabaseConfig } = await import('../../src/utils/supabase');

    await expect(refreshSupabaseConfig()).resolves.toBe(true);
    await expect(refreshSupabaseConfig()).resolves.toBe(true);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('persists only the PKCE verifier across the OAuth navigation', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {
      auth: {
        google_oauth_enabled: true,
        supabase_url: 'https://preview.supabase.co/',
        supabase_publishable_key: 'public-key',
      },
    }));
    const { clearSupabaseTransientStorage, getSupabaseClient } = await import('../../src/utils/supabase');
    const supabase = await getSupabaseClient();

    const { data, error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: 'https://preview.example.com/pages/auth/callback',
        skipBrowserRedirect: true,
      },
    });

    expect(error).toBeNull();
    expect(data.url).toContain('code_challenge=');
    expect(window.sessionStorage.getItem('vowpic_supabase_pkce-code-verifier')).toBeTruthy();
    expect(window.sessionStorage.getItem('vowpic_supabase_pkce')).toBeNull();
    expect(window.sessionStorage.getItem('vowpic_supabase_pkce-user')).toBeNull();

    clearSupabaseTransientStorage();
    expect(window.sessionStorage.getItem('vowpic_supabase_pkce-code-verifier')).toBeNull();
  });
});
