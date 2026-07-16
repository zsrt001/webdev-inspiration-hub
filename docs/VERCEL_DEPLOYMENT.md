# Vercel Web SaaS Deployment Guide

VowPic is an overseas responsive Web SaaS. It has no WeChat, Mini Program,
guest-account, public-password, anonymous partner-session, Live Portrait,
local-recommendation, or lead/CRM product surface.

The frontend is compiled with uni-app's Web target. Uni-app names that target
`h5`, so the compiler output directory remains `frontend/dist/build/h5`; this
is a build-tool convention, not a separate H5 product or mobile runtime.

## Build contract

Use the repository root as the Vercel project root. The checked-in
`vercel.json` is authoritative:

```text
Install Command: cd frontend && npm ci --ignore-scripts
Build Command: cd frontend && npm run build:web
Output Directory: frontend/dist/build/h5
Backend Function: api/index.py
```

The root [`.python-version`](../.python-version) fixes the Vercel Function
runtime to Python 3.12. The root `requirements.txt` is generated, installed
with hashes, dependency-checked, and entry-point imported in an exact Python
3.12.13 Bookworm CI job. That Vercel API graph is intentionally separate from
the Python 3.11 Worker/backend lock so version-conditional dependencies cannot
be hidden by the wrong resolver runtime.

The root [`.vercelignore`](../.vercelignore) must keep its repository-level
`scripts` exclusion anchored as `/scripts`. The Web build invokes
`frontend/scripts/clean-web-output.mjs`; an unanchored `scripts` pattern also
excludes that nested directory from Vercel's upload context even though the
file remains present in Git and local CI.

The `/api/*` and `/health*` routes are rewritten to FastAPI; all other paths
fall back to the Web application. Production deployment from `main` remains
disabled in `vercel.json` while the protected release workflow is in force.

## Environment contract

Use [`backend/.env.example`](../backend/.env.example) as the field inventory.
Do not copy example values into Production, commit secrets, or add provider
keys to `VITE_*` variables.

At minimum, the deployment owner must provision and validate:

- runtime and control-plane PostgreSQL URLs using the roles required by the
  release contract;
- Vercel runtime coordinates and the runtime-bundle identity;
- Supabase/Google OAuth backend settings;
- private storage, generation-provider, payment-provider, webhook, support,
  rate-limit, and cron credentials required by the capabilities being tested;
- a backend-only Admin token or configured Admin UUID/email.

The bootstrap environment flags in `vercel.json` remain `false`. PostgreSQL
`ops_feature_flags` is the runtime authority for the seven active
capabilities. Missing rows, database failures, stale bundle bindings, or
invalid release coordinates must resolve to OFF. Frontend visibility is only a
UX reflection of that server decision.

Retired feature flags such as `REMOTE_JOIN_ENABLED` and
`LIVE_PORTRAIT_ENABLED` stay explicitly false only as safe-baseline controls;
they do not restore routes or product UI.

## Database and identity

Apply reviewed Alembic migrations before promotion. Run migration commands
from `backend` so the `app` package resolves correctly:

```powershell
Push-Location backend
..\.venv\Scripts\python.exe scripts/migrate_db.py
Pop-Location
```

Application startup and ordinary requests must not execute runtime DDL.
Production readiness must verify the expected revision, runtime role,
control-plane role, and RLS posture.

Configure Google OAuth URLs exactly as described in
[`SUPABASE_SETUP.md`](./SUPABASE_SETUP.md). Do not use wildcard Vercel callback
URLs. The browser receives no Supabase key from the frontend bundle; it starts
and exchanges OAuth through backend-owned endpoints.

## Current safe-baseline behavior

Before later commercial stages are activated, all seven high-risk
capabilities are OFF. The storefront may load public content, but authentication,
uploads, generation, credit-pack checkout, subscription billing, private
downloads, and partner invites must remain unavailable.

The following are intentional fail-closed behaviors, not release failures:

- retired product routes return HTTP 410 with stable retirement codes;
- disabled active capabilities return structured HTTP 503 responses before
  identity lookup, database mutation, queueing, or provider calls;
- the cleanup endpoints return `cleanup_paused` until durable deletion retry
  and recovery are installed;
- billing UI and catalog fallbacks remain hidden while billing capabilities
  are OFF.

Do not claim Google login, checkout, generation, private download, or automatic
deletion is production-ready solely because the Web build loads.

## Local verification before deployment

From the repository root:

```powershell
Push-Location backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
Pop-Location

Push-Location frontend
npm ci --ignore-scripts
npm run typecheck
npm run build:web
Pop-Location

.\.venv\Scripts\python.exe scripts/release/verify_safe_baseline.py
git diff --check
```

Record skipped integration tests and missing external credentials as
`NOT_RUN`; never convert them to PASS.

## Staged and formal-domain verification

Deployment is an external state change and requires explicit approval. Use the
protected release workflow in
[`operations/risk-lockdown-runbook.md`](./operations/risk-lockdown-runbook.md)
in its documented order:

1. bind the exact reviewed source SHA, runtime bundle, migration revision,
   Vercel project, staged deployment, and formal domain;
2. prove all capability rows are OFF against live PostgreSQL authority;
3. verify liveness, strict readiness, every guarded route, every 410 tombstone,
   signed webhook/reconciliation/logout reachability, and zero runtime DDL;
4. promote only after the staged report passes;
5. repeat the same checks on the formal domain and preserve immutable evidence;
6. restore or retain edge deny rules on any mismatch. Never roll back to the
   known-unsafe pre-baseline deployment.

Opening the homepage or receiving HTTP 200 from `/health` is not sufficient
evidence that the SaaS business flow is ready.
