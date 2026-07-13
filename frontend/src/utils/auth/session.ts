/** Restore and reuse the verified Google/Supabase application session. */

import { getCurrentSupabaseSession } from '../supabase';
import { storeSession, getAuthProvider, getToken, getUserId } from './storage';
import { isJwtToken } from './identity';

async function restoreSupabaseSession(): Promise<{ userId: string; token: string } | null> {
    const session = await getCurrentSupabaseSession();
    const token = session?.access_token || '';
    const userId = session?.user?.id || '';
    if (!userId || !isJwtToken(token)) return null;
    return storeSession(userId, token);
}

export async function ensureSession(
    _apiBaseUrl?: string,
): Promise<{ userId: string; token: string } | null> {
    const restoredSession = await restoreSupabaseSession();
    if (restoredSession) return restoredSession;

    const currentUserId = getUserId();
    const currentToken = getToken();
    if (
        getAuthProvider() === 'supabase'
        && currentUserId
        && isJwtToken(currentToken)
    ) {
        return { userId: currentUserId, token: currentToken as string };
    }

    return null;
}
