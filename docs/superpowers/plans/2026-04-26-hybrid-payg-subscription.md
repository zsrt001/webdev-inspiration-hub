# Hybrid PAYG And Subscription Implementation Plan

> **Historical — not execution authority.**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a commercial hybrid billing model where users can buy credit packs on demand and subscribe to monthly plans that grant credits and product entitlements.

**Architecture:** Credits remain the single spendable currency. One append-only credit ledger records both one-off purchases and subscription grants, while payment and subscription tables only describe payment-provider state. Backend APIs are the business authority; Supabase RLS protects direct client access.

**Tech Stack:** FastAPI, SQLAlchemy Async, Alembic, Supabase Postgres/Auth/RLS, Vue 3/uni-app H5, Pinia, Creem-compatible hosted checkout/webhooks.

---

## Product Rules

- One-time credit packs and subscriptions both add credits to the same `user_credits` balance.
- Generation, remote join, Live Portrait, and future premium tools only deduct credits from the ledger.
- Subscriptions are not unlimited usage. Each active billing period grants a fixed number of credits.
- Subscription credits and purchased credits share one visible balance for MVP. If expiry is needed later, add buckets then.
- Do not store credit-card numbers, card tokens, CVV, billing addresses, or bank data. Store only provider customer/subscription/payment IDs and non-sensitive status.
- Refunds create compensating ledger entries. Do not mutate historical credit transactions.
- Backend/service role handles all writes. Browser Supabase clients can only read their own rows where RLS allows it.

## MVP Plans

```text
free:
  price: 0
  monthly_credits: 0
  use: account baseline only

starter_monthly:
  price: configurable
  monthly_credits: 80
  use: light users

creator_monthly:
  price: configurable
  monthly_credits: 300
  use: frequent users

studio_monthly:
  price: configurable
  monthly_credits: 900
  use: studio / high-frequency users
```

## Data Model

```text
subscription_plans
  id uuid pk
  code text unique
  name text
  billing_interval text: month/year/none
  price_cents int
  currency text
  monthly_credits int
  feature_flags jsonb
  is_active bool

user_subscriptions
  id uuid pk
  user_id uuid fk users.id
  plan_id uuid fk subscription_plans.id
  provider text
  provider_customer_id text nullable
  provider_subscription_id text unique nullable
  status text: trialing/active/past_due/canceled/expired
  current_period_start timestamptz nullable
  current_period_end timestamptz nullable
  cancel_at_period_end bool
  metadata_json jsonb

subscription_credit_grants
  id uuid pk
  subscription_id uuid fk user_subscriptions.id
  user_id uuid fk users.id
  period_key text
  credits int
  credit_transaction_id uuid fk credit_transactions.id
  unique(subscription_id, period_key)

payment_events
  id uuid pk
  provider text
  event_id text
  event_type text
  object_id text nullable
  payload_json jsonb
  processed_at timestamptz nullable
  error text nullable
  unique(provider, event_id)
```

## File Map

- Create `backend/app/models/subscription_plan.py`: plan catalog model.
- Create `backend/app/models/user_subscription.py`: subscription state and period model.
- Create `backend/app/models/subscription_credit_grant.py`: period grant idempotency model.
- Create `backend/app/models/payment_event.py`: webhook idempotency/event audit model.
- Modify `backend/app/models/__init__.py`: register new models for Alembic metadata.
- Create `backend/app/schemas/subscription.py`: public API request/response schemas.
- Create `backend/app/services/subscription_service.py`: plan listing, status mapping, period grant, cancellation.
- Modify `backend/app/services/payment_service.py`: route subscription webhook events to `SubscriptionService`.
- Create `backend/app/routers/subscriptions.py`: subscription plans, current subscription, checkout, cancel endpoints.
- Modify `backend/app/routers/payments.py`: persist provider events and dispatch subscription events.
- Modify `backend/app/routers/__init__.py`: include subscription router.
- Create `backend/alembic/versions/20260426_0004_subscription_billing.py`: tables, indexes, seed plans, RLS policies.
- Create `backend/tests/test_subscription_billing.py`: status model, idempotent grants, own-user access behavior.
- Modify `frontend/src/components/PaymentModal.vue`: add tabs for credit packs vs subscription plans.
- Modify `frontend/src/pages/account/index.vue`: show plan, period, cancel state, monthly grant history.
- Modify `frontend/src/stores/user.ts` or create `frontend/src/stores/subscription.ts`: cache current subscription and plans.
- Modify `docs/superpowers/plans/2026-04-25-commercial-mvp-production-saas.md`: mark hybrid billing task progress.

---

### Task 1: Subscription Schema And Migration

**Files:**
- Create: `backend/app/models/subscription_plan.py`
- Create: `backend/app/models/user_subscription.py`
- Create: `backend/app/models/subscription_credit_grant.py`
- Create: `backend/app/models/payment_event.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/20260426_0004_subscription_billing.py`
- Test: `backend/tests/test_subscription_billing.py`

