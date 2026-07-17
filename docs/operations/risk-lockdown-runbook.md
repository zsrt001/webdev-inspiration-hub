# Risk-lockdown and one-time safe-baseline runbook

This runbook is for the overseas VowPic Web SaaS deployment. The protected
Tasks 1-4 safe-baseline workflow does not depend on WeChat, a WeChat Mini
Program, guest OpenID, Remote Join, Live Portrait, local recommendations, or
lead capture, and every high-risk public capability is guarded OFF. Task 5's
Web-only runtime cleanup is implemented and locally verified in the current
dirty worktree, but it is not committed, released, or Production evidence.
Historical migrations, inventory readers, anti-fraud detectors, negative
security tests, and bounded in-flight-order compatibility remain deliberately;
they are not active product entry points.

## Status language

- `PASS` means the named command or protected external check ran against the
  exact source/runtime/deployment and produced immutable evidence.
- `FAIL` means it ran and a required assertion failed.
- `NOT_RUN` means it did not run or a protected prerequisite was absent. It is
  never equivalent to PASS.

Repository tests, typecheck, build, local mocks, and a Vercel deployment URL do
not prove that Production risk containment is active. Until the formal domain
serves the verified safe baseline, the previous Production surface is still a
live risk.

## External containment before the workflow

The Production owner must perform and capture these settings before exposing
any deployment credential to the workflow:

1. Confirm the committed `vercel.json` keeps `git.deploymentEnabled.main=false`.
   This disables automatic deployment from the Production branch while other
   branches can still produce Preview builds.
2. In Vercel Project Settings > Environments > Production > Branch Tracking,
   disable Vercel Git Production auto-assignment by turning off **Auto-assign
   Custom Production Domains**. Read the setting back after saving it.
3. In Project Settings > Git, disable deploy hooks by deleting/revoking every
   hook and recording that no active hook remains. A hook URL is a deployment
   secret and must not be copied into evidence.
4. Capture timestamped project-setting evidence with project ID, actor, source
   SHA, the auto-assignment state, deploy-hook count, formal-domain target, and
   the last known deployment ID. Redact tokens and hook URLs.
5. Keep the production domain on the last known deployment only while the
   protected safe-baseline install is pending. After promotion it must remain
   on that safe baseline, or on a later bundle that preserves the same
   fail-closed contract; it must never move back to the pre-kill-switch build.

If any setting cannot be read back, the protected release is `NOT_RUN`.

## Protected inputs

The GitHub `production` Environment must require an authorized reviewer and
provide only the scoped, expiring inputs used by the one-purpose workflow:

- a GitHub Environment secret containing the dedicated read-only inventory and
  verification URL
- a separate GitHub Environment secret containing the workflow-only migration
  URL, used only after inventory/restore evidence is durable
- Vercel Production Sensitive variables for the deployed API `DATABASE_URL`
  using a non-owner `NOBYPASSRLS` login that is a member of `vowpic_runtime`,
  plus a distinct `CONTROL_PLANE_DATABASE_URL` login that is a member only of
  `vowpic_control_writer`
- a workflow-generated loopback PostgreSQL restore target with distinct random
  Admin/restore passwords; no long-lived restore credential leaves the runner
- inventory HMAC key
- independent HMAC keys for edge evidence and runtime-statement-audit evidence
- Vercel token, org ID, and project ID
- one `x-vercel-protection-bypass: <secret>` header pair with an exact 32-character
  alphanumeric Vercel secret, used only for the staged deployment URL; the
  workflow creates/read-backs that exact secret through the
  Vercel project API, preserves every pre-existing automation bypass without
  overwrite or revocation, and fails if readback shows an unexpected entry change
- the cleanup cron token used to reach the authenticated cleanup guard; the
  workflow also publishes it as a Vercel Production Sensitive variable
- approval ID, the exact immutable runtime `source_sha`, and the exact reviewed
  release-control `runner_sha` on current `main`; they are identical for a
  fresh install or ordinary retry, while the one narrowly defined STAGED
  verifier takeover below keeps the old deployed source and uses a newer
  control-only runner
