#!/usr/bin/env node
/**
 * Validate and sign one phase of the linked ordinary-user commercial chain.
 */

import {
  canonical,
  parseArgs,
  readPrivateInput,
  readSignedReport,
  rejectSensitiveEvidence,
  requireAssertions,
  requireCoordinate,
  requireExactKeys,
  requireReleaseBinding,
  safeError,
  signReport,
  verifyCollectorProof,
  verifySignedReport,
  writeCreateOnce,
} from "./_acceptance_common.mjs";
import { createHmac, timingSafeEqual } from "node:crypto";
import { readFile } from "node:fs/promises";

const PHASE_ASSERTIONS = {
  "first-login-and-auth-security": [
    "ordinary_google_user",
    "first_login",
    "refresh_rotated",
    "logout_revoked",
    "post_logout_denied",
    "second_login_same_account",
    "legacy_jwt_denied",
    "legacy_openid_header_denied",
    "legacy_visitor_header_denied",
    "forwarded_identity_spoof_denied",
    "browser_admin_token_denied",
    "no_admin_or_test_bypass",
  ],
  "commercial-before-delete": [
    "ordinary_google_user",
    "first_login",
    "welcome_grant_once",
    "refresh_rotated",
    "logout_revoked",
    "post_logout_denied",
    "second_login_same_account",
    "legacy_jwt_denied",
    "legacy_openid_header_denied",
    "legacy_visitor_header_denied",
    "forwarded_identity_spoof_denied",
    "browser_admin_token_denied",
    "private_upload",
    "trial_job_ready",
    "trial_qa_passed",
    "watermarked_preview",
    "signed_checkout_webhook",
    "exact_order_entitlement",
    "private_final_download",
    "paid_grant_consumed",
    "full_refund_verified",
    "refund_reversal_and_debt",
    "second_purchase_verified",
    "debt_offset_exact",
    "residual_spendable_exact",
    "account_export_complete",
    "no_admin_or_test_bypass",
  ],
  "queue-provider-unknown-state": [
    "dispatch_paused",
    "fault_intent_coordinate_prepared",
    "one_submit_limit",
    "bounded_cost",
    "correlation_bound",
    "no_admin_or_test_bypass",
  ],
  "complete-provider-unknown-state": [
    "provider_accepted",
    "worker_lost_submit_response",
    "same_provider_task_recovered",
    "single_submit",
    "single_capture",
    "fault_rule_armed_once",
    "recovery_completed_before_disarm",
    "no_admin_or_test_bypass",
  ],
  "commercial-finalize-delete": [
    "prior_commercial_chain_passed",
    "provider_unknown_chain_passed",
    "account_closed",
    "sessions_revoked",
    "private_objects_deleted",
    "private_store_read_after_delete_not_found",
    "no_acceptance_binding_residue",
    "no_admin_or_test_bypass",
  ],
};

const PHASE_LINKS = {
  "first-login-and-auth-security": [
    "user_id",
    "first_session_id",
    "rotated_session_id",
    "second_session_id",
  ],
  "commercial-before-delete": [
    "user_id",
    "upload_asset_id",
    "trial_order_id",
    "trial_reservation_id",
    "trial_job_id",
    "trial_attempt_id",
    "trial_candidate_asset_id",
    "trial_preview_asset_id",
    "trial_qa_verdict_id",
    "purchase_id",
    "checkout_id",
    "payment_event_id",
    "credit_grant_id",
    "paid_order_id",
    "paid_reservation_id",
    "paid_job_id",
    "paid_attempt_id",
    "paid_final_asset_id",
    "entitlement_id",
    "refund_id",
    "reversal_id",
    "debt_fact_id",
    "second_purchase_id",
    "second_grant_id",
    "debt_offset_fact_id",
    "account_export_id",
  ],
  "queue-provider-unknown-state": [
    "user_id",
    "order_id",
    "reservation_id",
    "job_id",
    "attempt_id",
    "fault_intent_id",
    "client_correlation_id",
  ],
  "complete-provider-unknown-state": [
    "user_id",
    "order_id",
    "job_id",
    "attempt_id",
    "fault_intent_id",
    "provider_task_id",
    "provider_capture_id",
  ],
  "commercial-finalize-delete": [
    "user_id",
    "account_close_id",
    "deletion_batch_id",
  ],
};

