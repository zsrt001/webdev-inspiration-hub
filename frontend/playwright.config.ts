import { defineConfig } from '@playwright/test';

const previewRun = process.env.RUN_PREVIEW_E2E === '1';
const productionRun = process.env.RUN_PRODUCTION_E2E === '1';
const localA11yRun = process.env.RUN_LOCAL_A11Y === '1';
const protectedRun = previewRun || productionRun;
const selectedModes = [previewRun, productionRun, localA11yRun].filter(Boolean).length;
const baseURL = String(
  previewRun
    ? process.env.PREVIEW_BASE_URL || ''
    : productionRun
      ? process.env.PRODUCTION_BASE_URL || ''
      : 'http://127.0.0.1:4173',
).trim().replace(/\/$/, '');
const storageState = String(
  previewRun
    ? process.env.PREVIEW_GOOGLE_STORAGE_STATE_PATH || ''
    : process.env.PRODUCTION_GOOGLE_STORAGE_STATE_PATH || '',
).trim();
const identityEmail = String(
  previewRun
    ? process.env.PREVIEW_GOOGLE_EMAIL || ''
    : process.env.PRODUCTION_GOOGLE_EMAIL || '',
).trim();
const browserChannel = String(process.env.PLAYWRIGHT_BROWSER_CHANNEL || '').trim();

if (selectedModes !== 1) {
  throw new Error(
    'PLAYWRIGHT_MODE_REQUIRED: select exactly one preview, production, or local-a11y mode.',
  );
}
if (protectedRun && (!baseURL || !storageState || !identityEmail)) {
  throw new Error(
    'PROTECTED_IDENTITY_NOT_RUN: protected base URL, Google storage state, and identity email '
    + 'are required for the selected mode.',
  );
}
if (protectedRun) {
  const parsed = new URL(baseURL);
  if (
    parsed.protocol !== 'https:'
    || parsed.username
    || parsed.password
    || parsed.pathname !== '/'
    || parsed.search
    || parsed.hash
  ) {
    throw new Error('PROTECTED_IDENTITY_NOT_RUN: base URL must be an exact HTTPS origin.');
  }
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
  projects: protectedRun ? [
    {
      name: 'chromium',
      use: {
        browserName: 'chromium',
        ...(browserChannel ? { channel: browserChannel } : {}),
      },
    },
  ] : [
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
