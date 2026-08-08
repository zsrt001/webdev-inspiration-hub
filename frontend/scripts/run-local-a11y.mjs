import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const playwrightCli = path.join(
  frontendRoot,
  'node_modules',
  '@playwright',
  'test',
  'cli.js',
);
const result = spawnSync(
  process.execPath,
  [playwrightCli, 'test', '--grep', '@a11y'],
  {
    cwd: frontendRoot,
    env: { ...process.env, RUN_LOCAL_A11Y: '1' },
    stdio: 'inherit',
  },
);

if (result.error) {
  throw result.error;
}

process.exitCode = result.status ?? 1;
