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
const publicBearerToken = argValue('--public-bearer-token', process.env.PUBLIC_USER_BEARER_TOKEN);
const sourcesPath = argValue(
  '--sources',
  join(root, 'artifacts', 'production-generation-test', 'uploaded-sources.json'),
);
const outDir = argValue('--out-dir', join(root, 'artifacts', 'production-generation-test', 'generated'));
const pollSeconds = Number(argValue('--poll-seconds', process.env.POLL_SECONDS || '420'));
const pollIntervalMs = Number(argValue('--poll-interval-ms', process.env.POLL_INTERVAL_MS || '5000'));
const executeInlineRaw = argValue('--execute-inline', process.env.PROBE_EXECUTE_INLINE);
const executeInline = executeInlineRaw === undefined
  ? undefined
  : ['1', 'true', 'yes', 'on'].includes(String(executeInlineRaw).trim().toLowerCase());

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

async function requestPublicJson(path, options = {}) {
  if (!publicBearerToken) return null;
  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers: {
      accept: 'application/json',
      authorization: `Bearer ${publicBearerToken}`,
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
    return {
      ok: false,
      status: response.status,
      error: body?.detail || body?.message || body?.raw || response.statusText,
    };
  }
  return { ok: true, status: response.status, body };
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
    execute_inline: executeInline,
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
    name: 'single_outdoor_text',
    label: 'Single person outdoor lighting text',
    image_url: bride,
    template_id: 'solo_royal_castle',
    global_style_text:
      'commercial outdoor bridal portrait, identity-preserving image edit, professional on-location studio lighting, natural skin texture',
    scene_text:
      'outdoor garden wedding portrait at golden hour, sun used only as rim light, frontal softbox-style fill on the face, sky and white dress not overexposed',
    outfit_text:
      'full ivory wedding gown, clean veil, elegant bouquet, dress texture and lace detail preserved',
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

function roundSummary(order) {
  const rounds = Array.isArray(order.generation_rounds) ? order.generation_rounds : [];
  return rounds.map((round) => ({
    round: round.round ?? null,
    stage: round.stage ?? null,
    repair_mode: round.repair_mode ?? null,
    qa_passed: round.qa_passed ?? null,
    qa_reasons: Array.isArray(round.qa_reasons) ? round.qa_reasons : [],
    candidate_count: round.candidate_count ?? 0,
    selected_candidate_index: round.selected_candidate_index ?? null,
    candidate_scores: Array.isArray(round.candidate_scores)
      ? round.candidate_scores.map((score) => ({
          index: score.index ?? null,
          score: score.score ?? null,
          qa_passed: score.qa_passed ?? score.passed ?? null,
          hard_gate_reasons: Array.isArray(score.hard_gate_reasons) ? score.hard_gate_reasons : [],
          reasons: Array.isArray(score.reasons) ? score.reasons : [],
        }))
      : [],
    billable: Boolean(round.billable),
    billing_reason: round.billing_reason ?? null,
    extra_credits_charged: Number(round.extra_credits_charged || 0),
    used_previous_result: Boolean(round.used_previous_result),
  }));
}

function qaSummary(order) {
  const qa = order.qa_summary && typeof order.qa_summary === 'object' ? order.qa_summary : {};
  return {
    qa_last_reasons: Array.isArray(qa.qa_last_reasons) ? qa.qa_last_reasons : [],
    qa_attempt_count: qa.qa_attempt_count ?? null,
    failure_code: qa.failure_code ?? order.failure_code ?? null,
    failure_provider: qa.failure_provider ?? null,
    credit_refund: qa.credit_refund ?? null,
  };
}

function billingSummary(order) {
  const rounds = roundSummary(order);
  const repairRounds = rounds.filter((round) => round.round !== 1 || round.repair_mode !== 'primary_generation');
  return {
    credits_cost: Number(order.credits_cost || 0),
    refunded_credits: Number(order.refunded_credits || 0),
    automatic_repair_extra_charges: repairRounds.reduce(
      (sum, round) => sum + Number(round.extra_credits_charged || 0),
      0,
    ),
    repair_round_count: repairRounds.length,
    all_repair_rounds_non_billable: repairRounds.every((round) => round.billable === false),
  };
}

function adminRoundEvidence(order) {
  const rounds = roundSummary(order);
  if (!Array.isArray(order.generation_rounds) || rounds.length === 0) return false;
  return rounds.every((round) => (
    Array.isArray(round.qa_reasons)
    && Array.isArray(round.candidate_scores)
    && typeof round.billable === 'boolean'
    && Number.isFinite(Number(round.extra_credits_charged))
  ));
}

function publicContractEvidence(publicOrderResult) {
  if (!publicBearerToken) {
    return {
      checked: false,
      ok: false,
      reason: 'missing_public_bearer_token',
    };
  }
  if (!publicOrderResult?.ok) {
    return {
      checked: true,
      ok: false,
      reason: 'public_order_fetch_failed',
      status: publicOrderResult?.status ?? null,
      error: publicOrderResult?.error ?? null,
    };
  }
  const payload = publicOrderResult.body || {};
  const params = payload.generation_params || {};
  const source = payload.source_image_urls || {};
  const forbiddenTopLevel = ['generation_rounds', 'qa_summary'];
  const forbiddenParams = ['debug', 'qa_last_issues', 'identity_reference_pack', 'image_edit_rounds', 'candidate_selection'];
  const forbiddenSource = ['identity_reference_pack'];
  const leaked = [
    ...forbiddenTopLevel.filter((key) => key in payload),
    ...forbiddenParams.filter((key) => key in params).map((key) => `generation_params.${key}`),
    ...forbiddenSource.filter((key) => key in source).map((key) => `source_image_urls.${key}`),
  ];
  return {
    checked: true,
    ok: leaked.length === 0,
    leaked,
    source_image_count: Array.isArray(source.images) ? source.images.length : 0,
    final_visible: Boolean(payload.final_image_urls),
  };
}

function acceptanceGates(order, generatedUrlCount, publicEvidence) {
  const billing = billingSummary(order);
  const statusOk = String(order.status || '').toUpperCase() === 'COMPLETED' && !order.error_message;
  return {
    completed: statusOk,
    has_generated_image: generatedUrlCount > 0,
    qa_clear: qaSummary(order).qa_last_reasons.length === 0,
    no_extra_repair_charge: billing.automatic_repair_extra_charges === 0 && billing.all_repair_rounds_non_billable,
    admin_round_evidence_visible: adminRoundEvidence(order),
    public_contract_checked: publicEvidence.ok,
  };
}

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
  const publicOrderResult = await requestPublicJson(`/api/v1/orders/${orderId}`);
  const public_contract = publicContractEvidence(publicOrderResult);
  const gates = acceptanceGates(order, uniqueUrls.length, public_contract);
  const ok = Object.values(gates).every(Boolean);
  const item = {
    name: test.name,
    label: test.label,
    order_id: orderId,
    status: order.status,
    ok,
    gates,
    error_message: order.error_message || null,
    source_urls: [test.image_url, test.second_image_url].filter(Boolean),
    generated_urls: uniqueUrls,
    downloaded_files: downloadedFiles,
    qa_summary: qaSummary(order),
    billing_summary: billingSummary(order),
    generation_rounds: roundSummary(order),
    public_contract,
  };
  report.tests.push(item);
  console.log(JSON.stringify({
    step: 'done',
    test: test.name,
    order_id: orderId,
    ok,
    gates,
    public_contract,
    image_count: uniqueUrls.length,
    rounds: item.generation_rounds.map((round) => ({
      round: round.round,
      repair_mode: round.repair_mode,
      qa_reasons: round.qa_reasons,
      extra_credits_charged: round.extra_credits_charged,
    })),
  }, null, 2));
}

report.finishedAt = new Date().toISOString();
await mkdir(outDir, { recursive: true });
const reportPath = join(outDir, 'report.json');
await writeFile(reportPath, JSON.stringify(report, null, 2));
console.log(JSON.stringify({ step: 'report', reportPath, ok: report.tests.every((test) => test.ok) }, null, 2));
