import { beforeEach, describe, expect, it, vi } from 'vitest';

const requestMock = vi.fn();
const supabaseMocks = vi.hoisted(() => ({
  exchangeCodeForSession: vi.fn(),
}));

vi.mock('../../src/utils/supabase', () => ({
  clearSupabaseTransientStorage: vi.fn(),
  discardSupabaseClient: vi.fn(),
  getSupabaseClient: vi.fn(async () => ({
    auth: { exchangeCodeForSession: supabaseMocks.exchangeCodeForSession },
  })),
  refreshSupabaseConfig: vi.fn(async () => true),
}));

function response(statusCode: number, data: unknown = {}) {
  return Promise.resolve({ statusCode, data });
}

describe('browser Cookie session discovery', () => {
  beforeEach(() => {
    vi.resetModules();
    requestMock.mockReset();
    supabaseMocks.exchangeCodeForSession.mockReset();
    supabaseMocks.exchangeCodeForSession.mockResolvedValue({
      data: { session: { access_token: 'broker-token' } },
      error: null,
    });
    vi.stubGlobal('uni', { request: requestMock });
    document.cookie = 'vowpic_csrf=; Max-Age=0; path=/';
    window.sessionStorage.clear();
    window.history.replaceState(null, '', '/');
  });

  it('does not probe protected endpoints when no local session cookie exists', async () => {
    const { ensureSession } = await import('../../src/services/auth');

    await expect(ensureSession(true)).resolves.toBeNull();
    expect(requestMock).not.toHaveBeenCalled();
  });

  it('verifies and refreshes a server-issued Cookie session at most once', async () => {
    document.cookie = 'vowpic_csrf=csrf-cookie-token; path=/';
    requestMock
      .mockReturnValueOnce(response(401))
      .mockReturnValueOnce(response(401));
    const { ensureSession } = await import('../../src/services/auth');

    await expect(ensureSession()).resolves.toBeNull();

    expect(requestMock).toHaveBeenCalledTimes(2);
    expect(requestMock.mock.calls[0][0]).toMatchObject({
      method: 'GET',
      url: expect.stringMatching(/\/auth\/me$/),
      withCredentials: true,
    });
    expect(requestMock.mock.calls[1][0]).toMatchObject({
      method: 'POST',
      url: expect.stringMatching(/\/auth\/refresh$/),
      header: expect.objectContaining({ 'X-CSRF-Token': 'csrf-cookie-token' }),
      withCredentials: true,
    });
  });

  it('caches a verified session for subsequent navigation', async () => {
    document.cookie = 'vowpic_csrf=csrf-cookie-token; path=/';
    requestMock.mockReturnValueOnce(response(200, {
      id: 'user-1',
      email: 'user@example.com',
    }));
    const { ensureSession } = await import('../../src/services/auth');

    await expect(ensureSession()).resolves.toMatchObject({ id: 'user-1' });
    await expect(ensureSession()).resolves.toMatchObject({ id: 'user-1' });
    expect(requestMock).toHaveBeenCalledTimes(1);
  });

  it('shows the server request reference when the local session exchange is denied', async () => {
    window.history.replaceState(null, '', '/pages/auth/callback?code=google-code');
    window.sessionStorage.setItem('vowpic_oauth_intent', JSON.stringify({
      intentToken: 'i'.repeat(32),
      redirectPath: '/pages/account/index',
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    }));
    requestMock.mockReturnValueOnce(response(503, {
      code: 'service_unavailable',
      message: 'Service is temporarily unavailable.',
      request_id: 'request-ref-123',
    }));
    const { finishGoogleLogin } = await import('../../src/services/auth');

    await expect(finishGoogleLogin()).rejects.toThrow(
      'Service is temporarily unavailable. (Reference: request-ref-123)',
    );
  });
});
