# Vercel Deployment Guide

This project is deployed as a PC/mobile web SaaS on Vercel:

- Frontend: uni-app web build from `frontend/dist/build/h5`.
- Backend: FastAPI through `api/index.py`.
- API routing: `/api/:path*`, `/health`, and backend static style assets are rewritten to the Python function.

## Build Settings

Use the repository root as the Vercel project root.

```text
Build Command: cd frontend && npm ci && npm run build:web
Output Directory: frontend/dist/build/h5
Install Command: default
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
ADMIN_TOKEN=
PHONE_CRYPTO_KEY=

SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_JWT_SECRET=
SUPABASE_JWT_AUDIENCE=authenticated

VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
VITE_API_BASE_URL=

FRONTEND_BASE_URL=https://your-domain.com
WEBHOOK_BASE_URL=https://your-domain.com
CORS_ALLOW_ORIGINS=https://your-domain.com

STORAGE_PROVIDER=vercel
BLOB_READ_WRITE_TOKEN=
VERCEL_BLOB_TOKEN=

GENERATION_ENGINE=wenwen
WENWEN_API_KEY=
WENWEN_CHAT_API_KEY=
WENWEN_VISION_API_KEY=

PAYMENT_PROVIDER=creem
CREEM_API_KEY=
CREEM_WEBHOOK_SECRET=
CREEM_PRODUCT_PACK_50=
CREEM_PRODUCT_PACK_120=
CREEM_PRODUCT_PACK_300=
```

Leave `VITE_API_BASE_URL` empty on Vercel. The frontend will use same-origin `/api/v1`.

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
```
