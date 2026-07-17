# Production inventory and restore-evidence contract

This contract is read-only and fail-closed. It does not authorize a Production
connection, migration, deployment, or data repair. A missing protected
credential is `NOT_RUN` (exit 3), never an empty or zero-filled report.

## Inventory evidence

Run only from the reviewed tooling commit:

```powershell
python scripts/release/inventory_production.py `
  --database-url-env PRODUCTION_READ_ONLY_DATABASE_URL `
  --hmac-key-env INVENTORY_HMAC_KEY `
  --output "artifacts/security-baseline/$env:GITHUB_RUN_ID/inventory-summary.json"
```

The command creates the report and sibling `manifest.sha256` exactly once. An
existing destination is an error. The database URL and HMAC key are read only
from the named environment variables and are never written to the report.

`inventory-summary.json` has these top-level fields:

| Field | Meaning |
| --- | --- |
| `generated_at` | UTC generation timestamp. |
| `schema_revision` | Exact `alembic_version.version_num` observed in the read-only transaction. |
| `users` | Account, conflict, and legacy-account ownership/entitlement counts. |
| `ledger` | Transaction, balance-mismatch, unlinked-debit, and orphan counts. |
| `orders` | Total, active, legacy-unverified, deleted, and expired-source-reference counts. |
| `objects` | Asset visibility, sharing, legacy ownership, unique-asset, and non-asset URL counts. |
| `conflict_group_hmacs` | Sorted keyed identifiers for duplicate email/subject groups; no raw identifier is present. |
| `url_inventory_hmac_sha256` | SHA-256 over sorted unique HMACs of normalized URL/object references. |
| `read_only_proof` | Runtime proof that both the transaction and source role are read-only. |

The `users` section includes `guest`, `password`, `other_retired_provider`, `visitor`,
`missing_subject`, duplicate group counts, orphan owners, and these migration
size gates:

- `legacy_accounts`
- `legacy_accounts_with_orders` and `legacy_account_orders`
- `legacy_accounts_with_credit_balance` and `legacy_credit_balance`
- `legacy_active_subscriptions` and `legacy_accounts_with_active_subscriptions`
- `legacy_subscription_credit_grants`

Legacy counts measure records that must be reconciled or retired for the
overseas web product. Provider-specific retired branches are not restored.

The URL inventory covers `users.avatar_url`, all strings nested in the three
order JSONB image fields, both Live Portrait URL fields, and
`credit_purchases.checkout_url`. Checkout URLs are counted as non-asset URL
references. Query strings and fragments are removed before keyed hashing so a
signed URL or token cannot survive in evidence. The HMAC key must contain at
least 32 bytes, remain in protected secret storage, and remain stable only for
runs that need comparable conflict identifiers.

Raw email addresses, provider subjects/OpenIDs, avatar/image/video/checkout
URLs, object keys, tokens, database URLs, and passwords are forbidden in the
report. Pydantic rejects unknown top-level fields, negative counts, malformed
hashes, and a report whose read-only proof is not valid.

The query is catalog-driven so the pre-`0013` legacy shape is valid input.
Columns introduced or reconciled by `0013` are queried only when they actually
exist. A missing required baseline key (`users.id` or `users.openid`) fails the
inventory instead of returning a partial zero report.

## Mandatory read-only proof

Before aggregate queries, the tool executes `SET TRANSACTION READ ONLY` and
requires all of the following:

- `transaction_read_only=true`
- `default_transaction_read_only=true`
- current role is not superuser and has no create-database, create-role, or
  replication privilege
- current role has `BYPASSRLS`, so RLS cannot silently hide rows from the
  aggregate
- zero `INSERT`, `UPDATE`, or `DELETE` table privileges in `public`
- a rolled-back no-op DML write probe fails with PostgreSQL SQLSTATE `25006`

`BYPASSRLS` is required only on the dedicated inventory role and does not
replace the read-only default or zero-write checks. Any failed proof aborts
before a report is written.

## Backup/restore rehearsal evidence

`backend/scripts/backup_restore_rehearsal.py` accepts three distinct
connections: the read-only source, a disposable restore role/database, and a
separate target-Admin connection used only for cleanup. It rejects:

- a target with the same database identity as the source
- a target database outside the `vowpic_restore_` prefix
- a target role outside the `vowpic_restore_` prefix or different from the
  target URL user
- a target and Admin connection on different servers
- nonlocal unencrypted PostgreSQL connections
- a public-addressed/non-internal target
- a nonlocal target credential without an explicit expiry of at most two hours
- a raw-dump scratch directory equal to or nested below the sanitized artifact
  directory

The archive command is `pg_dump --format=custom --no-owner --no-acl`. Restore
uses `pg_restore --clean --if-exists --no-owner --no-acl --exit-on-error`.
Passwords are supplied only through `PGPASSWORD`; URLs and passwords are not
placed in command arguments or error evidence.

The restored database must match the source on Alembic revision, table names,
exact row counts, foreign-key orphan count, ledger mismatch count, and the URL
inventory checksum. The restore checksum covers the same known URL-bearing
columns that exist in the observed legacy schema and preserves duplicate
references for the comparison; it never assumes that `0013`-reconciled columns
already exist.

`--scratch-dir` is mandatory and points to runner temp, outside
`--artifact-dir`. `restore.dump` is created only in that scratch directory, so
an interrupted or failed deletion cannot place raw Production bytes in the
uploaded evidence tree. One mandatory `finally` path terminates target sessions,
drops the disposable database and role, proves both are absent, and deletes the
dump after success, restore failure, or comparison failure. Cleanup failure
overrides any prior success and fails the gate. Only the create-once sanitized
`restore-summary.json` may survive under the artifact directory; a dump or
restored Production-data database is never a normal artifact.

Generated evidence and disposable archives are ignored by Git. Production
inventory remains `NOT_RUN` until the protected read-only source credential is
supplied. The protected workflow creates its own password-authenticated,
loopback-only PostgreSQL 17 restore target and destroys it after the rehearsal.
When the observed Production revision is legacy `20260427_0006`, the protected
workflow runs this same inventory/restore pair and persists its sanitized
artifact before any bridge write. Only then may the distinct migration login
upgrade to `20260516_0012`; the normal safe-baseline preflight, inventory, and
restore are rerun at `0012`. The legacy administrator URL is neither an
inventory source nor an application runtime secret.