- one 32-byte base64 `SAFE_BASELINE_BUILD_ARTIFACT_KEY_B64`, retained unchanged
  for at least the full ninety-day build-recovery window

The workflow generates `vowpic.edge-lockdown.v1`, `vowpic.edge-handoff.v1`, and
`vowpic.runtime-ddl-audit.v1` inside the same protected job that consumes them.
The edge collector reads and changes only the three exact VowPic custom-rule
names, inspects the Vercel draft before every publish, reads the active rules
back after publish, and stops before mutation if any unrelated rule or existing
draft appears. It creates a random runner-bypass value in a mode-0600 runner
temp file; the value is never a GitHub/Vercel secret, report field, log, or
artifact. Transport or workflow location is not authority: each report must
carry a valid SHA-256 HMAC from its separately protected evidence signer.
Changing `passed`, coverage, route groups, source/runtime/deployment
coordinates, workflow run/attempt, generated/expiry timestamps, or any other
field invalidates the signature. Every signed report has at most a one-hour
lifetime and must have at least fifteen minutes remaining when verified.
Missing, stale, replayed, or unauthenticated reports are `NOT_RUN`.

## Control-plane database roles

Migration `0013` creates the fixed NOLOGIN/NOBYPASSRLS groups
`vowpic_runtime` and `vowpic_control_writer`, enables and forces RLS on every
control-plane table, removes PUBLIC access, grants runtime SELECT plus only the
acceptance-binding consumption columns, and grants the writer the audited
control-plane DML surface. It does not create login passwords.

Run `scripts/release/bootstrap_production_database_roles.sql` once in the exact
Production project's SQL Editor. It creates a transaction-read-only inventory
login with only public-schema SELECT access and no `BYPASSRLS`, a separate
migration login whose default role owns the application schema, the fixed
application logins in a disabled state, and the NOLOGIN identity roles required
by migration `0014`. The migration owner receives only the role membership
needed to transfer the reviewed identity sequence/function ownership; the
migration login remains `NOCREATEROLE`. Its one-result JSON contains newly generated inventory and
migration passwords for immediate transfer to the GitHub `production`
Environment. Do not save that result in the repository, logs, artifacts, or
shell history, and never substitute the legacy administrator URL.

Before any aggregate inventory, the protected workflow rechecks current
`main`, then runs `production_inventory_rls.py` through the migration login.
Every public RLS table must be owned by the migration owner and receive exactly
one permissive, role-scoped, SELECT-only `vowpic_inventory_select` policy. The
inventory proof requires NOBYPASSRLS, zero memberships/ownership/write grants,
direct read privilege on every public table/sequence, and exact policy coverage.
The same reconciliation runs inside the `0012 -> 0014` reservation transaction
after Alembic and before the reservation row, so migration and inventory
visibility cannot commit separately.

Before staging, the protected workflow creates or rotates two distinct
non-owner, non-superuser, NOBYPASSRLS logins, grants each exactly one matching
group, and verifies the exact SQL surface. The provisioning transaction grants
the runtime group only the reviewed SELECT/INSERT/UPDATE verbs for the retained
Stage-1 business tables and creates one command-specific RLS policy per granted
verb; DELETE is absent. Any unexpected group membership, object ownership,
policy, or privilege fails closed. The writer login gets no general
business-table privileges. The workflow writes the two
URLs directly to separate Vercel Production Sensitive variables over stdin and
reads back only key/type/target metadata. Passwords and URLs are never emitted
as evidence. Do not reuse `postgres`, the workflow migration login, a table
owner, `service_role`, a superuser, or any BYPASSRLS credential.
The verification reconnects as each new login and reads back `current_user`,
LOGIN/INHERIT state, `rolsuper`, `rolcreatedb`, `rolcreaterole`,
`rolreplication`, `rolbypassrls`, membership in both fixed groups, reviewed
business privileges, forbidden DELETE, and the owner of `ops_feature_flags`.
Production/Preview readiness performs the same role query and fails if either
application login loses LOGIN/INHERIT, becomes an owner, gains any elevated
role flag, leaves its required group, or joins the other application's group.
Configuration also requires distinct login names and one matching database
target; Supabase direct/pooler URLs are matched by project reference so two
projects cannot pass as one database.

