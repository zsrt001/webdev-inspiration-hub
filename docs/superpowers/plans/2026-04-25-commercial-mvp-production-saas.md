# Commercial MVP Production SaaS Implementation Plan

> **Historical — not execution authority.**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the AI Wedding web SaaS as a commercial MVP with Supabase Auth, credits, payments, generation records, admin operations, and production-grade deployment foundations.

**Architecture:** Keep Supabase as identity and Postgres provider; the FastAPI backend remains the business authority for users, credits, payments, orders, and generation. The browser web app is built from the current uni-app H5 target for Vercel, then progressively hardened into a desktop/mobile responsive SaaS web product. Production changes are delivered in small slices: migrations first, then auth/account, then payment ledger, then generation reliability, then observability.

**Tech Stack:** FastAPI, SQLAlchemy Async, Alembic, Supabase Auth/Postgres, Vercel Python Functions, Vue 3/uni-app web build, Pinia, Supabase JS, Creem/manual payments, Vercel Blob or S3-compatible storage.

---

## Current Verified Baseline

- Backend liveness: `GET http://127.0.0.1:8001/health` returns healthy.
- Backend readiness: `GET http://127.0.0.1:8001/health/ready` returns ready with database OK.
- Local guest auth: `POST /api/v1/auth/login` returns JWT and local user id.
- User mapping: `GET /api/v1/users/me` works with local JWT.
- Credit ledger: `GET /api/v1/credits/balance` creates welcome balance, and `GET /api/v1/credits/transactions` returns `WELCOME_BONUS`.
- Supabase Google OAuth: Supabase `/auth/v1/authorize?provider=google` returns 302 to Google using the configured Google client id.
- Frontend dev server: `http://127.0.0.1:3000/` returns 200.
- Known issue: browser automation sees uni-h5 generic console `Object` errors on the create page; this must be reduced to actionable error details before final production acceptance.

## Deployment Decision

- Treat the current uni-app `build:h5` output as the web build artifact for Vercel.
- Keep frontend and backend in one Vercel project for MVP:
  - Static frontend from `frontend/dist/build/h5`.
  - FastAPI serverless entry from `api/index.py`.
  - `/api/:path*`, `/health`, and `/static/styles/:path*` rewritten to the Python function.
- Use inline generation mode on Vercel only for short-running API calls. If image generation regularly exceeds Vercel limits, move generation execution to a dedicated worker host while keeping web/API on Vercel.

---

### Task 1: Lock Production Configuration Boundaries

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/.env.example`
- Modify: `.env.example`
- Test: `backend/tests/test_runtime_config.py`

- [x] **Step 1: Add failing tests for production safety**

Create `backend/tests/test_runtime_config.py`:

```python
import unittest

from app.core.config import Settings


class RuntimeConfigTest(unittest.TestCase):
    def test_production_disables_schema_auto_create_by_default(self) -> None:
        settings = Settings(debug=False)
        self.assertFalse(settings.auto_create_tables)

    def test_development_allows_schema_auto_create_by_default(self) -> None:
        settings = Settings(debug=True)
        self.assertTrue(settings.auto_create_tables)

    def test_vercel_uses_inline_generation_by_default(self) -> None:
        settings = Settings(vercel="1", task_execution_mode="auto")
        self.assertEqual(settings.generation_execution_mode, "inline")
```

- [x] **Step 2: Run the new tests and confirm failure**

Run:

```powershell
python -m unittest backend/tests/test_runtime_config.py -v
```

Expected: failure because `auto_create_tables` does not exist.

- [x] **Step 3: Add `auto_create_tables` setting**

In `backend/app/core/config.py`, add:

```python
    auto_create_tables: bool | None = None

    @property
    def should_auto_create_tables(self) -> bool:
        if self.auto_create_tables is not None:
            return bool(self.auto_create_tables)
        return bool(self.debug)
