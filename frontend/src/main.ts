import { createSSRApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';

if (typeof window !== 'undefined') {
    window.addEventListener('unhandledrejection', (event) => {
        const reason = event.reason;
        if (reason && typeof reason === 'object' && 'statusCode' in reason) {
            event.preventDefault();
        }
    });
}

export function createApp() {
    const app = createSSRApp(App);
    const pinia = createPinia();

    app.use(pinia);

    return {
        app,
    };
}
