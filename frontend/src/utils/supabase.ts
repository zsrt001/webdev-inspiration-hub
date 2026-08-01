import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import { API_BASE_URL, isWebRuntime } from './apiConfig';

const SUPABASE_PKCE_STORAGE_KEY = 'vowpic_supabase_pkce';

interface PublicAuthConfig {
    google_oauth_enabled?: boolean;
    supabase_url?: string;
    supabase_publishable_key?: string;
}

let authConfig: PublicAuthConfig | null = null;
let configRequest: Promise<boolean> | null = null;
let client: SupabaseClient | null = null;

function normalizedAuthConfig(payload: any): PublicAuthConfig {
    const source = payload?.auth && typeof payload.auth === 'object' ? payload.auth : {};
    return {
        google_oauth_enabled: Boolean(source.google_oauth_enabled),
        supabase_url: String(source.supabase_url || '').trim().replace(/\/$/, ''),
        supabase_publishable_key: String(source.supabase_publishable_key || '').trim(),
    };
}

function isUsableConfig(config: PublicAuthConfig | null): boolean {
    if (!isWebRuntime() || !config?.google_oauth_enabled) return false;
    if (!config.supabase_url || !config.supabase_publishable_key) return false;
    try {
        const url = new URL(config.supabase_url);
        return url.protocol === 'https:' && url.pathname === '/';
    } catch {
        return false;
    }
}

export async function refreshSupabaseConfig(force = false): Promise<boolean> {
    if (!isWebRuntime()) return false;
    if (!force && authConfig) return isUsableConfig(authConfig);
    if (!force && configRequest) return configRequest;

    configRequest = fetch(`${API_BASE_URL}/ops/public_config`, {
        method: 'GET',
        credentials: 'include',
        headers: { Accept: 'application/json' },
    }).then(async (response) => {
        authConfig = response.ok
            ? normalizedAuthConfig(await response.json())
            : null;
        if (!isUsableConfig(authConfig)) client = null;
        return isUsableConfig(authConfig);
    }).catch(() => {
        authConfig = null;
        client = null;
        return false;
    }).finally(() => {
        configRequest = null;
    });
    return configRequest;
}

export async function getSupabaseClient(): Promise<SupabaseClient> {
    const available = await refreshSupabaseConfig();
    if (!available || !authConfig?.supabase_url || !authConfig.supabase_publishable_key) {
        throw new Error('Google sign-in is not available on this deployment.');
    }
    if (!client) {
        client = createClient(authConfig.supabase_url, authConfig.supabase_publishable_key, {
            auth: {
                flowType: 'pkce',
                persistSession: false,
                autoRefreshToken: false,
                detectSessionInUrl: false,
                storage: window.sessionStorage,
                storageKey: SUPABASE_PKCE_STORAGE_KEY,
            },
        });
    }
    return client;
}

export function discardSupabaseClient(): void {
    client = null;
}

export function clearSupabaseTransientStorage(): void {
    if (!isWebRuntime()) return;
    window.sessionStorage.removeItem(`${SUPABASE_PKCE_STORAGE_KEY}`);
    window.sessionStorage.removeItem(`${SUPABASE_PKCE_STORAGE_KEY}-code-verifier`);
    window.sessionStorage.removeItem(`${SUPABASE_PKCE_STORAGE_KEY}-user`);
}
