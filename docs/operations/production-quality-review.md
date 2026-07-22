# Production six-case quality review

This runbook covers the only human step in the `COMMERCIAL_7A` linked
acceptance chain. It does not authorize a release, deployment, payment, data
migration, or capability activation. The protected workflow remains
fail-closed until the exact six generated cases are reviewed.

## Preconditions

- The `linked-production-acceptance-prepare` job has passed.
- The `quality-human-review` job is waiting for the separately submitted,
  run-bound reviewed artifact.
- Download the artifact named
  `commercial-7a-quality-review-<run-id>-<attempt>`.
- Confirm that the artifact contains `quality-review-request.json`,
  `quality-review-draft.json`, `release.json`, and `release-manifest.json`.
- Work only from the exact source SHA and run attempt named by the request.
  Never reuse a draft or review from another run.

The request expires two hours after `produced_at`. If it is expired, reject or
cancel the pending job and rerun the protected release from a fresh request.
Do not edit the request, extend its expiry, or reuse a previous review.

## Review the exact private outputs

1. Read the staged target origin from the protected release coordinates in
   `release.json`. Do not copy that origin into the completed draft.
2. Sign in to that exact staged target with the dedicated acceptance account.
3. For each entry in `quality-review-request.json`, open the bound private
   final master in the same authenticated browser:

   ```text
   <staged-target-origin>/api/v1/orders/<order_id>/assets/<review_asset_id>/download
   ```

4. Confirm that the image corresponds to the named case and review it against
   `release/quality-rubric.json`.
5. Inspect all six cases. A missing, inaccessible, substituted, or ambiguous
   image is a failed review; do not submit a passing review.

`review_asset_id` is database-bound to the exact active `final_master` whose
parent is the selected passing candidate. The final collector repeats this
lineage check before accepting any score.

## Complete the draft

Make a copy of `quality-review-draft.json`. Do not edit its `schema`,
`request_sha256`, case IDs, or case count.

For every case:

- set `reviewer_ref` to an opaque authorized reviewer reference; do not use an
  email address or access token;
- set all four integer scores from 1 through 5;
- list only defined hard-defect codes, or use an empty list;
- set `reviewed_at` to the actual UTC review time within the request window.

Set top-level `review_complete` to `true` only after all six images have been
reviewed. Zero placeholder scores, missing fields, duplicate cases, late
timestamps, unknown defects, or a different request hash are rejected.

## Submit without exposing signing keys

1. Base64-encode the completed JSON bytes without changing the JSON file.
2. Dispatch `.github/workflows/production-quality-review.yml` with the exact
   source SHA, release run ID, release attempt, and the encoded completed
   draft.
3. Confirm that the review workflow uploads
   `commercial-7a-reviewed-quality-<run-id>-<attempt>` before the two-hour
   request expiry. The waiting release job imports that exact artifact and
   continues automatically.

The review workflow, not the reviewer workstation, reads the acceptance and
quality signing keys. It binds the completed draft to the immutable request,
creates `quality-human-review.json`, and uploads the reviewed artifact. No
run-specific review is stored as a reusable GitHub Secret. The final
acceptance job independently verifies the signature, release coordinates,
request hash, case set, timestamps, rubric, and database lineage before it
restores Worker dispatch.

## Failure handling

- Do not submit a passing review when any case fails the rubric. Leave the
  failing scores or hard defects in the draft so the review workflow records
  a truthful failure.
- If the request expires or the account/output cannot be inspected, cancel
  the run. The release cleanup path keeps high-risk flags OFF, disables the
  Worker, and reconciles the staged auth origin.
- Never paste raw credentials, cookies, signed download URLs, image bytes, or
  customer identity into the draft, workflow logs, or issue comments.
