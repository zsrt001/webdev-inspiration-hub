import { API_BASE_URL, isWebRuntime } from '../utils/apiConfig';
import {
    clearSupabaseTransientStorage,
    discardSupabaseClient,
    getSupabaseClient,
    refreshSupabaseConfig,
} from '../utils/supabase';

const OAUTH_INTENT_KEY = 'vowpic_oauth_intent';

export interface SessionUser {
    id: string;
    email?: string | null;
    nickname?: string | null;
    avatar_url?: string | null;
    role?: string | null;
    status?: string | null;
    last_login_at?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
}

interface OAuthIntentState {
    intentToken: string;
    redirectPath: string;
    expiresAt: string;
}

let cachedUser: SessionUser | null = null;
let sessionLoaded = false;
let pendingSession: Promise<SessionUser | null> | null = null;

function cookieValue(name: string): string {
    if (!isWebRuntime()) return '';
    const prefix = `${encodeURIComponent(name)}=`;
    const match = document.cookie.split(';').map((item) => item.trim()).find((item) => item.startsWith(prefix));
    if (!match) return '';
    try {
        return decodeURIComponent(match.slice(prefix.length));
    } catch {
        return '';
    }
}

export function getCsrfToken(): string {
    return cookieValue('vowpic_csrf');
}

function authHeaders(method: string): Record<string, string> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase())) {
        const csrf = getCsrfToken();
        if (csrf) headers['X-CSRF-Token'] = csrf;
    }
    return headers;
}

async function authRequest(method: 'GET' | 'POST', path: string, data?: any) {
    return uni.request({
        url: `${API_BASE_URL}${path}`,
        method,
        data,
        header: authHeaders(method),
        withCredentials: true,
    });
}

function responseMessage(response: UniNamespace.RequestSuccessCallbackResult, fallback: string): string {
    const payload: any = response.data || {};
    const detail = payload.detail && typeof payload.detail === 'object' ? payload.detail : payload;
    const message = String(detail.message || payload.message || fallback);
    const requestId = String(detail.request_id || payload.request_id || '').trim();
    return requestId ? `${message} (Reference: ${requestId})` : message;
}

function readIntentState(): OAuthIntentState {
    if (!isWebRuntime()) throw new Error('Google sign-in is only available in the web app.');
    try {
        const parsed = JSON.parse(window.sessionStorage.getItem(OAUTH_INTENT_KEY) || '{}');
        const state: OAuthIntentState = {
            intentToken: String(parsed.intentToken || ''),
            redirectPath: String(parsed.redirectPath || ''),
            expiresAt: String(parsed.expiresAt || ''),
        };
        if (!state.intentToken || !state.redirectPath || !state.expiresAt) throw new Error();
        if (Number.isNaN(Date.parse(state.expiresAt)) || Date.parse(state.expiresAt) <= Date.now()) throw new Error();
        return state;
    } catch {
        throw new Error('The sign-in request is missing or expired. Please start again.');
    }
}

function clearOAuthAttemptState(): void {
    if (!isWebRuntime()) return;
    window.sessionStorage.removeItem(OAUTH_INTENT_KEY);
    clearSupabaseTransientStorage();
}

function clearCallbackState(): void {
    if (!isWebRuntime()) return;
    clearOAuthAttemptState();
    window.history.replaceState(null, document.title, window.location.pathname);
}

export async function startGoogleLogin(nextPath = '/pages/account/index'): Promise<void> {
    if (!isWebRuntime()) throw new Error('Google sign-in is only available in the web app.');
    if (!await refreshSupabaseConfig(true)) {
        throw new Error('Google sign-in is not available on this deployment.');
    }
    const intentResponse = await authRequest('POST', '/auth/oauth-intents', { next_path: nextPath });
    if (intentResponse.statusCode < 200 || intentResponse.statusCode >= 300) {
        throw new Error(responseMessage(intentResponse, 'Unable to start sign-in.'));
    }
    const payload: any = intentResponse.data || {};
    const state: OAuthIntentState = {
        intentToken: String(payload.intent_token || ''),
        redirectPath: String(payload.redirect_path || ''),
        expiresAt: String(payload.expires_at || ''),
    };
    if (!state.intentToken || !state.redirectPath || !state.expiresAt || !payload.callback_url) {
        throw new Error('The sign-in service returned an invalid intent.');
    }
    clearOAuthAttemptState();
    window.sessionStorage.setItem(OAUTH_INTENT_KEY, JSON.stringify(state));

    try {
        const supabase = await getSupabaseClient();
        const { data, error } = await supabase.auth.signInWithOAuth({
            provider: 'google',
            options: {
                redirectTo: String(payload.callback_url),
                skipBrowserRedirect: true,
                queryParams: { prompt: 'select_account' },
            },
        });
        if (error || !data.url) {
            throw new Error(error?.message || 'Unable to open Google sign-in.');
        }
        window.location.assign(data.url);
    } catch (error) {
        clearOAuthAttemptState();
        discardSupabaseClient();
        throw error;
    }
}

