# Vercel Deployment Guide

This project is deployed as a PC/mobile web SaaS on Vercel:

- Frontend: uni-app web build from `frontend/dist/build/h5`.
- Backend: FastAPI through `api/index.py`.
- API routing: `/api/:path*`, `/health`, and backend static style assets are rewritten to the Python function.

## Build Settings

Use the repository root as the Vercel project root.

```text
Install Command: cd frontend && npm ci --ignore-scripts
Build Command: cd frontend && npm run build:web
Output Directory: frontend/dist/build/h5
```

The same settings are encoded in `vercel.json`.

## Required Environment Variables

Set these in Vercel Project Settings -> Environment Variables.

```env
DEBUG=false
AUTO_CREATE_TABLES=false
TASK_EXECUTION_MODE=inline

DATABASE_URL=
SECRET_KEY=
ADMIN_USER_IDS=
ADMIN_OPENIDS=
ADMIN_EMAILS=
PHONE_CRYPTO_KEY=

SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_JWT_SECRET=
SUPABASE_JWT_AUDIENCE=authenticated

VITE_API_BASE_URL=

FRONTEND_BASE_URL=https://your-domain.com
WEBHOOK_BASE_URL=https://your-domain.com
CORS_ALLOW_ORIGINS=https://your-domain.com

STORAGE_PROVIDER=vercel
BLOB_READ_WRITE_TOKEN=

GENERATION_ENGINE=wenwen
WENWEN_API_KEY=
WENWEN_CHAT_API_KEY=
WENWEN_VISION_API_KEY=

PAYMENT_PROVIDER=creem
SUPPORT_CONTACT_EMAIL=
SUPPORT_CONTACT_URL=
REFUND_POLICY_URL=/pages/legal/refund
CREEM_API_KEY=
CREEM_WEBHOOK_SECRET=
CREEM_PRODUCT_PACK_50=
CREEM_PRODUCT_PACK_120=
CREEM_PRODUCT_PACK_300=

SUBSCRIPTION_BILLING_ENABLED=false
CREEM_SUBSCRIPTION_STARTER_PRODUCT_ID=
CREEM_SUBSCRIPTION_CREATOR_PRODUCT_ID=
CREEM_SUBSCRIPTION_STUDIO_PRODUCT_ID=

CLEANUP_CRON_TOKEN=
CRON_SECRET=

RATE_LIMIT_ENABLED=true
RATE_LIMIT_DEFAULT_REQUESTS=240
RATE_LIMIT_DEFAULT_WINDOW_SECONDS=60
RATE_LIMIT_SENSITIVE_REQUESTS=40
RATE_LIMIT_SENSITIVE_WINDOW_SECONDS=60
NEW_ACCOUNT_IP_LIMIT_PER_HOUR=8
NEW_ACCOUNT_DEVICE_LIMIT_PER_HOUR=3

TRIAL_WELCOME_CREDITS=6
TRIAL_DAILY_GENERATION_LIMIT=3
TRIAL_PREVIEW_MAX_WIDTH=900
TRIAL_PREVIEW_MAX_HEIGHT=1125
TRIAL_WATERMARK_TEXT=AI WEDDING STUDIO PREVIEW

POSTPROCESS_ENABLED=true
POSTPROCESS_UPSCALE_FACTOR=2
POSTPROCESS_MAX_LONG_EDGE=2400
POSTPROCESS_JPEG_QUALITY=92
POSTPROCESS_VARIANTS=2x3,3x4,9x16
```

Leave `VITE_API_BASE_URL` empty on Vercel. The frontend will use same-origin `/api/v1`.

## Billing And Pricing Rules

Credits are the only spendable unit. One-time packs and subscription grants share one balance.

```text
Single base generation: 2 credits
Director / advanced single generation: 3 credits
Local couple generation: 3 credits
Remote couple generation: 4 credits
Premium/vintage scene generation: 5 credits
Live Portrait 5s: 6 credits
Live Portrait extra 5s block: +4 credits
```

