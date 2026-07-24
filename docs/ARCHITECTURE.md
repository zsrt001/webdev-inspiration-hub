# VowPic Architecture

VowPic is an overseas Web SaaS. This document describes the current repository architecture; it does not claim that protected Preview, Production, or external Provider gates have run. Missing live evidence remains `NOT_RUN`.

## Runtime topology

- `frontend/` is a Vue 3 and Uni-app browser application. `uni -p h5` is only Uni-app's Web build target token.
- `api/` exposes the FastAPI application through Vercel. The API owns authentication exchange, business rules, order facts, private delivery, and sanitized public capability configuration.
- The FastAPI website backend executes generation through `generation_executor_service.py`. Order creation performs one bounded immediate kick. The authenticated order page then calls `POST /api/v1/orders/{order_id}/progress`; each request advances at most one durable step, while the protected operations endpoint remains available for explicit recovery. There is no Vercel Cron dependency.
- PostgreSQL is the authoritative store for identities, sessions, feature flags, credits, reservations, orders, attempts, media grants, release coordinates, and audit facts.
- Redis is optional cache/session infrastructure only. It is not a generation queue, lease authority, deployment gate, or Production prerequisite.
- `generation-job.v1` is the durable compatibility boundary across website deployments. The active backend authorizes Provider work against its own release activation, then resumes compatible older jobs without rewriting their origin stamps. Legacy or unstamped payloads stay outside the executor and are classified by the controlled generation backfill before activation.
- Private object storage holds source media, masters, variants, and create-once release evidence. Public Vercel Blob is not a substitute for the required private acceptance store.

## External services

- Supabase Auth provides Google OAuth and the PostgreSQL project boundary. The application issues its own HttpOnly session after validating the short-lived Supabase token.
- EvoLink is the only image-generation Provider. VowPic persists a local `SUBMITTING` attempt before Provider I/O; an ambiguous response becomes `UNKNOWN` and is never submitted again automatically. EvoLink-specific idempotency or client-correlation lookup is not a deployment prerequisite, while the configured key, submit/task-query flow, callback, and private-input fetch still require protected live verification.
- Creem supplies signed payment and subscription facts. Missing live checkout, scheduled-cancel, or refund evidence keeps only the affected billing capability OFF; it does not block code deployment or EvoLink generation.
- Vercel hosts Web/API deployments. Automatic Preview is browse-only; protected Preview workflows build exact deployment/runtime evidence.

## Runtime roles

Database access is split into distinct login roles on one database:

- migration administrator: workflow-only schema and controlled release operations;
- application runtime: normal website API reads and business writes under RLS;
- control-plane writer: constrained feature-flag and release-state transitions;
- control-plane reader: read-only release-coordinate resolution.

The migration credential must never be injected into the website API. The runtime and control-plane logins must be distinct, non-superuser, and non-`BYPASSRLS`.

## Release layers

1. PR CI verifies locked dependencies, migrations, PostgreSQL RLS/concurrency, OpenAPI drift, frontend tests/build/accessibility, and backend generation contracts.
2. Stage 5 binds a protected identity Preview and a separate commercial Preview to exact source, bundle, deployment, database, private storage, Provider, and cleanup evidence.
3. Stage 6 exercises the real SaaS journey, payment, private delivery, account deletion, Partner Invite, visual, responsive, and accessibility cases.
4. Production uses a manual protected staged deployment, explicit promotion, observation, rollback rehearsal, and post-contract cleanup.

No layer may infer PASS from a lower layer. Current gate state is tracked in `release/gates.json` and `docs/PRODUCTION_ACCEPTANCE.md`. `release/provider-capabilities.json` records the adapter behavior implemented by the code; real provider behavior is proven separately in protected Preview.
