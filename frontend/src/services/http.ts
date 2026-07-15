import { clearCachedSession, getCsrfToken, refreshLocalSession } from './auth';
import { API_BASE_URL } from '../utils/apiConfig';

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
export type HttpResponseType = 'json' | 'blob' | 'text';

export interface HttpFieldError {
  field: string;
  message: string;
}

export interface HttpRequestOptions<TBody = unknown> {
  method?: HttpMethod;
  body?: TBody;
  headers?: Record<string, string>;
  idempotencyKey?: string;
  responseType?: HttpResponseType;
  retryUnauthorized?: boolean;
}

export interface HttpUploadOptions {
  onProgress?: (percent: number) => void;
}

export class HttpError extends Error {
  readonly statusCode: number;
  readonly code?: string;
  readonly requestId?: string;
  readonly retryable: boolean;
  readonly fieldErrors: HttpFieldError[];
  readonly detail: any;

  constructor(args: {
    statusCode: number;
    message: string;
    code?: string;
    requestId?: string;
    retryable?: boolean;
    fieldErrors?: HttpFieldError[];
    detail?: any;
  }) {
    super(args.message);
    this.name = 'HttpError';
    this.statusCode = args.statusCode;
    this.code = args.code;
    this.requestId = args.requestId;
    this.retryable = args.retryable === true;
    this.fieldErrors = args.fieldErrors ?? [];
    this.detail = args.detail;
  }
}

const SAFE_METHODS = new Set<HttpMethod>(['GET']);
const FORBIDDEN_HEADER = /^authorization$/i;

function requestUrl(path: string): string {
  const cleanPath = String(path || '').trim();
  if (!cleanPath.startsWith('/') || cleanPath.startsWith('//')) {
    throw new Error('API paths must be root-relative');
  }
  return `${API_BASE_URL}${cleanPath}`;
}

