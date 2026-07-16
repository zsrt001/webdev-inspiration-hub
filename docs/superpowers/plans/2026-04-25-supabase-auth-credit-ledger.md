# Supabase Auth And Credit Ledger Implementation Plan

> **Historical — not execution authority.**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the commercial MVP identity foundation by connecting Supabase Google OAuth to the existing backend user model and introducing an auditable credit ledger.

**Architecture:** Supabase Auth owns external identity, while FastAPI maps verified Supabase users into the existing `users` table. Business state remains backend-owned: balances live in `user_credits`, immutable movements live in `credit_transactions`, and orders/payments continue to reference local user IDs.

**Tech Stack:** FastAPI, SQLAlchemy async, python-jose, httpx, Uni-app/Vue, `@supabase/supabase-js`.

---

### Task 1: Backend Auth Contract

**Files:**
- Create: `backend/tests/test_supabase_auth.py`
- Modify: `backend/app/core/user_auth.py`
- Modify: `backend/app/models/user.py`
- Modify: `backend/app/schemas/user.py`
- Modify: `backend/app/routers/users.py`

- [ ] Write tests for mapping verified Supabase claims to a local user.
- [ ] Run `python -m unittest backend.tests.test_supabase_auth -v` and confirm the test fails before implementation.
- [ ] Add Supabase Auth settings and token verification helpers.
- [ ] Extend `users` with auth provider fields and expose `/users/me`.
- [ ] Re-run backend tests.

### Task 2: Credit Ledger

**Files:**
- Create: `backend/app/models/credit_transaction.py`
- Create: `backend/tests/test_credit_ledger.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/services/credit_service.py`
- Modify: `backend/app/routers/credits.py`
- Modify: `backend/app/services/payment_service.py`

- [ ] Write tests for welcome credit, debit, refund, and admin grant transactions.
- [ ] Run `python -m unittest backend.tests.test_credit_ledger -v` and confirm failure.
- [ ] Add `credit_transactions` model and ledger writing service logic.
- [ ] Route existing balance, deduct, add, and payment completion through the ledger.
- [ ] Add `/credits/transactions` for the current user.
- [ ] Re-run backend tests.

### Task 3: Frontend OAuth

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Create: `frontend/src/utils/supabase.ts`
- Modify: `frontend/src/utils/auth.ts`
- Modify: `frontend/src/components/NavBar.vue`
- Modify: `frontend/.env.local`
- Modify: `frontend/.env.example`

- [ ] Install `@supabase/supabase-js`.
- [ ] Add a Supabase client that is only active when URL and anon key are configured.
- [ ] Update H5 login to use Google OAuth and complete the callback session.
- [ ] Keep WeChat Mini Program and guest fallback paths.
- [ ] Add login/logout UI entry in the nav without blocking existing guest browsing.
- [ ] Run `npm run build:h5`.

### Task 4: Configuration And Verification

**Files:**
- Modify: `backend/.env.example`
- Modify: `.env.example`
- Modify: `docs/SUPABASE_SETUP.md`

- [ ] Document required Supabase Auth environment variables.
- [ ] Run `python -m unittest discover -s backend/tests`.
- [ ] Run `python -m compileall -q app scripts/check_supabase.py` from `backend`.
- [ ] Run `npm run build:h5` from `frontend`.
