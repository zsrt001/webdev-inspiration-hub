#!/usr/bin/env node

import { chmod, mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(
  new URL("../release-tools/package.json", import.meta.url),
);
const {
  issueSignedToken,
  parseStoreIdFromDelegationToken,
  presignUrl,
} = require("@vercel/blob");

const DELIVERY_SCHEMA = "vowpic.private-repair-presigned-delivery.v1";
const MAX_DELIVERY_BYTES = 10_000;
const VALIDITY_MS = 15 * 60 * 1_000;
const OBJECT_KEY_PATTERN =
  /^control-reader-repair\/[1-9][0-9]*\/delivery\.json$/;
const PUT_QUERY_KEYS = new Set([
  "pathname",
  "vercel-blob-add-random-suffix",
  "vercel-blob-allow-overwrite",
  "vercel-blob-allowed-content-types",
  "vercel-blob-delegation",
  "vercel-blob-maximum-size-in-bytes",
  "vercel-blob-signature",
  "vercel-blob-valid-until",
]);
const SAFE_FAILURE_REASONS = new Set([
  "private repair signed token issuance failed",
  "private repair delegation token validation failed",
  "private repair presigned URL creation failed",
  "private repair Blob token targets an unexpected store",
]);

function required(value, message) {
  const clean = String(value ?? "").trim();
  if (!clean) {
    throw new Error(message);
  }
  return clean;
}

export function validatedObjectKey(value) {
  const objectKey = required(value, "private repair delivery object key is missing");
  if (!OBJECT_KEY_PATTERN.test(objectKey)) {
    throw new Error("private repair delivery object key is invalid");
  }
  return objectKey;
}

export function normalizedStoreId(value) {
  let storeId = required(value, "private repair Blob store ID is missing");
  if (storeId.startsWith("store_")) {
    storeId = storeId.slice("store_".length);
  }
  storeId = storeId.toLowerCase();
  if (!/^[a-z0-9]{8,64}$/.test(storeId)) {
    throw new Error("private repair Blob store ID is invalid");
  }
  return storeId;
}

function uniqueQuery(url, allowedKeys) {
  const keys = new Set(url.searchParams.keys());
  if ([...keys].some((key) => !allowedKeys.has(key))) {
    throw new Error("private repair presigned URL has unexpected parameters");
  }
  for (const key of keys) {
    if (url.searchParams.getAll(key).length !== 1) {
      throw new Error("private repair presigned URL has duplicate parameters");
    }
  }
}

function validateCommonUrl(value) {
  const raw = required(value, "private repair presigned URL is missing");
  if (raw.length > 16_384) {
    throw new Error("private repair presigned URL is too long");
  }
  const url = new URL(raw);
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.port ||
    url.hash
  ) {
    throw new Error("private repair presigned URL is invalid");
  }
  if (
    !url.searchParams.get("vercel-blob-delegation") ||
    !url.searchParams.get("vercel-blob-signature")
  ) {
    throw new Error("private repair presigned URL is unsigned");
  }
  return url;
}

export function validatePresignedPutUrl(value, objectKey) {
  const url = validateCommonUrl(value);
  uniqueQuery(url, PUT_QUERY_KEYS);
  if (
    url.hostname !== "vercel.com" ||
    url.pathname !== "/api/blob/" ||
    url.searchParams.get("pathname") !== objectKey
  ) {
    throw new Error("private repair control-plane URL target is invalid");
  }
  if (
    url.searchParams.get("vercel-blob-allowed-content-types") !==
      "application/json" ||
    url.searchParams.get("vercel-blob-maximum-size-in-bytes") !==
      String(MAX_DELIVERY_BYTES) ||
    url.searchParams.get("vercel-blob-add-random-suffix") !== "false" ||
    url.searchParams.get("vercel-blob-allow-overwrite") !== "false"
  ) {
    throw new Error("private repair PUT URL constraints are invalid");
  }
  return url.toString();
}

function parseArguments(argv) {
  const options = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!name?.startsWith("--") || value === undefined || options.has(name)) {
      throw new Error("private repair presign arguments are invalid");
    }
    options.set(name, value);
  }
  if (
    options.size !== 2 ||
    !options.has("--object-key") ||
    !options.has("--output-directory")
  ) {
    throw new Error("private repair presign arguments are invalid");
  }
  return {
    objectKey: validatedObjectKey(options.get("--object-key")),
    outputDirectory: resolve(
      required(options.get("--output-directory"), "output directory is missing"),
    ),
  };
}