export async function finishGoogleLogin(): Promise<SessionUser> {
    if (!isWebRuntime()) throw new Error('Google sign-in is only available in the web app.');
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    if (fragment.has('access_token')) {
        clearCallbackState();
        discardSupabaseClient();
        throw new Error('Implicit OAuth token fragments are not accepted. Please start again.');
    }
    const query = new URLSearchParams(window.location.search);
    const oauthError = String(query.get('error_description') || query.get('error') || '').trim();
    if (oauthError) {
        clearCallbackState();
        discardSupabaseClient();
        throw new Error('Google sign-in was not completed.');
    }
    const code = String(query.get('code') || '').trim();
    if (!code) {
        clearCallbackState();
        discardSupabaseClient();
        throw new Error('The Google authorization code is missing.');
    }

    let intent: OAuthIntentState | null = null;
    let exchange: UniNamespace.RequestSuccessCallbackResult | null = null;
    try {
        intent = readIntentState();
        const supabase = await getSupabaseClient();
        const { data, error } = await supabase.auth.exchangeCodeForSession(code);
        const accessToken = String(data.session?.access_token || '').trim();
        if (error || !accessToken) {
            throw new Error(error?.message || 'Google sign-in exchange failed.');
        }
        exchange = await authRequest('POST', '/auth/supabase/session', {
            access_token: accessToken,
            intent_token: intent.intentToken,
        });
    } finally {
        discardSupabaseClient();
        clearCallbackState();
    }
    if (!intent || !exchange) {
        throw new Error('The local sign-in session could not be created.');
    }
    if (exchange.statusCode < 200 || exchange.statusCode >= 300) {
        throw new Error(responseMessage(exchange, 'The local sign-in session could not be created.'));
    }
    cachedUser = exchange.data as SessionUser;
    sessionLoaded = true;
    window.location.replace(intent.redirectPath);
    return cachedUser;
}

async function fetchSessionUser(): Promise<SessionUser | null> {
    const response = await authRequest('GET', '/auth/me');
    if (response.statusCode >= 200 && response.statusCode < 300) {
        cachedUser = response.data as SessionUser;
        sessionLoaded = true;
        return cachedUser;
    }
    if (response.statusCode === 401) return null;
    throw new Error(responseMessage(response, 'Unable to verify the current session.'));
}

export async function refreshLocalSession(): Promise<boolean> {
    const response = await authRequest('POST', '/auth/refresh');
    if (response.statusCode >= 200 && response.statusCode < 300) return true;
    if (response.statusCode === 401 || response.statusCode === 403) {
        cachedUser = null;
        sessionLoaded = true;
        return false;
    }
    throw new Error(responseMessage(response, 'Unable to refresh the session.'));
}

export async function ensureSession(force = false): Promise<SessionUser | null> {
    if (!force && sessionLoaded) return cachedUser;
    if (!force && pendingSession) return pendingSession;
    // Every server-issued browser session includes the readable CSRF cookie.
    // Its absence is a fail-closed signal that there is no local session to
    // verify or refresh, so public pages should not generate expected 401s.
    if (isWebRuntime() && !getCsrfToken()) {
        cachedUser = null;
        sessionLoaded = true;
        return null;
    }
    pendingSession = (async () => {
        let user = await fetchSessionUser();
        if (!user && await refreshLocalSession()) user = await fetchSessionUser();
        cachedUser = user;
        sessionLoaded = true;
        return user;
    })().finally(() => {
        pendingSession = null;
    });
    return pendingSession;
}

export function getCachedSessionUser(): SessionUser | null {
    return cachedUser;
}

export function isSupabaseLoggedIn(): boolean {
    return sessionLoaded && cachedUser !== null;
}

export function clearCachedSession(): void {
    cachedUser = null;
    sessionLoaded = true;
}

export async function logout(): Promise<void> {
    let response = await authRequest('POST', '/auth/logout');
    if (response.statusCode === 401 && await refreshLocalSession()) {
        response = await authRequest('POST', '/auth/logout');
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
        throw new Error(responseMessage(response, 'Sign-out failed. Please retry.'));
    }
    clearCachedSession();
}

export const signInWithGoogle = startGoogleLogin;
