/** Barrel re-export — all existing `import { ... } from '@/utils/auth'` continue to work. */

// Session / guest bootstrap
export { ensureSession, login } from './session';

// Identity & fingerprint
export { getClientFingerprint, getGuestUserId, isJwtToken } from './identity';

// Storage & auth state
export { getAuthProvider, getToken, getUserId, isLoggedIn, isGuestSession, isSupabaseLoggedIn, logout } from './storage';

// Google OAuth (re-export from supabase module for backward compatibility)
import { signInWithGoogle as _googleSignIn } from '../supabase';
export async function signInWithGoogle(): Promise<void> {
    await _googleSignIn();
}

// Default export for `import auth from '@/utils/auth'`
import { ensureSession } from './session';
import { login } from './session';
import { getClientFingerprint, getGuestUserId, isJwtToken } from './identity';
import { getAuthProvider, getToken, getUserId, isLoggedIn, isGuestSession, isSupabaseLoggedIn, logout } from './storage';

const _default = {
    ensureSession,
    login,
    logout,
    getToken,
    getUserId,
    getAuthProvider,
    getClientFingerprint,
    isLoggedIn,
    isGuestSession,
    isSupabaseLoggedIn,
    isJwtToken,
    getGuestUserId,
    signInWithGoogle,
};
export default _default;
