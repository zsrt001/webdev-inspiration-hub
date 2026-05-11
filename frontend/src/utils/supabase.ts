import { API_BASE_URL, isH5Runtime, resolveBackendOrigin } from './apiConfig';

type Session = {
    access_token: string;
    user: {
        id: string;
        email?: string | null;
    };
};

type OAuthParams = {
    accessToken: string;
    error: string;
};

let googleAuthConfigured = false;
let publicConfigLoaded = false;
let pendingPublicConfig: Promise<boolean> | null = null;

function getOAuthParams(): OAuthParams {
    if (!isH5Runtime()) return { accessToken: '', error: '' };

    const hash = window.location.hash.startsWith('#')
        ? window.location.hash.slice(1)
        : window.location.hash;
    const hashParams = new URLSearchParams(hash);
    const queryParams = new URLSearchParams(window.location.search);

    return {
        accessToken: String(hashParams.get('access_token') || queryParams.get('access_token') || '').trim(),
        error: String(
            hashParams.get('error_description') ||
            hashParams.get('error') ||
            queryParams.get('error_description') ||
            queryParams.get('error') ||
            '',
        ).trim(),
    };
}

function clearOAuthParams(): void {
    if (!isH5Runtime()) return;
    if (!window.location.hash && !/[?&](access_token|error|error_description)=/.test(window.location.search)) return;

    window.history.replaceState(
        null,
        document.title,
        `${window.location.pathname}${stripOAuthQuery(window.location.search)}`,
    );
}

function stripOAuthQuery(search: string): string {
    if (!search) return '';
    const params = new URLSearchParams(search);
    ['access_token', 'expires_at', 'expires_in', 'provider_token', 'refresh_token', 'token_type', 'type', 'error', 'error_description'].forEach((key) => {
        params.delete(key);
    });
    const next = params.toString();
    return next ? `?${next}` : '';
}

function decodeJwtPayload(token: string): Record<string, any> {
    const part = token.split('.')[1] || '';
    if (!part) return {};
    const padded = part.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(part.length / 4) * 4, '=');
    try {
        return JSON.parse(window.atob(padded));
    } catch {
        return {};
    }
}

async function exchangeSupabaseAccessToken(accessToken: string): Promise<Session | null> {
    // Include previous guest ID for account merge
    let previousGuestId = '';
    try {
        previousGuestId = String(uni.getStorageSync('ai_wedding_guest_id') || '').trim();
    } catch {}
    const data: Record<string, string> = { access_token: accessToken };
    if (previousGuestId) {
        data.previous_guest_id = previousGuestId;
    }
    const response = await uni.request({
        url: `${API_BASE_URL}/auth/supabase/session`,
        method: 'POST',
        data,
        header: {
            'Content-Type': 'application/json',
        },
    });

    if (response.statusCode < 200 || response.statusCode >= 300) {
        return null;
    }

    const payload: any = response.data || {};
    const access_token = String(payload.access_token || payload.token || '').trim();
    const userId = String(payload.user_id || payload.userId || '').trim();
    if (!access_token || !userId || access_token.split('.').length !== 3) {
        return null;
    }

    const claims = decodeJwtPayload(accessToken);
    return {
        access_token,
        user: {
            id: userId,
            email: typeof claims.email === 'string' ? claims.email : null,
        },
    };
}

export function isSupabaseConfigured(): boolean {
    return isH5Runtime() && googleAuthConfigured;
}

function readGoogleAuthEnabled(payload: any): boolean {
    const auth = payload?.auth;
    if (!auth || typeof auth !== 'object') return false;
    return Boolean(auth.google_oauth_enabled || auth.google_enabled || auth.supabase_enabled);
}

export async function refreshSupabaseConfig(force = false): Promise<boolean> {
    if (!isH5Runtime()) {
        googleAuthConfigured = false;
        publicConfigLoaded = true;
        return false;
    }

    if (publicConfigLoaded && !force) return googleAuthConfigured;
    if (pendingPublicConfig && !force) return pendingPublicConfig;

    pendingPublicConfig = uni.request({
        url: `${API_BASE_URL}/ops/public_config`,
        method: 'GET',
    }).then((response) => {
        googleAuthConfigured = response.statusCode >= 200
            && response.statusCode < 300
            && readGoogleAuthEnabled(response.data);
        publicConfigLoaded = true;
        return googleAuthConfigured;
    }).catch(() => {
        googleAuthConfigured = false;
        publicConfigLoaded = true;
        return false;
    }).finally(() => {
        pendingPublicConfig = null;
    });

    return pendingPublicConfig;
}

export function getSupabaseClient(): null {
    return null;
}

export async function getCurrentSupabaseSession(): Promise<Session | null> {
    const params = getOAuthParams();
    if (params.error) {
        clearOAuthParams();
        return null;
    }

    if (!params.accessToken) {
        return null;
    }

    try {
        return await exchangeSupabaseAccessToken(params.accessToken);
    } finally {
        clearOAuthParams();
    }
}

export function getSupabaseRedirectUrl(): string {
    if (!isH5Runtime()) return 'http://localhost:3000/pages/account/index';
    return `${window.location.origin}/pages/account/index`;
}

export async function signInWithGoogle(): Promise<void> {
    if (!isH5Runtime()) {
        throw new Error('Google sign-in is only available in the web app');
    }

    const available = await refreshSupabaseConfig();
    if (!available) {
        throw new Error('Google sign-in is not available on this deployment.');
    }

    const backendOrigin = resolveBackendOrigin();
    const startUrl = `${backendOrigin || ''}/api/v1/auth/supabase/google/start?next=${encodeURIComponent('/pages/account/index')}`;
    window.location.assign(startUrl);
}

export async function signOutFromSupabase(): Promise<void> {
    return;
}
