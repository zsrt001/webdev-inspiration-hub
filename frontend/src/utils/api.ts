import { ensureSession, getClientFingerprint, isJwtToken } from './auth';
import { API_BASE_URL, isH5Runtime, resolveBackendOrigin } from './apiConfig';

function getRuntimeLocale(): 'zh' | 'en' {
    try {
        const stored = uni.getStorageSync('aws_locale');
        return stored === 'zh' ? 'zh' : 'en';
    } catch {
        return 'en';
    }
}

function localeText(zh: string, en: string): string {
    return getRuntimeLocale() === 'zh' ? zh : en;
}

const LEGACY_STATIC_MAP: Record<string, string> = {
    '/hero_banner.jpg': '/style-previews/royal_castle.jpg',
    '/static/hero_banner.jpg': '/style-previews/royal_castle.jpg',
    '/style-previews/hero_banner.jpg': '/style-previews/royal_castle.jpg',
    '/static/style-previews/hero_banner.jpg': '/style-previews/royal_castle.jpg',
    '/style-previews/couple_royal_castle.jpg': '/style-previews/royal_castle.jpg',
    '/static/style-previews/couple_royal_castle.jpg': '/style-previews/royal_castle.jpg',
    '/style-previews/solo_chn_xiuhe.jpg': '/style-previews/chn_xiuhe.jpg',
    '/static/style-previews/solo_chn_xiuhe.jpg': '/style-previews/chn_xiuhe.jpg',
    '/style-previews/solo_gothic_romance_v2.png': '/style-previews/solo_gothic_romance.jpg',
    '/static/style-previews/solo_gothic_romance_v2.png': '/style-previews/solo_gothic_romance.jpg',
    '/style-previews/couple_hk_retro_v2.png': '/style-previews/couple_hk_retro.jpg',
    '/static/style-previews/couple_hk_retro_v2.png': '/style-previews/couple_hk_retro.jpg',
    '/style-previews/couple_cyberpunk_city_v2.png': '/style-previews/couple_cyberpunk_city.jpg',
    '/static/style-previews/couple_cyberpunk_city_v2.png': '/style-previews/couple_cyberpunk_city.jpg',
    '/custom_mode.jpg': '/style-previews/custom_mode.jpg',
    '/static/custom_mode.jpg': '/style-previews/custom_mode.jpg',
};

const ROOT_PREVIEW_FILES = new Set([
    'beach_sunset.jpg',
    'chn_xiuhe.jpg',
    'classic_bw.jpg',
    'couple_beach_sunset.jpg',
    'couple_chn_xiuhe.jpg',
    'couple_classic_bw.jpg',
    'couple_cyberpunk_city.jpg',
    'couple_gothic_romance.jpg',
    'couple_hk_retro.jpg',
    'couple_japanese_shiromuku.jpg',
    'couple_korean_minimal.jpg',
    'couple_old_money.jpg',
    'couple_school_days.jpg',
    'couple_twilight_forest.jpg',
    'custom_free.jpg',
    'custom_mode.jpg',
    'cyber_city.jpg',
    'cyberpunk_city.jpg',
    'golden_chinese_courtyard.jpg',
    'golden_modern_remake.jpg',
    'golden_vintage_studio_8090.jpg',
    'gothic_dark.jpg',
    'gothic_romance.jpg',
    'hk_retro.jpg',
    'japanese_shiromuku.jpg',
    'jp_kimono.jpg',
    'korean_minimal.jpg',
    'kor_white.jpg',
    'old_money.jpg',
    'out_forest.jpg',
    'royal_castle.jpg',
    'school_days.jpg',
    'solo_beach_sunset.jpg',
    'solo_gothic_romance.jpg',
    'solo_korean_minimal.jpg',
    'solo_old_money.jpg',
    'solo_royal_castle.jpg',
    'twilight_forest.jpg',
    'west_castle.jpg',
]);

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
    const mappedStaticPath = LEGACY_STATIC_MAP[withLeadingSlash];
    if (mappedStaticPath) {
        return `/static${mappedStaticPath}`;
    }

    const rootPreviewFile = withLeadingSlash.replace(/^\/static\//, '').replace(/^\//, '');
    if (ROOT_PREVIEW_FILES.has(rootPreviewFile)) {
        return `/static/style-previews/${rootPreviewFile}`;
    }

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
    requestId?: string;
}

async function buildHeaders(_path: string): Promise<Record<string, string>> {
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
    };

    const session = await ensureSession(API_BASE_URL);
    const token = session?.token || null;

    if (isJwtToken(token) && token) {
        headers.Authorization = `Bearer ${token}`;
    }

    headers['X-Device-Id'] = getClientFingerprint();

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
    error.requestId = String(payload?.request_id || detail?.request_id || '').trim() || undefined;
    if (detail && typeof detail === 'object' && typeof detail.error === 'string') {
        error.code = detail.error;
    }
    return error;
}

async function requestJson<T>(
    method: 'GET' | 'POST' | 'PUT' | 'DELETE',
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

        const res = await requestOnce();

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

export async function del<T>(
    path: string,
    options: RequestOptions = {}
): Promise<T> {
    const merged = { showLoading: true, showError: true, ...options };
    return requestJson<T>('DELETE', path, undefined, merged, localeText('处理中...', 'Processing...'));
}

export interface UploadOptions {
    onProgress?: (percent: number) => void;
}

export async function uploadFile(
    path: string,
    filePath: string,
    name: string = 'file',
    options: UploadOptions = {}
): Promise<any> {
    const uploadLabel = localeText('上传中...', 'Uploading...');
    uni.showLoading({ title: uploadLabel });

    try {
        const headers = await buildHeaders(path);
        const res = await new Promise<UniNamespace.UploadFileSuccessCallbackResult>((resolve, reject) => {
            const uploadTask = uni.uploadFile({
                url: resolveApiUrl(path),
                filePath,
                name,
                header: headers,
                success: resolve,
                fail: reject,
            });

            uploadTask.onProgressUpdate((progress) => {
                const pct = Math.round(progress.progress);
                uni.showLoading({ title: `${uploadLabel} ${pct}%`, mask: true });
                if (options.onProgress) options.onProgress(pct);
            });
        });

        if (res.statusCode >= 200 && res.statusCode < 300) {
            return JSON.parse(res.data);
        }

        let payload: any = res.data;
        try { payload = JSON.parse(res.data); } catch {}
        throw createApiError(res.statusCode, payload);
    } catch (error: any) {
        uni.showToast({ title: error.message || 'Upload failed', icon: 'none' });
        throw error;
    } finally {
        uni.hideLoading();
    }
}

export default {
    get,
    post,
    put,
    del,
    uploadFile,
};
