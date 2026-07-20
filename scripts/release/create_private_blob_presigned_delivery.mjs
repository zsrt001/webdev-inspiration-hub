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
const SIGNATURE_QUERY_KEYS = new Set([
  "vercel-blob-delegation",
  "vercel-blob-signature",
  "vercel-blob-valid-until",
]);
const PUT_QUERY_KEYS = new Set([
  "pathname",
  "vercel-blob-add-random-suffix",
  "vercel-blob-allow-overwrite",
  "vercel-blob-allowed-content-types",
  "vercel-blob-maximum-size-in-bytes",
  ...SIGNATURE_QUERY_KEYS,
]);
const DELETE_QUERY_KEYS = new Set(["pathname", ...SIGNATURE_QUERY_KEYS]);

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

export function validatePresignedUrl(
  value,
  operation,
  objectKey,
  storeId,
) {
  const url = validateCommonUrl(value);
  if (operation === "get") {
    uniqueQuery(url, SIGNATURE_QUERY_KEYS);
    if (
      url.hostname !== `${storeId}.private.blob.vercel-storage.com` ||
      url.pathname !== `/${objectKey}`
    ) {
      throw new Error("private repair GET URL target is invalid");
    }
    return url.toString();
  }
  const allowedKeys = operation === "put" ? PUT_QUERY_KEYS : DELETE_QUERY_KEYS;
  uniqueQuery(url, allowedKeys);
  if (
    !["put", "delete"].includes(operation) ||
    url.hostname !== "vercel.com" ||
    url.pathname !== "/api/blob/" ||
    url.searchParams.get("pathname") !== objectKey
  ) {
    throw new Error("private repair control-plane URL target is invalid");
  }
  if (
    operation === "put" &&
    (url.searchParams.get("vercel-blob-allowed-content-types") !==
      "application/json" ||
      url.searchParams.get("vercel-blob-maximum-size-in-bytes") !==
        String(MAX_DELIVERY_BYTES) ||
      url.searchParams.get("vercel-blob-add-random-suffix") !== "false" ||
      url.searchParams.get("vercel-blob-allow-overwrite") !== "false")
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
  const signedToken = await issueSignedToken({
    token,
    pathname: key,
    operations: ["put", "get", "delete"],
    validUntil: requestedValidUntil,
    allowedContentTypes: ["application/json"],
    maximumSizeInBytes: MAX_DELIVERY_BYTES,
  });
  const actualStoreId = normalizedStoreId(
    parseStoreIdFromDelegationToken(signedToken.delegationToken),
  );
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
  const common = { pathname: key, access: "private", validUntil: signedToken.validUntil };
  const put = await presignUrl(signedToken, {
    ...common,
    operation: "put",
    allowedContentTypes: ["application/json"],
    maximumSizeInBytes: MAX_DELIVERY_BYTES,
    addRandomSuffix: false,
    allowOverwrite: false,
  });
  const get = await presignUrl(signedToken, { ...common, operation: "get" });
  const remove = await presignUrl(signedToken, {
    ...common,
    operation: "delete",
  });
  const putUrl = validatePresignedUrl(put.presignedUrl, "put", key, actualStoreId);
  const getUrl = validatePresignedUrl(get.presignedUrl, "get", key, actualStoreId);
  const deleteUrl = validatePresignedUrl(
    remove.presignedUrl,
    "delete",
    key,
    actualStoreId,
  );

  await mkdir(outputDirectory, { recursive: true, mode: 0o700 });
  await chmod(outputDirectory, 0o700);
  await writeSecretFile(outputDirectory, "delivery-object-key.txt", key);
  await writeSecretFile(outputDirectory, "delivery-put-url.txt", putUrl);
  await writeSecretFile(outputDirectory, "delivery-get-url.txt", getUrl);
  await writeSecretFile(outputDirectory, "delivery-delete-url.txt", deleteUrl);
  const state = {
    expires_at: new Date(signedToken.validUntil).toISOString(),
    maximum_size_in_bytes: MAX_DELIVERY_BYTES,
    object_key: key,
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
  main().catch(() => {
    process.stderr.write("private Blob delivery capability generation failed\n");
    process.exitCode = 1;
  });
}