- [x] **Step 1: Write failing model contract tests**

Test enum/status strings and idempotency constraints:

```python
def test_subscription_status_contract():
    assert SubscriptionStatus.ACTIVE.value == "active"
    assert SubscriptionStatus.PAST_DUE.value == "past_due"
    assert SubscriptionStatus.CANCELED.value == "canceled"

def test_payment_event_unique_identity():
    event = PaymentEvent(provider="creem", event_id="evt_1", event_type="subscription.paid")
    assert event.provider == "creem"
    assert event.event_id == "evt_1"
```

- [x] **Step 2: Run test and verify RED**

```powershell
python -m unittest backend.tests.test_subscription_billing -v
```

Expected: fails because models do not exist.

- [x] **Step 3: Add SQLAlchemy models**

Use lowercase status values and explicit unique constraints:

```python
class SubscriptionStatus(str, Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"
```

- [x] **Step 4: Add Alembic migration**

Migration creates tables, indexes, and seed rows for `starter_monthly`, `creator_monthly`, and `studio_monthly`. It also enables RLS using the same `public.app_current_user_id()` policy pattern already added.

- [x] **Step 5: Verify**

```powershell
python -m unittest backend.tests.test_subscription_billing -v
python -m unittest backend.tests.test_supabase_rls_migration -v
cd backend
python scripts/migrate_db.py
```

Expected: tests pass and Supabase upgrades to head.

---

### Task 2: Idempotent Subscription Credit Grants

**Files:**
- Create: `backend/app/services/subscription_service.py`
- Modify: `backend/app/services/credit_service.py` if a helper is needed for source metadata.
- Test: `backend/tests/test_subscription_billing.py`

- [x] **Step 1: Write failing grant idempotency test**

```python
async def test_subscription_period_grant_is_idempotent():
    service = SubscriptionService()
    first = await service.grant_period_credits(db, subscription, period_key="2026-04")
    second = await service.grant_period_credits(db, subscription, period_key="2026-04")
    assert first.credit_transaction_id == second.credit_transaction_id
    assert ledger_purchase_count(db, subscription.user_id, "subscription_grant") == 1
```

- [x] **Step 2: Run and verify RED**

```powershell
python -m unittest backend.tests.test_subscription_billing -v
```

Expected: fails because `SubscriptionService` does not exist.

- [x] **Step 3: Implement `grant_period_credits`**

Behavior:

```text
if grant exists for subscription_id + period_key:
  return existing grant
else:
  add credit transaction type SUBSCRIPTION_GRANT
  insert subscription_credit_grants row
  flush and return
```

- [x] **Step 4: Add `SUBSCRIPTION_GRANT` transaction type**

Extend `CreditTransactionType` with:

```python
SUBSCRIPTION_GRANT = "SUBSCRIPTION_GRANT"
```

- [x] **Step 5: Verify**

```powershell
python -m unittest backend.tests.test_subscription_billing backend.tests.test_credit_ledger -v
```

Expected: subscription grant and existing ledger tests pass.

---

### Task 3: Webhook Event Audit And Dispatch

**Files:**
- Modify: `backend/app/services/payment_service.py`
- Modify: `backend/app/routers/payments.py`
- Modify: `backend/app/services/subscription_service.py`
- Test: `backend/tests/test_subscription_billing.py`
- Test: `backend/tests/test_payment_webhook.py`

- [ ] **Step 1: Write failing event idempotency test**

```python
async def test_repeated_subscription_webhook_is_processed_once():
    event = {"id": "evt_sub_1", "type": "subscription.paid", "data": {"subscription_id": "sub_1"}}
    await payment_service.handle_webhook_event(db, event)
    await payment_service.handle_webhook_event(db, event)
    assert processed_event_count(db, "creem", "evt_sub_1") == 1
    assert grant_count(db, "sub_1") == 1
```

- [ ] **Step 2: Run and verify RED**

```powershell
python -m unittest backend.tests.test_subscription_billing -v
```

Expected: fails because webhook dispatch is not implemented.

- [ ] **Step 3: Persist `payment_events` before processing**

Create event row keyed by `(provider, event_id)`. If insert conflicts or existing row is processed, return without reprocessing.

- [ ] **Step 4: Map provider events**

Initial event mapping:

```text
checkout.paid -> one-time credit purchase finalize
subscription.created -> upsert subscription
subscription.paid -> upsert subscription + grant current period credits
subscription.updated -> update status/period/cancel flag
subscription.canceled -> mark canceled
subscription.past_due -> mark past_due
subscription.refunded -> create refund adjustment only after manual policy decision
```

- [ ] **Step 5: Verify**

```powershell
python -m unittest backend.tests.test_payment_webhook backend.tests.test_subscription_billing -v
```

Expected: one-time purchase webhook behavior still passes and subscription events are idempotent.

