#!/usr/bin/env node

const args = process.argv.slice(2);
let baseUrl = 'https://www.vowpic.com';
let readinessTimeoutSec = 180;
let skipGenerationBackendProbe = false;

for (let index = 0; index < args.length; index += 1) {
  const arg = args[index];
  if (arg === '--base-url' && args[index + 1]) {
    baseUrl = args[index + 1];
    index += 1;
  } else if (arg === '--readiness-timeout-sec' && args[index + 1]) {
    readinessTimeoutSec = Number(args[index + 1]) || readinessTimeoutSec;
    index += 1;
  } else if (arg === '--skip-generation-backend-probe') {
    skipGenerationBackendProbe = true;
  }
}

const base = baseUrl.replace(/\/+$/, '');

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJson(path, timeoutSec = 45) {
  const url = `${base}${path}`;
  console.log(`GET ${url}`);
  let lastError;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutSec * 1000);
    try {
      const response = await fetch(url, {
        headers: { accept: 'application/json' },
        signal: controller.signal,
      });
      const text = await response.text();
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${text.slice(0, 500)}`);
      }
      return JSON.parse(text);
    } catch (error) {
      lastError = error;
      if (attempt < 3) await sleep(2000 * attempt);
    } finally {
      clearTimeout(timeout);
    }
  }
  throw new Error(`Request failed for ${url}: ${lastError?.message || 'unknown error'}`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const health = await fetchJson('/health', 30);
assert(['healthy', 'ok'].includes(health.status), `Health check failed: ${JSON.stringify(health)}`);

let readinessPath = '/api/v1/ops/readiness?probe_storage=true';
if (!skipGenerationBackendProbe) readinessPath += '&probe_generation_backend=true';
const readiness = await fetchJson(readinessPath, readinessTimeoutSec);
assert(
  readiness.commercial_ready === true,
  `Commercial readiness failed. Blockers: ${JSON.stringify(readiness.blockers || 'unknown')}`,
);

const config = await fetchJson('/api/v1/ops/public_config', 30);
const homeImage = String(config?.placements?.home_banner?.image_url || '');
assert(homeImage.trim(), 'Public config is missing placements.home_banner.image_url');
assert(
  !/(hero_banner|couple_royal_castle|_v2\.png)/.test(homeImage),
  `Public config still points at a legacy heavy image: ${homeImage}`,
);

const templatesPayload = await fetchJson('/api/v1/templates', 30);
const templates = Array.isArray(templatesPayload)
  ? templatesPayload
  : Array.isArray(templatesPayload?.templates)
    ? templatesPayload.templates
    : [];
assert(templates.length >= 8, `Template endpoint returned too few templates: ${templates.length}`);

const summary = {
  base_url: base,
  health_status: health.status,
  commercial_ready: readiness.commercial_ready,
  home_banner_image: homeImage,
  template_count: templates.length,
  storage_probe: readiness?.checks?.storage_rw_probe?.ok ?? null,
  generation_backend_probe: skipGenerationBackendProbe
    ? 'skipped'
    : readiness?.checks?.generation_backend_probe?.ok ?? null,
};

console.log('Production smoke passed.');
console.log(JSON.stringify(summary, null, 2));
