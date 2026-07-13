# VowPic Online Deployment Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the verified online blockers, prove the Preview commercial runtime with real evidence, and only then promote the exact accepted source SHA to the formal domains.

**Architecture:** Keep the existing fail-closed release architecture. Fix application defects at their source, reconcile the production schema through the existing controlled release workflow, then register and test an immutable Preview release before Production promotion.

**Tech Stack:** Vue 3 / uni-app / TypeScript, FastAPI / SQLAlchemy / PostgreSQL / Alembic, Python `unittest`, Vercel, GitHub Actions.

## Global Constraints

- Overseas Web/H5 only; do not restore WeChat or Mini Program behavior.
- Do not weaken fail-closed runtime checks or feature gates to make Preview green.
- Do not edit historical migration `20260516_0012`; production schema changes use the approved workflow and current migration chain.
- No production mutation until backup/restore rehearsal, dry-run, exact impact preview, and rollback evidence pass.
- No Google OAuth, upload, generation, payment, email, or admin write smoke against Production.
- Preserve unrelated user changes; no commit, push, PR, or deployment outside the user-approved ordered release flow.

---

### Task 1: Stop non-tab pages from producing TabBar promise rejections

**Files:**
- Modify: `frontend/src/stores/i18n.ts`
- Modify: `frontend/src/App.vue`
- Test: `backend/tests/test_frontend_runtime_contract.py`

**Interfaces:**
- Consumes: uni-app `setTabBarItem` Promise API.
- Produces: `applyTabBarLocale(): Promise<void>` that handles the expected non-TabBar rejection without leaving an unhandled Promise.

- [x] Add a source-contract regression test requiring an awaited/caught TabBar update and no fire-and-forget rejected Promise.
- [x] Run the new test and verify it fails on the current implementation.
- [x] Implement the smallest error-boundary change and remove the legacy App lifecycle console logs.
- [x] Run the regression test, frontend typecheck, and frontend H5 build.

### Task 2: Use timestamp-compatible cutoffs in ops alerts

**Files:**
- Modify: `backend/app/services/ops_alert_service.py`
- Test: `backend/tests/test_ops_alert_service.py`

**Interfaces:**
- Consumes: `Lead.created_at` as `TIMESTAMP WITHOUT TIME ZONE`; `Order.updated_at` and `LivePortraitJob.updated_at` as timezone-aware timestamps.
- Produces: separate naive-UTC and aware-UTC cutoff values for the matching SQL column types.

- [x] Add an async regression test that captures the three generated statements and asserts the Lead cutoff is naive while Order/Live Portrait cutoffs are UTC-aware.
- [x] Run the test and verify it fails with the current shared aware cutoff.
- [x] Introduce separate cutoff values without changing schemas or unrelated alert behavior.
- [x] Run the regression test and the existing risk/analytics suites.

### Task 3: Align the active frontend with the retired guest-auth contract

**Files:**
- Modify: `frontend/src/pages/auth/login.vue`
- Modify: `frontend/src/pages/auth/register.vue`
- Modify: `frontend/src/pages/account/index.vue`
- Modify: `frontend/src/utils/auth/session.ts`
- Modify: `frontend/src/utils/api.ts`
- Modify as proven necessary: `frontend/src/utils/auth/index.ts`, `frontend/src/utils/auth/identity.ts`, `frontend/src/utils/auth/storage.ts`, `frontend/src/utils/auth/_keys.ts`, `frontend/src/utils/supabase.ts`
- Modify: `backend/app/schemas/auth.py`
- Modify: `backend/app/schemas/user.py`
- Modify: `backend/app/routers/auth/guest.py`
- Modify: `backend/app/routers/auth/google.py`
- Modify: `backend/app/routers/auth/_helpers.py`
- Modify: `backend/app/routers/users.py`
- Remove: `backend/app/routers/auth/merge.py`
- Test: `backend/tests/test_web_only_contract.py`
- Test: `backend/tests/test_risk_lockdown.py`

