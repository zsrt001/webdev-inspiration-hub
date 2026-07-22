# Production Worker host addendum

Status: **IMPLEMENTED IN REPOSITORY / LIVE PROVISIONING NOT YET RUN**

This addendum is the current operational authority for the overseas VowPic
Production Worker. The selected host is Railway and the immutable deployment
contract is `release/worker-host-contract.json` (`vowpic.worker-host-contract.v2`).
The repository implementation is executable; a live Railway project, service,
token, deployment, and runtime proof are still required before Production can
be called accepted.

## Deployment contract

- GitHub Actions builds `backend/Dockerfile.worker` once and pushes
  `ghcr.io/zsrt001/vowpic-worker` with an immutable digest.
- The release downloads Railway CLI `5.27.2` from the official release asset and
  verifies both the archive and executable SHA-256 values before use.
- Runtime secret values are read from the protected Production environment and
  passed to `railway variable set` through standard input. They are never placed
  in arguments or evidence reports.
- The Worker service is scaled to zero before its image source changes. The
  release accepts exactly one new successful Railway deployment for the
  digest-pinned image, then starts one replica.
- A signed status report is accepted only when Railway reports that exact
  deployment as successful and the staged/formal API returns the expected
  source, runtime, API deployment, Worker deployment, and image digest while
  `/health/ready` confirms a fresh Worker heartbeat.
- Rollback stops the Worker before the API rollback. Release evidence contains
  identifiers and hashes only, never secret values.

Interactive Railway project creation is intentionally outside the workflow.
The one-time account setup must provide `RAILWAY_TOKEN`, `RAILWAY_PROJECT_ID`,
`RAILWAY_ENVIRONMENT`, `RAILWAY_WORKER_SERVICE`, and `RAILWAY_REGION` to the
protected Production environment. Once present, the workflow performs the
bounded deployment without dashboard clicks.

## Evolink boundary

Evolink is used as a normal image-generation API. VowPic does not require an
Evolink-specific idempotency or lookup endpoint, provider agreement, or host
network-fault feature. Before the HTTP submit begins, VowPic persists the
attempt as `SUBMITTING` with its own correlation. If the response is lost, the
attempt becomes `UNKNOWN` and is not automatically submitted again. A verified
callback or an explicitly audited recovery may correlate the existing provider
task; otherwise the attempt remains fail-closed. This prevents duplicate
generation and duplicate charges without inventing a provider capability.

## Creem boundary

Creem subscription checkout uses the documented API, and period-end cancellation
uses the documented subscription cancel endpoint with a durable local intent so
an ambiguous timeout is never submitted twice. Renewal, cancellation, refund,
dispute, and replay outcomes are accepted from signed sandbox webhook/transaction
facts. VowPic does not call a refund mutation that is absent from its bound API
reference: refunds are initiated through Creem's Dashboard/support workflow, and
the signed `refund.created` event drives the existing immutable credit reversal
and entitlement logic. Missing sandbox evidence blocks only the affected
commercial activation, not Worker deployment or use of Evolink.

Production CI must never receive a raw card number, expiry, CVC, or reusable
payment instrument. The automated release gate verifies the configured live
products, signed-webhook boundary, checkout creation, and non-payment SaaS
chain without completing a charge. After the exact target is promoted, one
controlled low-value acceptance is completed on Creem's hosted checkout: the
operator enters payment details only on the Creem origin, VowPic verifies the
signed initial-payment projection, requests period-end cancellation through
the application, and verifies the scheduled-cancel state. A refund is then
initiated in the Creem Dashboard/support workflow and is accepted only after
the signed `refund.created` event produces the matching immutable reversal.
Until those facts exist, payment acceptance remains `NOT_RUN`; code deployment
and EvoLink image generation are not blocked by a reusable payment credential.

## Current release effect

Repository implementation and static tests may proceed. Production acceptance
remains pending only until the real Railway coordinates/secrets are provisioned,
the single staged deployment passes the linked SaaS acceptance flow, the same
artifact is promoted once, and the formal domain completes observation.
