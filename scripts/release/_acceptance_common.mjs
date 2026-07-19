#!/usr/bin/env node
/**
 * Shared fail-closed helpers for linked COMMERCIAL_7A acceptance evidence.
 */

import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { lstat, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const SHA40 = /^[0-9a-f]{40}$/;
const SHA64 = /^[0-9a-f]{64}$/;
const RUNTIME_ID = /^rtb_[0-9a-f]{64}$/;
const COORDINATE = /^[A-Za-z0-9_.:-]{1,180}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SENSITIVE_KEY = /(email|token|cookie|password|secret|authorization|raw_url|object_key|permanent_url)/i;
const JWT = /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/;
const EMAIL = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i;

export function canonical(value) {
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function sha256(value) {
  const bytes = Buffer.isBuffer(value) ? value : Buffer.from(String(value), "utf8");
  return createHash("sha256").update(bytes).digest("hex");
}

export function parseArgs(argv, { required = [], flags = [] } = {}) {
  const parsed = {};
  const flagNames = new Set(flags);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key?.startsWith("--")) {
      throw new Error("arguments must use --name value form");
    }
    const name = key.slice(2).replaceAll("-", "_");
    if (flagNames.has(name)) {
      parsed[name] = true;
      continue;
    }
    const value = argv[index + 1];
    if (value === undefined || value.startsWith("--")) {
      throw new Error(`missing value for ${key}`);
    }
    parsed[name] = value;
    index += 1;
  }
  for (const name of required) {
    if (!parsed[name]) {
      throw new Error(`missing --${name.replaceAll("_", "-")}`);
    }
  }
  return parsed;
}

function exactPathWithin(rootValue, candidateValue, label) {
  const root = path.resolve(rootValue);
  const candidate = path.resolve(candidateValue);
  if (candidate === root || !candidate.startsWith(`${root}${path.sep}`)) {
    throw new Error(`${label} must be inside RUNNER_TEMP`);
  }
  return candidate;
}

export async function readPrivateInput(inputPath) {
  const runnerTemp = String(process.env.RUNNER_TEMP || "").trim();
  if (!runnerTemp) {
    throw new Error("RUNNER_TEMP is required for acceptance input");
  }
  const resolved = exactPathWithin(runnerTemp, inputPath, "acceptance input");
  const stat = await lstat(resolved);
  if (!stat.isFile() || stat.size < 2 || stat.size > 1_000_000) {
    throw new Error("acceptance input must be one bounded regular file");
  }
  if (process.platform !== "win32" && (stat.mode & 0o077) !== 0) {
    throw new Error("acceptance input must be mode 0600");
  }
  const raw = await readFile(resolved);
  let payload;
  try {
    payload = JSON.parse(raw.toString("utf8"));
  } catch {
    throw new Error("acceptance input is not valid JSON");
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("acceptance input must be an object");
  }
  return { payload, inputSha256: sha256(raw) };
}

export function requireExactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (canonical(actual) !== canonical(expected)) {
    throw new Error(`${label} fields are not exact`);
  }
}

export function requireAssertions(assertions, required, label = "assertions") {
  requireExactKeys(assertions, required, label);
  const failed = required.filter((name) => assertions[name] !== true);
  if (failed.length) {
    throw new Error(`${label} failed: ${failed.join(",")}`);
  }
}

export function requireCoordinate(value, label, { uuid = false } = {}) {
  const normalized = String(value || "");
  const pattern = uuid ? UUID : COORDINATE;
  if (!pattern.test(normalized)) {
    throw new Error(`${label} is invalid`);
  }
  return normalized;
}

export function requireReleaseBinding(payload) {
  if (!SHA40.test(String(payload.source_sha || ""))) {
    throw new Error("acceptance source SHA is invalid");
  }
  if (!RUNTIME_ID.test(String(payload.runtime_bundle_id || ""))) {
    throw new Error("acceptance runtime bundle ID is invalid");
  }
  requireCoordinate(payload.deployment_id, "acceptance deployment ID");
  if (!SHA64.test(String(payload.manifest_sha256 || ""))) {
    throw new Error("acceptance manifest SHA-256 is invalid");
  }
  if (!SHA64.test(String(payload.user_subject_hmac_sha256 || ""))) {
    throw new Error("ordinary-user subject HMAC is invalid");
  }
}

export function rejectSensitiveEvidence(value, pathParts = []) {
  if (Array.isArray(value)) {
    value.forEach((item, index) => rejectSensitiveEvidence(item, [...pathParts, String(index)]));
    return;
  }
  if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      const isBooleanDenialAssertion = child === true && key.endsWith("_denied");
      if (SENSITIVE_KEY.test(key) && !isBooleanDenialAssertion) {
        throw new Error(`sensitive evidence key is forbidden: ${[...pathParts, key].join(".")}`);
      }
      rejectSensitiveEvidence(child, [...pathParts, key]);
    }
    return;
  }
  if (typeof value === "string" && (JWT.test(value) || EMAIL.test(value))) {
    throw new Error(`sensitive evidence value is forbidden: ${pathParts.join(".")}`);
  }
}