function createRequestId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (globalThis.crypto?.getRandomValues) {
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    return `web-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
  }
  throw new Error('Secure request correlation is unavailable');
}

function validateIdempotencyKey(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  const clean = value.trim();
  if (!clean || clean.length > 128 || /[\x00-\x20\x7f]/.test(clean)) {
    throw new Error('Idempotency key is invalid');
  }
  return clean;
}

function buildHeaders(args: {
  method: HttpMethod;
  requestId: string;
  headers?: Record<string, string>;
  idempotencyKey?: string;
  jsonBody?: boolean;
}): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const [name, value] of Object.entries(args.headers ?? {})) {
    if (FORBIDDEN_HEADER.test(name)) {
      throw new Error('Authorization headers are forbidden for Cookie-authenticated requests');
    }
    headers[name] = value;
  }
  if (args.jsonBody) headers['Content-Type'] = 'application/json';
  headers['X-Request-ID'] = args.requestId;
  if (!SAFE_METHODS.has(args.method)) {
    const csrf = getCsrfToken();
    if (csrf) headers['X-CSRF-Token'] = csrf;
  }
  const idempotencyKey = validateIdempotencyKey(args.idempotencyKey);
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
  return headers;
}

function canRefresh(path: string): boolean {
  return !path.startsWith('/auth/refresh') && !path.startsWith('/auth/logout');
}

function normalizedPayload(payload: unknown): Record<string, unknown> {
  return payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
}

function normalizeFieldErrors(value: unknown): HttpFieldError[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const candidate = item as Record<string, unknown>;
    const field = String(candidate.field ?? '').trim();
    const message = String(candidate.message ?? '').trim();
    return field && message ? [{ field, message }] : [];
  });
}

function errorMessage(payload: Record<string, unknown>, statusCode: number): string {
  const detail = normalizedPayload(payload.detail);
  const message = String(payload.message ?? detail.message ?? '').trim();
  if (message) return message;
  if (statusCode === 401) return 'Authentication expired, please retry';
  if (statusCode === 403) return 'No permission';
  if (statusCode === 404) return 'Resource not found';
  if (statusCode === 422) return 'Validation failed';
  if (statusCode >= 500) return 'Service unavailable, please try again later';
  return `Request failed: ${statusCode}`;
}

function createHttpError(
  statusCode: number,
  rawPayload: unknown,
  responseRequestId?: string | null,
): HttpError {
  const payload = normalizedPayload(rawPayload);
  const detail = normalizedPayload(payload.detail);
  const fieldErrors = normalizeFieldErrors(payload.field_errors ?? detail.field_errors);
  const requestId = String(
    payload.request_id ?? detail.request_id ?? responseRequestId ?? '',
  ).trim() || undefined;
  const code = String(payload.code ?? detail.code ?? '').trim() || undefined;
  return new HttpError({
    statusCode,
    message: errorMessage(payload, statusCode),
    code,
    requestId,
    retryable: payload.retryable === true || detail.retryable === true,
    fieldErrors,
    detail: payload.detail ?? rawPayload,
  });
}

async function readResponse(response: Response, responseType: HttpResponseType): Promise<unknown> {
  if (response.status === 204) return undefined;
  if (responseType === 'blob' && response.ok) return response.blob();
  if (responseType === 'text' && response.ok) return response.text();
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) return response.json();
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function serializeBody(body: unknown): { body?: BodyInit; jsonBody: boolean } {
  if (body === undefined) return { jsonBody: false };
  if (
    typeof body === 'string'
    || body instanceof Blob
    || body instanceof FormData
    || body instanceof URLSearchParams
    || body instanceof ArrayBuffer
  ) {
    return { body, jsonBody: false };
  }
  return { body: JSON.stringify(body), jsonBody: true };
}

export async function httpRequest<TResponse, TBody = unknown>(
  path: string,
  options: HttpRequestOptions<TBody> = {},
): Promise<TResponse> {
  const method = options.method ?? 'GET';
  const requestId = createRequestId();
  const serialized = serializeBody(options.body);
  const headers = buildHeaders({
    method,
    requestId,
    headers: options.headers,
    idempotencyKey: options.idempotencyKey,
    jsonBody: serialized.jsonBody,
  });
  const request = () => fetch(requestUrl(path), {
    method,
    body: serialized.body,
    headers,
    credentials: 'include',
  });

  let response = await request();
  if (
    response.status === 401
    && options.retryUnauthorized !== false
    && canRefresh(path)
    && await refreshLocalSession()
  ) {
    response = await request();
  }
  const payload = await readResponse(response, options.responseType ?? 'json');
  if (response.ok) return payload as TResponse;
  if (response.status === 401) clearCachedSession();
  throw createHttpError(response.status, payload, response.headers.get('x-request-id'));
}

export async function httpUpload<TResponse>(
  path: string,
  filePath: string,
  name = 'file',
  options: HttpUploadOptions = {},
): Promise<TResponse> {
  const requestId = createRequestId();
  const header = buildHeaders({ method: 'POST', requestId });
  const uploadOnce = () => new Promise<UniNamespace.UploadFileSuccessCallbackResult>((resolve, reject) => {
    const task = uni.uploadFile({
      url: requestUrl(path),
      filePath,
      name,
      header,
      withCredentials: true,
      success: resolve,
      fail: reject,
    });
    task.onProgressUpdate((progress) => options.onProgress?.(Math.round(progress.progress)));
  });

  let response = await uploadOnce();
  if (response.statusCode === 401 && canRefresh(path) && await refreshLocalSession()) {
    response = await uploadOnce();
  }
  let payload: unknown = response.data;
  try {
    payload = JSON.parse(response.data);
  } catch {
    // Non-JSON error bodies remain explicit transport failures below.
  }
  if (response.statusCode >= 200 && response.statusCode < 300) return payload as TResponse;
  if (response.statusCode === 401) clearCachedSession();
  const responseHeaders = (response as unknown as { header?: Record<string, string> }).header;
  throw createHttpError(
    response.statusCode,
    payload,
    responseHeaders?.['X-Request-ID'] ?? responseHeaders?.['x-request-id'],
  );
}
