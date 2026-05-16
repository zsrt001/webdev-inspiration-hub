/** Guest session bootstrap and login flow. */

import { normalizeBaseUrl, resolveDefaultApiBaseUrl } from '../apiConfig';
import { getCurrentSupabaseSession } from '../supabase';
import { storeSession, getToken, getUserId } from './storage';
import { getClientFingerprint, getGuestUserId, isJwtToken } from './identity';
import { GUEST_ID_KEY } from './_keys';

let pendingSessionPromise: Promise<{ userId: string; token: string }> | null = null;

function resolveLoginUrl(apiBaseUrl?: string): string {
    const baseUrl = normalizeBaseUrl(apiBaseUrl) || resolveDefaultApiBaseUrl();
    return `${baseUrl}/auth/login`;
}

function normalizeLoginResponse(payload: any): { userId: string; token: string; username?: string } {
    const data = payload?.data ?? payload ?? {};
    const userId = String(data?.user_id || data?.userId || '').trim();
    const token = String(data?.access_token || data?.accessToken || data?.token || '').trim();
    const username = String(data?.username || '').trim();
    if (!userId || !isJwtToken(token)) {
        throw new Error('Invalid authentication response');
    }
    return { userId, token, username };
}

async function restoreSupabaseSession(): Promise<{ userId: string; token: string } | null> {
    const session = await getCurrentSupabaseSession();
    const token = session?.access_token || '';
    const userId = session?.user?.id || '';
    if (!userId || !isJwtToken(token)) return null;
    return storeSession(userId, token, 'supabase');
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
        header: {
            'X-Visitor-Id': guestId.slice(0, 64),
            'X-Device-Id': getClientFingerprint(),
        },
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
                        header: {
                            'X-Visitor-Id': fallbackGuestId.slice(0, 64),
                            'X-Device-Id': getClientFingerprint(),
                        },
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