```

- [x] **Step 4: Use the setting in startup**

In `backend/app/main.py`, wrap `Base.metadata.create_all` and `ALTER TABLE` compatibility SQL behind `settings.should_auto_create_tables`. In production, startup should validate DB connectivity but not mutate schema.

- [x] **Step 5: Document env flags**

Add to `.env.example` and `backend/.env.example`:

```env
DEBUG=false
AUTO_CREATE_TABLES=false
TASK_EXECUTION_MODE=inline
```

- [x] **Step 6: Verify**

Run:

```powershell
python -m unittest discover -s backend/tests -v
```

Expected: all tests pass.

---

### Task 2: Add Alembic Migration Foundation

**Files:**
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/versions/20260425_0001_initial_saas_schema.py`
- Create: `backend/scripts/migrate_db.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_alembic_config.py`

- [x] **Step 1: Test migration config exists**

Create `backend/tests/test_alembic_config.py`:

```python
from pathlib import Path
import unittest


class AlembicConfigTest(unittest.TestCase):
    def test_alembic_files_exist(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "alembic.ini").is_file())
        self.assertTrue((root / "alembic" / "env.py").is_file())
        versions = list((root / "alembic" / "versions").glob("*.py"))
        self.assertTrue(versions)
```

- [x] **Step 2: Run and confirm failure**

Run:

```powershell
python -m unittest backend/tests/test_alembic_config.py -v
```

- [x] **Step 3: Add Alembic config**

`backend/alembic.ini` should point to `alembic` and use runtime `DATABASE_URL` through `env.py`, not a hardcoded secret.

- [x] **Step 4: Add migration runner**

`backend/scripts/migrate_db.py` should run:

```python
from alembic import command
from alembic.config import Config
from pathlib import Path


def main() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    main()
```

- [x] **Step 5: Verify against Supabase**

Run:

```powershell
cd backend
python scripts/migrate_db.py
```

Expected: exits 0 and leaves schema at `head`.

---

### Task 2.5: Supabase RLS Data Boundary

**Files:**
- Create: `backend/alembic/versions/20260426_0003_supabase_rls_policies.py`
- Create: `backend/tests/test_supabase_rls_migration.py`

- [x] **Step 1: Enable RLS for sensitive tables**

Protect `users`, `user_credits`, `credit_transactions`, `credit_purchases`, `orders`, `live_portrait_jobs`, `leads`, and `click_stats`.

- [x] **Step 2: Restrict authenticated reads to own rows**

Use Supabase `auth.uid()` mapped through `users.auth_subject` via `public.app_current_user_id()`.

- [x] **Step 3: Deny direct client access to service-only tables**

`leads` and `click_stats` have RLS enabled with no authenticated direct-read policy. Backend/service role remains the authority.

- [x] **Step 4: Pre-wire future commercial tables**

If subscription, Creem customer, referral, or invite tables already exist in an environment, the migration applies own-row select policies when expected user ownership columns are present.

- [x] **Step 5: Verify**

Run:

```powershell
python -m unittest backend.tests.test_supabase_rls_migration -v
cd backend
python scripts/migrate_db.py
```

Expected: test passes and Supabase upgrades to `head`.

---

### Task 3: Web/Vercel Build Hardening

**Files:**
- Modify: `vercel.json`
- Modify: `frontend/package.json`
- Modify: `frontend/src/utils/apiConfig.ts`
- Modify: `frontend/src/utils/supabase.ts`
- Create: `docs/VERCEL_DEPLOYMENT.md`
- Test: `frontend` build command and backend import check

- [x] **Step 1: Add explicit web scripts**

In `frontend/package.json`, add:

```json
"dev:web": "uni -p h5",
"build:web": "uni build -p h5"
```

Keep `dev:h5` and `build:h5` for compatibility.

- [x] **Step 2: Update Vercel build command**

In `vercel.json`, set:

```json
"buildCommand": "cd frontend && npm ci && npm run build:web",
"outputDirectory": "frontend/dist/build/h5"
```

- [x] **Step 3: Ensure frontend API base works on same-origin production**

`frontend/src/utils/apiConfig.ts` should resolve production API base to `/api/v1` when `VITE_API_BASE_URL` is absent and `window.location.origin` is not localhost.

