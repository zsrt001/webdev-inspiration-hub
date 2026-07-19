# Production Worker host addendum

Status: **NOT APPROVED / NOT RUN**

This document records the current truth boundary for the overseas VowPic
Production Worker. No host provider, executable, deployment, registry, or
one-shot Evolink response-drop control has been approved or verified yet.
`release/worker-host-contract.json` is therefore deliberately fail-closed and
must not be treated as a deployable host configuration.

## Required evidence before approval

Approval requires one reviewed host-specific replacement of the current
contract that binds:

- the provider and an immutable absolute control executable with its SHA-256;
- exact argument contracts for every allowlisted Worker action;
- digest-pinned image build/push and suspended deployment;
- secret injection without command-line or report disclosure;
- start, stop, dispatch, heartbeat, rollback, logs, absence checks, and failure
  reconciliation;
- a one-shot response-drop rule scoped to one persisted fault intent, runtime,
  deployment, client correlation, one-submit limit, cost cap, and expiry;
- intent-ID keyed inspect/disarm after a lost arm response;
- a durable non-reusable disarm tombstone that rejects late or concurrent arm;
- monitored support ownership, legal review, region, retention, and incident
  response contacts.

Screenshots, mutable image tags, an executable without a pinned hash, or a rule
that cannot be queried after the original runner disappears are insufficient.

## Current release effect

`scripts/release/run_approved_worker_host.py` exits with `NOT_RUN` before
executing any external process while this contract remains unapproved.
Consequently COMMERCIAL_7A source freeze, Production migration, activation, and
observation must remain blocked. This is a factual external prerequisite, not a
test fixture or an application bypass.
