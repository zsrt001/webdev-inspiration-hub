# VowPic Architecture

VowPic is an overseas Web SaaS. This document describes the current repository architecture; it does not claim that protected Preview, Production, or external Provider gates have run. Missing live evidence remains `NOT_RUN`.

## Runtime topology

- `frontend/` is a Vue 3 and Uni-app browser application. `uni -p h5` is only Uni-app's Web build target token.
- `api/` exposes the FastAPI application through Vercel. The API owns authentication exchange, business rules, order facts, private delivery, and sanitized public capability configuration.
- `backend/app/worker.py` is the long-running Redis/ARQ Worker runtime. Vercel functions do not execute generation jobs.
- PostgreSQL is the authoritative store for identities, sessions, feature flags, credits, reservations, orders, attempts, media grants, release coordinates, and audit facts.
- Redis carries queues, leases, heartbeat state, and bounded coordination. Protected commercial Preview requires an isolated Redis instance.
- Private object storage holds source media, masters, variants, and create-once release evidence. Public Vercel Blob is not a substitute for the required private acceptance store.

## External services

- Supabase Auth provides Google OAuth and the PostgreSQL project boundary. The application issues its own HttpOnly session after validating the short-lived Supabase token.
- EvoLink is the only image-generation Provider. Generation remains OFF while lost-submit-response reconciliation is `UNVERIFIED`.
- Creem supplies signed payment and subscription facts. Refund and subscription lifecycle contracts remain OFF until official contract and sandbox evidence are verified.
- Vercel hosts Web/API deployments. Automatic Preview is browse-only; protected Preview workflows build exact deployment/runtime evidence.

## Runtime roles

Database access is split into distinct login roles on one database:

- migration administrator: workflow-only schema and controlled release operations;
- application runtime: normal API/Worker reads and business writes under RLS;
- control-plane writer: constrained feature-flag and release-state transitions;
- control-plane reader: read-only release-coordinate resolution.

The migration credential must never be injected into the API or Worker. The runtime and control-plane logins must be distinct, non-superuser, and non-`BYPASSRLS`.

## Release layers

1. PR CI verifies locked dependencies, migrations, PostgreSQL RLS/concurrency, OpenAPI drift, frontend tests/build/accessibility, and the Worker image.
2. Stage 5 binds a protected identity Preview and a separate commercial Preview to exact source, bundle, deployment, database, private storage, Redis, Provider, and cleanup evidence.
3. Stage 6 exercises the real SaaS journey, payment, private delivery, account deletion, Partner Invite, visual, responsive, and accessibility cases.
4. Production uses a manual protected staged deployment, explicit promotion, observation, rollback rehearsal, and post-contract cleanup.

No layer may infer PASS from a lower layer. Current gate state is tracked in `release/gates.json` and `docs/PRODUCTION_ACCEPTANCE.md`. `release/provider-capabilities.json` records the adapter behavior implemented by the code; real provider behavior is proven separately in protected Preview.