- [x] **Step 4: Ensure OAuth redirect is domain-aware**

`frontend/src/utils/supabase.ts` should use:

```ts
return `${window.location.origin}/`;
```

Already present; keep this as the production redirect contract.

- [x] **Step 5: Document Vercel environment variables**

Create `docs/VERCEL_DEPLOYMENT.md` with required env names:

```text
DATABASE_URL
DEBUG=false
AUTO_CREATE_TABLES=false
SECRET_KEY
ADMIN_TOKEN
SUPABASE_URL
SUPABASE_ANON_KEY
VITE_SUPABASE_URL
VITE_SUPABASE_ANON_KEY
FRONTEND_BASE_URL
WEBHOOK_BASE_URL
PAYMENT_PROVIDER
CREEM_API_KEY
CREEM_WEBHOOK_SECRET
WENWEN_API_KEY
STORAGE_PROVIDER
BLOB_READ_WRITE_TOKEN
```

- [x] **Step 6: Verify**

Run:

```powershell
cd frontend
npm run build:web
cd ..
python -c "import sys; sys.path.insert(0, 'backend'); from app.main import app; print(app.title)"
```

Expected: frontend build succeeds and backend imports.

---

### Task 4: Account And User Center MVP

**Files:**
- Modify: `backend/app/routers/users.py`
- Modify: `backend/app/schemas/user.py`
- Modify: `frontend/src/pages/orders/orders.vue`
- Create: `frontend/src/pages/account/index.vue`
- Modify: `frontend/src/pages.json`
- Test: `backend/tests/test_supabase_auth.py`

- [x] **Step 1: Backend exposes stable `/users/me`**

Ensure response includes:

```json
{
  "id": "uuid",
  "openid": "supabase:<sub>",
  "auth_provider": "supabase",
  "auth_subject": "<sub>",
  "email": "user@example.com",
  "role": "user",
  "status": "active",
  "last_login_at": "iso"
}
```

- [x] **Step 2: Add account page**

Account page should show:

```text
登录状态
邮箱
当前积分
最近积分流水
最近订单
退出登录
```

- [x] **Step 3: Add navigation entry**

Add `/pages/account/index` to `frontend/src/pages.json` and nav menu.

- [ ] **Step 4: Verify**

Run browser flow:

```text
Home -> 登录 -> Google OAuth -> return -> Account -> Balance visible
```

Manual Google credential entry is user-owned; agent only verifies post-login state.

Status note: Account page, guest account state, credit balance, and recent ledger rendering were verified locally. Google OAuth post-login verification remains user-owned because credential entry and consent screens must be completed by the user.

---

### Task 5: Recharge And Payment Closure

**Files:**
- Modify: `backend/app/models/credit_purchase.py`
- Modify: `backend/app/services/payment_service.py`
- Modify: `backend/app/routers/payments.py`
- Modify: `frontend/src/components/PaymentModal.vue`
- Create: `backend/tests/test_payment_webhook.py`
- Create: `backend/alembic/versions/20260426_0002_payment_status_webhook_idempotency.py`

- [x] **Step 1: Payment states**

Support:

```text
pending
paid
failed
expired
refunded
```

- [x] **Step 2: Webhook idempotency**

Webhook completion must be idempotent by provider event id or provider order id. Repeated webhook calls must not add credits twice.

- [x] **Step 3: Signature validation**

`CREEM_WEBHOOK_SECRET` must be required when `PAYMENT_PROVIDER=creem` and `DEBUG=false`.

- [x] **Step 4: Frontend package checkout**

Payment modal should:

```text
list packages -> create checkout -> redirect hosted checkout -> poll status after return
```

- [x] **Step 5: Verify**

Run:

```powershell
python -m unittest backend/tests/test_payment_webhook.py -v
```

Expected: idempotent webhook and failed signature tests pass.

- [x] **Step 6: Apply migration**

Normalize persisted statuses to `pending/paid/failed/expired/refunded`, add unique `webhook_event_id`, and switch revenue analytics to `paid`.

