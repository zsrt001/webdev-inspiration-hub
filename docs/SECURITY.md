# VowPic Security Contract

VowPic is an overseas Web SaaS. Security claims require code, test, and runtime evidence; an unavailable external dependency is `NOT_RUN`, never an implicit PASS.

## Identity and sessions

- Public sign-in is Google OAuth through Supabase PKCE. Guest, password, WeChat, OpenID/UnionID, and anonymous partner sessions are retired.
- The backend validates a short-lived Supabase token during the exchange and then issues a local `HttpOnly`, `Secure`, same-site session cookie.
- OAuth state, callback origin, session rotation, logout, replay denial, and deployment-bound acceptance identities are verified independently.
- Admin and business authorization use canonical UUID identities and server-side permissions, never browser-provided legacy identity fields.

## Database and tenancy

- PostgreSQL RLS and explicit service checks isolate users and private assets.
- Runtime, control-plane writer, read-only resolver, and migration credentials are separate roles with least privilege.
- Runtime roles must not own protected tables, be superusers, or have `BYPASSRLS`.
- Schema changes run only through versioned Alembic migrations; request-time DDL is forbidden.

## Media and network boundaries

- Uploads are validated for type, size, ownership, content policy, and lifecycle state before use.
- Source media and generated masters live in private storage. Delivery uses bounded, purpose-specific grants and checks ownership again at read time.
- Provider fetch grants are opaque, short-lived, read-limited, bound to the exact asset/job/attempt/deployment/runtime, and revoked during cleanup.
- Outbound URLs and redirects are checked against SSRF, alternate-host, and open-redirect abuse. Raw grant tokens, object keys, credentials, and customer media are not logged.

## Generation and payment

- EvoLink output and task state are untrusted external data and must pass schema, lineage, status, cost, and QA validation.
- A submission with a lost response stays `UNKNOWN`, is not charged as accepted, and is never resubmitted automatically. A known Provider task ID may be queried; an independently established task may be attached only through the audited recovery path.
- Creem webhooks require signature verification, event uniqueness, transaction lineage, and idempotent projection. Redirect state alone never proves payment.
- Credits use reservations, immutable allocations, capture/release/reversal facts, and duplicate-submit protection.

## Capability and secret controls

- Google auth, authenticated upload, generation, billing, private download, and Partner Invite fail closed when sanitized public configuration is missing.
- Secrets are supplied only by protected environments. Secret plaintext must not be printed, committed, copied into artifacts, or moved between environments by automation.
- Release evidence is sanitized, content-addressed, source/runtime/deployment bound, create-once, and independently resolved.

Provider safety is enforced by request signing, idempotent local intents, exact task correlation, and the no-resubmit policy for ambiguous EvoLink submissions. `release/provider-capabilities.json` records these implemented behaviors; protected Preview must still prove the real EvoLink and Creem paths before Production activation.
