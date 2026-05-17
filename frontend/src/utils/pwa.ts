import { trackEvent } from './analytics';

const SW_PATH = '/sw.js';

function shouldRegisterPwa(): boolean {
    if (typeof window === 'undefined' || typeof navigator === 'undefined') return false;
    if (!('serviceWorker' in navigator)) return false;
    const host = window.location.hostname;
    return window.location.protocol === 'https:' || host === 'localhost' || host === '127.0.0.1';
}

export function registerPwa(): void {
    if (!shouldRegisterPwa()) return;

    window.addEventListener('load', () => {
        navigator.serviceWorker.register(SW_PATH, { scope: '/' })
            .then((registration) => {
                void trackEvent({
                    eventType: 'pwa_service_worker_ready',
                    sourcePage: 'app',
                    meta: {
                        scope: registration.scope,
                        push_deferred: true,
                    },
                });
            })
            .catch(() => undefined);
    }, { once: true });
}

export function installPromptSupported(): boolean {
    return typeof window !== 'undefined' && 'BeforeInstallPromptEvent' in window;
}