export function signReport(report) {
  const key = Buffer.from(String(process.env.ACCEPTANCE_EVIDENCE_SIGNING_KEY || ""), "utf8");
  if (key.length < 32) {
    throw new Error("ACCEPTANCE_EVIDENCE_SIGNING_KEY must contain at least 32 bytes");
  }
  const unsigned = { ...report };
  delete unsigned.signature;
  const signature = createHmac("sha256", key).update(canonical(unsigned)).digest("hex");
  return { ...unsigned, signature: `hmac-sha256:${signature}` };
}

export function verifySignedReport(report, expected = {}) {
  const key = Buffer.from(String(process.env.ACCEPTANCE_EVIDENCE_SIGNING_KEY || ""), "utf8");
  if (key.length < 32) {
    throw new Error("ACCEPTANCE_EVIDENCE_SIGNING_KEY must contain at least 32 bytes");
  }
  const signature = String(report?.signature || "");
  if (!/^hmac-sha256:[0-9a-f]{64}$/.test(signature)) {
    throw new Error("acceptance report signature is invalid");
  }
  const unsigned = { ...report };
  delete unsigned.signature;
  const wanted = createHmac("sha256", key).update(canonical(unsigned)).digest("hex");
  const actualBytes = Buffer.from(signature.slice("hmac-sha256:".length), "hex");
  const wantedBytes = Buffer.from(wanted, "hex");
  if (actualBytes.length !== wantedBytes.length || !timingSafeEqual(actualBytes, wantedBytes)) {
    throw new Error("acceptance report signature mismatch");
  }
  for (const [field, value] of Object.entries(expected)) {
    if (report[field] !== value) {
      throw new Error(`acceptance report ${field} mismatch`);
    }
  }
  rejectSensitiveEvidence(unsigned);
  return unsigned;
}

export function verifyCollectorProof(payload, expectedPhase) {
  const proof = payload?.collector;
  requireExactKeys(
    proof,
    [
      "schema",
      "phase",
      "source_sha",
      "runtime_bundle_id",
      "deployment_id",
      "manifest_sha256",
      "browser_report_sha256",
      "database_facts_sha256",
      "collected_at",
      "input_sha256",
      "signature",
    ],
    "acceptance collector proof",
  );
  if (
    proof.schema !== "vowpic.acceptance-collector-proof.v1"
    || proof.phase !== expectedPhase
    || proof.source_sha !== payload.source_sha
    || proof.runtime_bundle_id !== payload.runtime_bundle_id
    || proof.deployment_id !== payload.deployment_id
    || proof.manifest_sha256 !== payload.manifest_sha256
  ) {
    throw new Error("acceptance collector proof binding mismatch");
  }
  for (const [field, value] of Object.entries({
    browser_report_sha256: proof.browser_report_sha256,
    database_facts_sha256: proof.database_facts_sha256,
    input_sha256: proof.input_sha256,
  })) {
    if (!SHA64.test(String(value || ""))) {
      throw new Error(`acceptance collector ${field} is invalid`);
    }
  }
  const collectedAt = new Date(String(proof.collected_at || ""));
  if (Number.isNaN(collectedAt.valueOf()) || !String(proof.collected_at).endsWith("Z")) {
    throw new Error("acceptance collector timestamp is invalid");
  }
  const unsignedPayload = { ...payload };
  delete unsignedPayload.collector;
  if (sha256(canonical(unsignedPayload)) !== proof.input_sha256) {
    throw new Error("acceptance collector input hash mismatch");
  }
  const key = Buffer.from(String(process.env.ACCEPTANCE_EVIDENCE_SIGNING_KEY || ""), "utf8");
  if (key.length < 32) {
    throw new Error("ACCEPTANCE_EVIDENCE_SIGNING_KEY must contain at least 32 bytes");
  }
  const unsignedProof = { ...proof };
  delete unsignedProof.signature;
  const signature = String(proof.signature || "");
  if (!/^hmac-sha256:[0-9a-f]{64}$/.test(signature)) {
    throw new Error("acceptance collector signature is invalid");
  }
  const wanted = createHmac("sha256", key).update(canonical(unsignedProof)).digest("hex");
  const actualBytes = Buffer.from(signature.slice("hmac-sha256:".length), "hex");
  const wantedBytes = Buffer.from(wanted, "hex");
  if (actualBytes.length !== wantedBytes.length || !timingSafeEqual(actualBytes, wantedBytes)) {
    throw new Error("acceptance collector signature mismatch");
  }
  rejectSensitiveEvidence(proof);
  return proof;
}

export async function readSignedReport(reportPath, expected = {}) {
  const payload = JSON.parse(await readFile(path.resolve(reportPath), "utf8"));
  return verifySignedReport(payload, expected);
}

export async function writeCreateOnce(outputPath, report) {
  const output = path.resolve(outputPath);
  const runnerTemp = String(process.env.RUNNER_TEMP || "").trim();
  if (runnerTemp) {
    exactPathWithin(runnerTemp, output, "acceptance output");
  }
  rejectSensitiveEvidence(report);
  await mkdir(path.dirname(output), { recursive: true });
  await writeFile(output, `${canonical(report)}\n`, {
    encoding: "utf8",
    flag: "wx",
    mode: 0o600,
  });
}

export function safeError(error) {
  const message = error instanceof Error ? error.message : String(error);
  return message.replaceAll(/https?:\/\/\S+/g, "[URL_REDACTED]").replace(EMAIL, "[EMAIL_REDACTED]");
}
