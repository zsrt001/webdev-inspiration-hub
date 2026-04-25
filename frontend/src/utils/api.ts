import { ensureSession, getGuestUserId, getToken, isJwtToken, logout } from './auth';
import { API_BASE_URL, isH5Runtime, resolveBackendOrigin } from './apiConfig';

function getRuntimeLocale(): 'zh' | 'en' {
    try {
        const stored = uni.getStorageSync('aws_locale');
        return stored === 'en' ? 'en' : 'zh';
    } catch {
        return 'zh';
    }
}

function localeText(zh: string, en: string): string {
    return getRuntimeLocale() === 'en' ? en : zh;
}

export function resolveApiUrl(path: string): string {
    const cleanPath = path.startsWith('/') ? path : `/${path}`;
    return `${API_BASE_URL}${cleanPath}`;
}

export function resolvePublicUrl(url: string | null | undefined): string {
    const raw = (url || '').trim();
    if (!raw) return '';

    if (
        raw.startsWith('http://') ||
        raw.startsWith('https://') ||
        raw.startsWith('data:') ||
        raw.startsWith('blob:')
    ) {
        return raw;
    }

    const withLeadingSlash = raw.startsWith('/') ? raw : `/${raw}`;

    if (withLeadingSlash.startsWith('/style-previews/')) {
        return `/static${withLeadingSlash}`;
    }

    if (
        withLeadingSlash === '/hero_banner.jpg' ||
        withLeadingSlash === '/custom_mode.jpg' ||
        /\.(jpg|jpeg|png|webp|svg)$/i.test(withLeadingSlash)
    ) {
        return withLeadingSlash.startsWith('/static/')
            ? withLeadingSlash
            : `/static/${withLeadingSlash.replace(/^\//, '')}`;
    }

    const backendOrigin = resolveBackendOrigin();
    if (backendOrigin) return `${backendOrigin}${withLeadingSlash}`;

    return isH5Runtime() ? withLeadingSlash : `${backendOrigin}${withLeadingSlash}`;
}

interface RequestOptions {
    showLoading?: boolean;
    showError?: boolean;
}

export interface ApiError extends Error {
    statusCode?: number;
    detail?: any;
    code?: string;
}

function canRecoverUnauthorized(path: string): boolean {
    return !path.startsWith('/auth/login');
}

async function rebootstrapSession(): Promise<boolean> {
    logout();
    try {
        const session = await ensureSession(API_BASE_URL);
        return !!session?.token && isJwtToken(session.token);
    } catch {
        return false;
    }
}

function getAdminToken(): string | null {
    try {
        const raw = uni.getStorageSync('ADMIN_TOKEN');
        if (typeof raw === 'string' && raw.trim()) {
            return raw.trim();
        }
    } catch {
        // ignore
    }
    return null;
}

function shouldBootstrapSession(path: string): boolean {
    const publicPrefixes = ['/templates', '/ops/public_config', '/analytics/click', '/health'];
    return !publicPrefixes.some((prefix) => path.startsWith(prefix));
}

async function buildHeaders(path: string): Promise<Record<string, string>> {
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
    };

    let token = getToken();
    if (!isJwtToken(token) && shouldBootstrapSession(path)) {
        try {
            const session = await ensureSession(API_BASE_URL);
            token = session?.token || getToken();
        } catch {
            token = getToken();
        }
    }

    const hasJwt = isJwtToken(token);
    if (hasJwt && token) {
        headers.Authorization = `Bearer ${token}`;
    }

    const visitorIdentity = getGuestUserId().trim();
    if (!hasJwt && visitorIdentity) {
        headers['X-Visitor-Id'] = visitorIdentity.slice(0, 64);
    }

    const adminToken = getAdminToken();
    if (adminToken) {
        const needsAdmin =
            path.startsWith('/admin') ||
            path.startsWith('/leads') ||
            path.startsWith('/analytics/stats') ||
            path.startsWith('/upload/delete');
        if (needsAdmin) {
            headers['X-Admin-Token'] = adminToken;
        }
    }

    return headers;
}

