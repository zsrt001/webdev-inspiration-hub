#!/usr/bin/env node
/**
 * Validate exactly six linked human-reviewed quality cases without image leakage.
 */

import { readFile } from "node:fs/promises";

import {
  parseArgs,
  readPrivateInput,
  rejectSensitiveEvidence,
  requireCoordinate,
  requireExactKeys,
  requireReleaseBinding,
  safeError,
  sha256,
  signReport,
  verifyCollectorProof,
  writeCreateOnce,
} from "./_acceptance_common.mjs";

function numericScore(value, minimum, maximum, label) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label} is outside the rubric`);
  }
  return value;
}

export function validateQualityInput(payload, casesContract, rubric) {
  requireExactKeys(
    payload,
    [
      "schema",
      "source_sha",
      "runtime_bundle_id",
      "deployment_id",
      "manifest_sha256",
      "user_subject_hmac_sha256",
      "cases",
      "collector",
    ],
    "quality input",
  );
  if (
    payload.schema !== "vowpic.quality-acceptance-input.v1"
    || casesContract.schema !== "vowpic.quality-cases.v1"
    || rubric.schema !== "vowpic.quality-rubric.v1"
  ) {
    throw new Error("quality contract schema is invalid");
  }
  requireReleaseBinding(payload);
  verifyCollectorProof(payload, "quality");
  const expectedIds = casesContract.cases.map((item) => item.id);
  if (
    expectedIds.length !== 6
    || new Set(expectedIds).size !== 6
    || !expectedIds.includes("golden_anniversary")
    || !expectedIds.includes("partner_invite_remote_couple")
  ) {
    throw new Error("quality contract must contain the exact six mandatory cases");
  }
  if (!Array.isArray(payload.cases) || payload.cases.length !== 6) {
    throw new Error("quality input must contain exactly six cases");
  }
  const byId = new Map(payload.cases.map((item) => [item.id, item]));
  if (byId.size !== 6 || expectedIds.some((id) => !byId.has(id))) {
    throw new Error("quality input case set is incomplete or unexpected");
  }
  const dimensions = rubric.dimensions;
  const results = [];
  for (const id of expectedIds) {
    const item = byId.get(id);
    requireExactKeys(
      item,
      [
        "id",
        "order_id",
        "job_id",
        "status",
        "initial_candidate_count",
        "repair_candidate_count",
        "selected_candidate_id",
        "review_asset_id",
        "reviewer_ref",
        "scores",
        "hard_defects",
        "passed",
      ],
      `quality case ${id}`,
    );
    requireCoordinate(item.order_id, `${id} order ID`);
    requireCoordinate(item.job_id, `${id} job ID`);
    requireCoordinate(item.selected_candidate_id, `${id} candidate ID`);
    requireCoordinate(item.review_asset_id, `${id} review asset ID`);
    requireCoordinate(item.reviewer_ref, `${id} reviewer reference`);
    if (
      item.status !== "READY"
      || item.initial_candidate_count !== 1
      || !Number.isInteger(item.repair_candidate_count)
      || item.repair_candidate_count < 0
      || item.repair_candidate_count > 2
      || item.passed !== true
      || !Array.isArray(item.hard_defects)
      || item.hard_defects.length !== 0
    ) {
      throw new Error(`quality case ${id} did not reach reviewed READY`);
    }
    requireExactKeys(item.scores, dimensions, `${id} scores`);
    const scores = dimensions.map((dimension) => (
      numericScore(
        item.scores[dimension],
        rubric.score_minimum,
        rubric.score_maximum,
        `${id}.${dimension}`,
      )
    ));
    const average = scores.reduce((total, score) => total + score, 0) / scores.length;
    if (
      average < rubric.average_minimum
      || scores.some((score) => score < rubric.dimension_minimum)
    ) {
      throw new Error(`quality case ${id} is below the fixed rubric`);
    }
    results.push({
      id,
      order_id: item.order_id,
      job_id: item.job_id,
      selected_candidate_id: item.selected_candidate_id,
      review_asset_id: item.review_asset_id,
      reviewer_ref: item.reviewer_ref,
      candidate_count: item.initial_candidate_count + item.repair_candidate_count,
      scores: item.scores,
      average,
      passed: true,
    });
  }
  rejectSensitiveEvidence(results);
  return results;
}

async function main() {
  const args = parseArgs(process.argv.slice(2), {
    required: ["base_url", "cases", "rubric", "output"],
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
    throw new Error("quality base URL must be an exact HTTPS origin");
  }
  const inputPath = String(
    args.input || process.env.QUALITY_ACCEPTANCE_INPUT_FILE || "",
  ).trim();
  if (!inputPath) {
    throw new Error("NOT_RUN: real quality reviews are required");
  }
  const [{ payload, inputSha256 }, casesRaw, rubricRaw] = await Promise.all([
    readPrivateInput(inputPath),
    readFile(args.cases),
    readFile(args.rubric),
  ]);
  const casesContract = JSON.parse(casesRaw.toString("utf8"));
  const rubric = JSON.parse(rubricRaw.toString("utf8"));
  const results = validateQualityInput(payload, casesContract, rubric);
  const report = signReport({
    schema: "vowpic.quality-acceptance.v1",
    passed: true,
    source_sha: payload.source_sha,
    runtime_bundle_id: payload.runtime_bundle_id,
    deployment_id: payload.deployment_id,
    manifest_sha256: payload.manifest_sha256,
    input_sha256: inputSha256,
    cases_contract_sha256: sha256(casesRaw),
    rubric_sha256: sha256(rubricRaw),
    cases: results,
    produced_at: new Date().toISOString(),
  });
  await writeCreateOnce(args.output, report);
}

main().catch((error) => {
  process.stderr.write(`ERROR: ${safeError(error)}\n`);
  process.exitCode = 1;
});