The migration URL remains a separate protected workflow-only administrator; it
is never exposed to the application. CI and pre-release verification must run
the real PostgreSQL RLS test proving the runtime login can read flags but cannot
mutate them and the writer can perform only trigger/CAS-constrained control
updates.

Protected Preview deployments therefore require three separate database
secrets: `PREVIEW_RUNTIME_DATABASE_URL` for the application runtime login,
`PREVIEW_CONTROL_PLANE_DATABASE_URL` for the control-writer login, and
`PREVIEW_MIGRATION_DATABASE_URL` for workflow-only administration. The
identity API, commercial API, Worker, and Provider-proof process must receive
only the first two application URLs. `PREVIEW_CONTROL_READ_DATABASE_URL`
remains a fourth, read-only resolver input and does not replace either runtime
role.

The workflow uses create-once, digest-bound sanitized artifacts as checkpoints, not one best-
effort upload at job end. Sanitized reservation evidence is uploaded before
`RESERVED` or deploy, staged verification before Promote, formal verification
before the `FORMAL_VERIFIED` CAS, and completion evidence before the terminal
`COMPLETED` CAS. Every upload must return a non-empty artifact ID and SHA-256
digest; `if-no-files-found=error` is mandatory. A final failure artifact is only
a diagnostic copy and never authorizes a phase transition.

The raw signed edge and runtime reports stay in runner temp. `restore.dump` is
created only under a separate runner-temp scratch directory, never below
`artifacts/security-baseline`, so even an interrupted deletion cannot place it
in an evidence upload. Credentials, database rows, raw SQL, URLs, and tokens are
also excluded. The reservation stores the exact successful reservation-
checkpoint artifact URL.

One separate build artifact contains only AES-256-GCM encrypted envelopes of a
tar-wrapped exact `.vercel/output` tree and its manifest sidecar. It is retained
for ninety days solely for crash recovery; the public repository never uploads
the plaintext tar or sidecar.
The tar wrapper preserves directory entries, Unix modes, and symbolic-link
targets; the bound manifest independently covers those semantics plus every
regular-file digest. The archive includes a strict one-line manifest sidecar;
recovery must match the unpacked tree against it before any unbound manifest can
be CAS-bound. Before download, the authenticated GitHub artifact API must
return one exact run/name result; only a successful empty result is
`NOT_FOUND`. Authentication, network, rate-limit, server, or malformed-response
failures are `ERROR` and cannot authorize rebuilding. The exact artifact ID and
SHA-256 digest are persisted with the manifest and recovery downloads by ID.
The protected encryption key must not be logged, removed, or rotated while a
retained artifact may still be needed. A wrong/missing key or associated-data
mismatch fails authentication and removes partial plaintext. The encrypted
artifact is uploaded before
its content manifest is CAS-bound while `RESERVED` and before any deploy. A
retry always probes the immutable artifact named by the `RESERVED` row's
recorded attempt, including when an upload succeeded immediately before the
manifest CAS crashed. If that unbound artifact exists it is hashed and bound,
even after the reservation audit deadline. Only a missing artifact, a still-
null manifest, and the original ninety-day artifact-recovery window permit a
build under the same artifact-attempt name. A missing artifact after that
window or any bound
manifest with a missing artifact fails closed for audited manual forward
disposition. This build artifact is not a sanitized audit report; only its
encrypted envelopes may be retained by GitHub Actions. Automatic recovery after
the ninety-day retention boundary is not promised; a missing retained artifact
requires audited manual forward disposition.

