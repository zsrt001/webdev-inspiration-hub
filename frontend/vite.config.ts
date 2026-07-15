import { defineConfig } from 'vite';
import uni from '@dcloudio/vite-plugin-uni';
import { resolve } from 'path';
import { copyFileSync, existsSync, mkdirSync, readFileSync } from 'fs';
import type { Connect, Plugin } from 'vite';

const PWA_PUBLIC_FILES = [
    'manifest.webmanifest',
    'sw.js',
    'offline.html',
    'icons/pwa-icon.svg',
];
const WEB_OUTPUT_ROOT = resolve(__dirname, 'dist/build/h5');

const UNI_REMOTE_SHADOW_STYLE = '/@dcloudio/uni-h5/style/framework/shadow.css';
const FORBIDDEN_BROWSER_ASSET_HOSTS = [
    'cdn.dcloud.net.cn',
    'fonts.googleapis.com',
    'fonts.gstatic.com',
];
const UNI_REMOTE_SHADOW_URL = /https:\/\/cdn\.dcloud\.net\.cn\/img\/shadow-[a-z]+\.png/gi;
const INLINE_TRANSPARENT_PIXEL = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';

function contentType(pathname: string): string {
    if (pathname.endsWith('.webmanifest')) return 'application/manifest+json; charset=utf-8';
    if (pathname.endsWith('.js')) return 'application/javascript; charset=utf-8';
    if (pathname.endsWith('.svg')) return 'image/svg+xml; charset=utf-8';
    return 'text/html; charset=utf-8';
}

function pwaStaticPlugin() {
    const publicRoot = resolve(__dirname, 'public');
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
                const target = resolve(WEB_OUTPUT_ROOT, file);
                mkdirSync(resolve(target, '..'), { recursive: true });
                copyFileSync(source, target);
            }
        },
    };
}

function webAssetPolicyPlugin(): Plugin {
    return {
        name: 'vowpic-web-asset-policy',
        enforce: 'post',
        transform(_code, id) {
            const normalizedId = id.replace(/\\/g, '/').split('?')[0];
            if (!normalizedId.endsWith(UNI_REMOTE_SHADOW_STYLE)) return null;

            // Uni-app injects a delayed request to its public CDN solely to
            // preload a native-navigation shadow. VowPic uses its own Web
            // navigation and a self-only CSP, so the preload is inapplicable.
            return { code: '', map: null };
        },
        generateBundle(_options, bundle) {
            const violations: string[] = [];
            for (const [fileName, output] of Object.entries(bundle)) {
                if (fileName.endsWith('.map')) continue;

                if (
                    output.type === 'asset'
                    && fileName.endsWith('.css')
                    && typeof output.source === 'string'
                ) {
                    output.source = output.source.replace(
                        UNI_REMOTE_SHADOW_URL,
                        INLINE_TRANSPARENT_PIXEL,
                    );
                }

                const source = output.type === 'asset'
                    ? String(output.source)
                    : output.code;
                for (const host of FORBIDDEN_BROWSER_ASSET_HOSTS) {
                    if (source.includes(host)) violations.push(`${fileName}:${host}`);
                }
            }
            if (violations.length > 0) {
                this.error(`forbidden remote browser assets: ${violations.join(', ')}`);
            }
        },
    };
}

export default defineConfig({
    plugins: [
        uni(),
        webAssetPolicyPlugin(),
        pwaStaticPlugin(),
    ],
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
