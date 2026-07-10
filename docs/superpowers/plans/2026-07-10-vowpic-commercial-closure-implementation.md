# VowPic Commercial Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current VowPic repository into the approved overseas Web-only commercial product, stopping the currently exposed high-risk paths first and reaching `Production accepted` only after the linked production evidence gates pass.

**Architecture:** Preserve FastAPI, Vue 3/Uni-app H5, Supabase/PostgreSQL, Redis/ARQ, Creem, Evolink, and private object storage. PostgreSQL owns identity, billing, media, job, consent, and feature-flag facts; Redis only caches flags and wakes durable jobs; a long-running OCI Worker owns Provider, QA, repair, and delivery work. This is one dependency-ordered program split into six reviewable work packages and two production releases; never implement it as one unreviewable deployment.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy Async, Alembic, PostgreSQL 15, Redis 7, ARQ, Pydantic 2 strict schemas, Pillow/OpenCV, Supabase Google OAuth, Creem, Evolink, Vercel Web/API, Vue 3.4.21, Uni-app H5, Pinia, TypeScript 5.3.3, Vite 5.2.8, Vitest 1.6.1, Playwright 1.61.1.

## Global Constraints

- Authority is `docs/superpowers/specs/2026-07-10-vowpic-commercial-closure-design.md`. If an older README, PRD, plan, fallback constant, runtime JSON file, or UI string conflicts with that specification, stop and resolve the conflict in favor of the specification before coding.
- Product surface is overseas Web/H5 only. Remove WeChat, Mini Program, OpenID ownership, guest generation, public password auth, public external-URL input, Live Portrait, local vendor recommendations, leads/contact forms, transactional-email promises, and unverified subscription perks from the active release.
- Do not create `/v2`, replace the stack, introduce a UI framework, or add a second generation Provider.
- Work in an isolated worktree created with `superpowers:using-git-worktrees`; suggested branch: `codex/vowpic-commercial-closure`.
- Start from Alembic head `20260516_0012`; append migrations `20260710_0013` onward and never edit historical migrations.
- Production identity is Google through Supabase Authorization Code + PKCE. Local access lifetime is 15 minutes; rotating refresh lifetime is 30 days; browser transport is Cookie-only.
- State-changing Cookie requests require SameSite, exact Origin, and CSRF validation. Browser code must not store local or Supabase bearer tokens in local storage.
- Upload limits are 10 MiB/file, 5 files/request, 40 MP, 20 requests/hour/user, 200 MiB/day/user, and 2 concurrent uploads/user. Allowed decoded formats are JPEG, PNG, and WebP.
- Provider asset grants use 32 random bytes, 600-second TTL, and at most 3 reads.
- Credit reservation TTL is 1800 seconds. Generation lease is 120 seconds with 30-second heartbeat. Never retry a SUBMITTING/UNKNOWN Provider attempt without reconciliation.
- `WELCOME_BONUS=2` is granted once per verified provider subject and can fund only the base single-subject trial; couple, Partner Invite, Golden Anniversary, Director, and premium-scene modes are never free.
- QA is fail-closed. One initial generation plus at most two candidate-producing repair attempts are allowed. Watermark failure never exposes the final master.
- Trial output is one watermarked 3:4 image no larger than 900x1125. Paid output is a private 3:4 master plus 2:3, 3:2, 3:4, 4:5, 9:16, and 1:1 variants.
- Retention is 24 hours for orphan uploads, 7 days for source/reference images, 30 days free, 90 days credit pack or expired subscription credits, 180 days active ordinary subscription, and 365 days active Studio.
- Runtime feature flags are PostgreSQL-authoritative and use `OFF | ACCEPTANCE_COHORT | ON`; Redis may cache only `OFF` for at most 30 seconds. `ACCEPTANCE_COHORT`/`ON` require a live authority read, acceptance cohorts expire within 86400 seconds, and every authority/cache failure fails closed.
- Frontend runs on Node 20. Pin `vue@3.4.21`, `typescript@5.3.3`, `vite@5.2.8`, and `vue-tsc@1.8.27`; add exact `vitest@1.6.1`, `@vue/test-utils@2.4.6`, `jsdom@24.1.3`, `@playwright/test@1.61.1`, `@axe-core/playwright@4.12.1`, and `openapi-typescript@7.13.0`.
- Pin Vercel CLI to `55.0.0`. Never install `latest` in CI.
- Every production-affecting task adds focused tests first, proves the new test fails for the intended reason, implements the minimum change, reruns the focused tests, updates `docs/ai-worklog.md`, reviews the diff, and commits.
- Every PowerShell release/migration job begins with `Set-StrictMode -Version Latest`, `$ErrorActionPreference = 'Stop'`, and, on the pinned PowerShell version, `$PSNativeCommandUseErrorActionPreference = $true`; alternatively every native process is invoked through one tested wrapper that throws on a nonzero exit code. A later successful command must never mask a failed Python/Node/npm/npx/Alembic command. Workflow tests inject a native failure immediately before every write, migration, Provider call, Worker transition, domain transition, flag transition, and final CAS, and prove the next command was not invoked.
- PowerShell interpolation that appends a colon to an environment variable must use `${env:NAME}:suffix`; static tests reject every unbraced environment-variable-plus-colon form because it truncates fencing/idempotency keys.
- Mocking is allowed for unit isolation only. Preview and Production gates must use the real integration class named by the specification and cannot be satisfied by fixtures, old artifacts, or Admin probes.
- Do not open Generation, credit-pack checkout, subscriptions, private download, or Partner Invite until that capability's mandatory gate is fresh and bound to the target release bundle.

---

## Work Packages And Exit Gates

| Package | Tasks | Exit gate |
| --- | --- | --- |
| 0. Stop risk growth | 1-4 | Unsafe routes are server-blocked; audited kill switches work; inventory and restore evidence exist; baseline gates cannot report false success. |
| 1. Identity and private media | 5-11 | Web-only identity, Cookie sessions, private upload/owner-read, ownership, and deletion tests pass with real PostgreSQL/private storage integration; commercial final download remains closed. |
| 2. Commercial transaction core | 12-15 | Catalog, reservation primitives, pack, subscription, refund, dispute, debt, merge lineage, and entitlement invariants pass under concurrency and duplicate/out-of-order events; the atomic order switch waits for job schema in Task 16. |
| 3. Durable generation and delivery | 16-20 | Outbox, Worker lease, Provider reconciliation, strict QA/repair, watermark, variants, and failure settlement pass without inline execution. |
| 4. Web product and Partner Invite | 21-25 | Real user tasks, two-browser invite, accessibility, responsive layouts, typed contracts, and frontend tests pass. |
| 5. Release, migration, and acceptance | 26-30 | PR/Preview/Production contracts, private migration, linked live chains, observation, rollback, and later contract cleanup all pass. |

Execution stops at every package exit gate. A failure keeps downstream high-risk flags `OFF`; it is not converted into a warning or a green report.

## Pre-Task 0 External Risk Containment

These are production-owner actions, not repository evidence. Execute and capture timestamped project-setting/deployment evidence before coding; if access is unavailable, record NOT_RUN and treat the current production surface as still exposed.

- [ ] Disable Vercel Git automatic Production domain assignment, Production deploy hooks, and any Worker-platform automatic Production release. Keep Preview builds only; do not Promote from the dashboard.
- [ ] Disable the current `production-smoke` because it anonymously creates a remote session and uploads a favicon instead of testing the commercial chain.
- [ ] Set the verified existing environment controls `REMOTE_JOIN_ENABLED=false`, `LIVE_PORTRAIT_ENABLED=false`, and `SUBSCRIPTION_BILLING_ENABLED=false`. Temporarily block new anonymous upload/Gatekeeper, order creation/generation, credit-pack checkout, Partner/remote-session creation, recommendations, and leads at the platform edge without blocking signed payment webhooks, reconciliation, logout, or incident evidence access. Record exact path rules and their test responses.
- [ ] Pause the current destructive cleanup cron and any user-delete path that can clear database URLs after storage deletion failure. Preserve references for inventory/retry and return an explicit unavailable response rather than false deletion success. Restore deletion only after Task 11's state machine passes.
- [ ] Create a separate Private Blob store, a least-privilege production read-only database role, and a disposable `vowpic_restore_*` rehearsal database. Do not modify/delete objects in the existing public store.
- [ ] Capture current Production deployment ID, source SHA if available, Worker image/deployment identity if available, Alembic revision, sanitized environment snapshot hash, active queue depth, and cleanup state as incident evidence. This snapshot is not a safe rollback target.
- [ ] After Tasks 1-4, deploy one reviewed safe-baseline bundle with high-risk capabilities OFF. From that point, forbid rollback to any deployment that lacks the kill-switch and reference-preserving safety behavior.

## File Responsibility Map

### Backend foundations

- `backend/app/core/config.py`: static bootstrap defaults and exact security limits; no business state.
- `backend/app/core/feature_flags.py`: flag enums, request context, and FastAPI dependencies.
- `backend/app/core/session_auth.py`: Cookie parsing, local JWT verification, session lookup, CSRF/Origin enforcement.
- `backend/app/services/feature_flag_service.py`: PostgreSQL authority, Redis cache, audit, cohort resolution.
- `backend/app/services/production_inventory_service.py`: read-only legacy counts and conflict reports.
- `backend/app/services/storage.py`: low-level private object-key adapter only; no ownership decisions.
- `backend/app/services/media_asset_service.py`: upload intents, media ownership, grants, reads, and deletion state.
- `backend/app/services/external_fetch_service.py`: Admin-only SSRF-safe HTTPS fetch.

### Data models and migrations

- `backend/alembic/versions/20260710_0013_ops_feature_flags.py`: authoritative flags, flag audit, release activation records.
- `backend/alembic/versions/20260710_0014_web_identity_sessions.py`: identities, sessions, OAuth intents, controlled merges, account tombstones.
- `backend/alembic/versions/20260710_0015_private_media_assets.py`: upload batches, media assets, asset grants, deletion fields and RLS.
- `backend/alembic/versions/20260710_0016_commercial_ledger.py`: billing catalog, grant lots, reservations, allocations, entitlements, reconciliation cases, API idempotency, and the generic outbox.
- `backend/alembic/versions/20260710_0017_creem_payment_facts.py`: captured/refund/dispute event facts and reconciliation constraints.
- `backend/alembic/versions/20260710_0018_subscription_facts.py`: normalized subscription invoices, transactions, cancel intents, grants, uniqueness and RLS.
- `backend/alembic/versions/20260710_0019_generation_jobs.py`: jobs, attempts, leases, verdicts and generation payload versions; it consumes the generic outbox from `0016`.
- `backend/alembic/versions/20260710_0020_partner_consent.py`: typed Partner Invite and consent cases, expanding the legacy remote-join table without rewriting migration `20260510_0009`.
- `backend/alembic/versions/20260710_0021_contract_cleanup.py`: release 7b destructive cleanup only.

### Commercial and generation services

- `backend/app/services/billing_catalog_service.py`: versioned pack/subscription catalog and Creem product validation.
- `backend/app/services/credit_reservation_service.py`: grant-lot allocation, reserve/capture/release/refund/debt.
- `backend/app/services/order_transaction_service.py`: idempotent order + reservation + job + outbox transaction.
- `backend/app/services/payment_service.py`: Creem request/event facts and credit-pack handlers.
- `backend/app/services/subscription_service.py`: paid transaction grants, paid-through, cancellation, invoice refund/dispute.
- `backend/app/services/outbox_service.py`: durable publish and deterministic ARQ wake-up.
- `backend/app/services/generation_job_service.py`: claim, heartbeat, fencing, retry, reconciliation and settlement.
- `backend/app/services/evolink_service.py`: Provider submission/query adapter; no accounting or public URLs.
- `backend/app/services/qa_verdict_service.py`: strict immutable semantic/technical verdicts.
- `backend/app/services/delivery_asset_service.py`: master/variant/watermark artifacts and entitlements.

### Web product and release

- `frontend/src/services/*.ts`: typed auth, media, order, billing, download, and invite transports.
- `frontend/src/composables/*.ts`: page-independent user-flow state.
- `frontend/src/components/create/*`, `frontend/src/components/preview/*`, `frontend/src/components/payment/*`: focused presentation components.
- `frontend/src/generated/api.d.ts`: generated OpenAPI types; never hand-edit.
- `frontend/tests/unit/*`: Vitest component/composable tests.
- `frontend/e2e/*`: Playwright Preview and Production user journeys.
- `release/*.json`: versioned gate, quality, impact, severity and activation contracts.
- `scripts/release/*.py` and `scripts/release/*.mjs`: inventory, migration, evidence, aggregate gate, staged deployment and acceptance runners.
- `.github/workflows/ci.yml`: secret-free PR aggregate only.
- `.github/workflows/integration.yml`: protected Preview integration.
- `.github/workflows/production-release.yml`: serialized staged Production, approval, Promote, observation and rollback.

---

## Work Package 0 — Stop Risk Growth

### Task 1: Ship A Fail-Closed Bootstrap Lockdown

**Files:**
- Create: `backend/app/core/feature_flags.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/integration/__init__.py`
- Create: `backend/tests/test_risk_lockdown.py`
- Create: `backend/tests/test_no_runtime_ddl.py`
- Modify: `backend/tests/test_commercial_policy.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/admin_audit_service.py`
- Modify: `backend/app/services/account_risk_service.py`
- Modify: `backend/app/services/email_service.py`
- Modify: `backend/app/services/credit_service.py`
- Modify: `backend/app/services/schema_guard_service.py`
- Modify: `backend/app/routers/auth/google.py`
- Modify: `backend/app/routers/upload.py`
- Modify: `backend/app/routers/gatekeeper.py`
- Modify: `backend/app/routers/orders.py`
- Modify: `backend/app/routers/payments.py`
- Modify: `backend/app/routers/subscriptions.py`
- Modify: `backend/app/routers/session.py`
- Modify: `backend/app/routers/live_portrait.py`
- Modify: `backend/app/routers/recommendations.py`
- Modify: `backend/app/routers/leads.py`
- Modify: `backend/app/routers/users.py`
- Modify: `backend/app/routers/credits.py`
- Modify: `backend/app/routers/admin.py`
- Modify: `backend/app/worker_tasks.py`
- Modify: `backend/app/services/retention_service.py`
- Modify: `backend/app/services/ops_config_service.py`
- Modify: `backend/app/routers/ops.py`
- Modify: `backend/data/ops_config.json`
- Modify: `frontend/src/stores/ops.ts`
- Modify: `vercel.json`
- Modify: `.env.example`
- Modify: `backend/.env.example`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: `Capability`, `bootstrap_capability_enabled(capability: Capability) -> bool`, and `require_bootstrap_capability(capability: Capability) -> None`.
- Produces: settings `google_auth_enabled`, `authenticated_upload_enabled`, `generation_enabled`, `credit_pack_checkout_enabled`, `subscription_billing_enabled`, `private_download_enabled`, and `partner_invite_enabled`; all default false.
- Consumed by: Task 2, which preserves the same `Capability` names while replacing static decisions with PostgreSQL-backed decisions.

- [ ] **Step 1: Write the failing lockdown tests**

Create one test-package bootstrap so every planned root-level command such as `python -m unittest backend.tests.test_risk_lockdown` imports the real `backend/app` package. The integration package marker is intentionally empty; its parent package performs the path setup.

Add `test_no_runtime_ddl.py` before implementation. It scans and instruments cold start plus auth/Admin/credit/signed-webhook requests and fails on `Base.metadata.create_all`, request-time `CREATE TABLE`, `ALTER TABLE`, `CREATE INDEX`, `ensure_credit_guardrails()`, `ensure_generation_refund_guardrails()`, or any swallowed schema error. It permits DDL only from Alembic migration files/commands. This is a Task 1 safe-baseline property, not a Task 7 cleanup.

```python
# backend/tests/__init__.py
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
```

```python
# backend/tests/test_risk_lockdown.py
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.core.config import Settings
from app.core.feature_flags import Capability, require_bootstrap_capability


ROOT = Path(__file__).resolve().parents[2]


class RiskLockdownTest(unittest.TestCase):
    def test_high_risk_capabilities_default_off(self) -> None:
        settings = Settings(_env_file=None)
        for field in (
            "google_auth_enabled",
            "authenticated_upload_enabled",
            "generation_enabled",
            "credit_pack_checkout_enabled",
            "subscription_billing_enabled",
            "private_download_enabled",
            "partner_invite_enabled",
        ):
            self.assertFalse(getattr(settings, field), field)

    def test_disabled_capability_returns_503(self) -> None:
        with patch("app.core.feature_flags.settings.generation_enabled", False):
            with self.assertRaises(HTTPException) as raised:
                require_bootstrap_capability(Capability.GENERATION)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "capability_disabled")

    def test_vercel_does_not_force_unsafe_flags_on(self) -> None:
        payload = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        env = payload.get("env", {})
        self.assertEqual(env.get("REMOTE_JOIN_ENABLED"), "false")
        self.assertEqual(env.get("GENERATION_ENABLED"), "false")
        self.assertNotEqual(env.get("QA_REQUIRE_IDENTITY_EMBEDDING"), "false")
```

- [ ] **Step 2: Run the tests and confirm the unsafe current defaults fail**

Run from repository root:

```powershell
python -m unittest backend.tests.test_risk_lockdown backend.tests.test_no_runtime_ddl -v
```

Expected: FAIL because `feature_flags.py` and the new settings do not exist, `REMOTE_JOIN_ENABLED` is currently true, and Vercel currently disables identity embedding.

- [ ] **Step 3: Add the minimal bootstrap capability contract**

```python
# backend/app/core/feature_flags.py
from enum import StrEnum

from fastapi import HTTPException

from app.core.config import get_settings


settings = get_settings()


class Capability(StrEnum):
    GOOGLE_AUTH = "google_auth"
    AUTHENTICATED_UPLOAD = "authenticated_upload"
    GENERATION = "generation"
    CREDIT_PACK_CHECKOUT = "credit_pack_checkout"
    SUBSCRIPTION_BILLING = "subscription_billing"
    PRIVATE_DOWNLOAD = "private_download"
    PARTNER_INVITE = "partner_invite"


_SETTING_BY_CAPABILITY = {
    Capability.GOOGLE_AUTH: "google_auth_enabled",
    Capability.AUTHENTICATED_UPLOAD: "authenticated_upload_enabled",
    Capability.GENERATION: "generation_enabled",
    Capability.CREDIT_PACK_CHECKOUT: "credit_pack_checkout_enabled",
    Capability.SUBSCRIPTION_BILLING: "subscription_billing_enabled",
    Capability.PRIVATE_DOWNLOAD: "private_download_enabled",
    Capability.PARTNER_INVITE: "partner_invite_enabled",
}


def bootstrap_capability_enabled(capability: Capability) -> bool:
    return bool(getattr(settings, _SETTING_BY_CAPABILITY[capability], False))


def require_bootstrap_capability(capability: Capability) -> None:
    if bootstrap_capability_enabled(capability):
        return
    raise HTTPException(
        status_code=503,
        detail={"code": "capability_disabled", "capability": capability.value},
    )
```

Add the seven Boolean fields to `Settings` with false defaults. Invoke the matching guard before every side effect in Google exchange, upload/Gatekeeper, order creation, checkout, subscription, Partner Invite/remote join, Worker Provider submission, Admin generation/probe/regenerate, and user download/serialization paths. A disabled order response must omit preview/final URLs rather than returning the existing public values. Frontend config failure resolves every high-risk surface to hidden/OFF; it never falls back to `remote_join=true` or another enabled state. `ops_config.json` is not an authority for a security decision.

Live Portrait, local recommendations, and leads/contact are retired—not hidden capabilities—so Task 1 does not invent enum values or borrow another capability. Every public create/list/detail/read/mutation endpoint for those routers returns 410 before query, serialization, or side effect; no Live Portrait URL is returned. Task 5 completes their UI/product cleanup, but the Tasks 1-4 safe baseline already proves the permanent 410 contract.

Retire the legacy credit mutation/fallback routes immediately: authenticated `POST /credits/deduct`, service-token `POST /credits/add`, and the old direct `POST /credits/purchase` return 410 before any lock, ledger, balance, Provider, or outbox side effect; no route may synthesize `GENERATION_DEBIT` outside a reservation capture. Until Task 12 installs the authoritative PostgreSQL catalog, `GET /credits/packages` returns structured 503 rather than the static package list. Read-only authenticated balance/transaction history remains available. Route tests snapshot ledger/balance/purchase/order/job/outbox counts and prove all retired calls leave them unchanged. Existing Admin credit and generation actions are guarded OFF before their first side effect; Tasks 13 and 16 either route them through the canonical services or retire them permanently.

Set the corresponding `vercel.json` environment values to `"false"`; set `QA_REQUIRE_VISION` and `QA_FAIL_ON_VISION_ERROR` to `"true"`; remove the current `QA_REQUIRE_IDENTITY_EMBEDDING=false` override. Do not enable ARQ in Vercel yet—Generation remains blocked until Task 17 deploys the Worker.

Remove every request/startup schema writer from `main.py`, Admin audit, account risk, email, credit, and schema-guard services. `schema_guard_service.py` becomes read-only revision/table/index validation; missing schema fails readiness with a traceable error. Delete `ensure_credit_guardrails()` and `ensure_generation_refund_guardrails()` plus swallowed-error fallbacks. Task 2 migration `0013` creates any currently missing baseline tables/indexes with stable names; later migrations reuse/validate them rather than creating duplicates. After this point Alembic is the only schema writer.

As an immediate reference-preservation patch, storage deletion failure must leave every URL/object reference and `deleted_at` unchanged and return an explicit retryable failure. Cleanup is POST/service-auth only; remove mutating GET behavior. Keep scheduled cleanup paused until Task 11 introduces leased retry state and confirms real storage absence.

- [ ] **Step 4: Verify every blocked route fails before creating side effects**

```powershell
python -m unittest backend.tests.test_risk_lockdown backend.tests.test_no_runtime_ddl backend.tests.test_remote_join_config backend.tests.test_runtime_config backend.tests.test_commercial_policy -v
$unsafeHits = rg -n -S 'REMOTE_JOIN_ENABLED.*true|GENERATION_ENABLED.*true|QA_REQUIRE_IDENTITY_EMBEDDING.*false' vercel.json .env.example backend/app
if ($LASTEXITCODE -eq 0) { $unsafeHits; throw 'unsafe production-enabling configuration remains' }
if ($LASTEXITCODE -gt 1) { throw 'unsafe configuration scan failed' }
```

Expected: all unit tests PASS; the search returns no active production-enabling configuration.

- [ ] **Step 5: Record and commit the safety baseline**

Append the exact commands/results and the fact that no external deployment has occurred to `docs/ai-worklog.md`, then run:

```powershell
git diff --check
git add backend/app/core/config.py backend/app/core/feature_flags.py backend/app/main.py backend/app/services/admin_audit_service.py backend/app/services/account_risk_service.py backend/app/services/email_service.py backend/app/services/credit_service.py backend/app/services/schema_guard_service.py backend/app/routers/auth/google.py backend/app/routers/upload.py backend/app/routers/gatekeeper.py backend/app/routers/orders.py backend/app/routers/payments.py backend/app/routers/subscriptions.py backend/app/routers/session.py backend/app/routers/live_portrait.py backend/app/routers/recommendations.py backend/app/routers/leads.py backend/app/routers/users.py backend/app/routers/credits.py backend/app/routers/admin.py backend/app/worker_tasks.py backend/app/services/retention_service.py backend/app/services/ops_config_service.py backend/app/routers/ops.py backend/data/ops_config.json frontend/src/stores/ops.ts backend/tests/__init__.py backend/tests/integration/__init__.py backend/tests/test_risk_lockdown.py backend/tests/test_no_runtime_ddl.py backend/tests/test_commercial_policy.py vercel.json .env.example backend/.env.example docs/ai-worklog.md
git commit -m "fix: fail closed high-risk product capabilities"
```

### Task 2: Replace Static Switches With Audited Server-Side Flags

**Files:**
- Create: `backend/app/models/ops_feature_flag.py`
- Create: `backend/app/models/ops_feature_flag_audit.py`
- Create: `backend/app/models/release_activation.py`
- Create: `backend/app/models/acceptance_identity_binding.py`
- Create: `backend/app/models/data_migration_run.py`
- Create: `backend/app/models/data_migration_checkpoint.py`
- Create: `backend/app/models/release_observation.py`
- Create: `backend/app/services/feature_flag_service.py`
- Create: `backend/app/services/acceptance_identity_service.py`
- Create: `backend/app/routers/ops_admin.py`
- Create: `scripts/release/provision_acceptance_identity.py`
- Create: `scripts/release/build_runtime_bundle_id.py`
- Create: `release/safe-baseline-contract.json`
- Create: `backend/alembic/versions/20260710_0013_ops_feature_flags.py`
- Create: `backend/tests/test_feature_flags.py`
- Create: `backend/tests/test_feature_flag_route_guards.py`
- Create: `backend/tests/test_acceptance_identity_binding.py`
- Create: `backend/tests/test_data_migration_checkpoint_schema.py`
- Create: `backend/tests/test_release_observation_schema.py`
- Create: `backend/tests/test_runtime_bundle_id.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/routers/__init__.py`
- Modify: `backend/app/core/feature_flags.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/redis_client.py`
- Modify: `.env.example`
- Modify: `backend/.env.example`
- Modify: `vercel.json`
- Modify: `backend/app/routers/auth/google.py`
- Modify: `backend/app/routers/upload.py`
- Modify: `backend/app/routers/gatekeeper.py`
- Modify: `backend/app/routers/orders.py`
- Modify: `backend/app/routers/payments.py`
- Modify: `backend/app/routers/subscriptions.py`
- Modify: `backend/app/routers/session.py`
- Modify: `backend/app/routers/live_portrait.py`
- Modify: `backend/app/routers/recommendations.py`
- Modify: `backend/app/routers/leads.py`
- Modify: `backend/app/routers/users.py`
- Modify: `backend/app/worker_tasks.py`
- Modify: `backend/app/routers/ops.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: `FeatureFlagState`, `FeatureFlagContext`, `FeatureFlagDecision`, `AcceptanceIdentityBinding`, service-only `DataMigrationRun`/`DataMigrationCheckpoint`, durable `ReleaseObservationRun`/samples, `resolve_capability()`, `require_request_capability()`, `require_worker_capability()`, `set_capability_state()`, and `emergency_disable()`.
- Consumes: Task 1 `Capability` names and bootstrap defaults.
- Replaces: every Task 1 route/Worker call to `require_bootstrap_capability`; static false defaults must not remain stacked in front of the PostgreSQL decision.
- Produces for Tasks 26-29: immutable `ReleaseActivation` rows and current/target snapshot hashes.

- [ ] **Step 1: Write model, resolution, cache, cohort, and audit tests**

```python
# backend/tests/test_feature_flags.py
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.feature_flags import Capability, FeatureFlagContext, FeatureFlagState
from app.services.feature_flag_service import decide_flag


class FeatureFlagDecisionTest(unittest.TestCase):
    def test_missing_flag_fails_closed(self) -> None:
        decision = decide_flag(
            Capability.GENERATION, None, FeatureFlagContext(environment="production")
        )
        self.assertEqual(decision.state, FeatureFlagState.OFF)
        self.assertFalse(decision.allowed)

    def test_acceptance_cohort_requires_user_deployment_and_unexpired_binding(self) -> None:
        user_id = uuid4()
        context = FeatureFlagContext(
            environment="production",
            deployment_id="dpl_target",
            runtime_bundle_id="rtb_target",
            user_id=user_id,
            now=datetime.now(timezone.utc),
        )
        row = {
            "state": "ACCEPTANCE_COHORT",
            "deployment_id": "dpl_target",
            "runtime_bundle_id": "rtb_target",
            "cohort_user_ids": [str(user_id)],
            "expires_at": context.now + timedelta(minutes=30),
        }
        self.assertTrue(decide_flag(Capability.GENERATION, row, context).allowed)
        self.assertFalse(decide_flag(
            Capability.GENERATION, row,
            context.__class__(environment="production", deployment_id="dpl_other", runtime_bundle_id="rtb_target", user_id=user_id, now=context.now),
        ).allowed)
        self.assertFalse(decide_flag(
            Capability.GENERATION, row,
            context.__class__(environment="production", deployment_id="dpl_target", runtime_bundle_id="rtb_other", user_id=user_id, now=context.now),
        ).allowed)

    def test_cohort_ttl_above_86400_is_rejected(self) -> None:
        from app.services.feature_flag_service import validate_cohort_expiry
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            validate_cohort_expiry(now, now + timedelta(seconds=86401))
```

Add async tests proving: PostgreSQL read wins over Redis; Redis cache TTL is at most 30; a storage/cache error resolves OFF; writes create an audit row; emergency OFF propagates by deleting the cache key; unknown capability names are rejected. Route tests prove every Task 1 call site now uses the PostgreSQL service, DB failure blocks all, frontend/env/JSON cannot enable a capability, cohort without a verified user or preapproved verified-identity binding fails closed, and an ON flag permits only the exact active target deployment/bundle while the private-compatible baseline, old deployment URL, wrong bundle, or wrong Worker digest resolves OFF. Worker tests require server-stamped job user/API deployment/bundle plus running OCI digest; spoofed payload fields fail. Rollback tests require audited OFF propagation before `vercel rollback <recorded-baseline-id>` and reject a second Promote of an already promoted deployment. Signed webhooks/reconciliation/logout/reference-preserving deletion are never blocked, and no `require_bootstrap_capability` call remains outside its retired compatibility test. Binding tests prove provider/subject HMAC/deployment/environment/expiry are all required, raw subject/email are never stored, replay consumes once, spoofed headers cannot match, and two concurrent first exchanges create one local user/identity. Activation tests fix the discriminated kinds `SAFE_BASELINE_INSTALL | COMMERCIAL_7A | CONTRACT_7B`, monotonic CAS phases, completed-row immutability, and uniqueness per environment/kind/runtime bundle. The same workflow/bundle attempt may only recover an incomplete row; a different bundle creates a new row and cannot overwrite an active observation.

`test_runtime_bundle_id.py` fixes a discriminated, canonical formula. Role `SAFE_BASELINE` uses only the reviewed Tasks 1-4 source SHA, role, schema `0013`, the exact `20260710_0013` migration checksum, `release/safe-baseline-contract.json`, and pinned builder/tool-contract version. That contract contains the bootstrap capability names, all-OFF seed contract, and builder schema only; SAFE_BASELINE explicitly rejects Worker/job payload/provider/model/catalog/full gate/activation-plan inputs because those artifacts do not exist yet. Roles `COMMERCIAL_7A` and `CONTRACT_7B` require their release-specific deployment-independent contracts and a digest-pinned Worker. Every role forbids deployment ID, API prebuilt checksum, resolved OFF/target snapshots, final manifest SHA, evidence, and live state. Identical canonical inputs reproduce one ID; any allowed input drift changes it.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
python -m unittest backend.tests.test_feature_flags -v
```

Expected: FAIL because the models, migration, and service do not exist.

- [ ] **Step 3: Add migration and typed flag interfaces**

```python
# backend/app/core/feature_flags.py (public interface)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID


class FeatureFlagState(StrEnum):
    OFF = "OFF"
    ACCEPTANCE_COHORT = "ACCEPTANCE_COHORT"
    ON = "ON"


@dataclass(frozen=True)
class FeatureFlagContext:
    environment: str
    deployment_id: str | None = None
    runtime_bundle_id: str | None = None
    worker_image_digest: str | None = None
    user_id: UUID | None = None
    verified_identity_hash: str | None = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class FeatureFlagDecision:
    capability: Capability
    state: FeatureFlagState
    allowed: bool
    snapshot_hash: str
    reason: str
```

Migration `20260710_0013` has exact `down_revision = "20260516_0012"`, creates `ops_feature_flags`, `ops_feature_flag_audits`, `release_activations`, service-only `acceptance_identity_bindings`, service-only `data_migration_runs`/`data_migration_checkpoints`, and service-only `release_observation_runs`/append-only samples, seeds every high-risk capability OFF for `preview` and `production`, and enables RLS so only service/admin roles can mutate. It also absorbs the exact missing baseline tables/indexes formerly created at runtime by Admin audit, account risk, email, credit/refund guardrails, using stable names that later migrations validate/reuse; migration tests reject duplicate index/table definitions. Use a unique `(environment, capability)` constraint, enum/check `SAFE_BASELINE_INSTALL | COMMERCIAL_7A | CONTRACT_7B`, unique `(environment,kind,runtime_bundle_id)`, a separate one-time Production uniqueness guard for `SAFE_BASELINE_INSTALL`, monotonic phase/version CAS, and immutable audits/completed activations. Release activation rows bind final source SHA, pre-deploy `runtime_bundle_id`, final manifest SHA-256, baseline/target/Worker IDs and roles, private evidence prefix, workflow run, phase/version, and approval so a fresh job can resolve coordinates without inheriting a shell or mutable alias; secret values are never stored. A binding stores provider, keyed subject HMAC, environment, target deployment, expiry no later than 86400 seconds, actor/audit reason, consumed local user ID/time, and unique `(environment,deployment_id,provider,subject_hmac)`; raw provider subject or email is forbidden. Migration-run rows form one parent release lease plus per-script/per-mode child runs storing inventory/manifest/script hashes, approval, lease owner/fencing token, heartbeat, state, and sanitized counts; checkpoint rows are append-only and uniquely bind child run/script/mode/batch boundary. Observation runs bind manifest/runtime/deployment/Worker/snapshot hashes, `OBSERVING | FINALIZING | PASSED | FAILED`, start/deadline, cleanup-cycle proof, CAS version, and finalizer; signed samples are append-only with unique time buckets. These tables are control-plane facts for Tasks 28-30, not a runtime-DDL escape hatch.

Because COMMERCIAL_7A/CONTRACT_7B reserve before the Worker digest/runtime ID exists, `release_activations` also has a partial unique active `(environment, kind, source_sha)` constraint. The runtime ID is filled exactly once by CAS and then the non-null runtime-bundle uniqueness applies; a rerun resolves that same reservation rather than creating an attempt-derived release row.

- [ ] **Step 4: Implement PostgreSQL authority and FastAPI dependency**

```python
# backend/app/services/feature_flag_service.py
async def resolve_capability(
    db: AsyncSession,
    capability: Capability,
    context: FeatureFlagContext,
) -> FeatureFlagDecision:
    # Enabled/cohort decisions always require a live authoritative PostgreSQL read.
    row = await _load_authoritative_row(db, context.environment, capability)
    decision = decide_flag(capability, row, context)
    await _cache_off_decision(decision, ttl_seconds=30)
    return decision


async def require_request_capability(
    request: Request,
    db: AsyncSession,
    capability: Capability,
    *,
    verified_user_id: UUID | None = None,
) -> FeatureFlagDecision:
    context = FeatureFlagContext(
        environment=settings.runtime_environment,
        deployment_id=settings.deployment_id,
        runtime_bundle_id=settings.runtime_bundle_id,
        user_id=verified_user_id,
    )
    decision = await resolve_capability(db, capability, context)
    if not decision.allowed:
        raise HTTPException(503, detail={"code": "capability_disabled", "capability": capability.value})
    return decision


async def require_worker_capability(
    db: AsyncSession,
    capability: Capability,
    *,
    deployment_id: str,
    runtime_bundle_id: str,
    worker_image_digest: str,
    user_id: UUID | None,
) -> FeatureFlagDecision:
    decision = await resolve_capability(
        db, capability,
        FeatureFlagContext(
            environment=settings.runtime_environment,
            deployment_id=deployment_id,
            runtime_bundle_id=runtime_bundle_id,
            worker_image_digest=worker_image_digest,
            user_id=user_id,
        ),
    )
    if not decision.allowed:
        raise CapabilityDisabled(capability.value)
    return decision
```

Add `runtime_environment` (strict `development | preview | production`), platform-trusted `deployment_id`, immutable pre-deploy `runtime_bundle_id`, optional Worker image digest, and `ACCEPTANCE_IDENTITY_HMAC_KEY` settings; missing/invalid production values fail startup readiness and flag evaluation OFF. On Vercel, `deployment_id` is read only from the System Environment Variable `VERCEL_DEPLOYMENT_ID`, never a project/user environment override, and workflow inspect/runtime reports must match it. `runtime_bundle_id` is deterministically computed before the Vercel build from final source/config/schema/Worker-digest/flag-contract/activation-plan inputs and explicitly excludes deployment IDs, resolved snapshots, and the later final manifest hash. Every non-OFF flag row is bound to the exact active runtime bundle ID and API target deployment; the audited activation row separately stores the final manifest SHA-256 after deploy. `ACCEPTANCE_COHORT` additionally requires an allowed canonical user or verified-identity binding; `ON` still fails closed for the private-compatible baseline, an old/unbound deployment URL, wrong runtime bundle, or wrong Worker digest. A Worker decision verifies the locked job's canonical user plus stamped API deployment/runtime bundle and the running OCI digest against the manifest; a caller cannot supply these stamps. Emergency rollback first writes/audits OFF and waits propagation, then uses the verified rollback command for the baseline—never the reverse. Before Task 7 there is no trusted HTTP user, so ordinary request guards pass `verified_user_id=None` and cohort decisions fail closed. Task 7 passes either the verified Cookie/Supabase-mapped user after authentication or, only inside the OAuth exchange after full Supabase verification, the keyed provider-subject hash. `acceptance_identity_service.py` locks and consumes a matching unexpired binding in the same transaction that creates the local user/identity; no other route can supply that context. `provision_acceptance_identity.py` is protected-Admin tooling that takes exact Supabase subjects from a secret file, stores only HMACs, binds them to one deployment/environment/expiry, and records actor/reason. Only OFF decisions may be served from the at-most-30-second Redis cache; `ON` and `ACCEPTANCE_COHORT` always require a live PostgreSQL authority read, so a PostgreSQL outage cannot leave a cached high-risk capability enabled. Production mutation accepts OFF only until Task 26/29 gates exist. The sole earlier exception is a protected Preview workflow after Tasks 6-7 tests/migrations PASS: it may set only `GOOGLE_AUTH` to a deployment/runtime-bundle-bound `ACCEPTANCE_COHORT` with precreated bindings and expiry at most two hours, run the browser smoke, then restore OFF and prove the original snapshot hash. Admin mutation endpoints require Google-backed database Admin identity, record actor/reason/target manifest/runtime bundle, reject cohort TTL over 86400, and return old/new snapshot hashes. `/ops/public_config` exposes only sanitized state; it never exposes cohort user IDs or identity hashes.

- [ ] **Step 5: Verify migration, cache failure, audit, and route enforcement**

```powershell
python -m unittest backend.tests.test_feature_flags backend.tests.test_feature_flag_route_guards backend.tests.test_acceptance_identity_binding backend.tests.test_data_migration_checkpoint_schema backend.tests.test_release_observation_schema backend.tests.test_runtime_bundle_id backend.tests.test_security_hardening -v
docker compose up -d postgres redis
$env:DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/ai_wedding'
Push-Location backend
alembic upgrade head
Pop-Location
```

Expected: tests PASS; Alembic reports head `20260710_0013`; seeded production flags are OFF.

- [ ] **Step 6: Record and commit authoritative flags**

```powershell
git diff --check
git add backend/app/core/feature_flags.py backend/app/core/config.py backend/app/models/ops_feature_flag.py backend/app/models/ops_feature_flag_audit.py backend/app/models/release_activation.py backend/app/models/acceptance_identity_binding.py backend/app/models/data_migration_run.py backend/app/models/data_migration_checkpoint.py backend/app/models/release_observation.py backend/app/models/__init__.py backend/app/services/feature_flag_service.py backend/app/services/acceptance_identity_service.py backend/app/core/redis_client.py backend/app/routers/auth/google.py backend/app/routers/upload.py backend/app/routers/gatekeeper.py backend/app/routers/orders.py backend/app/routers/payments.py backend/app/routers/subscriptions.py backend/app/routers/session.py backend/app/routers/live_portrait.py backend/app/routers/recommendations.py backend/app/routers/leads.py backend/app/routers/users.py backend/app/worker_tasks.py backend/app/routers/ops.py backend/app/routers/ops_admin.py backend/app/routers/__init__.py scripts/release/provision_acceptance_identity.py scripts/release/build_runtime_bundle_id.py release/safe-baseline-contract.json backend/alembic/versions/20260710_0013_ops_feature_flags.py backend/tests/test_feature_flags.py backend/tests/test_feature_flag_route_guards.py backend/tests/test_acceptance_identity_binding.py backend/tests/test_data_migration_checkpoint_schema.py backend/tests/test_release_observation_schema.py backend/tests/test_runtime_bundle_id.py .env.example backend/.env.example vercel.json docs/ai-worklog.md
git commit -m "feat: add audited server-side release flags"
```

### Task 3: Produce Read-Only Legacy Inventory And Restore Evidence

**Files:**
- Create: `backend/app/services/production_inventory_service.py`
- Create: `scripts/release/inventory_production.py`
- Create: `backend/scripts/backup_restore_rehearsal.py`
- Create: `backend/tests/test_production_inventory.py`
- Create: `backend/tests/test_backup_restore_rehearsal.py`
- Create: `docs/operations/production-inventory-schema.md`
- Modify: `.gitignore`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: `build_inventory_report(db: AsyncSession, identifier_hmac_key: bytes) -> ProductionInventoryReport`.
- Produces: `artifacts/security-baseline/<run-id>/inventory-summary.json` and `manifest.sha256`; raw emails, image URLs, tokens, and object keys are excluded.
- Consumed by: Tasks 8, 11, 28, and 30 for migration batch sizes and conflict gates.

- [ ] **Step 1: Write report-shape and read-only-query tests**

```python
# backend/tests/test_production_inventory.py
import unittest
from app.services.production_inventory_service import ProductionInventoryReport


class ProductionInventoryTest(unittest.TestCase):
    def test_report_contains_required_conflict_counts_without_sensitive_rows(self) -> None:
        report = ProductionInventoryReport(
            schema_revision="20260516_0012",
            users={"total": 3, "guest": 1, "duplicate_email_groups": 1, "asset_owners_unknown": 0},
            ledger={"balance_mismatch_users": 0, "legacy_unlinked_debits": 0},
            orders={"active": 0, "legacy_unverified": 1},
            objects={"public_user_assets": 2, "shared_public_assets": 4, "unknown_role": 0},
        )
        payload = report.model_dump(mode="json")
        self.assertNotIn("email", payload)
        self.assertNotIn("url", payload)
        self.assertNotIn("object_key", payload)
```

Add a runtime assertion that the transaction is read-only and that the supplied database role cannot write. Tests also reject raw email, OpenID, URL, object key, and token output; identifiers needed for conflict grouping are HMACed.

- [ ] **Step 2: Run the focused tests and verify failure**

```powershell
python -m unittest backend.tests.test_production_inventory -v
```

Expected: FAIL because the inventory service and report schema do not exist.

- [ ] **Step 3: Implement the read-only aggregate report**

```python
# backend/app/services/production_inventory_service.py
class ProductionInventoryReport(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_revision: str
    users: dict[str, int]
    ledger: dict[str, int]
    orders: dict[str, int]
    objects: dict[str, int]


async def build_inventory_report(db: AsyncSession, identifier_hmac_key: bytes) -> ProductionInventoryReport:
    await db.execute(text("SET TRANSACTION READ ONLY"))
    revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
    return ProductionInventoryReport(
        schema_revision=str(revision or "unknown"),
        users=await _user_counts(db),
        ledger=await _ledger_counts(db),
        orders=await _order_counts(db),
        objects=await _object_reference_counts(db),
    )
```

Aggregate guest/password/`wx_*`/visitor/missing-subject/duplicate-email and duplicate-subject counts; assets/entitlements by legacy account; ledger/materialized balance mismatch; active/legacy-unverified orders; every URL-bearing JSON field; public/private/shared/unknown object role; expired sources still readable. Hash identifiers only when a stable group reference is needed.

- [ ] **Step 4: Implement backup/restore rehearsal with an isolated target**

`backup_restore_rehearsal.py` must require a read-only source, an encrypted/network-isolated disposable target, and a separate target-admin connection. It rejects the same database identity, rejects a target database name without the `vowpic_restore_` prefix, and rejects a long-lived/shared target credential. It runs `pg_dump --format=custom --no-owner --no-acl`, restores with `pg_restore --clean --if-exists --no-owner --no-acl --exit-on-error`, and compares revision, tables, exact row counts, FK orphans, ledger/balance, and URL-inventory checksum. Credentials are never printed. In one mandatory `finally` path it terminates target connections, drops the rehearsal database, revokes/drops the temporary target role or proves provider TTL destruction, and deletes the archive. Cleanup runs after success, restore failure, and comparison failure; any database/credential cleanup failure makes the gate FAIL and alerts. Only sanitized checksums/counts survive as evidence; neither the dump nor a restored Production-data database is a normal artifact.

```powershell
python -m unittest backend.tests.test_backup_restore_rehearsal -v
```

Expected: unit tests PASS for source/target alias rejection, target-prefix enforcement, redacted commands, mismatch failure, and archive/database/credential cleanup after success, restore failure, and comparison failure. A forced cleanup failure exits nonzero.

Run against local containers first:

```powershell
docker compose up -d postgres
# These credentials are local-only fixtures. The source role is deliberately unable to write.
docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS vowpic_restore_local WITH (FORCE)"
docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "DROP ROLE IF EXISTS vowpic_restore_local_role"
docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE vowpic_restore_local_role LOGIN PASSWORD 'restore_local_only'"
docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE vowpic_restore_local OWNER vowpic_restore_local_role"
$roleExists = docker compose exec -T postgres psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='vowpic_inventory_local'"
if ($roleExists.Trim() -ne '1') {
    docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE vowpic_inventory_local LOGIN PASSWORD 'inventory_local_only'"
}
docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "ALTER ROLE vowpic_inventory_local LOGIN PASSWORD 'inventory_local_only'"
docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "ALTER ROLE vowpic_inventory_local SET default_transaction_read_only = on"
docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "ALTER ROLE vowpic_inventory_local BYPASSRLS"
docker compose exec -T postgres psql -U postgres -d ai_wedding -v ON_ERROR_STOP=1 -c "GRANT CONNECT ON DATABASE ai_wedding TO vowpic_inventory_local"
docker compose exec -T postgres psql -U postgres -d ai_wedding -v ON_ERROR_STOP=1 -c "GRANT USAGE ON SCHEMA public TO vowpic_inventory_local"
docker compose exec -T postgres psql -U postgres -d ai_wedding -v ON_ERROR_STOP=1 -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO vowpic_inventory_local"
docker compose exec -T postgres psql -U postgres -d ai_wedding -v ON_ERROR_STOP=1 -c "GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO vowpic_inventory_local"
$env:PRODUCTION_READ_ONLY_DATABASE_URL='postgresql://vowpic_inventory_local:inventory_local_only@localhost:5432/ai_wedding'
$env:RESTORE_REHEARSAL_DATABASE_URL='postgresql://vowpic_restore_local_role:restore_local_only@localhost:5432/vowpic_restore_local'
$env:RESTORE_REHEARSAL_ADMIN_DATABASE_URL='postgresql://postgres:postgres@localhost:5432/postgres'
$env:RESTORE_REHEARSAL_ROLE_NAME='vowpic_restore_local_role'
python backend/scripts/backup_restore_rehearsal.py --source-url-env PRODUCTION_READ_ONLY_DATABASE_URL --target-url-env RESTORE_REHEARSAL_DATABASE_URL --target-admin-url-env RESTORE_REHEARSAL_ADMIN_DATABASE_URL --target-role-name-env RESTORE_REHEARSAL_ROLE_NAME --expected-target-db-prefix vowpic_restore_ --artifact-dir "artifacts/security-baseline/$env:GITHUB_RUN_ID/restore"
```

Expected: exit 0 with sanitized dump checksum, restored Alembic revision, row-count report, no source mutation, and proof that neither `vowpic_restore_local` nor `vowpic_restore_local_role` exists after the report is written. The rehearsal must also attempt a harmless write in a rolled-back source transaction and fail with read-only SQLSTATE `25006`; a source superuser or write-capable role is a hard failure. Production likewise uses a temporary target role; the admin role is used only for terminate/drop/revoke cleanup and is never the restore connection.

- [ ] **Step 5: Commit reviewed inventory/restore tooling before any Production credential is exposed**

Commit code and report schema, but do not commit generated inventory/dumps:

```powershell
git diff --check
git add backend/app/services/production_inventory_service.py backend/scripts/backup_restore_rehearsal.py backend/tests/test_production_inventory.py backend/tests/test_backup_restore_rehearsal.py scripts/release/inventory_production.py docs/operations/production-inventory-schema.md .gitignore docs/ai-worklog.md
git commit -m "feat: add read-only migration inventory evidence"
```

- [ ] **Step 6: Run the committed inventory tool against Production only after protected read-only credentials are supplied**

```powershell
python scripts/release/inventory_production.py --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --hmac-key-env INVENTORY_HMAC_KEY --output "artifacts/security-baseline/$env:GITHUB_RUN_ID/inventory-summary.json"
```

Expected: exit 0 and a redacted report. Missing read-only credentials is `NOT_RUN`, never a zero-filled report.
The protected workflow verifies its checked-out SHA equals the tooling commit before reading the credential. Actual sanitized counts/results are attached to immutable evidence and recorded in a later evidence-only worklog commit; the inventory code is never changed during that run.

### Task 4: Remove Unsafe Automatic Production Deployment And Establish A Truthful Baseline Gate

**Files:**
- Create: `backend/tests/test_ci_release_contract.py`
- Create: `scripts/release/verify_baseline.ps1`
- Create: `scripts/release/verify_safe_baseline.py`
- Create: `scripts/release/register_safe_baseline.py`
- Create: `docs/operations/risk-lockdown-runbook.md`
- Create: `.github/workflows/safe-baseline-release.yml`
- Modify: `backend/tests/test_no_runtime_ddl.py`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: `npm run typecheck`, `npm run test:unit` (initially a no-tests-is-failure command), and `scripts/release/verify_baseline.ps1`.
- Produces: PR aggregate job `quality-gate`; it has no Preview/Production secrets and performs no deployment.
- Produces: one manual, protected, one-purpose `safe-baseline-release.yml` that deploys Tasks 1-4 with every high-risk capability OFF; it is not triggered by push or reusable as a general release bypass.
- Produces: one unique `SAFE_BASELINE_INSTALL` activation reservation/completion; after success the workflow can never build/deploy another HEAD.
- Consumed by: Task 26, which expands this baseline instead of replacing its fail-closed behavior.

- [ ] **Step 1: Write static workflow and package pin tests**

```python
# backend/tests/test_ci_release_contract.py
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class CiReleaseContractTest(unittest.TestCase):
    def test_pr_workflow_contains_no_production_deploy(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("vercel deploy --prebuilt --prod", workflow)
        self.assertNotIn("relying on Vercel Git integration", workflow)
        self.assertIn("quality-gate", workflow)

    def test_frontend_core_tool_versions_are_exact(self) -> None:
        package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
        expected = {
            "vue": "3.4.21",
            "typescript": "5.3.3",
            "vite": "5.2.8",
            "vue-tsc": "1.8.27",
        }
        merged = {**package["dependencies"], **package["devDependencies"]}
        for name, version in expected.items():
            self.assertEqual(merged[name], version)
```

- [ ] **Step 2: Run tests and baseline commands to capture the current red state**

```powershell
python -m unittest backend.tests.test_ci_release_contract -v
Push-Location frontend
npm ls typescript vue-tsc vite vue --depth=0
npx vue-tsc --noEmit
Pop-Location
```

Expected: contract test FAIL because CI deploys automatically and versions are ranges; typecheck reproduces the recorded incompatibility before pins are changed.

- [ ] **Step 3: Pin the existing frontend toolchain and add non-empty scripts**

Use exact package versions; do not upgrade Vue/Uni-app/Vite majors in this task:

```json
{
  "scripts": {
    "typecheck": "vue-tsc --noEmit",
    "test:unit": "vitest run --passWithNoTests=false",
    "build:web": "uni build -p h5"
  },
  "dependencies": {
    "vue": "3.4.21"
  },
  "devDependencies": {
    "typescript": "5.3.3",
    "vite": "5.2.8",
    "vue-tsc": "1.8.27"
  }
}
```

Run `npm install --package-lock-only` so the lockfile matches the exact declarations. Task 22 adds Vitest and the first real test before `test:unit` becomes a mandatory CI command; until then the baseline script must report `frontend_unit=NOT_RUN`, not PASS.

- [ ] **Step 4: Reduce `ci.yml` to a secret-free truthful PR gate**

Keep checkout, Python 3.11 install, all backend unittest, Node 20 `npm ci`, typecheck, and Web build. Remove the `deploy` and `production-smoke` jobs entirely. Add a final `quality-gate` job with `if: always()` that exits nonzero unless every required upstream result equals `success`; zero collected backend tests and a skipped typecheck are failures.

`risk-lockdown-runbook.md` must state the exact external check: disable Vercel Git Production auto-assignment and deploy hooks, retain Preview builds only, capture the project-setting evidence, and verify the production domain points at the last known deployment only until the protected safe-baseline workflow below succeeds. After that promotion, the domain must remain on the safe baseline (or a later compatible bundle), never on the pre-kill-switch deployment.

- [ ] **Step 5: Implement the one-time protected safe-baseline release**

`safe-baseline-release.yml` is `workflow_dispatch` only, uses GitHub's protected Production Environment and `concurrency.cancel-in-progress: false`, and accepts one `source_sha` that must equal the reviewed Tasks 1-4 commit and current approved main head. A read-only preflight first checks Alembic revision and, when `0013` already exists, the unique activation row; a completed install or different-run reservation rejects before dump/restore, migration, deployment-secret resolution, or build. Only the first schema-`0012` install (or the same run-ID/SHA retry) performs the Task 3 read-only inventory/restore rehearsal. Under one PostgreSQL advisory lock, one explicit transaction, and the same Alembic connection, it runs exactly upgrade `0013` and inserts `ReleaseActivation(kind=SAFE_BASELINE_INSTALL, phase=RESERVED)` bound to source SHA, workflow run/attempt, approver, and expiry; PostgreSQL transactional DDL means both commit or both roll back. Seeing schema `0013` with no reservation is `ORPHANED_SCHEMA`: the workflow does not auto-adopt it or deploy and requires audited manual forward disposition. Seeing `0012` with a reservation is likewise invalid. Crash-boundary tests inject failure before/inside/after migration and prove no deploy secret is resolved unless the revision/row pair is consistent.

Before resolving a Vercel deployment secret or building, it computes role `SAFE_BASELINE` runtime bundle ID from only the Task 2 contract. It injects that ID into the exact deployment runtime via pinned `vercel deploy --env`, then reads it back; it does not claim the runner variable changed the prebuilt output. `register_safe_baseline.py` advances CAS phases `RESERVED -> STAGED -> PROMOTED -> FORMAL_VERIFIED -> COMPLETED`, persisting runtime ID, build checksum, exact deployment ID/URL, formal-domain observation, run/attempt, and evidence hashes after each external side effect. A rerun never rebuilds after STAGED: it resolves/inspects/reuses the recorded ID. If a crash occurs between deploy and STAGED CAS, recovery enumerates exact source/runtime/build matches and proceeds only when exactly one unbound candidate exists; ambiguity preserves edge deny and requires manual forward disposition. If Promote succeeded before its CAS, recovery compares the formal domain to the exact staged ID and advances rather than Promoting again. After one completed install every later invocation—including a later main HEAD—fails before build/deploy. `verify_safe_baseline.py` snapshots relevant table counts, requires 503 `capability_disabled` on every guarded high-risk route and 410 on every permanently retired Live Portrait/recommendations/leads/legacy-credit route, then proves counts and public-object references are unchanged. It also proves a deliberately invalid signed-webhook request reaches signature validation (400/401, never capability 503), logout is not feature-blocked, cleanup remains paused, and runtime flags read OFF from PostgreSQL. A database audit/statement recorder around cold start, auth, Admin, credit, webhook, logout, reconciliation, and readiness must record zero runtime DDL; the read-only schema guard reports expected revision/tables/indexes only. Any `CREATE/ALTER/DROP` outside the committed Alembic invocation is a hard failure. Static/workflow tests cover every crash boundary and later-HEAD rejection; emergency recovery may only run audited flags-OFF-first `vercel rollback <recorded-deployment-id>` followed by rollback status/formal verification, never rebuild or second-Promote through this workflow.

- [ ] **Step 6: Run the baseline gate locally**

```powershell
Push-Location frontend
npm ci
npm run typecheck
npm run build:web
Pop-Location
Push-Location backend
python -m unittest discover -s tests -v
Pop-Location
powershell -ExecutionPolicy Bypass -File scripts/release/verify_baseline.ps1
```

Expected: backend suite has zero error/failure, typecheck and build exit 0, and the baseline report explicitly records `frontend_unit=NOT_RUN` until Task 22. If the pre-existing backend error remains, fix its verified dependency/configuration cause in this task without weakening the test.

- [ ] **Step 7: Commit the no-auto-production and manual safety-release baseline**

```powershell
git diff --check
git add backend/tests/test_ci_release_contract.py backend/tests/test_no_runtime_ddl.py scripts/release/verify_baseline.ps1 scripts/release/verify_safe_baseline.py scripts/release/register_safe_baseline.py docs/operations/risk-lockdown-runbook.md frontend/package.json frontend/package-lock.json .github/workflows/ci.yml .github/workflows/safe-baseline-release.yml docs/ai-worklog.md
git commit -m "ci: stop unsafe automatic production releases"
```

- [ ] **Step 8: Execute the committed safe-baseline workflow and verify the formal domain**

The protected workflow runs the following commands from the committed Tasks 1-4 SHA only after the unique install reservation succeeds; no uncommitted script or workflow may be executed. Staged verification uses the deployment-protection bypass secret and proves application-level 503. After Promote, temporary project-edge deny rules remain in place while an allowlisted protected runner bypasses them to verify the same application response. The workflow then removes one route-group deny rule at a time and immediately rechecks 503/no-side-effect without bypass; any mismatch atomically restores that rule. Signed webhook/reconciliation/logout rules were never denied. Evidence records `edge_deny`, `edge_bypass_app_503`, and final `app_flag_503` separately so an edge block cannot masquerade as the kill switch. On success it CASes the reservation to completed with the immutable deployment ID; if this final CAS fails, edge deny remains and no other run is allowed to guess state.

```powershell
npx --yes vercel@55.0.0 pull --yes --environment=production --token=$env:VERCEL_TOKEN
$safeRuntimeBundleId = python scripts/release/build_runtime_bundle_id.py --release-role SAFE_BASELINE --source-sha $env:GITHUB_SHA --schema 20260710_0013 --safe-baseline-contract release/safe-baseline-contract.json --ops-migration backend/alembic/versions/20260710_0013_ops_feature_flags.py --builder-contract-version safe-baseline.v1 --output $env:RUNNER_TEMP/safe-runtime-bundle-id.txt
$env:RUNTIME_BUNDLE_ID = $safeRuntimeBundleId.Trim()
npx --yes vercel@55.0.0 build --prod --token=$env:VERCEL_TOKEN
$safeBaselineUrl = npx --yes vercel@55.0.0 deploy --prebuilt --prod --skip-domain --env RUNTIME_BUNDLE_ID=$env:RUNTIME_BUNDLE_ID --token=$env:VERCEL_TOKEN
python scripts/release/register_safe_baseline.py --phase STAGED --runtime-bundle-id $env:RUNTIME_BUNDLE_ID --deployment-url $safeBaselineUrl --source-sha $env:GITHUB_SHA --build-output .vercel/output --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env SAFE_BASELINE_APPROVAL_ID
python scripts/release/verify_safe_baseline.py --base-url $safeBaselineUrl --deployment-bypass-header-env VERCEL_AUTOMATION_BYPASS_HEADER --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --expected-source-sha $env:GITHUB_SHA --expected-runtime-bundle-id $env:RUNTIME_BUNDLE_ID --expected-schema 20260710_0013 --require-platform-deployment-id --expected-layer app --output artifacts/security-baseline/safe-baseline-staged.json
npx --yes vercel@55.0.0 promote $safeBaselineUrl --yes --token=$env:VERCEL_TOKEN
npx --yes vercel@55.0.0 promote status --token=$env:VERCEL_TOKEN
python scripts/release/register_safe_baseline.py --phase PROMOTED --deployment-url $safeBaselineUrl --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env SAFE_BASELINE_APPROVAL_ID
python scripts/release/verify_safe_baseline.py --base-url $env:PRODUCTION_BASE_URL --edge-bypass-header-env EDGE_RULE_BYPASS_HEADER --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --expected-source-sha $env:GITHUB_SHA --expected-runtime-bundle-id $env:RUNTIME_BUNDLE_ID --expected-schema 20260710_0013 --expected-deployment-url $safeBaselineUrl --require-platform-deployment-id --expected-layer app --output artifacts/security-baseline/safe-baseline-formal.json
python scripts/release/register_safe_baseline.py --phase FORMAL_VERIFIED --deployment-url $safeBaselineUrl --formal-report artifacts/security-baseline/safe-baseline-formal.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env SAFE_BASELINE_APPROVAL_ID
python scripts/release/register_safe_baseline.py --phase COMPLETED --deployment-url $safeBaselineUrl --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env SAFE_BASELINE_APPROVAL_ID
```

Expected: staged, edge-bypass, and each post-handoff formal-domain report PASS; deployment IDs/SHA match, high-risk rows/counts do not grow, signed webhook/reconciliation/logout paths remain reachable, runtime-DDL count is exactly zero across cold start and every probe, all capability rows are OFF, and temporary edge rules are removed only after their application guard is proven. Any staged failure prevents Promote. Any formal-domain mismatch restores/keeps the corresponding edge deny and requires a forward safe-baseline fix; the workflow must not restore the unsafe old deployment.

**Work Package 0 exit:** Re-run Tasks 1-4 verification, produce inventory and restore artifacts, verify Vercel production auto-assignment is disabled, and require the protected `safe-baseline-release.yml` staged/formal reports to PASS for the exact Tasks 1-4 SHA/deployment/schema. Do not claim risk containment is active until that fail-closed baseline is actually current on the formal domain and each public high-risk route returns 503 without side effects.

## Work Package 1 — Web Identity And Private Media

### Task 5: Remove WeChat, Mini Program, Guest, And Public Password Runtime Paths

**Files:**
- Create: `backend/tests/test_web_only_contract.py`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/src/manifest.json`
- Modify: `frontend/src/utils/auth/index.ts`
- Modify: `frontend/src/utils/auth/session.ts`
- Modify: `frontend/src/utils/auth/identity.ts`
- Modify: `frontend/src/utils/auth/_keys.ts`
- Modify: `frontend/src/utils/auth/storage.ts`
- Modify: `frontend/src/utils/supabase.ts`
- Modify: `frontend/src/pages/auth/login.vue`
- Modify: `frontend/src/pages/auth/register.vue`
- Modify: `frontend/src/pages/create/index.vue`
- Modify: `frontend/src/pages/preview/preview.vue`
- Modify: `frontend/src/pages/account/index.vue`
- Modify: `frontend/src/pages.json`
- Modify: `frontend/src/components/PaymentModal.vue`
- Modify: `backend/app/routers/auth/__init__.py`
- Modify: `backend/app/routers/auth/guest.py`
- Delete: `backend/app/routers/auth/merge.py`
- Modify: `backend/app/routers/auth/google.py`
- Modify: `backend/app/routers/auth/_helpers.py`
- Modify: `backend/app/routers/auth/_shared.py`
- Modify: `backend/app/routers/users.py`
- Modify: `backend/app/routers/live_portrait.py`
- Modify: `backend/app/routers/recommendations.py`
- Modify: `backend/app/routers/leads.py`
- Modify: `backend/app/schemas/auth.py`
- Modify: `backend/app/schemas/user.py`
- Modify: `backend/app/models/order.py`
- Modify: `backend/tests/test_supabase_auth.py`
- Modify: `README.md`
- Modify: `docs/PRD.md`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: retired endpoints return HTTP 410 with code `auth_method_retired`.
- Removes: `dev:mp-weixin`, `build:mp-weixin`, `@dcloudio/uni-mp-weixin`, QR-code package use, `MP-WEIXIN`, `provider: 'weixin'`, public `openid`, guest ownership headers, password CTAs, and active Live Portrait/local-recommendation/leads promises.
- Preserves temporarily: `users.openid` as a database-only legacy alias until Task 30.

- [ ] **Step 1: Write a Web-only contract test**

```python
# backend/tests/test_web_only_contract.py
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class WebOnlyContractTest(unittest.TestCase):
    def test_frontend_has_no_mini_program_build_or_dependency(self) -> None:
        package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
        self.assertNotIn("dev:mp-weixin", package["scripts"])
        self.assertNotIn("build:mp-weixin", package["scripts"])
        self.assertNotIn("@dcloudio/uni-mp-weixin", package["dependencies"])
        manifest = json.loads((ROOT / "frontend/src/manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("mp-weixin", manifest)

    def test_public_auth_schema_has_no_openid_or_guest_merge(self) -> None:
        schema = (ROOT / "backend/app/schemas/auth.py").read_text(encoding="utf-8")
        self.assertNotIn("previous_guest_id", schema)
        self.assertNotIn("openid:", schema)
```

Add explicit tests calling `/auth/login`, public password, every Live Portrait endpoint (create, list, detail), recommendation, and leads endpoints and asserting 410 with no User/business row created and no URL field serialized. Update `test_supabase_auth.py` in this task so public `UserRead`/auth schemas explicitly exclude `openid/auth_provider/auth_subject`; any still-required legacy alias assertion is database-internal only and cannot authorize or serialize a user. Tests also require no `previous_guest_id`, guest/OpenID merge helper, guest storage key, Guest Account copy, or active `pages/join/landing` route; the landing source may remain unregistered until Task 24 rewrites/re-adds it. Read-only legacy inventory/migration remains service-authenticated outside the public router; the entire Live Portrait product router is 410-only until Task 30 removes it.

- [ ] **Step 2: Run the contract and verify current WeChat/guest paths fail it**

```powershell
python -m unittest backend.tests.test_web_only_contract -v
```

Expected: FAIL on Mini Program scripts/dependency/config and public OpenID/guest schema.

- [ ] **Step 3: Remove active Web-incompatible code without dropping legacy columns**

Keep `backend/app/routers/auth/guest.py` as a 410-only compatibility endpoint during the rollback window:

```python
@router.post("/login")
async def retired_guest_login() -> None:
    raise HTTPException(
        status_code=410,
        detail={"code": "auth_method_retired", "method": "guest"},
    )
```

Remove guest bootstrap calls, guest/token storage keys, `previous_guest_id`, `_merge_guest_account`, `X-User-OpenID`/`X-Visitor-Id` ownership headers, and Continue-as-guest copy from frontend auth/order/account surfaces. Remove the anonymous join route from `pages.json` until Task 24 installs the authenticated Partner flow. Remove Mini Program and QR packages/config. Remove `openid` from public Pydantic/TypeScript responses; retain internal UUID `user_id`; `_helpers.py`/`_shared.py` retain only non-legacy helpers needed by Task 7. Remove Live Portrait, local recommendation, leads/contact form, and unverified subscription-perk CTAs from the active H5 surface. Live Portrait create/list/detail plus all other retired public routes return 410 before query/serialization/mutation and cannot be re-enabled by frontend/config fallback; only the later service-authenticated inventory/backfill reads historical rows.

Do not globally delete the words “guest” or “WeChat”: content-policy detection of payment QR/WeChat Pay scams and legacy migration documentation remain valid non-product evidence.

- [ ] **Step 4: Verify active paths and lockfile**

```powershell
Push-Location frontend
npm install --package-lock-only
npm ci
npm run typecheck
npm run build:web
Pop-Location
python -m unittest backend.tests.test_web_only_contract backend.tests.test_supabase_auth -v
$legacyProductHits = rg -n -S 'MP-WEIXIN|mp-weixin|provider: .weixin.|previous_guest_id|X-User-OpenID' frontend/src frontend/package.json backend/app/routers backend/app/schemas
if ($LASTEXITCODE -eq 0) { $legacyProductHits; throw 'active WeChat or guest product contract remains' }
if ($LASTEXITCODE -gt 1) { throw 'Web-only contract scan failed' }
```

Expected: build/tests PASS; the active-root search returns no hits. The 410 compatibility response uses only neutral `auth_method_retired` wording, and historical/migration evidence is outside this active scan and covered separately by tests.

- [ ] **Step 5: Record and commit Web-only cleanup**

```powershell
git diff --check
git add frontend/package.json frontend/package-lock.json frontend/src/manifest.json frontend/src/utils/auth/index.ts frontend/src/utils/auth/session.ts frontend/src/utils/auth/identity.ts frontend/src/utils/auth/_keys.ts frontend/src/utils/auth/storage.ts frontend/src/utils/supabase.ts frontend/src/pages/auth/login.vue frontend/src/pages/auth/register.vue frontend/src/pages/create/index.vue frontend/src/pages/preview/preview.vue frontend/src/pages/account/index.vue frontend/src/pages.json frontend/src/components/PaymentModal.vue backend/app/routers/auth/__init__.py backend/app/routers/auth/guest.py backend/app/routers/auth/merge.py backend/app/routers/auth/google.py backend/app/routers/auth/_helpers.py backend/app/routers/auth/_shared.py backend/app/routers/users.py backend/app/routers/live_portrait.py backend/app/routers/recommendations.py backend/app/routers/leads.py backend/app/schemas/auth.py backend/app/schemas/user.py backend/app/models/order.py backend/tests/test_web_only_contract.py backend/tests/test_supabase_auth.py README.md docs/PRD.md docs/ai-worklog.md
git commit -m "refactor: enforce Web-only product identity"
```

### Task 6: Add Identity, OAuth Intent, Session, Merge, And Tombstone Schema

**Files:**
- Create: `backend/app/models/user_identity.py`
- Create: `backend/app/models/oauth_login_intent.py`
- Create: `backend/app/models/auth_session.py`
- Create: `backend/app/models/auth_refresh_token.py`
- Create: `backend/app/models/user_account_merge.py`
- Create: `backend/app/models/account_claim_proof.py`
- Create: `backend/app/models/identity_email_conflict.py`
- Create: `backend/app/models/account_tombstone.py`
- Create: `backend/alembic/versions/20260710_0014_web_identity_sessions.py`
- Create: `backend/tests/test_identity_session_schema.py`
- Create: `backend/tests/integration/test_identity_rls.py`
- Modify: `backend/app/models/user.py`
- Modify: `backend/app/models/credit_transaction.py`
- Modify: `backend/app/models/credit_purchase.py`
- Modify: `backend/app/models/order.py`
- Modify: `backend/app/models/subscription_credit_grant.py`
- Modify: `backend/app/models/user_subscription.py`
- Modify: `backend/app/models/user_credit.py`
- Modify: `backend/app/models/live_portrait_job.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: unique `UserIdentity(provider, subject)`, single-use `OAuthLoginIntent`, revocable `AuthSession`, hash-only rotating `AuthRefreshToken` generations, service-verified `AccountClaimProof`, explicit `IdentityEmailConflict`, immutable `UserAccountMerge`, and minimal `AccountTombstone`.
- Consumed by: Tasks 7-8 and every authenticated task after them.

- [ ] **Step 1: Write schema and migration tests**

```python
# backend/tests/test_identity_session_schema.py
import unittest
from pathlib import Path

from app.models.auth_refresh_token import AuthRefreshToken
from app.models.user_account_merge import UserAccountMerge
from app.models.user_identity import UserIdentity


class IdentitySessionSchemaTest(unittest.TestCase):
    def test_identity_subject_and_legacy_merge_are_unique(self) -> None:
        identity_names = {item.name for item in UserIdentity.__table__.constraints}
        merge_names = {item.name for item in UserAccountMerge.__table__.constraints}
        self.assertIn("uq_user_identities_provider_subject", identity_names)
        self.assertIn("uq_user_account_merges_legacy_user", merge_names)

    def test_refresh_tokens_are_hash_only(self) -> None:
        columns = set(AuthRefreshToken.__table__.columns.keys())
        self.assertIn("token_hash", columns)
        self.assertNotIn("refresh_token", columns)

    def test_migration_follows_current_head(self) -> None:
        path = Path(__file__).resolve().parents[1] / "alembic/versions/20260710_0014_web_identity_sessions.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn('down_revision = "20260710_0013"', text)
```

Add PostgreSQL integration tests for duplicate subject, duplicate legacy merge, merge self-reference, merge cycles, email duplicates, tombstone FK restrictions, session-family uniqueness, and one-time claim-proof consumption bound to exact legacy/canonical IDs. Prove a user delete cannot cascade-delete any credit transaction, purchase, order, subscription grant/projection, user-credit fact, or retained Live Portrait audit row. Every identity/session/refresh/OAuth-intent/claim-proof/merge/email-conflict/tombstone table is service-only under PostgreSQL RLS; ordinary authenticated roles cannot directly SELECT or mutate it. `test_identity_rls.py` connects as a real authenticated PostgreSQL role and proves own-row access, cross-user denial, direct `user_identities` SELECT denial, malicious `search_path` resistance, and the separately authorized service-role path.

- [ ] **Step 2: Run the tests and confirm missing schema failure**

```powershell
python -m unittest backend.tests.test_identity_session_schema -v
```

Expected: FAIL because the new models and migration do not exist.

- [ ] **Step 3: Implement normalized model contracts**

```python
# backend/app/models/auth_session.py
class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_family", "family_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`AuthRefreshToken` stores `session_id`, monotonically increasing `generation`, `token_hash`, `ACTIVE | USED | REVOKED`, expiry, used/revoked timestamps, and optional replacement token ID. Unique `(session_id,generation)` plus retained USED rows makes old-token reuse detectable; overwriting one hash in place is forbidden.

`AccountClaimProof` stores canonical/legacy IDs, `VERIFIED_PAYMENT | VERIFIED_SUPPORT_CASE`, a hash of the external reference, verifier service/Admin identity, verification timestamp, expiry, consumed timestamp, and audit request ID. Raw client strings never become proof facts; only server-side payment-event validation or an audited database-Admin action backed by a monitored support case may create one.

`IdentityEmailConflict` stores the canonical and legacy user IDs, an HMAC of the normalized conflicting email, `OPEN | RESOLVED_MERGED | RESOLVED_DISTINCT`, discovery source/time, resolution time, and resolution audit ID. Login/claim discovery creates it idempotently; email equality never merges accounts. Resolution requires the Task 8 proof path and remains queryable after resolution.

`OAuthLoginIntent` stores a 128-bit app-intent token hash, browser binding hash, redirect path, 10-minute expiry, and consumed timestamp. It does not store or pretend to validate Supabase's internal OAuth state, Google nonce, or PKCE verifier. `UserIdentity` stores provider=`supabase`, subject, user ID, verified email snapshot, and timestamps. `users.email` becomes non-unique profile data; `users.openid/unionid/username/password/auth_provider/auth_subject` remain nullable legacy fields until Task 30. Migration `0014` changes every existing financial/order/subscription/retained-job user FK represented by the model files in this task from destructive CASCADE to RESTRICT or tombstone-safe behavior. It also replaces `app_current_user_id()` and every affected RLS policy with a 7a dual lookup: prefer `(provider,subject)` in `user_identities`, fall back to legacy `users.auth_provider/auth_subject` only when no normalized row exists. The resolver is a narrowly scoped `SECURITY DEFINER` SQL function owned by a non-login owner, uses `SET search_path = pg_catalog, public`, validates the required JWT provider/subject claim shapes, queries only the indexed identity/legacy keys with no dynamic SQL, has `REVOKE ALL FROM PUBLIC`, and grants EXECUTE only to the authenticated role; that role receives no direct table SELECT. The fallback is read-compatible only and is measured for zero use before Task 30; new writes never populate legacy identity fields. Task 30 replaces it with an identity-only function with the same hardening before dropping legacy fields.

- [ ] **Step 4: Upgrade a fresh PostgreSQL database and prove constraints**

```powershell
docker compose up -d postgres
$env:DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/ai_wedding'
Push-Location backend
alembic upgrade head
alembic current
Pop-Location
python -m unittest backend.tests.test_identity_session_schema backend.tests.integration.test_identity_rls -v
```

Expected: current revision `20260710_0014`; all unit/integration assertions PASS.

- [ ] **Step 5: Commit the additive identity schema**

```powershell
git diff --check
git add backend/app/models/user_identity.py backend/app/models/oauth_login_intent.py backend/app/models/auth_session.py backend/app/models/auth_refresh_token.py backend/app/models/user_account_merge.py backend/app/models/account_claim_proof.py backend/app/models/identity_email_conflict.py backend/app/models/account_tombstone.py backend/app/models/user.py backend/app/models/credit_transaction.py backend/app/models/credit_purchase.py backend/app/models/order.py backend/app/models/subscription_credit_grant.py backend/app/models/user_subscription.py backend/app/models/user_credit.py backend/app/models/live_portrait_job.py backend/app/models/__init__.py backend/alembic/versions/20260710_0014_web_identity_sessions.py backend/tests/test_identity_session_schema.py backend/tests/integration/test_identity_rls.py docs/ai-worklog.md
git commit -m "feat: add Web identity and revocable session schema"
```

### Task 7: Implement Broker-Verified Google PKCE Exchange And Cookie-Only Local Sessions

**Files:**
- Create: `backend/app/core/session_auth.py`
- Create: `backend/app/core/security_headers.py`
- Create: `backend/app/services/auth_session_service.py`
- Create: `backend/app/services/oauth_intent_service.py`
- Create: `backend/app/routers/auth/session.py`
- Create: `backend/tests/test_cookie_sessions.py`
- Create: `backend/tests/test_web_security_baseline.py`
- Modify: `backend/tests/test_security_hardening.py`
- Modify: `backend/tests/test_admin_management_routes.py`
- Modify: `backend/tests/test_email_service.py`
- Modify: `backend/tests/test_supabase_auth.py`
- Modify: `backend/tests/test_database_config.py`
- Create: `frontend/src/services/auth.ts`
- Create: `frontend/src/pages/auth/callback.vue`
- Modify: `backend/app/core/supabase_auth.py`
- Modify: `backend/app/core/user_auth.py`
- Modify: `backend/app/core/admin_auth.py`
- Modify: `backend/app/core/database.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/runtime_checks.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/auth/google.py`
- Modify: `backend/app/routers/auth/__init__.py`
- Modify: `backend/app/routers/admin.py`
- Modify: `backend/app/routers/upload.py`
- Modify: `backend/app/routers/gatekeeper.py`
- Modify: `backend/app/routers/orders.py`
- Modify: `backend/app/routers/payments.py`
- Modify: `backend/app/routers/subscriptions.py`
- Modify: `backend/app/services/schema_guard_service.py`
- Modify: `backend/app/services/admin_audit_service.py`
- Modify: `backend/app/services/account_risk_service.py`
- Modify: `backend/app/services/email_service.py`
- Modify: `backend/app/schemas/auth.py`
- Modify: `frontend/src/utils/supabase.ts`
- Modify: `frontend/src/utils/api.ts`
- Modify: `frontend/src/pages/auth/login.vue`
- Modify: `frontend/src/pages.json`
- Modify: `middleware.ts`
- Modify: `vercel.json`
- Modify: `.env.example`
- Modify: `backend/.env.example`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: `POST /api/v1/auth/oauth-intents`, `POST /api/v1/auth/supabase/session`, `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`, and `GET /api/v1/auth/me`.
- Produces: `get_session_user()` and `get_optional_session_user()`; later routers must use these instead of Bearer/OpenID fallbacks.
- Cookies: `vowpic_access` (HttpOnly/Secure/SameSite=Lax, Path `/api/v1`, Max-Age 900), `vowpic_refresh` (HttpOnly/Secure/SameSite=Strict, Path `/api/v1/auth/refresh`, Max-Age 2592000), and non-HttpOnly `vowpic_csrf` (Secure/SameSite=Lax, Path `/api/v1`) bound by hash to the session.
- Produces: verified TLS/CORS/security-header/Origin contract and separates browser Admin identity from service credentials.

- [ ] **Step 1: Write broker-claim, app-intent, Cookie, rotation, reuse, Origin, and CSRF tests**

```python
# backend/tests/test_cookie_sessions.py
import unittest
from datetime import timedelta

from app.services.auth_session_service import ACCESS_TTL, REFRESH_TTL


class CookieSessionContractTest(unittest.TestCase):
    def test_session_ttls_match_contract(self) -> None:
        self.assertEqual(ACCESS_TTL, timedelta(minutes=15))
        self.assertEqual(REFRESH_TTL, timedelta(days=30))

    def test_access_claims_require_revocation_fields(self) -> None:
        from app.core.session_auth import validate_access_claim_shape
        with self.assertRaises(ValueError):
            validate_access_claim_shape({"sub": "user-only"})
        claims = validate_access_claim_shape({
            "sub": "00000000-0000-0000-0000-000000000001",
            "sid": "00000000-0000-0000-0000-000000000002",
            "jti": "00000000-0000-0000-0000-000000000003",
            "token_version": 1,
            "iat": 1,
            "exp": 2,
        })
        self.assertEqual(claims.token_version, 1)
```

Add async route tests proving: wrong-browser/reused/expired app intent fails; missing or stale Supabase `session_id`/`iat` fails; provider other than Google fails; anonymous or unverified-email identity fails; access Cookie is HttpOnly/Secure; refresh rotates once while retaining the USED hash; reuse revokes the family; logout revokes; disabled user fails; missing/wrong Origin or CSRF fails; no response body contains bearer tokens. Rewrite `test_supabase_auth.py` around normalized `(provider, subject) -> UserIdentity -> canonical user` behavior and remove every import/assertion for `build_supabase_openid` or an OpenID-derived identity; do not keep a compatibility helper merely to make the test collect. Rewrite `test_database_config.py` so Production/Supabase connections require CA verification plus hostname checking (`CERT_REQUIRED`, never `CERT_NONE`); any local-test exception is explicit, non-production-only, and cannot be selected by a Production URL. Add a first-login cohort test: a fully verified Supabase subject with one unexpired deployment-bound `AcceptanceIdentityBinding` creates one local user/identity and consumes the binding in the same transaction; missing/wrong/replayed binding creates no user. Add security tests for verified database TLS, exact-origin CORS, CSP/HSTS/nosniff/referrer/permissions headers, no mutating GET, browser Admin rejecting `X-Admin-Token` and hardcoded email, and zero runtime DDL during auth/Admin/credit requests. Origin tests accept the exact formal origin and, only for a registered unexpired release activation, one exact staged origin derived from trusted Vercel system metadata; arbitrary `*.vercel.app`, wildcard/globstar redirects, wrong deployment/runtime role, caller `Host`/`Forwarded` spoofing, and formal-domain confusion all fail. The scan must cover `Base.metadata.create_all`, request-time `CREATE TABLE`, `ALTER TABLE`, `CREATE INDEX`, `ensure_credit_guardrails()`, and `ensure_generation_refund_guardrails()`. Do not add a fake “Google nonce verified” assertion when no Google ID token is available.

- [ ] **Step 2: Run focused tests and verify current bearer flow fails**

```powershell
python -m unittest backend.tests.test_cookie_sessions backend.tests.test_supabase_auth -v
```

Expected: FAIL because current exchange returns a 30-day Bearer token, auto-merges by email, and lacks session/CSRF checks.

- [ ] **Step 3: Implement strict Supabase claim and local session services**

```python
# backend/app/services/auth_session_service.py
ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=30)


async def issue_session(db: AsyncSession, user: User, response: Response) -> None:
    raw_refresh = secrets.token_urlsafe(48)
    raw_csrf = secrets.token_urlsafe(32)
    session = AuthSession(
        user_id=user.id,
        family_id=uuid.uuid4(),
        token_version=1,
        csrf_token_hash=sha256(raw_csrf.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + REFRESH_TTL,
    )
    db.add(session)
    await db.flush()
    db.add(AuthRefreshToken(
        session_id=session.id,
        generation=1,
        token_hash=sha256(raw_refresh.encode()).hexdigest(),
        status=RefreshTokenStatus.ACTIVE,
        expires_at=datetime.now(timezone.utc) + REFRESH_TTL,
    ))
    _set_session_cookies(response, session, raw_refresh, raw_csrf)
```

`verify_supabase_token()` must verify Supabase issuer, audience, signature, `exp/iat`, `session_id`, subject, `is_anonymous=false`, Google provider/AMR, and verified email. Supabase Auth is the broker responsible for Google state/nonce and PKCE verification. Only verify Google nonce independently when a signed Google ID token is actually returned; otherwise record `broker_verified` evidence and never claim double verification. Identity lookup is only `(provider, subject)`; same email never auto-merges. Every protected request decodes local JWT, validates `jti` shape/expiry, loads `AuthSession` by `sid`, compares `token_version/user`, and checks user status/role from the database; `jti` is a per-access-token audit identifier, not a mutable single-value session column. Replace Task 2's temporary `verified_user_id=None` calls on authenticated upload/Gatekeeper/order/payment/subscription routes with the exact `get_session_user().id`. Google exchange first verifies the Supabase identity and app intent, then either resolves an existing local identity or presents the keyed subject hash to `acceptance_identity_service.py`; an unexpired binding for the exact environment/deployment permits first local user creation in `ACCEPTANCE_COHORT` and is consumed atomically. `ON` may create a normal new user without a binding. No caller-controlled header/body value can become `verified_identity_hash`; add route tests proving spoofed values cannot enter a cohort.

`POST /auth/oauth-intents` is a rate-limited, browser-bound, no-user/no-business-row preflight. It returns 503 while GOOGLE_AUTH is OFF; while state is ACCEPTANCE_COHORT or ON it may create only the 10-minute intent before identity is known. Cohort membership is enforced at `/auth/supabase/session` after cryptographic identity verification, so an unbound Google subject gets a denial and creates no local user/session even if it obtained an intent.

- [ ] **Step 4: Implement frontend PKCE without persistent bearer storage**

```ts
// frontend/src/services/auth.ts
export async function startGoogleLogin(nextPath: string): Promise<void> {
  const intent = await post<OAuthIntent>('/auth/oauth-intents', { next_path: nextPath });
  sessionStorage.setItem('vowpic_oauth_intent', JSON.stringify(intent));
  await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: intent.callback_url },
  });
}

export async function completeGoogleCallback(code: string, intentToken: string): Promise<UserProfile> {
  const { data, error } = await supabase.auth.exchangeCodeForSession(code);
  if (error || !data.session?.access_token) throw new Error('oauth_code_exchange_failed');
  try {
    return await post<UserProfile>('/auth/supabase/session', {
      access_token: data.session.access_token,
      intent_token: intentToken,
    });
  } finally {
    await clearTransientSupabaseSession();
  }
}
```

The callback accepts only `code` and the app-intent token. In `frontend/src/pages/auth/callback.vue`, call `supabase.auth.exchangeCodeForSession(code)` in the same browser, send the returned short-lived Supabase access token plus app intent to the backend once, then clear the transient Supabase session. Reject an `access_token` URL fragment. Configure Supabase JS with `flowType:'pkce'`, `persistSession:false`, `autoRefreshToken:false`, `detectSessionInUrl:false`, and a `sessionStorage` verifier adapter. `api.ts` sends `credentials:'include'` and `X-CSRF-Token` for state-changing methods; it never sets Authorization from local storage.

- [ ] **Step 5: Enforce verified TLS, same-origin Web security, and Admin/service separation**

Use `ssl.CERT_REQUIRED`, `check_hostname=True`, and `sslmode=verify-full` semantics for PostgreSQL/Supabase; never translate `require/prefer` to `CERT_NONE`. Production CORS accepts only the exact Web origin with explicit methods/headers. Add HSTS, nosniff, `strict-origin-when-cross-origin`, minimal Permissions Policy, and this minimum CSP:

```text
default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none';
connect-src 'self' https://*.supabase.co; img-src 'self' data: blob:;
script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:;
upgrade-insecure-requests
```

Unsafe Cookie requests require exact Origin and CSRF/session hash. Production normally accepts only the exact formal Web origin. A temporary staged validation origin is accepted only when `VERCEL_URL` and `VERCEL_DEPLOYMENT_ID` come from platform system metadata and match an unexpired `ReleaseActivation` row for the same environment/runtime bundle, exact staged role, exact `https://<vercel-url>` origin, and audited expiry. Request `Host`, `Forwarded`, `X-Forwarded-*`, body/query values, suffix matching, wildcard domains, and project environment overrides cannot authorize it. GET/HEAD routes do not mutate. Browser Admin uses the local Cookie session plus database role; `X-Admin-Token` is isolated to a separate service dependency and cannot authenticate the Admin UI. Task 1/safe-baseline already removed `Base.metadata.create_all`, request/startup-time DDL, and every ensure-schema writer; Task 7 modifications adapt those services to UUID/session audit and assert the zero-DDL contract remains true without reintroducing a writer. Alembic migrations are the only schema writers. `schema_guard_service.py` remains a read-only revision/required-table readiness check until Task 30 deletes it. Missing schema fails readiness/request explicitly; no service catches the error and mutates schema. Edge/in-memory rate limiting is defense-in-depth, not the identity/upload quota authority.

Normalize user errors to `{code, message, request_id, retryable, field_errors}`. Exception handlers map known domain errors explicitly and log a redacted internal cause; they never return `str(exception)`, SQL, object keys, Provider payloads, stack traces, or filesystem paths. Add shape/redaction assertions to `test_web_security_baseline.py`.

- [ ] **Step 6: Verify the complete session and Web security lifecycle**

```powershell
python -m unittest backend.tests.test_cookie_sessions backend.tests.test_supabase_auth backend.tests.test_security_hardening backend.tests.test_web_security_baseline backend.tests.test_admin_management_routes backend.tests.test_email_service -v
Push-Location frontend
npm run typecheck
npm run build:web
Pop-Location
$bearerHits = rg -n -S 'localStorage.*token|Authorization.*Bearer|access_token.*setStorage|ai_wedding_token' frontend/src
if ($LASTEXITCODE -eq 0) { $bearerHits; throw 'persistent bearer-token path remains' }
if ($LASTEXITCODE -gt 1) { throw 'bearer-token scan failed' }
```

Expected: tests/build PASS; the search finds no persistent bearer-token path.

- [ ] **Step 7: Commit Cookie sessions and Web security baseline**

```powershell
git diff --check
git add backend/app/core/session_auth.py backend/app/core/security_headers.py backend/app/core/supabase_auth.py backend/app/core/user_auth.py backend/app/core/admin_auth.py backend/app/core/database.py backend/app/core/config.py backend/app/core/runtime_checks.py backend/app/main.py backend/app/services/auth_session_service.py backend/app/services/oauth_intent_service.py backend/app/services/schema_guard_service.py backend/app/services/admin_audit_service.py backend/app/services/account_risk_service.py backend/app/services/email_service.py backend/app/routers/auth/google.py backend/app/routers/auth/session.py backend/app/routers/auth/__init__.py backend/app/routers/admin.py backend/app/routers/upload.py backend/app/routers/gatekeeper.py backend/app/routers/orders.py backend/app/routers/payments.py backend/app/routers/subscriptions.py backend/app/schemas/auth.py backend/tests/test_cookie_sessions.py backend/tests/test_web_security_baseline.py backend/tests/test_security_hardening.py backend/tests/test_admin_management_routes.py backend/tests/test_email_service.py backend/tests/test_supabase_auth.py backend/tests/test_database_config.py frontend/src/services/auth.ts frontend/src/utils/supabase.ts frontend/src/utils/api.ts frontend/src/pages/auth/login.vue frontend/src/pages/auth/callback.vue frontend/src/pages.json middleware.ts vercel.json .env.example backend/.env.example docs/ai-worklog.md
git commit -m "feat: add revocable Google Cookie sessions"
```

### Task 8: Implement Controlled Legacy Proof, Empty-Account Merge, And Soft Account Closure

**Files:**
- Create: `backend/app/services/account_merge_service.py`
- Create: `backend/app/services/account_claim_proof_service.py`
- Create: `backend/app/services/account_closure_service.py`
- Create: `backend/app/routers/auth/account_claim.py`
- Create: `backend/tests/test_account_merge.py`
- Create: `backend/tests/test_account_closure.py`
- Modify: `backend/app/routers/auth/__init__.py`
- Modify: `backend/app/routers/users.py`
- Modify: `frontend/src/pages/account/index.vue`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: server-verified one-time proof facts, merge-graph locking, empty-account cleanup/merge, and a fail-closed `commercial_lineage_not_ready` result for any account with orders, credits, payments, subscriptions, or assets.
- Produces: `close_account(user_id) -> AccountTombstone`.
- Consumes: Task 3 inventory and Task 6 merge/session schema. Task 13 adds grant-lot/accounting lineage before assetful/financial merges may execute.

- [ ] **Step 1: Write proof, concurrency, lineage, and closure tests**

```python
# backend/tests/test_account_merge.py
class AccountMergePolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_jwt_or_email_alone_cannot_claim_assets(self) -> None:
        with self.assertRaises(AccountClaimError) as raised:
            await claim_legacy_account(
                self.db,
                canonical_user_id=self.canonical_id,
                legacy_user_id=self.legacy_id,
                verified_proof_id=None,
            )
        self.assertEqual(raised.exception.code, "ownership_proof_required")

    async def test_second_concurrent_merge_cannot_succeed(self) -> None:
        first, second = await asyncio.gather(self.claim(), self.claim(), return_exceptions=True)
        self.assertEqual(sum(not isinstance(item, Exception) for item in (first, second)), 1)
```

Add tests that arbitrary/nonexistent payment references and support-case strings create no proof; unsigned/unpaid/mismatched payment facts fail; only a database Admin can record a support proof and only when a monitored support channel is configured; proof canonical/legacy mismatch, expiry, or reuse fails. Also test no chain/cycle/self merge; any balance/order/payment/subscription/asset returns `commercial_lineage_not_ready` without rebinding or ledger writes; an empty legacy profile can merge once; sessions revoke on closure; financial rows remain; asset references are preserved and closure is marked `media_cleanup_pending` rather than cascade-disappearing before Task 11. Grant-lineage/refund transfer tests belong to Task 13 after the commercial schema exists.

- [ ] **Step 2: Run focused tests and verify current automatic guest merge fails**

```powershell
python -m unittest backend.tests.test_account_merge backend.tests.test_account_closure -v
```

Expected: FAIL because current `_merge_guest_account` rewrites ownership from a guest ID without sufficient proof and hard closure is not modeled.

- [ ] **Step 3: Implement the transactional proof and merge policy**

```python
async def claim_legacy_account(
    db: AsyncSession,
    *,
    canonical_user_id: UUID,
    legacy_user_id: UUID,
    verified_proof_id: UUID | None,
) -> UserAccountMerge:
    if canonical_user_id == legacy_user_id:
        raise AccountClaimError("self_merge_forbidden")
    if verified_proof_id is None:
        raise AccountClaimError("ownership_proof_required")
    proof = await lock_verified_claim_proof(
        db, proof_id=verified_proof_id,
        canonical_user_id=canonical_user_id, legacy_user_id=legacy_user_id,
    )
    await _lock_and_validate_merge_graph(db, canonical_user_id, legacy_user_id)
    footprint = await _load_legacy_footprint(db, legacy_user_id)
    if footprint.has_orders_or_assets_or_financial_history:
        raise AccountClaimError("commercial_lineage_not_ready")
    merge = await _create_unique_merge(db, canonical_user_id, legacy_user_id, proof.id)
    await _merge_empty_profile(db, merge)
    proof.consume(merge_id=merge.id, consumed_at=utcnow())
    return merge
```

`verify_payment_claim_reference()` must locate a provider-signed paid fact already associated with the legacy account and compare the submitted reference using normalized/hash form; a client cannot claim by merely naming a payment. `record_support_claim_proof()` is database-Admin-only, requires a configured monitored support channel and audit evidence hash, and is not exposed to the customer router. Do not attempt a balance transfer before Task 12 grant lots and Task 13 reversal/merge lineage exist. Do not rewrite CreditTransaction, PaymentEvent, purchase, risk, or audit owners. Account closure revokes identities/sessions immediately, writes a minimal tombstone, anonymizes deletable profile fields, and sets `media_cleanup_pending` while preserving every asset reference and restricted financial row. Task 11 is the first task allowed to enqueue/confirm storage deletion.

- [ ] **Step 4: Verify with real PostgreSQL constraints**

```powershell
python -m unittest backend.tests.test_account_merge backend.tests.test_account_closure backend.tests.test_credit_ledger -v
```

Expected: all PASS, including concurrent unique empty-account merge, financial/asset merge refusal, session revocation, and immutable-history assertions.

- [ ] **Step 5: Commit controlled claims and closure**

```powershell
git diff --check
git add backend/app/services/account_merge_service.py backend/app/services/account_claim_proof_service.py backend/app/services/account_closure_service.py backend/app/routers/auth/account_claim.py backend/app/routers/auth/__init__.py backend/app/routers/users.py backend/tests/test_account_merge.py backend/tests/test_account_closure.py frontend/src/pages/account/index.vue docs/ai-worklog.md
git commit -m "feat: control legacy claims and account closure"
```

### Task 9: Add Private Media, Upload Intent, Grant, And Deletion Schema

**Files:**
- Create: `backend/app/models/upload_batch.py`
- Create: `backend/app/models/media_asset.py`
- Create: `backend/app/models/asset_access_grant.py`
- Create: `backend/app/models/upload_quota_window.py`
- Create: `backend/app/models/upload_quota_state.py`
- Create: `backend/app/models/upload_quota_reservation.py`
- Create: `backend/alembic/versions/20260710_0015_private_media_assets.py`
- Create: `backend/tests/test_media_asset_schema.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/order.py`
- Modify: `backend/app/models/live_portrait_job.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: `MediaAssetRole`, `MediaAssetStatus`, `UploadBatch`, `MediaAsset`, `AssetAccessGrant`, `UploadQuotaWindow`, durable per-batch/part `UploadQuotaReservation`, and one locked `UploadQuotaState` per user.
- Canonical references after this task are `asset_id` and `object_key`; signed/provider URLs are never stored.
- Consumed by: Tasks 10-11, 13, 19-21, and 28.

- [ ] **Step 1: Write schema, transition, ownership, and RLS tests**

```python
# backend/tests/test_media_asset_schema.py
import unittest
from app.models.media_asset import MediaAssetRole, MediaAssetStatus


class MediaAssetSchemaTest(unittest.TestCase):
    def test_upload_failure_has_one_state_at_a_time(self) -> None:
        self.assertEqual(MediaAssetStatus.PENDING_UPLOAD.value, "PENDING_UPLOAD")
        self.assertEqual(MediaAssetStatus.UPLOAD_FAILED.value, "UPLOAD_FAILED")
        self.assertNotEqual(MediaAssetStatus.UPLOAD_FAILED, MediaAssetStatus.PENDING_DELETE)

    def test_required_roles_exist(self) -> None:
        self.assertEqual(
            {role.value for role in MediaAssetRole},
            {"source", "intermediate", "candidate", "qa_input", "preview_watermarked", "final_master", "delivery_variant", "legacy_video"},
        )
```

Add migration tests for owner/order/parent FKs, deterministic unique object key, `read_revoked_at`, deletion retry fields, deletion `lease_owner/lease_claim_id/lease_expires_at/fencing_token`, checksum/size/dimensions, grant token hash, service-only RLS on grants/deletion/quota work, unique `(user_id, window_kind, window_start)`, nonnegative quota counters, and one quota-state row per user. Provider grants also store exact `provider`, `purpose`, nullable future `job_id`/`attempt_id`, `runtime_bundle_id`, target API deployment ID, serving deployment role, expiry, maximum reads, used count, and revocation time; the token itself is hash-only. `job_id` is a nullable UUID plus index in `0015`; do not invent a foreign key before `generation_jobs` exists. Task 16/`0019` adds and validates that FK.

- [ ] **Step 2: Run tests and confirm current URL-only Order schema fails**

```powershell
python -m unittest backend.tests.test_media_asset_schema -v
```

Expected: FAIL because media models/migration do not exist and Orders still use URL JSONB as authority.

- [ ] **Step 3: Implement additive media schema and explicit transitions**

```python
class MediaAssetStatus(StrEnum):
    PENDING_UPLOAD = "PENDING_UPLOAD"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    ACTIVE = "ACTIVE"
    PENDING_DELETE = "PENDING_DELETE"
    DELETE_FAILED = "DELETE_FAILED"
    DELETED = "DELETED"
    QUARANTINED = "QUARANTINED"


class MediaAssetRole(StrEnum):
    SOURCE = "source"
    INTERMEDIATE = "intermediate"
    CANDIDATE = "candidate"
    QA_INPUT = "qa_input"
    PREVIEW_WATERMARKED = "preview_watermarked"
    FINAL_MASTER = "final_master"
    DELIVERY_VARIANT = "delivery_variant"
    LEGACY_VIDEO = "legacy_video"
```

Migration `0015` is expand-only: add tables/indexes/RLS and nullable asset-reference columns to Orders and `live_portrait_jobs` (`source_asset_id`, `video_asset_id`). `upload_quota_windows` persists exact hourly request and daily byte buckets; `upload_quota_states` persists the current concurrent-slot count and version under a per-user row lock; `upload_quota_reservations` uniquely records `(batch_id,part_ordinal)`, reserved bytes, actual attempted bytes, `RESERVED | SETTLED | RELEASED`, and settlement/slot-release timestamps so crash recovery cannot double-release. Provider-grant binding columns are present from this migration so the later Worker does not need runtime DDL or an amended committed migration. Do not delete legacy URL columns until Task 30. Use RESTRICT for ownership/history and keep object keys until storage confirms absence.

- [ ] **Step 4: Upgrade and verify the schema**

```powershell
Push-Location backend
alembic upgrade head
alembic current
Pop-Location
python -m unittest backend.tests.test_media_asset_schema backend.tests.test_supabase_rls_migration -v
```

Expected: revision `20260710_0015`; tests PASS.

- [ ] **Step 5: Commit private media schema**

```powershell
git diff --check
git add backend/app/models/upload_batch.py backend/app/models/media_asset.py backend/app/models/asset_access_grant.py backend/app/models/upload_quota_window.py backend/app/models/upload_quota_state.py backend/app/models/upload_quota_reservation.py backend/app/models/__init__.py backend/app/models/order.py backend/app/models/live_portrait_job.py backend/alembic/versions/20260710_0015_private_media_assets.py backend/tests/test_media_asset_schema.py docs/ai-worklog.md
git commit -m "feat: add private media asset authority"
```

### Task 10: Implement Authenticated Uploads, Safe Decode, Private Storage, Grants, And Admin Fetch

**Files:**
- Create: `backend/app/services/media_asset_service.py`
- Create: `backend/app/services/upload_quota_service.py`
- Create: `backend/app/services/external_fetch_service.py`
- Create: `backend/app/routers/media.py`
- Create: `backend/tests/test_authenticated_upload.py`
- Create: `backend/tests/test_external_fetch_security.py`
- Create: `backend/tests/integration/test_private_storage.py`
- Modify: `backend/app/services/storage.py`
- Modify: `backend/app/routers/upload.py`
- Modify: `backend/app/routers/gatekeeper.py`
- Modify: `backend/app/services/gatekeeper_service.py`
- Modify: `backend/app/services/identity_reference_service.py`
- Modify: `backend/app/services/identity_embedding_service.py`
- Modify: `backend/app/routers/__init__.py`
- Modify: `backend/tests/test_gatekeeper_service.py`
- Modify: `backend/tests/test_identity_reference_service.py`
- Modify: `backend/tests/test_identity_embedding_service.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: `create_upload_batch()`, `store_validated_upload()`, `activate_upload_batch()`, `create_provider_grant()`, and `stream_provider_grant()`.
- Produces: `stream_authenticated_multipart_upload(request: Request, user: User)`, which owns body streaming after session/Origin/CSRF and quota admission; the route declares no FastAPI `File`/`Form`/`UploadFile` body dependency.
- Public upload response: `{batch_id, assets: [{asset_id, width, height, mime_type, byte_size, expires_at}]}`.
- Admin fetch: `fetch_admin_https(url: str) -> ValidatedImageBytes` with exact SSRF limits.

- [ ] **Step 1: Write malicious-file, rollback, ownership, quota, grant, and SSRF tests**

```python
# backend/tests/test_authenticated_upload.py
class AuthenticatedUploadTest(unittest.IsolatedAsyncioTestCase):
    async def test_batch_is_all_or_nothing(self) -> None:
        response = await self.client.post(
            "/api/v1/media/uploads",
            files=[self.valid_jpeg(), self.truncated_png()],
            cookies=self.user_cookies,
            headers=self.csrf_headers,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "upload_batch_rejected")
        self.assertEqual(await self.active_asset_count(), 0)

    async def test_cross_user_asset_reference_is_forbidden(self) -> None:
        response = await self.client.get(f"/api/v1/media/{self.other_asset_id}", cookies=self.user_cookies)
        self.assertEqual(response.status_code, 403)
```

Add cases for missing session, MIME/magic mismatch, 10 MiB, 5 files, 40 MP, image bomb, EXIF removal, hourly/daily/concurrent quota, storage crash after first object, stale intent cleanup, 32-byte grant hash/600 TTL/3 reads, private/link-local/metadata DNS, redirect re-resolution, non-443 port, >2 redirects, 5-second connect/30-second total timeout, and >10 MiB stream. Rewrite `test_gatekeeper_service.py` so vision outage/schema failure is blocking and can never PASS through OCR/local fallback. Rewrite identity reference/embedding tests and services so face/upper-body derivatives are private `MediaAsset` IDs and embeddings receive owner-checked private bytes/asset IDs, never `face_crop_url`, `upper_body_crop_url`, or arbitrary source URL strings. Tests instrument `Request.stream()` and prove an unauthenticated/bad-Origin/bad-CSRF/slot-exhausted request reads zero body bytes; forged/missing Content-Length cannot reduce the reservation; concurrent file parts cannot exceed 200 MiB/day; a rejected partial stream charges actual bytes read but releases only unused reserved bytes and the active slot.

- [ ] **Step 2: Run focused tests and verify current public URL upload fails**

```powershell
python -m unittest backend.tests.test_authenticated_upload backend.tests.test_external_fetch_security -v
```

Expected: FAIL because current upload is anonymous, trusts MIME, skips bad batch members, returns public URLs, and storage uses `public-read`/`access="public"`.

- [ ] **Step 3: Refactor storage to private object-key operations**

```python
class PrivateObjectStore(Protocol):
    def put_private(self, object_key: str, data: bytes, content_type: str) -> None:
        raise NotImplementedError

    def read_private(self, object_key: str) -> bytes:
        raise NotImplementedError

    def delete_private(self, object_key: str) -> DeleteResult:
        raise NotImplementedError


class DeleteResult(StrEnum):
    DELETED = "DELETED"
    NOT_FOUND = "NOT_FOUND"
    FAILED = "FAILED"
```

S3 upload omits ACL or uses bucket-owner private policy. Vercel Blob uses a separately provisioned Private Blob store and private access, storing its pathname/object key rather than a permanent public URL. A token connected to the current public store cannot be made private by changing a per-file argument; runtime readiness must verify that the connected store itself is private. Local private storage is debug-only and served only through the authenticated media router.

- [ ] **Step 4: Implement deterministic upload intent and strict decode**

The FastAPI route must not declare any `File`, `Form`, or `UploadFile` body field, because FastAPI/Starlette would parse/spool the multipart body before the dependency chain can enforce this contract. First validate Cookie session, exact Origin, CSRF, capability, and request rate using only headers/cookies; lock the canonical `UploadQuotaState` and UTC hour window, reserve one of two active slots plus one of 20 requests, create `UploadBatch`, and commit before calling `Request.stream()`. Parse the body with the existing bounded `python-multipart` streaming callbacks: part headers/boundaries have small fixed limits, and before reading each file part body the service locks the daily window and reserves the full 10 MiB maximum for that part. Stop before a sixth file. Never trust Content-Length, filename, declared MIME, or multipart-reported size as quota evidence.

Track raw bytes read per part. On completion or rejection, settle each 10 MiB reservation down to actual raw bytes read, retain those attempted bytes against the 200-MiB day limit (including malformed/failed uploads, preventing free abuse), release only the unused maximum, and release the active slot exactly once; stale batch leases perform the same idempotent settlement. PostgreSQL/cache failure is fail-closed and edge/in-memory limiters are not authority. Apply hard stream limits, accept decoded JPEG/PNG/WebP only, verify magic, set `Image.MAX_IMAGE_PIXELS=40000000`, decode/load, normalize orientation, strip EXIF/ICC/GPS, and re-encode to canonical JPEG/WebP. Create PENDING_UPLOAD asset rows before object writes; activate the entire batch in one transaction only after every object succeeds. Failure transitions each affected row `UPLOAD_FAILED -> PENDING_DELETE`; it never returns partial 200.

Gatekeeper and identity-reference/embedding pipelines accept owner-checked `asset_id`, load private bytes through `media_asset_service`, and persist every derivative as a private asset linked to its source; no permanent crop/source URL is stored in `generation_params` or returned. Gatekeeper fails closed when required vision/safety schema is unavailable. Ordinary user APIs reject URLs. Admin fetch is a separate Admin-auth route and is never accepted by order creation. It allows HTTPS port 443 only, resolves and validates every DNS/redirect hop, rejects private/loopback/link-local/multicast/metadata/reserved ranges, pins the connection to the validated IP while preserving original-host TLS SNI/hostname verification, limits redirects to two, and enforces both declared and streamed size limits to prevent DNS rebinding or redirect SSRF.

- [ ] **Step 5: Verify real private storage integration**

```powershell
python -m unittest backend.tests.test_authenticated_upload backend.tests.test_external_fetch_security backend.tests.test_gatekeeper_service -v
$env:RUN_PRIVATE_STORAGE_INTEGRATION='1'
python -m unittest backend.tests.integration.test_private_storage -v
```

Expected: unit tests PASS; integration uploads, reads, and deletes a private object; an unauthenticated direct URL is denied. Missing integration credentials yields NOT_RUN/nonzero in the integration gate, not PASS.

- [ ] **Step 6: Commit private upload and grants**

```powershell
git diff --check
git add backend/app/services/storage.py backend/app/services/media_asset_service.py backend/app/services/upload_quota_service.py backend/app/services/external_fetch_service.py backend/app/services/gatekeeper_service.py backend/app/services/identity_reference_service.py backend/app/services/identity_embedding_service.py backend/app/routers/media.py backend/app/routers/upload.py backend/app/routers/gatekeeper.py backend/app/routers/__init__.py backend/tests/test_authenticated_upload.py backend/tests/test_external_fetch_security.py backend/tests/test_gatekeeper_service.py backend/tests/test_identity_reference_service.py backend/tests/test_identity_embedding_service.py backend/tests/integration/test_private_storage.py docs/ai-worklog.md
git commit -m "feat: authenticate and privatize media uploads"
```

### Task 11: Implement Private Owner Reads, Reference-Guarded Retention, And Retryable Deletion

**Files:**
- Create: `backend/app/services/media_deletion_service.py`
- Create: `backend/tests/test_private_asset_read.py`
- Create: `backend/tests/test_media_deletion.py`
- Create: `backend/tests/integration/test_private_storage_deletion.py`
- Modify: `backend/tests/test_account_closure.py`
- Modify: `backend/app/routers/media.py`
- Modify: `backend/app/services/retention_service.py`
- Modify: `backend/app/services/media_asset_service.py`
- Modify: `backend/app/services/account_closure_service.py`
- Modify: `backend/app/routers/ops.py`
- Modify: `backend/app/routers/users.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: `authorize_owner_asset_read(user, asset)`, `request_asset_deletion()`, `claim_deletion_batch()`, and `run_reference_guard()`.
- Defers: paid/trial final delivery authorization until Task 20, after order entitlements and generation artifacts exist; `PRIVATE_DOWNLOAD` stays OFF.
- Consumes later: Task 16 job/attempt state, Task 21 consent cases, and Task 28 migration references; unknown/new reference types fail closed.

- [ ] **Step 1: Write owner-read, retention, active-reference, and deletion retry tests**

```python
# backend/tests/test_media_deletion.py
class MediaDeletionTest(unittest.IsolatedAsyncioTestCase):
    async def test_delete_failure_preserves_object_key_for_retry(self) -> None:
        asset = await self.active_asset()
        self.store.delete_private.return_value = DeleteResult.FAILED
        await self.service.delete_one(self.db, asset.id)
        self.assertEqual(asset.status, MediaAssetStatus.DELETE_FAILED)
        self.assertIsNotNone(asset.object_key)
        self.assertGreater(asset.next_delete_at, self.now)

    async def test_active_generation_reference_blocks_source_delete(self) -> None:
        result = await self.service.request_deletion(self.db, self.source_id)
        self.assertEqual(result.code, "active_reference")
        self.store.delete_private.assert_not_called()
```

Add tests for authenticated owner source read, cross-user denial, final/candidate/master role denial before Task 20, `Cache-Control: private, no-store`, object-key non-disclosure, 404/410 idempotent delete success, user/account/retention triggers using the same guard, and 24h/7/30/90/180/365 deadlines. Unknown future job/consent/migration reference types block deletion until their later task adds an explicit resolver.

Also test that a deletion request with an active blocker immediately makes the asset unreadable while leaving the object intact; two cleaners cannot own one live lease; an expired lease can be reclaimed with a higher fencing token; stale claim success/failure cannot overwrite the new owner; and storage NOT_FOUND settles exactly once.

- [ ] **Step 2: Run focused tests and verify URL cleanup cannot satisfy them**

```powershell
python -m unittest backend.tests.test_private_asset_read backend.tests.test_media_deletion backend.tests.test_account_closure backend.tests.test_commercial_policy -v
```

Expected: FAIL because current cleanup deletes by URL and can clear references before confirmed deletion.

- [ ] **Step 3: Implement one deletion state machine and reference guard**

```python
async def request_asset_deletion(db: AsyncSession, asset_id: UUID, *, reason: str) -> MediaAsset:
    asset = await _lock_asset(db, asset_id)
    now = datetime.now(timezone.utc)
    asset.read_revoked_at = asset.read_revoked_at or now
    asset.status = MediaAssetStatus.PENDING_DELETE
    asset.next_delete_at = now
    asset.deletion_reason = reason
    blockers = await run_reference_guard(db, asset)
    if blockers:
        asset.deletion_blockers = canonical_blocker_codes(blockers)
        asset.next_delete_at = next_reference_recheck(now)
        return asset
    asset.deletion_blockers = ()
    return asset


async def confirm_storage_deletion(
    db: AsyncSession,
    *,
    asset_id: UUID,
    lease_claim_id: UUID,
    fencing_token: int,
    result: DeleteResult,
) -> None:
    asset = await _lock_asset(db, asset_id)
    require_current_deletion_claim(asset, lease_claim_id, fencing_token, datetime.now(timezone.utc))
    if result in {DeleteResult.DELETED, DeleteResult.NOT_FOUND}:
        asset.status = MediaAssetStatus.DELETED
        asset.deleted_at = datetime.now(timezone.utc)
        asset.clear_deletion_lease()
        return
    asset.status = MediaAssetStatus.DELETE_FAILED
    asset.delete_attempts += 1
    asset.next_delete_at = next_backoff(asset.delete_attempts)
    asset.clear_deletion_lease()
```

The request transition and read revocation commit even when a reference blocks physical deletion; APIs treat `read_revoked_at` or PENDING_DELETE as unreadable. Never remove `object_key` until DELETED. `claim_deletion_batch()` selects due unblocked PENDING_DELETE/DELETE_FAILED rows with `FOR UPDATE SKIP LOCKED`, writes a fresh UUID `lease_claim_id`, owner, 120-second expiry, and incremented fencing token, then commits before storage I/O. `confirm_storage_deletion()` and retry writes use compare-and-set on asset ID/claim ID/fence; stale or expired claims are rejected, and an expired lease is recoverable by a new claim. Consume `media_cleanup_pending` account tombstones through the same state machine. At this task the guard checks existing order/task markers, shared templates, account closure, and migration references; any unrecognized/future reference blocks. Tasks 16 and 21 add explicit generation-attempt and consent-case resolvers. A 7-day source deadline with unresolved Provider state remains blocked until the later resolver reconciles/stops the job and applies release/refund settlement before deletion.

- [ ] **Step 4: Implement private authorized streaming**

`GET /api/v1/media/{asset_id}` loads Cookie user, ownership, and asset role/status, then streams only that user's ACTIVE source upload without exposing object key/permanent URL; set `Cache-Control: private, no-store`. Candidate, master, preview, and delivery roles are denied here. Task 20 adds the separate entitlement-aware order download endpoint after commercial/job facts exist.

- [ ] **Step 5: Verify scheduled cleanup and real deletion**

```powershell
python -m unittest backend.tests.test_private_asset_read backend.tests.test_media_deletion backend.tests.test_account_closure backend.tests.test_commercial_policy -v
$env:RUN_PRIVATE_STORAGE_INTEGRATION='1'
python -m unittest backend.tests.integration.test_private_storage_deletion -v
```

Expected: all unit tests PASS; integration confirms object absence and retry behavior.

- [ ] **Step 6: Commit private access and deletion**

```powershell
git diff --check
git add backend/app/services/media_deletion_service.py backend/app/services/media_asset_service.py backend/app/services/account_closure_service.py backend/app/services/retention_service.py backend/app/routers/media.py backend/app/routers/ops.py backend/app/routers/users.py backend/tests/test_private_asset_read.py backend/tests/test_media_deletion.py backend/tests/test_account_closure.py backend/tests/integration/test_private_storage_deletion.py docs/ai-worklog.md
git commit -m "feat: enforce private delivery and durable deletion"
```

**Work Package 1 exit:** Run migrations from empty PostgreSQL through `0015`, all Tasks 5-11 focused tests, real private storage upload/owner-read/delete, and a protected Preview browser Google session smoke. For that smoke only, preprovision the exact Supabase subject binding, set Preview GOOGLE_AUTH to a deployment-bound cohort for at most two hours, prove intent→first login→refresh→logout with no bypass, then restore GOOGLE_AUTH OFF and verify the original snapshot hash; every other high-risk flag and all Production flags remain OFF. Commercial final download is intentionally unavailable until Task 20, and this package proves foundations rather than Production readiness.

---

## Work Package 2 — Commercial Transaction Core

### Task 12: Add Versioned Catalog, Grant Lots, Reservations, Allocations, And Entitlements

**Files:**
- Create: `backend/app/models/billing_catalog.py`
- Create: `backend/app/models/credit_grant_lot.py`
- Create: `backend/app/models/credit_reservation.py`
- Create: `backend/app/models/order_entitlement.py`
- Create: `backend/app/models/order_entitlement_funding.py`
- Create: `backend/app/models/welcome_grant_claim.py`
- Create: `backend/app/models/payment_reconciliation_case.py`
- Create: `backend/app/models/idempotency_record.py`
- Create: `backend/app/models/outbox_event.py`
- Create: `backend/app/services/billing_catalog_service.py`
- Create: `scripts/release/import_provider_catalog.py`
- Create: `release/catalog/catalog-2026-07-10.json`
- Create: `release/catalog/provider-product-mapping.schema.json`
- Create: `backend/alembic/versions/20260710_0016_commercial_ledger.py`
- Create: `backend/tests/test_commercial_ledger_migration.py`
- Create: `backend/tests/test_billing_catalog.py`
- Create: `backend/tests/test_billing_catalog_import.py`
- Modify: `backend/app/models/credit_transaction.py`
- Modify: `backend/app/models/user_credit.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/routers/credits.py`
- Modify: `backend/tests/test_regional_pricing.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: immutable `BillingCatalogVersion`, `BillingProduct`, `CreditGrantLot`, `CreditReservation`, `CreditReservationAllocation`, `OrderEntitlement` plus immutable funding rows, one identity-bound `WelcomeGrantClaim`, `PaymentReconciliationCase`, `IdempotencyRecord`, and generic `OutboxEvent` facts.
- Consumed by: Tasks 13-15 for accounting, Task 20 for delivery, Task 23 for catalog-only UI, and Task 28 for legacy reconciliation.

- [ ] **Step 1: Write migration and catalog contract tests**

```python
# backend/tests/test_billing_catalog.py
EXPECTED_PRODUCTS = {
    "pack_50": (1290, "USD", 50),
    "pack_120": (2490, "USD", 120),
    "pack_300": (4990, "USD", 300),
    "starter_monthly": (1900, "USD", 80),
    "creator_monthly": (4900, "USD", 300),
    "studio_monthly": (12900, "USD", 900),
}


class BillingCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_or_conflicting_catalog_fails_closed(self) -> None:
        with self.assertRaises(BillingCatalogUnavailable):
            await load_active_catalog(self.db, environment="production")

    async def test_seeded_catalog_is_exact(self) -> None:
        catalog = await load_active_catalog(self.seeded_db, environment="production")
        actual = {
            item.product_code: (item.pre_tax_minor_units, item.currency, item.credits)
            for item in catalog.products
        }
        self.assertEqual(actual, EXPECTED_PRODUCTS)
```

Add PostgreSQL tests for: `0016.down_revision == 20260710_0015`; append-only ledger rows; `accounting_balance` may be negative while `spendable_balance` is never negative; one active reservation per order; allocation amounts are positive and sum exactly to the reservation; reservation transitions are only `RESERVED -> CAPTURED | RELEASED | EXPIRED`; entitlement transitions are only `ACTIVE -> REVOKED`; entitlement funding rows exactly reproduce captured reservation allocations; one verified identity can own only one welcome-grant claim; one paid funding grant/purchase can unlock at most one trial order; cumulative reversals cannot exceed the original grant; and financial/user foreign keys are RESTRICT/tombstone-safe. Rewrite `test_regional_pricing.py` to consume the authoritative catalog/localized presentation and reject any import of static `CREDIT_PACKAGES` or fallback price; localization may format currency/copy but cannot change amount, product, or credits. Route tests require `GET /credits/packages` to read only the active PostgreSQL catalog and return 503 on missing/conflicting versions; legacy direct `/credits/purchase`, `/credits/deduct`, and `/credits/add` remain 410 with zero side effects.

- [ ] **Step 2: Run focused tests and confirm the old balance-only model fails**

```powershell
python -m unittest backend.tests.test_commercial_ledger_migration backend.tests.test_billing_catalog -v
```

Expected: FAIL because the current schema has a materialized balance and coarse transactions but no catalog version, grant lineage, reservation allocation, entitlement, reconciliation, or request-hash idempotency facts.

- [ ] **Step 3: Add the expand-only commercial migration**

Create `0016` after `0015`. Keep existing credit/payment columns for compatibility; do not delete or rewrite them in this migration. Add exact database enums/check constraints, unique idempotency keys, partial unique indexes, immutable transaction triggers, RLS, and a generic IDs-only `outbox_events` table with unique dedupe key/status/attempt/next-attempt/payload-version fields so payment events in Task 14 are crash-recoverable before generation tables exist. Add these lineage fields:

```python
class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class EntitlementStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


def spendable_balance(accounting_balance: int, reserved: int) -> int:
    return max(0, accounting_balance - reserved)


def debt(accounting_balance: int) -> int:
    return max(0, -accounting_balance)
```

`credit_grant_lots` references the original positive ledger transaction and records `source_type`, `source_id`, `expires_at`, retention tier, original amount, immutable `debt_offset_amount`, reversed amount, and frozen amount. Spendable amount can never include the debt-offset portion, even after a later purchase makes the materialized balance positive. `credit_reservation_allocations` references `(reservation_id, grant_lot_id, amount)` and is immutable after insert. `order_entitlement_fundings` copies the exact captured allocation IDs/amounts into immutable entitlement lineage; it cannot point to an uncaptured reservation. `welcome_grant_claims` uniquely binds one verified `user_identity_id` to one positive welcome ledger transaction/grant lot and survives account merge. Existing untraceable balance is represented only by an audited `legacy_pool` lot during Task 28; this migration does not fabricate a purchase source.

- [ ] **Step 4: Seed and expose one authoritative catalog**

Seed the six exact products above with a version and effective timestamp. Product metadata contains no unimplemented `remote_join`, `live_portrait`, `priority_generation`, trial, upgrade, downgrade, pause, proration, or email promise. Add:

```python
async def load_active_catalog(
    db: AsyncSession,
    *,
    environment: str,
    now: datetime | None = None,
) -> BillingCatalogSnapshot:
    now = now or datetime.now(timezone.utc)
    versions = list((await db.scalars(
        select(BillingCatalogVersion).where(
            BillingCatalogVersion.environment == environment,
            BillingCatalogVersion.effective_at <= now,
            or_(BillingCatalogVersion.expires_at.is_(None), BillingCatalogVersion.expires_at > now),
        )
    )).all())
    if len(versions) != 1:
        raise BillingCatalogUnavailable("active_catalog_cardinality")
    products = list((await db.scalars(
        select(BillingProduct).where(BillingProduct.catalog_version_id == versions[0].id)
    )).all())
    snapshot = BillingCatalogSnapshot.from_rows(versions[0], products)
    snapshot.assert_exact_release_contract()
    return snapshot


async def require_catalog_product(
    db: AsyncSession,
    *,
    product_code: str,
    provider_product_id: str,
    pre_tax_minor_units: int,
    currency: str,
) -> BillingProductSnapshot:
    catalog = await load_active_catalog(
        db, environment=settings.runtime_environment, now=datetime.now(timezone.utc)
    )
    product = catalog.by_code.get(product_code)
    if product is None:
        raise BillingCatalogUnavailable("product_not_active")
    if (
        product.provider_product_id != provider_product_id
        or product.pre_tax_minor_units != pre_tax_minor_units
        or product.currency != currency
    ):
        raise BillingCatalogMismatch(product_code)
    return product
```

Replace the old static credits package response with this service snapshot. Keep balance and immutable transaction-history reads, but do not resurrect a direct credit mutation or purchase route; Tasks 14-15 expose the only checkout flows.

Missing, overlapping, expired, provider-ID-mismatched, amount-mismatched, currency-mismatched, or duplicate active catalog rows fail closed and create an operational alert; no caller receives a fallback price.

The migration and `release/catalog/catalog-2026-07-10.json` version only product codes, prices, credits, retention, and effective dates. Real Creem product IDs are environment-specific and are loaded by `scripts/release/import_provider_catalog.py` from a protected JSON file whose shape is fixed by `provider-product-mapping.schema.json`. The importer requires the target environment, catalog version, provider, exact six product codes, release SHA, approver audit ID, and source SHA-256; it writes append-only `BillingProviderProduct` rows plus an audit record, refuses duplicate/conflicting IDs or in-place mutation, and emits only provider-ID hashes. Checkout cannot open until the Production mapping checksum is bound to the release manifest.

```powershell
python scripts/release/import_provider_catalog.py --database-url-env PREVIEW_DATABASE_URL --mapping-file-env CREEM_PRODUCT_MAPPING_FILE --environment preview --catalog-version 2026-07-10 --release-sha $env:GITHUB_SHA --dry-run
python -m unittest backend.tests.test_billing_catalog_import -v
```

Expected: dry-run validates all six real mappings without writes; the test proves missing/extra/conflicting mappings fail closed. Production write mode is reserved for the protected Task 29 workflow and must use the exact reviewed file checksum.

- [ ] **Step 5: Verify empty-database migration, constraints, and catalog**

```powershell
Push-Location backend
$env:DATABASE_URL=$env:TEST_DATABASE_URL
& ..\.venv\Scripts\python.exe scripts/migrate_db.py
& ..\.venv\Scripts\python.exe -m alembic current
& ..\.venv\Scripts\python.exe -m unittest tests.test_commercial_ledger_migration tests.test_billing_catalog tests.test_billing_catalog_import -v
Pop-Location
```

Expected: revision is `20260710_0016`; all focused tests PASS; catalog output equals the six-product contract exactly.

- [ ] **Step 6: Record and commit the schema foundation**

```powershell
git diff --check
git add backend/alembic/versions/20260710_0016_commercial_ledger.py backend/app/models/billing_catalog.py backend/app/models/credit_grant_lot.py backend/app/models/credit_reservation.py backend/app/models/order_entitlement.py backend/app/models/order_entitlement_funding.py backend/app/models/welcome_grant_claim.py backend/app/models/payment_reconciliation_case.py backend/app/models/idempotency_record.py backend/app/models/outbox_event.py backend/app/models/credit_transaction.py backend/app/models/user_credit.py backend/app/models/__init__.py backend/app/services/billing_catalog_service.py backend/app/routers/credits.py scripts/release/import_provider_catalog.py release/catalog/catalog-2026-07-10.json release/catalog/provider-product-mapping.schema.json backend/tests/test_commercial_ledger_migration.py backend/tests/test_billing_catalog.py backend/tests/test_billing_catalog_import.py backend/tests/test_regional_pricing.py docs/ai-worklog.md
git commit -m "feat: add commercial ledger and catalog facts"
```

### Task 13: Implement Deterministic Reservations, Idempotency, Reversals, And Account-Merge Lineage

**Files:**
- Create: `backend/app/services/credit_reservation_service.py`
- Create: `backend/app/services/idempotency_service.py`
- Create: `backend/app/services/credit_reversal_service.py`
- Create: `backend/app/services/account_merge_credit_service.py`
- Create: `backend/app/services/welcome_grant_service.py`
- Create: `backend/tests/test_credit_reservations.py`
- Create: `backend/tests/test_idempotency_service.py`
- Create: `backend/tests/test_account_merge_lineage.py`
- Create: `backend/tests/test_welcome_grant.py`
- Create: `backend/tests/test_financial_reporting_authority.py`
- Modify: `backend/app/services/credit_service.py`
- Modify: `backend/app/services/generation_credit_policy.py`
- Modify: `backend/app/services/ops_monitoring_service.py`
- Modify: `backend/app/services/account_merge_service.py`
- Modify: `backend/app/services/auth_session_service.py`
- Modify: `backend/app/services/admin_service.py`
- Modify: `backend/app/routers/auth/account_claim.py`
- Modify: `backend/app/routers/admin.py`
- Modify: `backend/tests/test_admin_management_routes.py`
- Modify: `backend/tests/test_credit_ledger.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: one identity-bound welcome grant; deterministic grant allocation; idempotent reserve/capture/release/expire/refund/reversal operations; and compensation-based account merge that preserves root grant lineage.
- Consumes: Task 12 commercial schema. Task 16 later invokes these primitives inside the single order/reservation/job/outbox transaction; the old order writer stays blocked until then.

- [ ] **Step 1: Write concurrency, allocation, idempotency, reversal, and merge tests**

```python
# backend/tests/test_idempotency_service.py
class IdempotencyServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_scope_key_and_hash_reuses_stored_result(self) -> None:
        first = await begin_idempotent_request(
            self.db, user_id=self.user_id, endpoint="orders.create", key="key-1", request_hash="hash-a"
        )
        second = await begin_idempotent_request(
            self.db, user_id=self.user_id, endpoint="orders.create", key="key-1", request_hash="hash-a"
        )
        self.assertEqual(first.record_id, second.record_id)

    async def test_same_key_with_different_hash_conflicts(self) -> None:
        await begin_idempotent_request(
            self.db, user_id=self.user_id, endpoint="orders.create", key="key-1", request_hash="hash-a"
        )
        with self.assertRaises(IdempotencyConflict):
            await begin_idempotent_request(
                self.db, user_id=self.user_id, endpoint="orders.create", key="key-1", request_hash="hash-b"
            )
```

Add real PostgreSQL tests proving: concurrent local-session provisioning for one verified identity creates exactly one welcome claim/ledger/grant lot and one materialized balance increment; merged accounts cannot claim a second welcome grant; a welcome lot funds only a 2-credit base single-subject trial; couple, Partner Invite, Golden Anniversary, Director, premium scene, fourth attempt within 24 hours, or any attempt after a READY trial fails with zero order/reservation/job/outbox side effects; paid orders never allocate a welcome lot; concurrent reservations cannot overspend; allocation orders by earliest expiry, ledger created time, then stable UUID; allocations are immutable; reserve/capture/release/expire/refund are unique and idempotent; only a still-QUEUED pre-SUBMITTING reservation may expire/release; capture atomically subtracts both `reserved` and accounting balance and writes one debit; capture replay changes neither field; release/expiry subtract only reserved; post-capture failure writes one refund; refund/dispute cumulative reversal never exceeds root grant; consumed reversal creates debt and zero spendable; and merge lock order follows sorted user UUIDs. Rewrite the existing `test_credit_ledger.py` so direct `deduct_credits_async` cannot manufacture `GENERATION_DEBIT` and generic `add_credits_async` cannot manufacture purchase/admin grants: generation debit must come only from reservation capture with attempt provenance, and every positive grant must have an allowed immutable root plus lot. `test_financial_reporting_authority.py` proves Admin/ops totals query immutable ledger, reservation/allocation, reversal, and reconciliation facts; `generation_params`, `credits_cost`, `refunded_credits`, `fallback_amount`, Live Portrait cost, or coarse order status cannot affect billed/refunded/revenue metrics. During the blocked 7a transition `generation_credit_policy.py` may only validate/format an authoritative reservation/ledger result and must reject missing lineage; it cannot derive money from mutable params. Its remaining callers are removed in Tasks 16/18/19, then Task 19 deletes it. Do not retain legacy helpers for test compatibility. Admin tests prove legacy `grant_credits_to_user()`/`add_credits_async()` cannot be reached: a positive adjustment creates one audited, idempotent `ADMIN_GRANT` root ledger fact plus grant lot, while a negative adjustment is a bounded compensation/reversal against named roots, never an unlinked balance edit. Same request ID replays once, different payload conflicts, concurrent calls serialize, and missing approval/reason/source lineage changes nothing.

- [ ] **Step 2: Run focused tests and confirm the balance-only services fail**

```powershell
python -m unittest backend.tests.test_credit_reservations backend.tests.test_idempotency_service backend.tests.test_account_merge_lineage backend.tests.test_financial_reporting_authority -v
```

Expected: FAIL because current credit mutation has no grant allocation, request hash, root reversal cap, or merge lineage.

- [ ] **Step 3: Implement deterministic reservation and settlement primitives**

```python
async def reserve_credits(
    db: AsyncSession,
    *,
    user_id: UUID,
    order_id: UUID,
    amount: int,
    funding_policy: OrderFundingPolicySnapshot,
    idempotency_key: str,
    now: datetime,
) -> CreditReservation:
    existing = await db.scalar(select(CreditReservation).where(
        CreditReservation.user_id == user_id,
        CreditReservation.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        if (
            existing.order_id != order_id
            or existing.amount != amount
            or existing.funding_policy_hash != funding_policy.canonical_hash()
        ):
            raise IdempotencyConflict("reservation_payload_mismatch")
        return existing
    credit = await db.scalar(
        select(UserCredit).where(UserCredit.user_id == user_id).with_for_update()
    )
    await validate_and_lock_funding_policy(
        db, user_id=user_id, amount=amount, policy=funding_policy, now=now,
    )
    lots = list((await db.scalars(
        select(CreditGrantLot)
        .where(
            CreditGrantLot.user_id == user_id,
            CreditGrantLot.spendable_amount > 0,
            funding_policy.allowed_lot_predicate(),
        )
        .order_by(CreditGrantLot.expires_at.asc().nullslast(), CreditGrantLot.created_at, CreditGrantLot.id)
        .with_for_update()
    )).all())
    if credit is None or max(0, credit.balance - credit.reserved) < amount:
        raise InsufficientCredits(amount)
    reservation = CreditReservation(
        user_id=user_id, order_id=order_id, amount=amount,
        status=ReservationStatus.RESERVED, idempotency_key=idempotency_key,
        funding_policy_snapshot=funding_policy.model_dump(mode="json"),
        funding_policy_hash=funding_policy.canonical_hash(),
        expires_at=now + timedelta(seconds=1800),
    )
    db.add(reservation)
    await db.flush()
    allocate_reservation_fefo(db, reservation, lots)
    credit.reserved += amount
    return reservation


async def capture_reservation(
    db: AsyncSession,
    *,
    reservation_id: UUID,
    provider_attempt_id: UUID,
    idempotency_key: str,
) -> CreditReservation:
    reservation = await lock_reservation(db, reservation_id)
    if reservation.status is ReservationStatus.CAPTURED:
        if reservation.provider_attempt_id != provider_attempt_id:
            raise IdempotencyConflict("capture_attempt_mismatch")
        return reservation
    if reservation.status is not ReservationStatus.RESERVED:
        raise InvalidReservationTransition(reservation.status, ReservationStatus.CAPTURED)
    credit = await db.scalar(
        select(UserCredit).where(UserCredit.user_id == reservation.user_id).with_for_update()
    )
    if credit is None or credit.reserved < reservation.amount:
        raise CreditInvariantViolation("reserved_balance_underflow")
    reservation.status = ReservationStatus.CAPTURED
    reservation.provider_attempt_id = provider_attempt_id
    credit.reserved -= reservation.amount
    credit.balance -= reservation.amount
    await capture_reservation_allocations(db, reservation.id)
    await append_unique_credit_transaction(
        db, reservation=reservation, transaction_type="GENERATION_DEBIT",
        amount=-reservation.amount, balance_after=credit.balance,
        idempotency_key=idempotency_key,
    )
    return reservation


async def release_or_refund_reservation(
    db: AsyncSession,
    *,
    reservation_id: UUID,
    settlement: Literal["RELEASE", "GENERATION_REFUND"],
    idempotency_key: str,
) -> CreditSettlement:
    reservation = await lock_reservation(db, reservation_id)
    return await settle_locked_reservation(
        db, reservation=reservation, settlement=settlement, idempotency_key=idempotency_key
    )
```

`ensure_welcome_grant_for_identity()` locks the verified `UserIdentity`, inserts the unique `WelcomeGrantClaim`, one positive `WELCOME_BONUS=2` ledger row, its 2-credit welcome grant lot, and materialized balance update in one transaction, and returns the existing claim on replay. Task 13 modifies `auth_session_service.py` so first successful Google local-session provisioning calls this service before issuing the new Cookie session; any provisioning failure issues neither a new session nor a partial grant. Existing sessions do not receive grants from a client-triggered endpoint. The claim remains bound to the original identity through account merge, preventing a second grant. `OrderFundingPolicySnapshot` is server-derived and immutable: it includes mode, subject count, trial flag, identity claim ID, attempts in the rolling 24-hour window, READY-trial history, allowed lot class, and policy version. Its canonical hash is included in the order idempotency request hash and both the snapshot and hash are stored on the order/reservation; replay with a changed policy conflicts. The reservation service locks/validates those facts and never trusts a client trial boolean.

Lock user materialized credit and eligible grant lots, allocate deterministically, and update balance only in the same transaction as its immutable ledger fact. Welcome lots enforce single-subject base-trial policy and the three-attempt-per-24-hour limit; a READY trial permanently consumes trial eligibility. At capture, compute one immutable retention snapshot from the actual allocations: active paid-through Studio = 365 days; active paid-through ordinary subscription = 180; credit pack or a subscription grant after paid-through = 90; welcome/free = 30. Use the highest applicable captured tier; later events may extend an entitlement but never shorten an existing READY deadline.

- [ ] **Step 4: Implement root-grant reversal and controlled commercial account merge**

`reverse_root_grant()` freezes/reverses by the original grant, enforces the shared cap, creates debt when necessary, and revokes linked entitlements. `merge_credit_accounts()` uses paired `ACCOUNT_MERGE_OUT/ACCOUNT_MERGE_IN`, preserves original purchase/invoice/payment/audit ownership, records canonical/legacy/root lineage, and rebinds only mutable assets/orders/current entitlement. At this task it refuses nonterminal reservations, payment holds, and every subscription-bearing account because normalized subscription ownership rules do not exist until Task 15; it does not import/query future generation or Partner tables. Task 15 extends only the normalized subscription branch, Task 16 extends only terminal/settled generation facts, and Task 21 extends only after `partner_consent_cases` exists. Task 8's `commercial_lineage_not_ready` branch is replaced only for a proof-approved account that passes these locks.

Replace the Admin credit endpoint with a database-Admin-only command that requires an idempotency key, approved audit reason, target canonical UUID, and explicit positive grant policy or exact reversal roots. It calls the same ledger/lot/reversal services in one transaction; direct materialized-balance mutation is deleted. The public legacy credits mutation routes remain 410. No service token, email, OpenID, or arbitrary signed amount can bypass this contract.

- [ ] **Step 5: Verify real PostgreSQL concurrency and merge atomicity**

```powershell
$env:RUN_POSTGRES_INTEGRATION='1'
python -m unittest backend.tests.test_credit_reservations backend.tests.test_idempotency_service backend.tests.test_account_merge_lineage backend.tests.test_account_merge backend.tests.test_welcome_grant backend.tests.test_financial_reporting_authority -v
```

Expected: all tests PASS; no overspend, partial merge, owner rewrite of immutable facts, or double reversal.

- [ ] **Step 6: Record and commit financial primitives**

```powershell
git diff --check
git add backend/app/services/credit_reservation_service.py backend/app/services/idempotency_service.py backend/app/services/credit_reversal_service.py backend/app/services/account_merge_credit_service.py backend/app/services/welcome_grant_service.py backend/app/services/credit_service.py backend/app/services/generation_credit_policy.py backend/app/services/ops_monitoring_service.py backend/app/services/account_merge_service.py backend/app/services/auth_session_service.py backend/app/services/admin_service.py backend/app/routers/auth/account_claim.py backend/app/routers/admin.py backend/tests/test_credit_reservations.py backend/tests/test_idempotency_service.py backend/tests/test_account_merge_lineage.py backend/tests/test_welcome_grant.py backend/tests/test_financial_reporting_authority.py backend/tests/test_admin_management_routes.py backend/tests/test_credit_ledger.py docs/ai-worklog.md
git commit -m "feat: add idempotent credit and merge lineage"
```

### Task 14: Implement Creem Credit-Pack Facts, Signed Events, Reversals, And Reconciliation

**Files:**
- Create: `backend/app/services/creem_event_service.py`
- Create: `backend/app/services/payment_reconciliation_service.py`
- Create: `backend/app/core/provider_contracts.py`
- Create: `backend/app/schemas/payment.py`
- Create: `backend/alembic/versions/20260710_0017_creem_payment_facts.py`
- Create: `backend/tests/test_creem_payment_migration.py`
- Create: `backend/tests/test_credit_pack_checkout.py`
- Create: `backend/tests/test_payment_reversals.py`
- Create: `backend/tests/integration/test_creem_refund_creation_contract.py`
- Modify: `backend/app/models/credit_purchase.py`
- Modify: `backend/app/models/payment_event.py`
- Modify: `backend/app/services/payment_service.py`
- Modify: `backend/app/routers/payments.py`
- Modify: `backend/tests/test_payment_webhook.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: local purchase intents, verified normalized event facts, credit-pack grant/reversal/dispute lineage, and manual reconciliation cases.
- External boundary: signed Creem event ingestion is implementable from documented webhooks. A verified merchant-side refund-creation API has not been established; public self-service/automatic refund initiation therefore remains disabled until Task 29 records an official endpoint and successful Creem test-mode proof.

- [ ] **Step 1: Write raw-body signature and event-replay tests**

```python
# backend/tests/test_credit_pack_checkout.py
class CreditPackCheckoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_never_grants_credits(self) -> None:
        await self.client.get("/api/v1/payments/success?status=paid&purchase_id=x")
        self.assertEqual(await self.ledger_sum(), 0)

    async def test_duplicate_signed_event_grants_once(self) -> None:
        body, signature = self.signed_checkout_event(event_id="evt_1")
        await self.post_webhook(body, signature)
        await self.post_webhook(body, signature)
        self.assertEqual(await self.count_grants("evt_1"), 1)
```

Add tests that `0017.down_revision == 20260710_0016` plus raw-body HMAC-SHA256/constant-time comparison, invalid signature, database failure returning 5xx, unknown signed event stored `UNHANDLED`, event replay/out-of-order facts, internal purchase ID recovery after checkout response loss, catalog/product/pre-tax amount/currency mismatch, tax recorded separately, no fallback catalog, full refund reversal, partial refund OPEN case/freeze, dispute freeze/win/loss, reversal cap, consumed-credit debt, and entitlement revocation. Rewrite `test_payment_webhook.py` from legacy finalize/status-field expectations to normalized signed event facts, immutable captured/refund/dispute amounts, dedupe/hash conflict, and lineage-driven grant/reversal behavior; a coarse status update alone can never finalize money or access. Checkout concurrency tests require one Provider call for concurrent same-key/same-hash requests, 409 for same key/different hash, stored redirect replay from READY/CONFIRMED, no Provider call from CALLING/UNKNOWN, stable Provider idempotency key, and an ambiguous timeout that can only be reconciled—not automatically resubmitted.

- [ ] **Step 2: Run focused payment tests and confirm current coarse states fail**

```powershell
python -m unittest backend.tests.test_creem_payment_migration backend.tests.test_credit_pack_checkout backend.tests.test_payment_reversals backend.tests.test_payment_webhook -v
```

Expected: FAIL because current single purchase status and event rows do not encode independent capture/refund/dispute facts or full reversal lineage.

- [ ] **Step 3: Persist a local purchase intent before Creem checkout**

```python
class PurchaseIntentState(StrEnum):
    NEW = "NEW"
    CALLING = "CALLING"
    READY = "READY"
    UNKNOWN = "UNKNOWN"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    CONFIRMED = "CONFIRMED"


async def create_credit_pack_checkout(
    db: AsyncSession,
    *,
    user_id: UUID,
    product_code: str,
    idempotency_key: str,
) -> CheckoutRedirect:
    product = await require_checkout_catalog_product(db, product_code=product_code)
    intent = await lock_or_create_purchase_intent(
        db,
        user_id=user_id,
        product=product,
        idempotency_key=idempotency_key,
        request_hash=canonical_checkout_request_hash(product),
    )
    if intent.state in {PurchaseIntentState.READY, PurchaseIntentState.CONFIRMED}:
        return CheckoutRedirect.model_validate(intent.stored_response)
    if intent.state in {PurchaseIntentState.CALLING, PurchaseIntentState.UNKNOWN}:
        raise CheckoutReconciliationPending(intent.id)
    await transition_checkout_to_calling_once(db, intent)
    provider_request = intent.to_provider_request(idempotency_key=intent.provider_request_id)
    await db.commit()  # durable CALLING boundary before the external request
    try:
        checkout = await creem_client.create_checkout(provider_request)
    except CreemAmbiguousNetworkError:
        await mark_checkout_unknown(db, purchase_id=intent.id)
        await db.commit()
        raise CheckoutStatusUnknown(intent.id)
    await validate_checkout_response(checkout, intent.catalog_snapshot)
    await mark_checkout_ready(db, purchase_id=intent.id, checkout=checkout)
    await db.commit()
    return CheckoutRedirect.from_purchase(intent, checkout)
```

Commit `purchase_id`, request hash, catalog snapshot, unpredictable internal metadata ID, stable `provider_request_id`, and `CALLING` before the external call. A row lock/unique constraint elects exactly one caller. READY/CONFIRMED replays the stored response; CALLING/UNKNOWN returns a pending/reconciliation result without another Provider call. FAILED_RETRYABLE may return to NEW only after signed/provider query evidence proves the original request was absent or the verified Provider idempotency contract makes a repeat safe. On response loss persist UNKNOWN and reconcile; never guess that a timeout means failure. Validate returned product/amount/currency against the same snapshot. Never derive a grant from redirect query parameters.

- [ ] **Step 4: Normalize signed events and apply compensation-only accounting**

```python
class NormalizedPaymentEvent(BaseModel):
    model_config = ConfigDict(strict=True)
    event_id: str
    event_type: str
    occurred_at: datetime
    object_id: str
    request_id: str | None
    customer_id: str | None
    pre_tax_minor_units: int | None
    tax_minor_units: int | None
    currency: str | None
    normalized_status: str
    business_metadata: dict[str, str]
    raw_payload_sha256: str


async def ingest_verified_creem_event(
    db: AsyncSession,
    raw_body: bytes,
    signature: str,
    webhook_secret: bytes,
) -> AcceptedEvent:
    expected = hmac.new(webhook_secret, raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InvalidWebhookSignature()
    normalized = normalize_creem_event(json.loads(raw_body), hashlib.sha256(raw_body).hexdigest())
    accepted = await insert_payment_event_once(db, normalized)
    if accepted.created:
        db.add(OutboxEvent.for_payment_event(accepted.event_id, normalized.event_type))
    await db.commit()
    return accepted
```

Migration `0017` adds checkout intent state/request hash/stored response/provider request uniqueness, captured/refund/dispute transaction facts, raw-hash/normalized event fields, reconciliation constraints, and RLS without dropping legacy purchase status. The webhook transaction inserts `(provider,event_id)` once plus an outbox row, then returns 200. Separate handlers apply `checkout.completed`, `refund.created`, `dispute.created`, and dispute outcome facts. A verified purchase grant appends the full positive PURCHASE ledger fact, updates materialized balance, and makes only the residual after pre-existing debt spendable in its grant lot; a later full reversal still reverses the original full grant and may restore debt. Purchase display state is derived from `captured_minor_units`, `refunded_minor_units`, refund facts, and dispute facts; event ordering never overwrites a newer provider fact. Full refunds append `PURCHASE_REVERSAL`; a lost dispute appends `DISPUTE_REVERSAL`; both share the root-grant reversal cap. Partial refunds freeze unspent lineage, open the unique reconciliation case, and expose `PARTIAL_RECONCILIATION_REQUIRED` without guessing a credit ratio or overwriting payment facts.

- [ ] **Step 5: Hard-gate refund initiation on verified provider capability**

Add code-versioned provider fact `CREEM_REFUND_CREATION=UNVERIFIED` in `provider_contracts.py`; it is not an environment variable, frontend value, or mutable Admin flag. While UNVERIFIED, `POST /payments/{purchase_id}/refund` returns a structured 503 `provider_refund_creation_unverified`, writes no refund success fact, and leaves signed `refund.created` processing active. A later code/release change may set VERIFIED only while binding an official endpoint/auth/request/response/idempotency schema hash and Creem test-mode evidence to the release manifest. Do not invent a URL from dashboard or webhook documentation.

- [ ] **Step 6: Verify signed replay and reconciliation behavior**

```powershell
python -m unittest backend.tests.test_creem_payment_migration backend.tests.test_credit_pack_checkout backend.tests.test_payment_reversals backend.tests.test_payment_webhook -v
```

Expected: all unit/PostgreSQL tests PASS; the refund-initiation test remains intentionally closed while signed refund handling passes.

- [ ] **Step 7: Record and commit credit-pack facts**

```powershell
git diff --check
git add backend/alembic/versions/20260710_0017_creem_payment_facts.py backend/app/core/provider_contracts.py backend/app/models/credit_purchase.py backend/app/models/payment_event.py backend/app/services/creem_event_service.py backend/app/services/payment_reconciliation_service.py backend/app/services/payment_service.py backend/app/routers/payments.py backend/app/schemas/payment.py backend/tests/test_creem_payment_migration.py backend/tests/test_credit_pack_checkout.py backend/tests/test_payment_reversals.py backend/tests/test_payment_webhook.py backend/tests/integration/test_creem_refund_creation_contract.py docs/ai-worklog.md
git commit -m "feat: reconcile Creem credit-pack facts"
```

### Task 15: Normalize Subscription Transactions, Grants, Cancellation, Refunds, And Disputes

**Files:**
- Create: `backend/app/models/subscription_invoice.py`
- Create: `backend/app/models/subscription_cancel_intent.py`
- Create: `backend/alembic/versions/20260710_0018_subscription_facts.py`
- Create: `backend/tests/test_subscription_facts_migration.py`
- Create: `backend/tests/test_subscription_lifecycle.py`
- Create: `backend/tests/integration/test_creem_subscription_lifecycle.py`
- Modify: `backend/app/models/subscription_plan.py`
- Modify: `backend/app/core/provider_contracts.py`
- Modify: `backend/app/models/user_subscription.py`
- Modify: `backend/app/models/subscription_credit_grant.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/services/subscription_service.py`
- Modify: `backend/app/services/account_merge_credit_service.py`
- Modify: `backend/app/routers/subscriptions.py`
- Modify: `backend/app/schemas/subscription.py`
- Modify: `frontend/src/stores/subscription.ts`
- Modify: `backend/tests/test_account_merge_lineage.py`
- Modify: `backend/tests/test_subscription_billing.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: normalized provider-neutral subscription facts and exactly one grant per verified paid transaction.
- Consumes: Task 12 catalog/ledger and Task 14 signed event ingestion; Task 23 renders only these server facts.

- [ ] **Step 1: Write lifecycle, uniqueness, and amount-contract tests**

```python
class NormalizedSubscriptionStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
```

Test `0018.down_revision == 20260710_0017`; one canonical user has at most one nonterminal subscription; `(provider,provider_transaction_id)` and `(subscription_id,period_start,period_end)` are both unique; created/trialing/active without a verified paid transaction grants zero; exact Starter/Creator/Studio grants are 80/300/900; repeat/out-of-order webhooks grant once; missing stable transaction ID keeps billing disabled; PAST_DUE grants nothing and does not extend paid-through; retry success needs a new paid transaction; same/different plan checkout while active returns 409; unsupported trial/pause/resume/upgrade/downgrade/proration endpoints do not exist. Rewrite the existing `test_subscription_billing.py` away from `YYYY-MM`/settings/product helpers: it must require the stable paid provider transaction plus the independent subscription-period uniqueness key, authoritative catalog snapshots, and zero grant for status-only events. Do not keep an old period helper as a hidden grant path. Cancellation tests require concurrent same-key/same-hash requests to elect one Provider caller, confirmed replay to return the stored result, same key/different hash to return 409, CALLING/UNKNOWN to return pending without re-calling Creem, and ambiguous timeout to remain reconciliation-only until Provider evidence authorizes a safe retry. Add a lineage test for negative balance → subscription paid transaction → later pack purchase → reservation → refund of either root: the subscription grant permanently records its debt-offset portion, only residual credits become allocatable, no old lot resurrects, and entitlement/reversal attribution remains on the exact funding roots. Add merge tests that lock both users and every normalized subscription/invoice row; preserve immutable invoice/payment/grant ownership; rebind only the current subscription projection and active entitlement; atomically reject when both sides have an open subscription, provider IDs conflict, cancellation/refund/reconciliation is pending, or an unnormalized legacy row remains.

- [ ] **Step 2: Run focused tests and confirm the period-key implementation fails**

```powershell
python -m unittest backend.tests.test_subscription_facts_migration backend.tests.test_subscription_lifecycle backend.tests.test_subscription_billing -v
```

Expected: FAIL because the current `YYYY-MM`-style grant key and coarse lifecycle do not prove a unique paid invoice transaction or Creem-confirmed cancellation.

- [ ] **Step 3: Add `0018` subscription fact tables and constraints**

`subscription_invoices` records provider transaction/invoice IDs, subscription, period, pre-tax amount, tax, currency, provider status, occurred time, raw hash, and catalog snapshot. `subscription_credit_grants` references both the invoice and positive ledger/grant-lot lineage. `subscription_cancel_intents` uses `NEW | CALLING | UNKNOWN | CONFIRMED | FAILED_RETRYABLE`, stable request/request-hash/provider-idempotency IDs, stored response, attempt count, next retry, and provider evidence. Retain old columns for 7a compatibility; do not drop them before Task 30.

- [ ] **Step 4: Implement fact-driven state, grants, and period-end cancellation**

```python
async def apply_subscription_paid_transaction(
    db: AsyncSession,
    *,
    event: NormalizedPaymentEvent,
    catalog: BillingProductSnapshot,
) -> SubscriptionGrantResult:
    validate_paid_subscription_event(event, catalog)
    invoice = await insert_subscription_invoice_once(db, event=event, catalog=catalog)
    await db.flush()
    if invoice.credit_grant_id is not None:
        return SubscriptionGrantResult.replayed(invoice.credit_grant_id)
    subscription = await lock_subscription(db, invoice.subscription_id)
    credit = await db.scalar(
        select(UserCredit).where(UserCredit.user_id == subscription.user_id).with_for_update()
    )
    if credit is None:
        raise CreditAccountMissing(subscription.user_id)
    apply_newer_provider_fact(subscription, event)
    ledger = CreditTransaction.subscription_grant(
        user_id=subscription.user_id, source_id=str(invoice.id), amount=catalog.credits,
        balance_after=credit.balance + catalog.credits,
    )
    db.add(ledger)
    await db.flush()
    debt_offset = min(max(0, -credit.balance), catalog.credits)
    lot = CreditGrantLot.from_subscription_invoice(
        user_id=subscription.user_id,
        root_transaction_id=ledger.id,
        invoice_id=invoice.id,
        amount=catalog.credits,
        debt_offset_amount=debt_offset,
        spendable_original_amount=catalog.credits - debt_offset,
        retention_tier=catalog.retention_tier,
    )
    db.add(lot)
    await db.flush()
    grant = SubscriptionCreditGrant(
        subscription_id=subscription.id, user_id=subscription.user_id,
        invoice_id=invoice.id, credit_transaction_id=ledger.id,
        grant_lot_id=lot.id, credits=catalog.credits,
    )
    db.add(grant)
    await db.flush()
    invoice.credit_grant_id = grant.id
    credit.balance += catalog.credits
    subscription.paid_through_at = max_datetime(subscription.paid_through_at, invoice.period_end)
    return SubscriptionGrantResult.created(grant.id)


async def request_period_end_cancellation(
    db: AsyncSession,
    *,
    user_id: UUID,
    subscription_id: UUID,
    idempotency_key: str,
) -> SubscriptionCancelIntent:
    subscription = await lock_user_subscription(db, user_id, subscription_id)
    intent = await lock_or_create_cancel_intent(
        db,
        subscription=subscription,
        idempotency_key=idempotency_key,
        request_hash=canonical_cancel_request_hash(subscription),
    )
    if intent.state is CancelIntentState.CONFIRMED:
        return intent
    if intent.state in {CancelIntentState.CALLING, CancelIntentState.UNKNOWN}:
        raise CancellationReconciliationPending(intent.id)
    await transition_cancel_to_calling_once(db, intent)
    provider_request_id = intent.provider_request_id
    await db.commit()  # durable CALLING boundary before Creem side effect
    try:
        provider_fact = await creem_client.cancel_at_period_end(
            subscription.provider_subscription_id,
            idempotency_key=provider_request_id,
        )
    except CreemAmbiguousNetworkError:
        await mark_cancel_unknown(db, intent.id)
        await db.commit()
        raise CancellationReconciliationPending(intent.id)
    await confirm_cancel_intent(db, intent.id, provider_fact)
    await db.commit()
    return await load_cancel_intent(db, intent.id)
```

Raw status mapping is fixed by the specification. Only a verified catalog-matching paid transaction creates/extends paid-through and grants credits. The full positive ledger amount first repays pre-existing accounting debt; `debt_offset_amount` is immutable and only the residual enters the allocatable lot, matching the credit-pack rule. Cancellation row-locks a unique request hash, persists CALLING, then elects one caller of the verified Creem cancellation API with a stable Provider idempotency key. CONFIRMED replays; CALLING/UNKNOWN never calls again; FAILED_RETRYABLE may return to NEW only after reconciliation proves absence or the verified Provider contract makes replay safe. Until Creem API or signed webhook confirms it, the UI says cancellation pending and normalized state stays unchanged; failures remain reconcilable and never show success.

Extend `merge_credit_accounts()` only after these tables exist. Under the same sorted-user lock and transaction, lock normalized subscriptions, invoices, grants, cancel intents, reversals, and entitlements. Immutable invoice/payment/grant facts retain their original owner and merge lineage; only the canonical current-subscription projection and mutable current entitlement may be rebound. Either-side nonterminal provider conflict, dual open subscription, pending cancellation/refund/dispute/reconciliation, or unknown legacy mapping fails atomically with no owner or ledger change.

Cancellation does not expire an already paid, unreversed grant lot. After `paid_through_at`, unused subscription credits remain spendable but Task 13 evaluates them at the 90-day credit-pack retention tier; existing READY deadlines never shrink. PAST_DUE creates no grant and extends no paid-through; recovery requires a new unique paid transaction.

`provider_contracts.py` records the official Creem period-end cancellation schema hash and keeps `CREEM_SUBSCRIPTION_PAID_TRANSACTION=UNVERIFIED` until test-mode payloads prove a stable transaction ID and both invoice uniqueness keys. `SUBSCRIPTION_BILLING` remains OFF while either required fact is unverified.

- [ ] **Step 5: Implement invoice-specific reversals and disputes**

No public subscription refund endpoint exists. A service-authorized, audited operation may request only a specific invoice's full refund after a verified Creem refund-creation contract is available. A signed full refund appends one `SUBSCRIPTION_REVERSAL`, creates debt if consumed, and revokes linked entitlement. Partial refund opens reconciliation and freezes lineage. Invoice dispute freeze/win/loss shares the same cumulative reversal cap. Refund never implicitly cancels renewal.

- [ ] **Step 6: Verify test-mode lifecycle gate**

```powershell
python -m unittest backend.tests.test_subscription_facts_migration backend.tests.test_subscription_lifecycle backend.tests.test_subscription_billing -v
$env:RUN_CREEM_TEST_MODE='1'
python -m unittest backend.tests.integration.test_creem_subscription_lifecycle -v
```

Expected: local tests PASS. Integration is PASS only when real Creem test-mode first payment, renewal, duplicate/out-of-order event, past-due recovery, confirmed period-end cancel, full invoice refund, partial anomaly, and dispute fixtures all produce linked facts. Missing stable transaction IDs or refund/cancel capability yields NOT_RUN/nonzero and leaves `SUBSCRIPTION_BILLING` OFF.

- [ ] **Step 7: Record and commit subscription facts**

```powershell
git diff --check
git add backend/alembic/versions/20260710_0018_subscription_facts.py backend/app/core/provider_contracts.py backend/app/models/subscription_invoice.py backend/app/models/subscription_cancel_intent.py backend/app/models/subscription_plan.py backend/app/models/user_subscription.py backend/app/models/subscription_credit_grant.py backend/app/models/__init__.py backend/app/services/subscription_service.py backend/app/services/account_merge_credit_service.py backend/app/routers/subscriptions.py backend/app/schemas/subscription.py frontend/src/stores/subscription.ts backend/tests/test_subscription_facts_migration.py backend/tests/test_subscription_lifecycle.py backend/tests/test_subscription_billing.py backend/tests/test_account_merge_lineage.py backend/tests/integration/test_creem_subscription_lifecycle.py docs/ai-worklog.md
git commit -m "feat: normalize subscription payment facts"
```

**Work Package 2 exit:** Empty-database migration reaches `0018`; real PostgreSQL concurrency proves no overspend or duplicate grants; Creem test-mode facts prove catalog, checkout, signed replay, lifecycle, reversals, disputes, and cancellation where the provider exposes a verified contract. Any missing refund creation or stable subscription transaction contract keeps the affected checkout/refund capability OFF and the corresponding external gate NOT_RUN.

---

## Work Package 3 — Durable Generation And Delivery

### Task 16: Add Durable Job Facts And Atomically Switch Order Creation

**Files:**
- Create: `backend/app/models/generation_job.py`
- Create: `backend/app/models/generation_attempt.py`
- Create: `backend/app/models/qa_verdict.py`
- Create: `backend/app/services/order_transaction_service.py`
- Create: `backend/alembic/versions/20260710_0019_generation_jobs.py`
- Create: `backend/tests/test_generation_job_migration.py`
- Create: `backend/tests/test_generation_state_constraints.py`
- Create: `backend/tests/test_order_transaction.py`
- Modify: `backend/app/models/media_asset.py`
- Modify: `backend/app/models/order.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/services/order_creation_service.py`
- Modify: `backend/app/services/generation_stage_service.py`
- Modify: `backend/app/services/media_deletion_service.py`
- Modify: `backend/app/services/account_merge_credit_service.py`
- Modify: `backend/tests/test_media_deletion.py`
- Modify: `backend/tests/test_account_merge_lineage.py`
- Modify: `backend/app/routers/orders.py`
- Modify: `backend/app/routers/admin.py`
- Modify: `backend/app/schemas/order.py`
- Modify: `backend/tests/test_order_creation_service.py`
- Modify: `backend/tests/test_generation_stage_service.py`
- Modify: `backend/tests/test_admin_management_routes.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: PostgreSQL-authoritative job facts, payload version `generation-job.v1`, and the required order + reservation + job + outbox transaction.
- Consumes: Task 12 generic outbox/idempotency/catalog facts and Task 13 reservation primitives. Consumed by Tasks 17-20 and Partner Invite settlement in Task 21.

- [ ] **Step 1: Write migration, uniqueness, and transition tests**

```python
class GenerationJobStatus(StrEnum):
    QUEUED = "QUEUED"
    ACTIVE = "ACTIVE"
    RECONCILING = "RECONCILING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class GenerationAttemptStatus(StrEnum):
    PREPARED = "PREPARED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    UNKNOWN = "UNKNOWN"
    FINISHED = "FINISHED"
    FAILED = "FAILED"
```

Tests require `0019.down_revision == 20260710_0018`, one nonterminal job per order, one attempt number and stable client request ID per job, positive fencing token, lease/heartbeat consistency, immutable QA verdict rows, IDs-only JSON payloads, declared compatibility version, and a validated foreign key from `media_assets.job_id` after both tables exist. Every newly queued job also stores immutable server-derived `api_deployment_id`, `runtime_bundle_id`, and `expected_worker_image_digest`; missing/client-overridden stamps fail with zero order side effects. Rewrite `test_order_creation_service.py` from external URL/Gatekeeper helper inputs to owned ACTIVE asset IDs and the atomic transaction contract. Order tests require same key/hash to return the stored response, same key/different hash to return 409, and rollback to leave zero order/reservation/job/outbox rows. Make the temporary 7a `generation_stage_service.py` projection strict over an explicit known-state map and rewrite its test so an unknown state raises/blocks instead of silently becoming QUEUED; Task 30 deletes this compatibility projection after zero references. Static and route tests prove `backend/app/routers/admin.py` no longer imports/calls `run_order_generation` or `enqueue_generate_order`, creates URL-backed probe orders, sends legacy `generate_order`, or exposes inline/regenerate execution; retired Admin probe/regenerate calls return 410 with zero order/reservation/job/outbox/ledger changes. Merge tests prove any `QUEUED | ACTIVE | RECONCILING` job, `PREPARED | SUBMITTING | SUBMITTED | UNKNOWN` attempt, active/stale lease without completed recovery, or unsettled reservation blocks account merge even when the reservation is already CAPTURED. A terminal job is mergeable only after debit/refund, QA publication, entitlement, and deletion-reference settlement agree. A concurrent merge-versus-Worker claim/complete race yields one fully serialized result with no mixed owner, ledger, grant, or download projection.

- [ ] **Step 2: Run tests and confirm current order/task columns are insufficient**

```powershell
python -m unittest backend.tests.test_generation_job_migration backend.tests.test_generation_state_constraints backend.tests.test_order_transaction -v
```

Expected: FAIL because `orders.task_id` and in-process status updates cannot encode attempt boundaries, leases, fencing, unknown external state, verdict history, or durable publication.

- [ ] **Step 3: Add expand-only tables and database guards**

The generic `outbox_events` table already exists from `0016`. `generation_jobs` stores order, active generation, status, retry counts, repair count, next retry, lease owner, per-execution `lease_claim_id`, lease expiry, heartbeat, fencing token, payload version, immutable API deployment/bundle/expected Worker-digest stamps, and settlement/error facts. Legacy rows may be nullable during expand/backfill, but the database/service guard rejects a new queued `generation-job.v1` without all three stamps; Task 28 proves zero missing stamp before Generation can leave OFF. `generation_attempts` stores attempt kind `INITIAL | REPAIR | INFRA_RETRY`, stable client request ID, provider job ID, timestamps, cost/currency, result asset, and exact status. `qa_verdicts` stores checker/model/schema versions, strict decision, reasons, metrics, response hash, and candidate asset; UPDATE/DELETE is denied. Register a generation reference resolver in `media_deletion_service.py`: PREPARED/QUEUED/ACTIVE/SUBMITTING/SUBMITTED/UNKNOWN/RECONCILING attempts block source deletion; only a terminal job with completed release/refund settlement and revoked grants can release the reference. Extend `test_media_deletion.py` to prove both branches.

Use partial unique indexes for a nonterminal job per order and one active lease. Add constraints rather than Python-only validation. Do not remove legacy task/status columns before Task 30.

Retire the current Admin probe/regenerate execution routes in 7a rather than preserving a privileged second generation engine. Remove URL-backed probe-order construction, inline `run_order_generation()`, legacy `enqueue_generate_order()`/`generate_order`, and task-ID patching from the router. Operational acceptance uses Task 29's ordinary Google-user linked chain; bounded QA repair uses the same durable job/attempt service. If a future Admin retry is needed, it must be a separately approved command that transitions the locked durable job under the same stamps, lease/fence, reservation, QA, and settlement contracts—this plan does not add it.

- [ ] **Step 4: Implement the one atomic order transaction and switch the router**

```python
@dataclass(frozen=True)
class CreateOrderCommand:
    user_id: UUID
    idempotency_key: str
    request_hash: str
    asset_ids: tuple[UUID, ...]
    product_policy: OrderPolicySnapshot
    funding_policy: OrderFundingPolicySnapshot
    credit_cost: int


async def create_order_transaction(db: AsyncSession, command: CreateOrderCommand) -> AcceptedOrder:
    # No Redis, HTTP, storage, QA, Creem, or Provider call is permitted here.
    request = await begin_idempotent_request(
        db, user_id=command.user_id, endpoint="orders.create",
        key=command.idempotency_key, request_hash=command.request_hash,
    )
    if request.completed_response is not None:
        return AcceptedOrder.model_validate(request.completed_response)
    await lock_and_validate_active_assets(db, command.user_id, command.asset_ids)
    order = Order.from_command(command)
    db.add(order)
    await db.flush()
    reservation = await reserve_credits(
        db, user_id=command.user_id, order_id=order.id,
        amount=command.credit_cost, idempotency_key=f"order:{request.id}",
        funding_policy=command.funding_policy, now=datetime.now(timezone.utc),
    )
    runtime = require_server_runtime_execution_stamp()
    job = GenerationJob.queued(
        order_id=order.id,
        payload_version=1,
        api_deployment_id=runtime.api_deployment_id,
        runtime_bundle_id=runtime.runtime_bundle_id,
        expected_worker_image_digest=runtime.worker_image_digest,
    )
    db.add(job)
    await db.flush()
    db.add(OutboxEvent.generation_created(job_id=job.id, order_id=order.id))
    accepted = AcceptedOrder(
        order_id=order.id, status="QUEUED", status_url=f"/api/v1/orders/{order.id}"
    )
    request.complete(accepted.model_dump(mode="json"))
    order.reservation_id = reservation.id
    order.generation_job_id = job.id
    return accepted
```

After identity, owned ACTIVE assets, Gatekeeper, catalog price, and spendable balance pass, one PostgreSQL transaction consumes the canonical request hash, creates order/pricing/policy snapshots, reserves credits, creates one stamped job, and inserts one `GENERATION_JOB_CREATED.v1` IDs-only outbox event. `require_server_runtime_execution_stamp()` reads verified server configuration/runtime identity; request JSON/header/query cannot supply or override it, and a missing/mismatched bundle/digest fails before order creation. The router requires `Idempotency-Key`, returns HTTP 202 with only `order_id`, `status=QUEUED`, and `status_url`; insufficient credits returns 402 with zero rows. Disable the old immediate-deduct and direct Redis/inline dispatch writer before this switch.

Extend `merge_credit_accounts()` after the generation tables exist. It locks job, attempt, lease, reservation, QA, entitlement, grant, and media-reference rows in the documented global order. Any nonterminal/UNKNOWN/incompletely settled fact rejects the merge; only a terminal, fully settled graph may rebind mutable current order/asset/entitlement projections while immutable job/attempt/ledger facts retain original owner plus merge lineage. Worker claim/completion uses the same account/job fencing order so merge and Worker cannot commit cross-owner state.

- [ ] **Step 5: Establish public compatibility projection**

During 7a, detailed job state is written first and projected to the existing public order enum. Add `settlement_status`, `delivery_status`, and these user states without exposing Provider internals:

```text
QUEUED -> GENERATING -> QA_PENDING -> READY
QA_PENDING -> REPAIRING -> QA_PENDING
FAILED | CANCELLED | UNKNOWN_EXTERNAL_STATE | CONSENT_REVIEW_REQUIRED
```

When `deleted_at` exists, user projection is `DELETED`; financial and job facts remain intact.

- [ ] **Step 6: Verify migration, atomicity, and state constraints on PostgreSQL**

```powershell
Push-Location backend
$env:DATABASE_URL=$env:TEST_DATABASE_URL
& ..\.venv\Scripts\python.exe scripts/migrate_db.py
& ..\.venv\Scripts\python.exe -m unittest tests.test_generation_job_migration tests.test_generation_state_constraints tests.test_order_transaction -v
Pop-Location
```

Expected: revision `20260710_0019`; all tests PASS; direct invalid SQL transitions fail; concurrent order requests do not overspend; no Redis/Provider call occurs before commit.

- [ ] **Step 7: Record and commit durable job schema and atomic order switch**

```powershell
git diff --check
git add backend/alembic/versions/20260710_0019_generation_jobs.py backend/app/models/generation_job.py backend/app/models/generation_attempt.py backend/app/models/qa_verdict.py backend/app/models/media_asset.py backend/app/models/order.py backend/app/models/__init__.py backend/app/services/order_transaction_service.py backend/app/services/order_creation_service.py backend/app/services/generation_stage_service.py backend/app/services/media_deletion_service.py backend/app/services/account_merge_credit_service.py backend/app/routers/orders.py backend/app/routers/admin.py backend/app/schemas/order.py backend/tests/test_generation_job_migration.py backend/tests/test_generation_state_constraints.py backend/tests/test_order_transaction.py backend/tests/test_order_creation_service.py backend/tests/test_generation_stage_service.py backend/tests/test_media_deletion.py backend/tests/test_account_merge_lineage.py backend/tests/test_admin_management_routes.py docs/ai-worklog.md
git commit -m "feat: transact orders into durable generation jobs"
```

### Task 17: Implement Outbox Dispatch, Worker Claim, Lease, Heartbeat, Fencing, And OCI Runtime

**Files:**
- Create: `backend/app/services/outbox_service.py`
- Create: `backend/app/services/generation_job_service.py`
- Create: `backend/app/services/job_lease_service.py`
- Create: `backend/Dockerfile.worker`
- Create: `backend/requirements.lock.txt`
- Create: `backend/scripts/worker_entrypoint.py`
- Create: `backend/tests/test_outbox_dispatch.py`
- Create: `backend/tests/test_worker_leases.py`
- Create: `backend/tests/test_worker_heartbeat.py`
- Create: `backend/tests/integration/test_outbox_worker_recovery.py`
- Create: `requirements.in`
- Modify: `requirements.txt`
- Modify: `backend/app/core/task_queue.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/worker_tasks.py`
- Modify: `docker-compose.yml`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: deterministic ARQ job `generation:v1:{job_id}`, payload `{job_id,payload_version}`, and public Worker heartbeat bound to the release bundle.
- External requirement: Production needs a user-approved overseas long-running Docker-capable host. Vercel Functions, `BackgroundTasks`, or inline execution are prohibited substitutes.

- [ ] **Step 1: Write crash, duplicate, lease, heartbeat, and fencing tests**

```python
# backend/tests/test_worker_leases.py
class WorkerLeaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_stale_fencing_token_cannot_write(self) -> None:
        first = await self.claim(worker_id="worker-a", claim_id=uuid4())
        await self.expire(first)
        second = await self.claim(worker_id="worker-b", claim_id=uuid4())
        with self.assertRaises(StaleWorkerFence):
            await self.complete(
                first.job_id, claim_id=first.claim_id, fencing_token=first.fencing_token
            )
        await self.complete(
            second.job_id, claim_id=second.claim_id, fencing_token=second.fencing_token
        )
```

Add tests for commit-before-publish crash, publish-before-mark crash, deterministic Redis dedupe, `FOR UPDATE SKIP LOCKED`, heartbeat every 30 seconds, 120-second lease, same-worker duplicate delivery not incrementing the fence or invalidating the active execution, distinct claim IDs being mutually exclusive, expired heartbeat rejection, kill switch at worker entry and before every Provider side effect, only PREPARED work requeued after lease expiry, SUBMITTING work moved to reconciliation, three infrastructure retries with exponential backoff, DLQ settlement once, and old payload rejection. Static runtime tests require the Worker function list to contain only durable v1 jobs/schedules: no `generate_live_portrait`, legacy `generate_order`, `session_service`, inline order execution, or URL payload path remains.

- [ ] **Step 2: Run focused tests and confirm inline/legacy execution fails**

```powershell
python -m unittest backend.tests.test_outbox_dispatch backend.tests.test_worker_leases backend.tests.test_worker_heartbeat -v
```

Expected: FAIL because the current worker accepts only order IDs and has no database claim, fencing, heartbeat, version, or crash-safe outbox.

- [ ] **Step 3: Implement publisher and claim protocol**

```python
@dataclass(frozen=True)
class GenerationJobMessage:
    job_id: UUID
    payload_version: int = 1


async def publish_pending_outbox(db: AsyncSession, redis: ArqRedis, *, limit: int) -> PublishResult:
    events = list((await db.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.status == OutboxStatus.PENDING, OutboxEvent.next_attempt_at <= utcnow())
        .order_by(OutboxEvent.created_at)
        .with_for_update(skip_locked=True)
        .limit(limit)
    )).all())
    published: list[UUID] = []
    for event in events:
        message = event.to_queue_message()
        await redis.enqueue_job(
            message.function_name, *message.args, _job_id=event.dedupe_key,
        )
        event.mark_published(utcnow())
        published.append(event.id)
    return PublishResult(event_ids=tuple(published))


async def claim_generation_job(
    db: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    claim_id: UUID,
    lease_seconds: int = 120,
) -> JobLease:
    job = await db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
    if job is None or job.status not in {GenerationJobStatus.QUEUED, GenerationJobStatus.ACTIVE}:
        raise JobNotClaimable(job_id)
    now = utcnow()
    if job.lease_expires_at is not None and job.lease_expires_at > now:
        if job.lease_owner == worker_id and job.lease_claim_id == claim_id:
            return JobLease.from_job(job)
        raise JobAlreadyLeased(job_id)
    if await has_submitting_or_unknown_attempt(db, job.id):
        job.status = GenerationJobStatus.RECONCILING
        raise JobRequiresReconciliation(job.id)
    job.status = GenerationJobStatus.ACTIVE
    job.lease_owner = worker_id
    job.lease_claim_id = claim_id
    job.fencing_token += 1
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    return JobLease.from_job(job)


async def heartbeat_job(
    db: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    claim_id: UUID,
    fencing_token: int,
) -> JobLease:
    job = await db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
    now = utcnow()
    if (
        job is None
        or job.lease_owner != worker_id
        or job.lease_claim_id != claim_id
        or job.fencing_token != fencing_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= now
    ):
        raise StaleWorkerFence(job_id)
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=120)
    return JobLease.from_job(job)
```

Redis carries no image, user, order payload, price, or secret. Each handler invocation generates one UUID `claim_id`; claim, heartbeat, Provider boundary, and completion carry the same claim ID plus fencing token. A repeat of the same invocation is idempotent, while a different delivery cannot steal an unexpired lease even on the same Worker process. A stale Worker is unable to write completion or accounting facts. Remove Live Portrait and legacy session hooks from `worker.py`, `worker_tasks.py`, and `task_queue.py`; the retired 410 router has no Worker function. Existing `generate_order` messages are drained or isolated before enabling `generate_order_v1`; old Workers cannot consume v1 messages.

- [ ] **Step 4: Build a dedicated Worker image and truthful readiness**

Generate both API and Worker hash-locked transitive dependency sets in the same clean Python 3.11 Linux environment using the verified available `pip-tools==7.5.3`. Copy the current direct API requirements into new `requirements.in`; root `requirements.txt` becomes generated output because Vercel installs that exact root filename. Keep `backend/requirements.txt` as the Worker/server direct-dependency input and generate its separate lock:

```powershell
python -m pip install pip-tools==7.5.3
python -m piptools compile --generate-hashes --resolver=backtracking --output-file requirements.txt requirements.in
python -m piptools compile --generate-hashes --resolver=backtracking --output-file backend/requirements.lock.txt backend/requirements.txt
python -m pip install --require-hashes -r requirements.txt
python -m pip install --require-hashes -r backend/requirements.lock.txt
```

`backend/Dockerfile.worker` installs with `python -m pip install --require-hashes -r requirements.lock.txt`, runs only the ARQ Worker plus dispatcher/retention schedules, and never starts Uvicorn. Vercel's root API build consumes the generated hash-bearing `requirements.txt`; the committed file contains no floating `>=` constraints. `/version` and Worker heartbeat publish git SHA, image digest supplied at deployment, deployment ID, schema revision, payload min/max, config hash, and target/current feature snapshot hashes. Readiness fails for missing PostgreSQL, Redis, private storage, strict QA runtime, provider configuration, or a heartbeat older than 120 seconds. CI regenerates both lock files in the same pinned Linux resolver image and fails on any diff, so the same source SHA cannot resolve different API/Worker dependency graphs.

- [ ] **Step 5: Verify crash recovery with real PostgreSQL and Redis**

```powershell
docker compose up -d postgres redis
$env:RUN_WORKER_INTEGRATION='1'
python -m unittest backend.tests.integration.test_outbox_worker_recovery -v
docker build --file backend/Dockerfile.worker --tag vowpic-worker:local backend
```

Expected: integration kills/restarts the Worker without duplicate Provider submission or settlement; image build succeeds. Production host deploy is still NOT_RUN unless an approved registry/host and exact deployment command are supplied.

- [ ] **Step 6: Record and commit Worker runtime**

```powershell
git diff --check
git add requirements.in requirements.txt backend/Dockerfile.worker backend/requirements.lock.txt backend/scripts/worker_entrypoint.py backend/app/services/outbox_service.py backend/app/services/generation_job_service.py backend/app/services/job_lease_service.py backend/app/core/task_queue.py backend/app/worker.py backend/app/worker_tasks.py docker-compose.yml backend/tests/test_outbox_dispatch.py backend/tests/test_worker_leases.py backend/tests/test_worker_heartbeat.py backend/tests/integration/test_outbox_worker_recovery.py docs/ai-worklog.md
git commit -m "feat: run generation through durable worker leases"
```

### Task 18: Implement Evolink Submission Boundaries And Unknown-State Reconciliation

**Files:**
- Create: `backend/app/services/generation_attempt_service.py`
- Create: `backend/app/services/evolink_reconciliation_service.py`
- Create: `backend/tests/test_provider_submission_boundary.py`
- Create: `backend/tests/test_evolink_reconciliation.py`
- Create: `backend/tests/test_single_generation_provider.py`
- Create: `backend/tests/integration/test_evolink_submission_reconciliation.py`
- Modify: `backend/app/services/evolink_service.py`
- Modify: `backend/app/services/generation_service.py`
- Modify: `backend/app/core/provider_contracts.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/runtime_checks.py`
- Modify: `backend/app/services/provider_workflow.py`
- Modify: `backend/app/services/media_asset_service.py`
- Modify: `backend/app/routers/media.py`
- Modify: `backend/app/worker_tasks.py`
- Modify: `backend/app/schemas/order.py`
- Modify: `backend/tests/test_evolink_provider.py`
- Modify: `backend/tests/test_generation_quality_policy.py`
- Modify: `backend/tests/test_photometric_qa_service.py`
- Modify: `backend/tests/test_runtime_config.py`
- Modify: `.env.example`
- Modify: `backend/.env.example`
- Delete: `backend/app/services/comfyui_service.py`
- Delete: `backend/app/services/wenwen_service.py`
- Delete: `backend/app/workflows/comfyui_base.zip`
- Delete: `backend/app/workflows/comfyui_base.json`
- Delete: `backend/app/workflows/comfyui_couple_inpaint.json`
- Delete: `backend/app/workflows/comfyui_cloud_base_minimal.json`
- Delete: `backend/app/workflows/comfyui_cloud_couple_minimal.json`
- Delete: `backend/app/workflows/comfyui_live_portrait.json`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: `PREPARED -> SUBMITTING -> SUBMITTED` CAS, stable internal client request ID, provider task query after a known task ID, and explicit `UNKNOWN_EXTERNAL_STATE`.
- Verified blocker: current Evolink generation returns an asynchronous task ID and task lookup accepts that task ID; no documented idempotency key or queryable client-correlation field has been established. A timeout before receiving task ID is therefore unsafe to replay.

- [ ] **Step 1: Write Provider boundary and unknown-state tests**

```python
class ProviderSubmissionBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_before_task_id_is_never_reposted(self) -> None:
        self.provider.submit.side_effect = httpx.ReadTimeout("uncertain")
        await self.worker.run(self.job_id)
        attempt = await self.load_attempt()
        self.assertEqual(attempt.status, GenerationAttemptStatus.UNKNOWN)
        self.assertEqual((await self.load_job()).status, GenerationJobStatus.RECONCILING)
        await self.reaper.run()
        self.provider.submit.assert_awaited_once()
```

Add tests for flag check before SUBMITTING, exact job bundle/deployment/Worker-digest stamps, durable attempt before HTTP, known task ID polling, transient query failure without resubmission, Provider terminal failure classification, task/result schema validation, grant TTL/read limit, cost recording, admin retry denied until confirmed not accepted, and audited retry after such confirmation. Rewrite the four existing Wenwen-coupled tests named in Files before deleting `wenwen_service.py`: remove only image-generation/fallback imports and assertions, retain independently typed text/vision QA assertions against their real adapter, and update runtime config tests so only exact Evolink can satisfy `GENERATION_ENGINE`. Do not preserve a production image fallback or a dead compatibility module to make collection green. `test_single_generation_provider.py` scans all real callers and requires production image generation to resolve exactly Evolink: no Wenwen/ComfyUI generation import, workflow, fallback model, runtime enum, or hidden Admin/Worker branch remains; `LLM_PROVIDER=wenwen` may remain only for separately typed text/vision QA and cannot satisfy an image-generation interface. Invalid `GENERATION_ENGINE` or any value other than exact `evolink` fails readiness. Live Portrait remains retired/410. Prove only the INITIAL candidate-producing attempt captures the reservation; every REPAIR asserts the same reservation is already CAPTURED by that initial attempt, never changes capture provenance/ledger/balance, and fails before Provider HTTP if the invariant is absent.

Provider-fetch tests bind every grant to `provider=evolink`, purpose, asset/job/attempt, target API deployment, runtime bundle, 600-second TTL, at most three reads, and revocation. During staged acceptance, Evolink receives only an opaque HTTPS URL on the formal origin served by the already promoted private-compatible baseline for the same runtime bundle; that baseline validates the target deployment through the registered activation mapping before streaming bytes. The route is the sole maintenance/edge-deny exception, performs no user action, and is protected by the grant token—not a Cookie, Vercel deployment-protection bypass, broad IP exception, or project secret. Wrong serving role/bundle/target/provider/purpose, expiry, fourth read, revocation, redirect, alternate Host, or logs containing the raw token fail. The sandbox integration must prove Evolink can fetch this route from its own network before Generation cohort opens.

- [ ] **Step 2: Run tests and confirm current direct submission fails**

```powershell
python -m unittest backend.tests.test_provider_submission_boundary backend.tests.test_evolink_reconciliation backend.tests.test_evolink_provider backend.tests.test_single_generation_provider -v
```

Expected: FAIL because current Provider flow lacks a durable SUBMITTING boundary and can only reason from in-memory request outcome.

- [ ] **Step 3: Implement durable submission and known-task reconciliation**

```python
async def submit_generation_attempt(
    db: AsyncSession,
    *,
    job_id: UUID,
    fencing_token: int,
) -> GenerationAttempt:
    job = await lock_job_with_fence(db, job_id, fencing_token)
    await require_worker_capability(
        db,
        Capability.GENERATION,
        deployment_id=job.api_deployment_id,
        runtime_bundle_id=job.runtime_bundle_id,
        worker_image_digest=current_worker_image_digest(),
        user_id=job.user_id,
    )
    attempt = GenerationAttempt.prepared(
        job_id=job.id, attempt_number=job.next_attempt_number,
        client_request_id=uuid.uuid4(), provider="evolink",
    )
    db.add(attempt)
    await db.flush()
    grants = await issue_job_asset_grants(db, job=job, attempt=attempt)
    attempt.mark_submitting()
    await db.commit()  # durable UNKNOWN boundary before Provider side effect
    try:
        response = await evolink_client.submit(grants)
    except httpx.TimeoutException:
        await mark_attempt_unknown(db, attempt.id, reason="submit_response_lost")
        await db.commit()
        return await load_attempt(db, attempt.id)
    await mark_attempt_submitted(db, attempt.id, response.task_id, response.cost)
    if attempt.kind is GenerationAttemptKind.INITIAL:
        await capture_reservation(
            db, reservation_id=job.reservation_id, provider_attempt_id=attempt.id,
            idempotency_key=f"capture:{attempt.id}",
        )
    else:
        await assert_repair_uses_initial_capture(
            db,
            reservation_id=job.reservation_id,
            initial_attempt_id=job.initial_attempt_id,
        )
    await db.commit()
    return await load_attempt(db, attempt.id)


async def reconcile_evolink_attempt(
    db: AsyncSession,
    *,
    attempt_id: UUID,
    fencing_token: int,
) -> ReconciliationResult:
    attempt = await lock_attempt_with_fence(db, attempt_id, fencing_token)
    if attempt.provider_job_id is None:
        return ReconciliationResult.unresolved("provider_task_id_absent")
    fact = await evolink_client.get_task(attempt.provider_job_id)
    validated = EvolinkTaskFact.model_validate(fact, strict=True)
    await apply_evolink_task_fact(db, attempt=attempt, fact=validated)
    await db.commit()
    return ReconciliationResult.from_fact(validated)
```

Before HTTP, re-evaluate Generation against the locked job stamps and running Worker digest, issue job-bound private asset grants whose public URL is the exact formal provider-grant origin described above, and commit `SUBMITTING`. This is permitted only after Task 29 has promoted and registered the same-runtime private-compatible baseline; an unbound staged deployment is never exposed to Evolink. After the INITIAL attempt returns a task ID, commit `SUBMITTED` and capture the reservation once with immutable initial-attempt provenance. A REPAIR submits only after verifying that exact capture and never calls capture again. If the request fails before any possible acceptance, apply the classified retry policy. If acceptance is ambiguous and no task ID exists, persist UNKNOWN/RECONCILING and stop. If task ID exists, query by task ID until a validated terminal fact; never infer success from a non-empty URL.

- [ ] **Step 4: Encode the Provider capability gate instead of fabricating safety**

Add code-versioned fact `EVOLINK_SUBMISSION_RECONCILIATION = VERIFIED | UNVERIFIED` to `provider_contracts.py`. It becomes VERIFIED only in a code/release change that binds an official contract hash plus sandbox evidence proving either a Provider idempotency key or a client correlation value queryable after a lost response. With current evidence it is UNVERIFIED, so `GENERATION` cannot enter `ACCEPTANCE_COHORT` or `ON`, and Production generation evidence remains NOT_RUN. Known-task reconciliation may still be implemented and tested; it does not solve the lost-response case.

- [ ] **Step 5: Verify sandbox contract when Provider capability becomes available**

```powershell
$env:RUN_EVOLINK_SANDBOX='1'
python -m unittest backend.tests.integration.test_evolink_submission_reconciliation -v
```

Expected today: NOT_RUN/nonzero with reason `provider_correlation_unverified`. Expected before opening Generation: test deliberately loses the POST response, finds or deduplicates the same Provider task without a second generation, and records one capture.

- [ ] **Step 6: Record and commit Provider safety boundary**

```powershell
git diff --check
git add backend/app/services/generation_attempt_service.py backend/app/services/evolink_reconciliation_service.py backend/app/services/evolink_service.py backend/app/services/generation_service.py backend/app/core/provider_contracts.py backend/app/core/config.py backend/app/core/runtime_checks.py backend/app/services/provider_workflow.py backend/app/services/media_asset_service.py backend/app/routers/media.py backend/app/worker_tasks.py backend/app/schemas/order.py .env.example backend/.env.example backend/app/services/comfyui_service.py backend/app/services/wenwen_service.py backend/app/workflows/comfyui_base.zip backend/app/workflows/comfyui_base.json backend/app/workflows/comfyui_couple_inpaint.json backend/app/workflows/comfyui_cloud_base_minimal.json backend/app/workflows/comfyui_cloud_couple_minimal.json backend/app/workflows/comfyui_live_portrait.json backend/tests/test_provider_submission_boundary.py backend/tests/test_evolink_reconciliation.py backend/tests/test_single_generation_provider.py backend/tests/test_evolink_provider.py backend/tests/test_generation_quality_policy.py backend/tests/test_photometric_qa_service.py backend/tests/test_runtime_config.py backend/tests/integration/test_evolink_submission_reconciliation.py docs/ai-worklog.md
git commit -m "feat: stop unsafe provider resubmission"
```

### Task 19: Enforce Strict QA, Immutable Verdicts, And Two Bounded Repair Attempts

**Files:**
- Create: `backend/app/schemas/qa.py`
- Create: `backend/app/services/qa_verdict_service.py`
- Create: `backend/app/services/generation_repair_service.py`
- Create: `backend/tests/test_strict_qa_schema.py`
- Create: `backend/tests/test_generation_repairs.py`
- Create: `backend/tests/integration/test_strict_qa_runtime.py`
- Modify: `backend/app/services/llm_service.py`
- Modify: `backend/app/services/qa_service.py`
- Modify: `backend/app/services/qa_pipeline.py`
- Modify: `backend/app/services/qa_rules.py`
- Modify: `backend/app/services/provider_workflow.py`
- Modify: `backend/app/services/generation_policy.py`
- Modify: `backend/app/services/analytics_reporting_service.py`
- Modify: `backend/tests/test_generation_quality_policy.py`
- Modify: `backend/tests/test_analytics_reporting_service.py`
- Delete: `backend/app/services/generation_credit_policy.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: strict `PASS | REPAIR | REJECT` verdicts, immutable per-candidate evidence, and at most two candidate-producing repairs after the initial candidate.

- [ ] **Step 1: Reverse fail-open expectations and write strict schema tests**

```python
class QaDecision(StrEnum):
    PASS = "PASS"
    REPAIR = "REPAIR"
    REJECT = "REJECT"


class StrictQaVerdict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    decision: QaDecision
    required_checks: RequiredChecks
    reasons: tuple[str, ...]
    scores: QaScores
```

Tests prove string `"false"` is rejected instead of truthy, every required check must exist, enums/JSON/scores are strict, timeout/vision/embedding outage never PASS, candidate bytes/checksum/asset belong to the same attempt, repair reason codes are bounded, repair three is refused, and all candidates/verdicts remain inspectable. Rewrite `analytics_reporting_service.py` and its existing test to derive QA/repair/cost metrics only from normalized generation attempts and immutable QA verdicts; `Order.generation_params.debug.image_edit_rounds` and coarse legacy `OrderStatus` are not analytics authority or fallback. Task 16 has removed the order-creation importer, Task 18 has deleted ComfyUI, and this task removes the Provider/test importer; after the Admin/ops importers were removed in Task 13, delete `generation_credit_policy.py` and prove zero reverse imports. Financial tests prove the initial attempt captures once, both repair submissions preserve the initial `provider_attempt_id` capture provenance and create no new debit/capture, and a repair refuses before Provider HTTP if reservation status/provenance is not the expected CAPTURED initial attempt. Replace the existing fail-open tests that allow vision-system warning delivery with fail-closed assertions; do not weaken or delete the risk they expose.

- [ ] **Step 2: Run focused tests and reproduce the existing truthiness/fallback defects**

```powershell
python -m unittest backend.tests.test_strict_qa_schema backend.tests.test_generation_repairs backend.tests.test_generation_quality_policy backend.tests.test_generation_stage_service -v
```

Expected: FAIL; current `bool(data.get("passed"))` accepts a non-empty `"false"` string, and vision-error/fallback paths can still produce delivery.

- [ ] **Step 3: Implement strict technical and semantic verdict persistence**

Validate downloaded bytes, magic, decode, size, pixels, dimensions, blank/duplicate output, subject count, identity, face/limb integrity, age lock, style/attire, composition, exposure, prohibited content, and watermark state. Persist the private candidate and checksum before semantic QA. A missing dependency makes Worker readiness false; `QA_STRICT=true` is invariant, not a feature flag. Store only necessary scores/reasons/versions/hash, never face embeddings or raw sensitive inputs in logs.

- [ ] **Step 4: Implement bounded repair and ledger-derived failure settlement**

```python
async def decide_next_generation_action(
    db: AsyncSession,
    *,
    job_id: UUID,
    verdict_id: UUID,
) -> Literal["READY_FOR_DELIVERY", "CREATE_REPAIR", "FAIL_AND_SETTLE"]:
    job = await lock_generation_job(db, job_id)
    verdict = await load_immutable_verdict(db, verdict_id)
    if verdict.job_id != job.id or verdict.candidate_asset_id is None:
        raise QaLineageMismatch(verdict_id)
    if verdict.decision is QaDecision.PASS:
        job.status = GenerationJobStatus.ACTIVE
        job.public_projection = "QA_PENDING"
        return "READY_FOR_DELIVERY"
    if verdict.decision is QaDecision.REPAIR and job.repair_attempts < 2:
        repair = GenerationAttempt.repair_from_verdict(job=job, verdict=verdict)
        db.add(repair)
        job.repair_attempts += 1
        job.public_projection = "REPAIRING"
        return "CREATE_REPAIR"
    await fail_and_settle_generation(
        db, job=job, reason_codes=verdict.reasons,
        idempotency_key=f"qa-failure:{job.id}",
    )
    return "FAIL_AND_SETTLE"
```

One initial candidate plus no more than two candidate-producing repairs is allowed. Infrastructure retries that produce no candidate are tracked separately. Each repair has a targeted reason code, new attempt, candidate asset, and verdict, but it reuses the already captured commercial authorization and never changes reservation capture provenance or ledger balance. After the limit or a hard reject, fail the order, append exactly one refund against that initial capture, and expose no candidate. QA outage eventually fails/refunds after infrastructure retry exhaustion; it never leaves an immortal GENERATING order.

- [ ] **Step 5: Verify strict QA and real Worker readiness**

```powershell
python -m unittest backend.tests.test_strict_qa_schema backend.tests.test_generation_repairs backend.tests.test_generation_quality_policy backend.tests.test_generation_stage_service -v
$env:RUN_QA_RUNTIME_INTEGRATION='1'
python -m unittest backend.tests.integration.test_strict_qa_runtime -v
```

Expected: all local tests PASS; integration loads the real vision/embedding runtime and fails nonzero if unavailable.

- [ ] **Step 6: Record and commit strict QA**

```powershell
git diff --check
git add backend/app/schemas/qa.py backend/app/services/qa_verdict_service.py backend/app/services/generation_repair_service.py backend/app/services/llm_service.py backend/app/services/qa_service.py backend/app/services/qa_pipeline.py backend/app/services/qa_rules.py backend/app/services/provider_workflow.py backend/app/services/generation_policy.py backend/app/services/analytics_reporting_service.py backend/app/services/generation_credit_policy.py backend/tests/test_strict_qa_schema.py backend/tests/test_generation_repairs.py backend/tests/test_generation_quality_policy.py backend/tests/test_analytics_reporting_service.py backend/tests/integration/test_strict_qa_runtime.py docs/ai-worklog.md
git commit -m "fix: make generation QA fail closed"
```

### Task 20: Build Private Master, Fixed Variants, Trial Watermark, Entitlement, And Settlement Delivery

**Files:**
- Modify: `backend/app/services/delivery_asset_service.py`
- Create: `backend/app/services/private_download_service.py`
- Create: `backend/tests/test_delivery_assets.py`
- Create: `backend/tests/test_trial_watermark.py`
- Create: `backend/tests/test_delivery_settlement.py`
- Create: `backend/tests/integration/test_private_delivery.py`
- Modify: `backend/app/services/postprocess_service.py`
- Modify: `backend/app/services/trial_access_service.py`
- Modify: `backend/app/services/media_asset_service.py`
- Modify: `backend/app/services/retention_service.py`
- Modify: `backend/app/routers/orders.py`
- Modify: `backend/app/schemas/order.py`
- Modify: `backend/tests/test_order_public_contract.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: one private 3:4 master, fixed paid variants, one trial watermark asset, derived settlement status, and authorized byte streaming.

- [ ] **Step 1: Write delivery completeness and leak-prevention tests**

```python
PAID_VARIANTS = {
    "2:3", "3:2", "3:4", "4:5", "9:16", "1:1",
}


class DeliveryAssetsTest(unittest.IsolatedAsyncioTestCase):
    async def test_ready_requires_master_and_every_variant(self) -> None:
        result = await self.pipeline(self.passing_candidate, omit="9:16")
        self.assertNotEqual(result.public_status, "READY")

    async def test_watermark_failure_never_returns_master(self) -> None:
        self.watermarker.side_effect = WatermarkError("render failed")
        response = await self.create_trial_delivery()
        self.assertNotIn(self.master_asset_id, response.visible_asset_ids)
```

Add tests for actual download/decode/dimensions of each variant; postprocess technical QA; trial maximum 900x1125 and exactly 3:4; no Provider/candidate/master/object-key/permanent URL in public DTOs; cross-user denial; missing/revoked/refunded/disputed entitlement denial; `Cache-Control: private, no-store`; every ordinarily paid order creates one ACTIVE entitlement whose immutable funding rows exactly match captured reservation allocations; paid trial unlock idempotently binds one order and one purchase/grant without consuming purchased credits; retention extends from original READY time and never shrinks; durable PENDING intent recovery finds/deletes a crash-left deterministic object; and one settlement fact per terminal path. Rewrite the existing `test_order_public_contract.py` to require only asset IDs/roles/status plus authorized streaming routes and to reject serialized source/identity-crop/preview/final URLs.

- [ ] **Step 2: Run focused tests and confirm current original-image fallback fails**

```powershell
python -m unittest backend.tests.test_delivery_assets backend.tests.test_trial_watermark backend.tests.test_delivery_settlement -v
```

Expected: FAIL because existing postprocess/watermark paths may fall back to original images and order responses expose direct URLs.

- [ ] **Step 3: Implement validated delivery artifacts**

```python
async def build_delivery_assets(
    db: AsyncSession,
    *,
    job_id: UUID,
    passing_candidate_asset_id: UUID,
    fencing_token: int,
) -> DeliveryAssetSet:
    job = await lock_job_with_fence(db, job_id, fencing_token)
    verdict = await require_passing_verdict(db, job.id, passing_candidate_asset_id)
    candidate = await read_private_validated_asset(db, passing_candidate_asset_id)
    rendered = render_master_and_variants(candidate.content, PAID_VARIANTS)
    pending = await create_pending_delivery_assets(
        db, job=job, rendered=rendered, deterministic_keys=True,
    )
    await db.commit()  # durable object intents exist before the first storage write
    try:
        stored = await store_and_technical_qa_every_asset(db, pending, rendered)
        if job.order_policy.is_trial:
            preview = render_watermarked_trial(stored.master, max_size=(900, 1125))
            stored = stored.with_trial_preview(await store_and_technical_qa_trial(db, job, preview))
        await activate_complete_delivery_set(db, job=job, assets=stored, verdict=verdict)
        await db.commit()
        return stored
    except Exception:
        await db.rollback()
        await mark_pending_delivery_for_deletion(db, pending)
        await db.commit()
        raise
```

Download and validate the passing private candidate, create a 3:4 master, produce all six named variants, compute checksums and deterministic object keys, then commit all PENDING asset intents before any object I/O. Store each privately and run technical QA on bytes/dimensions. For trial, produce a separate watermarked 3:4 image no larger than 900x1125. A stale-intent reconciler compares each deterministic key/checksum, either resumes the exact set or marks every present object `PENDING_DELETE`; it also scans the dedicated acceptance/delivery prefix for objects without a matching intent. Any missing/failed write, decode, technical QA, watermark, or process crash leaves the order non-READY and retries/fails through the job policy; no fallback asset is substituted and no orphan is silently ignored.

- [ ] **Step 4: Implement entitlement-only private streaming and final settlement**

For an ordinarily paid order, before READY the settlement service locks the CAPTURED reservation and creates one ACTIVE `order_entitlement` plus immutable `order_entitlement_fundings` copied from every captured allocation; an allocation mismatch, reversed/frozen source, or replay with different funding fails closed. `POST /api/v1/orders/{order_id}/unlock` is only for an owned READY trial: it requires `Idempotency-Key` and one active paid pack/subscription grant not already used for another trial unlock. It creates one ACTIVE entitlement bound to the exact order/purchase/root grant without decrementing the new grant's credits; replay returns the same entitlement, while a different order/source conflicts. Refund/dispute/reversal revokes every entitlement funded by the affected root grant, and retention extends from the original READY timestamp without ever shortening.

`GET /api/v1/orders/{order_id}/assets/{asset_id}/download` resolves Cookie session, canonical user, order ownership, asset role/status, active entitlement, purchase/invoice/refund/dispute lineage, and feature flag. At Task 20 it authorizes only non-Partner orders and fails closed for legacy/remote-join markers rather than importing the future consent schema; Task 21 adds the consent-case check after `partner_consent_cases` exists. It streams bytes without object key or permanent URL and audits final download. READY is committed only after artifact set, entitlement/watermark, verdict, and accounting settlement are mutually consistent.

- [ ] **Step 5: Verify private delivery with real storage**

```powershell
python -m unittest backend.tests.test_delivery_assets backend.tests.test_trial_watermark backend.tests.test_delivery_settlement -v
$env:RUN_PRIVATE_STORAGE_INTEGRATION='1'
python -m unittest backend.tests.integration.test_private_delivery -v
```

Expected: unit tests PASS; integration uploads, downloads, decodes, and deletes the complete private set while unauthenticated/cross-user/refunded requests fail.

- [ ] **Step 6: Record and commit delivery closure**

```powershell
git diff --check
git add backend/app/services/delivery_asset_service.py backend/app/services/private_download_service.py backend/app/services/postprocess_service.py backend/app/services/trial_access_service.py backend/app/services/media_asset_service.py backend/app/services/retention_service.py backend/app/routers/orders.py backend/app/schemas/order.py backend/tests/test_delivery_assets.py backend/tests/test_trial_watermark.py backend/tests/test_delivery_settlement.py backend/tests/test_order_public_contract.py backend/tests/integration/test_private_delivery.py docs/ai-worklog.md
git commit -m "feat: deliver private verified generation assets"
```

**Work Package 3 exit:** Empty-database migration reaches `0019`; real PostgreSQL/Redis/private-storage/QA tests prove durable execution and fail-closed delivery. Generation remains OFF until an approved long-running Worker host is deployed and Evolink lost-response reconciliation is verified. With current Provider evidence, this package can become code-complete but cannot satisfy the Production Generation gate.

---

## Work Package 4 — Web Product And Partner Invite

### Task 21: Replace Anonymous Remote Join With Authenticated Partner Invite And Consent

**Files:**
- Create: `backend/app/models/partner_invite.py`
- Create: `backend/app/models/partner_invite_event.py`
- Create: `backend/app/models/partner_consent_case.py`
- Create: `backend/app/schemas/partner_invite.py`
- Create: `backend/app/services/partner_invite_service.py`
- Create: `backend/app/routers/partner_invites.py`
- Create: `backend/alembic/versions/20260710_0020_partner_consent.py`
- Create: `backend/tests/test_partner_invite_migration.py`
- Create: `backend/tests/test_partner_invite_service.py`
- Create: `backend/tests/test_partner_consent_settlement.py`
- Create: `backend/tests/integration/test_partner_invite_rls.py`
- Modify: `backend/app/routers/session.py`
- Modify: `backend/app/services/session_service.py`
- Modify: `backend/app/services/media_deletion_service.py`
- Modify: `backend/app/services/account_merge_credit_service.py`
- Modify: `backend/app/services/private_download_service.py`
- Modify: `backend/tests/test_media_deletion.py`
- Modify: `backend/tests/test_account_merge_lineage.py`
- Modify: `backend/tests/test_delivery_settlement.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/routers/__init__.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: authenticated Web-only invite/consent API and typed cancellation settlement.
- Replaces: legacy anonymous `/session/*` behavior, short IDs, public image URLs, empty QR fields, and runtime DDL. Legacy table/code stays read-only until Task 30.

- [ ] **Step 1: Write migration, token, role, state, and settlement tests**

```python
class PartnerInviteStatus(StrEnum):
    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    CONSENTED = "CONSENTED"
    COMPLETED = "COMPLETED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PartnerConsentCaseStatus(StrEnum):
    OPEN = "OPEN"
    SETTLED_DELETION_PENDING = "SETTLED_DELETION_PENDING"
    CANCELLED_AND_DELETED = "CANCELLED_AND_DELETED"
```

Tests require `0020.down_revision == 20260710_0019`; token entropy is 32 random bytes and only its keyed hash is stored; TTL is exactly 86400 seconds; host/partner are distinct verified Google identities; one token binds once; database constraints reject skipped/reversed transitions; host cannot read partner source; partner cannot read host source/order/credits/result; expired/completed/revoked tokens fail closed; every transition is audited/rate-limited; and legacy `/session/*` returns 410 without runtime DDL.

- [ ] **Step 2: Run focused tests and confirm anonymous remote join fails the contract**

```powershell
python -m unittest backend.tests.test_partner_invite_migration backend.tests.test_partner_invite_service backend.tests.test_partner_consent_settlement -v
```

Expected: FAIL because current sessions use an anonymous eight-character identifier, 30-minute TTL, JSON/public URLs, no identity or consent, and caller-controlled state changes.

- [ ] **Step 3: Add `0020` tables, RLS, constraints, and events**

Create typed `partner_invites`, `partner_invite_events`, and `partner_consent_cases`. Invite rows store host, optional bound partner, token hash, purpose, state, expiry, consent timestamps, order/job references, and optimistic version. Source media references point to Task 9 media assets, never URLs. Host and partner RLS projections expose only the fields each role needs; service-only operations own state changes, grants, cases, and deletion. Extend `account_merge_credit_service.py` now—not earlier—to refuse any OPEN consent case and to rebind only terminal, fully settled invite references; `test_account_merge_lineage.py` proves no partial merge around an OPEN case. Extend `private_download_service.py` now to require a still-valid completed consent lineage for Partner-derived outputs and make OPEN/withdrawn/revoked consent deny future download; `test_delivery_settlement.py` proves local orders remain unaffected and Partner revocation fails closed.

- [ ] **Step 4: Implement authenticated endpoints and one transition service**

```text
POST /api/v1/partner-invites
POST /api/v1/partner-invites/accept
GET  /api/v1/partner-invites/{invite_id}
POST /api/v1/partner-invites/{invite_id}/consent
POST /api/v1/partner-invites/{invite_id}/revoke
POST /api/v1/partner-invites/{invite_id}/withdraw
```

```python
async def transition_partner_invite(
    db: AsyncSession,
    *,
    invite_id: UUID,
    actor_user_id: UUID,
    expected_version: int,
    command: PartnerInviteCommand,
) -> PartnerInviteSnapshot:
    invite = await db.scalar(
        select(PartnerInvite).where(PartnerInvite.id == invite_id).with_for_update()
    )
    if invite is None:
        raise PartnerInviteNotFound(invite_id)
    if invite.version != expected_version:
        raise PartnerInviteVersionConflict(invite.version)
    actor_role = invite.role_for(actor_user_id)
    next_state = authorize_partner_command(invite, actor_role, command, utcnow())
    before = invite.snapshot()
    apply_partner_command(invite, command, next_state)
    invite.version += 1
    db.add(PartnerInviteEvent.from_transition(
        invite=invite, actor_user_id=actor_user_id, before=before,
        command=command, request_id=current_request_id(),
    ))
    await db.flush()
    return invite.snapshot_for(actor_role)
```

Creation returns the raw token/join URL once; later reads never return it. Token is not identity; accept requires partner Google session. Partner uploads through the authenticated private media service and explicitly consents to one order. Host owns pricing, order, and result; partner owns only the partner source and receives no result entitlement.

- [ ] **Step 5: Implement withdrawal across job, accounting, grants, and deletion**

Before QUEUED, withdrawal deletes partner assets and prevents submission. After QUEUED, create one OPEN case, project `CONSENT_REVIEW_REQUIRED`, revoke grants/downloads, and block any new Provider submit. PREPARED releases; SUBMITTING/UNKNOWN/SUBMITTED reconciles and requests Provider cancellation before settlement; captured/no-delivery refunds once. READY revokes access/deletes all derived assets and refunds only when audit proves no successful final download. The case transition is strictly two-phase: `OPEN -> SETTLED_DELETION_PENDING -> CANCELLED_AND_DELETED`. Provider handling, unique ledger settlement, and grant/download revocation must finish before entering SETTLED_DELETION_PENDING. The consent-case resolver still blocks every OPEN case, but for SETTLED_DELETION_PENDING it authorizes only assets owned by that exact case so the leased deletion Worker can remove them; unrelated assets remain blocked. Only after every source/derived object verifies DELETED/NOT_FOUND (404/410) does the service close the case as CANCELLED_AND_DELETED. Extend tests for OPEN blocking, case-owned deletion authorization, unrelated-asset denial, deletion retry, and final close. A closed invite cannot be resumed.

- [ ] **Step 6: Verify PostgreSQL RLS and settlement integration**

```powershell
$env:RUN_POSTGRES_INTEGRATION='1'
python -m unittest backend.tests.test_partner_invite_migration backend.tests.test_partner_invite_service backend.tests.test_partner_consent_settlement backend.tests.integration.test_partner_invite_rls -v
```

Expected: all tests PASS; no cross-role object exposure and exactly one settlement per withdrawal branch.

- [ ] **Step 7: Record and commit Partner Invite backend**

```powershell
git diff --check
git add backend/alembic/versions/20260710_0020_partner_consent.py backend/app/models/partner_invite.py backend/app/models/partner_invite_event.py backend/app/models/partner_consent_case.py backend/app/models/__init__.py backend/app/schemas/partner_invite.py backend/app/services/partner_invite_service.py backend/app/services/media_deletion_service.py backend/app/services/account_merge_credit_service.py backend/app/services/private_download_service.py backend/app/routers/partner_invites.py backend/app/routers/session.py backend/app/services/session_service.py backend/app/routers/__init__.py backend/tests/test_partner_invite_migration.py backend/tests/test_partner_invite_service.py backend/tests/test_partner_consent_settlement.py backend/tests/test_media_deletion.py backend/tests/test_account_merge_lineage.py backend/tests/test_delivery_settlement.py backend/tests/integration/test_partner_invite_rls.py docs/ai-worklog.md
git commit -m "feat: add authenticated partner consent flow"
```

### Task 22: Establish OpenAPI As The Frontend Type Source And Add A Locked Test Toolchain

**Files:**
- Create: `backend/scripts/export_openapi.py`
- Create: `openapi/openapi.json`
- Create: `frontend/src/generated/api.d.ts`
- Create: `frontend/src/services/http.ts`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/tests/setup.ts`
- Create: `frontend/tests/unit/http.spec.ts`
- Create: `backend/tests/test_openapi_contract.py`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/tsconfig.json`
- Modify: `frontend/src/utils/api.ts`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: one committed OpenAPI snapshot, generated TypeScript types, Vitest component harness, Playwright browser harness, and an exact breaking-drift gate.
- Rule: generated types are never hand-edited and pages cannot create a second DTO source.

- [ ] **Step 1: Pin compatible frontend versions and scripts**

Run from repository root:

```powershell
npm --prefix frontend install --save-exact vue@3.4.21 typescript@5.3.3 vite@5.2.8 vue-tsc@1.8.27
npm --prefix frontend install --save-dev --save-exact vitest@1.6.1 @vue/test-utils@2.4.6 jsdom@24.1.3 @playwright/test@1.61.1 @axe-core/playwright@4.12.1 openapi-typescript@7.13.0
npm --prefix frontend run playwright:install
```

Add scripts: `typecheck`, `openapi:generate`, `test:unit`, `test:a11y`, `test:e2e`, `playwright:install` (`playwright install --with-deps chromium firefox`), and `build:web`. Node 20 is the only CI runtime. Every CI/Preview browser job runs `npm --prefix frontend run playwright:install` from the exact locked `@playwright/test@1.61.1` install (or uses the exact `mcr.microsoft.com/playwright:v1.61.1-noble` image); it never calls a floating npx package. A runner with npm packages but no matching browser binaries fails before tests. Do not install floating `latest`.

- [ ] **Step 2: Write contract-export and HTTP harness tests**

Tests require stable operation IDs for auth/media/orders/catalog/checkout/subscription/download/invite; no OpenID/visitor ownership/public image URL fields; strict order `settlement_status`; and deterministic snapshot output. `http.spec.ts` asserts `credentials:'include'`, CSRF on unsafe methods, request ID propagation, one refresh retry, no bearer header, field-error preservation, and idempotency key reuse.

- [ ] **Step 3: Run tests and confirm there is no current frontend test/type authority**

```powershell
python -m unittest backend.tests.test_openapi_contract -v
npm --prefix frontend run typecheck
npm --prefix frontend run test:unit
```

Expected: FAIL until exporter, generated types, test harness, and typed HTTP service exist. Record any pre-existing TypeScript failure instead of weakening compiler settings.

- [ ] **Step 4: Export deterministically and generate types**

```python
# backend/scripts/export_openapi.py
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app


VOLATILE_KEYS = {"x-generated-at"}


def canonicalize(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: canonicalize(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [canonicalize(item) for item in value]
    return value


def export_openapi() -> None:
    output = REPOSITORY_ROOT / "openapi" / "openapi.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(canonicalize(app.openapi()), ensure_ascii=False, indent=2, sort_keys=True)
    output.write_text(payload + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    export_openapi()
```

Generate with the pinned local binary:

```powershell
python backend/scripts/export_openapi.py
npm --prefix frontend run openapi:generate
git add -N -- openapi/openapi.json frontend/src/generated/api.d.ts
git diff --check -- openapi/openapi.json frontend/src/generated/api.d.ts
git diff -- openapi/openapi.json frontend/src/generated/api.d.ts
```

On first generation, intent-to-add makes untracked files visible and the review must inspect the full generated diff; run both generators twice and require identical SHA-256 hashes before staging. After snapshots are committed, CI uses `git diff --exit-code -- openapi/openapi.json frontend/src/generated/api.d.ts` as the drift gate. Zero collected tests, skipped mandatory suites, or generation drift is failure.

- [ ] **Step 5: Add typed transport foundation**

Create the HTTP implementation in `frontend/src/services/http.ts` during this task and migrate `frontend/src/utils/api.ts` to a thin compatibility adapter. It owns Cookie/CSRF/request ID/field errors/idempotency behavior; it contains no product fallbacks. Service-specific calls land in Task 23.

- [ ] **Step 6: Verify toolchain, snapshot, and build**

```powershell
python -m unittest backend.tests.test_openapi_contract -v
python backend/scripts/export_openapi.py
npm --prefix frontend run openapi:generate
git add -N -- openapi/openapi.json frontend/src/generated/api.d.ts
git diff --check -- openapi/openapi.json frontend/src/generated/api.d.ts
git diff -- openapi/openapi.json frontend/src/generated/api.d.ts
npm --prefix frontend run typecheck
npm --prefix frontend run test:unit
npm --prefix frontend run build:web
```

Expected: all commands PASS; no skipped/zero-test suite.

- [ ] **Step 7: Record and commit the contract toolchain**

```powershell
git diff --check
git add backend/scripts/export_openapi.py backend/tests/test_openapi_contract.py openapi/openapi.json frontend/src/generated/api.d.ts frontend/src/services/http.ts frontend/src/utils/api.ts frontend/vitest.config.ts frontend/playwright.config.ts frontend/tests/setup.ts frontend/tests/unit/http.spec.ts frontend/package.json frontend/package-lock.json frontend/tsconfig.json .github/workflows/ci.yml docs/ai-worklog.md
git commit -m "test: establish typed web contract gates"
```

### Task 23: Refactor The H5 Main Journey Onto Typed Services And Real Server Facts

**Files:**
- Create: `backend/app/schemas/account_export.py`
- Create: `backend/app/services/account_export_service.py`
- Create: `backend/app/routers/account_data.py`
- Create: `backend/tests/test_account_export.py`
- Modify: `backend/app/routers/__init__.py`
- Modify: `frontend/src/services/auth.ts`
- Create: `frontend/src/services/media.ts`
- Create: `frontend/src/services/orders.ts`
- Create: `frontend/src/services/billing.ts`
- Create: `frontend/src/services/downloads.ts`
- Create: `frontend/src/composables/useCreateDraft.ts`
- Create: `frontend/src/composables/useOrderPolling.ts`
- Create: `frontend/src/components/create/CreateFlowShell.vue`
- Create: `frontend/src/components/create/SubjectCountStep.vue`
- Create: `frontend/src/components/create/PortraitUploadStep.vue`
- Create: `frontend/src/components/create/StyleStep.vue`
- Create: `frontend/src/components/create/ReviewAndSubmitStep.vue`
- Create: `frontend/src/components/preview/GenerationProgress.vue`
- Create: `frontend/src/components/preview/PreviewGallery.vue`
- Create: `frontend/src/components/preview/PurchasePanel.vue`
- Create: `frontend/src/components/preview/DownloadPanel.vue`
- Create: `frontend/src/components/preview/FailureRecovery.vue`
- Create: `frontend/src/components/payment/CheckoutSummary.vue`
- Create: `frontend/src/components/payment/PackageSelector.vue`
- Create: `frontend/src/components/payment/PaymentStatus.vue`
- Create: `frontend/src/components/home/HomeHero.vue`
- Create: `frontend/src/components/home/HowItWorks.vue`
- Create: `frontend/src/components/home/StyleGallery.vue`
- Create: `frontend/src/components/home/PricingSection.vue`
- Create: `frontend/src/components/home/FaqSection.vue`
- Create: `frontend/tests/unit/useCreateDraft.spec.ts`
- Create: `frontend/tests/unit/createSubmission.spec.ts`
- Create: `frontend/tests/unit/GenerationProgress.spec.ts`
- Create: `frontend/tests/unit/PaymentStatus.spec.ts`
- Create: `frontend/tests/unit/HomePricing.spec.ts`
- Create: `frontend/tests/unit/AdminRoute.spec.ts`
- Create: `frontend/tests/unit/AdminOrders.spec.ts`
- Modify: `frontend/src/pages/index/index.vue`
- Modify: `frontend/src/pages/create/index.vue`
- Modify: `frontend/src/pages/preview/preview.vue`
- Modify: `frontend/src/components/PaymentModal.vue`
- Modify: `frontend/src/stores/order.ts`
- Modify: `frontend/src/stores/subscription.ts`
- Modify: `frontend/src/pages/orders/orders.vue`
- Modify: `frontend/src/pages/account/index.vue`
- Modify: `frontend/src/pages/admin/AdminLayout.vue`
- Modify: `frontend/src/pages/admin/index.vue`
- Modify: `frontend/src/pages/admin/orders.vue`
- Modify: `frontend/src/pages.json`
- Modify: `docs/ai-worklog.md`

**Interfaces:**

```ts
export function useCreateDraft(): CreateDraftController
export function useOrderPolling(orderId: string): OrderPollingController
export const mediaService: { uploadPortraits(files: File[]): Promise<UploadBatchRead> }
export const orderService: { createOrder(input: CreateOrderInput, key: string): Promise<AcceptedOrder> }
export const billingService: { getCatalog(): Promise<BillingCatalog>; createCheckout(input: CheckoutInput, key: string): Promise<CheckoutRead> }
export const downloadService: { download(orderId: string, assetId: string): Promise<Blob> }
```

- [ ] **Step 1: Write user-behavior component and composable tests**

Cover One person/Couple only; login return restores template/count/text but no File bytes; authenticated upload preserves the exact `UploadBatchRead {batch_id,assets}` envelope and exposes asset IDs/per-file errors from its `assets` field; order creation preserves exact `AcceptedOrder {order_id,status,status_url}` rather than pretending to return a full Order; submit displays exact price/retention/trial terms; double click and timeout reuse one idempotency key; reload follows `status_url` to fetch the server Order projection; polling stops/retries visibly instead of swallowing error; Home/Pricing and Payment use the same server catalog and show unavailable rather than static packs; settlement copy derives only from `NOT_CHARGED | CAPTURED | REFUNDED | RECONCILING`; private download uses Blob response; and internal Provider/Gatekeeper/artifact/QA IDs never render. OpenAPI/TypeScript tests compare these service return types to the generated operation response types with no hand-written unwrap DTO. Admin tests prove its route is lazy/separate and fetches no business data until the local session and database role check succeeds. `AdminRoute.spec.ts` also proves the Admin landing page no longer exposes URL-backed generation probes, candidate/public URL previews, task IDs, or regenerate actions. `AdminOrders.spec.ts` proves the registered Admin Orders page has no regenerate command/button, `task_id`, old `generation_rounds`, candidate/permanent URL resolution, or raw Provider/QA projection; it renders read-only normalized job/attempt/verdict/settlement summaries and requests any authorized thumbnail by private asset ID through the authenticated media endpoint. Backend account-export tests require the Cookie user, include merged immutable history and consent/media IDs, deny cross-user access, and exclude tokens, hashes, object keys, permanent URLs, raw Provider payloads, embeddings, and internal paths.

- [ ] **Step 2: Run focused tests and confirm current pages/fallbacks fail**

```powershell
npm --prefix frontend run test:unit -- tests/unit/useCreateDraft.spec.ts tests/unit/createSubmission.spec.ts tests/unit/GenerationProgress.spec.ts tests/unit/PaymentStatus.spec.ts tests/unit/HomePricing.spec.ts tests/unit/AdminRoute.spec.ts tests/unit/AdminOrders.spec.ts
```

Expected: FAIL because current large pages call URL/bearer-era APIs directly, use static catalog fallbacks, swallow polling failures, and infer settlement.

- [ ] **Step 3: Implement typed services and draft/polling state**

Services consume generated types only. `CreateDraft` persists style/template, one/couple, text, and return intent; it never persists image bytes. `createOrder` generates one key per deliberate submission and retains it through timeout/retry until a definitive response. Polling maps server facts to user copy and exits permanent loading on terminal or recoverable error.

Implement `GET /api/v1/account/export` as an authenticated attachment using a strict `AccountExport` schema. `build_account_export()` queries the canonical user plus merge-linked immutable history and returns profile, identity provider names/created times, orders/settlement, ledger, purchases/refunds/disputes, subscriptions/invoices, Partner consent events, media role/status/checksum metadata, retention/deletion status, and audit references. It returns asset IDs rather than bytes/object keys; private images remain downloadable through their authorized endpoints. Apply an explicit field allowlist and a final recursive forbidden-key scan before serialization. Account UI downloads this JSON and shows generated time/schema version.

```python
FORBIDDEN_EXPORT_KEYS = frozenset({
    "password", "token", "token_hash", "csrf_token_hash", "object_key",
    "provider_payload", "raw_payload", "embedding", "internal_path",
})


async def build_account_export(db: AsyncSession, user_id: UUID) -> AccountExport:
    linked_user_ids = await canonical_and_merged_user_ids(db, user_id)
    export = AccountExport(
        generated_at=utcnow(), schema_version="account-export.v1",
        profile=await export_profile(db, user_id),
        identities=await export_identity_metadata(db, linked_user_ids),
        orders=await export_orders_and_settlement(db, linked_user_ids),
        ledger=await export_ledger(db, linked_user_ids),
        billing=await export_billing_facts(db, linked_user_ids),
        consents=await export_partner_consent_facts(db, linked_user_ids),
        media=await export_media_metadata(db, linked_user_ids),
    )
    reject_forbidden_export_keys(export.model_dump(mode="json"), FORBIDDEN_EXPORT_KEYS)
    return export
```

- [ ] **Step 4: Split pages into focused components and remove false UI**

Page files retain route parsing, component composition, and navigation only. Split Home into Hero/How it works/Style gallery/Pricing/FAQ and make Pricing consume the same typed catalog as checkout. Keep Admin as a lazy, separate route; render/fetch nothing beyond auth state until the server confirms database Admin role. Remove the URL-backed generation probe/candidate gallery/task-ID contract from `admin/index.vue`. Rewrite the actively registered `admin/orders.vue` as read-only normalized operations UI: no Admin regenerate path, legacy task ID, public/candidate URL, debug rounds, or raw Provider fields; authorized media uses private asset-ID endpoints. Delete hardcoded price/success fallbacks, Direct/Studio unverified benefits, Leads, Live Portrait, local recommendations, and engineering labels. The failure panel shows real settlement, retry eligibility, request ID, and support only when a validated support channel exists. Orders/Account expose truthful transactions and retention, not raw provider facts.

- [ ] **Step 5: Verify unit, type, and H5 build gates**

```powershell
python -m unittest backend.tests.test_account_export -v
npm --prefix frontend run typecheck
npm --prefix frontend run test:unit
npm --prefix frontend run build:web
```

Expected: all commands PASS; no page-local API duplication or catalog fallback found by `rg`.

- [ ] **Step 6: Record and commit the real H5 main journey**

```powershell
git diff --check
git add backend/app/schemas/account_export.py backend/app/services/account_export_service.py backend/app/routers/account_data.py backend/app/routers/__init__.py backend/tests/test_account_export.py frontend/src/services/auth.ts frontend/src/services/media.ts frontend/src/services/orders.ts frontend/src/services/billing.ts frontend/src/services/downloads.ts frontend/src/composables/useCreateDraft.ts frontend/src/composables/useOrderPolling.ts frontend/src/components/create/CreateFlowShell.vue frontend/src/components/create/SubjectCountStep.vue frontend/src/components/create/PortraitUploadStep.vue frontend/src/components/create/StyleStep.vue frontend/src/components/create/ReviewAndSubmitStep.vue frontend/src/components/preview/GenerationProgress.vue frontend/src/components/preview/PreviewGallery.vue frontend/src/components/preview/PurchasePanel.vue frontend/src/components/preview/DownloadPanel.vue frontend/src/components/preview/FailureRecovery.vue frontend/src/components/payment/CheckoutSummary.vue frontend/src/components/payment/PackageSelector.vue frontend/src/components/payment/PaymentStatus.vue frontend/src/components/home/HomeHero.vue frontend/src/components/home/HowItWorks.vue frontend/src/components/home/StyleGallery.vue frontend/src/components/home/PricingSection.vue frontend/src/components/home/FaqSection.vue frontend/src/pages/index/index.vue frontend/src/pages/create/index.vue frontend/src/pages/preview/preview.vue frontend/src/components/PaymentModal.vue frontend/src/stores/order.ts frontend/src/stores/subscription.ts frontend/src/pages/orders/orders.vue frontend/src/pages/account/index.vue frontend/src/pages/admin/AdminLayout.vue frontend/src/pages/admin/index.vue frontend/src/pages/admin/orders.vue frontend/src/pages.json frontend/tests/unit/useCreateDraft.spec.ts frontend/tests/unit/createSubmission.spec.ts frontend/tests/unit/GenerationProgress.spec.ts frontend/tests/unit/PaymentStatus.spec.ts frontend/tests/unit/HomePricing.spec.ts frontend/tests/unit/AdminRoute.spec.ts frontend/tests/unit/AdminOrders.spec.ts docs/ai-worklog.md
git commit -m "feat: rebuild the typed web user journey"
```

### Task 24: Implement Partner Invite H5 And Two-Browser Permission Proof

**Files:**
- Create: `frontend/src/services/partnerInvites.ts`
- Create: `frontend/src/composables/usePartnerInvite.ts`
- Create: `frontend/src/components/partner/PartnerInviteCreate.vue`
- Create: `frontend/src/components/partner/PartnerInviteAccept.vue`
- Create: `frontend/src/components/partner/PartnerConsent.vue`
- Create: `frontend/src/components/partner/PartnerInviteStatus.vue`
- Create: `frontend/tests/unit/usePartnerInvite.spec.ts`
- Create: `frontend/e2e/partner-invite.spec.ts`
- Modify: `frontend/src/pages/create/index.vue`
- Modify: `frontend/src/pages/join/landing.vue`
- Modify: `frontend/src/pages.json`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: host create/revoke/status and partner accept/consent/upload/withdraw flows.
- Gate: `PARTNER_INVITE` remains OFF until a real Preview test with two Google-backed browser contexts passes.

- [ ] **Step 1: Write role, token, consent, refresh, and withdrawal UI tests**

Unit tests prove invite links contain only raw one-time token, both roles require Google session, partner sees no host asset/order/credit/result, host sees no partner source bytes, state survives refresh from server, expired/revoked links have no upload path, consent is explicit, withdrawal copy matches real settlement, and no QR/WeChat/mini-program term exists.

- [ ] **Step 2: Run focused tests and confirm legacy join UI fails**

```powershell
npm --prefix frontend run test:unit -- tests/unit/usePartnerInvite.spec.ts
```

Expected: FAIL because current join flow is anonymous, URL-based, and has no Google identity or consent state.

- [ ] **Step 3: Implement typed Partner Invite pages**

The host receives a copyable HTTPS link, not a platform QR contract. The partner signs in, accepts, consents, and uploads through `mediaService`. The host can submit only at CONSENTED and pays current couple pricing. Revoke/withdraw actions require confirmation and show actual case/settlement state. Refresh always rehydrates from the authenticated API.

- [ ] **Step 4: Add mandatory two-context Playwright flow**

```ts
test("host and partner complete one consent-bound couple order", async ({ browser }) => {
  const host = await browser.newContext({ storageState: process.env.HOST_AUTH_STATE });
  const partner = await browser.newContext({ storageState: process.env.PARTNER_AUTH_STATE });
  // Create, accept, consent, upload, submit, assert cross-role denial, revoke/cleanup.
});
```

Add a second case for post-QUEUE withdrawal, `CONSENT_REVIEW_REQUIRED`, settlement, and verified deletion. Use isolated Preview identities/resources; no mocked auth/storage/provider is accepted in the Preview gate.

- [ ] **Step 5: Verify component and Preview E2E gates**

```powershell
npm --prefix frontend run test:unit -- tests/unit/usePartnerInvite.spec.ts
$env:RUN_PREVIEW_E2E='1'
npm --prefix frontend run test:e2e -- e2e/partner-invite.spec.ts
```

Expected: unit PASS. Preview E2E PASS only with two real test identities and real sandbox resources; otherwise NOT_RUN/nonzero and flag stays OFF.

- [ ] **Step 6: Record and commit Partner Invite H5**

```powershell
git diff --check
git add frontend/src/services/partnerInvites.ts frontend/src/composables/usePartnerInvite.ts frontend/src/components/partner/PartnerInviteCreate.vue frontend/src/components/partner/PartnerInviteAccept.vue frontend/src/components/partner/PartnerConsent.vue frontend/src/components/partner/PartnerInviteStatus.vue frontend/src/pages/create/index.vue frontend/src/pages/join/landing.vue frontend/src/pages.json frontend/tests/unit/usePartnerInvite.spec.ts frontend/e2e/partner-invite.spec.ts docs/ai-worklog.md
git commit -m "feat: add consent-bound web partner invite"
```

### Task 25: Meet Accessibility, Responsive, Locale, Font, And Truthful Support Requirements

**Files:**
- Create: `frontend/src/assets/fonts/bodoni-moda-regular.woff2`
- Create: `frontend/src/assets/fonts/bodoni-moda-semibold.woff2`
- Create: `frontend/src/assets/fonts/jost-regular.woff2`
- Create: `frontend/src/assets/fonts/jost-semibold.woff2`
- Create: `frontend/src/assets/fonts/OFL.txt`
- Create: `frontend/src/styles/fonts.css`
- Create: `frontend/e2e/a11y-responsive.spec.ts`
- Create: `frontend/e2e/visual-regression.spec.ts`
- Create: `frontend/e2e/visual-regression.spec.ts-snapshots/home-desktop-chromium-linux.png`
- Create: `frontend/e2e/visual-regression.spec.ts-snapshots/create-mobile-chromium-linux.png`
- Create: `frontend/e2e/visual-regression.spec.ts-snapshots/orders-desktop-chromium-linux.png`
- Create: `frontend/e2e/visual-regression.spec.ts-snapshots/account-mobile-chromium-linux.png`
- Create: `frontend/e2e/visual-regression.spec.ts-snapshots/payment-dialog-desktop-chromium-linux.png`
- Create: `frontend/e2e/visual-regression.spec.ts-snapshots/home-reduced-motion-chromium-linux.png`
- Create: `frontend/tests/unit/PaymentDialog.a11y.spec.ts`
- Modify: `frontend/src/App.vue`
- Create: `frontend/src/styles/variables.css`
- Modify: `frontend/src/components/NavBar.vue`
- Modify: `frontend/src/components/CompareSlider.vue`
- Modify: `frontend/src/stores/i18n.ts`
- Modify: `frontend/index.html`
- Modify: `frontend/src/pages/legal/privacy.vue`
- Modify: `frontend/src/pages/legal/terms.vue`
- Modify: `frontend/src/pages/legal/refund.vue`
- Modify: `backend/app/services/legal_policy_service.py`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: English-default/CJK-optional locale-consistent UI, self-hosted licensed fonts, WCAG 2.2 AA target behavior, and support/legal copy that matches actual system facts.

- [ ] **Step 1: Add failing accessibility, viewport, locale, and font tests**

Test semantic heading/label/button/link/dialog roles, visible focus, route focus, dialog trap/Escape/restore, aria-live status/error, keyboard upload, keyboard CompareSlider, 44x44 targets, 16px mobile body, browser zoom allowed, reduced motion, image dimensions/lazy loading, and no color-only signal. Run at 375, 768, 1024, 1440 and mobile landscape; assert no horizontal overflow or fixed CTA/nav overlap. English is default, document `lang` updates with locale, USD/date/number formatting is locale-aware, and one page never mixes locale.

- [ ] **Step 2: Run focused tests and capture the current failures**

```powershell
npm --prefix frontend run test:unit -- tests/unit/PaymentDialog.a11y.spec.ts
npm --prefix frontend run test:a11y -- e2e/a11y-responsive.spec.ts
```

Expected: FAIL because current document language, remote fonts, dialogs/navigation, large-page semantics, and viewport behavior do not meet the fixed contract.

- [ ] **Step 3: Add verified self-hosted fonts and restrained visual tokens**

Acquire Bodoni Moda and Jost WOFF2 from their official upstream source during implementation, verify SIL Open Font License and checksums, commit the license, and reference only local assets. Do not use a runtime Google Fonts request. Apply `#F7F8FA`/warm ivory background, `#17191F` text, `#116A60` accent, white surfaces, and low-contrast borders; remove decorative glass/multicolor AI gradients. Visual snapshots are captured only after `document.fonts.ready` confirms the primary fonts loaded.

Generate the first reviewed Linux baselines once against the built protected Preview, then commit the six exact files listed above. A later `--update-snapshots` run is never an automatic fix: it requires screenshot review and a recorded reason.

```powershell
npm --prefix frontend run playwright:install
npm --prefix frontend run test:e2e -- e2e/visual-regression.spec.ts --update-snapshots
git add -N -- frontend/e2e/visual-regression.spec.ts-snapshots
git diff --binary -- frontend/e2e/visual-regression.spec.ts-snapshots
```

The first baseline is untracked, so a plain `git diff` is not evidence. Record each PNG SHA-256 and open every rendered snapshot plus its Playwright HTML comparison at the declared viewport after fonts are ready. A human reviewer must verify content, crop, focus state, locale, and absence of leaked personal/test data before staging; binary diff presence alone is insufficient. CI uses the committed baselines without `--update-snapshots`.

- [ ] **Step 4: Implement accessible navigation, dialogs, controls, and responsive layout**

Desktop and mobile both expose labeled Home/Create/Orders/Account. Use native interactive elements, one primary CTA per step, visible focus, focus management, aria-live progress, reduced motion, safe-area padding, and explicit media dimensions. Remove any `overflow-x:hidden` workaround that hides a real layout defect. CompareSlider has keyboard increments and announced values.

- [ ] **Step 5: Align legal/support pages with implemented facts**

Privacy/Terms/Refund state actual Cookie session, processors, retention, private storage, full pack refund boundary, subscription invoice refund boundary, partial-refund reconciliation, dispute/debt, deletion, cross-border processing, and inability to recall downloaded files. Record professional legal-review status truthfully; engineering does not claim certification. A support CTA is rendered only when runtime validation confirms a monitored `SUPPORT_EMAIL` or HTTPS support URL; otherwise show recovery steps without promising support.

- [ ] **Step 6: Verify accessibility, responsive, visual, type, and build**

```powershell
npm --prefix frontend run typecheck
npm --prefix frontend run test:unit
npm --prefix frontend run test:a11y -- e2e/a11y-responsive.spec.ts
npm --prefix frontend run test:e2e -- e2e/visual-regression.spec.ts
npm --prefix frontend run build:web
```

Expected: all tests PASS against the built Preview; console has no error; fonts are local and loaded; snapshots cover normal/reduced motion.

- [ ] **Step 7: Record and commit product-quality closure**

```powershell
git diff --check
git add frontend/src/assets/fonts/bodoni-moda-regular.woff2 frontend/src/assets/fonts/bodoni-moda-semibold.woff2 frontend/src/assets/fonts/jost-regular.woff2 frontend/src/assets/fonts/jost-semibold.woff2 frontend/src/assets/fonts/OFL.txt frontend/src/styles/fonts.css frontend/src/styles/variables.css frontend/src/App.vue frontend/src/components/NavBar.vue frontend/src/components/CompareSlider.vue frontend/src/stores/i18n.ts frontend/index.html frontend/src/pages/legal/privacy.vue frontend/src/pages/legal/terms.vue frontend/src/pages/legal/refund.vue backend/app/services/legal_policy_service.py frontend/e2e/a11y-responsive.spec.ts frontend/e2e/visual-regression.spec.ts frontend/e2e/visual-regression.spec.ts-snapshots/home-desktop-chromium-linux.png frontend/e2e/visual-regression.spec.ts-snapshots/create-mobile-chromium-linux.png frontend/e2e/visual-regression.spec.ts-snapshots/orders-desktop-chromium-linux.png frontend/e2e/visual-regression.spec.ts-snapshots/account-mobile-chromium-linux.png frontend/e2e/visual-regression.spec.ts-snapshots/payment-dialog-desktop-chromium-linux.png frontend/e2e/visual-regression.spec.ts-snapshots/home-reduced-motion-chromium-linux.png frontend/tests/unit/PaymentDialog.a11y.spec.ts docs/ai-worklog.md
git commit -m "fix: meet web accessibility and truthful policy contracts"
```

**Work Package 4 exit:** OpenAPI drift, typecheck, all component tests, H5 build, accessibility/responsive/visual tests, and the two-real-browser Partner Invite Preview flow PASS. If the monitored support channel or real Preview identities/resources are absent, the relevant UI promise stays hidden and Partner Invite stays OFF; build success alone does not satisfy this exit.

---

## Work Package 5 — Release, Migration, And Production Acceptance

### Task 26: Version The Gate Contract And Replace False-Green CI

**Files:**
- Create: `release/gates.json`
- Create: `release/change-impact.json`
- Create: `release/severity-contract.json`
- Create: `release/quality-cases.json`
- Create: `release/quality-rubric.json`
- Create: `release/activation-plan.json`
- Create: `release/runtime-contracts.json`
- Create: `scripts/release/aggregate_gates.py`
- Create: `backend/tests/test_release_contract.py`
- Create: `backend/tests/test_release_evidence.py`
- Create: `.github/workflows/integration.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.gitignore`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: versioned mandatory case IDs and exactly four gate statuses: `PASS | FAIL | NOT_RUN | NOT_APPLICABLE`.
- Rule: `release_ready` is logical AND over the exact mandatory case-ID set; missing, duplicate, skipped, cancelled, timed-out, stale, wrong-bundle, zero-test, unknown N/A, or unexpected case is failure.

- [ ] **Step 1: Write aggregator and workflow contract tests**

```python
# backend/tests/test_release_contract.py
class ReleaseContractTest(unittest.TestCase):
    def test_mandatory_case_set_must_match_exactly(self) -> None:
        contract = self.contract(case_ids={"auth", "billing", "generation"})
        evidence = self.evidence(case_ids={"auth", "billing"})
        result = aggregate(contract, evidence)
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.missing_case_ids, ["generation"])

    def test_not_run_never_counts_as_pass(self) -> None:
        self.assertEqual(aggregate_one(mandatory=True, status="NOT_RUN"), "FAIL")
```

Add tests for contract checksums, freshness, bundle equality, timeout/cancel/skip, allowed N/A allowlist, changed/unknown generation paths forcing `FULL_QUALITY`, final enabled capability refusing N/A, immutable evidence paths, secret redaction, and any false result producing nonzero exit. Workflow tests require zero-test failure, no fork secrets, pinned action/tool versions, no main-push production deploy/domain bind, and missing required secret causing job failure rather than a success message.

- [ ] **Step 2: Run tests and confirm current CI can report false success**

```powershell
python -m unittest backend.tests.test_release_contract backend.tests.test_release_evidence -v
```

Expected: FAIL; current CI has no versioned contract/aggregator, directly deploys main, installs floating Vercel CLI, treats a missing token as acceptable, and uses an anonymous session smoke.

- [ ] **Step 3: Define the immutable gate inputs**

`release/gates.json` names every PR, Preview, staged Production, formal-domain, observation, migration, privacy, commercial, quality, Partner Invite, Worker, and cleanup case with layer, mandatory flag, timeout, freshness, report schema, and explicit N/A eligibility. `release/change-impact.json` maps Provider/model/prompt/template/QA/pre/postprocess/watermark/model-asset paths and unparseable changes to `FULL_QUALITY`; only unrelated changes allow `CANARY_ONLY`. `release/severity-contract.json` maps unauthorized access/privacy/key/data loss/double-charge to P0 and real-flow/ledger/Worker/Outbox/webhook/deletion failures to P1; unknown production errors default P1.

`release/quality-cases.json` fixes six authorized cases: single template, single text, single outdoor text, local couple, Golden Anniversary, and Partner Invite remote couple. `release/quality-rubric.json` fixes identity, composition, attire/style, and naturalness/exposure scores 1-5, average at least 4.0, no dimension below 3, and any hard identity/safety/subject-count/technical defect as full failure. `release/activation-plan.json` fixes flag order and target snapshot. `release/runtime-contracts.json` fixes API/Worker/job payload compatibility versions and the exact source-file hashes that implement them; a release builder may not infer or accept a caller-provided free-form version.

- [ ] **Step 4: Replace CI with secret-free PR and protected Preview layers**

PR runs empty PostgreSQL migration, all backend tests, RLS/concurrency integration, OpenAPI drift, frontend type/unit/a11y/build, Worker image build, and the aggregator without external writes. Preview runs only from a trusted protected branch/environment with isolated PostgreSQL, Redis, Private Blob, Supabase identities, Creem test mode, and Provider sandbox. It executes the real linked flows, account data export, deletion, and two-context Partner Invite; mocks cannot produce Preview PASS.

- [ ] **Step 5: Verify local aggregate and workflow contracts**

```powershell
python -m unittest backend.tests.test_release_contract backend.tests.test_release_evidence -v
python scripts/release/aggregate_gates.py --contract release/gates.json --evidence-dir artifacts/release/local/01-ci --output artifacts/release/local/01-ci/aggregate.json
```

Expected: tests PASS. The sample aggregate intentionally exits nonzero until every mandatory local case report exists; that nonzero is the truthful expected baseline, not a test failure to suppress.

- [ ] **Step 6: Record and commit gate contracts**

```powershell
git diff --check
git add release/gates.json release/change-impact.json release/severity-contract.json release/quality-cases.json release/quality-rubric.json release/activation-plan.json release/runtime-contracts.json scripts/release/aggregate_gates.py backend/tests/test_release_contract.py backend/tests/test_release_evidence.py .github/workflows/ci.yml .github/workflows/integration.yml .gitignore docs/ai-worklog.md
git commit -m "ci: make release gates fail closed"
```

### Task 27: Define Immutable Bundle Identity And Append-Only Release Evidence

**Files:**
- Create: `backend/app/routers/runtime.py`
- Create: `backend/app/core/observability.py`
- Create: `backend/app/services/runtime_bundle_service.py`
- Create: `scripts/release/build_manifest.py`
- Modify: `scripts/release/build_runtime_bundle_id.py`
- Create: `scripts/release/register_bundle.py`
- Create: `scripts/release/collect_runtime_report.py`
- Create: `scripts/release/verify_bundle.py`
- Create: `scripts/release/append_evidence_index.py`
- Create: `release/bundle-manifest.schema.json`
- Create: `release/evidence-index.schema.json`
- Create: `backend/tests/test_version_route.py`
- Create: `backend/tests/test_release_bundle.py`
- Create: `backend/tests/test_observability_contract.py`
- Create: `.github/workflows/production-release.yml`
- Modify: `.github/workflows/integration.yml`
- Modify: `backend/app/routers/__init__.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/services/feature_flag_service.py`
- Modify: `backend/app/services/creem_event_service.py`
- Modify: `backend/app/services/outbox_service.py`
- Modify: `backend/app/services/generation_job_service.py`
- Modify: `backend/app/services/media_deletion_service.py`
- Modify: `vercel.json`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: immutable bundle manifest, sanitized API/Worker bundle reports, and a separate append-only evidence index.
- Pre-deploy runtime identity is role-discriminated. `SAFE_BASELINE` keeps Task 2's minimal formula. `COMMERCIAL_7A = SHA256(final source SHA + role + schema/payload/provider/model/policy/catalog/flag-contract/activation-plan hashes + Worker OCI digest + pinned tool contracts)`. `CONTRACT_7B` additionally binds `schema_before`, `schema_target`, contract-migration checksum, and pre-contract compatibility version. Every variant excludes Vercel deployment IDs, API build output, resolved OFF/target snapshots, live state, and final manifest hash, so the exact runtime ID exists before deployment and is injected into Worker plus each Vercel deployment runtime without mutating project environment later.
- Immutable bundle fields: runtime bundle ID, final source SHA, reproducible API build checksum, Preview ID, private-compatible baseline deployment ID, staged-target deployment ID, Worker image digest/deployment ID, Alembic revision, API/Worker/job payload compatibility, Provider/model/policy/catalog hashes, flag contract hash, pre-activation OFF snapshot hash, target snapshot hash, pinned tool versions, and gate-contract checksums.
- Append-only evidence fields: immutable bundle-manifest SHA-256, evidence type, case/run/attempt/deployment IDs, report SHA-256, produced/observed times, freshness result, reviewer/approval reference, and decision. Runtime current-flag snapshots, migrations, activation events, observation samples, and final decision live here; they never rewrite the bundle manifest.

- [ ] **Step 1: Write version, manifest, and mismatch tests**

Tests require `/api/v1/version` to expose only safe bundle facts, including the pre-injected runtime ID and platform-trusted `VERCEL_DEPLOYMENT_ID`; liveness proves process only; readiness verifies required dependencies and heartbeat. The builder is deterministic and discriminated by release role: SAFE_BASELINE rejects Worker/full-release inputs, while COMMERCIAL_7A and CONTRACT_7B require a digest-pinned Worker and their exact deployment-independent contracts. Every variant rejects deployment ID/API build output/resolved snapshot/live/evidence/final-manifest fields. Workflow tests prove the ID is computed before deployment, passed to the approved Worker deployment and every `vercel deploy --env RUNTIME_BUNDLE_ID=...`, read back from runtime, and never introduced by a post-deploy project-environment mutation or rebuild. Manifest build then requires that same runtime ID and rejects a mutable image tag without digest, unknown schema, missing/distinct-deployment violation, mismatched SHA/build/payload/config/flags, missing tool versions, stale Worker heartbeat, or Preview artifact substituted for Production. Tests reject future report checksums, observation results, activation results, or a mutable current snapshot inside the immutable bundle; they also reject changing an existing manifest path.

`register_bundle.py` first uploads the canonical final manifest to a content-addressed create-once Private evidence key, verifies the read-back hash, and CAS-registers runtime ID, manifest SHA, source SHA, API/Worker deployment IDs/roles, phase, and evidence prefix in `ReleaseActivation`; it never mutates deployment environment. Runtime code resolves this mapping after startup. An unregistered or mismatched deployment may expose liveness/version and operational readiness only; every non-OFF capability and side effect stays disabled. If a deploy succeeds before registration, rerun discovers and reuses only the exact source/runtime/build candidate; ambiguity fails closed. The protected Preview workflow uses the same canonical/signing primitives to publish a release-role-discriminated, create-once report only after every mandatory integration gate passes. Its key and payload bind repository/project, source SHA, Preview deployment ID, build checksum, schema, runtime-contract/gate hashes, report role (`commercial-7a` or later `contract-7b`), run/attempt, and PASS; a caller cannot publish or reinterpret it as Production evidence. `resolve-preview` accepts only the unique signed/read-back object with exact role/project/SHA and freshness. `append_evidence_index.py` writes a new canonical entry only, requires the final manifest hash, rejects duplicate/conflicting case IDs, and never opens the manifest for write. Within one job it may append a local canonical index; across jobs it must resolve the durable release/observation run from PostgreSQL and create immutable Private Blob entry objects, from which a finalizer deterministically reconstructs the index. Workflow tests start each phase with a fresh workspace/environment and require it to resolve final SHA, runtime bundle ID, deployment IDs, manifest hash, evidence prefix, and lease from the immutable manifest plus audited release row; another job's shell variable, local artifact path, or mutable `latest` alias is never authority. Observability tests require correlation fields and reject access/refresh tokens, CSRF secrets, full email, image bytes, embeddings, permanent object URLs, payment credentials, and internal filesystem paths. Evidence lives at:

`collect_runtime_report.py` requests liveness/readiness/version from one exact URL with a protected runner bypass when needed, records the platform-observed deployment ID/runtime ID/source/schema/snapshot fields, signs the sanitized canonical report, and fails on redirects or expected-coordinate mismatch. It never accepts response values as expected inputs and never prints the bypass secret. Worker reports come only from the approved host adapter's digest-bound heartbeat action.

```text
artifacts/release/<commit-sha>/<run-id>-<attempt>/<staged-production-deployment-id>/
  00-bundle-manifest.json
  evidence-index.ndjson
  01-ci/
  02-integration/
  03-production/
  04-review/
```

- [ ] **Step 2: Run focused tests and confirm runtime bundle identity is absent**

```powershell
python -m unittest backend.tests.test_version_route backend.tests.test_release_bundle backend.tests.test_observability_contract -v
```

Expected: FAIL because current API/Worker do not expose schema/payload/digest/flag hashes and current evidence can compare only a loose SHA.

- [ ] **Step 3: Implement sanitized runtime endpoints and deterministic manifest**

```python
@dataclass(frozen=True)
class PublicRuntimeBundle:
    git_sha: str
    runtime_bundle_id: str
    deployment_id: str
    schema_revision: str
    api_version: str
    worker_image_digest: str
    job_payload_min: int
    job_payload_max: int
    provider_policy_hash: str
    flag_contract_hash: str
    observed_current_flag_snapshot_hash: str
    target_flag_snapshot_hash: str
```

`observed_current_flag_snapshot_hash` is a runtime report field and is excluded from the immutable manifest; only its pre-activation OFF value and target contract are sealed. Runtime identity and final manifest identity are deliberately two levels: API/Worker/jobs/flags bind the pre-deploy runtime ID, while the post-deploy activation/evidence row binds the final manifest SHA containing real API/Worker/deployment/build facts. Bundle JSON is canonical/sorted and includes only immutable build/deployment/config/contract facts. The path is content-addressed and created once with exclusive semantics. Every later report is content-addressed separately and referenced by a newly appended `evidence-index.ndjson` entry bound to the exact final manifest SHA-256; no finalizer edits `00-bundle-manifest.json`.

- [ ] **Step 4: Implement correlated, redacted logs and mandatory operational metrics**

Structured events carry available `request_id`, internal/hashed `user_id`, `order_id`, `reservation_id`, `outbox_event_id`, `job_id`, `attempt_id`, `provider_job_id`, `artifact_id`, `purchase_id/payment_event_id`, `deployment_id`, and `git_sha`. Metrics cover auth/cross-user/upload rejection, Outbox backlog/age, queue latency, lease recovery, Provider latency/cost, QA reject/repair, watermark failure, download denial, webhook UNHANDLED, ledger reconciliation, deletion failures, and last successful cleanup. The redactor rejects access/refresh tokens, CSRF secrets, full emails, original image bytes, embeddings, permanent object URLs, payment credentials, and internal paths before emission. Liveness, readiness, and version remain separate.

- [ ] **Step 5: Add serialized build/stage workflow support without running it**

Define a non-reusable manual-only `workflow_dispatch` entry with required exact source SHA, GitHub Production Environment approval, and global `concurrency.cancel-in-progress: false`; explicitly forbid push/PR/schedule/repository dispatch/`workflow_call`. Pin Vercel CLI to `55.0.0`; require the target SHA to equal approved main HEAD and acquire the database release lease before secrets. The workflow can build once and deploy the same prebuilt output twice as distinct, unbound Production deployments, but Task 27 neither reads Production secrets nor runs it:

```powershell
npx --yes vercel@55.0.0 pull --yes --environment=production --token=$env:VERCEL_TOKEN
npx --yes vercel@55.0.0 build --prod --token=$env:VERCEL_TOKEN
$privateCompatibleBaselineUrl = npx --yes vercel@55.0.0 deploy --prebuilt --prod --skip-domain --env RUNTIME_BUNDLE_ID=$env:RUNTIME_BUNDLE_ID --token=$env:VERCEL_TOKEN
$stagedTargetUrl = npx --yes vercel@55.0.0 deploy --prebuilt --prod --skip-domain --env RUNTIME_BUNDLE_ID=$env:RUNTIME_BUNDLE_ID --token=$env:VERCEL_TOKEN
npx --yes vercel@55.0.0 inspect $privateCompatibleBaselineUrl --token=$env:VERCEL_TOKEN
npx --yes vercel@55.0.0 inspect $stagedTargetUrl --token=$env:VERCEL_TOKEN
```

The two deployment IDs must differ while source SHA and prebuilt checksum match. Do not build, deploy, or Promote in this task; these commands document the committed Task 29 workflow phases. Missing token, environment approval, bundle field, or exact SHA is a hard failure. Vercel Git auto production assignment, deploy hooks, and dashboard Promote remain externally disabled and are captured as reviewed evidence.

- [ ] **Step 6: Keep Worker Production deployment explicitly blocked until host approval**

Build and record the OCI digest in CI. Do not invent a registry, host, credential, or deployment command. Before executing Production, the user must approve one overseas long-running Docker host and an addendum must name the exact registry push, digest pin, deploy, rollback, secret injection, health, and log commands. Until then `worker_production_deployed=NOT_RUN`, Generation is OFF, and Task 29 cannot claim even `7a release accepted`.

- [ ] **Step 7: Verify bundle mismatch handling, observability, and staged workflow contract**

```powershell
python -m unittest backend.tests.test_version_route backend.tests.test_release_bundle backend.tests.test_observability_contract -v
python scripts/release/build_manifest.py --contracts release --output artifacts/release/local/00-bundle-manifest.json
python scripts/release/verify_bundle.py --manifest artifacts/release/local/00-bundle-manifest.json --api-report artifacts/release/local/api-version.json --worker-report artifacts/release/local/worker-version.json
```

Expected: tests PASS. Local verification exits nonzero until real staged API/Worker reports are supplied; never prefill them.

- [ ] **Step 8: Record and commit immutable bundle support**

```powershell
git diff --check
git add backend/app/routers/runtime.py backend/app/core/observability.py backend/app/services/runtime_bundle_service.py backend/app/routers/__init__.py backend/app/main.py backend/app/worker.py backend/app/services/feature_flag_service.py backend/app/services/creem_event_service.py backend/app/services/outbox_service.py backend/app/services/generation_job_service.py backend/app/services/media_deletion_service.py scripts/release/build_runtime_bundle_id.py scripts/release/build_manifest.py scripts/release/register_bundle.py scripts/release/collect_runtime_report.py scripts/release/verify_bundle.py scripts/release/append_evidence_index.py release/bundle-manifest.schema.json release/evidence-index.schema.json backend/tests/test_version_route.py backend/tests/test_release_bundle.py backend/tests/test_observability_contract.py .github/workflows/production-release.yml .github/workflows/integration.yml vercel.json docs/ai-worklog.md
git commit -m "ci: bind releases to immutable runtime bundles"
```

### Task 28: Build And Commit Audited 7a Migration Tooling

**Files:**
- Modify: `scripts/release/inventory_production.py`
- Create: `scripts/release/backfill_identities.py`
- Create: `scripts/release/backfill_commercial_facts.py`
- Create: `scripts/release/backfill_generation_facts.py`
- Create: `scripts/release/backfill_media_assets.py`
- Create: `scripts/release/migrate_public_media.py`
- Create: `scripts/release/verify_private_media.py`
- Create: `scripts/release/verify_runtime_drain.py`
- Create: `scripts/release/apply_additive_migrations.py`
- Create: `scripts/release/verify_inventory_signature.py`
- Create: `scripts/release/export_legacy_url_probe_manifest.py`
- Create: `scripts/release/verify_legacy_url_invalidation.mjs`
- Create: `backend/app/services/data_migration_checkpoint_service.py`
- Create: `backend/tests/test_backfill_idempotency.py`
- Create: `backend/tests/test_private_media_migration.py`
- Create: `backend/tests/test_data_migration_controls.py`
- Create: `.github/workflows/data-migration.yml`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: reviewed, checkpointed, idempotent 7a tools and workflow contract; this task never opens Production credentials or mutates Production.
- Consumes: Task 2 durable migration-run/checkpoint tables and Task 27 immutable bundle/evidence schemas.
- Irreversible boundary for later execution: after old public objects are deleted/invalidated, rollback is allowed only to the separately identified, formally verified private-compatible baseline plus its matching Worker digest.

- [ ] **Step 1: Write read-only, idempotency, classification, and rollback tests**

Tests require read-only inventory credentials and prove raw email/OpenID/URL/token never appears; row/revision/FK/balance/ledger/reference counts reconcile; repeat backfill produces no extra facts; conflicting identity is quarantined; old balance becomes `legacy_pool`; unlinked debit becomes `legacy_unlinked`; COMPLETED without persistent verdict becomes `legacy_unverified`; URL roles distinguish private user/source/candidate/final from public marketing/template/scene/outfit and temporary/debug; shared object keys use global reference counts; unknown owner becomes QUARANTINED; and a failed copy preserves the old reference. Order and retained Live Portrait URL fields are both inventoried; the latter are copied to the Task 9 `source_asset_id`/`video_asset_id` facts while the historical job row/status/cost/audit remains. Legacy generation rows without a provable API deployment/bundle/Worker digest are never stamped with invented values: terminal rows become legacy evidence, and nonterminal/runnable rows are quarantined until manually settled; Generation cannot open until unstamped runnable count is zero. Tests require distinct public-read, public-delete, private-read, and private-write credentials and distinct public/private store IDs. They reject cross-mode/cross-bundle/cross-script resume, stale/unsigned inventory, source database/store drift, concurrent Production runs, stale fencing tokens, and a write whose advisory/run lease has expired. They also fail until the formal-domain API plus Worker are private-compatible and all public-only runtimes/queues are drained.

Identity disposition is explicit rather than “quarantine forever.” Every canonical/login-capable account must end as `NORMALIZED` or `MERGED` with exactly one provider subject. Guest/password/orphan legacy subjects that must retain financial/audit history are `SOFT_CLOSED_TOMBSTONED`: sessions/login are revoked, profile PII is minimized, assets/open facts are rebound to the canonical tombstone/claim record, and no fake identity is created. Ownership/payment conflicts are `QUARANTINED_BLOCKING`; they block 7b while they own any active asset, open money/reconciliation fact, nonterminal job, or claimable account. A fully settled immutable historical subject may be excluded only through an audited disposition code/hash/count and retained tombstone/claim lineage. Tests fix exact counts and reject an uncategorized row.

Static workflow tests require `data-migration.yml` to expose only `workflow_call`—no `push`, `pull_request`, `schedule`, `workflow_dispatch`, or repository dispatch. It is callable only from the committed Task 29 `production-release.yml`; the called job independently checks its Production Environment approval, exact `github.workflow_ref`/caller path, final source SHA, immutable bundle hash, run/attempt, and PostgreSQL lease/fence before secrets or writes. Caller-supplied strings alone are never authority.

URL tests accept only the exact approved provider, store ID, HTTPS origin, bucket, and canonical object-key grammar. The migration runner derives keys locally and calls the allowlisted storage SDK; it never fetches caller/database URLs, follows redirects, resolves arbitrary hosts, or sends legacy credentials to another origin. Unknown/mismatched URLs become QUARANTINED and stop deletion. A genuinely external source may be evaluated only through Task 10's bounded SSRF client and is never eligible for automatic old-object deletion.

- [ ] **Step 2: Run focused tests and confirm current URL rows lack migration facts**

```powershell
python -m unittest backend.tests.test_backfill_idempotency backend.tests.test_private_media_migration backend.tests.test_data_migration_controls -v
```

Expected: FAIL until inventory schema, checkpointing, classification, copy verification, atomic switch, and full reconciliation exist.

- [ ] **Step 3: Implement checkpointed tooling and sensitive-probe isolation**

Every backfill/migration script accepts `--dry-run` or `--write`, `--batch-size`, `--migration-parent-run-id`, a distinct `--script-run-id`, `--resume`, `--inventory-report`, `--inventory-signature`, `--expected-inventory-sha256`, `--release-manifest`, `--expected-manifest-sha256`, and `--report`. Write mode additionally requires the protected `DATA_MIGRATION_APPROVAL_ID`. The parent run owns the global release/advisory lease; each script and each mode (dry/write/copy/delete/replay/schema) uses a separate immutable child ID. Immediately before every batch/control-plane write it verifies the canonical inventory signature/hash/freshness/source database identity/revision, immutable bundle hash/source SHA, parent lease/fence, child script/mode hash, approval, and database drift. `inventory_production.py` emits a canonical report plus detached HMAC signature; absence or mismatch is a hard failure.

Durable resume state is stored only in Task 2's audited database `data_migration_runs`/`data_migration_checkpoints`; `$RUNNER_TEMP` is forbidden for write checkpoints. A Production-global GitHub concurrency group plus PostgreSQL advisory lock and renewable parent-run lease/fencing token prevents two approved releases from overlapping. Each checkpoint binds parent ID, child/script-run ID, mode, script SHA, inventory SHA, manifest SHA, approval, last primary key, counts, and timestamp; cross-mode/cross-bundle/cross-script/cross-approval resume is rejected, while another child under the same healthy parent cannot inherit its checkpoint. A lease loss stops before the next write. `migrate_public_media.py` has only `copy-and-switch` and, after target Promote plus rollback-baseline verification, `delete-old-public`; those modes always use different child IDs. Credentials are exact-purpose: legacy public read for copy, legacy public delete for deletion, private write for copy, private read for verification; token/store mismatches fail.

`data-migration.yml` is a reusable implementation detail, not a release entrypoint. It obtains Production Environment approval in its own job and cross-checks the authenticated caller metadata against `.github/workflows/production-release.yml` at the exact final SHA before resolving any environment secret. Direct or mismatched invocation exits nonzero and writes no run row.

`export_legacy_url_probe_manifest.py` streams the raw old origin/CDN URLs and expected byte checksums from the read-only database into a mode-0600 file on the encrypted ephemeral runner disk. That file is never written to the workspace, uploaded, cached, printed, or placed in the release manifest; workflow `finally` cleanup removes it. `verify_legacy_url_invalidation.mjs` consumes that local file and emits only URL HMAC, location ID, timestamp, HTTP status, byte count, and whether returned bytes differ from the old checksum.

- [ ] **Step 4: Encode additive-migration and forward-fix safety**

`apply_additive_migrations.py` is the only 7a Production schema entrypoint. It requires the same manifest/inventory/approval/run lease, verifies the current revision, takes a PostgreSQL advisory lock, sets bounded `lock_timeout`/`statement_timeout`, and runs exactly `alembic upgrade 20260710_0020`. It records before/after revision, migration checksums/durations, locks waited, and every constraint/index strategy. Large validations use additive indexes and `NOT VALID` followed by controlled validation; no table rewrite or unbounded lock is hidden. Failure leaves flags OFF/maintenance active and requires a reviewed forward fix; it never downgrades Production automatically.

- [ ] **Step 5: Verify and commit all migration tooling before Production access**

```powershell
python -m unittest backend.tests.test_backfill_idempotency backend.tests.test_private_media_migration backend.tests.test_data_migration_controls -v
git diff --check
git add scripts/release/inventory_production.py scripts/release/backfill_identities.py scripts/release/backfill_commercial_facts.py scripts/release/backfill_generation_facts.py scripts/release/backfill_media_assets.py scripts/release/migrate_public_media.py scripts/release/verify_private_media.py scripts/release/verify_runtime_drain.py scripts/release/apply_additive_migrations.py scripts/release/verify_inventory_signature.py scripts/release/export_legacy_url_probe_manifest.py scripts/release/verify_legacy_url_invalidation.mjs backend/app/services/data_migration_checkpoint_service.py backend/tests/test_backfill_idempotency.py backend/tests/test_private_media_migration.py backend/tests/test_data_migration_controls.py .github/workflows/data-migration.yml docs/ai-worklog.md
git commit -m "feat: add audited private-data migration tooling"
```

Expected: tests PASS and the tooling commit is included in the later final reviewed source SHA. No Production inventory, schema upgrade, backfill, copy, delete, or probe is allowed before all Task 29 code/provider/acceptance commits are complete and the exact final SHA is approved.

- [ ] **Step 6: Prove the task boundary and hand execution to Task 29**

```powershell
git show --stat --oneline HEAD
git status --short
```

Expected: the committed diff contains tooling/contracts/tests only, the worktree is clean, and no Production evidence or generated checkpoint is tracked. Inventory, restore, migrations `0014–0020`, runtime drain, backfill, private copy/switch, old-public deletion, and URL invalidation all execute later from the exact final Task 29 source SHA; moving any of them back into Task 28 recreates the manifest/SHA time loop and is forbidden.

### Task 29: Run Linked Staged Production Acceptance, Promote Without Rebuild, Activate, And Observe

**Files:**
- Create: `scripts/release/run_linked_commercial_acceptance.mjs`
- Create: `scripts/release/run_subscription_acceptance.mjs`
- Create: `scripts/release/run_quality_acceptance.mjs`
- Create: `scripts/release/observe_release.py`
- Create: `scripts/release/activate_provider_contracts.py`
- Create: `scripts/release/apply_activation_plan.py`
- Create: `scripts/release/cleanup_acceptance_bindings.py`
- Create: `scripts/release/configure_staged_auth_origin.py`
- Create: `scripts/release/verify_provider_grant_fetch.py`
- Create: `scripts/release/run_approved_worker_host.py`
- Create: `scripts/release/verify_safe_baseline_bridge.py`
- Create: `scripts/release/replay_migration_window_events.py`
- Create: `release/provider-contracts.json`
- Create: `release/worker-host-contract.json`
- Create: `.github/workflows/release-observation.yml`
- Create: `docs/operations/worker-host-addendum.md`
- Create: `frontend/e2e/main-flow.spec.ts`
- Create: `frontend/e2e/production-canary.spec.ts`
- Create: `backend/tests/test_production_acceptance_contract.py`
- Create: `backend/tests/test_production_release_workflow.py`
- Create: `backend/tests/integration/test_safe_baseline_schema_0020_bridge.py`
- Modify: `scripts/run_prod_generation_acceptance.mjs`
- Modify: `backend/app/core/provider_contracts.py`
- Modify: `backend/app/services/payment_service.py`
- Modify: `backend/app/services/payment_reconciliation_service.py`
- Modify: `backend/app/routers/payments.py`
- Modify: `backend/app/services/subscription_service.py`
- Modify: `backend/app/routers/subscriptions.py`
- Modify: `backend/app/schemas/subscription.py`
- Modify: `backend/app/services/evolink_service.py`
- Modify: `backend/app/services/evolink_reconciliation_service.py`
- Modify: `backend/app/services/generation_attempt_service.py`
- Modify: `backend/tests/integration/test_creem_refund_creation_contract.py`
- Modify: `backend/tests/integration/test_creem_subscription_lifecycle.py`
- Modify: `backend/tests/integration/test_evolink_submission_reconciliation.py`
- Modify: `scripts/release/build_manifest.py`
- Modify: `release/gates.json`
- Modify: `.github/workflows/production-release.yml`
- Modify: `.github/workflows/integration.yml`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: one final reviewed source SHA, one immutable bundle manifest, distinct private-compatible-baseline and staged-target deployment identities built from the same prebuilt checksum, and append-only linked evidence. No Admin probe, unrelated payment/generation probe, fixture, old artifact, or future evidence field inside the bundle may satisfy a commercial gate.
- Current expected status: BLOCKED/NOT_RUN until approved Worker host, Evolink safe lost-response reconciliation, Creem refund-creation contract, monitored support channel, authorized six-case inputs, and required production approvals exist.

- [ ] **Step 1: Write acceptance-runner truthfulness tests**

Tests require every false assertion to exit nonzero, exact mandatory case set, ordinary Google users, linked purchase/invoice/order/reservation/job/attempt/artifact/entitlement IDs, controlled production cost/currency limits, cleanup of acceptance assets, no token/email/permanent URL in evidence, and no Admin/test bypass. `run_prod_generation_acceptance.mjs` must stop printing `ok` when any gate is false and must include Golden Anniversary and Partner Invite. Workflow tests enforce: all implementation/provider/acceptance commits precede final SHA freeze; protected Preview runs only from a commit and publishes the exact signed/read-back create-once `commercial-7a` report consumed by `resolve-preview`; untracked files make the freeze fail; one prebuilt checksum creates two distinct deployments; role COMMERCIAL_7A runtime ID is computed after Worker digest and injected into Worker plus both deployments; `0014→0020` precedes runtime use; formal maintenance/drain precedes writes; private-compatible baseline and staged target never share an ID; target is Promoted exactly once; recovery uses flags-OFF-first `vercel rollback` rather than a second Promote; old public deletion occurs only after target Promote and rollback-baseline verification; bundle is immutable and evidence append-only. A bridge integration checks out the exact Task 4 safe-baseline SHA against a temporary clone upgraded to `0020`, proves cold start has zero DDL and signed duplicate/out-of-order webhook, reconciliation, and logout remain safe/durable, then proves the final code replays the migration-window raw events into normalized facts exactly once. Observation tests forbid one 24-hour blocking CI job: a durable OBSERVING run receives signed short samples, rejects bundle/deployment/snapshot drift and sample gaps, requires one real cleanup cycle, and is finalized by a separate CAS-protected job after at least 24 hours. They also forbid relying on a previous job's shell variables, workspace, or local evidence index: every scheduled job must resolve the unique active run from PostgreSQL, checkout its exact source SHA, and use immutable Private Blob evidence keys. Release/migration workflow tests also require the plan-wide native-command fail-fast preamble/wrapper, inject failure before every destructive boundary, and statically reject every unbraced environment-variable-plus-colon interpolation.

Crash-boundary tests require one durable `COMMERCIAL_7A` activation state machine: `RESERVED -> WORKER_STAGED -> API_BASELINE_STAGED -> API_TARGET_STAGED -> MANIFEST_SEALED -> SCHEMA_0020 -> WORKER_RUNNING -> BASELINE_PROMOTED -> DATA_SWITCHED -> WORKER_DISPATCH_ENABLED -> TARGET_ACCEPTED -> TARGET_PROMOTED -> PUBLIC_INVALIDATED -> ACTIVATED -> OBSERVING -> 7A_ACCEPTED`. After every external effect, exact IDs/checksums/digests/reports are CAS-persisted. Rerun resolves the row and reuses completed artifacts; if Promote completed before CAS, it inspects the formal domain/rollback status and advances only when the exact ID matches. Any ambiguity or drift keeps flags OFF/edge deny and requires forward disposition.

`production-release.yml` is `workflow_dispatch` only and is not reusable: no push, pull request, schedule, repository/repository-dispatch, or `workflow_call`. Its required input is the exact approved final SHA. It uses the protected Production Environment, the global Production concurrency group with `cancel-in-progress: false`, rechecks approved main HEAD, and resolves the database release lease before any Production secret. Static tests reject every other trigger, caller, SHA, duplicate, or concurrent run.

- [ ] **Step 2: Run contract tests and confirm current production script is a false-green probe**

```powershell
python -m unittest backend.tests.test_production_acceptance_contract backend.tests.test_production_release_workflow -v
```

Expected: FAIL because the current script uses an Admin generation probe, only five cases, and does not exit nonzero when a gate is false.

- [ ] **Step 3: Implement linked ordinary-user acceptance runners**

Credit-pack chain for one acceptance Google user:

```text
Google login -> welcome grant -> private upload -> trial order/job/QA -> watermarked preview
-> Creem checkout/signed webhook -> exact order entitlement unlock -> private final download
-> paid-grant order -> account data export -> approved full refund -> reversal/access revoke -> user delete -> object 404/410
```

Subscription chain for one ordinary Google user:

```text
Starter checkout -> signed paid transaction -> one invoice/grant -> paid order/180-day snapshot
-> Creem-confirmed period-end cancel -> approved full invoice refund -> reversal/debt/access revoke
```

Creem test mode additionally proves renewal, past-due recovery, duplicate/out-of-order events, partial anomaly, and dispute outcome. Production does not manufacture a real chargeback. Six quality cases all reach READY within one initial plus at most two repair candidates and receive per-candidate human review under the fixed rubric.

- [ ] **Step 4: Verify and commit acceptance tooling before reading Production secrets**

```powershell
python -m unittest backend.tests.test_production_acceptance_contract backend.tests.test_production_release_workflow -v
npm --prefix frontend run test:e2e -- e2e/main-flow.spec.ts
git diff --check
git add scripts/release/run_linked_commercial_acceptance.mjs scripts/release/run_subscription_acceptance.mjs scripts/release/run_quality_acceptance.mjs scripts/release/observe_release.py scripts/release/apply_activation_plan.py scripts/release/cleanup_acceptance_bindings.py scripts/release/configure_staged_auth_origin.py scripts/release/verify_provider_grant_fetch.py scripts/release/verify_safe_baseline_bridge.py scripts/release/replay_migration_window_events.py scripts/run_prod_generation_acceptance.mjs frontend/e2e/main-flow.spec.ts frontend/e2e/production-canary.spec.ts backend/tests/test_production_acceptance_contract.py backend/tests/test_production_release_workflow.py backend/tests/integration/test_safe_baseline_schema_0020_bridge.py .github/workflows/production-release.yml .github/workflows/release-observation.yml .github/workflows/integration.yml docs/ai-worklog.md
git commit -m "test: add linked production acceptance runners"
```

Expected: secret-free unit/workflow tests and local browser-contract tests PASS, then the tooling commit exists. Only after that commit may the protected Preview workflow use real Preview identity/resources for the exact SHA; a missing resource is NOT_RUN/nonzero rather than a fixture PASS. No Production environment is opened in this step.

- [ ] **Step 5: Commit provider-verification support while every unknown contract remains UNVERIFIED**

Implement `release/provider-contracts.json` as the code-versioned authority and keep Creem refund creation, Creem stable subscription transaction/cancel facts, and Evolink lost-response reconciliation `UNVERIFIED`. `provider_contracts.py` validates the config and exposes no environment-variable override. The verification support binds official URL/version, canonical request/response/query schema hashes, authentication scheme, idempotency/correlation semantics, error/retry taxonomy, and expected sandbox report schema. No Production catalog import occurs here.

```powershell
python -m unittest backend.tests.test_credit_pack_checkout backend.tests.test_subscription_lifecycle backend.tests.test_provider_submission_boundary backend.tests.test_production_release_workflow -v
git diff --check
git add backend/app/core/provider_contracts.py backend/app/services/payment_service.py backend/app/services/payment_reconciliation_service.py backend/app/routers/payments.py backend/app/services/subscription_service.py backend/app/routers/subscriptions.py backend/app/schemas/subscription.py backend/app/services/evolink_service.py backend/app/services/evolink_reconciliation_service.py backend/app/services/generation_attempt_service.py backend/tests/integration/test_creem_refund_creation_contract.py backend/tests/integration/test_creem_subscription_lifecycle.py backend/tests/integration/test_evolink_submission_reconciliation.py scripts/release/activate_provider_contracts.py scripts/release/build_manifest.py release/provider-contracts.json release/gates.json docs/ai-worklog.md
git commit -m "feat: add provider contract verification support"
```

- [ ] **Step 6: Produce genuine sandbox evidence, then commit the activation addendum before freezing SHA**

Run only from the clean committed verification-support SHA. The Creem evidence must prove one exact test purchase refund with stable local request ID/replay dedupe; one subscription first paid transaction plus renewal with stable transaction IDs and both invoice uniqueness keys; Creem-confirmed period-end cancellation; and signed full-refund/reversal behavior. The Evolink evidence deliberately loses the submit response and finds/deduplicates the same task through a documented idempotency/correlation lookup without a second generation or capture. `activate_provider_contracts.py` accepts only signed, fresh, schema-valid reports from that SHA and writes nonempty evidence hashes into the versioned config. It does not accept a dashboard screenshot or self-authored fixture.

```powershell
$verificationSha = (git rev-parse HEAD).Trim()
$env:RUN_CREEM_TEST_MODE='1'
python -m unittest backend.tests.integration.test_creem_refund_creation_contract backend.tests.integration.test_creem_subscription_lifecycle -v
$env:RUN_EVOLINK_SANDBOX='1'
python -m unittest backend.tests.integration.test_evolink_submission_reconciliation -v
python scripts/release/activate_provider_contracts.py --expected-source-sha $verificationSha --creem-refund-report $env:CREEM_REFUND_CONTRACT_REPORT --creem-subscription-report $env:CREEM_SUBSCRIPTION_CONTRACT_REPORT --evolink-report $env:EVOLINK_RECONCILIATION_REPORT --approval-id-env PROVIDER_CONTRACT_APPROVAL_ID --output release/provider-contracts.json
python -m unittest backend.tests.test_credit_pack_checkout backend.tests.test_subscription_lifecycle backend.tests.test_provider_submission_boundary backend.tests.test_production_release_workflow -v
git diff --check
git add release/provider-contracts.json docs/ai-worklog.md
git commit -m "feat: activate verified provider contracts"
```

Expected only after genuine external evidence: all three integration contracts PASS and the second commit changes only reviewed contract/evidence hashes and factual worklog status. Missing Creem refund creation, stable subscription transaction/cancel evidence, or Evolink lost-response lookup leaves the fact UNVERIFIED, skips the activation commit, keeps the affected flag OFF, and ends 7a as NOT_RUN. Production catalog import remains later and is bound to the final SHA.

- [ ] **Step 7: Freeze the final source SHA and pass committed Preview/CI gates**

Before freeze, write and commit the user-approved overseas Worker-host addendum plus `release/worker-host-contract.json` with exact allowlisted executable/arguments for registry push, digest resolution, suspended deploy, start/stop, rollback, secret injection, heartbeat, and logs. `run_approved_worker_host.py` validates the contract hash, invokes argument arrays without a shell, rejects mutable tags/unlisted actions, redacts secrets, and emits canonical signed reports; the protected workflow calls only this adapter. Until a host-specific contract is reviewed, the script exits NOT_RUN/nonzero. Confirm monitored support, legal-review status, authorized six-case inputs, cost/currency caps, Private Blob store, and Production approvals exist. Then require a clean tree including untracked files, approved main HEAD, all mandatory CI, and protected Preview E2E for that exact commit. No later code/config/workflow/provider commit is allowed under the same bundle; a change restarts Step 7.

```powershell
git diff --check
git add docs/operations/worker-host-addendum.md scripts/release/run_approved_worker_host.py release/worker-host-contract.json .github/workflows/production-release.yml docs/ai-worklog.md
git commit -m "ops: bind the approved production Worker host"
$finalSha = (git rev-parse HEAD).Trim()
git diff --exit-code
git diff --cached --exit-code
$status = git status --porcelain=v1 --untracked-files=all
if ($status) { $status; throw 'worktree is not clean, including untracked files' }
git ls-files --error-unmatch release/gates.json release/activation-plan.json release/runtime-contracts.json release/provider-contracts.json release/worker-host-contract.json scripts/release/build_runtime_bundle_id.py scripts/release/register_bundle.py .github/workflows/production-release.yml
python -m unittest backend.tests.test_production_acceptance_contract backend.tests.test_production_release_workflow backend.tests.test_release_bundle -v
$env:RUN_POSTGRES_INTEGRATION='1'
python -m unittest backend.tests.integration.test_safe_baseline_schema_0020_bridge -v
npm --prefix frontend run typecheck
npm --prefix frontend run test:unit
npm --prefix frontend run build:web
```

Expected: local gates PASS, the exact safe-baseline SHA is proven zero-DDL and behavior-compatible with schema `0020`, its migration-window raw facts replay exactly once under final code, protected Preview workflow PASS for `$finalSha`, and the approved main head still equals `$finalSha`. The Preview workflow writes a signed create-once report containing Preview deployment ID, source SHA, build checksum, schema, runtime-contract hash, and mandatory gate result to Private evidence storage; stale/wrong-SHA/7b/other-project Preview evidence is rejected. If compatibility fails, stop before Production and commit a reviewed forward-compatible bridge, then restart SHA freeze; “the window is short” is not an exception. Missing Worker addendum/resource is NOT_RUN/nonzero. This is the last source/config commit before building the 7a bundle.

- [ ] **Step 8: Build once, create distinct unbound deployments, and seal the immutable bundle**

Inside the globally serialized protected Production workflow, reserve one `COMMERCIAL_7A` release row before the first external write. Hash the committed runtime/provider/catalog/flag/activation/Worker-host contracts and protected six-product mapping without logging provider IDs. Use only the committed Worker adapter to build/push the exact final SHA and resolve an immutable digest; then compute the COMMERCIAL_7A runtime ID. Inject that ID while deploying the same digest suspended and while creating both Vercel deployments. Build the Vercel prebuilt output once and deploy it twice with `--prod --skip-domain`: one `PRIVATE_COMPATIBLE_BASELINE_ID` and one different `STAGED_TARGET_ID`. Neither receives the formal domain and neither business route is invoked before schema compatibility exists.

After each external effect, CAS its report/hash/ID into the release row. Seal `00-bundle-manifest.json` only after the Preview ID, both trusted Vercel inspect reports, common prebuilt checksum, suspended Worker deployment/digest, runtime ID, expected schema `20260710_0020`, provider/catalog/config/contract hashes, OFF snapshot, and target snapshot exist. Upload the manifest to its content-addressed Private evidence key, read back the hash, and CAS `MANIFEST_SEALED`; no environment mutation or rebuild follows. A restart resolves the row and exact source-labelled registry/Vercel candidates, reuses the unique match, and never repeats a completed phase.

```powershell
python scripts/release/register_bundle.py resolve-preview --release-kind commercial-7a --source-sha $finalSha --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_READ_TOKEN --require-pass --output $env:RUNNER_TEMP/preview-resolution.json
python scripts/release/register_bundle.py reserve --kind COMMERCIAL_7A --environment production --source-sha $finalSha --preview-resolution-report $env:RUNNER_TEMP/preview-resolution.json --workflow-run-id $env:GITHUB_RUN_ID --workflow-attempt $env:GITHUB_RUN_ATTEMPT --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID --output $env:RUNNER_TEMP/release-reserved.json
python scripts/release/run_approved_worker_host.py build-push --contract release/worker-host-contract.json --source-sha $finalSha --output $env:RUNNER_TEMP/worker-build.json
$workerBuild = Get-Content -Raw $env:RUNNER_TEMP/worker-build.json | ConvertFrom-Json
$env:WORKER_IMAGE_DIGEST = $workerBuild.image_digest
$runtimeId = python scripts/release/build_runtime_bundle_id.py --release-role COMMERCIAL_7A --source-sha $finalSha --schema 20260710_0020 --worker-image-digest $env:WORKER_IMAGE_DIGEST --runtime-contract release/runtime-contracts.json --provider-contract release/provider-contracts.json --catalog-contract release/catalog/catalog-2026-07-10.json --flag-contract release/gates.json --activation-plan release/activation-plan.json --worker-host-contract release/worker-host-contract.json --builder-contract-version commercial-7a.v1 --output $env:RUNNER_TEMP/runtime-bundle-id.txt
$env:RUNTIME_BUNDLE_ID = $runtimeId.Trim()
python scripts/release/run_approved_worker_host.py deploy-suspended --contract release/worker-host-contract.json --source-sha $finalSha --image-digest $env:WORKER_IMAGE_DIGEST --runtime-bundle-id $env:RUNTIME_BUNDLE_ID --output $env:RUNNER_TEMP/worker-suspended.json
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase RESERVED --phase WORKER_STAGED --runtime-bundle-id $env:RUNTIME_BUNDLE_ID --worker-build-report $env:RUNNER_TEMP/worker-build.json --worker-deployment-report $env:RUNNER_TEMP/worker-suspended.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
npx --yes vercel@55.0.0 pull --yes --environment=production --token=$env:VERCEL_TOKEN
npx --yes vercel@55.0.0 build --prod --token=$env:VERCEL_TOKEN
$privateCompatibleBaselineUrl = npx --yes vercel@55.0.0 deploy --prebuilt --prod --skip-domain --env RUNTIME_BUNDLE_ID=$env:RUNTIME_BUNDLE_ID --token=$env:VERCEL_TOKEN
npx --yes vercel@55.0.0 inspect $privateCompatibleBaselineUrl --token=$env:VERCEL_TOKEN > $env:RUNNER_TEMP/private-baseline-inspect.txt
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase WORKER_STAGED --phase API_BASELINE_STAGED --runtime-bundle-id $env:RUNTIME_BUNDLE_ID --deployment-url $privateCompatibleBaselineUrl --deployment-role private-compatible-baseline --build-output .vercel/output --inspect-report $env:RUNNER_TEMP/private-baseline-inspect.txt --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
$stagedTargetUrl = npx --yes vercel@55.0.0 deploy --prebuilt --prod --skip-domain --env RUNTIME_BUNDLE_ID=$env:RUNTIME_BUNDLE_ID --token=$env:VERCEL_TOKEN
npx --yes vercel@55.0.0 inspect $stagedTargetUrl --token=$env:VERCEL_TOKEN > $env:RUNNER_TEMP/staged-target-inspect.txt
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase API_BASELINE_STAGED --phase API_TARGET_STAGED --runtime-bundle-id $env:RUNTIME_BUNDLE_ID --deployment-url $stagedTargetUrl --deployment-role staged-target --build-output .vercel/output --inspect-report $env:RUNNER_TEMP/staged-target-inspect.txt --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
$env:RELEASE_MANIFEST = "$env:RUNNER_TEMP/commercial-7a-bundle-manifest.json"
python scripts/release/build_manifest.py --release-kind commercial-7a --runtime-bundle-id $env:RUNTIME_BUNDLE_ID --source-sha $finalSha --preview-resolution-report $env:RUNNER_TEMP/preview-resolution.json --private-compatible-baseline-url $privateCompatibleBaselineUrl --private-compatible-baseline-inspect $env:RUNNER_TEMP/private-baseline-inspect.txt --staged-target-url $stagedTargetUrl --staged-target-inspect $env:RUNNER_TEMP/staged-target-inspect.txt --worker-report $env:RUNNER_TEMP/worker-suspended.json --provider-mapping-file-env CREEM_PRODUCT_MAPPING_FILE --expected-schema 20260710_0020 --contracts release --output $env:RELEASE_MANIFEST
$env:RELEASE_MANIFEST_SHA256 = (Get-FileHash $env:RELEASE_MANIFEST -Algorithm SHA256).Hash.ToLowerInvariant()
python scripts/release/register_bundle.py seal --kind COMMERCIAL_7A --expected-phase API_TARGET_STAGED --phase MANIFEST_SEALED --manifest $env:RELEASE_MANIFEST --expected-manifest-sha256 $env:RELEASE_MANIFEST_SHA256 --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_WRITE_TOKEN --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
python scripts/release/register_bundle.py resolve --kind COMMERCIAL_7A --source-sha $finalSha --minimum-phase MANIFEST_SEALED --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_READ_TOKEN --manifest-output $env:RUNNER_TEMP/commercial-7a-bundle-manifest.json --output $env:RUNNER_TEMP/commercial-7a-coordinates.json
python scripts/release/register_bundle.py resolve --kind SAFE_BASELINE_INSTALL --expected-phase COMPLETED --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --output $env:RUNNER_TEMP/safe-baseline-coordinates.json
$releaseCoordinates = Get-Content -Raw $env:RUNNER_TEMP/commercial-7a-coordinates.json | ConvertFrom-Json
$safeBaselineCoordinates = Get-Content -Raw $env:RUNNER_TEMP/safe-baseline-coordinates.json | ConvertFrom-Json
$finalSha = $releaseCoordinates.source_sha
$env:RUNTIME_BUNDLE_ID = $releaseCoordinates.runtime_bundle_id
$env:RELEASE_MANIFEST = $releaseCoordinates.manifest_local_path
$env:RELEASE_MANIFEST_SHA256 = $releaseCoordinates.manifest_sha256
$env:PRIVATE_COMPATIBLE_BASELINE_ID = $releaseCoordinates.private_compatible_baseline_deployment_id
$env:STAGED_TARGET_ID = $releaseCoordinates.staged_target_deployment_id
$privateCompatibleBaselineUrl = $releaseCoordinates.private_compatible_baseline_url
$stagedTargetUrl = $releaseCoordinates.staged_target_url
$env:WORKER_DEPLOYMENT_ID = $releaseCoordinates.worker_deployment_id
$env:WORKER_IMAGE_DIGEST = $releaseCoordinates.worker_image_digest
$env:PRIVATE_EVIDENCE_PREFIX = $releaseCoordinates.private_evidence_prefix
$env:SAFE_BASELINE_DEPLOYMENT_ID = $safeBaselineCoordinates.target_deployment_id
```

Expected: deployment IDs differ; source SHA/runtime ID/prebuilt checksum match; API reports read back platform deployment IDs and the injected runtime ID; the manifest is content-addressed/read-only and registered once; no future report/current-live-snapshot/final decision is embedded. A build or deployment drift starts a new bundle rather than editing this one. The Worker adapter or Vercel candidate cannot be guessed after a crash: zero/multiple exact matches is a hard stop. The resolution/read-back prologue above is mandatory at the start of every later fresh protected job; commands in Steps 9-15 may use those variables only after parsing and validating that durable JSON, never from an inherited shell, workflow output, or mutable alias.

`production-release.yml` owns an independent `if: failure() || cancelled()` reconciliation job for every phase before the release reaches OBSERVING. It resolves by exact source SHA plus workflow run/attempt with `--allow-absent`. `NO_ACTIVE_RELEASE/ENTRY_REJECTED` proves no source-labelled Worker/API/domain/schema effect and writes a sanitized create-once entry-refusal object without creating a release row; a lost response at the reserve commit boundary must instead rediscover the exact row. For a real row it reads actual formal-domain/runtime/Worker/schema state, audits every cohort/high-risk flag OFF, removes any exact staged Supabase origin/binding, stops/permits-absent the exact Worker, and chooses only a recorded compatible target: completed safe baseline before private handoff, or private-compatible baseline after data switch/public invalidation. A completed Promote with missing CAS is reconciled from formal-domain evidence; rollback uses `vercel rollback`, never a second Promote. Unknown/ambiguous state is a blocking manual-forward disposition. Cleanup is idempotent, create-once evidenced, cannot mark the failed release accepted, and its own failure blocks every later release. Tests inject failure/cancel and native nonzero immediately before/after reserve, Worker, deploy, migration, Promote, data switch, staged auth, public deletion, and every flag/CAS boundary.

- [ ] **Step 9: Enter maintenance, drain old runtimes, rehearse restore, and apply additive schema**

Keep all PostgreSQL flags OFF and put formal high-risk routes behind reviewed project-edge maintenance deny. Stop old Workers/dispatchers, wait the configured 300-second maximum old Function duration plus margin, and require legacy queue depth zero. From the sealed bundle, acquire the GitHub Production concurrency group and PostgreSQL advisory/run lease. Produce signed pre-migration inventory, run the Task 3 restore rehearsal with temporary target/admin credentials, and require target DB/credential destruction. Then execute exactly `0014→0020` with bounded locks/online validation and forward-fix failure semantics. No baseline/target API or Worker is started before the new revision is confirmed.

```powershell
$releaseRoot = "artifacts/release/$finalSha/$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT/$env:STAGED_TARGET_ID"
$productionEvidence = "$releaseRoot/03-production"
$reviewEvidence = "$releaseRoot/04-review"
$evidence = $productionEvidence
python scripts/release/verify_runtime_drain.py --formal-base-url $env:PRODUCTION_BASE_URL --expected-api-deployment-id $env:SAFE_BASELINE_DEPLOYMENT_ID --expected-worker-deployment-id none --old-deployments-file-env OLD_RUNTIME_IDS_FILE --legacy-queue-name generate_order --max-api-duration-seconds 300 --output "$evidence/pre-migration-drain.json"
python scripts/release/inventory_production.py --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --hmac-key-env INVENTORY_HMAC_KEY --output "$evidence/pre-migration-inventory.json" --signature-output "$evidence/pre-migration-inventory.sig"
$env:PRE_MIGRATION_INVENTORY_SHA256 = (Get-FileHash "$evidence/pre-migration-inventory.json" -Algorithm SHA256).Hash.ToLowerInvariant()
python backend/scripts/backup_restore_rehearsal.py --source-url-env PRODUCTION_READ_ONLY_DATABASE_URL --target-url-env RESTORE_REHEARSAL_DATABASE_URL --target-admin-url-env RESTORE_REHEARSAL_ADMIN_DATABASE_URL --target-role-name-env RESTORE_REHEARSAL_ROLE_NAME --expected-target-db-prefix vowpic_restore_ --artifact-dir "$env:RUNNER_TEMP/restore"
python scripts/release/register_bundle.py bind-migration-parent --kind COMMERCIAL_7A --expected-phase MANIFEST_SEALED --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env DATA_MIGRATION_APPROVAL_ID --output $env:RUNNER_TEMP/migration-parent.json
$env:MIGRATION_PARENT_RUN_ID = (Get-Content -Raw $env:RUNNER_TEMP/migration-parent.json | ConvertFrom-Json).migration_parent_run_id
python scripts/release/apply_additive_migrations.py --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --migration-parent-run-id $env:MIGRATION_PARENT_RUN_ID --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:additive-0020:write" --inventory-report "$evidence/pre-migration-inventory.json" --inventory-signature "$evidence/pre-migration-inventory.sig" --expected-inventory-sha256 $env:PRE_MIGRATION_INVENTORY_SHA256 --release-manifest $env:RELEASE_MANIFEST --expected-manifest-sha256 $env:RELEASE_MANIFEST_SHA256 --approval-id-env DATA_MIGRATION_APPROVAL_ID --target-revision 20260710_0020 --report "$evidence/additive-migrations.json" --write
python scripts/release/replay_migration_window_events.py --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --migration-parent-run-id $env:MIGRATION_PARENT_RUN_ID --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:window-replay-initial:write" --drain-report "$evidence/pre-migration-drain.json" --inventory-report "$evidence/pre-migration-inventory.json" --inventory-signature "$evidence/pre-migration-inventory.sig" --expected-inventory-sha256 $env:PRE_MIGRATION_INVENTORY_SHA256 --release-manifest $env:RELEASE_MANIFEST --expected-manifest-sha256 $env:RELEASE_MANIFEST_SHA256 --approval-id-env DATA_MIGRATION_APPROVAL_ID --report "$evidence/migration-window-replay.json" --write
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase MANIFEST_SEALED --phase SCHEMA_0020 --migration-parent-run-id $env:MIGRATION_PARENT_RUN_ID --migration-report "$evidence/additive-migrations.json" --replay-report "$evidence/migration-window-replay.json" --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env DATA_MIGRATION_APPROVAL_ID
```

Expected: drain, restore/destruction, additive migration, and migration-window replay PASS; before/after revision is recorded; every raw signed event that arrived while the safe baseline remained formal is normalized exactly once, duplicate/out-of-order semantics hold, logout remains valid, and no missing identity/RLS table exists. The fresh post-migration inventory/backfill includes these replayed facts. Failure leaves maintenance/flags OFF and uses a reviewed forward fix—never an automatic Production downgrade.

- [ ] **Step 10: Verify both compatible deployments, Promote the baseline, then backfill and switch private references**

Start the approved Worker deployment only after schema `0020`, with dispatch and Generation still OFF. Verify the unbound baseline and target independently expose the exact runtime ID/deployment and can dual-read old/public and new/private forms. Promote only the distinct private-compatible baseline to the formal domain without rebuild, verify it formally while flags remain OFF, and record it as rollback target; `STAGED_TARGET_ID` remains unbound. Wait the old safe-baseline Function duration plus margin, capture the formal handoff watermark, and replay/reconcile the remaining raw signed events after Step 9's first watermark; only a zero-difference report closes the migration window. Produce a fresh signed post-migration inventory after that replay, import the exact six-product Production mapping bound to `$finalSha`, run every dry-run under a distinct child ID, then write backfills/copy-and-switch under separate child IDs and the same parent lease.

Identity backfill must prove zero duplicate subject, zero active/login-capable user without exactly one normalized identity, zero legacy-fallback use, zero RLS policy gap, and exact disposition counts for `NORMALIZED | MERGED | SOFT_CLOSED_TOMBSTONED | QUARANTINED_BLOCKING`. No unresolved blocking case may own an active asset, open money/reconciliation fact, or nonterminal job. It does not fabricate identities for tombstones. The retained Live Portrait job URLs must be represented by verified media asset IDs or an explicit blocking disposition. Do not delete old public bytes.

```powershell
python scripts/release/run_approved_worker_host.py start --contract release/worker-host-contract.json --deployment-report $env:RUNNER_TEMP/worker-suspended.json --image-digest $env:WORKER_IMAGE_DIGEST --runtime-bundle-id $env:RUNTIME_BUNDLE_ID --dispatch-mode disabled --output "$evidence/worker-start-disabled.json"
python scripts/release/run_approved_worker_host.py heartbeat --contract release/worker-host-contract.json --deployment-report $env:RUNNER_TEMP/worker-suspended.json --expected-image-digest $env:WORKER_IMAGE_DIGEST --expected-runtime-bundle-id $env:RUNTIME_BUNDLE_ID --require-dispatch-mode disabled --output "$evidence/worker-version.json"
python scripts/release/collect_runtime_report.py --base-url $privateCompatibleBaselineUrl --deployment-bypass-header-env VERCEL_AUTOMATION_BYPASS_HEADER --expected-role private-compatible-baseline --expected-runtime-bundle-id $env:RUNTIME_BUNDLE_ID --expected-source-sha $finalSha --output "$evidence/private-compatible-baseline-version.json"
python scripts/release/collect_runtime_report.py --base-url $stagedTargetUrl --deployment-bypass-header-env VERCEL_AUTOMATION_BYPASS_HEADER --expected-role staged-target --expected-runtime-bundle-id $env:RUNTIME_BUNDLE_ID --expected-source-sha $finalSha --output "$evidence/staged-target-version.json"
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase SCHEMA_0020 --phase WORKER_RUNNING --worker-start-report "$evidence/worker-start-disabled.json" --worker-heartbeat-report "$evidence/worker-version.json" --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
python scripts/release/verify_bundle.py --manifest $env:RELEASE_MANIFEST --api-report "$evidence/private-compatible-baseline-version.json" --worker-report "$evidence/worker-version.json"
python scripts/release/verify_bundle.py --manifest $env:RELEASE_MANIFEST --api-report "$evidence/staged-target-version.json" --worker-report "$evidence/worker-version.json"
npx --yes vercel@55.0.0 promote $privateCompatibleBaselineUrl --yes --token=$env:VERCEL_TOKEN
npx --yes vercel@55.0.0 promote status --token=$env:VERCEL_TOKEN
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase WORKER_RUNNING --phase BASELINE_PROMOTED --deployment-url $privateCompatibleBaselineUrl --formal-base-url $env:PRODUCTION_BASE_URL --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
python scripts/release/verify_runtime_drain.py --formal-base-url $env:PRODUCTION_BASE_URL --expected-api-deployment-id $env:PRIVATE_COMPATIBLE_BASELINE_ID --expected-worker-deployment-id $env:WORKER_DEPLOYMENT_ID --old-deployments-file-env SAFE_BASELINE_RUNTIME_IDS_FILE --legacy-queue-name generate_order --max-api-duration-seconds 300 --output "$evidence/private-baseline-handoff-drain.json"
python scripts/release/replay_migration_window_events.py --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --migration-parent-run-id $env:MIGRATION_PARENT_RUN_ID --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:window-replay-final:write" --resume-from-report "$evidence/migration-window-replay.json" --cutover-report "$evidence/private-baseline-handoff-drain.json" --inventory-report "$evidence/pre-migration-inventory.json" --inventory-signature "$evidence/pre-migration-inventory.sig" --expected-inventory-sha256 $env:PRE_MIGRATION_INVENTORY_SHA256 --release-manifest $env:RELEASE_MANIFEST --expected-manifest-sha256 $env:RELEASE_MANIFEST_SHA256 --approval-id-env DATA_MIGRATION_APPROVAL_ID --report "$evidence/migration-window-final-replay.json" --write --resume
python scripts/release/inventory_production.py --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --hmac-key-env INVENTORY_HMAC_KEY --output "$evidence/post-migration-inventory.json" --signature-output "$evidence/post-migration-inventory.sig"
$env:POST_MIGRATION_INVENTORY_SHA256 = (Get-FileHash "$evidence/post-migration-inventory.json" -Algorithm SHA256).Hash.ToLowerInvariant()
python scripts/release/import_provider_catalog.py --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --mapping-file-env CREEM_PRODUCT_MAPPING_FILE --environment production --catalog-version 2026-07-10 --release-sha $finalSha --approval-id-env PROVIDER_CONTRACT_APPROVAL_ID --write
$common = @('--database-url-env','PRODUCTION_MIGRATION_DATABASE_URL','--migration-parent-run-id',$env:MIGRATION_PARENT_RUN_ID,'--inventory-report',"$evidence/post-migration-inventory.json",'--inventory-signature',"$evidence/post-migration-inventory.sig",'--expected-inventory-sha256',$env:POST_MIGRATION_INVENTORY_SHA256,'--release-manifest',$env:RELEASE_MANIFEST,'--expected-manifest-sha256',$env:RELEASE_MANIFEST_SHA256,'--approval-id-env','DATA_MIGRATION_APPROVAL_ID')
python scripts/release/backfill_identities.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:identities:dry" --batch-size 500 --report "$evidence/identity-dry.json" --dry-run
python scripts/release/backfill_commercial_facts.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:commercial:dry" --batch-size 500 --report "$evidence/commercial-dry.json" --dry-run
python scripts/release/backfill_generation_facts.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:generation:dry" --batch-size 500 --report "$evidence/generation-dry.json" --dry-run
python scripts/release/backfill_media_assets.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:media-assets:dry" --batch-size 200 --report "$evidence/media-dry.json" --dry-run
python scripts/release/migrate_public_media.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:media-copy:dry" --mode copy-and-switch --legacy-public-store-id-env LEGACY_PUBLIC_BLOB_STORE_ID --legacy-public-read-token-env LEGACY_PUBLIC_BLOB_READ_TOKEN --private-store-id-env PRIVATE_BLOB_STORE_ID --private-write-token-env PRIVATE_BLOB_WRITE_TOKEN --batch-size 50 --report "$evidence/public-media-copy-dry.json" --dry-run
python scripts/release/backfill_identities.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:identities:write" --batch-size 500 --report "$evidence/identity-write.json" --write --resume
python scripts/release/backfill_commercial_facts.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:commercial:write" --batch-size 500 --report "$evidence/commercial-write.json" --write --resume
python scripts/release/backfill_generation_facts.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:generation:write" --batch-size 500 --report "$evidence/generation-write.json" --write --resume
python scripts/release/backfill_media_assets.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:media-assets:write" --batch-size 200 --report "$evidence/media-write.json" --write --resume
python scripts/release/migrate_public_media.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:media-copy:write" --mode copy-and-switch --legacy-public-store-id-env LEGACY_PUBLIC_BLOB_STORE_ID --legacy-public-read-token-env LEGACY_PUBLIC_BLOB_READ_TOKEN --private-store-id-env PRIVATE_BLOB_STORE_ID --private-write-token-env PRIVATE_BLOB_WRITE_TOKEN --batch-size 50 --report "$evidence/public-media-copy.json" --write --resume
python scripts/release/verify_private_media.py --inventory "$evidence/post-migration-inventory.json" --migration-report "$evidence/public-media-copy.json" --runtime-drain-report "$evidence/pre-migration-drain.json" --output "$evidence/private-media-reconciliation.json"
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase BASELINE_PROMOTED --phase DATA_SWITCHED --migration-parent-run-id $env:MIGRATION_PARENT_RUN_ID --identity-report "$evidence/identity-write.json" --commercial-report "$evidence/commercial-write.json" --generation-report "$evidence/generation-write.json" --media-report "$evidence/private-media-reconciliation.json" --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env DATA_MIGRATION_APPROVAL_ID
```

Every dry/write/copy/delete/replay/schema command has its own child ID under the one stored parent lease; a child cannot resume another child. Write commands are allowed only after all listed dry-runs PASS. Every report is appended to durable evidence by hash. The formal baseline, staged target, Worker digest, schema, catalog mapping, identity dispositions, retained Live Portrait asset mappings, and private reconciliation must all match the immutable bundle.

- [ ] **Step 11: Provision two-stage identities and run linked staged acceptance**

First derive the exact staged HTTPS origin/callback from the sealed manifest's trusted target deployment and registered activation row. A protected least-privilege Supabase Auth configuration role adds only that exact callback, reads back the canonical redirect allowlist/hash, and records the same exact origin/expiry in the activation mapping used by Task 7 CORS/CSRF. Wildcards, `*.vercel.app`, caller URLs, and unrelated existing entries are rejected; the already configured exact formal callback must remain unchanged. Failure is NOT_RUN and creates no binding.

For each authorized acceptance subject, create a deployment-bound subject-HMAC binding and set only `GOOGLE_AUTH` to cohort for `STAGED_TARGET_ID`; no canonical user ID exists yet and no non-auth capability accepts the binding. Run the real staged Google first login, atomically consume the binding, and capture the resulting canonical user ID in a signed sanitized report. Only then add that canonical ID to upload, generation, checkout, subscription, private-download, and Partner-Invite cohorts. The same-runtime private-compatible baseline must already be formal and its token-only Provider grant route must pass a real Evolink fetch; Generation cohort cannot precede that proof. Clean every expired/unused binding. Run the two linked commercial chains, six quality cases, cross-user denial, account export/deletion, and production canary against the still-unbound staged target. The subscription chain binds stable paid transaction ID, both invoice uniqueness keys, cancel intent/provider confirmation, refund/reversal, and entitlement IDs; otherwise `SUBSCRIPTION_BILLING` remains OFF/NOT_RUN. After all staged gates, remove the temporary Supabase redirect and staged-origin activation entry, read back/prove no wildcard or residue, and keep only the exact formal callback before target Promote.

```powershell
python scripts/release/run_approved_worker_host.py set-dispatch --contract release/worker-host-contract.json --deployment-report $env:RUNNER_TEMP/worker-suspended.json --expected-image-digest $env:WORKER_IMAGE_DIGEST --runtime-bundle-id $env:RUNTIME_BUNDLE_ID --mode enabled --output "$evidence/worker-dispatch-enabled.json"
python scripts/release/run_approved_worker_host.py heartbeat --contract release/worker-host-contract.json --deployment-report $env:RUNNER_TEMP/worker-suspended.json --expected-image-digest $env:WORKER_IMAGE_DIGEST --expected-runtime-bundle-id $env:RUNTIME_BUNDLE_ID --require-dispatch-mode enabled --output "$evidence/worker-version-enabled.json"
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase DATA_SWITCHED --phase WORKER_DISPATCH_ENABLED --worker-dispatch-report "$evidence/worker-dispatch-enabled.json" --worker-heartbeat-report "$evidence/worker-version-enabled.json" --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
python scripts/release/configure_staged_auth_origin.py add --manifest $env:RELEASE_MANIFEST --expected-manifest-sha256 $env:RELEASE_MANIFEST_SHA256 --deployment-id $env:STAGED_TARGET_ID --supabase-project-ref-env SUPABASE_PROJECT_REF --supabase-auth-admin-token-env SUPABASE_AUTH_CONFIG_TOKEN --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --expires-in-seconds 7200 --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID --output "$evidence/staged-auth-origin-add.json"
python scripts/release/verify_provider_grant_fetch.py --formal-base-url $env:PRODUCTION_BASE_URL --manifest $env:RELEASE_MANIFEST --expected-manifest-sha256 $env:RELEASE_MANIFEST_SHA256 --serving-baseline-deployment-id $env:PRIVATE_COMPATIBLE_BASELINE_ID --target-api-deployment-id $env:STAGED_TARGET_ID --provider evolink --provider-contract release/provider-contracts.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --private-store-id-env PRIVATE_BLOB_STORE_ID --private-write-token-env PRIVATE_BLOB_WRITE_TOKEN --evolink-key-env EVOLINK_API_KEY --signing-key-env ACCEPTANCE_EVIDENCE_SIGNING_KEY --output "$evidence/provider-grant-fetch.json"
python scripts/release/provision_acceptance_identity.py --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --subject-file-env ACCEPTANCE_SUBJECTS_FILE --hmac-key-env ACCEPTANCE_IDENTITY_HMAC_KEY --environment production --deployment-id $env:STAGED_TARGET_ID --expires-in-seconds 7200 --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID --output "$evidence/identity-bindings.json"
python scripts/release/apply_activation_plan.py --phase google-auth-only --manifest $env:RELEASE_MANIFEST --deployment-id $env:STAGED_TARGET_ID --binding-report "$evidence/identity-bindings.json" --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID --output "$evidence/google-auth-cohort.json"
node scripts/release/run_linked_commercial_acceptance.mjs --phase first-login --base-url $stagedTargetUrl --output "$evidence/first-login-users.json"
python scripts/release/apply_activation_plan.py --phase staged-user-cohort --manifest $env:RELEASE_MANIFEST --deployment-id $env:STAGED_TARGET_ID --canonical-users-report "$evidence/first-login-users.json" --required-provider-grant-report "$evidence/provider-grant-fetch.json" --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID --output "$evidence/staged-cohort.json"
node scripts/release/run_linked_commercial_acceptance.mjs --base-url $stagedTargetUrl --output "$evidence/commercial-chain.json"
node scripts/release/run_subscription_acceptance.mjs --base-url $stagedTargetUrl --output "$evidence/subscription-chain.json"
node scripts/release/run_quality_acceptance.mjs --base-url $stagedTargetUrl --cases release/quality-cases.json --rubric release/quality-rubric.json --output "$evidence/quality-cases.json"
python scripts/release/cleanup_acceptance_bindings.py --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --deployment-id $env:STAGED_TARGET_ID --require-zero-unused --output "$evidence/identity-binding-cleanup.json"
python scripts/release/configure_staged_auth_origin.py remove --manifest $env:RELEASE_MANIFEST --expected-manifest-sha256 $env:RELEASE_MANIFEST_SHA256 --deployment-id $env:STAGED_TARGET_ID --supabase-project-ref-env SUPABASE_PROJECT_REF --supabase-auth-admin-token-env SUPABASE_AUTH_CONFIG_TOKEN --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --require-exact-formal-callback --require-no-wildcard --require-no-staged-residue --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID --output "$evidence/staged-auth-origin-remove.json"
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase WORKER_DISPATCH_ENABLED --phase TARGET_ACCEPTED --staged-acceptance-report "$evidence/commercial-chain.json" --quality-report "$evidence/quality-cases.json" --required-provider-grant-report "$evidence/provider-grant-fetch.json" --auth-origin-add-report "$evidence/staged-auth-origin-add.json" --auth-origin-remove-report "$evidence/staged-auth-origin-remove.json" --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
```

The add operation first CAS-records the pre-change Supabase allowlist hash and exact intended callback; if its response is lost, the cleanup path reads back and reconciles rather than adding again. An independent `if: always()`/cancel-safe protected cleanup job resolves the activation row (not the failed job workspace), removes that exact staged callback and DB origin, restores/verifies the pre-change allowlist hash, requires no wildcard/stale staged callback, disables Worker dispatch, and restores every cohort flag OFF on any unsuccessful staged run. Cleanup failure is a hard release failure with an alert; every later release preflight rejects stale staged callbacks. Workflow tests inject failure/cancel after every add/acceptance boundary.

Expected: every mandatory staged gate PASS with linked IDs and human review; no raw subject/email/token/permanent URL in evidence. Any failure runs the cleanup job, keeps the formal baseline active, and prevents target Promote. `TARGET_ACCEPTED` is unreachable without a PASS removal/read-back report.

- [ ] **Step 12: Promote the staged target exactly once but keep public capabilities cohort-only/OFF**

Reverify main SHA, manifest hash, staged target ID, Worker digest/heartbeat, schema, provider/catalog/config hashes, OFF/cohort snapshots, baseline rollback evidence, removed temporary staged auth origin, and every staged gate. Promote `STAGED_TARGET_ID` once without rebuild. Confirm the formal domain now reports that same ID and run only the preapproved cohort/OFF formal canary. Do not transition Generation or Private Download—or any downstream commercial capability—to public `ON` while an old public user URL can still return bytes. A mismatch first audits/propagates all affected flags OFF, verifies the recorded distinct `PRIVATE_COMPATIBLE_BASELINE_ID`/runtime bundle, then runs `vercel rollback <recorded-baseline-id-or-url>`, checks `vercel rollback status`, and verifies the formal domain; a second Promote is forbidden.

```powershell
python scripts/release/run_approved_worker_host.py heartbeat --contract release/worker-host-contract.json --deployment-report $env:RUNNER_TEMP/worker-suspended.json --expected-image-digest $env:WORKER_IMAGE_DIGEST --expected-runtime-bundle-id $env:RUNTIME_BUNDLE_ID --require-dispatch-mode enabled --maximum-age-seconds 120 --output "$evidence/worker-version-target-promote.json"
python scripts/release/verify_bundle.py --manifest $env:RELEASE_MANIFEST --api-report "$evidence/staged-target-version.json" --worker-report "$evidence/worker-version-target-promote.json"
npx --yes vercel@55.0.0 promote $stagedTargetUrl --yes --token=$env:VERCEL_TOKEN
npx --yes vercel@55.0.0 promote status --token=$env:VERCEL_TOKEN
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase TARGET_ACCEPTED --phase TARGET_PROMOTED --deployment-url $stagedTargetUrl --formal-base-url $env:PRODUCTION_BASE_URL --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
python scripts/release/apply_activation_plan.py --phase formal-cohort --manifest $env:RELEASE_MANIFEST --deployment-id $env:STAGED_TARGET_ID --canonical-users-report "$evidence/first-login-users.json" --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID --output "$evidence/formal-cohort.json"
npm --prefix frontend run test:e2e -- e2e/production-canary.spec.ts
```

- [ ] **Step 13: Only now delete old public bytes and verify independent invalidation**

Require target Promote PASS, formal canary PASS, private reconciliation PASS, and a fresh proof that the recorded distinct baseline still reads every private reference and is a valid `vercel rollback` target. Then use the separate least-privilege legacy delete credential under the durable parent lease and its own delete child run. Two independent-egress jobs each create their own mode-0600 ephemeral probe file from the signed inventory; no raw URL crosses jobs. After the maximum CDN invalidation window, every old origin/CDN request must be 404/410 and differ from old bytes.

```powershell
python scripts/release/collect_runtime_report.py --base-url $privateCompatibleBaselineUrl --deployment-bypass-header-env VERCEL_AUTOMATION_BYPASS_HEADER --expected-role private-compatible-baseline --expected-runtime-bundle-id $env:RUNTIME_BUNDLE_ID --expected-source-sha $finalSha --output "$evidence/private-compatible-baseline-post-switch.json"
python scripts/release/run_approved_worker_host.py heartbeat --contract release/worker-host-contract.json --deployment-report $env:RUNNER_TEMP/worker-suspended.json --expected-image-digest $env:WORKER_IMAGE_DIGEST --expected-runtime-bundle-id $env:RUNTIME_BUNDLE_ID --require-dispatch-mode enabled --maximum-age-seconds 120 --output "$evidence/worker-version-public-delete.json"
python scripts/release/verify_bundle.py --manifest $env:RELEASE_MANIFEST --api-report "$evidence/private-compatible-baseline-post-switch.json" --worker-report "$evidence/worker-version-public-delete.json"
python scripts/release/verify_private_media.py --inventory "$evidence/post-migration-inventory.json" --migration-report "$evidence/public-media-copy.json" --api-base-url $privateCompatibleBaselineUrl --require-private-read-all --output "$evidence/private-compatible-baseline-rollback.json"
python scripts/release/migrate_public_media.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:media-delete:dry" --mode delete-old-public --legacy-public-store-id-env LEGACY_PUBLIC_BLOB_STORE_ID --legacy-public-delete-token-env LEGACY_PUBLIC_BLOB_DELETE_TOKEN --private-store-id-env PRIVATE_BLOB_STORE_ID --private-read-token-env PRIVATE_BLOB_READ_TOKEN --batch-size 50 --report "$evidence/public-media-delete-dry.json" --dry-run
$env:PUBLIC_MEDIA_DELETE_DRY_SHA256 = (Get-FileHash "$evidence/public-media-delete-dry.json" -Algorithm SHA256).Hash.ToLowerInvariant()
python scripts/release/migrate_public_media.py @common --script-run-id "${env:MIGRATION_PARENT_RUN_ID}:media-delete:write" --mode delete-old-public --legacy-public-store-id-env LEGACY_PUBLIC_BLOB_STORE_ID --legacy-public-delete-token-env LEGACY_PUBLIC_BLOB_DELETE_TOKEN --private-store-id-env PRIVATE_BLOB_STORE_ID --private-read-token-env PRIVATE_BLOB_READ_TOKEN --batch-size 50 --required-dry-run-report "$evidence/public-media-delete-dry.json" --expected-dry-run-sha256 $env:PUBLIC_MEDIA_DELETE_DRY_SHA256 --report "$evidence/public-media-delete.json" --write --resume
python scripts/release/export_legacy_url_probe_manifest.py --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --hmac-key-env INVENTORY_HMAC_KEY --inventory-report "$evidence/post-migration-inventory.json" --inventory-signature "$evidence/post-migration-inventory.sig" --expected-inventory-sha256 $env:POST_MIGRATION_INVENTORY_SHA256 --release-manifest $env:RELEASE_MANIFEST --expected-manifest-sha256 $env:RELEASE_MANIFEST_SHA256 --output "$env:RUNNER_TEMP/legacy-url-probes.json"
node scripts/release/verify_legacy_url_invalidation.mjs --probe-manifest "$env:RUNNER_TEMP/legacy-url-probes.json" --location-id $env:PROBE_LOCATION_ID --requests-per-url 3 --require-status 404,410 --output "$evidence/legacy-url-$env:PROBE_LOCATION_ID.json"
python scripts/release/verify_private_media.py --inventory "$evidence/post-migration-inventory.json" --migration-report "$evidence/public-media-delete.json" --probe-report "$evidence/legacy-url-production-runner.json" --probe-report "$evidence/legacy-url-external-runner.json" --output "$evidence/private-media-final.json"
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase TARGET_PROMOTED --phase PUBLIC_INVALIDATED --delete-report "$evidence/public-media-delete.json" --private-media-report "$evidence/private-media-final.json" --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
```

The export/probe command pair runs separately in the Production runner and approved external runner; the raw file never crosses jobs. Expected: deletion, both complete URL-HMAC sets, and `private-media-final.json` PASS; raw probe files are deleted in `finally`. Cleanup failure, unknown origin/store, redirect, or incomplete coverage is a release FAIL and turns private download/generation OFF as required.

- [ ] **Step 14: Activate incrementally only after old public URL invalidation is complete**

Require Step 13 deletion and both independent invalidation reports PASS and fresh, then apply versioned `ACCEPTANCE_COHORT -> ON` events in activation-plan order. Run a formal-domain ordinary-user canary after each capability and append before/after snapshot/event/report hashes. Any mismatch turns that capability OFF within 60 seconds; if deployment rollback is needed, verify the recorded private-compatible baseline and use `vercel rollback` plus rollback-status/formal checks—never re-Promote it.

```powershell
python scripts/release/apply_activation_plan.py --phase formal-on --manifest $env:RELEASE_MANIFEST --deployment-id $env:STAGED_TARGET_ID --activation-plan release/activation-plan.json --required-invalidation-report "$evidence/private-media-final.json" --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID --output "$evidence/formal-activation.json"
npm --prefix frontend run test:e2e -- e2e/production-canary.spec.ts
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase PUBLIC_INVALIDATED --phase ACTIVATED --activation-report "$evidence/formal-activation.json" --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
```

- [ ] **Step 15: Observe through durable short jobs, then aggregate append-only evidence**

Do not hold one CI job open for 24 hours. The release workflow starts one durable observation row and CASes the release to `OBSERVING`, binding manifest/deployment/Worker/target-snapshot hashes, source SHA, and immutable Private evidence prefix. Each independent `release-observation.yml` scheduled run uses a pinned read-only bootstrap query—not the mutable default-branch application script—to resolve the unique active row from PostgreSQL (or a protected dispatch run ID), then checks out that exact SHA, downloads/verifies the sealed manifest and observation-script hashes, and runs one short signed sample at least every five minutes. The sample job uses a dedicated `Production-Observation` environment/role that may SELECT fixed metrics views and INSERT only an append-only sample for the active run; it has no feature-flag, migration, payment, storage-user-data, deploy, or Promote credential. Evidence uses a separate Private evidence store/token. It never relies on a previous runner's environment variables, workspace, or local `evidence-index.ndjson`, and it does not require human Production approval every five minutes.

Samples check unresolved P0/P1, unhandled signed webhooks, ledger reconciliation, Worker heartbeat under 120 seconds, oldest mandatory outbox under five minutes, synthetic-flow DLQ, acceptance-prefix deletion failures, cleanup status, RLS policy-gap count, and `app_current_user_id()` legacy-fallback usage; both identity counters must remain zero for the full observation window. Missing samples, a gap above the contract threshold, signature/hash drift, flag/bundle drift, or a new release attempting to supersede the row makes observation FAIL; emergency OFF remains allowed and also fails the release. After at least 24 hours and one real cleanup cycle, a separate manually approved Production finalizer resolves coordinates from DB, CASes observation `OBSERVING -> FINALIZING`, reconstructs/aggregates only DB/Private-store hashes, writes and reads back the final report plus final index as create-once objects, and only then locks both control rows and atomically CASes observation `FINALIZING -> PASSED` plus release `OBSERVING -> 7A_ACCEPTED` in one database transaction. A crash during FINALIZING resumes from stored hashes; PASSED can never precede the final index or leave the release behind. No job assumes an unapproved self-hosted runner.

`release-observation.yml` owns failures after OBSERVING for both release kinds; the earlier release workflow cannot clean up a later scheduled/finalizer job. A dedicated least-privilege emergency handler resolves the exact observation/release row from PostgreSQL, atomically marks a genuine failed sample/threshold run FAILED, audits all high-risk flags OFF through an OFF-only database operation, disables the exact Worker, and writes/reads back a create-once failure report. If `complete-finalize` committed before its response was lost, the handler verifies the atomic PASSED+accepted pair and final objects and does not overwrite success; if the row is FINALIZING with valid create-once objects but no final CAS, it resumes the exact finalization. The emergency role cannot turn a flag ON, migrate schema, alter payments, read user media, deploy API, or Promote/rollback a domain.

After a genuine FAILED result, a separate `observation-recovery` job enters the protected Production-Recovery environment and the same global release concurrency group. For `COMMERCIAL_7A`, it resolves the exact recorded private-compatible baseline from the failed activation/manifest, requires schema `0020`, uses `vercel rollback` plus rollback status/formal runtime verification, keeps all high-risk flags OFF, and ensures the exact recorded Worker is stopped or running only with high-risk dispatch disabled; it then records `ROLLED_BACK_PRIVATE_BASELINE` and blocks acceptance. For `CONTRACT_7B`, it first proves schema `0021` has started/completed, refuses every Vercel/Worker rollback to 7a or an old-field bundle, keeps flags OFF/7b Worker stopped, records `FORWARD_FIX_REQUIRED`, and requires a new reviewed contract-line release. Unknown kind/schema/deployment or recovery failure is a manual blocking incident. Static workflow tests require observation-start, one scheduled sample, protected finalizer, emergency handler, and recovery to be distinct dependency-bounded jobs; one sequential shell/job may not sample and immediately finalize. Tests cover sample failure, sample gap/P0/P1, finalizer failure before and after each object, `complete-finalize` before-response ambiguity, cancellation, emergency-handler failure, 7a exact-baseline rollback, and 7b rollback refusal.

`production-release.yml` observation-start job (runs once, then exits):

```powershell
python scripts/release/run_approved_worker_host.py heartbeat --contract release/worker-host-contract.json --deployment-report $env:RUNNER_TEMP/worker-suspended.json --expected-image-digest $env:WORKER_IMAGE_DIGEST --expected-runtime-bundle-id $env:RUNTIME_BUNDLE_ID --require-dispatch-mode enabled --maximum-age-seconds 120 --output "$productionEvidence/worker-version-observation-start.json"
python scripts/release/observe_release.py start --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --manifest $env:RELEASE_MANIFEST --deployment-id $env:STAGED_TARGET_ID --worker-report "$productionEvidence/worker-version-observation-start.json" --target-snapshot-report "$productionEvidence/formal-activation.json" --source-sha $finalSha --private-evidence-prefix $env:PRIVATE_EVIDENCE_PREFIX --minimum-hours 24 --output "$productionEvidence/observation-start.json"
python scripts/release/register_bundle.py advance --kind COMMERCIAL_7A --expected-phase ACTIVATED --phase OBSERVING --observation-start-report "$productionEvidence/observation-start.json" --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
```

`release-observation.yml` scheduled sample job (one sample per fresh runner):

```powershell
python scripts/release/observe_release.py sample --database-url-env OBSERVATION_DATABASE_URL --resolve-active --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_WRITE_TOKEN --signing-key-env OBSERVATION_SIGNING_KEY --output "$env:RUNNER_TEMP/observation-sample.json"
```

`release-observation.yml` protected finalizer job (a separate approved runner after the durable minimum):

```powershell
python scripts/release/observe_release.py prepare-finalize --release-kind commercial-7a --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --resolve-active --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_READ_WRITE_TOKEN --minimum-hours 24 --maximum-gap-minutes 15 --require-cleanup-cycle --output "$env:RUNNER_TEMP/finalize-coordinates.json"
$finalizeCoordinates = Get-Content -Raw $env:RUNNER_TEMP/finalize-coordinates.json | ConvertFrom-Json
python scripts/release/aggregate_gates.py --contract release/gates.json --evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --observation-run-id $finalizeCoordinates.observation_run_id --expected-manifest-sha256 $finalizeCoordinates.manifest_sha256 --expected-source-sha $finalizeCoordinates.source_sha --output "$env:RUNNER_TEMP/final.json"
python scripts/release/append_evidence_index.py --manifest-sha256 $finalizeCoordinates.manifest_sha256 --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_WRITE_TOKEN --observation-run-id $finalizeCoordinates.observation_run_id --report "$env:RUNNER_TEMP/final.json" --evidence-type final-decision --create-once --output "$env:RUNNER_TEMP/final-index.json" --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID
python scripts/release/observe_release.py complete-finalize --release-kind commercial-7a --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --observation-run-id $finalizeCoordinates.observation_run_id --final-report "$env:RUNNER_TEMP/final.json" --final-index-report "$env:RUNNER_TEMP/final-index.json" --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_READ_WRITE_TOKEN --expected-state FINALIZING --expected-release-phase OBSERVING --final-release-phase 7A_ACCEPTED --approval-id-env PRODUCTION_ACCEPTANCE_APPROVAL_ID --output "$env:RUNNER_TEMP/observation-final.json"
python scripts/release/register_bundle.py verify --kind COMMERCIAL_7A --expected-phase 7A_ACCEPTED --observation-final-report "$env:RUNNER_TEMP/observation-final.json" --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL
```

`release-observation.yml` protected `observation-recovery` job (only after the emergency handler records a genuine FAILED row):

```powershell
python scripts/release/observe_release.py resolve-failure --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --resolve-failed --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_READ_TOKEN --output "$env:RUNNER_TEMP/observation-recovery.json"
$observationRecovery = Get-Content -Raw "$env:RUNNER_TEMP/observation-recovery.json" | ConvertFrom-Json
if ($observationRecovery.release_kind -eq 'COMMERCIAL_7A') {
    python scripts/release/run_approved_worker_host.py reconcile-failure --contract release/worker-host-contract.json --release-resolution-report "$env:RUNNER_TEMP/observation-recovery.json" --desired-state stopped --output "$env:RUNNER_TEMP/observation-worker-stopped.json"
    npx --yes vercel@55.0.0 rollback $observationRecovery.private_compatible_baseline_url --yes --token=$env:VERCEL_TOKEN
    npx --yes vercel@55.0.0 rollback status --token=$env:VERCEL_TOKEN
    python scripts/release/collect_runtime_report.py --base-url $env:PRODUCTION_BASE_URL --expected-deployment-id $observationRecovery.private_compatible_baseline_deployment_id --expected-runtime-bundle-id $observationRecovery.runtime_bundle_id --expected-schema 20260710_0020 --output "$env:RUNNER_TEMP/observation-7a-rollback.json"
    python scripts/release/observe_release.py complete-recovery --resolution-report "$env:RUNNER_TEMP/observation-recovery.json" --worker-report "$env:RUNNER_TEMP/observation-worker-stopped.json" --api-report "$env:RUNNER_TEMP/observation-7a-rollback.json" --disposition ROLLED_BACK_PRIVATE_BASELINE --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_RECOVERY_APPROVAL_ID --output "$env:RUNNER_TEMP/observation-recovery-final.json"
} elseif ($observationRecovery.release_kind -eq 'CONTRACT_7B' -and $observationRecovery.schema_revision -eq '20260710_0021') {
    python scripts/release/run_approved_worker_host.py reconcile-failure --contract release/worker-host-contract.json --release-resolution-report "$env:RUNNER_TEMP/observation-recovery.json" --desired-state stopped --output "$env:RUNNER_TEMP/observation-worker-stopped.json"
    python scripts/release/observe_release.py complete-recovery --resolution-report "$env:RUNNER_TEMP/observation-recovery.json" --worker-report "$env:RUNNER_TEMP/observation-worker-stopped.json" --forbid-api-rollback --disposition FORWARD_FIX_REQUIRED --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env PRODUCTION_RECOVERY_APPROVAL_ID --output "$env:RUNNER_TEMP/observation-recovery-final.json"
} else {
    throw 'unknown observation recovery coordinates; manual blocking incident'
}
```

Expected under current known prerequisites: nonzero/NOT_RUN, neither 7a nor the overall product is accepted. Expected only after all external prerequisites and live 7a evidence: PASS with current flag snapshot equal to target and status `7a release accepted`; the overall `Production accepted` label remains forbidden until Task 30 post-contract gates pass. Actual commands, redacted IDs, reviewers, blockers, and rollback status go into a later evidence-only worklog commit; secrets, raw inventory, URLs, object keys, dumps, and checkpoints are never committed.

### Task 30: Perform Independent 7b Contract Cleanup And Rewrite Authoritative Documentation

**Files:**
- Create: `backend/alembic/versions/20260710_0021_contract_cleanup.py`
- Create: `backend/tests/test_contract_cleanup_migration.py`
- Create: `backend/tests/test_contract_release_workflow.py`
- Create: `backend/tests/test_no_retired_imports.py`
- Create: `release/contract-forbidden-references.json`
- Create: `scripts/release/verify_forbidden_references.py`
- Create: `scripts/release/apply_contract_migration.py`
- Create: `scripts/release/run_contract_acceptance.mjs`
- Modify: `release/bundle-manifest.schema.json`
- Modify: `scripts/release/build_manifest.py`
- Modify: `scripts/release/build_runtime_bundle_id.py`
- Modify: `scripts/release/register_bundle.py`
- Modify: `scripts/release/collect_runtime_report.py`
- Modify: `scripts/release/verify_bundle.py`
- Modify: `scripts/release/apply_activation_plan.py`
- Modify: `scripts/release/observe_release.py`
- Modify: `scripts/release/append_evidence_index.py`
- Modify: `scripts/release/run_approved_worker_host.py`
- Modify: `backend/tests/test_release_bundle.py`
- Modify: `backend/tests/test_runtime_bundle_id.py`
- Modify: `backend/tests/test_production_release_workflow.py`
- Modify: `backend/tests/integration/test_identity_rls.py`
- Modify: `release/activation-plan.json`
- Modify: `release/gates.json`
- Modify: `release/runtime-contracts.json`
- Modify: `.github/workflows/release-observation.yml`
- Modify: `.github/workflows/integration.yml`
- Create: `.github/workflows/contract-release.yml`
- Create: `docs/ARCHITECTURE.md`
- Create: `docs/SECURITY.md`
- Create: `docs/OPERATIONS_RUNBOOK.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/PRD.md`
- Modify: `docs/PRODUCTION_ACCEPTANCE.md`
- Modify: `docs/SUPABASE_SETUP.md`
- Modify: `docs/VERCEL_DEPLOYMENT.md`
- Modify: `backend/app/models/user.py`
- Modify: `backend/app/models/order.py`
- Modify: `backend/app/models/live_portrait_job.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/__init__.py`
- Modify: `backend/app/routers/auth/__init__.py`
- Modify: `backend/app/routers/auth/google.py`
- Modify: `backend/app/routers/admin.py`
- Modify: `backend/app/routers/templates.py`
- Modify: `backend/app/routers/ops.py`
- Modify: `backend/app/routers/recommendations.py`
- Modify: `backend/app/core/admin_auth.py`
- Modify: `backend/app/core/user_auth.py`
- Modify: `backend/app/services/admin_service.py`
- Modify: `backend/app/services/credit_service.py`
- Modify: `backend/app/services/evolink_service.py`
- Modify: `backend/app/services/order_creation_service.py`
- Modify: `backend/app/services/provider_workflow.py`
- Modify: `frontend/src/pages/admin/users.vue`
- Modify: `backend/tests/test_admin_management_routes.py`
- Modify: `backend/tests/test_remote_join_config.py`
- Modify: `backend/tests/test_commercial_policy.py`
- Modify: `frontend/tests/unit/AdminRoute.spec.ts`
- Delete: `backend/app/routers/auth/guest.py`
- Delete: `backend/app/routers/session.py`
- Delete: `backend/app/routers/live_portrait.py`
- Delete: `backend/app/services/session_service.py`
- Delete: `backend/app/services/schema_guard_service.py`
- Delete: `backend/app/services/ops_config_service.py`
- Delete: `backend/app/services/lead_crm_service.py`
- Delete: `backend/app/services/generation_stage_service.py`
- Delete: `backend/tests/test_generation_stage_service.py`
- Delete: `backend/data/ops_config.json`
- Modify: `docs/superpowers/plans/2026-04-25-commercial-mvp-production-saas.md`
- Modify: `docs/superpowers/plans/2026-04-25-supabase-auth-credit-ledger.md`
- Modify: `docs/superpowers/plans/2026-04-26-hybrid-payg-subscription.md`
- Modify: `docs/实施任务清单_清洁版.md`
- Modify: `docs/商用切换待办清单_2026-04-10.md`
- Modify: `docs/商用闭环打通说明.md`
- Modify: `DOCUMENTATION_STUDIO_3_0.md`
- Modify: `docs/ai-worklog.md`

**Interfaces:**
- Produces: schema/code/docs with no runtime dependency on OpenID, guest/password, public user URLs, legacy remote join, old queue payloads, runtime DDL, or fallback catalogs.
- Entry gate for Production execution: Task 29 7a release must be accepted for traffic, observed for at least 24 hours, fully reconciled, and rollback-rehearsed. This is not yet the overall `Production accepted` state; only post-contract Task 30 can grant that label. The 7b code/migration/workflow is authored, tested, reviewed, and committed before any Production access, but it refuses Production execution until that entry evidence is fresh and bound to the exact 7a bundle.

- [ ] **Step 1: Write precondition, zero-reference, schema, and forward-fix tests**

`0021.down_revision == 20260710_0020`. The workflow refuses unless 7a immutable manifest hash plus final gate/private migration/observation/rollback evidence objects all PASS and match the promoted target when resolved from the 7a activation row and Private evidence store; caller environment paths are not authority. Extend the immutable bundle schema as a versioned/discriminated union: the already sealed 7a manifest shape remains byte-for-byte read-only verifiable without new required fields or reinterpretation, while the new contract-release variant adds a new pre-deploy `CONTRACT_7B` runtime ID, `schema_before=20260710_0020`, `schema_target=20260710_0021`, contract-migration checksum, pre-contract compatibility version, and the one API deployment/Worker digest that must run on both revisions. The 7b runtime ID is not reused from 7a; `test_runtime_bundle_id.py` requires role, both revisions, migration checksum, compatibility/runtime contracts, source SHA, and new Worker digest before API/Worker deployment. `test_release_bundle.py` fixes canonical fixtures for both versions and rejects a manifest that pretends one observed revision covers both sides.

`release/contract-forbidden-references.json` is the single versioned contract consumed by both unit tests and `verify_forbidden_references.py`; it defines scan roots, token/regex rules, and narrow migration/history allowlists. It requires zero active references to `openid`, `unionid`, `auth_provider`, `auth_subject`, `X-User-OpenID`, `X-Visitor-Id` ownership, guest/password login, `mp-weixin`, `MP-WEIXIN`, `mp_path`, old public user URL fields including retained Live Portrait URLs, legacy `generate_order`, Live Portrait Worker/ComfyUI workflows, Wenwen/ComfyUI image generation/fallbacks, direct credit add/deduct/static package fallbacks, `generation_credit_policy`/`fallback_amount` or `generation_params` billing authority, Admin regenerate/task-ID/debug-round/public-candidate-URL UI, old queue payloads, runtime `create_all/ALTER/CREATE TABLE/CREATE INDEX`, credit guardrail functions, Creator 260, and fallback price/catalog. `username` is retained only as non-authoritative profile data; no login/auth lookup may use it. Admin backend/frontend tests require UUID/identity-metadata search/projection and reject OpenID creation/probe/copy. `test_no_retired_imports.py` walks Python imports and application startup, proving every deleted module has zero reverse imports. Rewrite `test_remote_join_config.py` so it validates PostgreSQL-authoritative flags and absence of JSON fallback instead of importing the deleted `ops_config_service`; rewrite `test_commercial_policy.py` so Live Portrait is permanently 410/no-cost-path while current Web commercial policies remain asserted. Financial/payment/job/audit facts remain immutable and are never dropped.

`contract-release.yml` is manual `workflow_dispatch` only—no push, pull request, schedule, repository/repository-dispatch, or reusable `workflow_call`. Inputs are the exact approved 7b SHA and accepted 7a activation/run ID. It uses the protected Production Environment, one global Production concurrency group with `cancel-in-progress: false`, verifies approved main HEAD and clean tracked source, and acquires/resolves the database release/advisory lease before any deployment/migration secret. Static tests reject a different HEAD, duplicate/concurrent release, non-manual trigger, or caller-provided 7a PASS claim.

Task 30 extends and tests `apply_activation_plan.py`, `observe_release.py`, the protected Preview workflow, the scheduled observation workflow, and evidence finalizer for a `contract-7b` Preview report that proves the same source on temporary schema `0020` and `0021`, discriminated `contract-cohort`, mandatory contract acceptance, `contract-formal-on`, and `contract` observation phases. The Preview publisher signs, writes create-once, reads back, and binds project/source/deployment/build/both-schema/runtime/forbidden-reference/gate contracts; `resolve-preview` rejects a 7a, one-schema, stale, wrong-project, or caller-authored report. Cross-job Production coordinates come only from the CONTRACT_7B activation/observation rows and create-once Private evidence objects; a local evidence directory/index can never satisfy the 24-hour gate.

- [ ] **Step 2: Run contract tests and expect them to refuse early execution**

```powershell
python -m unittest backend.tests.test_contract_cleanup_migration backend.tests.test_contract_release_workflow backend.tests.test_release_bundle -v
```

Expected before 7a Production acceptance: tests PASS for refusal/order contracts and Production execution is NOT_RUN. Do not create a green execution report by substituting local fixtures for the 7a bundle/evidence.

- [ ] **Step 3: Author the independent contract migration with a fail-closed runtime entry gate**

The migration can be authored and tested locally before 7a, but Production execution is guarded. Its transactional/CAS precondition locks the accepted 7a release/observation and current CONTRACT_7B release rows and rechecks: zero active/login-capable account without exactly one `user_identity`; zero duplicate provider subject; exact audited counts for `NORMALIZED | MERGED | SOFT_CLOSED_TOMBSTONED | QUARANTINED_BLOCKING`; zero blocking quarantine that still owns active assets, open money/reconciliation, nonterminal jobs, or claimable login; zero legacy identity fallback use and zero policy gap in every signed sample across the completed 24-hour 7a observation; zero current generation/catalog fallback counters; zero old Order/LivePortrait URL/object/reference; zero old queue/runtime writer; and every forbidden-field count. Tombstoned historical users keep financial/audit/claim lineage without a fabricated identity.

First replace `app_current_user_id()` plus every affected RLS policy with an identity-only `SECURITY DEFINER` resolver owned by a non-login role, `SET search_path = pg_catalog, public`, strict JWT provider/subject validation, no dynamic SQL, `REVOKE ALL FROM PUBLIC`, EXECUTE only for authenticated, and no direct identity-table SELECT. Real PostgreSQL tests assert own-row success, cross-user/direct-table denial, malicious-search-path resistance, and service-role behavior. Then validate previously NOT VALID constraints, apply final active-identity uniqueness contracts, remove compatibility triggers/views, and only then drop `users.openid/unionid/auth_provider/auth_subject/password`, old URL/status/queue compatibility columns, and retired remote-session schema. `users.username` remains non-authoritative profile data. Retained Live Portrait audit rows keep source/video asset IDs, status, cost, and audit timestamps while URL columns disappear. Downgrade refuses destructive reconstruction; after 7b, recovery is a forward schema/code fix or compensating ledger transaction, never rollback to an old-field bundle.

- [ ] **Step 4: Delete retired code and prove no runtime DDL or legacy auth remains**

Delete the exact retired files above and every import before running the app: auth package init no longer imports guest; root router no longer imports session/Live Portrait; `admin_auth.py`, `user_auth.py`, Google auth, and Admin no longer import schema guard; templates/ops/recommendations/credit service no longer import ops config; Admin removes retired CRM calls before `lead_crm_service.py` is deleted. The strict 7a `generation_stage_service.py` compatibility projection and its dedicated test are deleted only after `evolink_service.py`, `order_creation_service.py`, and `provider_workflow.py` stop importing it and read the normalized job/attempt/verdict projector; unknown state remains fail-closed in the authoritative projector. Templates use their versioned public data, ops uses authoritative flag/runtime services, and credit reads the billing catalog. Change Admin service/router/UI search, probe, response, and creation paths from OpenID/UnionID to canonical UUID plus non-secret identity-provider metadata; no compatibility alias may recreate either field. Update the Live Portrait ORM to asset-ID-only retained audit facts and delete its 410-only public router. `test_no_retired_imports.py`, a clean `python -c "from app.main import app"`, and the full suite must all pass after every Delete action.

Task 1/safe-baseline already removed every request-time `Base.metadata.create_all`, `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE`, `CREATE INDEX`, credit guardrail, and ensure-schema mutation; this task proves they remain absent while deleting the now-unused read-only `schema_guard_service.py`. Preserve controlled, audited legacy account-claim history and immutable audit/financial facts; do not delete evidence merely because the login mechanism is retired.

- [ ] **Step 5: Rewrite the authoritative documentation layer**

README documents overseas Web-only startup and current status. PRD documents the real Home/Create/Orders/Account and Partner Invite journeys. Architecture describes Vercel API/Web, long-running Worker, PostgreSQL, Redis, Private Blob, Creem, and Evolink with the unresolved-provider gate if still applicable. Security covers sessions/RLS/upload/SSRF/payment/Admin/logging. Operations covers deployment, feature-off, reconciliation, DLQ, deletion, migration, private-compatible rollback, and post-contract forward fixes. `docs/VERCEL_DEPLOYMENT.md` becomes the current platform appendix: Google/UUID database Admin only, manual protected release workflow, exact runtime/deployment verification, and no `ADMIN_OPENIDS` or automatic main-to-production instruction. Production Acceptance contains executable three-layer evidence rules. Legal docs and documented legal-review status match the actual release.

Mark the seven exact older authorities listed in Files—three Superpowers plans plus `实施任务清单_清洁版.md`, `商用切换待办清单_2026-04-10.md`, `商用闭环打通说明.md`, and root `DOCUMENTATION_STUDIO_3_0.md`—`Historical — not execution authority` at the top; do not silently delete history. Historical text may mention retired systems only inside the documentation scanner's exact path/marker allowlist and can never satisfy an active gate. `docs/实施任务清单.md` is not named by the approved design and is not modified without a separate evidence-based decision.

- [ ] **Step 6: Run final schema, reference, test, type, build, and contract gates**

```powershell
Push-Location backend
$env:DATABASE_URL=$env:TEST_DATABASE_URL
& ..\.venv\Scripts\python.exe scripts/migrate_db.py
& ..\.venv\Scripts\python.exe -m alembic current
& ..\.venv\Scripts\python.exe -c "from app.main import app; assert app is not None"
& ..\.venv\Scripts\python.exe -m unittest discover -s tests -v
Pop-Location
python scripts/release/verify_forbidden_references.py --contract release/contract-forbidden-references.json --mode runtime --report artifacts/contract/runtime-reference-scan.json
python scripts/release/verify_forbidden_references.py --contract release/contract-forbidden-references.json --mode documentation --report artifacts/contract/documentation-reference-scan.json
npm --prefix frontend run typecheck
npm --prefix frontend run test:unit
npm --prefix frontend run build:web
```

Expected: a temporary PostgreSQL database migrates through `20260710_0021`; all tests/builds PASS; runtime scan has zero disallowed hits; documentation scan accepts only exact contract allowlists for migration/history/worklog evidence and rejects an unlabeled active instruction.

- [ ] **Step 7: Commit the immutable 7b code, migration, workflow, and documentation before Production**

```powershell
git diff --check
git add backend/alembic/versions/20260710_0021_contract_cleanup.py backend/tests/test_contract_cleanup_migration.py backend/tests/test_contract_release_workflow.py backend/tests/test_no_retired_imports.py backend/tests/test_release_bundle.py backend/tests/test_runtime_bundle_id.py backend/tests/test_production_release_workflow.py backend/tests/integration/test_identity_rls.py backend/tests/test_admin_management_routes.py backend/tests/test_remote_join_config.py backend/tests/test_commercial_policy.py frontend/tests/unit/AdminRoute.spec.ts release/contract-forbidden-references.json release/bundle-manifest.schema.json release/activation-plan.json release/gates.json release/runtime-contracts.json scripts/release/verify_forbidden_references.py scripts/release/apply_contract_migration.py scripts/release/run_contract_acceptance.mjs scripts/release/build_manifest.py scripts/release/build_runtime_bundle_id.py scripts/release/register_bundle.py scripts/release/collect_runtime_report.py scripts/release/verify_bundle.py scripts/release/apply_activation_plan.py scripts/release/observe_release.py scripts/release/append_evidence_index.py scripts/release/run_approved_worker_host.py .github/workflows/contract-release.yml .github/workflows/release-observation.yml .github/workflows/integration.yml backend/app/models/user.py backend/app/models/order.py backend/app/models/live_portrait_job.py backend/app/models/__init__.py backend/app/main.py backend/app/routers/__init__.py backend/app/routers/auth/__init__.py backend/app/routers/auth/google.py backend/app/routers/admin.py backend/app/routers/templates.py backend/app/routers/ops.py backend/app/routers/recommendations.py backend/app/core/admin_auth.py backend/app/core/user_auth.py backend/app/services/admin_service.py backend/app/services/credit_service.py backend/app/services/evolink_service.py backend/app/services/order_creation_service.py backend/app/services/provider_workflow.py frontend/src/pages/admin/users.vue backend/app/routers/auth/guest.py backend/app/routers/session.py backend/app/routers/live_portrait.py backend/app/services/session_service.py backend/app/services/schema_guard_service.py backend/app/services/ops_config_service.py backend/app/services/lead_crm_service.py backend/app/services/generation_stage_service.py backend/tests/test_generation_stage_service.py backend/data/ops_config.json README.md docs/README.md docs/PRD.md docs/PRODUCTION_ACCEPTANCE.md docs/SUPABASE_SETUP.md docs/VERCEL_DEPLOYMENT.md docs/ARCHITECTURE.md docs/SECURITY.md docs/OPERATIONS_RUNBOOK.md docs/superpowers/plans/2026-04-25-commercial-mvp-production-saas.md docs/superpowers/plans/2026-04-25-supabase-auth-credit-ledger.md docs/superpowers/plans/2026-04-26-hybrid-payg-subscription.md docs/实施任务清单_清洁版.md docs/商用切换待办清单_2026-04-10.md docs/商用闭环打通说明.md DOCUMENTATION_STUDIO_3_0.md docs/ai-worklog.md
git commit -m "refactor: remove legacy runtime contracts"
$contractSha = (git rev-parse HEAD).Trim()
git diff --exit-code
git diff --cached --exit-code
$contractStatus = git status --porcelain=v1 --untracked-files=all
if ($contractStatus) { $contractStatus; throw '7b worktree is not clean, including untracked files' }
git ls-files --error-unmatch backend/alembic/versions/20260710_0021_contract_cleanup.py .github/workflows/contract-release.yml release/contract-forbidden-references.json release/bundle-manifest.schema.json release/gates.json release/activation-plan.json release/runtime-contracts.json scripts/release/build_runtime_bundle_id.py scripts/release/register_bundle.py scripts/release/apply_contract_migration.py scripts/release/observe_release.py
python -m unittest backend.tests.test_contract_cleanup_migration backend.tests.test_contract_release_workflow backend.tests.test_release_bundle backend.tests.test_runtime_bundle_id -v
npm --prefix frontend run typecheck
npm --prefix frontend run test:unit
npm --prefix frontend run build:web
```

Expected: the clean committed 7b SHA contains every code/migration/workflow/doc change and no Production result. The same 7b code passes temporary-PostgreSQL compatibility gates once at schema `0020` and once at `0021`; mandatory CI and the protected Preview real-integration workflow PASS for exactly `$contractSha`. That Preview writes a signed create-once deployment/source/build/schema/contracts report, and Step 8 rejects a stale, 7a, wrong-SHA, or wrong-project Preview ID. Actual 7a evidence remains external and referenced by immutable hashes. Any later source change creates a new reviewed 7b SHA before execution.

- [ ] **Step 8: Deploy compatible code, drain old runtimes, then execute `0021` under one protected lease**

Only after resolving the accepted 7a row/evidence from PostgreSQL and Private evidence storage, reserve one `CONTRACT_7B` activation before the first external write. Build/push the exact committed 7b Worker once, resolve its immutable digest, compute a new role-specific CONTRACT_7B runtime ID, and inject it into one suspended Worker deployment plus one unbound Vercel API deployment built once. The code no longer reads old fields but tolerates their presence. Seal/register a 7b manifest with runtime ID, exact API/Worker IDs, prebuilt checksum, `schema_before`, `schema_target`, migration checksum, Preview ID, forbidden/runtime/gate/activation contract hashes, and accepted 7a parent hash.

Against current schema `0020`, start the exact Worker with dispatch disabled and verify the unbound API/Worker. Promote that exact deployment under maintenance/flags OFF, stop the exact 7a Worker, wait max Function duration plus margin, and prove old API/Worker/queue drain. Produce a fresh signed inventory and fresh Task 3 restore rehearsal whose temporary database/credential destruction PASS; stale 7a inventory/backup evidence cannot authorize a destructive migration. Bind one durable parent migration lease to the CONTRACT_7B row and one child ID to the contract write. Run `0021` once; then verify the same already-deployed API/Worker against the formal domain at `schema_target` without rebuild. The durable phase machine is `RESERVED -> WORKER_STAGED -> API_STAGED -> MANIFEST_SEALED -> WORKER_RUNNING_DISABLED -> PRE_CONTRACT_VERIFIED -> TARGET_PROMOTED -> DRAINED -> SCHEMA_0021 -> POST_VERIFIED -> WORKER_DISPATCH_ENABLED -> COHORT_ACCEPTED -> ACTIVATED -> OBSERVING -> PRODUCTION_ACCEPTED`; each external effect is CAS-persisted and recoverable by exact ID/hash. The `resolve CONTRACT_7B`/JSON parsing prologue shown below is repeated at every fresh protected job to re-download and verify the manifest and recreate `$contractSha`, runtime/deployment/Worker coordinates; inherited shell variables or prior runner files are forbidden. After the irreversible point, a pre-contract deployment is forbidden; failures use a new reviewed forward code/schema fix on the contract line.

```powershell
$contractPreview = python scripts/release/register_bundle.py resolve-preview --release-kind contract-7b --source-sha $contractSha --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_READ_TOKEN --require-pass --output $env:RUNNER_TEMP/contract-preview-resolution.json
$release7a = python scripts/release/register_bundle.py resolve --kind COMMERCIAL_7A --expected-phase 7A_ACCEPTED --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_READ_TOKEN --output $env:RUNNER_TEMP/release-7a-resolution.json
python scripts/release/register_bundle.py reserve --kind CONTRACT_7B --environment production --source-sha $contractSha --preview-resolution-report $env:RUNNER_TEMP/contract-preview-resolution.json --parent-resolution-report $env:RUNNER_TEMP/release-7a-resolution.json --workflow-run-id $env:GITHUB_RUN_ID --workflow-attempt $env:GITHUB_RUN_ATTEMPT --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID --output $env:RUNNER_TEMP/contract-reserved.json
python scripts/release/run_approved_worker_host.py build-push --contract release/worker-host-contract.json --source-sha $contractSha --release-kind contract-7b --output $env:RUNNER_TEMP/contract-worker-build.json
$contractWorkerBuild = Get-Content -Raw $env:RUNNER_TEMP/contract-worker-build.json | ConvertFrom-Json
$env:CONTRACT_WORKER_IMAGE_DIGEST = $contractWorkerBuild.image_digest
$contractRuntime = python scripts/release/build_runtime_bundle_id.py --release-role CONTRACT_7B --source-sha $contractSha --schema-before 20260710_0020 --schema-target 20260710_0021 --contract-migration backend/alembic/versions/20260710_0021_contract_cleanup.py --compatibility-version contract-7b.pre0021.v1 --worker-image-digest $env:CONTRACT_WORKER_IMAGE_DIGEST --runtime-contract release/runtime-contracts.json --provider-contract release/provider-contracts.json --catalog-contract release/catalog/catalog-2026-07-10.json --flag-contract release/gates.json --activation-plan release/activation-plan.json --forbidden-reference-contract release/contract-forbidden-references.json --worker-host-contract release/worker-host-contract.json --builder-contract-version contract-7b.v1 --output $env:RUNNER_TEMP/contract-runtime-bundle-id.txt
$env:CONTRACT_RUNTIME_BUNDLE_ID = $contractRuntime.Trim()
$env:RUNTIME_BUNDLE_ID = $env:CONTRACT_RUNTIME_BUNDLE_ID
python scripts/release/run_approved_worker_host.py deploy-suspended --contract release/worker-host-contract.json --source-sha $contractSha --release-kind contract-7b --image-digest $env:CONTRACT_WORKER_IMAGE_DIGEST --runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --output $env:RUNNER_TEMP/contract-worker-suspended.json
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase RESERVED --phase WORKER_STAGED --runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --worker-build-report $env:RUNNER_TEMP/contract-worker-build.json --worker-deployment-report $env:RUNNER_TEMP/contract-worker-suspended.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
npx --yes vercel@55.0.0 pull --yes --environment=production --token=$env:VERCEL_TOKEN
npx --yes vercel@55.0.0 build --prod --token=$env:VERCEL_TOKEN
$contractTargetUrl = npx --yes vercel@55.0.0 deploy --prebuilt --prod --skip-domain --env RUNTIME_BUNDLE_ID=$env:CONTRACT_RUNTIME_BUNDLE_ID --token=$env:VERCEL_TOKEN
npx --yes vercel@55.0.0 inspect $contractTargetUrl --token=$env:VERCEL_TOKEN > $env:RUNNER_TEMP/contract-target-inspect.txt
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase WORKER_STAGED --phase API_STAGED --runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --deployment-url $contractTargetUrl --deployment-role contract-target --build-output .vercel/output --inspect-report $env:RUNNER_TEMP/contract-target-inspect.txt --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
$env:CONTRACT_RELEASE_MANIFEST = "$env:RUNNER_TEMP/contract-7b-bundle-manifest.json"
python scripts/release/build_manifest.py --release-kind contract-7b --runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --source-sha $contractSha --preview-resolution-report $env:RUNNER_TEMP/contract-preview-resolution.json --staged-target-url $contractTargetUrl --staged-target-inspect $env:RUNNER_TEMP/contract-target-inspect.txt --worker-report $env:RUNNER_TEMP/contract-worker-suspended.json --schema-before 20260710_0020 --schema-target 20260710_0021 --contract-migration backend/alembic/versions/20260710_0021_contract_cleanup.py --parent-release-resolution $env:RUNNER_TEMP/release-7a-resolution.json --contracts release --output $env:CONTRACT_RELEASE_MANIFEST
$env:CONTRACT_RELEASE_MANIFEST_SHA256 = (Get-FileHash $env:CONTRACT_RELEASE_MANIFEST -Algorithm SHA256).Hash.ToLowerInvariant()
python scripts/release/register_bundle.py seal --kind CONTRACT_7B --expected-phase API_STAGED --phase MANIFEST_SEALED --manifest $env:CONTRACT_RELEASE_MANIFEST --expected-manifest-sha256 $env:CONTRACT_RELEASE_MANIFEST_SHA256 --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_WRITE_TOKEN --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
python scripts/release/register_bundle.py resolve --kind CONTRACT_7B --source-sha $contractSha --minimum-phase MANIFEST_SEALED --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_READ_TOKEN --manifest-output $env:RUNNER_TEMP/contract-7b-bundle-manifest.json --output $env:RUNNER_TEMP/contract-7b-coordinates.json
$contractCoordinates = Get-Content -Raw $env:RUNNER_TEMP/contract-7b-coordinates.json | ConvertFrom-Json
$contractSha = $contractCoordinates.source_sha
$env:CONTRACT_RUNTIME_BUNDLE_ID = $contractCoordinates.runtime_bundle_id
$env:RUNTIME_BUNDLE_ID = $contractCoordinates.runtime_bundle_id
$env:CONTRACT_RELEASE_MANIFEST = $contractCoordinates.manifest_local_path
$env:CONTRACT_RELEASE_MANIFEST_SHA256 = $contractCoordinates.manifest_sha256
$env:CONTRACT_DEPLOYMENT_ID = $contractCoordinates.staged_target_deployment_id
$contractTargetUrl = $contractCoordinates.staged_target_url
$env:CONTRACT_WORKER_IMAGE_DIGEST = $contractCoordinates.worker_image_digest
$env:CONTRACT_WORKER_DEPLOYMENT_ID = $contractCoordinates.worker_deployment_id
python scripts/release/run_approved_worker_host.py start --contract release/worker-host-contract.json --release-resolution-report $env:RUNNER_TEMP/contract-7b-coordinates.json --image-digest $env:CONTRACT_WORKER_IMAGE_DIGEST --runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --dispatch-mode disabled --output $env:RUNNER_TEMP/contract-worker-start.json
python scripts/release/run_approved_worker_host.py heartbeat --contract release/worker-host-contract.json --release-resolution-report $env:RUNNER_TEMP/contract-7b-coordinates.json --expected-image-digest $env:CONTRACT_WORKER_IMAGE_DIGEST --expected-runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --require-dispatch-mode disabled --output $env:RUNNER_TEMP/contract-pre-worker.json
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase MANIFEST_SEALED --phase WORKER_RUNNING_DISABLED --worker-start-report $env:RUNNER_TEMP/contract-worker-start.json --worker-heartbeat-report $env:RUNNER_TEMP/contract-pre-worker.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
python scripts/release/collect_runtime_report.py --base-url $contractTargetUrl --deployment-bypass-header-env VERCEL_AUTOMATION_BYPASS_HEADER --expected-role contract-target --expected-runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --expected-source-sha $contractSha --expected-schema 20260710_0020 --output $env:RUNNER_TEMP/contract-pre-api.json
python scripts/release/verify_bundle.py --manifest $env:CONTRACT_RELEASE_MANIFEST --phase pre-contract --api-report $env:RUNNER_TEMP/contract-pre-api.json --worker-report $env:RUNNER_TEMP/contract-pre-worker.json
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase WORKER_RUNNING_DISABLED --phase PRE_CONTRACT_VERIFIED --api-report $env:RUNNER_TEMP/contract-pre-api.json --worker-report $env:RUNNER_TEMP/contract-pre-worker.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
npx --yes vercel@55.0.0 promote $contractTargetUrl --yes --token=$env:VERCEL_TOKEN
python scripts/release/collect_runtime_report.py --base-url $env:PRODUCTION_BASE_URL --expected-role contract-target --expected-runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --expected-source-sha $contractSha --expected-schema 20260710_0020 --output $env:RUNNER_TEMP/contract-formal-pre-api.json
$env:CONTRACT_DEPLOYMENT_ID = (Get-Content -Raw $env:RUNNER_TEMP/contract-formal-pre-api.json | ConvertFrom-Json).deployment_id
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase PRE_CONTRACT_VERIFIED --phase TARGET_PROMOTED --deployment-url $contractTargetUrl --formal-api-report $env:RUNNER_TEMP/contract-formal-pre-api.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
python scripts/release/run_approved_worker_host.py stop --contract release/worker-host-contract.json --release-resolution-report $env:RUNNER_TEMP/release-7a-resolution.json --reason contract-cutover --output $env:RUNNER_TEMP/release-7a-worker-stop.json
$env:CONTRACT_DRAIN_REPORT = "$env:RUNNER_TEMP/contract-drain.json"
python scripts/release/verify_runtime_drain.py --formal-base-url $env:PRODUCTION_BASE_URL --expected-api-deployment-id $env:CONTRACT_DEPLOYMENT_ID --expected-worker-report $env:RUNNER_TEMP/contract-pre-worker.json --old-release-resolution-report $env:RUNNER_TEMP/release-7a-resolution.json --old-worker-stop-report $env:RUNNER_TEMP/release-7a-worker-stop.json --legacy-queue-name generate_order --max-api-duration-seconds 300 --output $env:CONTRACT_DRAIN_REPORT
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase TARGET_PROMOTED --phase DRAINED --drain-report $env:CONTRACT_DRAIN_REPORT --old-worker-stop-report $env:RUNNER_TEMP/release-7a-worker-stop.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
$contractEvidence = "artifacts/release/$contractSha/$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT/$env:CONTRACT_DEPLOYMENT_ID/03-production"
python scripts/release/inventory_production.py --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --hmac-key-env INVENTORY_HMAC_KEY --output "$contractEvidence/contract-inventory.json" --signature-output "$contractEvidence/contract-inventory.sig"
$env:CONTRACT_INVENTORY_REPORT = "$contractEvidence/contract-inventory.json"
$env:CONTRACT_INVENTORY_SIGNATURE = "$contractEvidence/contract-inventory.sig"
$env:CONTRACT_INVENTORY_SHA256 = (Get-FileHash $env:CONTRACT_INVENTORY_REPORT -Algorithm SHA256).Hash.ToLowerInvariant()
python backend/scripts/backup_restore_rehearsal.py --source-url-env PRODUCTION_READ_ONLY_DATABASE_URL --target-url-env RESTORE_REHEARSAL_DATABASE_URL --target-admin-url-env RESTORE_REHEARSAL_ADMIN_DATABASE_URL --target-role-name-env RESTORE_REHEARSAL_ROLE_NAME --expected-target-db-prefix vowpic_restore_ --artifact-dir "$env:RUNNER_TEMP/contract-restore"
$env:CONTRACT_ENTRY_REPORT = "$contractEvidence/contract-entry.json"
python scripts/release/verify_forbidden_references.py --contract release/contract-forbidden-references.json --mode production-entry --release-7a-resolution-report $env:RUNNER_TEMP/release-7a-resolution.json --contract-manifest $env:CONTRACT_RELEASE_MANIFEST --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --report $env:CONTRACT_ENTRY_REPORT
python scripts/release/register_bundle.py bind-migration-parent --kind CONTRACT_7B --expected-phase DRAINED --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID --output $env:RUNNER_TEMP/contract-migration-parent.json
$env:CONTRACT_MIGRATION_PARENT_RUN_ID = (Get-Content -Raw $env:RUNNER_TEMP/contract-migration-parent.json | ConvertFrom-Json).migration_parent_run_id
python scripts/release/apply_contract_migration.py --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --migration-parent-run-id $env:CONTRACT_MIGRATION_PARENT_RUN_ID --script-run-id "${env:CONTRACT_MIGRATION_PARENT_RUN_ID}:contract-0021:dry" --inventory-report $env:CONTRACT_INVENTORY_REPORT --inventory-signature $env:CONTRACT_INVENTORY_SIGNATURE --expected-inventory-sha256 $env:CONTRACT_INVENTORY_SHA256 --release-manifest $env:CONTRACT_RELEASE_MANIFEST --expected-manifest-sha256 $env:CONTRACT_RELEASE_MANIFEST_SHA256 --entry-report $env:CONTRACT_ENTRY_REPORT --drain-report $env:CONTRACT_DRAIN_REPORT --approval-id-env CONTRACT_RELEASE_APPROVAL_ID --target-revision 20260710_0021 --report $env:RUNNER_TEMP/contract-migration-dry.json --dry-run
$env:CONTRACT_MIGRATION_DRY_SHA256 = (Get-FileHash $env:RUNNER_TEMP/contract-migration-dry.json -Algorithm SHA256).Hash.ToLowerInvariant()
$env:CONTRACT_MIGRATION_REPORT = "$contractEvidence/contract-migration.json"
python scripts/release/apply_contract_migration.py --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --migration-parent-run-id $env:CONTRACT_MIGRATION_PARENT_RUN_ID --script-run-id "${env:CONTRACT_MIGRATION_PARENT_RUN_ID}:contract-0021:write" --inventory-report $env:CONTRACT_INVENTORY_REPORT --inventory-signature $env:CONTRACT_INVENTORY_SIGNATURE --expected-inventory-sha256 $env:CONTRACT_INVENTORY_SHA256 --release-manifest $env:CONTRACT_RELEASE_MANIFEST --expected-manifest-sha256 $env:CONTRACT_RELEASE_MANIFEST_SHA256 --entry-report $env:CONTRACT_ENTRY_REPORT --drain-report $env:CONTRACT_DRAIN_REPORT --required-dry-run-report $env:RUNNER_TEMP/contract-migration-dry.json --expected-dry-run-sha256 $env:CONTRACT_MIGRATION_DRY_SHA256 --approval-id-env CONTRACT_RELEASE_APPROVAL_ID --target-revision 20260710_0021 --report $env:CONTRACT_MIGRATION_REPORT --write
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase DRAINED --phase SCHEMA_0021 --migration-parent-run-id $env:CONTRACT_MIGRATION_PARENT_RUN_ID --migration-report $env:CONTRACT_MIGRATION_REPORT --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
python scripts/release/collect_runtime_report.py --base-url $env:PRODUCTION_BASE_URL --expected-role contract-target --expected-runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --expected-source-sha $contractSha --expected-schema 20260710_0021 --output $env:RUNNER_TEMP/contract-post-api.json
python scripts/release/run_approved_worker_host.py heartbeat --contract release/worker-host-contract.json --release-resolution-report $env:RUNNER_TEMP/contract-7b-coordinates.json --expected-image-digest $env:CONTRACT_WORKER_IMAGE_DIGEST --expected-runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --require-dispatch-mode disabled --output $env:RUNNER_TEMP/contract-post-worker.json
python scripts/release/verify_bundle.py --manifest $env:CONTRACT_RELEASE_MANIFEST --phase post-contract --api-report $env:RUNNER_TEMP/contract-post-api.json --worker-report $env:RUNNER_TEMP/contract-post-worker.json
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase SCHEMA_0021 --phase POST_VERIFIED --api-report $env:RUNNER_TEMP/contract-post-api.json --worker-report $env:RUNNER_TEMP/contract-post-worker.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
```

- [ ] **Step 9: Rebind the exact 7b deployment, run post-contract gates, and append evidence**

Because every non-OFF flag is bound to an exact runtime bundle/deployment, promoting the 7b deployment leaves high-risk capabilities OFF. Enable dispatch only on the exact 7b Worker and CAS that external effect. Bind only the retained canonical 7a acceptance users to `ACCEPTANCE_COHORT`; then run the new contract acceptance runner against the formal domain. It must link and recheck RLS/cross-user/auth/Admin/ledger/job/private media/payment/subscription/generation/Partner/browser behavior, the exact identity-disposition counts, and zero forbidden runtime references. Only after all mandatory contract-cohort reports PASS may the activation plan move the exact bundle/deployment to its target ON snapshot and run a formal ordinary-user canary after each capability. Any failure returns flags OFF, disables dispatch, and requires a new reviewed forward 7b bundle; 0021 is not downgraded.

Reuse the durable short-job observation for at least 24 hours and one cleanup cycle, with zero forbidden-reference/fallback/RLS/P0/P1/reconciliation errors. Each scheduled contract sample resolves the unique CONTRACT_7B observation row and exact source/manifest from PostgreSQL/Private evidence storage. The finalizer follows `OBSERVING -> FINALIZING`, aggregates DB/store hashes, creates and reads back the final report/index, then atomically CASes observation `FINALIZING -> PASSED` plus release `OBSERVING -> PRODUCTION_ACCEPTED`. After OBSERVING, the shared `release-observation.yml` emergency/resume handler defined in Task 29—not the already-ended contract workflow—owns sample/finalizer failure and complete-before-response ambiguity. A local `CONTRACT_EVIDENCE_DIR`, inherited shell variable, or one sample immediately followed by finalize is forbidden. Record actual results in a later evidence-only worklog commit; never amend the 7b code commit or bundle manifest.

`contract-release.yml` has an independent protected `if: failure() || cancelled()` cleanup/reconcile job. It resolves by exact approved 7b source SHA plus workflow run/attempt with `--allow-absent`. A pre-reservation `NO_ACTIVE_RELEASE/ENTRY_REJECTED` branch proves no 7b Production Worker/API/domain/schema effect, writes/read-backs a sanitized create-once entry-refusal report, and never creates or blocks a fake release; a reserve-commit/response-loss boundary must rediscover the exact row instead. For a real row, cleanup resolves current schema, inspects the actual formal-domain deployment even when the phase CAS is stale, audits every cohort/high-risk flag OFF, and reconciles the 7b Worker with `allow-absent` for failures before deployment. If the formal domain already serves 7b while schema remains `0020`—including Promote-success/CAS-failure—it stops the exact 7b Worker, runs `vercel rollback` to the accepted 7a target resolved from the parent activation, ensures the exact accepted 7a Worker/digest/runtime bundle is running, and verifies formal API plus heartbeat; a second Promote is forbidden. If formal already serves 7a, it verifies rather than rolling back again. Any unknown deployment/ambiguous candidate is a hard manual-forward stop. If `0021` has started/completed, it keeps the exact 7b Worker stopped/disabled and records `FORWARD_FIX_REQUIRED` without schema downgrade or old-bundle rollback. All row-owning branches write/read-back a create-once cleanup report and CAS-record the observed failed phase/disposition. Cleanup is idempotent and uses no failed-job workspace; cleanup failure keeps the workflow failed, alerts operators, and blocks every later release. Tests inject failure/cancel immediately before/after entry resolution, reserve commit and every external effect, especially Worker deploy/start and Promote-before-CAS, plus drain, dry-run/write, dispatch, cohort, each ON transition, and observation start; observation/finalization failures are injected in the shared observation workflow.

`contract-release.yml` activation and observation-start job (runs once, then exits):

```powershell
python scripts/release/run_approved_worker_host.py set-dispatch --contract release/worker-host-contract.json --release-resolution-report $env:RUNNER_TEMP/contract-7b-coordinates.json --expected-image-digest $env:CONTRACT_WORKER_IMAGE_DIGEST --runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --mode enabled --output $env:RUNNER_TEMP/contract-worker-dispatch-enabled.json
python scripts/release/run_approved_worker_host.py heartbeat --contract release/worker-host-contract.json --release-resolution-report $env:RUNNER_TEMP/contract-7b-coordinates.json --expected-image-digest $env:CONTRACT_WORKER_IMAGE_DIGEST --expected-runtime-bundle-id $env:CONTRACT_RUNTIME_BUNDLE_ID --require-dispatch-mode enabled --maximum-age-seconds 120 --output $env:RUNNER_TEMP/contract-worker-enabled.json
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase POST_VERIFIED --phase WORKER_DISPATCH_ENABLED --worker-dispatch-report $env:RUNNER_TEMP/contract-worker-dispatch-enabled.json --worker-heartbeat-report $env:RUNNER_TEMP/contract-worker-enabled.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
python scripts/release/apply_activation_plan.py --phase contract-cohort --manifest $env:CONTRACT_RELEASE_MANIFEST --deployment-id $env:CONTRACT_DEPLOYMENT_ID --release-7a-resolution-report $env:RUNNER_TEMP/release-7a-resolution.json --approval-id-env CONTRACT_RELEASE_APPROVAL_ID --output $env:RUNNER_TEMP/contract-cohort.json
node scripts/release/run_contract_acceptance.mjs --base-url $env:PRODUCTION_BASE_URL --manifest $env:CONTRACT_RELEASE_MANIFEST --cohort-report $env:RUNNER_TEMP/contract-cohort.json --release-7a-resolution-report $env:RUNNER_TEMP/release-7a-resolution.json --output $env:RUNNER_TEMP/contract-acceptance.json
python scripts/release/verify_forbidden_references.py --contract release/contract-forbidden-references.json --mode production-post-contract --contract-manifest $env:CONTRACT_RELEASE_MANIFEST --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --report $env:RUNNER_TEMP/contract-reference-scan.json
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase WORKER_DISPATCH_ENABLED --phase COHORT_ACCEPTED --cohort-report $env:RUNNER_TEMP/contract-cohort.json --acceptance-report $env:RUNNER_TEMP/contract-acceptance.json --reference-scan-report $env:RUNNER_TEMP/contract-reference-scan.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
python scripts/release/apply_activation_plan.py --phase contract-formal-on --manifest $env:CONTRACT_RELEASE_MANIFEST --deployment-id $env:CONTRACT_DEPLOYMENT_ID --activation-plan release/activation-plan.json --required-contract-acceptance-report $env:RUNNER_TEMP/contract-acceptance.json --required-reference-scan-report $env:RUNNER_TEMP/contract-reference-scan.json --approval-id-env CONTRACT_RELEASE_APPROVAL_ID --output $env:RUNNER_TEMP/contract-activation.json
npm --prefix frontend run test:e2e -- e2e/production-canary.spec.ts
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase COHORT_ACCEPTED --phase ACTIVATED --activation-report $env:RUNNER_TEMP/contract-activation.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
python scripts/release/observe_release.py start --release-kind contract-7b --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --manifest $env:CONTRACT_RELEASE_MANIFEST --deployment-id $env:CONTRACT_DEPLOYMENT_ID --worker-report $env:RUNNER_TEMP/contract-worker-enabled.json --target-snapshot-report $env:RUNNER_TEMP/contract-activation.json --source-sha $contractSha --resolve-private-evidence-prefix --minimum-hours 24 --output $env:RUNNER_TEMP/contract-observation-start.json
python scripts/release/register_bundle.py advance --kind CONTRACT_7B --expected-phase ACTIVATED --phase OBSERVING --observation-start-report $env:RUNNER_TEMP/contract-observation-start.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
```

`release-observation.yml` scheduled contract sample job (one sample per fresh runner):

```powershell
python scripts/release/observe_release.py sample --release-kind contract-7b --database-url-env OBSERVATION_DATABASE_URL --resolve-active --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_WRITE_TOKEN --signing-key-env OBSERVATION_SIGNING_KEY --output $env:RUNNER_TEMP/contract-observation-sample.json
```

`release-observation.yml` protected contract finalizer job (a separate approved runner after the durable minimum):

```powershell
python scripts/release/observe_release.py prepare-finalize --release-kind contract-7b --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --resolve-active --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_READ_WRITE_TOKEN --minimum-hours 24 --maximum-gap-minutes 15 --require-cleanup-cycle --output $env:RUNNER_TEMP/contract-finalize-coordinates.json
$contractFinalize = Get-Content -Raw $env:RUNNER_TEMP/contract-finalize-coordinates.json | ConvertFrom-Json
python scripts/release/aggregate_gates.py --contract release/gates.json --release-variant contract-7b --evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --observation-run-id $contractFinalize.observation_run_id --expected-manifest-sha256 $contractFinalize.manifest_sha256 --expected-source-sha $contractFinalize.source_sha --output $env:RUNNER_TEMP/contract-final.json
python scripts/release/append_evidence_index.py --manifest-sha256 $contractFinalize.manifest_sha256 --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_WRITE_TOKEN --observation-run-id $contractFinalize.observation_run_id --report $env:RUNNER_TEMP/contract-final.json --evidence-type contract-final-decision --create-once --output $env:RUNNER_TEMP/contract-final-index.json --approval-id-env CONTRACT_RELEASE_APPROVAL_ID
python scripts/release/observe_release.py complete-finalize --release-kind contract-7b --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --observation-run-id $contractFinalize.observation_run_id --final-report $env:RUNNER_TEMP/contract-final.json --final-index-report $env:RUNNER_TEMP/contract-final-index.json --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_READ_WRITE_TOKEN --expected-state FINALIZING --expected-release-phase OBSERVING --final-release-phase PRODUCTION_ACCEPTED --approval-id-env CONTRACT_RELEASE_APPROVAL_ID --output $env:RUNNER_TEMP/contract-observation-final.json
python scripts/release/register_bundle.py verify --kind CONTRACT_7B --expected-phase PRODUCTION_ACCEPTED --observation-final-report $env:RUNNER_TEMP/contract-observation-final.json --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL
```

Failure/cancel cleanup job (not the success path):

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
python scripts/release/register_bundle.py resolve --kind CONTRACT_7B --source-sha $env:APPROVED_CONTRACT_SHA --workflow-run-id $env:GITHUB_RUN_ID --workflow-attempt $env:GITHUB_RUN_ATTEMPT --active-or-forward-fix --allow-absent --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --output $env:RUNNER_TEMP/contract-failure-resolution.json
$failureResolution = Get-Content -Raw $env:RUNNER_TEMP/contract-failure-resolution.json | ConvertFrom-Json
if ($failureResolution.disposition -in @('NO_ACTIVE_RELEASE','ENTRY_REJECTED')) {
    python scripts/release/collect_runtime_report.py --base-url $env:PRODUCTION_BASE_URL --edge-bypass-header-env EDGE_RULE_BYPASS_HEADER --forbid-production-source-sha $env:APPROVED_CONTRACT_SHA --allow-unavailable --output $env:RUNNER_TEMP/contract-entry-formal-check.json
    python scripts/release/run_approved_worker_host.py verify-no-release --contract release/worker-host-contract.json --source-sha $env:APPROVED_CONTRACT_SHA --release-kind contract-7b --output $env:RUNNER_TEMP/contract-entry-worker-check.json
    python scripts/release/register_bundle.py record-entry-refusal --kind CONTRACT_7B --resolution-report $env:RUNNER_TEMP/contract-failure-resolution.json --formal-report $env:RUNNER_TEMP/contract-entry-formal-check.json --worker-report $env:RUNNER_TEMP/contract-entry-worker-check.json --expected-schema 20260710_0020 --require-no-release-row --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_WRITE_TOKEN --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --output $env:RUNNER_TEMP/contract-entry-refusal.json
} else {
    python scripts/release/apply_activation_plan.py --phase emergency-off --release-resolution-report $env:RUNNER_TEMP/contract-failure-resolution.json --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID --output $env:RUNNER_TEMP/contract-emergency-off.json
    python scripts/release/collect_runtime_report.py --base-url $env:PRODUCTION_BASE_URL --edge-bypass-header-env EDGE_RULE_BYPASS_HEADER --allow-any-registered-deployment --allow-unavailable --output $env:RUNNER_TEMP/contract-failure-formal-observation.json
    python scripts/release/run_approved_worker_host.py reconcile-failure --contract release/worker-host-contract.json --release-resolution-report $env:RUNNER_TEMP/contract-failure-resolution.json --desired-state stopped --allow-absent --output $env:RUNNER_TEMP/contract-worker-reconcile.json
    python scripts/release/register_bundle.py plan-failure-recovery --kind CONTRACT_7B --release-resolution-report $env:RUNNER_TEMP/contract-failure-resolution.json --formal-observation-report $env:RUNNER_TEMP/contract-failure-formal-observation.json --worker-reconcile-report $env:RUNNER_TEMP/contract-worker-reconcile.json --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --output $env:RUNNER_TEMP/contract-recovery-plan.json
    $recoveryPlan = Get-Content -Raw $env:RUNNER_TEMP/contract-recovery-plan.json | ConvertFrom-Json
    if ($recoveryPlan.action -eq 'AMBIGUOUS') { throw 'contract recovery state is ambiguous; manual forward disposition required' }
    if ($recoveryPlan.action -in @('ROLLBACK_7A','ALREADY_7A')) {
        python scripts/release/register_bundle.py resolve-parent --child-resolution-report $env:RUNNER_TEMP/contract-failure-resolution.json --expected-kind COMMERCIAL_7A --expected-phase 7A_ACCEPTED --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_READ_TOKEN --output $env:RUNNER_TEMP/release-7a-failure-resolution.json
        $release7aFailure = Get-Content -Raw $env:RUNNER_TEMP/release-7a-failure-resolution.json | ConvertFrom-Json
        if ($recoveryPlan.action -eq 'ROLLBACK_7A') {
            npx --yes vercel@55.0.0 rollback $release7aFailure.target_deployment_url --yes --token=$env:VERCEL_TOKEN
            npx --yes vercel@55.0.0 rollback status --token=$env:VERCEL_TOKEN
        }
        python scripts/release/run_approved_worker_host.py ensure-running --contract release/worker-host-contract.json --release-resolution-report $env:RUNNER_TEMP/release-7a-failure-resolution.json --dispatch-mode enabled --output $env:RUNNER_TEMP/release-7a-worker-restart.json
        python scripts/release/run_approved_worker_host.py heartbeat --contract release/worker-host-contract.json --release-resolution-report $env:RUNNER_TEMP/release-7a-failure-resolution.json --require-dispatch-mode enabled --maximum-age-seconds 120 --output $env:RUNNER_TEMP/release-7a-worker-heartbeat.json
        python scripts/release/collect_runtime_report.py --base-url $env:PRODUCTION_BASE_URL --release-resolution-report $env:RUNNER_TEMP/release-7a-failure-resolution.json --expected-schema 20260710_0020 --output $env:RUNNER_TEMP/release-7a-formal-rollback.json
        python scripts/release/register_bundle.py record-failure --kind CONTRACT_7B --release-resolution-report $env:RUNNER_TEMP/contract-failure-resolution.json --off-report $env:RUNNER_TEMP/contract-emergency-off.json --worker-reconcile-report $env:RUNNER_TEMP/contract-worker-reconcile.json --rollback-api-report $env:RUNNER_TEMP/release-7a-formal-rollback.json --rollback-worker-report $env:RUNNER_TEMP/release-7a-worker-heartbeat.json --disposition ROLLED_BACK_TO_ACCEPTED_7A --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_WRITE_TOKEN --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID --output $env:RUNNER_TEMP/contract-cleanup-final.json
    } else {
        python scripts/release/register_bundle.py record-failure --kind CONTRACT_7B --release-resolution-report $env:RUNNER_TEMP/contract-failure-resolution.json --off-report $env:RUNNER_TEMP/contract-emergency-off.json --formal-observation-report $env:RUNNER_TEMP/contract-failure-formal-observation.json --worker-reconcile-report $env:RUNNER_TEMP/contract-worker-reconcile.json --disposition $recoveryPlan.disposition --private-evidence-store-id-env PRIVATE_EVIDENCE_STORE_ID --private-evidence-token-env PRIVATE_EVIDENCE_WRITE_TOKEN --database-url-env PRODUCTION_MIGRATION_DATABASE_URL --approval-id-env CONTRACT_RELEASE_APPROVAL_ID --output $env:RUNNER_TEMP/contract-cleanup-final.json
    }
}
```

**Work Package 5 exit:** 7a linked release acceptance, same-bundle Promote, target flag snapshot, 24-hour observation, scheduled cleanup, full reconciliation, and private-compatible rollback all PASS; then and only then independent 7b contract cleanup passes. Only the post-7b result may be labeled `Production accepted`. That label is forbidden while any mandatory gate is FAIL/NOT_RUN, Worker host is absent, Creem refund creation is unverified, Evolink lost-response safety is unverified, real payment/generation is missing, or authorized human quality review is incomplete.

---

## Plan-Wide Verification Checklist

- [ ] Every task was executed in order and every package exit gate is recorded before downstream work.
- [ ] `Push-Location backend; python -m unittest discover -s tests -v; Pop-Location` collects at least one test and has zero failures/errors/skips for mandatory suites.
- [ ] A temporary real PostgreSQL database migrates from empty through the expected current revision.
- [ ] Real PostgreSQL/Redis/Private Blob/Creem test/Provider sandbox/QA/browser tests are reported separately from unit tests.
- [ ] `npm --prefix frontend run typecheck`, unit, accessibility, E2E, OpenAPI drift, and `build:web` pass.
- [ ] Worker OCI digest, deployed ID, heartbeat, schema, payload, Provider policy, and flag hashes match one manifest.
- [ ] No mock, Admin probe, old artifact, static fallback, or unrelated record is used as Production evidence.
- [ ] Every release/migration PowerShell entry uses the pinned native-command fail-fast preamble/wrapper; injected native failures prove the next destructive command is not invoked, and `rg -n '\"\$env:[A-Za-z_][A-Za-z0-9_]*:' .github scripts docs/superpowers/plans/2026-07-10-vowpic-commercial-closure-implementation.md` returns no ambiguous child-ID interpolation.
- [ ] Every fresh Production job resolves source/runtime/manifest/API/Worker/evidence coordinates from ReleaseActivation/observation rows plus create-once Private evidence, and tests reject unset, inherited, caller-authored, stale, or mismatched coordinates.
- [ ] `git diff --check`, targeted legacy scans, and manual diff review find no unrelated edit, debug output, weakened validation, weakened test, or secret.
- [ ] `docs/ai-worklog.md` records objective, scope, decision, evidence, commands, exact results, risks, external blockers, and next gate for every formal change.
- [ ] Status is named honestly: `Code complete`, `Staging accepted`, `7a release accepted`, or `Production accepted`; only the last, after 7b, means the approved plan is fully implemented.