function extractErrorMessage(detail: any, statusCode: number): string {
    if (typeof detail === 'string' && detail.trim()) {
        return detail;
    }

    if (detail && typeof detail === 'object') {
        if (typeof detail.message === 'string' && detail.message.trim()) {
            return detail.message;
        }
        if (Array.isArray(detail.advice) && detail.advice.length > 0) {
            return String(detail.advice[0]);
        }
        if (typeof detail.error === 'string' && detail.error.trim()) {
            return detail.error;
        }
    }

    if (statusCode === 400) return 'Invalid request parameters';
    if (statusCode === 401) return 'Authentication expired, please retry';
    if (statusCode === 402) return 'Insufficient credits';
    if (statusCode === 403) return 'No permission';
    if (statusCode === 404) return 'Resource not found';
    if (statusCode === 422) return 'Validation failed';
    if (statusCode >= 500) return 'Service unavailable, please try again later';
    return `Request failed: ${statusCode}`;
}

function createApiError(statusCode: number, payload: any): ApiError {
    const detail = payload?.detail ?? payload;
    const error = new Error(extractErrorMessage(detail, statusCode)) as ApiError;
    error.statusCode = statusCode;
    error.detail = detail;
    if (detail && typeof detail === 'object' && typeof detail.error === 'string') {
        error.code = detail.error;
    }
    return error;
}

async function requestJson<T>(
    method: 'GET' | 'POST' | 'PUT',
    path: string,
    data: any,
    options: RequestOptions,
    loadingTitle: string
): Promise<T> {
    const { showLoading, showError = true } = options;

    if (showLoading) {
        uni.showLoading({ title: loadingTitle });
    }

    try {
        const requestOnce = async () => {
            const headers = await buildHeaders(path);
            return uni.request({
                url: resolveApiUrl(path),
                method,
                data,
                header: headers,
            });
        };

        let res = await requestOnce();
        if (res.statusCode === 401 && canRecoverUnauthorized(path) && await rebootstrapSession()) {
            res = await requestOnce();
        }

        if (res.statusCode >= 200 && res.statusCode < 300) {
            return res.data as T;
        }

        throw createApiError(res.statusCode, res.data);
    } catch (error: any) {
        if (showError) {
            uni.showToast({
                title: error.message || 'Network error',
                icon: 'none',
            });
        }
        throw error;
    } finally {
        if (showLoading) {
            uni.hideLoading();
        }
    }
}

export async function get<T>(
    path: string,
    options: RequestOptions = {}
): Promise<T> {
    const merged = { showLoading: false, showError: true, ...options };
    return requestJson<T>('GET', path, undefined, merged, localeText('加载中...', 'Loading...'));
}

export async function post<T>(
    path: string,
    data: any,
    options: RequestOptions = {}
): Promise<T> {
    const merged = { showLoading: true, showError: true, ...options };
    return requestJson<T>('POST', path, data, merged, localeText('处理中...', 'Processing...'));
}

export async function put<T>(
    path: string,
    data: any,
    options: RequestOptions = {}
): Promise<T> {
    const merged = { showLoading: true, showError: true, ...options };
    return requestJson<T>('PUT', path, data, merged, localeText('处理中...', 'Processing...'));
}

export async function uploadFile(
    path: string,
    filePath: string,
    name: string = 'file'
): Promise<any> {
    uni.showLoading({ title: localeText('上传中...', 'Uploading...') });

    try {
        const requestOnce = async () => {
            const headers = await buildHeaders(path);
            return uni.uploadFile({
                url: resolveApiUrl(path),
                filePath,
                name,
                header: headers,
            });
        };

        let res = await requestOnce();
        if (res.statusCode === 401 && canRecoverUnauthorized(path) && await rebootstrapSession()) {
            res = await requestOnce();
        }

        if (res.statusCode >= 200 && res.statusCode < 300) {
            return JSON.parse(res.data);
        }

        let payload: any = res.data;
        try {
            payload = JSON.parse(res.data);
        } catch {
            // keep raw payload
        }
        throw createApiError(res.statusCode, payload);
    } catch (error: any) {
        uni.showToast({
            title: error.message || 'Upload failed',
            icon: 'none',
        });
        throw error;
    } finally {
        uni.hideLoading();
    }
}

export default {
    get,
    post,
    put,
    uploadFile,
};
