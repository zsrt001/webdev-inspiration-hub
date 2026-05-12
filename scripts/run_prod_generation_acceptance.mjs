#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { basename, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));

function argValue(name, fallback = undefined) {
  const prefix = `${name}=`;
  const inline = process.argv.find((arg) => arg.startsWith(prefix));
  if (inline) return inline.slice(prefix.length);
  const index = process.argv.indexOf(name);
  if (index >= 0 && process.argv[index + 1]) return process.argv[index + 1];
  return fallback;
}

const baseUrl = String(argValue('--base-url', process.env.PROD_BASE_URL || 'https://www.vowpic.com')).replace(/\/$/, '');
const token = argValue('--admin-token', process.env.ADMIN_TOKEN);
const bearerToken = argValue('--bearer-token', process.env.ADMIN_BEARER_TOKEN);
const sourcesPath = argValue(
  '--sources',
  join(root, 'artifacts', 'production-generation-test', 'uploaded-sources.json'),
);
const outDir = argValue('--out-dir', join(root, 'artifacts', 'production-generation-test', 'generated'));
const pollSeconds = Number(argValue('--poll-seconds', process.env.POLL_SECONDS || '420'));
const pollIntervalMs = Number(argValue('--poll-interval-ms', process.env.POLL_INTERVAL_MS || '5000'));

if (!token && !bearerToken) {
  throw new Error('Provide --admin-token or --bearer-token for production admin auth.');
}

const headers = {
  accept: 'application/json',
  'content-type': 'application/json',
};
if (token) headers['X-Admin-Token'] = token;
if (bearerToken) headers.authorization = `Bearer ${bearerToken}`;

async function requestJson(path, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers: {
      ...headers,
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { raw: text.slice(0, 500) };
  }
  if (!response.ok) {
    const detail = body?.detail || body?.message || body?.raw || response.statusText;
    throw new Error(`${options.method || 'GET'} ${path} failed (${response.status}): ${JSON.stringify(detail)}`);
  }
  return body;
}

function collectImageUrls(value, urls = []) {
  if (!value) return urls;
  if (typeof value === 'string' && /^https?:\/\//.test(value)) {
    urls.push(value);
    return urls;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectImageUrls(item, urls);
    return urls;
  }
  if (typeof value === 'object') {
    for (const item of Object.values(value)) collectImageUrls(item, urls);
  }
  return urls;
}

