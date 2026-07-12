# Risk-lockdown and one-time safe-baseline runbook

This runbook is for the overseas VowPic web deployment. The protected
Tasks 1-4 safe-baseline workflow does not depend on WeChat, a WeChat Mini
Program, guest OpenID, Remote Join, Live Portrait, local recommendations, or
lead capture, and every high-risk public capability is guarded OFF. This is not
evidence that the legacy client/runtime code has already been deleted: WeChat,
Mini Program, and related identity paths remain in the repository until Task 5
passes its Web-only contract and is released through the later stage gates.

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

- read-only inventory and verification database URLs
- a migration database URL used only after inventory/restore succeeds
- the deployed API `DATABASE_URL` using a non-owner `NOBYPASSRLS` login that
  is a member of `vowpic_runtime`, plus a distinct
  `CONTROL_PLANE_DATABASE_URL` login that is a member only of
  `vowpic_control_writer`
- disposable restore database/Admin credentials and explicit credential expiry
- inventory HMAC key
- independent HMAC keys for edge evidence and runtime-statement-audit evidence
- Vercel token, org ID, and project ID
- deployment-protection and edge-bypass header pairs
- a short-lived existing user probe bearer, backend Admin token, and cleanup
  cron token used only to reach application guards
- approval ID and the exact reviewed source SHA
- one 32-byte base64 `SAFE_BASELINE_BUILD_ARTIFACT_KEY_B64`, retained unchanged
  for at least the full ninety-day build-recovery window

The workflow consumes base64 transport copies of the externally generated
`vowpic.edge-lockdown.v1`, `vowpic.runtime-ddl-audit.v1`, and
`vowpic.edge-handoff.v1` reports. The transport is not authority: each report
must carry a valid SHA-256 HMAC from the separately protected evidence signer.
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

Before staging, an approved database administrator must create two distinct
non-owner, non-superuser, NOBYPASSRLS logins, grant each exactly one matching
group, and grant the runtime login only the explicitly reviewed business-table
privileges needed by the retained safe-baseline paths. The writer login gets no
general business-table privileges. Do not reuse `postgres`, a table owner,
`service_role`, a superuser, or any BYPASSRLS credential. Store the two URLs as
separate protected secrets and read back `current_user`, `rolsuper`,
`rolbypassrls`, membership in both fixed groups, and the owner of
`ops_feature_flags`.
Production/Preview readiness performs the same query and fails if the runtime
or writer is owner, superuser, BYPASSRLS, outside its required group, or a
member of the other role's group. Configuration also requires distinct login
names and one matching database target; Supabase direct/pooler URLs are matched
by project reference so two projects cannot pass as one database.

The migration URL remains a separate protected workflow-only administrator; it
is never exposed to the application. CI and pre-release verification must run
the real PostgreSQL RLS test proving the runtime login can read flags but cannot
mutate them and the writer can perform only trigger/CAS-constrained control
updates.

The workflow uses create-once, digest-bound sanitized artifacts as checkpoints, not one best-
effort upload at job end. Sanitized reservation evidence is uploaded before
`RESERVED` or deploy, staged verification before Promote, formal verification
before the `FORMAL_VERIFIED` CAS, and completion evidence before the terminal
`COMPLETED` CAS. Every upload must return a non-empty artifact ID and SHA-256
digest; `if-no-files-found=error` is mandatory. A final failure artifact is only
a diagnostic copy and never authorizes a phase transition.

The raw externally transported reports stay in runner temp. `restore.dump` is
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
credentials return `NOT_RUN`; the workflow must not fill them with fixtures.
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
the Production owner or an approved, separately reviewed firewall automation
must:

1. snapshot and hash the active firewall configuration;
2. install/read back route-group deny rules for anonymous upload/Gatekeeper,
   order creation/generation, checkout/subscription, Partner Invite,
   recommendations, leads, and retired add-ons;
3. leave signed Creem webhook, incident evidence, reconciliation, and logout
   paths outside the deny groups;