---

### Task 6: Generation Reliability And Ledger Integrity

**Files:**
- Modify: `backend/app/routers/orders.py`
- Modify: `backend/app/services/wenwen_service.py`
- Modify: `backend/app/services/credit_service.py`
- Modify: `frontend/src/pages/create/index.vue`
- Modify: `frontend/src/pages/orders/orders.vue`
- Create: `backend/tests/test_generation_credit_flow.py`

- [ ] **Step 1: Enforce one debit per accepted generation**

Order creation must write exactly one `GENERATION_DEBIT` before dispatching generation.

- [ ] **Step 2: Refund on terminal failure**

Generation failure must write exactly one `GENERATION_REFUND`.

- [ ] **Step 3: Persist provider metadata**

Order should retain:

```text
generation_provider
provider_task_id
input_image_count
credit_cost
failure_reason
completed_at
```

- [ ] **Step 4: Frontend status polling**

Orders page should display:

```text
queued
processing
completed
failed
refunded
```

- [ ] **Step 5: Verify**

Run:

```powershell
python -m unittest backend/tests/test_generation_credit_flow.py -v
```

Expected: debit/refund idempotency passes.

---

### Task 7: Admin MVP

**Files:**
- Modify: `backend/app/routers/admin.py`
- Modify: `frontend/src/pages/admin/index.vue`
- Create: `backend/tests/test_admin_access.py`

- [ ] **Step 1: Protect all admin APIs**

Every admin route must require `X-Admin-Token`.

- [ ] **Step 2: Admin dashboard**

Admin page should show:

```text
total users
paid users
orders by status
credit purchases
recent failures
manual credit adjustment
```

- [ ] **Step 3: Verify**

Run:

```powershell
python -m unittest backend/tests/test_admin_access.py -v
```

Expected: unauthenticated admin calls return 401/403.

---

### Task 8: Observability, Abuse Control, And Launch Gate

**Files:**
- Modify: `backend/app/core/runtime_checks.py`
- Modify: `backend/app/routers/ops.py`
- Modify: `backend/app/services/ops_alert_service.py`
- Create: `backend/app/core/rate_limit.py`
- Create: `backend/tests/test_launch_gate.py`

- [ ] **Step 1: Launch readiness requires commercial env**

Production readiness should fail if these are missing:

```text
DATABASE_URL
SECRET_KEY
SUPABASE_URL
SUPABASE_ANON_KEY
FRONTEND_BASE_URL
WEBHOOK_BASE_URL
```

- [ ] **Step 2: Rate limit public mutation APIs**

Apply rate limits to:

```text
POST /api/v1/auth/login
POST /api/v1/orders/create
POST /api/v1/upload
POST /api/v1/payments/checkout
POST /api/v1/leads/submit
```

- [ ] **Step 3: Structured logs**

Log every order lifecycle transition with:

```text
order_id
user_id
from_status
to_status
provider
duration_ms
error_code
```

- [ ] **Step 4: Verify**

Run:

```powershell
python -m unittest backend/tests/test_launch_gate.py -v
```

Expected: missing production env fails readiness, complete config passes.

---

## Execution Order

1. Task 1: production config boundary.
2. Task 2: migrations.
3. Task 3: Vercel web deployment hardening.
4. Task 4: account center.
5. Task 5: payment/recharge closure.
6. Task 6: generation reliability.
7. Task 7: admin MVP.
8. Task 8: observability and launch gate.

## Acceptance Checklist

- [ ] `python -m unittest discover -s backend/tests -v` passes.
- [ ] `npm run build:web` passes.
- [ ] `GET /health/ready` returns ready in production env.
- [ ] Google OAuth login returns user to the production domain.
- [ ] New user receives welcome credits once.
- [ ] Paid credit top-up is idempotent.
- [ ] Generation debits once and refunds once on terminal failure.
- [ ] User can see orders and credit ledger.
- [ ] Admin APIs are protected and usable.
- [ ] `.env`, `backend/.env`, and `frontend/.env.local` remain ignored by Git.