The restore target must meet
[`production-inventory-schema.md`](./production-inventory-schema.md). Missing
source credentials return `NOT_RUN`; the restore target is a real PostgreSQL 17
cluster created on runner loopback, not a fixture or an application mock.
For nonlocal targets, a private-looking hostname and caller-supplied expiry are
not proof. The tool resolves every address, compares actual PostgreSQL
`inet_server_addr()` from restore/Admin connections, and reads database owner,
role `rolvaliduntil`, privilege flags, BYPASSRLS, and privileged memberships.
Any public/mismatched address, owner, expiry, or privilege fails before dump;
the report retains only booleans, counts, and a hashed address. Directory
creation is inside the mandatory cleanup `try/finally`, so an artifact/scratch
permission failure still drops the disposable database/role.

## Edge lockdown evidence

Vercel Firewall configuration is project state. Rule IDs and the pre-change
configuration must be read from the actual project; never invent or overwrite
the whole configuration from a generic template.

Before inventory/restore can authorize the first Production database write,
the protected firewall automation must:

1. snapshot and hash the active firewall configuration;
2. install/read back route-group deny rules for anonymous upload/Gatekeeper,
   order creation/generation, checkout/subscription, Partner Invite,
   recommendations, leads, and retired add-ons;
3. leave signed Creem webhook, incident evidence, reconciliation, and logout
   paths outside the deny groups;
4. create a random job-local header bypass, put it first, record its rule ID and
   signed evidence lease, and arrange an `always()` cleanup after every attempted
   lockdown path;
5. emit a signed/create-once edge report bound to project, source SHA, workflow
   run and attempt, formal domain, rule IDs, and the pre-change configuration
   hash.

### Hobby physical-rule limit and logical packing

