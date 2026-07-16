import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../src/services/auth', () => ({
  clearCachedSession: vi.fn(),
  getCsrfToken: vi.fn(),
  refreshLocalSession: vi.fn(),
}));

import {
  clearCachedSession,
  getCsrfToken,
  refreshLocalSession,
} from '../../src/services/auth';
import { HttpError, httpRequest } from '../../src/services/http';

const fetchMock = vi.fn(
  (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> =>
    Promise.resolve(jsonResponse(500, { message: 'Unexpected fetch call' })),
);

function jsonResponse(
  status: number,
  payload: unknown,
  requestId = 'server-request-1',
): Response {
  const responseHeaders = new Map<string, string>([
    ['content-type', 'application/json'],
    ['x-request-id', requestId],
  ]);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name: string) {
        return responseHeaders.get(name.toLowerCase()) ?? null;
      },
    },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
    blob: async () => new Blob([JSON.stringify(payload)]),
  } as unknown as Response;
}

function requestHeaders(callIndex = 0): Headers {
  const init = fetchMock.mock.calls[callIndex][1] as RequestInit;
  return new Headers(init.headers);
}

describe('httpRequest', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    vi.mocked(getCsrfToken).mockReturnValue('csrf-cookie-token');
    vi.mocked(refreshLocalSession).mockResolvedValue(false);
  });

  it('uses Cookie credentials and never adds a bearer token', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));

    await expect(httpRequest<{ ok: boolean }>('/orders')).resolves.toEqual({ ok: true });

    const [, init] = fetchMock.mock.calls[0];
    expect(init?.credentials).toBe('include');
    expect(requestHeaders().has('Authorization')).toBe(false);
    expect(requestHeaders().get('X-Request-ID')).toMatch(/^[A-Za-z0-9._:-]+$/);
  });

  it('adds CSRF to unsafe methods and rejects caller-supplied authorization', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, { ok: true }));

    await httpRequest('/orders/create', {
      method: 'POST',
      body: { template_id: 'solo_royal_castle' },
    });

    expect(requestHeaders().get('X-CSRF-Token')).toBe('csrf-cookie-token');
    await expect(httpRequest('/orders', {
      headers: { Authorization: 'Bearer forbidden' },
    })).rejects.toThrow('Authorization headers are forbidden');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('refreshes once and reuses request and idempotency correlation', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, {
        code: 'unauthorized',
        message: 'Expired',
        request_id: 'expired-request',
        field_errors: [],
      }))
      .mockResolvedValueOnce(jsonResponse(200, { checkout_url: 'https://checkout.invalid' }));
    vi.mocked(refreshLocalSession).mockResolvedValueOnce(true);

    await httpRequest('/payments/checkout', {
      method: 'POST',
      body: { package_code: 'starter' },
      idempotencyKey: 'checkout-key-1',
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(refreshLocalSession).toHaveBeenCalledTimes(1);
    expect(requestHeaders(0).get('Idempotency-Key')).toBe('checkout-key-1');
    expect(requestHeaders(1).get('Idempotency-Key')).toBe('checkout-key-1');
    expect(requestHeaders(1).get('X-Request-ID')).toBe(requestHeaders(0).get('X-Request-ID'));
  });

  it('preserves structured field errors and server request correlation', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(422, {
      code: 'validation_failed',
      message: 'Validation failed',
      request_id: 'validation-request-1',
      retryable: false,
      field_errors: [{ field: 'asset_ids.0', message: 'Invalid asset' }],
    }));

    const error = await httpRequest('/orders/create', {
      method: 'POST',
      body: {},
    }).catch((caught) => caught);

    expect(error).toBeInstanceOf(HttpError);
    expect(error).toMatchObject({
      statusCode: 422,
      code: 'validation_failed',
      requestId: 'validation-request-1',
      retryable: false,
      fieldErrors: [{ field: 'asset_ids.0', message: 'Invalid asset' }],
    });
  });

  it('clears the cached session after the single failed refresh attempt', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { message: 'Expired' }));
    vi.mocked(refreshLocalSession).mockResolvedValueOnce(false);

    await expect(httpRequest('/orders')).rejects.toBeInstanceOf(HttpError);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(refreshLocalSession).toHaveBeenCalledTimes(1);
    expect(clearCachedSession).toHaveBeenCalledTimes(1);
  });
});