New users receive trial credits for preview generation. Trial orders can view watermarked previews; HD downloads and poster export are unlocked only after paid credit history or an active subscription.

Subscriptions are not unlimited usage. Each billing period grants the plan's fixed credit amount through provider webhook reconciliation.

## Refunds, Support, And Legal Entry Points

Production deployments must expose a support contact before real-money checkout is enabled. Configure `SUPPORT_CONTACT_EMAIL` or `SUPPORT_CONTACT_URL` and keep the footer links visible on the homepage, creation flow, account center, order archive, and legal pages.

Refund handling is review-based:

```text
Eligible for review: duplicate charges, paid credits not delivered, webhook failures, platform-side queue failures, confirmed storage/generation delivery failures.
Usually not refundable after successful delivery: subjective style preference, user prompt choices, poor upload quality, or third-party payment delay.
Never request or store: card numbers, CVV, bank credentials, payment passwords, or full ID documents.
```

The public refund/support page is `/pages/legal/refund`; the backend also exposes the current legal policy summary at `/api/v1/legal/policies`.

## Rate Limiting And Admin Audit

`RATE_LIMIT_ENABLED=true` is required for commercial readiness. The built-in limiter is per-instance and should be paired with Vercel Firewall/WAF for stronger production protection.

Sensitive API prefixes use the stricter limit: auth, orders, payments, subscriptions, session, upload, and live portrait.

Admin write operations are recorded in `admin_audit_logs` and can be reviewed from `/api/v1/admin/audit_logs` or the Admin page. Prefer `ADMIN_USER_IDS`, `ADMIN_OPENIDS`, or `ADMIN_EMAILS` so the frontend only sends the normal signed-in user JWT. `ADMIN_TOKEN` is a backend-only fallback for scripts/internal calls and must not be stored in browser localStorage or exposed through `VITE_*` variables.

## Image Retention And Cleanup

The app stores only URLs and operational metadata in Postgres. Image files are deleted by policy:

```text
Uploaded source images: 7 days
Free generated images: 30 days
Paid credit-pack generated images: 90 days
Subscription generated images: 180 days
Studio subscription generated images: 365 days
```

`vercel.json` includes a daily cron for `/api/v1/ops/cleanup_expired_assets`. Set `CRON_SECRET` in Vercel and use the same value for `CLEANUP_CRON_TOKEN` when running local/manual cleanup jobs. The cleanup route accepts `Authorization: Bearer $CRON_SECRET` or `Authorization: Bearer $CLEANUP_CRON_TOKEN`.

Users can also delete image assets from Account Center. Deletion removes source, preview, and final image URLs from the order and deletes the underlying storage objects where the configured provider supports deletion.

## Database Migration

Run migrations before promoting a production deployment:

```powershell
cd backend
python scripts/migrate_db.py
```

Production startup should not mutate schema. Keep:

```env
AUTO_CREATE_TABLES=false
```

## Supabase OAuth URLs

The current frontend build does not embed Supabase anon keys. If Google OAuth is reintroduced later, keep the browser flow behind backend-owned endpoints instead of adding `VITE_SUPABASE_ANON_KEY`.

In Supabase Authentication settings:

```text
Site URL: https://your-domain.com
Redirect URLs:
https://your-domain.com
https://your-domain.com/
http://127.0.0.1:3000
http://127.0.0.1:3000/
```

In Google Cloud OAuth client:

```text
Authorized JavaScript origins:
https://your-domain.com
http://127.0.0.1:3000

Authorized redirect URIs:
https://<your-supabase-project-ref>.supabase.co/auth/v1/callback
```

## Verification

Before launch:

```powershell
python -m unittest discover -s backend/tests -v
cd frontend
npm run build:web
```

After deployment:

```text
GET https://your-domain.com/health
GET https://your-domain.com/health/ready
Open https://your-domain.com and verify Google login.
Create a guest session and verify balance/order pages.
Open Account Center and verify subscription, retention text, and recent generated image history.
Trigger `/api/v1/ops/readiness?strict=true` and verify subscription/payment/storage cleanup config.
```
