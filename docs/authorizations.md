# Project authorizations

This file records durable user authorizations for VowPic. It contains no
credentials, tokens, cookies, database URLs, or secret values.

## Active authorizations

- The user authorized use of the existing logged-in GitHub and Chrome sessions
  to inspect, create, update, merge, and verify VowPic pull requests and CI.
- The user authorized the ordered VowPic Production recovery and deployment
  sequence after required tests and release gates pass.
- The user authorized creation of two dedicated least-privilege Supabase
  observation database logins, controlled inventory/recovery exercises, and
  publication of the resulting protected GitHub/Vercel secrets. An old
  administrator `DATABASE_URL` must never be substituted for either dedicated
  login.
- The user authorized creation and publication of the reviewed Vercel deny
  rules and subsequent read-only verification.
- The user repeatedly instructed Codex to continue through all in-scope risks
  and leftovers without pausing for repeated approvals.

## Explicit constraints

- Do not modify, restart, or terminate Proxifier.
- Do not expose secret plaintext in commands, logs, artifacts, documentation,
  or chat.
- Do not claim Production or COMMERCIAL_7A completion without exact deployment,
  domain, protected-readiness, provider, Worker, linked-acceptance, and durable
  observation evidence.
