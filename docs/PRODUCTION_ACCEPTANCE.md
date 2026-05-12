# Production acceptance checklist

Use this checklist before marking a Vercel production release ready.

## Automated smoke

Run from the repository root:

```powershell
node .\scripts\prod_smoke_check.mjs --base-url "https://www.vowpic.com"
```

If Vercel is cold or the generation queue provider is slow, keep the queue probe enabled and raise the timeout:

```powershell
node .\scripts\prod_smoke_check.mjs --base-url "https://www.vowpic.com" --readiness-timeout-sec 240
```

The smoke check must pass:

- `/health` returns healthy.
- `/api/v1/ops/readiness` returns `commercial_ready: true`.
- Storage and generation queue probes are healthy.
- Public home banner no longer points at legacy heavy images.
- Templates endpoint returns a non-empty collection.

## Admin console

Open `/admin` after signing in with an authorized Google account.

- Confirm the Admin access card shows the current actor, roles, `/admin` entry, remote join state, session store, and generation mode.
- Use Users & credits to inspect user status and grant credits only when needed.
- Use Orders to inspect order status and generation details.
- Use Email delivery to send a real test email and confirm the latest email log is successful.
- Use Real generation probe with public Vercel Blob image URLs. For remote couple probes, provide two portrait URLs and keep "Remote join couple probe" enabled.
- Confirm the probe shows both source images and the generated wedding image.

## Release gates

Do not call the release done until all items are true:

- Google login works on production.
- Credits are deducted or granted according to the current credit rules.
- Email test succeeds with the verified sender.
- Solo generation, local couple generation, and remote couple generation each produce an acceptable wedding image.
- Admin console is reachable at `/admin` only for authorized admins.
- No production environment variable points at `127.0.0.1`, `localhost`, or local MinIO.
