/**
 * Authentication utilities for Web guest sessions and WeChat login.
 * All platforms exchange against the backend `/auth/login` endpoint.
 */

import { normalizeBaseUrl, resolveDefaultApiBaseUrl } from './apiConfig';
import {
    getCurrentSupabaseSession,
    isSupabaseConfigured,
    signInWithGoogle as startSupabaseGoogleSignIn,
    signOutFromSupabase,
} from './supabase';

const USER_ID_KEY = 'ai_wedding_user_id';
const TOKEN_KEY = 'ai_wedding_token';
const GUEST_ID_KEY = 'ai_wedding_guest_id';
const AUTH_PROVIDER_KEY = 'ai_wedding_auth_provider';

let pendingSessionPromise: Promise<{ userId: string; token: string }> | null = null;

function generateUUID(): string {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
        const random = (Math.random() * 16) | 0;
        const value = char === 'x' ? random : (random & 0x3) | 0x8;
        return value.toString(16);
    });
}

function resolveLoginUrl(apiBaseUrl?: string): string {
    const baseUrl = normalizeBaseUrl(apiBaseUrl) || resolveDefaultApiBaseUrl();
    return `${baseUrl}/auth/login`;
}

function normalizeLoginResponse(payload: any): { userId: string; token: string } {
    const data = payload?.data ?? payload ?? {};
    const userId = String(data?.user_id || data?.userId || '').trim();
    const token = String(data?.access_token || data?.accessToken || data?.token || '').trim();

    if (!userId || !isJwtToken(token)) {
        throw new Error('Invalid authentication response');
    }

    return { userId, token };
}

function storeSession(userId: string, token: string, provider: 'local' | 'supabase' = 'local'): { userId: string; token: string } {
    uni.setStorageSync(USER_ID_KEY, userId);
    uni.setStorageSync(TOKEN_KEY, token);
    uni.setStorageSync(AUTH_PROVIDER_KEY, provider);
    return { userId, token };
}

async function restoreSupabaseSession(): Promise<{ userId: string; token: string } | null> {
    if (!isSupabaseConfigured()) return null;
    const session = await getCurrentSupabaseSession();
    const token = session?.access_token || '';
    const userId = session?.user?.id || '';
    if (!userId || !isJwtToken(token)) return null;
    return storeSession(userId, token, 'supabase');
}

export function isJwtToken(token: string | null | undefined): boolean {
    return typeof token === 'string' && token.split('.').length === 3;
}

export function getGuestUserId(): string {
    const currentUserId = String(uni.getStorageSync(USER_ID_KEY) || '').trim();
    if (currentUserId.startsWith('guest_')) {
        return currentUserId;
    }

    let guestUserId = String(uni.getStorageSync(GUEST_ID_KEY) || '').trim();
    if (!guestUserId) {
        guestUserId = `guest_${generateUUID()}`;
        uni.setStorageSync(GUEST_ID_KEY, guestUserId);
    }
    return guestUserId;
}

export async function login(apiBaseUrl?: string): Promise<{ userId: string; token: string }> {
    const loginUrl = resolveLoginUrl(apiBaseUrl);

    // #ifdef H5
    const supabaseSession = await restoreSupabaseSession();
    if (supabaseSession) return supabaseSession;

    const guestId = getGuestUserId();
    const response = await uni.request({
        url: loginUrl,
        method: 'POST',
        data: { code: `web_${guestId}` },
    });

    if (response.statusCode < 200 || response.statusCode >= 300) {
        throw new Error('Failed to create web session');
    }

    const session = normalizeLoginResponse(response.data);
    return storeSession(session.userId, session.token, 'local');
    // #endif

    // #ifdef MP-WEIXIN
    return new Promise((resolve, reject) => {
        uni.login({
            provider: 'weixin',
            success: async (loginRes: UniApp.LoginRes) => {
                try {
                    const fallbackGuestId = getGuestUserId();
                    const response = await uni.request({
                        url: loginUrl,
                        method: 'POST',
                        data: { code: (loginRes.code || '').trim() || `mp_${fallbackGuestId}` },
                    });

                    if (response.statusCode < 200 || response.statusCode >= 300) {
                        reject(new Error('Failed to create mini program session'));
                        return;
                    }

                    const session = normalizeLoginResponse(response.data);
                    resolve(storeSession(session.userId, session.token, 'local'));
                } catch (error) {
                    reject(error);
                }
            },
            fail: reject,
        });
    });
    // #endif
}

export async function ensureSession(apiBaseUrl?: string): Promise<{ userId: string; token: string } | null> {
    // #ifdef H5
    const supabaseSession = await restoreSupabaseSession();
    if (supabaseSession) return supabaseSession;
    // #endif

    const currentUserId = getUserId();
    const currentToken = getToken();
    if (currentUserId && isJwtToken(currentToken)) {
        return { userId: currentUserId, token: currentToken as string };
    }

    if (!pendingSessionPromise) {
        pendingSessionPromise = login(apiBaseUrl).finally(() => {
            pendingSessionPromise = null;
        });
    }

    return pendingSessionPromise;
}

export function getToken(): string | null {
    const token = String(uni.getStorageSync(TOKEN_KEY) || '').trim();
    return token || null;
}

export function getUserId(): string | null {
    const userId = String(uni.getStorageSync(USER_ID_KEY) || '').trim();
    return userId || null;
}

export function getAuthProvider(): string | null {
    const provider = String(uni.getStorageSync(AUTH_PROVIDER_KEY) || '').trim();
    return provider || null;
}

export function isSupabaseLoggedIn(): boolean {
    return getAuthProvider() === 'supabase' && isJwtToken(getToken());
}

export function isLoggedIn(): boolean {
    return !!getUserId() && isJwtToken(getToken());
}

export function logout(): void {
    uni.removeStorageSync(USER_ID_KEY);
    uni.removeStorageSync(TOKEN_KEY);
    uni.removeStorageSync(AUTH_PROVIDER_KEY);
    void signOutFromSupabase();
}

export async function signInWithGoogle(): Promise<void> {
    await startSupabaseGoogleSignIn();
}

export default {
    ensureSession,
    login,
    logout,
    getToken,
    getUserId,
    getAuthProvider,
    isLoggedIn,
    isSupabaseLoggedIn,
    isJwtToken,
    getGuestUserId,
    signInWithGoogle,
};
