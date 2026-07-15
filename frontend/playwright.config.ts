import { defineConfig } from '@playwright/test';

const protectedRun = process.env.RUN_PREVIEW_E2E === '1';
const localA11yRun = process.env.RUN_LOCAL_A11Y === '1';
const baseURL = String(
  protectedRun ? process.env.PREVIEW_BASE_URL || '' : 'http://127.0.0.1:4173',
).trim().replace(/\/$/, '');
const storageState = String(process.env.PREVIEW_GOOGLE_STORAGE_STATE_PATH || '').trim();
const identityEmail = String(process.env.PREVIEW_GOOGLE_EMAIL || '').trim();
const browserChannel = String(process.env.PLAYWRIGHT_BROWSER_CHANNEL || '').trim();

if (protectedRun === localA11yRun) {
  throw new Error('PLAYWRIGHT_MODE_REQUIRED: select exactly one protected or local-a11y mode.');
}
if (protectedRun && (!baseURL || !storageState || !identityEmail)) {
  throw new Error(
    'PREVIEW_IDENTITY_NOT_RUN: RUN_PREVIEW_E2E=1, PREVIEW_BASE_URL, '
    + 'PREVIEW_GOOGLE_STORAGE_STATE_PATH, and PREVIEW_GOOGLE_EMAIL are required.',
  );
}
if (protectedRun && !baseURL.startsWith('https://')) {
  throw new Error('PREVIEW_IDENTITY_NOT_RUN: PREVIEW_BASE_URL must be an exact HTTPS origin.');
}

export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: [['line']],
  ...(localA11yRun ? {
    webServer: {
      command: 'npm run preview:web',
      url: baseURL,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  } : {}),
  use: {
    baseURL,
    ...(protectedRun ? { storageState } : {}),
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: protectedRun ? 'retain-on-failure' : 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        browserName: 'chromium',
        ...(browserChannel ? { channel: browserChannel } : {}),
      },
    },
    {
      name: 'firefox',
      use: { browserName: 'firefox' },
    },
  ],
});