The current Vercel team is on Hobby. Read-only dashboard verification on
2026-07-16 showed zero existing custom rules, while the platform permits at
most three ([Vercel WAF limits](https://vercel.com/docs/vercel-firewall/vercel-waf)).
The protected release also needs one short-lived custom bypass
rule; therefore the seven `EDGE_ROUTE_GROUPS` are logical groups and must fit
inside at most two physical deny rules. One physical rule ID may back multiple
logical groups, but the signed report must still list all seven groups
separately with `denied=true` and `read_back=true`.

Use this deterministic packing; do not use Vercel's natural-language rule
generator:

| Physical deny | Logical groups |
| --- | --- |
| `vowpic-lock-identity-generation` | `auth_upload`, `generation`, `partner_invite` |
| `vowpic-lock-commercial-retired` | `credit_checkout`, `subscription`, `retired_addons`, `leads_recommendations` |

Each logical group owns one or more deterministic OR condition groups inside its
physical rule, split by HTTP method where needed to avoid a method/path Cartesian
product. Scope every condition to the formal Production hostname and the exact
method/raw-path boundary below; a parent-prefix deny is forbidden when it would
also cover a preserved path.

| Logical group | Required deny boundary |
| --- | --- |
| `auth_upload` | Google intent/exchange, private media upload, Gatekeeper, retired `/auth/login`, and retired public `/upload` methods |
| `generation` | order creation, order deletion while cleanup is paused, retired Admin generation/regeneration, Admin/cron cleanup, and the retired pending-order poller |
| `credit_checkout` | credit catalog/checkout, user refund initiation, Admin credit grant, retired legacy credit mutation, and removed manual-checkout paths |
| `subscription` | subscription plan/current reads, checkout/cancel, and Admin Creem product/checkout probes |
| `partner_invite` | current `/partner-invites` methods and every retired `/session` method |
| `retired_addons` | legacy user create/read/patch and every retired `/live_portrait` method |
| `leads_recommendations` | retired local recommendations, lead submit/list/export, and Admin CRM preview/push/history |

Signed Creem webhook, payment reconciliation/status, incident/runtime evidence,
readiness, and `/auth/logout` are explicit exclusions. The runner bypass is the
third and final Hobby custom-rule slot. If any unrelated custom rule appears,
stop before staging rather than deleting it or exceeding capacity.

During handoff, remove one logical OR condition group, publish and read it back,
then prove the application guard and no-side-effect snapshot. When logical
groups share a rule ID, remove the physical rule only after its final logical
group passes. Restore the preceding exact physical-rule version on any mismatch.
Vercel stages custom-rule changes until an explicit publish, so inspect the
draft diff before every publish ([Firewall CLI contract](https://vercel.com/docs/cli/firewall)).

The workflow treats a missing or mismatched edge report as `NOT_RUN`. After
Promote, remove one logical route group at a time, publish and read it back, and
immediately prove that the un-bypassed representative request reaches its exact
application rejection: the permanently retired routes return their exact 410
code, while subscription checkout returns 401 `session_missing`. A read-only
database snapshot before and after every probe must be identical. On mismatch,
restore the full semantic lockdown from the captured snapshot. Remove the
runner bypass only after all application boundaries pass.

`vowpic.edge-handoff.v1` is independently fresh and signed. It is bound to the
same run/attempt, project, formal domain, source/runtime/deployment, and records
that every deny rule and the runner bypass were removed and read back. Its
`lockdown_after_config_sha256` must equal its `before_config_sha256`; on the
first formal handoff it must also equal the verified lockdown report's
`after_config_sha256`. A retry from `FORMAL_VERIFIED` does not reinstall edge
deny and then skip handoff. It skips the lockdown step and requires a fresh
handoff/current-state readback before terminal completion.

## Database statement-audit evidence

Static zero-DDL scans are necessary but do not prove the deployed runtime. The
protected workflow reads aggregate `pg_stat_statements` counts through a
fixed-output `SECURITY DEFINER` function that is executable only by the
dedicated migration owner. It snapshots the application runtime login's counts,
runs the exact cold-start, auth, Admin, credit, signed-webhook, logout,
reconciliation, cleanup, and readiness HTTP suite, then requires a positive
statement delta and a zero cumulative DDL count. It emits
`vowpic.runtime-ddl-audit.v1` with source SHA, runtime bundle ID, deployment ID,
workflow run/attempt, generated/expiry timestamps, exact coverage, a strictly
positive total statement count, `ddl_statement_count=0`, and `passed=true`.
Raw SQL, credentials, emails, URLs, and tokens are excluded. Without this fresh
report, staged/formal verification is `NOT_RUN`.

## One-time release sequence

1. Read the exact Production Alembic revision through the inventory role,
   recheck current `main`, and reconcile/prove the dedicated NOBYPASSRLS
   inventory policies through the migration login. If it
   is legacy `0006`, run inventory and an isolated PostgreSQL 17 restore, upload
   and prove the sanitized bridge artifact, recheck `main`, then use only the
   protected migration login to upgrade exactly to `0012` and read the revision
   back. `0012` and the already-installed `0014` retry state skip this bridge;
   every other revision fails closed.
2. Run the read-only safe-baseline preflight. `0013` without the unique
   activation is `ORPHANED_SCHEMA`; `0012` with an activation is invalid; a
   completed install blocks every future SHA before build.
3. Run Production inventory and isolated backup/restore. Preserve only the
   sanitized reports. Dump with `--enable-row-security`, restore through the
   isolated target Admin while separately proving the target owner role is
   non-superuser/NOBYPASSRLS, and create only the isolated NOLOGIN placeholders
   required by restored policies. Create the raw dump under runner temp,
   outside the upload tree, and delete the dump/target/temporary roles in all paths.
4. In the same protected job, install and read back the two packed deny rules
   and the first-priority ephemeral runner bypass, probe all seven groups as 403,
   prove the preserved webhook/readiness/logout paths are not denied, and emit
   and verify a fresh signed edge-lockdown report against the exact project,
   source SHA, workflow run/attempt, formal domain, rule IDs, lease, active hash,
   and stripped baseline hash. Persist the sanitized checkpoint and prove its
   artifact ID/digest before migration or any other Production database write.
5. Under one PostgreSQL advisory lock and transaction, upgrade exactly
   `0012 -> 0014` (including `0013` plus the reviewed repair), reconcile the
   inventory policies, and insert `SAFE_BASELINE_INSTALL/RESERVED`. All three
   commit or all roll back. Before the transaction writes, compare
   `pg_control_system().system_identifier`, current database name, and database
   OID through the read-only and migration connections; a mismatch fails before
   migration so inventory of one database cannot authorize mutation of another.
   Immediately before this boundary, read `refs/heads/main` through the
   authenticated GitHub API and require it still equals the approved source
   SHA.
6. Provision and reconnect through the two application logins, publish them as
   Vercel Production Sensitive variables, and verify that the old administrator
   URL is not used for either application setting. Then install the release
   toolchain after installing `uv 0.10.11` through the immutable
   `astral-sh/setup-uv` action commit and verifying the official Linux x86_64
   release checksum. Require the exact `uv 0.10.11` version before continuing
   with
   `npm ci --prefix scripts/release-tools --ignore-scripts`, resolve the
   committed-lock Vercel CLI executable, and require its version output to be
   exactly `56.2.0`. Require nonempty protected `VERCEL_PROJECT_ID` and
   `VERCEL_ORG_ID` coordinates before `vercel pull` or `vercel build`; the
   checkout has no authority to infer, link, or create a project. Compute the
   `SAFE_BASELINE` runtime identity. If no build is bound, build once, tar-wrap
   the exact `.vercel/output`, AES-256-GCM encrypt the tar plus manifest sidecar,
   and upload only their `.enc` envelopes. Prove the
   mode/link/content-aware manifest and
   artifact ID/digest, canonicalizing the upload action's raw 64-hex digest to
   the stored `sha256:<64 lowercase hex>` form before CAS-binding its manifest
   while still `RESERVED`. If a
   build is already bound, download that recorded ciphertext, authenticate and
   decrypt it with the same coordinate-bound AAD, then require its recomputed
   manifest to match. Enumerate every Vercel deployment-result page
   for an exact source/runtime/manifest/role match in any state, failing on a
   repeated cursor, page cap, malformed exact match, or ambiguity. Only zero
   exact matches may deploy the already-bound prebuilt output with `--prebuilt
   --prod --skip-domain`; one `READY` match is reused. A `QUEUED`, `BUILDING`,
   `ERROR`, `CANCELED`, or unknown exact match stops instead of authorizing a
   duplicate deploy. `npx`, a global install, floating package, or rebuild after
   binding is forbidden. Register the exact deployment ID and URL as `STAGED`.
   Re-read remote `main` immediately before any staged deployment and stop on
   drift.
7. Verify the staged URL through deployment protection. All seven Production
   capability rows are OFF; all 33 Stage-1 blocked route/method pairs return
   their exact application-level 503 `capability_disabled`, `cleanup_paused`,
   or `credit_catalog_unavailable` code, and all 17 permanent guest/OpenID,
   legacy-user, legacy-credit, Live Portrait, recommendation, lead, and Admin
   CRM routes return 410. Invalid signed webhook reaches signature
   rejection; cleanup is paused; table counts and URL checksum do not change;
   static and database runtime DDL counts are zero. Persist the staged
   checkpoint and prove its artifact ID/digest before Promote.
8. After durable staged evidence, CAS `STAGED -> PROMOTION_ARMED` before the
   only permitted Promote request. The attempt that performed that CAS may send
   the request once. A retry that starts from `PROMOTION_ARMED` is read-only: it
   checks the exact project's `lastAliasRequest` and the formal domain, and can
   advance only after both prove the target promotion succeeded. If neither
   proves whether the request was sent, stop for audited manual forward
   disposition; never send again. Rolling Releases are forbidden for this one-
   time baseline because partial traffic assignment is not a completed handoff.
   Never rebuild after STAGED except through the exact invalid-runtime-config
   rearm described below.
   Read remote `main` through the authenticated GitHub API immediately before
   and immediately after Promote; an API error or mismatch cannot be recorded
   as a completed current-main install.
9. Require the formal domain to resolve to a READY deployment in the exact
   Vercel project. Verify the formal domain through the temporary edge bypass,
   hand off one route group at a time, persist the formal checkpoint before the
   `FORMAL_VERIFIED` CAS, then persist completion evidence before the terminal
   `COMPLETED` CAS.
   Store the formal report as an opaque reference binding repository, run ID,
   artifact ID, artifact digest, and exact filename. A later resolver must use
   an Actions-read token, verify metadata and archive digest, safely extract
   that one report, and match its byte hash to the activation; an artifact web
   URL is never interpreted as a local path. Recheck authenticated `main` again
   before `FORMAL_VERIFIED` and before `COMPLETED`. On
   `RETRY_FORMAL_VERIFIED`, re-download and hash the exact reference already
   stored in PostgreSQL; do not replace it with a new artifact. Expiry,
   deletion, or mismatch requires audited manual forward disposition.

Crash recovery uses the recorded activation and encrypted build artifact. One
unbound exact source/runtime/build deployment may be bound; zero candidates may
deploy the previously bound output once; multiple candidates, an exhausted or
cyclic deployment listing, a missing build artifact, or a hash mismatch require
audited manual forward disposition. Once STAGED, reuse the recorded deployment
unless the exact invalid-runtime-config rearm below has first returned it to an
unbound reservation.
If Promote already happened, require the formal domain to resolve READY in the
exact project and to the staged ID and require the project `lastAliasRequest`
to show the target `promote` request succeeded before advancing; do not Promote
again. A 404, missing deployment ID, project/org mismatch, pending/failed/
skipped target request, another active alias request, Rolling Release, or non-
READY response is unknown/incomplete state and must never authorize another
Promote.

A failed or missing post-deploy statement audit, or a missing edge-handoff
report, deliberately stops the attempt with the activation retained at its last
durable phase. A GitHub rerun of the same workflow run ID and source SHA
recollects the runtime audit and regenerates fresh signed edge evidence inside
the protected job; it reuses the recorded deployment and never rebuilds after
STAGED unless the exact invalid-runtime-config rearm below is the recorded
reason the deployment could not serve application requests.

One exception exists for a verifier defect discovered only after the immutable
deployment has reached exactly `STAGED`. A newly approved workflow run may
adopt verification ownership without changing `source_sha`, runtime bundle,
manifest, encrypted build-artifact coordinates, deployment ID/URL, role, phase,
or snapshots. Both the read-only preflight and the migration-login CAS require
the deployed source to be an ancestor of the exact current-main `runner_sha`.
The complete source-to-runner diff must consist only of modified release-control
workflow/scripts, their contract test, and this runbook/worklog; application,
migration, dependency, build, configuration, deletion, addition, or rename
changes fail closed. The three takeover implementation/verifier/workflow files
must all be present in that diff. Fresh protected edge evidence is uploaded
before the CAS; the CAS changes only workflow run/attempt, its durable evidence
URL, and the monotonically increasing row version. The old evidence URL is
carried into the adoption record, and the new adoption record is included in
later staged/formal/completion artifacts. The adopted run must reuse the
recorded STAGED deployment and can only move forward through the ordinary
verification and Promote gates.

A second, distinct exception exists only when an unpromoted STAGED deployment
is proven fail-closed because `ACCEPTANCE_IDENTITY_HMAC_KEY` was missing or
invalid in the Vercel Production environment. Vercel Sensitive values are
deliberately unreadable after creation, so the protected GitHub secret must
contain at least 32 characters while the exact project metadata must prove one
project-level `sensitive`, Production-only, non-branch variable. A separately
rotated non-sensitive `ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256` companion is pulled
from that same Production project and compared in constant time with the
SHA-256 of the protected GitHub secret. The protected rearm step computes and
force-upserts only that companion through Vercel CLI before pulling it back; the
secret itself is neither printed nor written by this step. The evidence records
only the key names, minimum length, Sensitive/Production controls, project/team
and run coordinates, and PASS state; it contains neither value nor fingerprint.
This is not a general runtime-configuration repair mechanism. That sanitized
proof, the old activation coordinates, and fresh edge evidence must be durable
before mutation. The migration owner then takes the release advisory lock and
an ACCESS EXCLUSIVE table lock, temporarily disables only the activation
regression trigger inside the transaction, performs one exact version/source/
run/approval CAS from the fully bound unpromoted STAGED row to an unbound
RESERVED row, and restores the trigger before commit. The old coordinates are
retained by hash and durable preflight evidence; no application data or feature
state is changed. The repair attempt intentionally stops, and only the next
attempt of that same workflow run may continue. It must rebuild from a detached
worktree at the original immutable `source_sha`, verify the Sensitive metadata
and companion fingerprint again, bind a new encrypted build artifact and
deployment, and then pass every
ordinary staged, Promote, formal-domain, edge-handoff, and completion gate.

The immutable reservation expiry is an audit/recovery deadline, not a lease
that transfers ownership. An expired `RESERVED`, `STAGED`, `PROMOTION_ARMED`,
`PROMOTED`, or `FORMAL_VERIFIED` activation may be resumed only by the exact source SHA and
workflow run ID after all protected-environment and edge checks are fresh; its
workflow attempt must increase monotonically. `FORMAL_VERIFIED` additionally
requires its already bound artifact reference to remain downloadable and
byte-hash valid; retention expiry does not authorize replacement. Except for
the exact STAGED verifier takeover and invalid-runtime-config rearm above, a
different run or source remains a conflict and requires audited manual forward
disposition. Neither recovery path changes the deployed source.

## Emergency recovery

First audit all seven capability rows OFF and read the snapshot back. Recovery
may then run only `vercel rollback <recorded-deployment-id>`, followed by
rollback-status and formal-domain verification. It may not rebuild, redeploy a
new HEAD, or issue a second Promote through the safe-baseline workflow. Keep
edge deny active until the application guard and no-side-effect checks pass.

## Current execution state

Creating these scripts/workflows and the current Task 5 patch does not execute
the protected release. Until the Production owner supplies the setting, edge,
statement-audit, inventory, restore, staged, and formal-domain evidence, the
release status remains `NOT_RUN`, not risk-contained and not `Production
accepted`.

The local baseline report is also non-transferable evidence. It creates a fresh
virtual environment, installs the platform-matching hash lock, runs `pip check`
and the backend suite, and records the actual code identity. The fingerprint
helper hashes raw Git diff bytes plus untracked path/object hashes with external
diff and text conversion disabled, so console encoding and a Unicode worktree
cannot change the identity. A dirty worktree is reported as
`UNCOMMITTED_WORKTREE` with `source_sha=null`, a base commit, and a content
digest; it is never release-eligible. The report records Python, Node, operating
system, and the selected backend-lock SHA-256. Only exact Python `3.11.15`, Node
`24.17.0`, Linux, and a clean source identity can be release-eligible; a Windows
or otherwise drifted local run remains a useful engineering result but records
`runtime_alignment=NOT_RUN`. Linux and Windows resolver/backend locks are
distinct and are each regenerated and installed on their own CI platform.

Task 5 is implemented in this dirty checkout and passed its local static,
backend, typecheck, build, browser, and eight real PostgreSQL integration gates
on 2026-07-13. The PostgreSQL proof used the exact CI-pinned PostgreSQL 15
image and covered forced RLS/role separation plus migration repair/rollback.
The Mini Program
scripts, dependency, manifest configuration, native tab bar, WeChat/guest auth
branches, anonymous Remote Join UI/API, Live Portrait, recommendations, leads,
and CRM runtime implementations were removed. Permanently retired public paths
now return side-effect-free 410 tombstones; new order input rejects the removed
`remote_join` field; billing UI remains hidden unless a real public capability
and catalog are available. These are local engineering results only: the
worktree is uncommitted, the formal domain was not exercised, and Production
status remains `NOT_RUN`.

Local PostgreSQL integration can prove the mechanics but not Production state.
The current local verification covered a legacy `0012` shape, real
`pg_dump/pg_restore` comparison and cleanup, atomic `0012 -> 0014 + RESERVED`,
all Production seeds OFF, and rollback at the injected post-migration failure
boundary. Production inventory, Production restore, project-setting evidence,
edge changes, deployment, promotion, and formal-domain verification remain
`NOT_RUN` until the protected workflow is deliberately executed.
