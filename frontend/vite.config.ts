import { defineConfig } from 'vite';
import uni from '@dcloudio/vite-plugin-uni';
import { resolve } from 'path';
import { existsSync, mkdirSync, readFileSync, copyFileSync } from 'fs';
import type { Connect } from 'vite';

const PWA_PUBLIC_FILES = [
    'manifest.webmanifest',
    'sw.js',
    'offline.html',
    'icons/pwa-icon.svg',
];

function contentType(pathname: string): string {
    if (pathname.endsWith('.webmanifest')) return 'application/manifest+json; charset=utf-8';
    if (pathname.endsWith('.js')) return 'application/javascript; charset=utf-8';
    if (pathname.endsWith('.svg')) return 'image/svg+xml; charset=utf-8';
    return 'text/html; charset=utf-8';
}

function pwaStaticPlugin() {
    const publicRoot = resolve(__dirname, 'public');
    const outputRoot = resolve(__dirname, 'dist/build/h5');
    return {
        name: 'ai-wedding-pwa-static',
        configureServer(server: { middlewares: Connect.Server }) {
            server.middlewares.use((req, res, next) => {
                const pathname = String(req.url || '').split('?')[0].replace(/^\/+/, '');
                if (!PWA_PUBLIC_FILES.includes(pathname)) return next();
                const source = resolve(publicRoot, pathname);
                if (!existsSync(source)) return next();
                res.setHeader('Content-Type', contentType(pathname));
                res.end(readFileSync(source));
            });
        },
        closeBundle() {
            for (const file of PWA_PUBLIC_FILES) {
                const source = resolve(publicRoot, file);
                if (!existsSync(source)) continue;
                const target = resolve(outputRoot, file);
                mkdirSync(resolve(target, '..'), { recursive: true });
                copyFileSync(source, target);
            }
        },
    };
}

export default defineConfig({
    plugins: [uni(), pwaStaticPlugin()],
    resolve: {
        alias: {
            '@': resolve(__dirname, 'src'),
        },
    },
    server: {
        host: '127.0.0.1',
        port: 3000,
        strictPort: true
    }
});
