/** localStorage persistence: store, read, clear session state. */

import { signOutFromSupabase } from '../supabase';
import { AUTH_PROVIDER_KEY, TOKEN_KEY, USER_ID_KEY } from './_keys';
import { isJwtToken } from './identity';

export function storeSession(
    userId: string,
    token: string,
    provider: 'supabase' = 'supabase',
): { userId: string; token: string } {
    uni.setStorageSync(USER_ID_KEY, userId);
    uni.setStorageSync(TOKEN_KEY, token);
    uni.setStorageSync(AUTH_PROVIDER_KEY, provider);
    return { userId, token };
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

export function isLoggedIn(): boolean {
    return isSupabaseLoggedIn();
}

export function isSupabaseLoggedIn(): boolean {
    return getAuthProvider() === 'supabase' && isJwtToken(getToken());
}

export function logout(): void {
    uni.removeStorageSync(USER_ID_KEY);
    uni.removeStorageSync(TOKEN_KEY);
    uni.removeStorageSync(AUTH_PROVIDER_KEY);
    void signOutFromSupabase();
}
