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
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/api\/v1\/ops\/public_config$/), {
      method: 'GET',
      credentials: 'include',
      headers: { Accept: 'application/json' },
    });
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
});
