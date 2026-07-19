#!/usr/bin/env node
/**
 * Probe raw legacy URLs from an ephemeral manifest and emit HMAC-only results.
 */

import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { chmod, lstat, mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import path from "node:path";


function canonical(value) {
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}


function parseArgs(argv) {
  const parsed = { probeReports: [] };
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error("arguments must be --name value pairs");
    }
    parsed[key.slice(2).replaceAll("-", "_")] = value;
  }
  for (const required of ["probe_manifest", "location_id", "requests_per_url", "require_status", "output"]) {
    if (!parsed[required]) {
      throw new Error(`missing --${required.replaceAll("_", "-")}`);
    }
  }
  return parsed;
}


function printHelp() {
  process.stdout.write(
    [
      "Usage: verify_legacy_url_invalidation.mjs",
      "  --probe-manifest PATH",
      "  --location-id ID",
      "  --requests-per-url 1..5",
      "  --require-status 404,410",
      "  --output PATH",
      "",
    ].join("\n"),
  );
}


function verifyManifest(payload, key) {
  if (payload?.schema !== "vowpic.legacy-url-probe-manifest.v1" || !Array.isArray(payload.entries)) {
    throw new Error("legacy URL probe manifest schema is invalid");
  }
  const signature = String(payload.signature || "");
  if (!/^hmac-sha256:[0-9a-f]{64}$/.test(signature)) {
    throw new Error("legacy URL probe signature is invalid");
  }
  const unsigned = { ...payload };
  delete unsigned.signature;
  const expected = createHmac("sha256", key).update(canonical(unsigned)).digest("hex");
  const actualBytes = Buffer.from(signature.slice("hmac-sha256:".length), "hex");
  const expectedBytes = Buffer.from(expected, "hex");
  if (actualBytes.length !== expectedBytes.length || !timingSafeEqual(actualBytes, expectedBytes)) {
    throw new Error("legacy URL probe signature mismatch");
  }
  const seen = new Set();
  for (const entry of payload.entries) {
    if (
      typeof entry?.url !== "string"
      || !/^https:\/\//.test(entry.url)
      || !/^[0-9a-f]{64}$/.test(String(entry.url_hmac_sha256 || ""))
      || !/^[0-9a-f]{64}$/.test(String(entry.expected_old_sha256 || ""))
      || seen.has(entry.url_hmac_sha256)
    ) {
      throw new Error("legacy URL probe entry is invalid");
    }
    seen.add(entry.url_hmac_sha256);
  }
}


async function probeEntry(entry, requestsPerUrl, allowedStatuses) {
  const attempts = [];
  for (let index = 0; index < requestsPerUrl; index += 1) {
    const response = await fetch(entry.url, {
      method: "GET",
      redirect: "manual",
      cache: "no-store",
      headers: {
        "cache-control": "no-cache",
        "user-agent": "vowpic-legacy-invalidation-probe/1",
      },
      signal: AbortSignal.timeout(20_000),
    });
    const bytes = Buffer.from(await response.arrayBuffer());
    const returnedSha256 = createHash("sha256").update(bytes).digest("hex");
    attempts.push({
      status: response.status,
      byte_count: bytes.length,
      differs_from_old_checksum: returnedSha256 !== entry.expected_old_sha256,
      passed: allowedStatuses.has(response.status) && returnedSha256 !== entry.expected_old_sha256,
    });
  }
  return {
    url_hmac_sha256: entry.url_hmac_sha256,
    attempts,
    passed: attempts.every((attempt) => attempt.passed),
  };
}


async function main() {
  const rawArgs = process.argv.slice(2);
  if (rawArgs.length === 1 && ["--help", "-h"].includes(rawArgs[0])) {
    printHelp();
    return;
  }
  const args = parseArgs(rawArgs);
  const manifestPath = path.resolve(args.probe_manifest);
  try {
    const file = await lstat(manifestPath);
    if (!file.isFile()) {
      throw new Error("legacy URL probe manifest is not a regular file");
    }
    if (process.platform !== "win32" && (file.mode & 0o077) !== 0) {
      throw new Error("legacy URL probe manifest must be mode 0600");
    }
    const key = Buffer.from(String(process.env.INVENTORY_HMAC_KEY || ""), "utf8");
    if (key.length < 32) {
      throw new Error("INVENTORY_HMAC_KEY must contain at least 32 bytes");
    }
    const payload = JSON.parse(await readFile(manifestPath, "utf8"));
    verifyManifest(payload, key);
    const requestsPerUrl = Number.parseInt(args.requests_per_url, 10);
    if (!Number.isInteger(requestsPerUrl) || requestsPerUrl < 1 || requestsPerUrl > 5) {
      throw new Error("requests-per-url must be between 1 and 5");
    }
    const allowedStatuses = new Set(
      args.require_status.split(",").map((value) => Number.parseInt(value.trim(), 10)),
    );
    if ([...allowedStatuses].some((value) => ![404, 410].includes(value))) {
      throw new Error("only 404 and 410 are valid invalidation statuses");
    }
    if (!/^[A-Za-z0-9_.:-]{1,80}$/.test(args.location_id)) {
      throw new Error("location ID is invalid");
    }
    const results = [];
    for (const entry of payload.entries) {
      results.push(await probeEntry(entry, requestsPerUrl, allowedStatuses));
    }
    const failedCount = results.filter((result) => !result.passed).length;
    const report = {
      schema: "vowpic.legacy-url-invalidation.v1",
      passed: failedCount === 0,
      location_id: args.location_id,
      inventory_sha256: payload.inventory_sha256,
      manifest_sha256: payload.manifest_sha256,
      expected_count: payload.entries.length,
      probed_count: results.length,
      failed_count: failedCount,
      requests_per_url: requestsPerUrl,
      results,
      verified_at: new Date().toISOString(),
    };
    const output = path.resolve(args.output);
    await mkdir(path.dirname(output), { recursive: true });
    await writeFile(output, `${canonical(report)}\n`, { encoding: "utf8", flag: "wx", mode: 0o600 });
    await chmod(output, 0o600);
    if (!report.passed) {
      throw new Error("one or more legacy URLs remain reachable");
    }
  } finally {
    await unlink(manifestPath).catch(() => {});
  }
}


main().catch((error) => {
  process.stderr.write(`ERROR: ${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
