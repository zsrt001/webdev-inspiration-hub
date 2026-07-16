const LOCAL_BACKEND_ORIGIN = 'http://127.0.0.1:8001';

export function isWebRuntime(): boolean {
    return typeof window !== 'undefined' && typeof document !== 'undefined';
}

export function isLocalWebRuntime(): boolean {
    return (
        isWebRuntime() &&
        (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
    );
}

export function normalizeBaseUrl(url?: string): string {
    const trimmed = String(url || '').trim();
    if (!trimmed) return '';
    return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed;
}

export function resolveDefaultApiBaseUrl(): string {
    if (isLocalWebRuntime()) return `${LOCAL_BACKEND_ORIGIN}/api/v1`;
    if (isWebRuntime()) return '/api/v1';
    return `${LOCAL_BACKEND_ORIGIN}/api/v1`;
}

export const API_BASE_URL =
    normalizeBaseUrl(((import.meta as any)?.env?.VITE_API_BASE_URL as string) || '') ||
    resolveDefaultApiBaseUrl();

export function resolveBackendOrigin(): string {
    if (API_BASE_URL.startsWith('http://') || API_BASE_URL.startsWith('https://')) {
        try {
            return new URL(API_BASE_URL).origin;
        } catch {
            return LOCAL_BACKEND_ORIGIN;
        }
    }

    return isWebRuntime() ? '' : LOCAL_BACKEND_ORIGIN;
}
