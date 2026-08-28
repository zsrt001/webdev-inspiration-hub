/** Browser authentication is a server-owned Cookie session; no bearer is exposed to UI code. */

export {
    clearCachedSession,
    ensureSession,
    finishGoogleLogin,
    getCachedSessionUser,
    getCsrfToken,
    isSupabaseLoggedIn,
    localizedAuthError,
    logout,
    refreshLocalSession,
    signInWithGoogle,
    startGoogleLogin,
    type AuthLocale,
    type GoogleLoginOptions,
    type SessionUser,
} from '../../services/auth';

export { getClientFingerprint } from './identity';