function validateLinks(links, required) {
  requireExactKeys(links, required, "commercial links");
  for (const name of required) {
    requireCoordinate(links[name], `commercial link ${name}`);
  }
  if (new Set(Object.values(links)).size !== required.length) {
    throw new Error("commercial chain links must be distinct facts");
  }
}

async function readJson(path, label) {
  const payload = JSON.parse(await readFile(path, "utf8"));
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return payload;
}

function verifyWorkerReport(report, { action, runtimeBundleId, mode, faultIntentId, requireAbsent = false }) {
  const key = Buffer.from(String(process.env.WORKER_HOST_EVIDENCE_SIGNING_KEY || ""), "utf8");
  const signature = String(report.signature || "");
  if (key.length < 32 || !/^hmac-sha256:[0-9a-f]{64}$/.test(signature)) {
    throw new Error("Worker host report signature is invalid");
  }
  const unsigned = { ...report };
  delete unsigned.signature;
  const wanted = createHmac("sha256", key).update(canonical(unsigned)).digest();
  const actual = Buffer.from(signature.slice("hmac-sha256:".length), "hex");
  if (actual.length !== wanted.length || !timingSafeEqual(actual, wanted)) {
    throw new Error("Worker host report signature mismatch");
  }
  const coordinates = report.coordinates;
  if (
    report.schema !== "vowpic.worker-host-adapter-report.v1"
    || report.passed !== true
    || report.action !== action
    || !coordinates
    || coordinates.runtime_bundle_id !== runtimeBundleId
    || coordinates.worker_deployment_id !== process.env.RELEASE_WORKER_DEPLOYMENT_ID
    || (mode && coordinates.dispatch_mode !== mode)
    || (faultIntentId && coordinates.fault_intent_id !== faultIntentId)
  ) {
    throw new Error("Worker host report release binding mismatch");
  }
  if (requireAbsent && (coordinates.rule_present !== false || coordinates.runtime_rule_count !== 0)) {
    throw new Error("Worker response-drop absence was not proven");
  }
}

