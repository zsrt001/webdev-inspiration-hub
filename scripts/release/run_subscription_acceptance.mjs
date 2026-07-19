#!/usr/bin/env node
/**
 * Validate and sign the ordinary-user subscription lifecycle acceptance chain.
 */

import {
  parseArgs,
  readPrivateInput,
  rejectSensitiveEvidence,
  requireAssertions,
  requireCoordinate,
  requireExactKeys,
  requireReleaseBinding,
  safeError,
  signReport,
  verifyCollectorProof,
  writeCreateOnce,
} from "./_acceptance_common.mjs";

const ASSERTIONS = [
  "ordinary_google_user",
  "starter_checkout",
  "signed_initial_paid_event",
  "one_initial_invoice",
  "one_initial_grant",
  "paid_order_snapshot_180_days",
  "period_end_cancel_confirmed",
  "cancel_remains_active_until_period_end",
  "test_mode_signed_renewal_paid_event",
  "test_mode_renewal_transaction_unique",
  "test_mode_renewal_invoice_unique",
  "test_mode_full_invoice_refund_verified",
  "test_mode_refund_reversal_and_debt",
  "test_mode_access_revoked_after_refund",
  "test_mode_duplicate_event_deduped",
  "test_mode_out_of_order_event_reconciled",
  "test_mode_past_due_recovery_verified",
  "test_mode_partial_refund_anomaly_quarantined",
  "test_mode_dispute_outcome_verified",
  "no_real_chargeback_manufactured",
  "no_admin_or_test_bypass",
];

const LINKS = [
  "user_id",
  "subscription_id",
  "checkout_id",
  "initial_transaction_id",
  "initial_invoice_id",
  "initial_grant_id",
  "initial_order_id",
  "cancel_event_id",
  "provider_refund_evidence_id",
  "provider_renewal_evidence_id",
  "provider_cancel_evidence_id",
  "access_snapshot_id",
];

export function validateSubscriptionInput(payload) {
  requireExactKeys(
    payload,
    [
      "schema",
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
      "collector",
    ],
    "subscription input",
  );
  if (payload.schema !== "vowpic.subscription-acceptance-input.v1") {
    throw new Error("subscription input schema is invalid");
  }
  requireReleaseBinding(payload);
  requireAssertions(payload.assertions, ASSERTIONS, "subscription assertions");
  requireExactKeys(payload.links, LINKS, "subscription links");
  for (const name of LINKS) {
    requireCoordinate(payload.links[name], `subscription link ${name}`);
  }
  if (new Set(Object.values(payload.links)).size !== LINKS.length) {
    throw new Error("subscription links must be distinct facts");
  }
  verifyCollectorProof(payload, "subscription");
  if (
    !Number.isSafeInteger(payload.cost_minor_units)
    || !Number.isSafeInteger(payload.cost_cap_minor_units)
    || payload.cost_minor_units < 0
    || payload.cost_cap_minor_units < 1
    || payload.cost_minor_units > payload.cost_cap_minor_units
  ) {
    throw new Error("subscription acceptance cost exceeds the approved cap");
  }
  rejectSensitiveEvidence(payload);
  return payload;
}

async function main() {
  const args = parseArgs(process.argv.slice(2), {
    required: ["base_url", "output"],
  });
  const base = new URL(args.base_url);
  if (
    base.protocol !== "https:"
    || base.username
    || base.password
    || base.pathname !== "/"
    || base.search
    || base.hash
  ) {
    throw new Error("subscription base URL must be an exact HTTPS origin");
  }
  const inputPath = String(
    args.input || process.env.SUBSCRIPTION_ACCEPTANCE_INPUT_FILE || "",
  ).trim();
  if (!inputPath) {
    throw new Error("NOT_RUN: real subscription acceptance input is required");
  }
  const { payload, inputSha256 } = await readPrivateInput(inputPath);
  const valid = validateSubscriptionInput(payload);
  const report = signReport({
    schema: "vowpic.subscription-acceptance.v1",
    passed: true,
    source_sha: valid.source_sha,
    runtime_bundle_id: valid.runtime_bundle_id,
    deployment_id: valid.deployment_id,
    manifest_sha256: valid.manifest_sha256,
    user_subject_hmac_sha256: valid.user_subject_hmac_sha256,
    currency: valid.currency,
    cost_minor_units: valid.cost_minor_units,
    cost_cap_minor_units: valid.cost_cap_minor_units,
    assertions: valid.assertions,
    links: valid.links,
    input_sha256: inputSha256,
    produced_at: new Date().toISOString(),
  });
  await writeCreateOnce(args.output, report);
}

main().catch((error) => {
  process.stderr.write(`ERROR: ${safeError(error)}\n`);
  process.exitCode = 1;
});