4. create a short-lived allowlisted runner bypass and record its rule ID/TTL;
5. emit a signed/create-once edge report bound to project, source SHA, workflow
   run and attempt, formal domain, rule IDs, and the pre-change configuration
   hash.

The workflow treats a missing or mismatched edge report as `NOT_RUN`. After
Promote, remove one route-group deny at a time, read it back, and immediately
prove the un-bypassed request reaches the application `503
capability_disabled` response with no data delta. On mismatch, restore that
exact rule from the captured snapshot. Remove the runner bypass only after all
application guards pass.

`vowpic.edge-handoff.v1` is independently fresh and signed. It is bound to the
same run/attempt, project, formal domain, source/runtime/deployment, and records
that every deny rule and the runner bypass were removed and read back. Its
`lockdown_after_config_sha256` must equal its `before_config_sha256`; on the
first formal handoff it must also equal the verified lockdown report's
`after_config_sha256`. A retry from `FORMAL_VERIFIED` does not reinstall edge
deny and then skip handoff. It skips the lockdown step and requires a fresh
handoff/current-state readback before terminal completion.

## Database statement-audit evidence

Static zero-DDL scans are necessary but do not prove the deployed runtime. A
database/provider statement recorder must cover cold start, auth, Admin,
credit, signed webhook, logout (or explicit pre-Task-7 absence), reconciliation,
and readiness for the exact deployment. It emits
`vowpic.runtime-ddl-audit.v1` with source SHA, runtime bundle ID, deployment ID,
workflow run/attempt, generated/expiry timestamps, exact coverage, a strictly
positive total statement count, `ddl_statement_count=0`, and `passed=true`.
Raw SQL, credentials, emails, URLs, and tokens are excluded. Without this fresh
report, staged/formal verification is `NOT_RUN`.

## One-time release sequence

1. Run the read-only preflight. `0013` without the unique activation is
   `ORPHANED_SCHEMA`; `0012` with an activation is invalid; a completed install
   blocks every future SHA before build.
2. Run Production inventory and isolated backup/restore. Preserve only the
   sanitized reports. Create the raw dump under runner temp, outside the upload
   tree, and delete the dump/target/temporary role in all paths.
3. Verify the fresh signed edge-lockdown report against the exact project,
   source SHA, workflow run/attempt, formal domain, rule IDs, lease, and pre-
   change configuration hash. Persist the sanitized checkpoint and prove its
   artifact ID/digest before migration or any other Production database write.
4. Under one PostgreSQL advisory lock and transaction, upgrade exactly
   `0012 -> 0013` and insert `SAFE_BASELINE_INSTALL/RESERVED`. Either both
   commit or both roll back. Before the transaction writes, compare
   `pg_control_system().system_identifier`, current database name, and database
   OID through the read-only and migration connections; a mismatch fails before
   migration so inventory of one database cannot authorize mutation of another.
   Immediately before this boundary, read `refs/heads/main` through the
   authenticated GitHub API and require it still equals the approved source
   SHA.
5. Install the release toolchain with
   `npm ci --prefix scripts/release-tools --ignore-scripts`, resolve the
   committed-lock Vercel CLI executable, and require its version output to be
   exactly `55.0.0`. Require nonempty protected `VERCEL_PROJECT_ID` and
   `VERCEL_ORG_ID` coordinates before `vercel pull` or `vercel build`; the
   checkout has no authority to infer, link, or create a project. Compute the
   `SAFE_BASELINE` runtime identity. If no build is bound, build once, tar-wrap
   the exact `.vercel/output`, AES-256-GCM encrypt the tar plus manifest sidecar,
   and upload only their `.enc` envelopes. Prove the
   mode/link/content-aware manifest and
   artifact ID/digest, and CAS-bind its manifest while still `RESERVED`. If a
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
6. Verify the staged URL through deployment protection. All seven Production
   capability rows are OFF; all 33 Stage-1 blocked route/method pairs return
   their exact application-level 503 `capability_disabled`, `cleanup_paused`,
   or `credit_catalog_unavailable` code, and all 17 permanent guest/OpenID,
   legacy-user, legacy-credit, Live Portrait, recommendation, lead, and Admin
   CRM routes return 410. Invalid signed webhook reaches signature
   rejection; cleanup is paused; table counts and URL checksum do not change;
   static and database runtime DDL counts are zero. Persist the staged
   checkpoint and prove its artifact ID/digest before Promote.