**Interfaces:**
- Consumes: Google/Supabase sign-in and the backend `POST /api/v1/auth/login -> 410 guest_auth_retired` tombstone.
- Produces: no automatic guest bootstrap, guest merge, guest CTA/copy/storage key, or public guest fields, and explicit unauthenticated behavior until Google sign-in succeeds.

- [x] Add failing source and HTTP contract tests for the retired guest surface.
- [x] Remove the active guest bootstrap and UI entry points without weakening Google auth or protected-route behavior.
- [x] Remove internal OpenID/provider fields from public response models, JWT claims, frontend DTOs, and the OpenAPI projection while preserving the database compatibility mapping.
- [x] Run focused tests, typecheck, and H5 build.

### Task 4: Run the complete application regression gate

**Files:**
- Modify: `docs/ai-worklog.md`

- [x] Run all backend tests and report executed/skipped counts.
- [x] Run frontend typecheck and H5 production build.
- [x] Run workflow/script syntax checks and `git diff --check`.
- [x] Review the complete diff for unrelated changes, debug output, compatibility, security, data, and performance regressions.
- [x] Record exact commands, results, remaining risks, and the read-only Subagent use in `docs/ai-worklog.md`.

### Task 5: Reconcile the production database through the controlled workflow

**Files:**
- Existing workflow: `.github/workflows/safe-baseline-release.yml`
- Existing tools: `scripts/release/register_safe_baseline.py`, `scripts/release/verify_safe_baseline.py`, `backend/scripts/backup_restore_rehearsal.py`
- Create: `backend/alembic/versions/20260712_0014_repair_click_stats_values.py`
- Modify: `backend/app/services/schema_guard_service.py`, `release/safe-baseline-contract.json`, `scripts/release/build_runtime_bundle_id.py`

- [ ] Read back the current production revision and confirm the exact missing `click_stats` columns without exposing credentials.
- [x] Add a forward-only, idempotent repair migration without editing historical revision `20260516_0012`.
- [x] Generate offline SQL and a local impact report for `20260710_0013 -> 20260712_0014`.
- [x] Exercise missing-column and already-present-column paths on a disposable real PostgreSQL 15 database.
- [x] Add a bounded 15-second lock timeout and 5-minute statement timeout before the release advisory lock and Alembic DDL.
- [x] Add and run the opt-in real-PostgreSQL migration suite for missing, correct/nonzero, nullable, and incompatible-type rollback paths; wire it into the ephemeral CI database.
- [ ] Complete backup and restore rehearsal with verified cleanup.
- [ ] Apply the approved migration through `PRODUCTION_MIGRATION_DATABASE_URL`; never through the runtime role.
- [ ] Read back revision, columns, constraints, RLS/role facts, and verify `/api/v1/analytics/click` no longer returns 503.

### Task 6: Register and verify the exact Preview release

**Files:**
- Existing release scripts and Vercel project configuration.

- [ ] Build and verify the runtime bundle for the exact source SHA.
- [ ] Register the Preview activation and configure deployment-scoped runtime coordinates.
- [ ] Verify `/health`, strict readiness, public configuration, auth tombstones, CORS, and runtime logs.
- [ ] Run real Preview-only Google OAuth, private upload, generation, ledger, test-mode payment, webhook, download, and admin evidence flows using approved test identities and data.

### Task 7: Promote only the accepted artifact to Production

- [ ] Confirm the accepted Preview SHA, bundle ID, build artifact, migration revision, observation evidence, and rollback target are immutable and mutually consistent.
- [ ] Promote the accepted artifact; do not rebuild from a moving branch tip.
- [ ] Confirm `vowpic.com` and `www.vowpic.com` resolve to the accepted deployment and exact SHA.
- [ ] Run read-only formal-domain acceptance, monitor errors and latency, and retain the rollback window until the observation finalizer passes.
