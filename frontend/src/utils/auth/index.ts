/** Browser authentication is a server-owned Cookie session; no bearer is exposed to UI code. */

export {
    clearCachedSession,
    ensureSession,
    finishGoogleLogin,
    getCachedSessionUser,
    getCsrfToken,
    isSupabaseLoggedIn,
    logout,
    refreshLocalSession,
    signInWithGoogle,
    startGoogleLogin,
    type SessionUser,
} from '../../services/auth';

export { getClientFingerprint } from './identity';