7. After durable staged evidence, CAS `STAGED -> PROMOTION_ARMED` before the
   only permitted Promote request. The attempt that performed that CAS may send
   the request once. A retry that starts from `PROMOTION_ARMED` is read-only: it
   checks the exact project's `lastAliasRequest` and the formal domain, and can
   advance only after both prove the target promotion succeeded. If neither
   proves whether the request was sent, stop for audited manual forward
   disposition; never send again. Rolling Releases are forbidden for this one-
   time baseline because partial traffic assignment is not a completed handoff.
   Never rebuild after STAGED.
   Read remote `main` through the authenticated GitHub API immediately before
   and immediately after Promote; an API error or mismatch cannot be recorded
   as a completed current-main install.
8. Require the formal domain to resolve to a READY deployment in the exact
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
audited manual forward disposition. Once STAGED, reuse the recorded deployment.
If Promote already happened, require the formal domain to resolve READY in the
exact project and to the staged ID and require the project `lastAliasRequest`
to show the target `promote` request succeeded before advancing; do not Promote
again. A 404, missing deployment ID, project/org mismatch, pending/failed/
skipped target request, another active alias request, Rolling Release, or non-
READY response is unknown/incomplete state and must never authorize another
Promote.

A missing post-deploy statement-audit or edge-handoff report deliberately
stops the attempt with the activation retained at its last durable phase. A
GitHub rerun of the same workflow run ID and source SHA may continue after the
fresh signed evidence is supplied; it reuses the recorded deployment and never
rebuilds after STAGED.

The immutable reservation expiry is an audit/recovery deadline, not a lease
that transfers ownership. An expired `RESERVED`, `STAGED`, `PROMOTION_ARMED`,
`PROMOTED`, or `FORMAL_VERIFIED` activation may be resumed only by the exact source SHA and
workflow run ID after all protected-environment and edge checks are fresh; its
workflow attempt must increase monotonically. `FORMAL_VERIFIED` additionally
requires its already bound artifact reference to remain downloadable and
byte-hash valid; retention expiry does not authorize replacement. A different run or source remains
a conflict and requires audited manual forward disposition.

## Emergency recovery

First audit all seven capability rows OFF and read the snapshot back. Recovery
may then run only `vercel rollback <recorded-deployment-id>`, followed by
rollback-status and formal-domain verification. It may not rebuild, redeploy a
new HEAD, or issue a second Promote through the safe-baseline workflow. Keep
edge deny active until the application guard and no-side-effect checks pass.

## Current execution state

Creating these scripts/workflows does not execute the protected release. Until
the Production owner supplies the setting, edge, statement-audit, inventory,
restore, staged, and formal-domain evidence, the release status remains
`NOT_RUN`, not risk-contained and not `Production accepted`.

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

Task 5 has not run in this checkout. `dev:mp-weixin`, `build:mp-weixin`,
`@dcloudio/uni-mp-weixin`, `manifest.json` Mini Program configuration, and the
`uni.login({ provider: 'weixin' })` branch therefore remain known Web-only scope
debt. They are not used as evidence of safe-baseline completion and must not be
described as removed until Task 5's static, unit, and browser gates pass.

Local PostgreSQL integration can prove the mechanics but not Production state.
The current local verification covered a legacy `0012` shape, real
`pg_dump/pg_restore` comparison and cleanup, atomic `0012 -> 0013 + RESERVED`,
all Production seeds OFF, and rollback at the injected post-migration failure
boundary. Production inventory, Production restore, project-setting evidence,
edge changes, deployment, promotion, and formal-domain verification remain
`NOT_RUN` until the protected workflow is deliberately executed.
