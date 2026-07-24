# VowPic Operations Runbook

VowPic is an overseas Web SaaS. This runbook coordinates existing scripts and protected workflows; it does not authorize Production changes. A missing approval, credential, isolated resource, or required live observation is `NOT_RUN` for the affected capability.

## Normal verification order

1. Start from a clean commit on `main` and run PR CI.
2. Confirm all high-risk capabilities remain OFF.
3. Run the protected identity Preview against isolated PostgreSQL roles and private storage; verify Google login, owner read, cross-user denial, deletion, and cleanup.
4. Run the protected commercial Preview against isolated PostgreSQL, private storage, EvoLink, and Creem Test Mode. The Vercel FastAPI website backend must submit and reconcile generation work itself; verify its runtime coordinate, Provider submit/task-query/fetch path, payment/refund/subscription lifecycle, private media, account export/delete, Partner Invite, accessibility, and cleanup.
5. Materialize one complete Preview release aggregate bound to the same exact source SHA, runtime bundle, API deployment, gate contract, and cleanup reports. Production may not reserve or deploy from a partial Stage 5 aggregate or a `CLEANED` coordinate alone.
6. Start the manual Production workflow only after the complete Preview aggregate, required Provider/payment configuration, migration inventory, rollback material, approvals, and exact final SHA are verified. Missing payment lifecycle evidence blocks the commercial release; it is not converted into a deployment success.

The protected workflow entrypoints are `.github/workflows/integration.yml`, `.github/workflows/safe-baseline-release.yml`, and `.github/workflows/production-release.yml`. Do not replace them with dashboard clicks, deploy hooks, or automatic main-to-production assignment.

## Feature-off and readiness response

- If public runtime configuration is missing or invalid, every high-risk frontend surface stays hidden and the API returns a fail-closed readiness result.
- If the backend runtime coordinate, PostgreSQL lease/fencing checks, Provider adapter/task-query/fetch path, private storage, billing configuration, or database role check fails, keep the related capability OFF.
- Do not restore service by reusing a migration credential, Production database, public bucket, mock Provider response, or seeded payment fact.

## Migrations and backend generation operations

- Run Alembic with the protected migration role only after inventory and restore evidence are complete.
- The FastAPI website backend uses the application runtime role; release scripts use constrained control-plane and observation roles.
- Order creation performs one bounded EvoLink submission from the website backend. While the user is on the order page, authenticated `POST /api/v1/orders/{order_id}/progress` requests perform one PostgreSQL-backed lease/reconciliation/QA/settlement/delivery step at a time. The protected maintenance POST is manual recovery only; Production does not depend on Vercel Cron.
- Recovery uses PostgreSQL lease, claim ID, fencing token, retry budget, and `UNKNOWN`-state reconciliation. Never blindly repeat a Provider POST or manually patch a Provider task ID.

## UNKNOWN Provider submission manual settlement

Use this procedure only when generation recovery reports `human_required > 0` and an attempt is `UNKNOWN` without a Provider task ID. Keep automated generation recovery fail-closed until every listed case is resolved.

1. List the durable cases with the protected Admin endpoint `GET /api/v1/admin/generation/manual-settlements`. Do not use the generic order-status endpoint for a generation-managed order.
2. In the already authorized EvoLink operator console or read-only task-query surface, search by the stored request correlation and submission time. Preserve a sanitized evidence record in the approved private incident evidence store; it must contain the observed Provider acceptance fact and exact task ID when one exists, but no API key, cookie, connection string, or unrelated customer content.
3. Compute the SHA-256 of that immutable evidence record. Store only the evidence SHA-256 and an operator explanation in the VowPic resolution request; do not copy secret plaintext into the database, repository, logs, or Admin API.
4. Select exactly one action and submit it to `POST /api/v1/admin/generation/manual-settlements/{job_id}/resolve`:
   - `BIND_PROVIDER_TASK`: use only when EvoLink proves the request was accepted and gives the exact task ID. Send `provider_task_id`; do not send `provider_accepted`. This audited API binding is allowed; raw SQL or direct database patching is not.
   - `CONFIRMED_NOT_ACCEPTED_RETRY`: use only when EvoLink evidence proves the request was not accepted. Send `provider_accepted: false`. This authorizes one bounded resubmission of the existing attempt; absence of a task ID is not proof of non-acceptance.
   - `FAIL_AND_SETTLE`: use when the case must terminate. Send the proven `provider_accepted` fact. VowPic releases an unaccepted reservation, or captures and refunds an accepted/captured generation debit through the ledger.
5. Verify the returned job, attempt, order, reservation, settlement, and next-action states. Then verify the `resolve_generation_ambiguous_submission` Admin audit entry, the credit ledger/reservation lineage, revoked stale asset grants, and that no duplicate generation debit, refund, or Provider submission was created.
6. Re-run the protected generation maintenance endpoint. Confirm `human_required` decreases only for the resolved case and that remaining cases stay visible. A missing or inconclusive Provider fact remains `HUMAN_REQUIRED`; it must never trigger automatic replay.

## Legacy outbox retirement before Production observation

The outbox table remains for download audit and historical schema compatibility; it is not a generation queue. Before starting durable Production observation, the protected release workflow performs a bounded website-backend recovery drain and then runs `backend/scripts/retire_legacy_outbox.py` in two phases:

1. `inventory` uses the dedicated read-only Production login and classifies every `PENDING`, `PROCESSING`, or `FAILED` legacy envelope by its exact event contract and underlying generation/payment authority row.
2. `apply` requires the protected migration login, the data-migration approval ID, and the exact inventory snapshot hash. It marks only proven handled envelopes `DISPATCHED`, clears obsolete leases, and records a source-SHA-bound retirement reason on the same row.
3. Applied payment events, submitted/terminal generation attempts, and generation jobs already claimed or fully settled may retire. Queued/unsettled generation, `UNKNOWN` or `SUBMITTING` attempts, unapplied payments, missing facts, malformed payloads, and unknown event types remain blockers.
4. Re-running is idempotent. Never delete outbox rows, exclude legacy rows from the observation metric, mass-update unknown rows, or use a retry to infer Provider non-acceptance. Observation starts only when the post-apply active and blocked counts are both zero.

## Reconciliation, deletion, and cleanup

- Reconcile payment events, credit reservations, generation attempts, Provider task state, media grants, and delivery entitlements before closing a case.
- Account deletion revokes sessions and grants, removes permitted private objects, preserves required financial/audit facts, and verifies cross-user denial after cleanup.
- Every Preview run records its pre-change callback/origin/flag/resource snapshot and uses cancel-safe cleanup to restore it. Cleanup residue fails the gate.

## Rollback and incident handling

- Before promotion, verify the private-compatible baseline, staged target, manifest, source SHA, schema, backend executor digest, runtime bundle, and rollback coordinates.
- On P0/P1, reconciliation, RLS, Provider-cost, payment, or cleanup failure: stop new dispatch, keep capabilities OFF, preserve immutable evidence, and execute the approved rollback path.
- Never delete customer, ledger, payment, or release evidence during diagnosis. Never expose secret plaintext in logs or incident artifacts.
- Production acceptance requires the formal-domain chain, at least 24 hours of observation, scheduled cleanup, reconciliation, and rollback rehearsal. Until those gates pass, status remains `NOT_RUN`/not accepted.

Detailed safe-baseline and role checks live in `docs/operations/risk-lockdown-runbook.md`; exact status is recorded in `docs/PRODUCTION_ACCEPTANCE.md` and `docs/ai-worklog.md`.