---

### Task 4: Subscription API

**Files:**
- Create: `backend/app/schemas/subscription.py`
- Create: `backend/app/routers/subscriptions.py`
- Modify: `backend/app/routers/__init__.py`
- Test: `backend/tests/test_subscription_billing.py`

- [ ] **Step 1: Add schema contracts**

Responses:

```python
class SubscriptionPlanRead(BaseModel):
    code: str
    name: str
    billing_interval: str
    price_cents: int
    currency: str
    monthly_credits: int
    feature_flags: dict[str, Any]

class CurrentSubscriptionRead(BaseModel):
    status: str
    plan_code: str | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
```

- [ ] **Step 2: Add endpoints**

```text
GET  /api/v1/subscriptions/plans
GET  /api/v1/subscriptions/me
POST /api/v1/subscriptions/checkout
POST /api/v1/subscriptions/cancel
```

- [ ] **Step 3: Permission model**

All endpoints use `get_request_user`. Users can only see and manage their own subscription.

- [ ] **Step 4: Verify**

```powershell
python -m unittest backend.tests.test_subscription_billing -v
```

Expected: API contract tests pass.

---

### Task 5: Frontend Account And Checkout UX

**Files:**
- Modify: `frontend/src/components/PaymentModal.vue`
- Modify: `frontend/src/pages/account/index.vue`
- Create: `frontend/src/stores/subscription.ts`
- Modify: `frontend/src/utils/api.ts` only if a typed helper is needed.

- [ ] **Step 1: Add subscription store**

Store methods:

```text
fetchPlans()
fetchCurrentSubscription()
startSubscriptionCheckout(planCode)
cancelSubscription()
```

- [ ] **Step 2: Add payment modal tabs**

Tabs:

```text
积分包
订阅套餐
```

One-time packs keep existing checkout behavior. Subscription plans call `/subscriptions/checkout`.

- [ ] **Step 3: Add account subscription card**

Show:

```text
当前套餐
订阅状态
本期到期时间
本期发放积分
是否到期取消
管理订阅按钮
```

- [ ] **Step 4: Verify web build**

```powershell
cd frontend
npm.cmd run build:web
```

Expected: build completes.

---

### Task 6: Admin And Ops Visibility

**Files:**
- Modify: `backend/app/services/admin_service.py`
- Modify: `backend/app/routers/admin.py`
- Modify: `frontend/src/pages/admin/index.vue` if admin UI exists.

- [ ] **Step 1: Add admin subscription stats**

Dashboard metrics:

```text
active_subscriptions
past_due_subscriptions
canceled_this_month
subscription_mrr_cents
credits_granted_this_month
```

- [ ] **Step 2: Add payment event error visibility**

Expose recent failed `payment_events` for operational review.

- [ ] **Step 3: Verify**

```powershell
python -m unittest backend.tests.test_subscription_billing -v
```

Expected: admin stats unit tests pass.

---

### Task 7: Launch Gates

**Files:**
- Modify: `backend/app/core/runtime_checks.py`
- Modify: `docs/VERCEL_DEPLOYMENT.md`
- Modify: `.env.example`
- Modify: `backend/.env.example`

- [ ] **Step 1: Add required env flags**

```env
CREEM_SUBSCRIPTION_STARTER_PRODUCT_ID=
CREEM_SUBSCRIPTION_CREATOR_PRODUCT_ID=
CREEM_SUBSCRIPTION_STUDIO_PRODUCT_ID=
SUBSCRIPTION_BILLING_ENABLED=false
```

- [ ] **Step 2: Add readiness checks**

When `SUBSCRIPTION_BILLING_ENABLED=true`, require provider subscription product IDs and webhook secret.

- [ ] **Step 3: Verify**

```powershell
python -m unittest discover -s backend/tests -v
cd frontend
npm.cmd run build:web
```

Expected: all tests and build pass.

---

## Execution Order

1. Task 1: schema and migration.
2. Task 2: subscription grants.
3. Task 3: webhook event dispatch.
4. Task 4: backend API.
5. Task 5: frontend UX.
6. Task 6: admin/ops.
7. Task 7: launch gates.

## Acceptance Checklist

- [ ] One-time credit purchase still works.
- [ ] Repeated one-time payment webhook does not double-add credits.
- [ ] Repeated subscription paid webhook does not double-grant credits.
- [ ] Active subscription shows correctly in account center.
- [ ] Subscription users can still buy extra credit packs.
- [ ] Canceled subscription remains active until `current_period_end` when provider says so.
- [ ] RLS protects direct Supabase client access to user, credit, transaction, purchase, subscription, referral, and invite data.
- [ ] No credit-card information is stored in application tables.
- [ ] `python -m unittest discover -s backend/tests -v` passes.
- [ ] `npm.cmd run build:web` passes.
- [ ] `python scripts/migrate_db.py` succeeds against Supabase.