export function validateCommercialInput(payload, phase) {
  const isBrowserAuthPhase = phase === "first-login-and-auth-security";
  requireExactKeys(
    payload,
    [
      "schema",
      "phase",
      "source_sha",
      "runtime_bundle_id",
      "deployment_id",
      "manifest_sha256",
      "user_subject_hmac_sha256",
      "currency",
      "cost_minor_units",
      "cost_cap_minor_units",
      "assertions",
      "links",
      ...(isBrowserAuthPhase ? [] : ["collector"]),
    ],
    "commercial input",
  );
  if (payload.schema !== "vowpic.commercial-acceptance-input.v1" || payload.phase !== phase) {
    throw new Error("commercial input identity is invalid");
  }
  requireReleaseBinding(payload);
  if (!/^[A-Z]{3}$/.test(String(payload.currency || ""))) {
    throw new Error("commercial acceptance currency is invalid");
  }
  if (
    !Number.isSafeInteger(payload.cost_minor_units)
    || !Number.isSafeInteger(payload.cost_cap_minor_units)
    || payload.cost_minor_units < 0
    || payload.cost_cap_minor_units < 1
    || payload.cost_minor_units > payload.cost_cap_minor_units
  ) {
    throw new Error("commercial acceptance cost exceeds the approved cap");
  }
  requireAssertions(payload.assertions, PHASE_ASSERTIONS[phase]);
  validateLinks(payload.links, PHASE_LINKS[phase]);
  if (!isBrowserAuthPhase) {
    verifyCollectorProof(payload, phase);
  }
  rejectSensitiveEvidence(payload);
  return {
    schema: "vowpic.linked-commercial-acceptance.v1",
    phase,
    passed: true,
    source_sha: payload.source_sha,
    runtime_bundle_id: payload.runtime_bundle_id,
    deployment_id: payload.deployment_id,
    manifest_sha256: payload.manifest_sha256,
    user_subject_hmac_sha256: payload.user_subject_hmac_sha256,
    currency: payload.currency,
    cost_minor_units: payload.cost_minor_units,
    cost_cap_minor_units: payload.cost_cap_minor_units,
    assertions: payload.assertions,
    links: payload.links,
    produced_at: new Date().toISOString(),
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2), {
    required: ["phase", "base_url", "output"],
  });
  if (!Object.hasOwn(PHASE_ASSERTIONS, args.phase)) {
    throw new Error("commercial acceptance phase is not allowlisted");
  }
  const base = new URL(args.base_url);
  if (
    base.protocol !== "https:"
    || base.username
    || base.password
    || base.pathname !== "/"
    || base.search
    || base.hash
  ) {
    throw new Error("commercial base URL must be an exact HTTPS origin");
  }
  const inputPath = String(
    args.input || process.env.COMMERCIAL_ACCEPTANCE_INPUT_FILE || "",
  ).trim();
  if (!inputPath) {
    throw new Error("NOT_RUN: real commercial acceptance input is required");
  }
  const { payload, inputSha256 } = await readPrivateInput(inputPath);
  const unsigned = validateCommercialInput(payload, args.phase);
  if (args.commercial_report) {
    await readSignedReport(args.commercial_report, {
      schema: "vowpic.linked-commercial-acceptance.v1",
      phase: "commercial-before-delete",
      passed: true,
      source_sha: unsigned.source_sha,
      runtime_bundle_id: unsigned.runtime_bundle_id,
    });
  }
  if (args.phase === "queue-provider-unknown-state") {
    if (!args.require_worker_dispatch_report) {
      throw new Error("queue Provider canary requires a disabled Worker report");
    }
    verifyWorkerReport(
      await readJson(args.require_worker_dispatch_report, "Worker dispatch report"),
      { action: "set-dispatch", runtimeBundleId: unsigned.runtime_bundle_id, mode: "disabled" },
    );
  }
  if (args.phase === "complete-provider-unknown-state") {
    if (!args.prepare_report || !args.fault_intent_report || !args.fault_report) {
      throw new Error("complete Provider canary requires prepare, intent, and arm reports");
    }
    const prepare = verifySignedReport(await readJson(args.prepare_report, "Provider prepare report"), {
      schema: "vowpic.provider-unknown-canary.v1",
      passed: true,
      source_sha: unsigned.source_sha,
      runtime_bundle_id: unsigned.runtime_bundle_id,
    });
    const intent = await readJson(args.fault_intent_report, "Provider fault intent report");
    if (
      intent.schema !== "vowpic.acceptance-fault-intent.v1"
      || intent.passed !== true
      || intent.state !== "PREPARED"
      || intent.fault_intent_id !== prepare.fault_intent_id
      || intent.max_provider_submits !== 1
    ) {
      throw new Error("Provider fault intent binding mismatch");
    }
    verifyWorkerReport(await readJson(args.fault_report, "Provider fault arm report"), {
      action: "arm-response-drop-once",
      runtimeBundleId: unsigned.runtime_bundle_id,
      faultIntentId: intent.fault_intent_id,
    });
  }
  if (args.provider_unknown_state_report) {
    await readSignedReport(args.provider_unknown_state_report, {
      schema: "vowpic.linked-commercial-acceptance.v1",
      phase: "complete-provider-unknown-state",
      passed: true,
      source_sha: unsigned.source_sha,
      runtime_bundle_id: unsigned.runtime_bundle_id,
    });
  }
  if (args.phase === "commercial-finalize-delete") {
    if (!args.provider_unknown_disarm_report) {
      throw new Error("commercial finalization requires Provider fault disarm proof");
    }
    const providerState = await readSignedReport(args.provider_unknown_state_report, {
      schema: "vowpic.linked-commercial-acceptance.v1",
      phase: "complete-provider-unknown-state",
      passed: true,
      source_sha: unsigned.source_sha,
      runtime_bundle_id: unsigned.runtime_bundle_id,
    });
    verifyWorkerReport(await readJson(args.provider_unknown_disarm_report, "Provider disarm report"), {
      action: "disarm-response-drop",
      runtimeBundleId: unsigned.runtime_bundle_id,
      faultIntentId: providerState.links.fault_intent_id,
      requireAbsent: true,
    });
  }
  const report = signReport({ ...unsigned, input_sha256: inputSha256 });
  await writeCreateOnce(args.output, report);
}

main().catch((error) => {
  process.stderr.write(`ERROR: ${safeError(error)}\n`);
  process.exitCode = 1;
});
