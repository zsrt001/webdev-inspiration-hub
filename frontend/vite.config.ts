import { defineConfig } from 'vite';
import uni from '@dcloudio/vite-plugin-uni';
import { resolve } from 'path';

export default defineConfig({
    plugins: [uni()],
    resolve: {
        alias: {
            '@': resolve(__dirname, 'src'),
        },
    },
    server: {
        host: '127.0.0.1',
        port: 5173,
        strictPort: true
    }
});
