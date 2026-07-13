/** Public authentication helpers for the overseas Web application. */

export { ensureSession } from './session';
export { getClientFingerprint, isJwtToken } from './identity';
export { getAuthProvider, getToken, getUserId, isLoggedIn, isSupabaseLoggedIn, logout } from './storage';

import { signInWithGoogle as _googleSignIn } from '../supabase';
export async function signInWithGoogle(): Promise<void> {
    await _googleSignIn();
}

import { ensureSession } from './session';
import { getClientFingerprint, isJwtToken } from './identity';
import { getAuthProvider, getToken, getUserId, isLoggedIn, isSupabaseLoggedIn, logout } from './storage';

const auth = {
    ensureSession,
    logout,
    getToken,
    getUserId,
    getAuthProvider,
    getClientFingerprint,
    isLoggedIn,
    isSupabaseLoggedIn,
    isJwtToken,
    signInWithGoogle,
};

export default auth;
