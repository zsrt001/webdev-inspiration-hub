import { HttpError, httpRequest, httpUpload } from '../services/http';
import { API_BASE_URL, isWebRuntime, resolveBackendOrigin } from './apiConfig';

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

    return isWebRuntime() ? withLeadingSlash : `${backendOrigin}${withLeadingSlash}`;
}

interface RequestOptions {
    showLoading?: boolean;
    showError?: boolean;
    headers?: Record<string, string>;
}

export type ApiError = HttpError;

function transportOptions(options: RequestOptions): {
    headers: Record<string, string>;
    idempotencyKey?: string;
} {
    const headers: Record<string, string> = {};
    let idempotencyKey: string | undefined;
    for (const [name, value] of Object.entries(options.headers ?? {})) {
        if (name.toLowerCase() === 'idempotency-key') {
            idempotencyKey = value;
        } else {
            headers[name] = value;
        }
    }
    return { headers, idempotencyKey };
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
        const transport = transportOptions(options);
        return await httpRequest<T>(path, {
            method,
            body: data,
            headers: transport.headers,
            idempotencyKey: transport.idempotencyKey,
        });
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
        return await httpUpload(path, filePath, name, {
            onProgress(percent) {
                uni.showLoading({ title: `${uploadLabel} ${percent}%`, mask: true });
                options.onProgress?.(percent);
            },
        });
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
