# VowPic Operations Runbook

VowPic is an overseas Web SaaS. This runbook coordinates existing scripts and protected workflows; it does not authorize Production changes. A missing approval, credential, isolated resource, or external contract is `NOT_RUN` and stops the sequence.

## Normal verification order

1. Start from a clean commit on `main` and run PR CI.
2. Confirm all high-risk capabilities remain OFF.
3. Run the protected identity Preview against isolated PostgreSQL roles and private storage; verify Google login, owner read, cross-user denial, deletion, and cleanup.
4. Run the protected commercial Preview against isolated PostgreSQL, Redis, private storage, EvoLink, and an ephemeral Worker; verify heartbeat, Provider contract/fetch, and cleanup.
5. Run Stage 6 only after Stage 5 is bound to the same exact source/runtime and CLEANED.
6. Start the manual Production workflow only after approvals, Worker-host contract, Provider/payment contracts, migration inventory, rollback material, and exact final SHA are all verified.

The protected workflow entrypoints are `.github/workflows/integration.yml`, `.github/workflows/safe-baseline-release.yml`, and `.github/workflows/production-release.yml`. Do not replace them with dashboard clicks, deploy hooks, or automatic main-to-production assignment.

## Feature-off and readiness response

- If public runtime configuration is missing or invalid, every high-risk frontend surface stays hidden and the API returns a fail-closed readiness result.
- If the Worker heartbeat, Redis lease, Provider contract, private storage, billing contract, or database role check fails, keep the related capability OFF.
- Do not restore service by reusing a migration credential, Production database, shared Preview Redis, public bucket, mock Provider response, or seeded payment fact.

## Migrations and Worker operations

- Run Alembic with the protected migration role only after inventory and restore evidence are complete.
- API and Worker use the application runtime role; release scripts use the constrained control-plane roles.
- The overseas long-running Worker must be deployed by the reviewed host contract with an immutable image digest, bounded commands, health/heartbeat verification, rollback, and redacted logs.
- Queue recovery uses lease, fencing token, retry budget, UNKNOWN-state reconciliation, and DLQ facts. Never enqueue an unbounded retry loop or manually patch a Provider task ID.

## Reconciliation, deletion, and cleanup

- Reconcile payment events, credit reservations, generation attempts, Provider task state, media grants, and delivery entitlements before closing a case.
- Account deletion revokes sessions and grants, removes permitted private objects, preserves required financial/audit facts, and verifies cross-user denial after cleanup.
- Every Preview run records its pre-change callback/origin/flag/resource snapshot and uses cancel-safe cleanup to restore it. Cleanup residue fails the gate.

## Rollback and incident handling

- Before promotion, verify the private-compatible baseline, staged target, manifest, source SHA, schema, Worker digest, runtime bundle, and rollback coordinates.
- On P0/P1, reconciliation, RLS, Provider-cost, payment, or cleanup failure: stop new dispatch, keep capabilities OFF, preserve immutable evidence, and execute the approved rollback path.
- Never delete customer, ledger, payment, or release evidence during diagnosis. Never expose secret plaintext in logs or incident artifacts.
- Production acceptance requires the formal-domain chain, at least 24 hours of observation, scheduled cleanup, reconciliation, and rollback rehearsal. Until those gates pass, status remains `NOT_RUN`/not accepted.

Detailed safe-baseline and role checks live in `docs/operations/risk-lockdown-runbook.md`; exact status is recorded in `docs/PRODUCTION_ACCEPTANCE.md` and `docs/ai-worklog.md`.
