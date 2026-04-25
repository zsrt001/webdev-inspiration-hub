import { createClient, type SupabaseClient, type Session } from '@supabase/supabase-js';

const SUPABASE_URL = String((import.meta as any)?.env?.VITE_SUPABASE_URL || '').trim();
const SUPABASE_ANON_KEY = String((import.meta as any)?.env?.VITE_SUPABASE_ANON_KEY || '').trim();

let client: SupabaseClient | null = null;

export function isSupabaseConfigured(): boolean {
    return !!SUPABASE_URL && !!SUPABASE_ANON_KEY;
}

export function getSupabaseClient(): SupabaseClient | null {
    if (!isSupabaseConfigured()) return null;
    if (!client) {
        client = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
            auth: {
                persistSession: true,
                autoRefreshToken: true,
                detectSessionInUrl: true,
                flowType: 'pkce',
            },
        });
    }
    return client;
}

export async function getCurrentSupabaseSession(): Promise<Session | null> {
    const supabase = getSupabaseClient();
    if (!supabase) return null;

    const { data, error } = await supabase.auth.getSession();
    if (error) return null;
    if (data.session) return data.session;

    if (typeof window !== 'undefined') {
        const code = new URLSearchParams(window.location.search).get('code');
        if (code) {
            const exchanged = await supabase.auth.exchangeCodeForSession(code);
            return exchanged.data.session || null;
        }
    }

    return null;
}

export function getSupabaseRedirectUrl(): string {
    if (typeof window === 'undefined') return 'http://localhost:3000/';
    return `${window.location.origin}/`;
}

export async function signInWithGoogle(): Promise<void> {
    const supabase = getSupabaseClient();
    if (!supabase) {
        throw new Error('Supabase is not configured');
    }
    const { error } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: {
            redirectTo: getSupabaseRedirectUrl(),
            scopes: 'email profile',
        },
    });
    if (error) throw error;
}

export async function signOutFromSupabase(): Promise<void> {
    const supabase = getSupabaseClient();
    if (!supabase) return;
    await supabase.auth.signOut();
}