async function startProbe(test) {
  const payload = {
    image_url: test.image_url,
    second_image_url: test.second_image_url,
    template_id: test.template_id,
    remote_join: Boolean(test.remote_join),
    execute_inline: false,
    global_style_text: test.global_style_text,
    scene_text: test.scene_text,
    outfit_text: test.outfit_text,
    prompt_override: test.prompt_override,
  };
  Object.keys(payload).forEach((key) => payload[key] === undefined && delete payload[key]);
  return requestJson('/api/v1/admin/generation_probe', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

async function pollOrder(orderId) {
  const deadline = Date.now() + pollSeconds * 1000;
  let latest = null;
  while (Date.now() <= deadline) {
    latest = await requestJson(`/api/v1/admin/orders/${orderId}`);
    const status = String(latest.status || '').toUpperCase();
    if (status === 'COMPLETED' || latest.error_message) return latest;
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  throw new Error(`Timed out waiting for order ${orderId}; latest status=${latest?.status || 'unknown'}`);
}

async function downloadImages(testName, urls) {
  const testOutDir = join(outDir, testName);
  await mkdir(testOutDir, { recursive: true });
  const files = [];
  let index = 1;
  for (const url of urls) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Download failed ${response.status}: ${url}`);
    const buffer = Buffer.from(await response.arrayBuffer());
    const ext = basename(new URL(url).pathname).split('.').pop() || 'jpg';
    const file = join(testOutDir, `result_${String(index).padStart(2, '0')}.${ext}`);
    await writeFile(file, buffer);
    files.push(file);
    index += 1;
  }
  return files;
}

const sources = JSON.parse(await readFile(sourcesPath, 'utf8'));
const bride = sources['source_bride.jpg'];
const brideCastle = sources['source_bride_castle.jpg'] || bride;
const groom = sources['source_groom_clean.jpg'] || sources['source_groom_oldmoney.jpg'];

if (!bride || !groom) {
  throw new Error(`Missing required source URLs in ${sourcesPath}`);
}

const tests = [
  {
    name: 'single_template',
    label: 'Single person template',
    image_url: bride,
    template_id: 'solo_royal_castle',
  },
  {
    name: 'single_text',
    label: 'Single person text description',
    image_url: bride,
    template_id: 'solo_royal_castle',
    global_style_text:
      'premium bridal studio portrait, realistic identity preservation, refined editorial retouching, natural skin texture, sharp eyes',
    scene_text:
      'bright ivory indoor studio, controlled softbox lighting, gentle fill light, elegant floral background, no harsh backlight',
    outfit_text:
      'structured ivory satin wedding gown, fine veil, tasteful bouquet, luxury bridal styling',
  },
  {
    name: 'couple_local_template',
    label: 'Couple same-device template',
    image_url: bride,
    second_image_url: groom,
    template_id: 'royal_castle',
  },
  {
    name: 'couple_remote_text',
    label: 'Couple remote-session text description',
    image_url: brideCastle,
    second_image_url: groom,
    template_id: 'royal_castle',
    remote_join: true,
    global_style_text:
      'high-end wedding magazine portrait, both identities preserved, balanced couple composition, realistic faces and hands',
    scene_text:
      'warm European indoor bridal studio, polished architectural backdrop, controlled soft window light, no outdoor strong backlight',
    outfit_text:
      'ivory couture wedding dress and tailored cream suit, elegant refined styling, realistic fabric texture',
  },
];

const report = {
  baseUrl,
  sources,
  startedAt: new Date().toISOString(),
  tests: [],
};

console.log(JSON.stringify({ step: 'auth_check', baseUrl }, null, 2));
const adminMe = await requestJson('/api/v1/admin/me');
report.admin = {
  actor: adminMe.actor,
  remote_join_enabled: adminMe.remote_join_enabled,
  remote_join_session_store: adminMe.remote_join_session_store,
  generation_execution_mode: adminMe.generation_execution_mode,
};

for (const test of tests) {
  console.log(JSON.stringify({ step: 'start', test: test.name, label: test.label }, null, 2));
  const probe = await startProbe(test);
  const orderId = probe.order_id;
  if (!probe.started || !orderId) {
    report.tests.push({ ...test, probe, ok: false, error: probe.error_message || 'probe_not_started' });
    continue;
  }

  const order = await pollOrder(orderId);
  const imageUrls = [
    ...collectImageUrls(order.preview_image_urls),
    ...collectImageUrls(order.final_image_urls),
  ];
  const uniqueUrls = [...new Set(imageUrls)];
  const downloadedFiles = uniqueUrls.length ? await downloadImages(test.name, uniqueUrls) : [];
  const ok = String(order.status || '').toUpperCase() === 'COMPLETED' && !order.error_message && uniqueUrls.length > 0;
  const item = {
    name: test.name,
    label: test.label,
    order_id: orderId,
    status: order.status,
    ok,
    error_message: order.error_message || null,
    source_urls: [test.image_url, test.second_image_url].filter(Boolean),
    generated_urls: uniqueUrls,
    downloaded_files: downloadedFiles,
  };
  report.tests.push(item);
  console.log(JSON.stringify({ step: 'done', test: test.name, order_id: orderId, ok, image_count: uniqueUrls.length }, null, 2));
}

report.finishedAt = new Date().toISOString();
await mkdir(outDir, { recursive: true });
const reportPath = join(outDir, 'report.json');
await writeFile(reportPath, JSON.stringify(report, null, 2));
console.log(JSON.stringify({ step: 'report', reportPath, ok: report.tests.every((test) => test.ok) }, null, 2));