async function writeSecretFile(directory, name, value) {
  await writeFile(resolve(directory, name), `${value}\n`, {
    encoding: "utf8",
    flag: "wx",
    mode: 0o600,
  });
}

export async function createDeliveryCapabilities({
  objectKey,
  outputDirectory,
  readWriteToken,
  configuredStoreId,
}) {
  if (
    process.env.VERCEL_BLOB_API_URL ||
    process.env.NEXT_PUBLIC_VERCEL_BLOB_API_URL
  ) {
    throw new Error("private repair Blob API override is forbidden");
  }
  const key = validatedObjectKey(objectKey);
  const expectedStoreId = normalizedStoreId(configuredStoreId);
  const token = required(readWriteToken, "private repair Blob token is missing");
  if (token.length < 32) {
    throw new Error("private repair Blob token is invalid");
  }
  const issuedAt = Date.now();
  const requestedValidUntil = issuedAt + VALIDITY_MS;
  let signedToken;
  try {
    signedToken = await issueSignedToken({
      token,
      pathname: key,
      operations: ["put"],
      validUntil: requestedValidUntil,
      allowedContentTypes: ["application/json"],
      maximumSizeInBytes: MAX_DELIVERY_BYTES,
    });
  } catch {
    throw new Error("private repair signed token issuance failed");
  }
  let actualStoreId;
  try {
    actualStoreId = normalizedStoreId(
      parseStoreIdFromDelegationToken(signedToken.delegationToken),
    );
  } catch {
    throw new Error("private repair delegation token validation failed");
  }
  if (actualStoreId !== expectedStoreId) {
    throw new Error("private repair Blob token targets an unexpected store");
  }
  if (
    !Number.isInteger(signedToken.validUntil) ||
    signedToken.validUntil < issuedAt + 10 * 60 * 1_000 ||
    signedToken.validUntil > requestedValidUntil + 5_000
  ) {
    throw new Error("private repair Blob delegation expiry is invalid");
  }
  let put;
  try {
    put = await presignUrl(signedToken, {
      pathname: key,
      access: "private",
      validUntil: signedToken.validUntil,
      operation: "put",
      allowedContentTypes: ["application/json"],
      maximumSizeInBytes: MAX_DELIVERY_BYTES,
      addRandomSuffix: false,
      allowOverwrite: false,
    });
  } catch {
    throw new Error("private repair presigned URL creation failed");
  }
  const putUrl = validatePresignedPutUrl(put.presignedUrl, key);

  await mkdir(outputDirectory, { recursive: true, mode: 0o700 });
  await chmod(outputDirectory, 0o700);
  await writeSecretFile(outputDirectory, "delivery-object-key.txt", key);
  await writeSecretFile(outputDirectory, "delivery-put-url.txt", putUrl);
  const state = {
    expires_at: new Date(signedToken.validUntil).toISOString(),
    maximum_size_in_bytes: MAX_DELIVERY_BYTES,
    object_key: key,
    operations: ["put"],
    schema: DELIVERY_SCHEMA,
    state: "ISSUED",
  };
  await writeFile(
    resolve(outputDirectory, "delivery-capabilities.json"),
    `${JSON.stringify(state, null, 2)}\n`,
    { encoding: "utf8", flag: "wx", mode: 0o600 },
  );
  return state;
}

async function main() {
  const { objectKey, outputDirectory } = parseArguments(process.argv.slice(2));
  const state = await createDeliveryCapabilities({
    objectKey,
    outputDirectory,
    readWriteToken: process.env.PRIVATE_BLOB_READ_WRITE_TOKEN,
    configuredStoreId: process.env.PRIVATE_BLOB_STORE_ID,
  });
  process.stdout.write(
    `${JSON.stringify({ schema: state.schema, state: state.state })}\n`,
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    const reason = SAFE_FAILURE_REASONS.has(error?.message)
      ? error.message
      : "private Blob delivery capability generation failed";
    process.stderr.write(`${reason}\n`);
    process.exitCode = 1;
  });
}
