# AI Worklog

## 2026-07-10 — VowPic commercial closure design

### 本次目标

将现有代码、测试、CI、生产旁证和用户逐节确认的治理方案固化为唯一可审阅的商业闭环设计规格。

### 修改范围

- 新增 `docs/superpowers/specs/2026-07-10-vowpic-commercial-closure-design.md`。
- 新增本工作日志；未修改生产代码、数据库 migration、依赖或部署配置。

### 关键决策及原因

- 产品边界确定为海外 Web-only，清退微信、小程序、OpenID 和游客伪身份。
- 保留 FastAPI、Vue/Uni-app H5、Supabase/PostgreSQL、Redis/ARQ、Creem、Evolink 和对象存储，避免无关重写。
- 使用私有 artifact、事务 reservation/outbox、持久 job/attempt、严格 QA、水印和真实删除闭合安全与交付边界。
- 使用 Preview 验证同一 commit 的集成行为，再创建并验收 staged Production deployment；最终核对包含 Vercel deployment、Worker digest、schema 和 flags 的 release bundle，并以 Production accepted 作为唯一“完成”口径。
- 将一次性积分包和订阅拆成独立 gate；按持久化 catalog 把 Creator 统一为 USD 49 / 300 credits，废弃运行时与前端 260 credits 的漂移值，并补齐首付/续费、欠费、取消、invoice 退款和争议合同。

### 影响范围

当前只影响设计和后续实施边界。正式实施预计会涉及认证、上传、存储、订单、积分、支付、Worker、QA、留存、前端、CI 和权威文档；这些修改尚未开始。

### 验证命令与结果

- 占位符/含糊标记扫描：0 个匹配。
- 仓库证据入口检查：24 个引用路径全部存在。
- 五次独立定向审查覆盖验收、后端安全/状态机、一致性、订阅和最终冲突；最后复核未发现剩余 must-fix。
- Markdown 尾随空白、最终换行和 staged Git diff 检查：通过。
- 运行时测试：未运行。本次只修改设计文档，不以运行时测试冒充功能实现。

### 证据来源

- 当前仓库代码、Alembic migrations、后端测试、前端源码、`.github/workflows/ci.yml`、`vercel.json`、README/PRD/Production Acceptance。
- 订阅 catalog/漂移依据：`backend/alembic/versions/20260426_0004_subscription_billing.py`、`backend/app/services/subscription_service.py`、`frontend/src/stores/subscription.ts` 和 `frontend/src/components/PaymentModal.vue`。
- 2026-07-10 的后端测试、前端 typecheck、CI 和生产只读探针审计结果。
- Supabase、Vercel 和 Creem 官方文档。

### 未解决问题与风险

- 独立长运行 Worker 的实际部署主机尚未提供；没有该主机就不能开放生成。
- 面向用户的真实 support channel 尚需提供并验证有人受理；本 release 不用假 contact 表单替代。
- 生产 legacy 用户、账本、订单和公开对象需要只读盘点后才能确定迁移批次。
- Evolink 幂等/对账能力、真实 Creem 事件和私有存储读删仍需 sandbox/production 验证。
- 海外隐私、退款、Cookie 和数据跨境文本需要专业法律审核。

### 后续步骤

等待用户审阅书面规格。用户确认后，使用 `superpowers:writing-plans` 编写逐任务实施计划；在此之前不修改生产代码。

## 2026-07-10 — VowPic commercial closure implementation plan and risk containment

### 本次目标

在已确认设计规格下，把“风险继续恶化”的遗留项先转化为止损动作和硬闸门，再形成可逐任务执行、可验证、不可假完成的完整实施计划。

### 修改范围

- 新增 `docs/superpowers/plans/2026-07-10-vowpic-commercial-closure-implementation.md`。
- 更正设计规格第 6.2 节的 OAuth 责任边界：Supabase broker 负责 Google state/nonce/PKCE；VowPic 后端验证 Supabase JWT 和一次性 app intent，不在没有 Google ID token 时伪称二次验证 nonce。
- 同步设计规格的两级 release identity、Preview create-once 证据、7a/7b 状态机、durable observation/finalizer、identity disposition、RLS、PowerShell/回滚和文档治理边界。
- 在设计规格风险节补记 Creem refund-create、Evolink lost-response reconciliation 和 public-to-private Blob 切换的已确认阻塞。
- 未修改生产代码、数据库、依赖、部署或外部服务状态。

### 关键决策及原因

- 计划分为 6 个 work package、30 个顺序任务、9 个只追加 migration（`0013` 至 `0021`）和 7a/7b 两次独立生产 release；每个 package 失败即停止下游并保持相关能力 OFF。
- 在 Task 0 前新增外部风险止损清单：关闭自动生产发布/匿名 smoke、关现有高风险入口、暂停会丢引用的 cleanup、创建独立 Private Blob/read-only DB/restore DB，并保存事故旁证。
- PostgreSQL 先建立 flags、身份/session、私有素材、商业账本/outbox、支付事实、订阅事实、任务事实和 Partner consent；公开对象删除与 destructive schema cleanup 延迟到完成私有迁移、24 小时观察和回滚演练之后。
- 订单切换只发生在 job schema 已存在后；同一事务创建 order、reservation、job 和 outbox，避免计划顺序上先引用尚不存在的表。
- refresh rotation 使用保留 USED hash 的独立 token generations，才能检测旧 token 重用并撤销整个 family。
- Task 8 对有资产/财务历史的 legacy merge 先 fail closed；Task 13 建立 grant/reversal/merge lineage 后才允许受控执行，避免提前改写历史账务。
- Creem 已签名退款事件可以入站处理，但当前未核实商户 refund-create endpoint/auth/idempotency 合同；公开自助/自动退款创建保持关闭。
- Evolink 当前只能在收到 task ID 后查询；POST 响应丢失且没有 task ID 时不能安全重提，Generation 保持关闭，直到官方合同与 sandbox 证明幂等键或可查询 correlation。
- Worker host、真实 support channel、授权质量图和生产审批属于外部前提；计划明确记录 NOT_RUN，不用本地 build、Admin probe 或旧 artifact 替代。
- Preview 报告由 protected integration workflow 签名、create-once 写入并回读，Production 只能按 exact project/role/SHA 解析；不存在“调用方声称 Preview 已通过”的入口。
- release runtime ID 在部署前计算并注入 API/Worker，最终 manifest 在真实 deployment/build facts 齐全后封存；每个新 job 都从 ReleaseActivation/observation rows 与 Private evidence 重新解析坐标。
- 7a/7b 在预留前失败走 `NO_ACTIVE_RELEASE/ENTRY_REJECTED`，只证明没有外部副作用且不伪造 release；预留后按实际 Worker/API/domain/schema 状态恢复。进入 OBSERVING 后由独立 observation workflow 处理 sample/finalizer 失败和 complete-before-response 歧义。
- 所有 release/migration PowerShell 入口必须原生命令 fail-fast；17 处 parent child-run ID 使用 `${env:NAME}:suffix`，避免冒号解析吞掉 parent ID。
- 把现有仓库中与新契约冲突、会在删除模块后 collection fail 的 auth/TLS/ledger/subscription/payment/Provider/QA/private-media/order/analytics/legacy-config 测试显式纳入对应任务；不保留旧生产 fallback 来换取绿色测试。
- 旧 `generation_credit_policy` 先被限制为只验证权威 lineage，再由 reservation/ledger 报表替代并在最后一个 importer 清退后删除；Admin/ops 计费指标不得读取 mutable `generation_params` 或 fallback amount。
- 两个已注册 Admin 页面都纳入 H5 任务：移除 URL-backed generation probe、regenerate、task ID、debug rounds 和 candidate/permanent URL，改为 normalized read-only facts 与授权 asset-ID 读取。
- 观察工作流拆成 observation-start、独立 scheduled sample、受保护 finalizer、OFF-only emergency 和串行 recovery；7a 失败回滚 exact private-compatible baseline，7b/`0021` 后只允许 OFF/停 Worker/前向修复。

### 影响范围

当前只改变文档执行权威和风险边界。正式执行会涉及认证、TLS/CORS、Admin、上传/SSRF、私有存储、删除、积分/账本、Creem、订阅、Outbox/Worker、Evolink、QA、交付、Partner Invite、H5、CI/CD、迁移、监控和权威文档。

### 验证命令与结果

- Task 标题检查：30 个，编号 1-30 连续；Markdown code fence 为 352 个且成对。
- migration 顺序复核：`20260516_0012 -> 0013 -> 0014 -> 0015 -> 0016 -> 0017 -> 0018 -> 0019 -> 0020 -> 0021`。
- 文件动作复核：每个 Task 的 Files 与最终 `git add` 零漏项/零多项；Create/Modify/Delete 生命周期异常为 0。
- 不安全 PowerShell child-ID 插值扫描：0 个匹配。
- 冻结版哈希：implementation plan `22ef08655543c8c02d9f71c6dcfec1fae31fc7767e0007af05edbc52d8262c0a`（4647 行）；design spec `095af04d7466eee7165657575a19f09ba63c6ef28542e0f92f98dfbe3d082d59`（998 行）。
- 两个独立审查代理加一个结构子审查对同一冻结版复核：P0=0、P1=0；财务权威/Admin UI/observation 三项先前 FAIL 已逐项修复后复验 PASS。
- `git diff --check`：通过，包含 intent-to-add 后的新 implementation plan。
- 运行时测试：未运行。本次是设计纠偏和实施计划文档，不以未实施代码的测试结果冒充功能完成。

### 证据来源

- 真实入口和调用链：`api/index.py`、`backend/app/main.py`、routers、auth、upload/storage、order/credit/payment/subscription、Worker/generation/QA/retention 服务和现有 migrations/tests。
- H5 与发布：Create/Preview/Payment/Account 页面、auth/store、`.github/workflows/ci.yml`、`vercel.json`、现有 production acceptance script、Dockerfile/compose。
- 删除/迁移后测试调用链：现有 Supabase/TLS、Gatekeeper/identity-reference/embedding、catalog/ledger/payment/subscription、order/stage/Provider/QA/delivery/analytics、remote-config/commercial-policy 测试与其真实 imports/assertions。
- 官方资料：Supabase PKCE/session/JWT/Google Auth、Vercel Private Blob/staged deploy/Promote、Creem webhook/refund/cancel 文档、Evolink generation/task-query 文档。
- 当前 npm/PyPI 元数据用于固定前端、Vercel CLI 和 `pip-tools` 版本。

### 未解决问题与风险

- 尚未实际执行生产止损动作；没有外部权限证据时不能称风险已经被控制。
- 未批准长运行 Worker host/registry/deploy/rollback 命令，Production Worker gate 为 NOT_RUN。
- Creem refund-create 合同和 Evolink lost-response 安全能力仍是商业闭环硬阻塞。
- 生产 legacy 规模、Private Blob 真实读删、双 Google 身份、Creem test mode、授权六 case 和真实 support channel 尚未验证。
- 专业法律审核状态仍未提供；只能保证页面与实现事实一致，不能宣称法律认证。

### 后续步骤

完成冻结版终审并提交这三份文档。执行前由用户在“当前会话子代理逐任务执行”与“独立执行会话”中选择；没有选择前不修改生产代码或外部生产状态。

## 2026-07-11 — VowPic commercial closure one-time correction

### 本次目标

对已批准的 VowPic 商业闭环规格和实施计划做一次性纠偏，关闭顺序、依赖、真实证据、并发 fencing、发布恢复及风险继续恶化方面的全部已知缺口，形成可从 Task 1 严格执行的唯一文档权威。2026-07-10 工作日志中标记为“冻结版”的旧 implementation plan 由本修订明确取代；旧哈希和旧阶段描述只保留为历史记录，不再是执行依据。

### 修改范围

- 修改 `docs/superpowers/specs/2026-07-10-vowpic-commercial-closure-design.md`。
- 修改 `docs/superpowers/plans/2026-07-10-vowpic-commercial-closure-implementation.md`。
- 修改 `.gitignore`，忽略本地 `.superpowers/` 工作状态目录。
- 追加本工作日志。
- 未修改产品代码、migration 实现、依赖锁、数据库、部署、域名、Provider、支付、存储或任何外部服务状态。

### 关键纠偏

- 固定七阶段权威顺序：Tasks `1-22 -> 26-27 -> 23-25 -> 28-30`；每个阶段有独立退出门，不能按 Task 编号重新排序。
- Stage 1 在安全基线构建前建立 Python 3.11 hash-locked 依赖；外部止损动作仍需生产 owner 真实执行并留证，文档本身不冒充止损已生效。
- Stage 2 明确私有媒体、严格 QA、fail-closed 水印和真实删除；中央无副作用 410 tombstone router 永久承接退役路径。
- 分离 `PREVIEW_IDENTITY`、`PREVIEW_COMMERCIAL`、`SAFE_BASELINE`、`COMMERCIAL_7A` 和 `CONTRACT_7B` runtime identity，禁止 Preview 证据冒充 Production bundle。
- 将 Creem checkout/签名 webhook、Evolink Provider fetch/lost-response、真实 Google identity、Private Blob、Partner Invite 双浏览器和六个质量 case 固定为真实链路；fixture、seed credits、伪造 webhook、Admin probe 和旧 artifact 均不能产生 PASS。
- Partner consent 固定为 `OPEN -> SETTLED_DELETION_PENDING -> CANCELLED_AND_DELETED`；撤回、Provider 对账、唯一账本结算、授权撤销和对象删除使用同一严格状态机。
- 修正 schema/FK 时序：`credit_reservations.provider_attempt_id` 先 nullable、后在 generation attempts 存在时增加并验证 FK；asset grant、job、attempt 及订单事务依赖按实际创建顺序执行。
- 新增共享 release-coordinate resolver、OpenAPI 双生成稳定哈希、按 capability 的 canary、跨 fresh-job 的 create-once evidence、7a/7b 独立恢复和 forward-fix 规则。
- INITIAL Provider HTTP 前必须锁定并复验同 job/order、RESERVED、未过期且足额的 reservation；Provider、QA、repair/outbox、退款、delivery 和删除的每次外部 I/O 后都重新校验 worker/claim/lease/fence，旧 Worker 不得写状态或账务。
- Mandatory Red-Proof Matrix 覆盖 67 个行为驱动测试文件；缺依赖、import/collection error、skip、NOT_RUN、超时或无关旧失败都不算 red，后续 green 必须重跑同一文件。
- Task 30 修改 Provider projector 后必须先把 Evolink 重新置为 `UNVERIFIED`，再对 exact 7b support SHA 运行真实 lost-response sandbox，并以仅含 `release/provider-contracts.json` 和本工作日志的直接子提交重绑证据；最终 contract Preview 仍需真实 Provider、六质量 case、Worker fencing 和双 schema PASS。
- one-shot response-drop 在 host mutation 前先写入 create-once `PREPARED` intent。`0013` 预声明 intent/hash/state/expiry/cleanup claim/fencing 字段和 CAS 约束；fresh cleanup 取得 `CLEANUP_CLAIMED` 后，以稳定 intent ID 在 host 写入超过 300 秒规则 TTL 的 tombstone、解除规则、等待控制面收敛并证明 runtime-wide absence，关闭 arm 响应丢失及迟到 arm 窗口。

### 验证命令与结果

- `git diff --check`：PASS。
- 结构解析：30 个 Task；权威物理顺序正确；7 个 Stage、7 个 exit；360 个 Markdown code fence 成对。
- Files ↔ 最终 `git add`：零漏项、零多项；508 个声明文件的 Create/Modify/Delete 生命周期无冲突。
- PowerShell/release 扫描：未发现可执行的 `ConvertFrom-Json`、`Invoke-Expression`、直接坐标 JSON 解析或未加花括号的环境变量冒号拼接；规则说明文字中的禁用词不计为命令。
- Red/green 映射：Task 2 新增 release-activation schema 测试、Task 29 resolver/register/fault cleanup 测试和 Task 30 Provider rebind 测试均进入 pre-implementation red、post-implementation green 与最终暂存清单。
- 冻结文档哈希：design spec SHA-256 `8041187C8575312CEAD9DA32283206B35C109E6F91C97365A46BF0DEEF71A6B7`（1006 行）；implementation plan SHA-256 `57914603CC264DAB7034FC2B283A2C91E8435A7B8A617827A72D454A5E8BB247`（5192 行）。
- 独立复审：全局语义审计 PASS；Tasks 16-30 专项审计 PASS；机械/序列审计 PASS；最终 P0=0、P1=0、P2=0。
- 产品运行时测试：未运行。本次只修正文档权威，产品实现尚未开始，不能用规划检查冒充真实功能、Preview 或 Production 验收。

### 未解决问题与风险

- 生产风险止损动作尚未执行，状态为 NOT_RUN；正式域名、高风险入口、自动部署和现有 cleanup 不能宣称已经受控。
- 获批的海外长运行 Worker host/registry/deploy/rollback/fault-control 合同尚未提供，Production Worker 为 NOT_RUN。
- Creem refund creation、稳定 subscription transaction/cancel、Evolink lost-response sandbox、真实双 Google identity、Private Blob 读删和授权六质量 case 尚未实际验证。
- 生产 legacy inventory、恢复演练、迁移、公开对象失效、24 小时观察和 7a/7b rollback/forward-fix 均未执行。
- 真实受监控 support channel、隐私/退款/Cookie/跨境数据文本及专业法律审核仍是外部前提。
- 因此当前状态是“实施计划已纠正并通过文档审计”，不是 scaffold、RC、release-ready 或 `Production accepted`，更不是产品已完成。

### 下一执行门

后续只从 Task 1 开始：先写并运行真实 red test，再实现 fail-closed bootstrap lockdown，并按 Task 1 的 green、暂存和阶段证据要求收口。未通过 Task 1 前不得提前执行 Task 2、部署高风险功能或宣称风险恶化遗留项已经被实际处理。

## 2026-07-11 - Task 1 fail-closed bootstrap lockdown

### Goal and scope

- Execute only Task 1 of the approved VowPic commercial-closure plan.
- Stop growth of auth, upload, generation, checkout, subscription, download, Partner Invite, retired-product, runtime-schema, and deletion risks.
- Do not execute Task 2 migrations, deploy, alter production state, contact external services, commit, or push.

### Evidence and baseline

- Before Task 1 changes, the directly affected backend baseline passed 23/23 tests.
- Before Task 1 changes, the full backend suite ran 193 tests: 192 passed and one pre-existing Provider test errored because it opened the real local PostgreSQL connection. The failing test was `test_evolink_provider.EvolinkProviderTest.test_vision_error_retry_exhaustion_delivers_candidate_when_non_blocking`.
- After correcting test-fixture mistakes, the new lockdown test produced nine behavior failures against the old implementation. The runtime-schema test also enumerated 35 application DDL hits. These were target failures, not dependency or collection failures.
- A read-only subagent audit independently found runtime schema writers in startup, user schema guard, Admin audit, account risk, email log, credit guardrails, and Remote Join session paths. The primary agent verified the reported call sites with repository searches and tests. The subagent did not write files or create another agent.

### Changes

- Added the seven false-default bootstrap capabilities and the stable `Capability` / `require_bootstrap_capability` contract.
- Guarded Google OAuth, upload/Gatekeeper, order creation, checkout/manual payment, subscription mutation, generation Worker, Admin generation/probe/regeneration, and Admin credit actions before their first business or external side effect.
- Kept signed Creem webhook handling and authenticated credit balance/history reads active; balance now reports `can_generate=false` while generation is locked.
- Made legacy Remote Join, Live Portrait, local recommendations, leads/CRM, OpenID user routes, and direct credit mutations return permanent HTTP 410. Router-level tombstones run before auth/database dependencies on the retired public routers.
- Made the temporary credit-package catalog return structured HTTP 503.
- Removed all application startup/request DDL. Strict startup now performs read-only Alembic/table/index/column validation and fails traceably instead of repairing or swallowing schema problems. The legacy auto-create setting cannot enable schema mutation in any environment.
- Hid source, preview, final, master, and download URLs from disabled order responses; disabled order polling does not refresh or restart Provider work.
- Preserved every stored reference and `deleted_at` when any storage deletion fails, returned an explicit retryable failure, removed mutating cleanup GET, and paused both scheduled and Admin cleanup execution.
- Made backend, frontend fallback, runtime JSON, environment examples, and Vercel configuration fail closed. Removed the cleanup cron and the identity-embedding override that weakened QA.

### Verification

- `python -m unittest backend.tests.test_risk_lockdown backend.tests.test_no_runtime_ddl backend.tests.test_remote_join_config backend.tests.test_runtime_config backend.tests.test_commercial_policy -v`: 56 tests passed.
- Real in-process ASGI HTTP checks are included in the 56-test set: retired routes returned 410 before auth/database dependencies; public high-risk routes returned structured 503; no retired Live Portrait URL was serialized.
- `python -m unittest discover -s tests -q`: 226 tests ran; 225 passed and the same pre-existing Provider/database-isolation test errored.
- The same full discovery run with only that exact baseline test excluded: 225/225 passed.
- `npm run build:web`: passed. Existing Browserslist age and Sass legacy/import deprecation warnings remain.
- `python -m compileall -q backend/app backend/tests`: passed.
- Runtime DDL scan across `api` and `backend/app`: zero hits.
- Unsafe production-enabling configuration scan: zero hits.
- `git diff --check`: passed before this worklog append and is rerun at final handoff.

### Risks and remaining work

- No real database, Preview, Vercel deployment, Worker host, storage provider, Supabase identity, or Creem sandbox/production flow was exercised. No external deployment occurred.
- `ux_credit_transactions_order_refund_once` has no migration at current Alembic head `20260516_0012`. Strict production readiness therefore remains blocked on a database missing that index; approved Task 2 migration `20260710_0013` must reconcile it and legacy user columns/indexes. Task 1 intentionally did not add or run that migration.
- The one full-suite Provider test isolation failure is unchanged from baseline and remains assigned to Task 4; it was not hidden or weakened.
- Task 1 is a safe engineering baseline, not a usable commercial product, release candidate, or `Production accepted`. Web-only source removal, private storage, durable ledger/outbox/jobs, real Provider/QA evidence, UI cleanup, CI containment, deployment, and production acceptance remain in later approved tasks.

### External effects and Git

- External production containment remains NOT_RUN.
- No commit, push, deploy, data mutation, payment, email, Provider generation, or production storage operation was performed.

## 2026-07-11 - Task 2 audited PostgreSQL capability authority

### Goal and scope

- Replace Task 1 static route and Worker gates with PostgreSQL-authoritative, audited, deployment/runtime-bound decisions.
- Add the `0013` release control-plane schema, acceptance identity bindings, runtime-bundle builder, and initial fresh-job coordinate resolver.
- Preserve Task 1 tombstones, zero-runtime-DDL behavior, paused cleanup, and fail-closed user-visible behavior.

### Evidence and baseline

- The Task 1 risk/DDL/security baseline passed 38/38 before Task 2 production edits.
- Eight Task 2 test modules were added first; the initial focused run failed because the typed flag interfaces, service, seven models, migration, bundle builder, and resolver did not exist, and because ten active static guard call sites remained.
- One read-only subagent audited the existing migration chain, dirty overlap, Admin identity boundary, Worker stamp gap, and legacy refund index. The primary agent independently verified every adopted finding. The subagent did not write files or create a child agent.

### Changes

- Added typed `FeatureFlagState`, request/Worker contexts, deterministic decision snapshots, PostgreSQL authority reads, OFF-only Redis cache with a 30-second cap and network timeout, audited CAS mutations, and emergency OFF invalidation.
- Removed every active `require_bootstrap_capability` / `bootstrap_capability_enabled` call from routers and Worker code. Retired compatibility functions are permanently false and cannot be enabled by environment variables.
- Added exact runtime coordinates sourced from `RUNTIME_ENVIRONMENT`, pre-deploy `RUNTIME_BUNDLE_ID`, optional OCI digest, and Vercel's system-only `VERCEL_DEPLOYMENT_ID`; missing hosted coordinates fail readiness and flag evaluation closed.
- Added strict Google-backed database-Admin flag endpoints. Backend admin tokens, local JWTs, non-Google Supabase providers, and debug fallback cannot mutate the flag control plane. Preview and Production non-OFF mutations remain blocked until their later protected gates exist.
- Added hash-only, deployment-bound, expiring, single-consumption acceptance identity bindings and protected provisioning tooling. Raw provider subjects and emails are absent from the binding schema.
- Added migration `20260710_0013` with 14 seeded OFF flags, immutable audits/checkpoints/observation samples, release activation CAS/fault fencing, migration leases/checkpoints, observation runs/samples, RLS, and the missing generation-refund partial unique index. Existing Admin audit/email/risk tables and welcome index are reused rather than recreated.
- Added role-discriminated canonical runtime bundle identities and an initial resolver allowlist limited to `preview-identity` and `safe-baseline`; deployment IDs, live snapshots, manifests, evidence, and caller PASS claims are rejected as bundle/resolver authority.
- Sanitized public capability output and kept signed webhook, logout, permanently retired routes, and reference-preserving deletion outside capability blocking.

### Verification

- Task 2 focused suite plus security regression: 38/38 passed; expanded flag/schema checks also passed after implementation.
- Task 1 risk regression plus route adoption checks: 31/31 passed.
- `alembic upgrade 20260516_0012:20260710_0013 --sql`: passed and rendered 29,764 bytes including all control tables, 14 OFF inserts, and RLS statements. A first attempt exposed and then fixed offline JSONB seed rendering.
- Real local legacy database migration: full `0001 -> 0013` chain passed on a partially initialized `ai_wedding` schema with no Alembic version row. Post-migration checks found head `20260710_0013`, seven Preview OFF rows, seven Production OFF rows, all eight control-plane tables with RLS, required unique indexes, and no raw identity columns.
- Database rejection probes passed: invalid `ON` without release coordinates failed its check constraint; updating an inserted audit row failed with `ops_feature_flag_audits is append-only` and was rolled back.
- Real service transaction passed and was rolled back: PostgreSQL resolved Generation OFF, emergency OFF produced one audit row, and no verification audit remained committed.
- Read-only runtime schema guard passed against the migrated local database.
- Full backend discovery ran 263 tests: 262 passed and the unchanged Provider test-isolation error remained. It is assigned to Task 4 and was not skipped or relabeled.
- Compileall passed. A clean temporary-database migration completed once, but its first post-query command used the wrong virtualenv path; a second fresh attempt was blocked before database creation when Docker/PostgreSQL became unavailable. Therefore the successful legacy-database migration is current live migration evidence; the clean-database post-query is not claimed complete.

### Risks and remaining work

- No Production or protected Preview database, Vercel deployment, formal domain, Supabase identity, Worker host, payment, Provider, or storage operation was changed or verified.
- Worker execution intentionally remains closed: immutable server-stamped generation jobs do not exist until Task 16, so Task 2 rejects missing/spoofed stamps instead of trusting payload fields.
- Docker Desktop was started for local verification but its API remained unstable; no project container teardown or destructive reset was performed.
- The Provider test isolation error still prevents a truthful all-green baseline and must be repaired in Task 4 after Task 3.

### External effects and Git

- The local development `ai_wedding` database was migrated to `0013`; no Production credential or database was used.
- No commit, stage, push, deploy, payment, email, Provider generation, or production storage action was performed.

## 2026-07-11 - Tasks 3-4 inventory, restore, and protected release baseline

### Goal and scope

- Produce a read-only, redacted legacy inventory and a destructive-target-safe backup/restore rehearsal before any Production migration.
- Replace automatic Production deployment with secret-free PR CI and one manual, Environment-protected, one-time safe-baseline installer.
- Repair the known Provider test isolation and frontend typecheck blockers without changing their business assertions.
- Do not execute the protected Production workflow, change Vercel project/firewall/domain state, or commit/push.

### Changes

- Added catalog-driven Production inventory for users, legacy entitlements, ledger, orders, and all known URL/object references. Pre-`0013` schemas do not query columns that are absent; the dedicated role must be default read-only, have zero table-write privileges, fail a no-op write with SQLSTATE `25006`, and use `BYPASSRLS` for complete coverage.
- Added an isolated backup/restore rehearsal with redacted command arguments, source/target identity checks, exact revision/table/row/FK/ledger/URL comparison, mandatory target database/role destruction, and dump deletion on every path. URL checks are also catalog-driven for the legacy schema.
- Reservation now validates the actual inventory and restore report contracts rather than hashing arbitrary files. It also compares PostgreSQL system identifier, database name, and database OID through the read-only and migration connections before any write.
- Added same-transaction Alembic `0012 -> 0013` plus unique `SAFE_BASELINE_INSTALL/RESERVED`, bounded reservation expiry, exact deployment URL/ID persistence, CAS phases, deployment recovery by exact metadata, and formal-domain recovery that prevents a second Promote after a lost response.
- Added authenticated edge-lockdown, runtime-DDL, and edge-handoff evidence. External reports require SHA-256 HMACs and exact project/source/run/runtime/deployment coverage; a short or expired edge lease is rejected.
- Replaced PR deploy/smoke jobs with hash-lock reproduction, non-empty full backend tests, frontend locked install/typecheck/build, and a fail-closed aggregate quality gate. First-party Actions, the Python resolver image, framework versions, and Vercel CLI are pinned to immutable revisions/digests.
- Added the manual `safe-baseline-release.yml`: exact dispatch SHA on `main`, global non-cancelling concurrency, Production Environment, preflight/inventory/restore/reservation before Vercel token use, build/deploy once with `--skip-domain`, staged verification, Promote reconciliation, unbypassed formal verification, immutable completion, and per-attempt private upload of sanitized evidence only. Missing protected evidence returns `NOT_RUN`; a rerun of the same run reuses the recorded deployment and never rebuilds after STAGED.
- Generated separate root API and backend hash locks from exact inputs. `uvicorn[standard]` was decomposed into explicit standard dependencies so the Linux-only `uvloop` marker remains in both locks and Windows strict installation does not try to build uvloop.
- Pinned Vue `3.4.21`, TypeScript `5.3.3`, Vite `5.2.8`, vue-tsc `1.8.27`, and compatible Pinia `2.1.7`. Fixed the quality-array null narrowing and wrapped callback-form `uni.uploadFile` so progress and Promise completion both match the installed Uni-app types.
- Completed the Provider test's missing database-session isolation by replacing the unpatched template-style lookup with a deterministic async fixture; the existing non-blocking vision-error behavior assertion was not weakened.

### Verification

- Targeted Task 3/4 inventory, restore, release, and zero-runtime-DDL suite: 41/41 passed after the final evidence and legacy-schema changes.
- Full backend discovery: 304/304 passed; the previously failing Provider isolation case is green without skip or expected-failure relabeling.
- `verify_baseline.ps1`: backend, `pip check`, frontend `npm ci`, `vue-tsc --noEmit`, and H5 build passed; `frontend_unit=NOT_RUN` remains explicit because Task 22 has not added Vitest and a real unit suite.
- Real local PostgreSQL 15 integration on a disposable container: migrated `0001 -> 0012`, removed `username/password/avatar_url` to model the legacy shape, and completed read-only inventory at `0012`.
- Real local `pg_dump/pg_restore`: 17-table revision/table/row/FK/ledger/URL comparison passed; the disposable database and role were dropped and the dump deleted.
- Real local reservation: exact `0012 -> 0013 + RESERVED` committed atomically, all Production capability rows remained OFF, and the three missing legacy user columns were reconciled. Injecting failure after migration but before reservation left revision `0012` and no activation table, proving transaction rollback.
- Linux Python 3.11.15/Debian resolver preverification ran both `pip-tools==7.5.3` compiles twice with byte-identical outputs. Final SHA-256 values were `85e70b198a5eb700c4e92adf8ab9578a28ed55185cdd5cd077e53bcf720a9f79` for the root lock and `e6ab147c51ab4291e90becc37c36c946562e36554fb90e022d8c6581890a0a50` for the backend lock. A fresh Linux virtualenv installed the root lock with `--require-hashes`, and Windows installed the backend lock while correctly ignoring marked `uvloop`.
- Safe-baseline YAML parsed as one job and all 17 shell blocks passed `bash -n`; `git diff --check` passed before this append and is rerun at handoff.

### Risks and remaining work

- The exact pinned `python:3.11.9-slim-bookworm` resolver container could not be pulled locally because Docker Desktop had no HTTPS proxy. The available Linux 3.11 image gave reproducible preverification, but the exact image's two-pass `git diff --exit-code` remains for CI and is not relabeled PASS.
- Frontend build still emits existing Browserslist-age and Sass legacy/import deprecation warnings. They do not fail the pinned build but remain maintenance work outside this release-safety task.
- The release scripts are intentionally long one-purpose audit/state-machine entrypoints with pure tested helpers. Their file sizes were reviewed; splitting them during the safety closeout would add packaging/import risk without changing behavior. Any later feature growth should first extract query/evidence/platform adapters under the existing contract tests.
- Production project settings, deploy-hook removal, edge rules, runtime statement recorder, Production inventory/restore, staged deployment, Promote, and formal-domain probes remain `NOT_RUN`. Therefore risk containment is implemented and locally verified as an engineering baseline, but is not active in Production and is not `Production accepted`.

### Subagent and external effects

- One earlier read-only subagent audited migration/control-plane overlap and wrote no files; the primary agent independently verified adopted findings. No additional subagent was used for Tasks 3-4.
- No Production credential, deploy, domain/firewall mutation, payment, email, Provider request, commit, stage, or push occurred. All disposable local PostgreSQL/container and temporary report resources were removed.

## 2026-07-12 - Tasks 1-4 risk and residual hardening

### Goal and scope

- Close the locally actionable high-priority residuals in the Tasks 1-4 safe-baseline slice without crossing the protected Production boundary or starting Task 5.
- Keep status evidence honest: local engineering verification is not Production containment, a dirty worktree is not a release SHA, and legacy WeChat/Mini Program code is not removed until Task 5 passes.

### Corrections to earlier evidence

- The 2026-07-11 statement that Windows installed the Linux-generated backend lock was incomplete. A fresh isolated Windows install exposed the omitted `colorama` dependency required by `click==8.4.2`; the old cross-platform-lock assumption is superseded by separate Linux and Windows backend/resolver locks.
- Any earlier statement that the current checkout had no WeChat or Mini Program residue was incorrect. `dev:mp-weixin`, `build:mp-weixin`, `@dcloudio/uni-mp-weixin`, `manifest.json` Mini Program configuration, and `uni.login({ provider: 'weixin' })` still exist. Their removal belongs to Task 5 and is deliberately not claimed here.
- The old `python:3.11.9-slim-bookworm` resolver image and Node 20 runtime are superseded. The workflow now pins the current verified Python 3.11.15 image digest and exact Node 24.17.0 LTS; the exact Docker image still has not been pulled and run locally.

### Changes

- Added hash-locked `pip-tools==7.5.3` resolver environments for Linux and Windows. Added a Windows backend lock and an explicit `colorama==0.4.6 ; sys_platform == "win32"` direct marker so platform-specific resolution is visible rather than accidental.
- CI now uses exact runners/Python/Node, reproduces and installs Linux and Windows locks independently, and requires both platform jobs in the aggregate quality gate.
- Added a committed `scripts/release-tools` npm lock for Vercel CLI `55.0.0`; the protected release installs that lock, verifies the CLI's stdout version, and invokes its direct executable instead of `npx`.
- Moved authenticated signed edge-lockdown verification before the first Production database write. Missing or mismatched edge evidence now stops before reservation/migration.
- Made reservation-expiry recovery explicit and fail-closed: expiry does not transfer ownership; only the exact source SHA and workflow run can resume, and workflow attempts cannot move backward.
- Reworked `verify_baseline.ps1` to install the platform lock into a fresh temporary virtual environment, run `pip check` and all backend tests there, and fingerprint the actual working-tree bytes. A final evidence replay exposed that decoding `git diff` through different PowerShell console encodings changed the digest in the Chinese worktree. The fingerprint now runs in a dedicated Python helper over raw Git bytes with external diff/text conversion disabled and covers Unicode tracked/untracked paths; a dirty tree records `source_sha=null`, `UNCOMMITTED_WORKTREE`, a base SHA, a deterministic content digest, and `release_eligible=false`.
- Replaced five local Sass `@import` directives with module `@use`, refreshed `caniuse-lite`, and pinned Sass `1.97.3`. The remaining legacy Sass JavaScript API warnings originate in the pinned Uni-app/Vite adapter chain and are retained visibly rather than suppressed.
- Updated the authoritative plan, design specification, and operational runbook for the exact runtimes, dual-platform locks, locked release CLI, edge-before-write ordering, expired-owner recovery, truthful baseline identity, and the still-pending Task 5 Web-only cleanup.

### Verification

- Full local baseline gate exited 0 from a dirty checkout: 312 backend tests passed in a fresh hash-locked virtual environment; `pip check`, frontend `npm ci`, `vue-tsc --noEmit`, and H5 build passed. The report truthfully recorded `source_sha=null`, `code_identity=UNCOMMITTED_WORKTREE`, `release_eligible=false`, `frontend_unit=NOT_RUN`, and `TASKS_1_4_BASELINE_PASS_WITH_NOT_RUN`.
- Final self-review added red/green regressions proving a stale workflow attempt is rejected by read-only preflight before any Vercel credential/build and cannot re-enter the idempotent `RESERVED` retry branch. It also proves the worktree identity is stable under UTF-8/CP1252 output settings and covers clean, dirty, and Unicode-path repositories. The Task 4 contract suite then passed 32/32 and full backend discovery passed 316/316. The earlier 312-test fresh locked-environment gate remains the dependency-install evidence at this checkpoint; one final locked replay of the raw-byte fingerprint implementation runs after this log entry.
- Linux Python 3.11.15 resolver verification generated its resolver lock twice byte-identically and installed it with `--require-hashes`. Windows generated both its resolver and backend lock twice byte-identically, then a fresh Windows virtual environment installed the backend lock and passed `pip check`.
- Current lock SHA-256 values: Linux resolver `fbe576d9c34667f223d81ceb9d4d51466417e146be0592a079027bfa3bb75380`; Windows resolver `3933f13555742a682336343d5e5d0b241061bc3175c075b863be481f7131cc80`; Linux backend `e6ab147c51ab4291e90becc37c36c946562e36554fb90e022d8c6581890a0a50`; Windows backend `6071f58d70b622c2eef4876c0d42386abb986b397cc56e592d9a516ac85161d2`.
- Disposable PostgreSQL 15 integration migrated `0001 -> 0013`, inserted an expired `RESERVED` activation, allowed the exact run to resume with increasing attempts through `STAGED` and `PROMOTED`, and rejected a decreasing attempt. The container and temporary evidence were removed.
- Frontend build warning checks now report zero local Sass import deprecations and zero Browserslist-age warnings. Twenty-three legacy Sass API warnings and eight `npm ci` transitive deprecation warnings remain visible.
- The locked Vercel CLI installed from its committed npm lock and reported exactly `55.0.0`.
- Amended planning-doc mechanics passed with 30 unique Tasks covering `1..30`, 360 balanced Markdown fences, no unbraced PowerShell environment-variable-plus-colon interpolation, and no stale Node 20/`npx vercel` instruction. Current SHA-256 values are `db19794e23909f2d6de80493366a0e98820ab8b16aae3e6950c1cf704c4dde58` for the implementation plan and `ae18993340b5787dd915229efed1c34fa2ded3ee60536762856db456eef7deb4` for the design specification.

### Risks and remaining work

- Production project settings, signed edge/firewall state, Production inventory/restore, migration, Vercel staged deploy, Promote, runtime statement audit, formal-domain verification, and final CAS remain `NOT_RUN`. Risk containment is not active in Production and Stage 1 has not exited.
- The exact official pinned resolver container could not be pulled/run locally because registry access timed out. CI is the remaining exact-image execution boundary; cached Linux Python 3.11.15 evidence does not replace it.
- `npm audit` is `NOT_RUN` for both frontend and release tools: the configured npm mirror returns 404 because it does not implement the advisory endpoint, and explicit requests to the official npm registry time out. The eight observed frontend deprecation warnings are tied to the current Uni-app Mini Program/Jest-era dependency chain and remain until the Task 5/Task 22 dependency transitions can be verified without weakening Stage 1; they are not treated as a vulnerability audit result.
- Frontend unit tests remain `NOT_RUN` by the approved sequencing contract; Task 22 must install the exact Vitest harness and add real tests before this becomes a mandatory PASS gate.
- WeChat/Mini Program cleanup remains Task 5 scope and cannot be called complete before the protected safe baseline is actually current on the formal domain and Stage 1 exits.

### Subagent and external effects

- One read-only subagent independently scanned warnings, workflow ordering, release recovery, baseline-report truthfulness, and legacy WeChat residue. It wrote no files and created no child agent. The primary agent re-opened the cited files and reproduced every adopted high-priority finding before changing code.
- No Production credential, deployment, domain/firewall mutation, payment, email, Provider request, commit, stage, push, or real-data deletion occurred. Only disposable local dependency environments and PostgreSQL/container resources were used and cleaned.

## 2026-07-12 - Tasks 1-4 release-recovery residual closure

### Goal and scope

- Close the remaining locally actionable release-recovery and evidence-integrity risks in Tasks 1-4 without executing the protected Production workflow or starting Task 5.
- Make build reuse, deployment recovery, Promote reconciliation, and local-baseline reporting fail closed under lost responses, workflow retries, artifact expiry, and Windows PowerShell 5.1.

### Changes

- Archived `.vercel/output` as a tar payload and bound it to a strict sidecar plus a semantic manifest covering files, empty directories, Unix modes, and symlink targets. Recovery now verifies the sidecar and recovered semantic digest before reuse.
- Bound the immutable build artifact to the original `RESERVED` attempt, kept it for 90 days, and allowed an unbound rebuild only within the reservation-to-artifact recovery window. A manifest-bound missing artifact and a retry after the recovery window require manual disposition.
- Required exact Vercel project and organization coordinates before pull/build/deploy/recovery. Complete deployment pagination now verifies source SHA, runtime bundle, manifest, role, project, and state; an exact non-`READY` deployment blocks duplicate deployment instead of being ignored.
- Added `PROMOTION_ARMED` between `STAGED` and `PROMOTED`. Only the attempt that wins the `STAGED -> PROMOTION_ARMED` CAS may send Promote once. A retry from `PROMOTION_ARMED` is read-only and must prove the exact target from formal-domain state plus the project's exact successful `lastAliasRequest`; ambiguous, pending, failed, skipped, rolling-release, or invalid responses require manual disposition.
- Made a formal-domain 404 a controlled fail-closed result and isolated raw database dumps outside uploaded evidence artifacts.
- Added durable evidence checkpoints before reservation/deploy, Promote, formal-domain CAS, and completion CAS so each irreversible boundary has recoverable prior evidence.
- Replaced `[System.IO.Path]::GetRelativePath`, which is unavailable in Windows PowerShell 5.1, with a verified-root-prefix relative-path calculation in `verify_baseline.ps1`.
- Updated the implementation plan, design specification, deployment runbook, workflow contract tests, and recovery tests to match these state and evidence contracts.

### Verification

- Red/green regression cases cover tar transport, directory/mode/symlink semantics, sidecar validation, attempt binding, the 90-day boundary, exact non-`READY` deployment recovery, project/organization binding, formal-domain 404 and malformed JSON, `PROMOTION_ARMED`, `lastAliasRequest`, and rolling-release rejection.
- `.venv\Scripts\python.exe -m unittest backend.tests.test_ci_release_contract backend.tests.test_backup_restore_rehearsal -q`: final handoff rerun passed 57/57 in 12.903 seconds. An earlier correctly provisioned run passed in 14.044 seconds, and a read-only independent review reproduced the same 57/57 result. One intervening invocation with the unprovisioned system `python` was invalid and failed during collection/import because `httpx`, `pydantic`, and the real Alembic package were absent; no code conclusion is drawn from that environment error.
- `python -m unittest discover -s backend/tests -q`: 332/332 passed in 30.727 seconds before the final PowerShell compatibility correction. The corrected isolated baseline then reran the same 332 tests in a fresh hash-locked environment and passed in 38.542 seconds.
- Workflow parsing and shell validation passed for 15 CI `run` blocks and 30 safe-baseline release `run` blocks. `python -m compileall -q backend/app backend/scripts scripts/release api` passed.
- Frontend `npm ci --ignore-scripts`, `npm run typecheck`, and `npm run build:web` passed. `@dcloudio/uni-automator`, Jest, and jsdom are absent from the installed dependency tree. Two `phin` deprecation warnings remain through the deferred Mini Program/Jimp chain, and 23 upstream Sass legacy-JavaScript-API warnings remain visible.
- The locked release tools installed with `npm ci --ignore-scripts`, and the direct Vercel CLI executable reported exactly `55.0.0`.
- The first full isolated baseline completed its installs, 332 tests, typecheck, and build but failed while writing the report because Windows PowerShell 5.1 lacks `Path.GetRelativePath`. A red regression captured that incompatibility; after the compatibility fix, the full gate exited 0 in 217.4 seconds.
- The resulting temporary report had SHA-256 `60618fa7cb83d9b4c83089fadca40448bac7f5a2e75a2d72d2a4071e5c200ccf` and truthfully recorded `schema_version=safe-baseline.local.v3`, `source_sha=null`, `UNCOMMITTED_WORKTREE`, `working_tree_clean=false`, `release_eligible=false`, Python `3.11.9` versus expected `3.11.15`, Node `24.2.0` versus expected `24.17.0`, Windows versus expected Linux, `runtime_alignment=NOT_RUN`, all implemented engineering checks PASS, `frontend_unit=NOT_RUN`, and `TASKS_1_4_BASELINE_PASS_WITH_NOT_RUN`. The temporary report and generated build/install artifacts were removed after capture.

### Risks and remaining work

- The protected GitHub/Vercel workflow, Production inventory/restore/migration, edge and formal-domain checks, deploy, Promote, final CAS, and runtime statement audit remain `NOT_RUN`. No local result proves Production containment or acceptance.
- Exact execution of the pinned Linux resolver container remains `NOT_RUN` because registry access timed out. `npm audit` also remains `NOT_RUN`: the configured mirror lacks the advisory endpoint and the official registry timed out.
- The local dirty checkout and runtime mismatch make the successful local baseline non-release-bindable by design. A clean exact SHA on the pinned Linux/Node runtime is still required at the protected boundary.
- Frontend unit coverage remains `NOT_RUN` until Task 22. The two Mini Program-chain deprecations and Web/WeChat source removal remain Task 5/Task 22 work and are deliberately not removed before Stage 1 exits.

### Subagent and external effects

- One read-only subagent independently audited the stable release-recovery snapshot for P0/P1 issues and reran the 57-test focused suite. It found no new P0/P1 finding, wrote no files, and created no child agent. The primary agent independently verified the adopted evidence and final diff.
- No Production credential, deployment, domain/firewall mutation, database write, payment, email, Provider request, commit, stage, push, or data deletion occurred.

## 2026-07-12 - Pre-sync second review and functional closure

### Goal and scope

- Re-review the complete Tasks 1-4 diff before synchronization, run functional and real-database tests, and permit only ordered feature-branch synchronization.
- Keep `main`, the Vercel Production target, `vowpic.com`, and `www.vowpic.com` unchanged until external protection and CI gates are independently proven.
- Close every locally actionable review finding without starting Task 5 or claiming Production containment.

### Review-driven changes

- Retired caller-selected `X-User-OpenID` / `X-Visitor-Id` authentication and debug fingerprints. Generic bearer authentication now resolves only an already provisioned exact Supabase subject; user creation/update remains inside the guarded, acceptance-bound Google exchange.
- Moved order-create and paused-delete HTTP guards ahead of identity resolution. Guest login remains a database-free 410 tombstone, Partner Invite remains a PostgreSQL-authoritative 503 capability rather than a retired route, and order deletion preserves every reference until durable retries exist.
- Forced control-plane RLS and split runtime/writer sessions were tightened further: the two login names must be distinct, target one database, and belong to exactly one fixed group. Readiness rejects owner, superuser, BYPASSRLS, wrong group, opposite-group membership, and cross-project Supabase pooler URLs.
- Expanded formal verification from 31 paused plus 14 retired route/method pairs to 33 plus 17. Credit catalog and Admin cleanup now have explicit 503 probes; all three retired Admin CRM routes have explicit 410 probes and exact error-code assertions.
- Removed the account-page deletion action and corrected account, privacy, legal-policy API, and inline-consent copy. The site now states that retention periods are scheduled targets and automated/in-account deletion is paused; it no longer claims that files are currently deleted automatically or immediately.
- Added streaming AES-256-GCM envelopes for the exact `.vercel/output` tar and manifest sidecar. The public repository uploads only `.enc` files; wrong key/AAD and truncated/corrupt envelopes fail closed without partial plaintext. The protected key must remain unchanged for the ninety-day recovery window.
- Replaced unauthenticated `git ls-remote` checks with an authenticated GitHub ref guard at six boundaries: before reservation, before deploy, immediately before and after Promote, before `FORMAL_VERIFIED`, and before `COMPLETED`. Every nonterminal retry must still match the approved `main` SHA.
- A `RETRY_FORMAL_VERIFIED` run now receives the stored opaque reference/hash from read-only preflight, re-downloads and validates that exact artifact, and cannot silently replace it. Expired, deleted, malformed, or hash-mismatched evidence requires manual forward disposition.
- Fixed the isolated local-baseline discoverer to use the repository root as unittest's top-level directory, matching CI and allowing the integration package to import the real application.

### Red/green and review evidence

- The initial targeted review tests produced the intended failures for build-artifact plaintext upload, opposite-group RLS membership, same-login/cross-project database URLs, retired identity headers, generic Supabase auto-provisioning, five missing formal probes, false deletion copy, current-main retry drift, stored formal artifact replacement, and missing preflight evidence coordinates.
- Each finding received a focused green rerun. The combined affected suite passed 143/143 after the final changes.
- One read-only subagent performed two review passes, wrote no files, and created no child agent. The primary agent reopened every cited path and reproduced each adopted finding. The final subagent pass reported no open Critical or Important finding and reproduced its focused suite green.

### Functional and integration verification

- `.venv\Scripts\python.exe -m unittest discover -s backend/tests -t . -q`: ran 363 tests and reported `OK (skipped=4)`; all 359 executed tests passed, while four PostgreSQL integration cases remained behind their explicit opt-in switch.
- A random disposable PostgreSQL 15 database then migrated the complete `0001 -> 0013` chain and ran all four RLS integration cases: forced RLS/group-role facts, runtime read/no-write, writer constrained transition, and accidental dual-group readiness rejection all passed. The database, two login roles, and two fixed group roles were removed; readback found zero matching temporary databases and roles.
- `.venv\Scripts\python.exe -m compileall -q backend/app backend/scripts scripts/release`: passed.
- `npm run typecheck` and `npm run build:web`: passed. The build still emits the known upstream Uni-app Sass legacy-JavaScript-API warnings; no warning was suppressed or relabeled.
- Both workflow YAML files parsed. All 53 Linux Bash blocks passed `/bin/bash -n` in the existing backend container, and all three Windows PowerShell blocks passed `ScriptBlock.Create` parsing.
- Mechanical scope audit found 119 changed files, all covered by the Tasks 1-4 file lists, with zero extras and zero missing exact entries. Tasks 1-30 are unique and complete; 360 Markdown fences are balanced; first-party Actions are fixed to 40-character SHAs; `git diff --check` and the unsafe PowerShell environment-variable-colon scan passed.
- Secret-pattern scanning found no private key, GitHub token, OpenAI key, Slack token, or AWS access-key pattern. Database-URL matches were reviewed as explicit local/example/test credentials only.
- The first fresh isolated baseline correctly failed because its unittest discovery omitted the repository top level. After adding the regression and correction, a new temporary environment installed `backend/requirements.windows.lock.txt` with hashes, passed dependency checking, backend tests, frontend `npm ci`, typecheck, and build. Report SHA-256: `5afc1b8454498e32418598b2b09b2bc7d613f7da847c9f984968b3633cd5d25d`.
- The isolated report truthfully recorded `TASKS_1_4_BASELINE_PASS_WITH_NOT_RUN`, `UNCOMMITTED_WORKTREE`, and `release_eligible=false`: local Windows/Python 3.11.9/Node 24.2.0 differs from protected Linux/Python 3.11.15/Node 24.17.0, and frontend unit tests remain `NOT_RUN` until Task 22.

### Risks and synchronization boundary

- `npm audit` remains `NOT_RUN`: the configured mirror does not implement the advisory endpoint and the official registry previously timed out. No new npm dependency was added in this review loop.
- Production project settings, Vercel automatic Production assignment, deploy hooks, signed edge/firewall evidence, GitHub `production` Environment protection, Production inventory/restore/migration, staged deploy, Promote, runtime statement audit, and formal-domain verification remain `NOT_RUN`.
- The observed GitHub `production` Environment has no protection rule, and Vercel auto-assignment has not been read back as disabled. These are hard blockers to merging `main` or touching the formal domain.
- Ordered synchronization may therefore stop only at an atomic feature-branch commit/push and PR/CI. It must not merge, dispatch the protected release, or change `vowpic.com` / `www.vowpic.com` until both external protections and every required check are green.

### Local external effects

- One HTTP regression initially created one legacy `wx_*` user in the local development database; the exact test row was identified and deleted immediately, with one-row cleanup readback.
- Disposable PostgreSQL databases/roles and the failed diagnostic container were removed. No Production credential, business database, formal domain, payment, email, Provider request, storage object, or real customer record was touched.

## 2026-07-12 - Preview runtime pre-sync correction

### Goal and scope

- Verify the already pushed Tasks 1-4 commit against its real Vercel Preview before opening a PR or advancing any Production/domain step.
- Correct only the Preview cold-start failure while preserving Production config validation, route-level capability guards, and the protected release boundary.

### Live evidence and root cause

- Feature commit `b6012ddd9b41c9d603643edc931156931ac31170` was pushed only to `codex/vowpic-commercial-closure`. Vercel Git integration created Preview deployment `dpl_9iB7V5u6TkAzLbMxukfo735sYEva`; it was `READY`, `target=null`, and bound to that exact branch/SHA.
- The Preview root returned HTTP 200, but `/api/v1/ops/public_config` returned HTTP 500 `FUNCTION_INVOCATION_FAILED`. Runtime logs proved the FastAPI lifespan rejected missing `RUNTIME_BUNDLE_ID`, `ACCEPTANCE_IDENTITY_HMAC_KEY`, and `CONTROL_PLANE_DATABASE_URL`.
- `vercel.json` incorrectly forced `RUNTIME_ENVIRONMENT=production` for every deployment. Vercel's documented `VERCEL_ENV` already distinguishes `preview`, `production`, and `development` at build and runtime.
- `vowpic.com` and `www.vowpic.com` remained on Production deployment `dpl_8ryc7dh5XjocPnPjyw1Yq9ZkqGTA`, source SHA `52208b66fda5ab1a327c3af7d3840eabe74016fd`; the Preview had only its generated `.vercel.app` alias.

### Changes

- `Settings.runtime_environment` now gives explicit `RUNTIME_ENVIRONMENT` first precedence and otherwise consumes Vercel's system `VERCEL_ENV`; the all-deployment Production override was removed from `vercel.json`.
- Invalid strict configuration no longer crashes the function process. Lifespan records one `runtime_config_blocked` state, skips database/readiness initialization, and retains only liveness and operational readiness surfaces.
- A lifecycle-backed middleware returns a sanitized `runtime_not_ready` 503 before application dependencies for every other route. A missing lifecycle state revalidates strict configuration and defaults to blocked, while route-contract tests explicitly mark their synthetic app state as config-valid. The response does not expose blocker details, and CORS wraps the guard so allowed-origin GET errors and browser preflight remain usable.
- Core and operational readiness now report `commercial_config` as an immediate blocker before opening either database when strict configuration is invalid. Non-debug operational readiness cannot downgrade itself with `strict=false`.
- Sentry uses the resolved Preview/Production runtime environment instead of labeling every hosted error as Production.

### Red/green and regression evidence

- Initial red tests reproduced all four defects: Vercel Preview resolved as `development`, lifespan raised, application API reached the database and returned 500, and `vercel.json` still forced Production.
- Additional red tests proved strict default/missing-environment requests could bypass the first guard, CORS headers/preflight were lost, and readiness could continue into database probes despite invalid config.
- A first request-time implementation correctly blocked Preview but polluted local route-contract tests; full discovery exposed five route regressions. The final lifecycle-state implementation preserved the strict hosted behavior without changing those existing expectations.
- Focused runtime/lockdown/commercial suites passed 70/70; the additional valid-config/missing-lifespan fail-closed case passed 1/1. Final `.venv\Scripts\python.exe -m unittest discover -s backend/tests -t . -q` ran 375 tests and reported `OK (skipped=4)`; all 371 executed tests passed.
- `git diff --check` and the follow-up high-risk secret scan remained clean before the corrective commit.

### Remaining synchronization boundary

- The corrective commit, feature-branch push, replacement Preview deployment, and live HTTP re-verification remain pending at this log entry. A successful static root plus expected liveness/readiness/fail-closed API behavior is required before PR creation may continue.
- The GitHub connector cannot create the PR (`403 Resource not accessible by integration`), and neither available browser session is signed in. PR-only CI therefore remains `NOT_RUN` until authenticated PR creation is available.
- No `main` merge, protected release dispatch, Production deployment, alias promotion, DNS/domain mutation, payment, email, Provider request, or business-data write occurred.

### Subagent

- The same read-only review subagent re-opened only the follow-up patch and independently identified the strict-default guard and CORS ordering gaps. It wrote no files and created no child agent; the primary agent reproduced both failures, added red tests, implemented the lifecycle-state correction, and reran the affected and full suites.

## 2026-07-13 - Stage 1 release-line reconstruction and PostgreSQL readiness closure

### Goal and scope

- Reconstruct a Stage-1-only release line from exact remote `main` SHA `52208b66fda5ab1a327c3af7d3840eabe74016fd` instead of merging the mixed Stage 1/Stage 2 PR.
- Reuse the reviewed Tasks 1-4 history, carry only the fourteen allowlisted analytics/runtime repair paths, and keep every Web identity/Task 5 path out of the candidate.
- Prove the candidate locally, including a real PostgreSQL 15 migration/RLS run, without changing GitHub/Vercel settings, Production data, deployments, or formal-domain aliases.

### Baseline and reconstruction evidence

- `git fetch origin --prune` left `origin/main` at the exact audited SHA. The new isolated branch/worktree did not previously exist and was created cleanly from that remote ref.
- The pre-change remote-main command `python -m unittest discover -s backend/tests -t . -q` could not collect because `backend/tests` was not yet an importable package. The exact existing CI-style discovery from `backend/` ran 193 tests and reproduced one existing error: `test_vision_error_retry_exhaustion_delivers_candidate_when_non_blocking` opened a real database session. The reviewed `b6012dd` change supplied an explicit forbidden-session assertion and a template-context stub; the isolated regression then passed 1/1.
- Seven reviewed commits were cherry-picked in order. Before the runner-safe repair was carried, the resulting branch ran 375 tests with one known failure because the console-encoding fingerprint test depended on the checkout already being dirty. The reviewed `25c3e21` test creates its own dirty Unicode repository and removed that runner-state dependency.
- The fourteen repair files were checked using the union of tracked diff paths and `git ls-files --others --exclude-standard`; `git diff --name-only` alone does not include newly created untracked files. All fourteen current blobs matched their intended reviewed source commit exactly before the focused commit: twelve from `9a771e6` and the CI/test runner-safe pair from `25c3e21`.

### Changes

- Added the forward-only `20260712_0014` click-stats repair, schema-readiness checks, bounded migration timeouts, runtime-bundle/contract update, alert timestamp correction, real migration coverage, and runner-safe CI checks as the focused `ea1ab10` Stage 1 repair commit.
- Added a bounded CI wait before PostgreSQL integration tests. It requires both the official image entrypoint completion marker and final container-local `pg_isready`; it times out after 120 seconds and prints only the last 100 service log lines on failure.
- The wait was added through a red/green contract test. The red run failed because the step was absent; the green run passed after the minimal workflow change. This became commit `4fbe9ef`.

### Verification

- Focused repair/CI/alert suite: 70 tests passed with zero failures.
- `.venv\Scripts\python.exe -m unittest discover -s backend/tests -t . -q`: 386 tests passed, 8 conditionally skipped, zero failures.
- `npm --prefix frontend ci --ignore-scripts`: installed 594 locked packages successfully; two upstream `phin` deprecation warnings remained visible.
- `npm --prefix frontend run typecheck` and `npm --prefix frontend run build:web`: both passed. The Web/H5 build completed and retained the known upstream Sass legacy-JavaScript-API warnings.
- `powershell -ExecutionPolicy Bypass -File scripts/release/verify_baseline.ps1`: exited 0 after a fresh hash-locked Windows dependency install, dependency check, 386-test backend run, frontend locked install, typecheck, and Web build. The clean source identity was `ea1ab1014773381119a50e065d875afaf52bc866`; Python `3.11.9`, Node `24.2.0`, and Windows differ from the protected Python `3.11.15`, Node `24.17.0`, Linux runtime, so `runtime_alignment=NOT_RUN` and the local result is not Production-release evidence. Frontend unit tests also remain `NOT_RUN` by the existing Task 22 contract.
- The first disposable PostgreSQL run exposed an official-image readiness race: the temporary initialization server briefly reported healthy and then shut down before the final server started. Container-local `psql`, host TCP, and host psycopg2 probes all passed after the final entrypoint marker. A second harness attempt stopped before tests because PowerShell treated normal `docker logs` stderr as terminating output. After correcting only that diagnostic capture, the evidence-backed rerun passed all 8 real PostgreSQL tests: the full migration chain through `20260712_0014`, forced RLS/group-role isolation, runtime read/no-write, writer transition, wrong-group rejection, correct-column idempotency, missing-column repair, nullable backfill/hardening, and incompatible-type rollback without revision advance.
- CI YAML parsed successfully. `backend.tests.test_ci_release_contract` passed 58/58 after the readiness change. The disposable container and all four integration environment variables were removed; readback found no remaining `vowpic-stage1-postgres` container.

### Scope, risks, and external effects

- No Task 5/Web identity path was copied into this branch. The later identity migration remains Stage 2 and must use full revision ID `20260710_0014` with `down_revision = "20260712_0014"` only after Stage 1 exits.
- GitHub `production` Environment protection, `main` ruleset, protected secret/variable names, Vercel Production Branch Tracking, deploy-hook removal, PR checks, merge, protected workflow dispatch, Production inventory/restore/migration, signed edge/runtime reports, Promote, and formal-domain acceptance remain external gates and are not represented as PASS here.
- No branch push, PR creation, merge, GitHub/Vercel setting save, Production data write, deployment, domain mutation, payment, email, provider call, or customer-data operation occurred in this reconstruction. Local effects were limited to commits in the isolated branch, dependency/build directories ignored by Git, and disposable containers that were removed.

### Subagent

- One previously started read-only subagent audited Stage 2 migration/RLS/FK implications and confirmed Task 6 cannot start before Stage 1 exit. It wrote no files and created no child agent. The primary agent independently checked the adopted Stage 1 sequencing and migration-head evidence; no production-code subagent or parallel writer was used.

## 2026-07-13 - External Stage 1 release-path protection

### Goal and authorized boundary

- Close only the external protections required before synchronizing the reconstructed Stage 1 branch: Vercel Production domain assignment, GitHub `production` Environment protection, the exact Production branch policy/variable, and the `main` merge ruleset.
- The authorized synchronization boundary remains a feature-branch push plus draft PR/CI. No merge, protected release dispatch, Production deployment, domain reassignment, DNS change, secret-value creation, or Production-data write is authorized by this step.

### Vercel readback

- Production Branch Tracking remains exactly `main`.
- `Automatically assign Custom Production Domains` was disabled and saved. A fresh authenticated page readback reported the checkbox unchecked, the Save button disabled, and the page stated that Production deployments require manual promotion.
- The authenticated Git settings page reported zero Deploy Hooks; no hook was deleted or fabricated.
- The Production environment still listed `www.vowpic.com` and `webdev-inspiration-hub.vercel.app`. No deployment was created or promoted and no domain/alias mapping was changed.

### GitHub readback

- GitHub CLI authentication was revalidated as account `zsrt001` with repository scope. The earlier invalid-token symptom was a network-routing failure, not a credential failure.
- Environment `production` (`14989360592`) now requires reviewer `zsrt001`, has `can_admins_bypass=false`, and uses custom deployment branch policies. Exact API readback returned one allowed branch policy: `main` (`54502242`).
- Environment variable `PRODUCTION_BASE_URL` was created with exact value `https://www.vowpic.com` and read back through the GitHub API.
- Active repository ruleset `Protect main release path` (`18866940`) targets only `refs/heads/main`, has no bypass actor, prevents deletion and non-fast-forward updates, requires a pull request with conversation resolution, and requires strict status check `quality-gate`. The required context matches the exact job identifier in `.github/workflows/ci.yml`.
- The Environment secret API returned `total_count=0`. No placeholder or guessed value was created. The release workflow references 21 required names: `ADMIN_TOKEN`, `CLEANUP_CRON_TOKEN`, `EDGE_EVIDENCE_HMAC_KEY`, `EDGE_HANDOFF_REPORT_B64`, `EDGE_LOCKDOWN_REPORT_B64`, `INVENTORY_HMAC_KEY`, `PRODUCTION_MIGRATION_DATABASE_URL`, `PRODUCTION_READ_ONLY_DATABASE_URL`, `RESTORE_TARGET_ADMIN_DATABASE_URL`, `RESTORE_TARGET_CREDENTIAL_EXPIRES_AT`, `RESTORE_TARGET_DATABASE_URL`, `RESTORE_TARGET_ROLE_NAME`, `RUNTIME_AUDIT_HMAC_KEY`, `RUNTIME_DDL_AUDIT_REPORT_B64`, `SAFE_BASELINE_APPROVAL_ID`, `SAFE_BASELINE_BUILD_ARTIFACT_KEY_B64`, `SAFE_BASELINE_PROBE_USER_BEARER`, `VERCEL_AUTOMATION_BYPASS_HEADER`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, and `VERCEL_TOKEN`.
- `EDGE_HANDOFF_REPORT_B64`, `EDGE_LOCKDOWN_REPORT_B64`, and `RUNTIME_DDL_AUDIT_REPORT_B64` must contain independently produced signed evidence; they cannot be generated or replaced with placeholders during synchronization.

### Network diagnosis and local routing

- `gh api` initially failed while `curl` and Git-over-SSH remained usable. DNS inspection showed Proxifier remote-DNS placeholder addresses in `127.*`; the active profile was `静态.ppx` and its application rule no longer included `gh.exe`.
- The profile was loaded through Proxifier's supported silent-load path. Adding `gh.exe` alone did not fix the failure because `api.github.com` still resolved to a placeholder. Adding `github.com` and `*.github.com` to the profile DNS exclusion list, then clearing the DNS cache, changed `api.github.com` to a public address and made both `gh api` and `gh auth status` succeed.
- This diagnosis was verified with a real HTTP 200 repository API response and an authenticated user readback. No token value was displayed or copied into the repository.

### Verification and remaining boundary

- GitHub Environment, branch-policy, variable, secret-count, and ruleset state were read back again through the official REST API after the writes; the returned objects matched the intended values above.
- Vercel's saved checkbox state was read back from the authenticated DOM after the write. No Production deployment, Promote action, or alias/domain mutation occurred.
- The next allowed action is the verified Stage 1 feature-branch push and draft PR. CI must complete against the pushed commit; merging or starting the protected release remains blocked until all 21 owner-supplied secret values and all three signed evidence reports exist and are independently validated.

### Subagent

- No new subagent was started for this external-configuration step. The one previously used read-only subagent wrote no files; the primary agent performed every authenticated readback and applied every external setting change serially.

## 2026-07-13 - Task 5 Web-only runtime cleanup and residual removal

### Goal and scope

- Align the active product with the current overseas responsive Web SaaS contract. The Uni-app `h5` token remains only the browser compiler target; it is not the product definition.
- Remove obsolete Mini Program, WeChat/guest identity, anonymous Remote Join, Live Portrait, local recommendation, lead, and CRM runtime surfaces without rewriting historical migrations or deleting compatibility data needed for existing in-flight orders.
- Keep high-risk commercial capabilities fail-closed and make retired public routes deterministic, side-effect-free 410 responses.

### Baseline and red evidence

- Before the cleanup, the selected existing backend baseline ran 36 tests with zero failures; frontend typecheck and Web build also passed. This separated pre-existing health from the new contract failures.
- The first `backend.tests.test_web_only_contract` run produced 38 failures. Additional focused red cases covered hidden billing without a real catalog, forbidden public `remote_join`, dead operations configuration, legacy Admin identity surfaces, the unused lead-phone crypto path, the obsolete visitor header, and incorrect deployment documentation.
- No validation, assertion, release guard, or security check was weakened to obtain green results.

### Changes

- Removed Mini Program scripts/dependency/configuration, native tab-bar code, QR poster dependency/use, WeChat and guest authentication branches, guest merge, and anonymous partner-join UI/runtime routes.
- Final diff scanning found and removed a hard-coded non-Web OAuth redirect plus three unused/no-op Supabase helpers (`getSupabaseClient`, `isSupabaseConfigured`, and `signOutFromSupabase`). Their contract test failed before deletion and passed afterward.
- Removed the active Live Portrait, local recommendation, lead/CRM, and obsolete credit-mutation implementations. A centralized retired router preserves stable 410 compatibility for former public endpoints before auth, query parsing, database access, or other side effects.
- Public authentication is Google-only; public user/Admin contracts use canonical UUID/email identity. The internal `users.openid` compatibility column remains for the later data migration and for bounded internal mapping, but is no longer a public or Admin authorization surface.
- New orders reject unknown input including `remote_join`; two-subject creation follows the local-couple flow. Historical worker/session fields needed to finish already-created orders remain readable.
- Removed dead operations flags/configuration, the unused phone-crypto module/config, obsolete visitor-header handling, fake frontend unit-test script, hard-coded price/catalog fallbacks, and signed-out account defaults that looked like real subscription data.
- Billing and plan UI now remain absent unless the matching public capability and a real catalog are available. The Golden Anniversary detail page now exposes only its correct workflow.
- Rewrote current README/PRD/deployment guidance as Web SaaS documentation while preserving frozen specifications, migrations, inventory evidence, anti-fraud WeChat Pay/QR detectors, and negative legacy-header tests.
- Strengthened the retired-route contract so all 26 probes prove the database dependency is never resolved and no URL key is serialized. Added `path_separator = os` to Alembic configuration under a red/green config test, eliminating the Alembic 1.18 legacy path-splitting warning.

### Verification

- `Set-Location backend; ..\.venv\Scripts\python.exe -m unittest discover -s tests -q`: 406 tests collected, 398 passed, and the 8 explicitly gated PostgreSQL tests were skipped in discovery; zero failures.
- The same 8 gated tests then ran against the CI-pinned PostgreSQL 15 image and all passed: four forced-RLS/role-isolation cases and four click-stats migration/idempotency/backfill/rollback cases. The final rerun used `-W error::DeprecationWarning` and remained clean.
- The disposable `vowpic-task5-postgres-20260713` container was removed after exact name/image verification, port `5432` was confirmed free, and the Docker Desktop instance started for this proof was stopped.
- The affected aggregate suites ran 194 tests with zero failures. Task 5 itself requires the Python Web-only contract, typecheck, build, static scan, and browser gates; the separate frontend unit framework is introduced by the later Task 30 contract and is not counted as a Task 5 omission.
- Final `\.venv\Scripts\python.exe -m unittest backend.tests.test_web_only_contract -v`: 19 Web-only/static/tombstone tests passed after the OAuth helper and retired-route evidence cleanup.
- `Set-Location frontend; npm ci --ignore-scripts`, `npm run typecheck`, and `npm run build:web`: passed. Build output retained the upstream Dart Sass legacy-JavaScript-API deprecation warnings and Uni-app update notice.
- Desktop browser at 1280x720: Home had no horizontal overflow, remote flow, price, Buy Credits, View Plans, or pricing overlay; signed-out Account showed placeholders and sign-in requirements with no Admin console; Golden Anniversary exposed only `Start Golden Anniversary` and the Golden workflow.
- Independent Chrome mobile emulation at 390x844: the complete Home rendered at `scrollWidth=390` with no horizontal overflow, remote flow, price, Buy Credits, or View Plans. Desktop and mobile console/error collection returned zero warnings or errors.
- The in-app viewport override API did not apply its requested 390x844 size and remained 1280x720; mobile evidence therefore came from an independent CDP Chrome session rather than being misreported from that failed override.

### External release boundary

- No local Task 5 code or test gap remains open in this entry. Real Google OAuth, checkout/payment/webhook, AI provider generation, private object storage/download, Production inventory, deployment, formal-domain routing, and Production data belong to later protected stages; they were not fabricated with mocks or guessed credentials. Their capabilities remain OFF until those gates supply real credentials, authorization, and evidence.
- No commit, push, PR, merge, deployment, domain change, payment, email, provider call, or Production/customer-data write occurred.

### Subagent

- One read-only Subagent audited Web-only residuals, dependencies, compatibility boundaries, and missing tests. It wrote no files and created no child agent. The primary agent independently opened the cited code, implemented the cleanup, and verified the final behavior and scans.

## 2026-07-13 - Task 6 Web identity and revocable-session schema

### Goal and scope

- Add the approved Stage 2 normalized Web identity, OAuth-intent, local-session, rotating refresh-token, account-claim, email-conflict, immutable merge-lineage, and account-tombstone contracts.
- Follow the real Stage 1 head `20260712_0014`; the plan's stale parent reference was corrected before implementation. No Task 7 cookie/session endpoint, Production migration, deployment, or domain action was included.

### Baseline and red evidence

- The pre-change database had nine user foreign keys: two already used non-destructive actions and seven retained financial/order/subscription/job facts still used `ON DELETE CASCADE`. Business identity tables did not yet exist.
- Initial schema tests produced 21 missing-contract failures. Initial real PostgreSQL integration produced six failures because all eight Task 6 tables were absent.
- Second-review red tests reproduced the remaining security gaps: unsafe pre-existing cluster roles were accepted; malformed JWT claim types resolved users; backslash/control-character redirects passed; seven consumed/revoked/resolved states could move backward; legacy fallback had no counter; and a downgrade could silently drop nonempty identity facts.

### Changes

- Added and exported the eight Task 6 ORM models and migration `20260710_0014`, parented to `20260712_0014`. `users.email` is non-authoritative profile data, `openid` is nullable legacy compatibility data, and the existing OpenID unique-constraint/index representation now matches ORM metadata without changing uniqueness semantics.
- Converted the seven retained user foreign keys from destructive `CASCADE` to `RESTRICT`. Added exact provider/subject, family, generation, hash, state/timestamp, proof-consumption, conflict-resolution, and local-redirect constraints.
- Added forced service-only RLS for every identity table; ordinary authenticated users have no direct table privileges. The resolver is owned by a validated non-login/non-superuser/non-bypass role, uses fixed `pg_catalog, public` search path, rejects malformed provider/subject/anonymous claim types, and exposes EXECUTE only to `authenticated`.
- Added a restricted PostgreSQL sequence that records nonzero legacy-fallback use without exposing it to ordinary authenticated users. Normalized and invalid claim paths do not increment it.
- Added one-way database guards for identity revocation, OAuth intent consumption, session version/revocation, refresh state, claim consumption, conflict resolution, tombstone cleanup, and immutable merge facts. Deterministic advisory locks plus exact single-use proof binding make concurrent merge attempts commit at most once.
- Downgrade now fails before mutation when legacy columns are incompatible, any Task 6 table retains facts, fallback use has been observed, or a pre-existing identity role has unsafe attributes. An empty safe downgrade restores the previous foreign keys, comments, resolver, and Supabase RLS policies.

### Verification

- `python -m unittest backend.tests.test_identity_session_schema backend.tests.test_alembic_config -v`: 13 tests passed after the final schema-alignment additions.
- Disposable, pinned PostgreSQL 15 integration: `python -m unittest backend.tests.integration.test_identity_rls -v` passed 15/15. It covered fresh full-chain upgrade, exact constraints, per-table ACL/RLS, resolver ownership/configuration, malicious `search_path`, strict JWT shapes, fallback counting, service access, concurrent merge, one-way states, delete restrictions, two fail-closed downgrade paths, successful empty rollback, and unsafe-role rejection without revision advance.
- Fresh `alembic current` reported `20260710_0014 (head)`. Targeted autogenerate inspection found zero drift for all eight Task 6 tables, all seven touched identity comments, OpenID uniqueness metadata, and email uniqueness removal. Repository-wide `alembic check` still reports objects explicitly owned by later remote-join/control-plane cleanup tasks; they were not deleted out of sequence.
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/release/verify_baseline.ps1` exited 0 after a fresh hash-locked Windows dependency install, dependency check, 431-test backend discovery (`OK`, 23 environment-gated skips), frontend locked install, typecheck, and Web build. The known Dart Sass legacy-JavaScript-API warnings remained non-fatal.
- `git diff --check` passed. The disposable PostgreSQL container was exact-name verified and removed. Local Python `3.11.9`, Node `24.2.0`, and Windows still differ from the protected Python `3.11.15`, Node `24.17.0`, Linux release runtime, so this is engineering evidence rather than Production acceptance.

### External boundary and residual ordering

- No commit, push, PR, merge, deployment, Production database write, domain/DNS mutation, payment, email, Provider call, or customer-data operation occurred.
- The remaining repository-wide Alembic drift belongs to later approved tasks that retire legacy remote-join/control-plane structures. Removing those objects in Task 6 would violate dependency order and rollback compatibility; their presence is not represented as a Task 6 PASS.

### Subagent

- One read-only Subagent reviewed Task 6 models, migration, tests, and the authoritative plan/spec. It wrote no files and created no child agent. The primary agent independently reproduced every accepted finding in real PostgreSQL, fixed the implementation, added regression tests, and reran the complete suite.

## 2026-07-13 - Task 7 Google PKCE, Cookie sessions, protected Preview identity, and residual closure

### Goal and scope

- Replace the old browser Bearer/local-storage identity path with broker-verified Google Authorization Code + PKCE and revocable first-party Cookie sessions.
- Add the protected, deployment-bound Preview identity smoke/cleanup path without authorizing Production, a Worker, or any other commercial capability.
- Close locally actionable Web-only security, stale-build, dead-code, branding, cache, and browser-flow residues found during the second review. Preserve only the migration/read compatibility explicitly deferred by the approved plan; no out-of-order Production contract migration was executed.

### Baseline and red evidence

- The read-only Task 7 audit found the old implicit-token exchange, persistent Bearer path, permissive Supabase claim mapping, email/OpenID identity fallback, non-verifying database TLS, browser Admin service-token acceptance, and missing Origin/CSRF/security-header/Preview contracts. The primary agent opened and reproduced each accepted finding before changing it.
- Focused red tests covered missing Cookie/session/rotation/reuse, strict broker claims, exact-origin/CSRF/CORS/TLS/Admin separation, Preview activation/cleanup, and browser storage constraints.
- The final residual pass added red evidence for uncleared PKCE state on failed callbacks, unreachable retired Live Portrait Worker code, a dead queue producer, seven obsolete static files, international metadata still declaring `zh-CN`/the old brand, and an unchanged Service Worker cache namespace.
- One final baseline attempt failed after all 471 backend tests because the diagnostic Uni dev process still held `esbuild.exe`; process/parent/path inspection identified the exact Node/esbuild pair. After exact-process cleanup, the same baseline command passed.

### Changes

- Added hashed OAuth intents, strict Supabase Google broker verification, provider-subject-only normalized identity resolution, acceptance-binding consumption, local session/access/rotating-refresh issuance, refresh-family reuse revocation, logout, `/auth/me`, and Cookie-only browser dependencies. Browser Admin now requires the local session plus database role; service credentials use an isolated dependency.
- Added exact Origin/CSRF/CORS rules, minimum browser security headers, strict public error shaping, verified Supabase/PostgreSQL TLS, staged-origin activation proof, and POST-only mutating operations. Implicit OAuth fragments fail closed.
- Frontend login now uses PKCE with no persistent broker session or local Bearer. All account/navigation/create/Admin consumers use the local session. OAuth failure and exchange paths always clear the app intent plus the explicit Supabase PKCE storage key, verifier, and user key.
- Added the protected Preview identity workflow, exact activation reservation locks/uniqueness, deployment/runtime binding, owned callback add/read-back/removal, isolated Acceptance Identity binding, linked-session cleanup, artifact-independent database-authoritative cleanup, and explicit NOT_RUN behavior when protected inputs are absent.
- Removed generated Uni remote asset dependencies and made the Web build fail on the forbidden DCloud/Google font hosts. Added a guarded clean step because local Node `fs.rmSync` did not remove the old output reliably; identical rebuilds now produce identical hashes.
- Removed the retired Live Portrait queue producer and its unreachable database/provider body while keeping only the deterministic no-side-effect legacy queue rejection required before the later contract migration. Removed obsolete native-tab icons, static placeholder README, and two stale promotional backgrounds.
- Unified active international metadata and install surfaces on `VowPic` with English default language; updated the Service Worker namespace to `vowpic-pwa-v2` so previously deployed clients discard the old application shell.
- Fixed Admin dashboard loading to verify `/admin/me` before any Admin data reads, corrected the Windows Web dev launchers, removed a dead auth helper/import, and made non-Supabase production-inventory providers use a generic retired-provider category instead of product-specific identity labels.

### Verification

- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/release/verify_baseline.ps1`: final rerun exited 0 after a fresh hash-locked Windows Python install, `pip check`, 471 backend tests (`OK`, 23 environment-gated skips), `npm ci --ignore-scripts`, frontend typecheck, and Web build.
- `python -m unittest backend.tests.test_cookie_sessions backend.tests.test_supabase_auth backend.tests.test_web_security_baseline backend.tests.test_web_only_contract backend.tests.test_risk_lockdown backend.tests.test_preview_identity_workflow -q`: 92 focused tests passed after the OAuth/dead-code/static cleanup.
- Two consecutive `npm run build:web` runs produced the same file/hash manifest: 92 total output files and 48 bundled assets. The built-output scan found no forbidden remote asset host or deleted legacy asset.
- Real Chrome against the final built SPA rejected `#access_token`, reduced the URL to `/pages/auth/callback`, removed every seeded intent/PKCE session key, stored no access token, made no remote request, and emitted no page or console error. Earlier browser passes also covered Home/Create/Account/Admin/legal pages and verified Admin data requests do not start before role proof.
- `python -m compileall -q backend/app scripts/release`, `python -m pip check`, `npm ls --all --silent`, and `git diff --check` passed during the final verification loop. Test listeners on 3000/4173/9222 were removed.
- Expected non-fatal build output remains limited to the Uni update notice and Dart Sass legacy-JavaScript-API deprecation warnings.

### External boundary and truthful NOT_RUN items

- The 23 environment-gated PostgreSQL integration cases were not rerun in this final loop: Docker Desktop remained stuck while stopping, `docker version` timed out, no local `psql` existed, and no isolated PostgreSQL URL was supplied. Their skips are not counted as PASS.
- The protected real-Google Preview Playwright journey is NOT_RUN because `RUN_PREVIEW_E2E`, `PREVIEW_BASE_URL`, `PREVIEW_GOOGLE_STORAGE_STATE_PATH`, and `PREVIEW_GOOGLE_EMAIL` were not supplied. The test exits nonzero with that explicit reason rather than becoming a false skip.
- `npm audit --omit=dev` is NOT_RUN: the configured mirror did not support the audit endpoint and the official registry connection timed out through the local proxy. Locked install and dependency-tree checks passed, but they do not replace an advisory audit.
- Local Python `3.11.9`, Node `24.2.0`, and Windows differ from the protected Python `3.11.15`, Node `24.17.0`, Linux release runtime, so the local report is engineering evidence and correctly remains non-release-bindable.
- Planned legacy database fields/tables and bounded historical readers remain until the approved later migration/observation gate. They are not public authentication/product features and were not deleted out of sequence.
- No commit, push, PR, merge, deployment, domain/DNS mutation, Production database write, payment, email, Provider call, or customer-data operation occurred.

### Subagent

- One read-only Subagent audited the Web-only/Task 7 runtime, dependencies, compatibility boundaries, and missing tests. It wrote no files and created no child agent. The primary agent independently reopened the cited code, reproduced accepted findings, implemented the fixes, and reran the final focused, browser, build, and complete baseline checks.

## 2026-07-13 - Task 8 verified legacy-account claim and soft closure

### Goal and scope

- Add a controlled recovery path for an empty legacy account using an authenticated canonical Google session plus a server-verified claim proof. Do not expose arbitrary support strings as proof and do not move financial history before the later lot/lineage schema exists.
- Add immediate identity/session revocation and PII minimization for account closure while preserving financial rows and media references for the later deletion state machine.

### Baseline and red evidence

- Before Task 8 changes, 34 selected session, identity-schema, ledger, and Admin tests passed.
- Interface tests first failed because the three Task 8 services did not exist. The behavior suite then produced 24 business assertion failures with zero harness errors for unverified proof, mismatch/reuse/expiry, commercial footprint, graph safety, session revocation, and retained history.
- A second-review regression test failed because the service attempted to overwrite the PostgreSQL trigger's proof-consumption timestamp. The service was corrected to refresh the trigger-owned fact instead of assigning it.

### Changes

- Added hash-only payment/support claim-proof creation. Payment proof requires an existing paid purchase plus a matching processed provider event; support proof is internal-only, database-Admin-authorized, tied to a monitored HTTPS/email channel, and requires a 64-hex audit-evidence hash.
- Added one-time empty-account merge with exact proof binding, canonical normalized identity checks, graph safety, legacy session/token revocation, immutable merge lineage, and fail-closed rejection when any financial, subscription, order, or job footprint exists. Immutable financial/audit owners are not rewritten.
- Added soft account closure that revokes identities and sessions, minimizes profile/auth fields, writes an idempotent tombstone, retains all financial/media rows, and truthfully marks media cleanup pending.
- Added Cookie/CSRF-protected customer routes and account-page controls. The UI requires exact `CLOSE MY ACCOUNT` confirmation and does not claim that media bytes were already deleted.

### Verification and boundary

- `python -m unittest backend.tests.test_account_merge backend.tests.test_account_closure backend.tests.test_cookie_sessions backend.tests.test_identity_session_schema backend.tests.test_credit_ledger -q`: 46 tests passed after the trigger-ownership correction.
- A broader Task 8 backend run passed 51 tests; frontend `npm run typecheck` and `npm run build:web` passed. Build output retained only the known Uni update notice and Dart Sass legacy-API warnings.
- The real PostgreSQL identity/RLS suite is NOT_RUN in this loop. Docker API calls repeatedly timed out; logs showed a half-stopped Desktop/backend state, and a clean exact-process restart still did not restore the engine within the bounded health window. No local PostgreSQL service, listener, or `psql` executable exists. Unit/static success is not represented as a PostgreSQL PASS.
- No commit, push, PR, merge, deployment, Production data write, domain mutation, payment, email, Provider call, or customer-data operation occurred.

## 2026-07-13 - Task 9 private media, grant, quota, and deletion schema

### Goal and scope

- Introduce the expand-only private-media authority required by Tasks 10-11 without deleting legacy URL compatibility columns before Task 30.
- Make `asset_id` plus private `object_key` authoritative, keep signed/provider URLs out of the schema, and persist exact upload admission, quota settlement, grant binding, and deletion-retry facts.

### Baseline and red evidence

- Before Task 9 changes, 22 selected RLS, commercial-retention, and order-creation tests passed.
- `backend.tests.test_media_asset_schema` first failed at import because all six planned model modules and migration `0015` were absent.
- The second-review red pass then failed on an unbound quota reservation and missing database mutation guards. That proved crash recovery could not identify the exact daily bucket and that storage tombstones could still be physically deleted before absence was confirmed.

### Changes

- Added upload-batch, media-asset, hash-only access-grant, quota-window, per-user slot-state, and per-batch/part quota-reservation models. Grant rows include nullable indexed future job/attempt UUIDs with no premature foreign keys, plus exact provider, purpose, runtime bundle, target API deployment, serving role, expiry, and read limit facts.
- Added migration `20260710_0015` on `20260710_0014`, with `RESTRICT` ownership/history references, unique provider/object key and batch/part facts, nonnegative quota/deletion counters, coherent deletion leases, exact state checks, service-only forced RLS, and owner-only active-asset metadata reads that do not grant object-key access.
- Quota reservations bind the exact daily window and permit actual attempted bytes to exceed the reserved file maximum so rejected over-limit chunks can still be charged truthfully. Slot and settlement timestamps remain durable and idempotent.
- Added database triggers that enforce the documented media transition graph, make immutable object facts stable, make read revocation/deletion timestamps one-way, and reject physical media-row deletion. The media service role has no SQL `DELETE` privilege.
- Added canonical UUID-list columns to Orders and nullable `source_asset_id`/`video_asset_id` references to retained Live Portrait rows while preserving every legacy URL column for the later inventory/backfill/contract migration.

### Verification and boundary

- `python -m unittest backend.tests.test_media_asset_schema -v`: 8 schema/transition/ownership/RLS tests passed.
- Aggregate Task 8-9 and adjacent regressions ran 64 tests with zero failures.
- `python -m compileall -q ...`, `alembic heads`, SQLAlchemy metadata import/sort, and `git diff --check` passed; the sole migration head is `20260710_0015` and all six Task 9 tables are registered.
- Fresh-database upgrade, constraint/RLS behavior, and downgrade rehearsal remain NOT_RUN for the same verified local Docker/PostgreSQL environment failure recorded above. This entry does not call Task 9 release-accepted until that real PostgreSQL gate is rerun.

### Subagent

- One read-only Subagent audited Tasks 8-19 and classified each plan task against the current tree. It wrote no files and created no child agent. The primary agent independently opened the cited files, replaced an early interface-only scaffold with real behavior tests and implementations, found and fixed the trigger-timestamp defect, and verified the Task 9 schema changes directly.

## 2026-07-14 - Stage 5 multi-runtime evidence closure and residual audit

### Goal and scope

- Close the locally actionable Stage 5 Provider/Worker/cleanup evidence gaps without enabling Generation, starting Stage 6, deploying, binding the formal domain, or fabricating protected evidence.
- Repair the CI-to-Preview evidence handoff so the exact secret-free PR gate, Preview Identity runtime, Preview Commercial runtime, and joint cleanup can be aggregated without pretending they share one runtime bundle ID.
- Recheck residual code/test truthfulness after the Web SaaS cleanup; no WeChat or Mini Program surface was reintroduced.

### Baseline and red evidence

- Focused workflow tests first failed because CI did not persist its exact PR gate evidence and the Preview cleanup job attempted Stage 5 aggregation before Commercial ran. Since `commercial` depended on that cleanup job, the incomplete aggregate necessarily failed and skipped Commercial.
- The original gate aggregator accepted only one runtime ID, while the approved PR, Preview Identity, and Preview Commercial evidence intentionally belongs to different immutable runtimes. Moving the aggregate alone would therefore still have rejected valid evidence.
- Full backend discovery initially found two stale test contracts: one Provider catalog test depended on the caller's working directory for `sys.path`, and the Web-only test still asserted that the now-real Vitest suite must not exist.
- Executing the local baseline exposed another historical false status: it still reported `frontend_unit=NOT_RUN` after Task 22. A Windows PowerShell 5.1 run also showed that normal `unittest` progress on stderr could be elevated to `NativeCommandError` when the Python process participated in a strict pipeline.

### Changes

- Added exact `runtime_scope` ownership to every release gate and exact scope-to-runtime binding validation to `aggregate_gates.py`. Stage 5 now binds `pr`, `preview_identity`, `preview_commercial`, and a deterministic `stage5_composite` cleanup runtime; missing, extra, malformed, stale, duplicate, or cross-runtime evidence fails closed.
- Kept the Creem sandbox contract in the later full-release profile because the approved Stage 5 exit is Provider/Worker focused and the Creem contract remains intentionally unverified until the later payment stage.
- CI now uploads the exact PR evidence only after its own registry aggregate succeeds. The manual protected Preview workflow requires the exact CI run ID/attempt, downloads that immutable artifact with `actions:read`, and validates source SHA, gate hash, case set, runtime, freshness, and schema before reuse.
- Removed the premature cleanup-job aggregate. Added a final always-run Stage 5 job after both Preview Identity cleanup and Preview Commercial cleanup. It materializes a fresh content-scoped evidence set, verifies Provider source lineage and terminal task status, validates exact API/Worker/image coordinates, and writes the aggregate report outside the raw gate-evidence directory so later recursive replay cannot ingest the report as a fake case.
- Hardened Worker cleanup evidence to record and revalidate API deployment ID and image digest, retained terminal Redis-heartbeat absence proof, and kept Provider case/alias cleanup independent and fail-visible.
- Restored the real frontend unit contract across CI, Web-only tests, and `verify_baseline.ps1`. The baseline now actually runs `npm run test:unit`, records `frontend_unit=PASS` only after exit zero, and keeps runtime alignment/release eligibility separate. Its Python test runner writes progress to stdout while preserving the real exit code and all diagnostics.
- Made the catalog-import test independent of current working directory and redirected runtime-coordinate assertions to the extracted public runtime-bundle service rather than the old FastAPI entry point.
- Removed locally generated Playwright failure artifacts and ignored the standard Playwright result/report directories so future browser verification does not leave untracked test residue.
- Replaced product-level `H5` wording in the current authoritative design/implementation documents with `overseas Web SaaS`; retained `h5` only where it is the literal Uni-app compiler target/output token and explicitly documented that it is not a product form.

### Verification

- Final focused release/workflow regression: 70 tests passed with zero failures.
- Final backend discovery from `backend`: 723 tests passed, 33 environment-gated integration cases skipped, zero failures. Expected failure-path logging for catalog, retention, private storage, and fail-closed runtime guards remained visible.
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/release/verify_baseline.ps1` exited 0 after a fresh hash-locked Windows dependency install. Its report recorded backend tests, frontend typecheck, frontend unit, and Web build as PASS; `UNCOMMITTED_WORKTREE`, Windows/Python 3.11.9/Node 24.2.0 runtime mismatch, and `release_eligible=false` remained truthful.
- Frontend locked install and exact top-level tool check passed. OpenAPI types were byte-deterministic at SHA-256 `1ef36b51a2ba18c87299fb02d156afaceede5bb1d54a90d212997ca31e91042a`; typecheck passed; 5 Vitest tests passed; the Web build completed.
- OpenAPI export was byte-deterministic at SHA-256 `ee859a918793ad16e9d1cab5770fea92f43f60098ccf0c9eb27eda6e196eefb2`.
- System Chrome ran the built Web SaaS accessibility checks for Home, Login, Privacy, and Terms: 4/4 passed with no serious or critical axe violations. Local Firefox remained NOT_RUN because its Playwright engine is unavailable after the previously recorded download timeout; CI still installs and requires both engines.
- Both changed workflows parsed as YAML; `git diff --check` passed except for existing line-ending conversion warnings. `actionlint` is not installed locally, so semantic GitHub Actions execution remains a CI check rather than a claimed local PASS.

### External boundary and remaining gates

- Stage 5 protected execution remains NOT_RUN: no Preview/PostgreSQL/private-storage/Vercel/Google acceptance credentials or accepted EvoLink lost-response contract evidence are present locally. Generation remains OFF and Stage 6 is not authorized by the plan until the real Stage 5 Provider proof passes.
- The 33 skipped integration cases include real PostgreSQL/RLS/concurrency and protected external-service paths. They are configured in CI but were not relabeled as local PASS; Docker/PostgreSQL is still unavailable in this environment.
- `npm audit --omit=dev --audit-level=high` remains NOT_RUN because the configured npm mirror returns `404 [NOT_IMPLEMENTED]` for the audit API; the earlier official-registry attempt timed out. Locked install and exact dependency-tree checks passed but are not represented as an advisory audit.
- No commit, push, PR, merge, deployment, domain/DNS mutation, Production database write, payment, email, Provider submission, or customer-data operation occurred.

### Subagent

- No Subagent was used for this closure. All reads, edits, test executions, workflow review, and evidence checks were performed directly in the primary session.

## 2026-07-15 - Task 17 real PostgreSQL/Redis recovery and Worker image closure

### Goal and scope

- Replace the Task 17 hard-fail integration placeholder with a real, opt-in PostgreSQL plus Redis crash-recovery test and close the remaining local Worker-image build gate.
- Repair only defects proven by real asyncpg migration execution; do not enable Generation, call the Provider, advance Stage 6, deploy, or weaken the protected Stage 5 exit criteria.

### Baseline and red evidence

- A fresh PostgreSQL upgrade first failed because asyncpg bound the seeded catalog UUID parameters as `varchar` for UUID columns. A second fresh upgrade then failed because prepared asyncpg statements cannot contain multiple `CREATE FUNCTION`/`CREATE TRIGGER` commands.
- The existing Worker recovery integration file was an unconditional hard failure, so it could not prove PostgreSQL commit/rollback behavior, Redis ARQ deduplication, expired-lease reconciliation, or stale fencing.
- The first real integration run exposed a test-fixture ordering defect: the generation job was flushed before its referenced order. The fixture was corrected with explicit parent flushes; no production foreign key or validation was relaxed.

### Changes

- Added explicit PostgreSQL UUID casts to the commercial-catalog seed binds and a regression assertion for those casts.
- Split asyncpg-incompatible multi-command migration blocks in commercial ledger, Creem payment, subscription, and generation-job migrations into one DDL command per `op.execute`. Added an AST-based compatibility test covering migrations `0016` through `0020`.
- Replaced the Worker recovery placeholder with a local-only, explicit opt-in integration harness. It creates and drops a unique temporary database whose base name must contain `test`, runs `alembic upgrade head`, uses an isolated ARQ queue, cleans exact Redis keys, rejects non-PostgreSQL/non-Redis or non-local URLs, and still drops the temporary database if migration execution fails or times out.
- The real scenario proves that a Redis enqueue followed by a PostgreSQL rollback is safely redispatched and deduplicated, that an expired `SUBMITTING` attempt resumes in `RECONCILING` without a second Provider submission, that no credit settlement occurs, and that the stale fence cannot complete the attempt.

### Verification

- Focused migration, outbox, lease, heartbeat, and real recovery regression: 31 tests passed.
- Full backend discovery from the authoritative `backend` import root with the recovery integration enabled: 725 tests passed, 32 external/environment-gated tests skipped, zero failures.
- The integration's fresh temporary PostgreSQL database upgraded through migration `20260710_0020`; real Redis queue state and PostgreSQL recovery state were asserted before cleanup.
- The original `backend/Dockerfile.worker` built as `vowpic-worker:local` without editing the Dockerfile or dropping `--require-hashes`. Because the host/container DNS path returned false loopback/poisoned answers, the verification command used current bounded host mappings for Debian and PyPI after a first fail-visible build; the cached retry completed after Docker Desktop restarted from a transient daemon exit.
- Image inspection reported Linux/amd64, user `vowpic`, UID/GID `10001`, and command `["python","scripts/worker_entrypoint.py"]`. A container run imported `arq`, `cv2`, `onnxruntime`, and the real `app.worker` module and printed `worker-import-ok`.
- `.venv\\Scripts\\python.exe -m pip check` reported no broken requirements. `git diff --check` passed with only pre-existing LF-to-CRLF conversion warnings for three script files.

### Cleanup and boundary

- Removed the verified temporary `crane.exe` aliases, the downloaded image archive/checksums directory, the temporary PostgreSQL/Redis containers, the three local image tags created solely for this build proof, and the temporary Proxifier download rule. Restored the Docker Direct Proxifier rule name and the normal screen-log level.
- Protected Preview Identity/Commercial execution remains NOT_RUN because the required Preview environments/secrets are absent. The EvoLink lost-submit-response contract remains unverified because no accepted Provider idempotency or queryable client-correlation evidence exists. These are external Stage 5 gates, not silently reclassified local passes; Generation remains OFF and Stage 6 remains blocked by the approved plan.
- No Subagent was used. No commit, push, PR mutation, merge, deployment, domain/DNS mutation, Production data write, payment, email, Provider submission, or customer-data operation occurred.

## 2026-07-15 - Cross-runtime security, Worker, database, and browser closure

### Goal and scope

- Close the remaining locally actionable dependency, Worker-image, real-database, and public-browser gaps without enabling Generation or changing any remote environment, domain, deployment, payment, Provider, or Production state.
- Preserve the external Stage 5/6 gates as explicit `UNVERIFIED`/`NOT_RUN`; local success is engineering evidence only and is not release acceptance.

### Changes and decisions

- Replaced the unfixed `python-jose`/`ecdsa` chain with `PyJWT==2.13.0`, added the real release-artifact requirement `cryptography==49.0.0`, raised FastAPI/Starlette/Pillow to `0.139.0`/`1.3.1`/`12.3.0`, regenerated exact Linux and Windows hash locks, and added source/lock regression scans. JWT session secrets now fail closed below 32 bytes.
- Updated FastAPI route-contract introspection for the framework's lazy router inclusion without weakening the actual OpenAPI or HTTP contract. Regenerated the committed OpenAPI snapshot. Replaced the remaining Pillow service-layer `getdata()` calls with `get_flattened_data()` and added a deprecation regression scan.
- Upgraded the isolated release-tool Vercel CLI to `56.2.0`, added exact security overrides for its actionable transitive findings, regenerated its lock, and synchronized workflow/runbook/contract assertions.
- Added semantic `main`, `navigation`, and level-one heading contracts to Home, Login, Registration, Privacy, Refunds, and Terms. Added a keyboard skip link, descriptive image alternatives, and Playwright assertions so an axe-only pass cannot hide an empty heading/landmark tree.
- Pinned the Worker base to `python:3.11.15-slim-bookworm@sha256:721dc13fd1be0a771e54b72097634291d628d0007dee9da777e2ce676a9c998f`. The Dockerfile now normalizes Debian's default source to official HTTPS before installing system libraries and still installs the Linux lock with `--require-hashes` as non-root UID/GID `10001`.
- The real identity downgrade integration exposed two stale assertions that assumed revision `20260710_0014` was still head. The tests now capture the starting revision and prove a rejected multi-revision downgrade leaves that exact revision unchanged; production migration behavior was not altered.
- Added a frontend security contract that keeps Vite development/preview servers on `127.0.0.1` and rejects raw HTML/code sinks (`v-html`, `innerHTML`, `outerHTML`, `document.write`, `eval`, and `new Function`) in active source.

### Dependency and lock evidence

- Linux backend lock two-pass SHA-256: `a9becc1e855217afb949e1de743b9208ca1bebd84530b735781bd735d2548790`. Windows backend lock two-pass SHA-256: `70813a2074e28d4ff792a3e9502dc280c46d3626920fdab23e5892ab219325ce`. Both regenerations were byte-identical; a fresh Windows `--require-hashes` install and `pip check` passed.
- OSV-Scanner `v2.3.8` was checksum-verified against its release metadata and scanned the root/backend Linux/backend Windows/resolver locks plus frontend and release-tool npm locks. It found no Python or actionable release-tool advisory after the changes.
- Remaining frontend matches are upstream DCloud constraints: `@babel/core@7.25.2` and `vite@5.2.8`. The current DCloud release still pins those exact versions. Babel's advisory excludes trusted-source compilation; this repository compiles reviewed local source only. Vite is a build/development dependency, both local servers are loopback-only, and the static output does not ship a Vite server. Forced Babel/Vite overrides were rejected after they produced an invalid peer tree. Two `sandbox@3.4.3` matches are package-name-revival false positives because the advisory affected only the historical `<1.0.0` package.

### Verification

- Final repository baseline: `powershell.exe -ExecutionPolicy Bypass -File scripts/release/verify_baseline.ps1` exited 0, created a fresh hash-locked Windows environment, reported `pip check` PASS, collected 729 backend tests with zero failures and 33 explicit external/environment skips, and passed frontend typecheck, 5/5 unit tests, and Web build. The report remained truthful: `UNCOMMITTED_WORKTREE`, `release_eligible=false`, and Windows/Python 3.11.9/Node 24.2.0 versus the protected Linux/Python 3.11.15/Node 24.17.0 runtime.
- Independent full backend discovery also passed 729 tests with 33 skips. The expected fail-closed error-path logs remained visible and did not hide failures.
- Real disposable PostgreSQL/Redis verification passed: Worker crash recovery 1/1; control-plane RLS 4/4; Partner Invite RLS 3/3; click-stats repair 4/4. The first identity run passed 13/15 and exposed the two stale revision assertions; the targeted corrected pair passed 2/2, then a clean full rerun inside the pinned Python 3.11.15 Linux Worker image passed 15/15 against PostgreSQL 15 after migration through `20260710_0020`.
- The final uniquely tagged Worker build produced local image ID `sha256:c318eafafeff61f0f21580dd2ac192993daf1f155aa567fb8eb14a0bb0ceda3d`. History readback included the HTTPS source normalization; inspection showed user `vowpic` and command `["python","scripts/worker_entrypoint.py"]`; runtime UID/GID was `10001`; `pip check` passed; and imports reported cryptography `49.0.0`, FastAPI `0.139.0`, PyJWT `2.13.0`, Pillow `12.3.0`, and Starlette `1.3.1`.
- Frontend final checks passed: typecheck, Vitest 5/5, Web build, and built-site accessibility 12/12 across system Chrome and Playwright Firefox for the six public routes. An independent CDP inspection also confirmed Home has one main landmark, one navigation landmark, and one level-one heading.
- `git diff --check` passed with only the three previously recorded LF-to-CRLF advisory lines.

### Cleanup and external boundary

- A canceled local BuildKit client was observed continuing server-side and later overwriting a reused local validation tag. No remote registry or deployment was involved. All BuildKit history entries were confirmed completed/error, the overwritten tag was removed, and the current Dockerfile was rebuilt under a fresh unique tag before final inspection. The final validation tag and every disposable PostgreSQL/Redis container were removed.
- The task-owned CDP Chrome profile/process tree was stopped. No user Chrome session was closed.
- `CREEM_REFUND_CREATION`, `CREEM_SUBSCRIPTION_PAID_TRANSACTION`, `CREEM_SUBSCRIPTION_PERIOD_END_CANCELLATION`, and `EVOLINK_SUBMISSION_RECONCILIATION` remain `UNVERIFIED`. Protected Preview environments/secrets, real Provider/payment/storage evidence, Worker-host approval, formal-domain acceptance, and human quality approval remain external `NOT_RUN`. Generation remains OFF and Stage 6 remains blocked.
- No Subagent was used. No commit, push, PR mutation, merge, deployment, domain/DNS mutation, Production data write, payment, email, Provider submission, or customer-data operation occurred.

## 2026-07-15 - Frontend dependency and public-accessibility residual closure

### Goal and scope

- Remove locally actionable frontend dependency advisories, extend the built Web SaaS accessibility gate to every public legal/auth route, and reduce exposure from the upstream-pinned Vite development server.
- Recheck the protected Preview and Provider contract boundaries without creating environments, adding secrets, triggering workflows, deploying, changing the domain, or enabling Generation.

### Baseline and evidence

- The first complete lockfile OSV pass found 29 package/advisory matches across the build, test, and runtime tree, including the old Vitest 1.6.1 critical range and high-severity Babel SystemJS, glob, immutable, path-to-regexp, picomatch, and Rollup ranges.
- The existing accessibility list omitted Registration and Refunds. Adding both routes reproduced a serious `scrollable-region-focusable` violation on the built Refunds page.
- The first complete baseline after the upgrade correctly failed one CI-contract test because it still required vue-tsc 1.8.27. The contract and CI top-level dependency probe were updated to the evidence-backed versions before the final baseline rerun.
- `npm audit --omit=dev --audit-level=high` remains NOT_RUN: the configured mirror returns `404 [NOT_IMPLEMENTED]` for the audit endpoint, while the official registry path resolves through the local proxy to a false/fake-IP route. Direct OSV query-batch access was available and was used as independent lockfile evidence, not mislabeled as npm audit.

### Changes

- Upgraded Vitest from 1.6.1 to 3.2.6 and vue-tsc from 1.8.27 to 2.2.12. Added exact, lockfile-backed overrides for the other actionable vulnerable transitive packages and regenerated the install lock.
- Synchronized the exact frontend-tool CI contract and workflow dependency probe with those upgrades, and made the override set plus loopback-only development command regression-protected.
- Two attempted Babel-core overrides were rejected after `npm ls --all` proved peer-tree invalidity; both were fully removed. The final dependency tree has no invalid, extraneous, or missing required dependency.
- Added Registration and Refunds to the six-route accessibility contract. The Refunds page now provides a labeled, keyboard-focusable main region, matching the Privacy and Terms legal-shell contract.
- Bound `dev:web` explicitly to `127.0.0.1`; `preview:web` was already loopback-only. A real dev-server start reported only `http://127.0.0.1:3000/`, and the listener was absent after controlled shutdown.

### Verification

- `npm ci --no-audit --registry=https://registry.npmmirror.com`: passed; 654 packages installed. `npm ls --all --json`: exit 0 with no dependency problems.
- `npm run typecheck`: passed. `npm run test:unit`: Vitest 3.2.6 passed 5/5 tests. `npm run build:web`: passed.
- Final `scripts/release/verify_baseline.ps1` exited 0: the hash-locked Windows backend install and `pip check` passed; all 725 collected backend tests passed with 33 environment/external cases explicitly skipped; frontend typecheck, unit, and build all reported PASS. Its report truthfully retained `UNCOMMITTED_WORKTREE`, runtime mismatch, and `release_eligible=false`.
- System Chrome executed the final built Web SaaS axe gate for Home, Login, Registration, Privacy, Refunds, and Terms: 6/6 passed with no serious or critical violations.
- Final direct OSV lockfile scan covered 659 unique package/version pairs. All previously actionable critical/high findings were removed. The only remaining matches are the DCloud-exact `@babel/core@7.25.2` advisory and 15 `vite@5.2.8` advisories. The current DCloud Vue 3 release tag still pins those exact versions; forcing Babel 7.29.6 breaks npm's peer-tree validity, and forcing Vite 6 would violate DCloud's exact `vite: 5.2.8` peer contract. These build/development tools are not shipped in the generated static Web assets, and their local servers are loopback-bound.
- Removed Playwright's `.last-run.json` result residue. The temporary OSV download directory was absent, no temporary Proxifier npm rule remained, the existing Docker rule remained present, and Proxifier screen logging was restored to Normal.

### External boundary and residual gates

- GitHub read-only recheck found only `Preview`, `production`, and `fantastic-blessing / production`; required `preview-identity` and `preview-commercial` environments and their protected secrets are absent. No protected Stage 5 workflow was triggered.
- EvoLink's current public contract returns a provider task ID only in the submission response and supports status lookup by that ID. No accepted idempotency header or queryable client-correlation recovery contract was found, so lost-submit-response reconciliation remains unverified. Generation remains OFF and Stage 6 remains blocked.
- Firefox remains NOT_RUN locally because the Playwright Firefox engine is not installed; the final system-Chrome six-route gate passed. Protected Preview browser journeys remain NOT_RUN because their environments and credentials do not exist.
- No Subagent was used. No commit, push, PR mutation, merge, deployment, domain/DNS mutation, Production data write, payment, email, Provider submission, or customer-data operation occurred.

### Superseding final verification note

- The later cross-runtime closure entry above supersedes this entry's local Firefox and 725-test counts: the exact Playwright Firefox engine was subsequently installed and the six-route Chrome/Firefox gate passed 12/12; the final collected backend count is 729 with 33 explicit skips and zero failures.
- External protected Preview journeys are still `NOT_RUN`; installing a local browser engine does not supply Preview identities, environments, secrets, or release evidence.
- Final cleanup removed the task-owned CDP profile/screenshot, security/resolver virtual environments, Vercel candidate directories, checksum-verified OSV scanner/report directory, Playwright result traces, every disposable database/Redis container, and both local Worker validation tags. Readback found no matching file, container, image tag, or running BuildKit residue.

## 2026-07-15 - Support-baseline synchronization and generated-contract repair

### Goal and scope

- Re-review the accumulated overseas Web SaaS implementation, run the complete local engineering baseline, and synchronize only after the source was committed and locally verified.
- Diagnose the first PR checks without weakening tests or inventing missing Provider, payment, storage, Worker-host, Preview, or Production credentials.

### Changes and decisions

- Committed and pushed the reviewed support baseline as `50563ab3753704288e0222f14775c66b69cead27` on `codex/vowpic-stage1-safe-baseline`; the existing draft PR remains the review boundary and was not merged.
- Created the missing GitHub `preview-identity` and `preview-commercial` environments with the repository owner as required reviewer and a custom deployment-branch policy restricted to `main`. No environment or repository secret was created, copied, printed, or inferred.
- The first PR run passed Linux/Windows lock reproduction and the Worker image build. It exposed two independent failures: the committed frontend API type file was stale, while the backend aggregate suite failed only in GitHub's Linux job after its OpenAPI, migration, and four PostgreSQL contract steps had already passed.
- Regenerated `frontend/src/generated/api.d.ts`; two consecutive OpenAPI type generations were byte-identical and added the current FastAPI `ValidationError.ctx` and `ValidationError.input` fields.
- Extended `verify_baseline.ps1` to generate the frontend API types twice, require identical hashes, and reject a generated-file diff before typecheck. Added a CI contract assertion for that local guard so this local/PR verification gap cannot silently return.
- The existing local environment's stale Pillow/FastAPI failures were rejected as dependency-drift evidence. No backend production code or test expectation was changed from that invalid environment.
- The second PR run proved the frontend fix: deterministic generated types, typecheck, Vitest, Web build, and the real browser accessibility step all passed. Linux/Windows locks and the Worker image also passed. Downloading the exact backend log then identified two real failures rather than a transient job error.
- Replaced the runtime-bundle CLI test's Windows-only `.venv/Scripts/python.exe` path with `sys.executable`, binding the subprocess to the already verified test interpreter on Windows and Linux.
- The public catalog route already mapped catalog-domain, SQLAlchemy, and operating-system failures to its documented 503 response, but a raw asyncpg `PostgresError` escaped as 500. Added that driver boundary explicitly, retained a safe exception-type-only warning, and added a regression for the exact `InvalidCatalogNameError` observed in GitHub.
- A focused `backend`-directory run then exposed two risk-lockdown tests that imported `scripts.release` only after another test happened to add the repository root to `sys.path`. The module now establishes its own repository-root import boundary, removing that test-order dependency without changing application behavior.
- Added only `*.actions.githubusercontent.com` to the existing Proxifier development-platform target list after writing `静态.before-actions-log-routing.20260715-1215.ppx`; this enabled authenticated GitHub Actions log retrieval. The existing Docker target already contained `registry-1.docker.io`, so it was not changed.

### Verification

- Focused baseline-contract test passed 1/1. Two consecutive `npm run openapi:generate` executions produced identical bytes; frontend typecheck and Vitest 5/5 passed.
- The complete updated local baseline exited 0 from a newly created hash-locked Windows environment: `pip check` passed, all 729 backend tests passed with 33 explicit external/environment skips, API type generation was deterministic and matched the staged file, frontend typecheck passed, Vitest passed 5/5, and the Web build completed.
- The report truthfully recorded `UNCOMMITTED_WORKTREE`, `release_eligible=false`, and `runtime_alignment=NOT_RUN` because the local Windows/Python 3.11.9/Node 24.2.0 runtime is not the protected Linux/Python 3.11.15/Node 24.17.0 release runtime.
- After commit `24959d21e6438a4bab2647fad04cd87a0de01f63`, the same complete baseline reran and reported `CLEAN_COMMIT` with the exact same passing counts and checks before the commit was pushed.
- The four focused CI-failure reproductions passed from the same `backend` working directory used by GitHub: the runtime-bundle subprocess, raw asyncpg catalog unavailability, existing catalog fail-closed case, and retired-route HTTP contract.
- The first expanded three-module run exposed the two order-dependent imports described above; after the isolation fix, all 43 runtime-bundle, billing-catalog, and risk-lockdown tests passed from the CI working directory.
- `git diff --check` passed. No temporary test environment or generated Playwright result was retained.

### External boundary and remaining evidence

- The backend fixes still require a fresh GitHub PR run. Until that run completes, the Linux aggregate remains unresolved rather than relabeled locally passed.
- GitHub repository/environment secret-name readback was empty. Protected Preview, Vercel authenticated deployment inspection, Provider/payment/storage contracts, Worker-host execution, formal-domain acceptance, and human quality acceptance remain `NOT_RUN`/`UNVERIFIED`.
- Generation and billing remain OFF. No merge, protected Preview workflow, Production workflow, Production data write, payment, email, Provider submission, DNS, or formal-domain mutation occurred.
- No Subagent was used.

## 2026-07-15 - Vercel Python runtime lock alignment

### Goal and scope

- Diagnose the first remaining red Vercel Preview after the exact GitHub PR quality gate passed, without using an authenticated browser, changing the formal domain, or enabling high-risk capabilities.
- Close any repository-owned deployment gap before another ordered commit and Preview retry.

### Evidence and root cause

- GitHub deployment history showed successful Preview deployments through source `166dddd8751bb75e8dffe81fe51f7f13020469d6`, followed by three consecutive failures beginning at `50563ab3753704288e0222f14775c66b69cead27`. The latest failed deployment was `dpl_6naRoJeUCye1zpUxJnr5MC8LGyDf` for source `217b5dfb2867d890d4ab10f4f4db20c04a2fad0a`.
- The unauthenticated Vercel page exposed no build log, and the locked CLI correctly refused inspection without credentials. No failure reason was inferred from that page.
- Official Vercel runtime documentation identifies Python 3.12 as the default when the project does not declare a supported version. The repository had no root Python version declaration, while CI regenerated and installed the Vercel API lock only in Python 3.11.15.
- An isolated Python 3.12.13 Bookworm install reproduced the concrete failure: `vercel==0.6.0` declares `vercel-workers<1,>=0.0.16` only for Python 3.12 and later, but the Python 3.11-generated hash lock omitted it. Pip therefore rejected the unpinned and unhashed transitive requirement before application import.

### Changes

- Added a root `.python-version` declaring Python 3.12 and regenerated the root Vercel API hash lock in exact Python 3.12.13; the lock now pins and hashes `vercel-workers==0.0.25`.
- Kept the existing Python 3.11.15 Linux backend/Worker lock job and added a separate digest-pinned Python 3.12.13 Bookworm Vercel lock job. Each graph is regenerated twice, compared with the committed output, installed with hashes, and dependency-checked in its owning runtime.
- The Vercel job now imports the real `api.index` entry point after a fresh locked install. The aggregate PR gate requires the Linux backend lock, Vercel API lock, and Windows backend lock jobs together.
- Added CI contract regressions for the runtime declaration, exact image, conditional dependency, double lock generation, clean install, entry-point import, and aggregate-gate ownership. Updated the Vercel deployment guide with the split-runtime contract.

### Verification

- The new CI contract failed first because the Vercel 3.12 job and `.python-version` did not exist; after implementation, `python -m unittest backend.tests.test_ci_release_contract -q` passed 60/60 tests.
- The committed-candidate root lock regenerated twice byte-identically under Python 3.12.13 with SHA-256 `68599f2f8b0eef13b3082055e1bef6478c8d0b734eb7df9c9a46a6394291001a`.
- A fresh Python 3.12.13 Bookworm virtual environment installed all root requirements with `--require-hashes`, `pip check` reported no broken requirements, and the real Vercel function entry point imported successfully as `api.index.handler is api.index.app`.
- `.github/workflows/ci.yml` parsed successfully and `git diff --check` passed before the full repository baseline.
- The complete local baseline then exited 0 from a fresh Windows hash-locked environment: `pip check` passed, all 732 collected backend tests passed with 33 explicit external/environment skips, generated API types were deterministic and matched the committed file, frontend typecheck passed, Vitest passed 5/5, and the Web build completed. The report truthfully recorded `UNCOMMITTED_WORKTREE`, local runtime mismatch, and `release_eligible=false`.

### External boundary

- The failed Vercel deployment is not reclassified as passed. A new GitHub/Vercel Preview run against the corrected committed source is still required.
- Protected Preview identity/commercial workflows, Provider/payment/private-storage proof, Worker-host execution, formal-domain acceptance, and human quality acceptance remain external `NOT_RUN`/`UNVERIFIED` gates. Generation and billing remain OFF.
- No Subagent, authenticated Vercel session, merge, Production workflow, domain/DNS mutation, Production data write, payment, email, or Provider submission was used.

## 2026-07-15 - Vercel upload-context repair and GitHub Actions Node 24 migration

### Goal and scope

- Use the explicitly authorized logged-in Chrome session only to read the failed Vercel Preview build log for source `a902dd429d47f9a61a5efc9d35597d600f5793e5`.
- Repair the repository-owned deployment failure and remove all GitHub Actions Node 20 deprecation warnings without changing Vercel project settings, environment variables, domains, Production, or high-risk capability flags.

### Evidence and root cause

- Vercel deployment `dpl_HVBgqCwk9vtK1LRwJ5zNoGxMsu2x` reached the frontend build and failed after 39 seconds. Its authenticated deploy log reported `Error: Cannot find module '/vercel/path0/frontend/scripts/clean-web-output.mjs'` while running `cd frontend && npm run build:web` under Node 24.15.0.
- The exact source commit contains `frontend/scripts/clean-web-output.mjs`, and local/CI builds use it successfully. The root `.vercelignore` nevertheless contained the unanchored pattern `scripts`; Vercel documents `.vercelignore` as gitignore-like, so that pattern excluded the nested `frontend/scripts` directory from the deployment upload context.
- GitHub run `29434740863` passed every job but its annotations identified Node 20 action runtimes for `actions/checkout`, `actions/setup-python`, `actions/setup-node`, and `actions/upload-artifact`. The protected workflows also referenced the Node 20 `actions/download-artifact` release.
- Exact official release tags were resolved to immutable commit SHAs. The checked-in `action.yml` at each selected SHA was read from the official `actions/*` repository and declared `runs.using: node24`.

### Changes

- Anchored the repository-level Vercel exclusion as `/scripts`, retaining the intended root tooling exclusion while allowing the required `frontend/scripts/clean-web-output.mjs` into the deployment context.
- Updated all first-party JavaScript actions in `ci.yml`, `integration.yml`, `safe-baseline-release.yml`, and `production-release.yml` to immutable Node 24 release commits: checkout 6.0.3, setup-python 6.3.0, setup-node 6.5.0, upload-artifact 7.0.1, and download-artifact 8.0.1.
- Added one closed action-pin contract covering every `uses: actions/*` line and one Vercel upload-context contract that rejects a future unanchored `scripts` pattern while requiring the frontend cleanup script.
- Updated the Vercel deployment guide with the nested build-script upload requirement. No runtime application code, capability flag, domain, environment variable, secret, or Vercel project setting changed.

### Verification

- Before modification, the two affected test modules passed 72/72 tests in the repository `.venv`. An earlier invocation with the unprovisioned global Python was invalid and failed during import because `httpx` and `asyncpg` were absent; no code conclusion was drawn from it.
- The two new regression tests failed first against the unanchored Vercel ignore rule and Node 20 action pins, then passed after the implementation. All four workflow files parsed successfully, and the focused CI/deployment plus Web-security run passed 74/74 tests.
- A direct local call into the installed `@vercel/build-utils` ignore helper was unusable because that published package path could not resolve its external `ignore` module, including after a clean pinned release-tool install. This check is not counted as PASS and was not used to override the real Vercel log, committed source tree, official Vercel rule, or regression contract.
- `git diff --check` passed. The complete local baseline then exited 0 from a fresh hash-locked Windows environment: `pip check` passed; all 734 backend tests passed with 33 explicit external/environment skips; generated API types were deterministic; frontend typecheck passed; Vitest passed 5/5; and the real Web build completed through `frontend/scripts/clean-web-output.mjs`.
- The baseline correctly recorded `UNCOMMITTED_WORKTREE`, `runtime_alignment=NOT_RUN`, and `release_eligible=false` because local Windows/Python 3.11.9/Node 24.2.0 is not the protected Linux/Python 3.11.15/Node 24.17.0 runtime.

### External boundary

- A clean-commit baseline, fresh GitHub CI run, warning-annotation readback, and fresh Vercel Preview deployment are still required before this repair can be called externally verified.
- Protected Preview identity/commercial, EvoLink lost-response proof, Creem refund/subscription proof, private storage, Worker-host execution, formal-domain acceptance, and human quality acceptance remain `NOT_RUN`/`UNVERIFIED`. Generation and billing remain OFF.
- No Subagent, merge, protected workflow, Production deployment, domain/DNS mutation, environment-variable mutation, secret read/write, Production data write, payment, email, or Provider submission occurred.

## 2026-07-15 - Fail-closed frontend capability surfaces

### Goal and evidence

- Recheck the successful Vercel Preview as a real user-facing safe-baseline site after the repository-owned build and GitHub Actions warnings were closed.
- The deployed backend correctly returned `503 runtime_not_ready` and defaulted every high-risk capability OFF, but the frontend still rendered active-looking Google sign-in, upload, generation, billing unlock, and repeat-generation entry points. This contradicted the authoritative requirement that a public-config failure resolves every high-risk frontend surface hidden or OFF.
- The old `test_frontend_fallback_hides_every_high_risk_surface` only rejected three obsolete enabled strings and did not inspect any real page or navigation entry point. A replacement regression failed first because the shared capability getters and page guards did not exist.

### Changes

- Added centralized fail-closed getters for Google auth, creation, billing, private download, and Partner Invite to the existing operations store. The existing default and normalization behavior remains all-OFF unless the sanitized public config explicitly enables a capability.
- Gated the desktop/mobile navigation, homepage CTAs, style details, direct Studio route, login/register, account and order empty states, Preview billing/download/recreate controls, and their event handlers. The direct Studio route now renders a browse-only safe-baseline explanation instead of mounting upload or generation controls.
- Added client-side early returns before the browser image picker, generation submission, OAuth initiation, payment modal, private download, and create-route navigation. Server-side authorization remains independent and unchanged.
- Expanded the risk-lockdown regression across the real navigation, home, detail, create, auth, account, orders, and Preview source files. No high-risk capability was enabled and no backend, domain, environment, or Vercel project setting changed.

### Verification

- The new frontend-surface regression failed before implementation on the missing `creationAvailable` contract, then passed after the changes.
- Focused risk and Web-only regression passed 51/51; frontend typecheck passed; the capability-store and HTTP Vitest suites passed 8/8; and the real Web build completed. The new capability-store suite first exposed an invalid hoisted mock declaration in its own test setup; after switching to Vitest's hoisted factory, all three real fallback assertions passed.
- The complete local baseline exited 0 from a fresh hash-locked Windows environment: `pip check` passed, all 734 backend tests passed with 33 explicit external/environment skips, generated API types were deterministic, frontend typecheck passed, Vitest passed 8/8, and the Web build completed.
- The baseline truthfully recorded `UNCOMMITTED_WORKTREE`, `runtime_alignment=NOT_RUN`, and `release_eligible=false` because local Windows/Python 3.11.9/Node 24.2.0 differs from the protected Linux/Python 3.11.15/Node 24.17.0 runtime.

### External boundary

- A clean-commit baseline, GitHub CI, fresh Vercel Preview, and read-only browser verification of the updated controls are still required before this UI closure is externally verified.
- Protected Preview identity/commercial, Provider/payment/private-storage evidence, Worker-host execution, formal-domain acceptance, and human quality acceptance remain `NOT_RUN`/`UNVERIFIED`. Generation, authenticated upload, billing, private download, Partner Invite, and Google auth remain OFF.
- No Subagent, merge, protected workflow, Production deployment, domain/DNS mutation, environment-variable mutation, secret read/write, Production data write, payment, upload, email, or Provider submission occurred.

## 2026-07-15 - Frontend dependency audit and build-tool advisory boundary

### Goal and evidence

- Read the successful Vercel Preview deploy log after the fail-closed UI commit and investigate every reported dependency warning instead of treating a Ready deployment as risk-free.
- The authenticated log for deployment `dpl_DTiBZRGgNKYfVBPvJ14jcXy2Z5AW` at source `90882ba4b2446af4462125f149f7beb95231453d` reported 12 audit findings: 10 low, 1 moderate, and 1 high. A direct advisory audit reproduced the exact graph.
- The ten low findings all originated from DCloud's exact nested `@babel/core@7.25.2`. The remaining two audit nodes originate from DCloud's exact Vite 5.2.8 peer contract. The high Vite advisory requires a Windows development server exposed to the network; VowPic's enforced development and Preview scripts bind only to `127.0.0.1`, while Vercel serves the generated static Web output and does not deploy the Vite server. The bundled-script DOM-clobbering advisory applies to CJS/IIFE/UMD output containing a `document.currentScript` gadget; the generated VowPic output did not contain that gadget.
- Vite 5.4.21 was tested as a same-major candidate and rejected: it still matched the newly published Vite advisories and contradicted DCloud's exact 5.2.8 peer contract. The latest published DCloud alpha checked during this review still declares that exact peer, so no unsupported Vite override or framework migration was retained.

### Changes

- Overrode DCloud's nested Babel compiler to the patched `@babel/core@7.29.6`. The lock now deduplicates both vulnerable 7.25.2 copies to 7.29.6 without an invalid peer or dependency tree.
- Added a CI gate that runs `npm audit --omit=dev --audit-level=low`; every deployable production dependency must have zero audit findings.
- Extended the existing post-bundle Web asset policy to fail the build if any emitted browser asset contains `document.currentScript`, preventing the affected Vite non-ES gadget from silently entering a release artifact.
- Added a security contract covering the dev-only Vite boundary, patched Babel override, bundle gadget rejection, and production audit gate. The existing loopback-only server and raw-HTML-sink regression remains in force.

### Verification

- The new security contract failed first because the output-pattern guard and production audit gate did not exist, then passed after implementation.
- A clean locked frontend install completed and `npm ls @babel/core vite --all` exited 0: every Babel node resolves to 7.29.6 and the DCloud/Vite 5.2.8 peer tree remains valid.
- `npm audit --omit=dev --audit-level=low` reported `found 0 vulnerabilities`; frontend typecheck passed, Vitest passed 8/8, and the real Web build completed while enforcing the new bundle policy.
- The first complete baseline correctly failed one exact dependency-contract test because its closed override allowlist did not yet include the new Babel security override. After synchronizing that contract, a new isolated baseline passed `pip check`, all 735 backend tests with 33 explicit external/environment skips, deterministic generated API types, frontend typecheck, Vitest 8/8, and the real Web build. Its report truthfully recorded `UNCOMMITTED_WORKTREE`, local runtime mismatch, and `release_eligible=false`.
- The full development graph still reports the two DCloud/Vite build-tool nodes. They are not relabeled as fixed or production vulnerabilities: they remain an upstream exact-peer constraint with enforced exploit-precondition boundaries until DCloud publishes a supported patched Vite line.

### External boundary

- A clean-commit baseline, fresh GitHub CI audit gate, and fresh Vercel Preview build are required before this dependency closure is externally verified.
- Protected Preview identity/commercial, Provider/payment/private-storage evidence, Worker-host execution, formal-domain acceptance, and human quality acceptance remain `NOT_RUN`/`UNVERIFIED`. Generation, authenticated upload, billing, private download, Partner Invite, and Google auth remain OFF.
- No Subagent, merge, protected workflow, Production deployment, domain/DNS mutation, environment-variable mutation, secret read/write, Production data write, payment, upload, email, or Provider submission occurred.

## 2026-07-16 - Protected Preview runtime-role wiring closure

### Goal and evidence

- Re-audit the already-created Vercel, GitHub environment, Supabase-facing, Redis, private-storage, and EvoLink Preview path before any protected execution; repair repository-owned wiring defects without exposing or copying secret plaintext.
- The latest automatic Vercel Preview was build-ready but `/health/ready` returned `503` because the automatic deployment lacked a canonical runtime bundle, approved Preview role, acceptance HMAC, and control-plane database URL. GitHub readback confirmed the protected `preview-identity` and `preview-commercial` environments existed and were restricted to `main`, but each contained zero secrets and zero variables; no protected Preview workflow had run.
- Repository inspection found a separate defect in the protected workflow: `PREVIEW_MIGRATION_DATABASE_URL` was injected as both application `DATABASE_URL` and `CONTROL_PLANE_DATABASE_URL`, contradicting runtime configuration and the control-plane role contract that requires distinct logins on one database. Identity deployment also omitted both role URLs and the acceptance HMAC.

### Changes

- Added fail-fast protected inputs for `PREVIEW_RUNTIME_DATABASE_URL` and `PREVIEW_CONTROL_PLANE_DATABASE_URL`; identity API, commercial API, Worker, and Provider proof now receive only those two application-role URLs. `PREVIEW_MIGRATION_DATABASE_URL` remains available solely to explicit workflow administration and migration commands.
- Identity deployment now receives the acceptance HMAC. Commercial preflight now requires the private-storage endpoint and all EvoLink/provider-evidence inputs before any deployment or Provider effect, rather than discovering missing configuration after mutation starts.
- Added a closed workflow regression covering identity, commercial API, Worker, Provider-proof, and preflight blocks, including rejection of migration-credential reuse. Updated the operational runbook with the three-role Preview secret contract and the separate read-only resolver input.

### Verification

- The pre-change workflow regression failed on eight missing or unsafe bindings, then passed after the repair. The affected runtime/workflow/release suites passed 117/117 tests; the workflow parsed successfully as YAML; and `git diff --check` passed.
- `powershell.exe -ExecutionPolicy Bypass -File scripts/release/verify_baseline.ps1` exited 0 from a fresh hash-locked environment: `pip check` passed; all 736 backend tests passed with 33 explicit external/environment skips; generated API types were deterministic; frontend typecheck passed; Vitest passed 8/8; and the real Web build completed.
- The baseline truthfully recorded `UNCOMMITTED_WORKTREE`, `release_eligible=false`, and `runtime_alignment=NOT_RUN` because local Windows/Python 3.11.9/Node 24.2.0 differs from the protected Linux/Python 3.11.15/Node 24.17.0 release runtime.

### External boundary

- No secret plaintext was read, printed, persisted, or copied between Vercel and GitHub. The protected Preview workflows remain `NOT_RUN` until their existing environment entries receive the required protected inputs; the automatic browse-only Preview is not substituted for protected Stage 5 acceptance.
- Supabase dashboard project/bucket state was not authenticated in the inspected Chrome profile, so no exact project or bucket claim is made from that session. EvoLink lost-submit-response reconciliation remains `UNVERIFIED`; no Provider request was sent and Generation remains OFF.
- No Subagent, protected workflow, Production workflow, domain/DNS mutation, Production data write, payment, email, Provider submission, or customer-data operation occurred in this change.

## 2026-07-16 - Retired launcher and historical architecture cleanup

### Goal and evidence

- Continue the post-merge risk audit beyond external credentials and remove active-tree artifacts that still contradicted the overseas Web SaaS contract.
- `start_dev.bat` had no caller, invoked the removed `dev:h5` script, and labelled the Worker as a retired ComfyUI queue. `DOCUMENTATION_STUDIO_3_0.md` was an unreadable historical archive that still described Mini Program and ComfyUI product paths; the authoritative design already marked it non-authoritative and directed readers to Git history.

### Changes

- Deleted both obsolete artifacts instead of preserving broken compatibility launchers or misleading product documentation.
- Extended the closed Web-only deletion contract so either file reappearing fails CI.

### Verification

- Before modification, the current Web-only and release-contract suites passed 30/30 tests in the repository environment. The first command used an unprovisioned global Python and referenced one already-deleted test module; its two import errors were treated as an invalid test environment, not a product result.
- After deletion, the same 30 current tests passed; the wider Web-only, CI, security, release, and protected-Preview contract run passed 137/137; and `git diff --check` passed.
- The complete fresh hash-locked baseline exited 0: `pip check` passed, all 736 backend tests passed with 33 explicit external/environment skips, generated API types were deterministic, frontend typecheck passed, Vitest passed 8/8, and the real Web build completed. It correctly recorded `UNCOMMITTED_WORKTREE`, `runtime_alignment=NOT_RUN`, and `release_eligible=false` for the local Windows/runtime mismatch.
- The newly advertised DCloud 5.21 package line was checked rather than blindly upgraded. Its current `@dcloudio/vite-plugin-uni@3.0.0-alpha-5020120260710001` still requires exact Vite `5.2.8`, so it does not resolve the already bounded upstream Vite advisory. A local npm-audit refresh through the configured mirror was unavailable because that mirror does not implement the advisory endpoint; an explicit npmjs retry was blocked by the current proxy route. Neither failed lookup is counted as a security PASS, and dependencies were unchanged.

### External boundary

- GitHub environment readback still shows only the generated approval/HMAC/project coordinates and EvoLink public variables; no user-managed runtime, storage, Google, Redis, Vercel, Supabase, or Provider secret was added.
- EvoLink's current official documentation exposes only task lookup by known task ID and contains no documented idempotency key, client request ID, correlation field, metadata field, or task-list endpoint. The lost-response contract therefore remains `UNVERIFIED`, the sandbox proof remains `NOT_RUN`, and Generation remains OFF.
- No Subagent, protected workflow, Production workflow, domain/DNS mutation, secret read/write, database mutation, payment, email, Provider submission, or customer-data operation occurred.

## 2026-07-16 - Residual documentation, provider tooling, and asset cleanup

### Goal and evidence

- Continue the post-merge audit against the authoritative 2026-07-10 commercial-closure plan and the overseas Web SaaS runtime, then remove active-tree material that could still restore retired product or Provider paths.
- Repository reference scans found eight misleading or superseded operational documents, nine unused cover/asset generation launchers, three additional placeholder/promo utilities, eleven unreferenced duplicate or placeholder static images, one stale ComfyUI change-impact path, and stale InstantID/ComfyUI descriptions in otherwise active files.
- The retained April planning and commercial-closure documents are required historical evidence by the current plan, so they were preserved and marked `Historical — not execution authority` instead of being presented as current instructions.

### Changes

- Added the current `ARCHITECTURE`, `SECURITY`, and `OPERATIONS_RUNBOOK` documents and reduced the documentation index to the verified Web SaaS authority layer. Updated README verification commands, Preview acceptance truth, Vercel verification, and the four-role Supabase/Preview database boundary.
- Deleted superseded Studio 3.0, ComfyUI, Liblib, deployment, implementation-plan, feature-inventory, and preflight documents. Deleted the unused multi-provider image generator, eight legacy generation wrappers, placeholder/setup utilities, the legacy promo script, and eleven static images with no tracked caller.
- Removed the nonexistent ComfyUI path from change-impact policy; replaced stale InstantID/ComfyUI descriptions with the current EvoLink/Web SaaS boundary; and replaced operational exception swallowing with sanitized logs or explicit bounded fallbacks.
- Added closed regressions that keep the deleted artifacts absent, require the current document layer and exact Preview role documentation, reject stale Provider comments/change-impact paths, reject silent operational swallowing, and require every remaining static asset to have a caller.
- Synchronized the deterministic OpenAPI snapshot and the exact runtime hash contract after the corresponding active source descriptions changed. No validation or release gate was weakened.

### Verification

- The new deletion/documentation regressions were run red before implementation and passed afterward. Focused affected suites passed 98/98, delivery/runtime/Web-only suites passed 72/72, and the final focused release/provider/Web-only set passed 131/131 after the exact runtime source hash was refreshed.
- The first isolated full baseline correctly failed 1 of 739 backend tests because the committed OpenAPI snapshot still contained the retired InstantID description. After updating that one deterministic snapshot field, the OpenAPI contract passed 5/5 and a new complete baseline exited 0.
- The final fresh hash-locked baseline passed `pip check`; all 739 backend tests completed with 706 passing and 33 explicit external/environment skips; generated API types were byte-deterministic; frontend typecheck passed; Vitest passed 8/8; and the real Web build completed. Current-document Markdown path validation reported zero missing local references, and every remaining tracked static image has a tracked caller.
- The baseline truthfully recorded `UNCOMMITTED_WORKTREE`, `runtime_alignment=NOT_RUN`, and `release_eligible=false` because local Windows/Python 3.11.9/Node 24.2.0 differs from the protected Linux/Python 3.11.15/Node 24.17.0 release runtime.

### External boundary

- Readback of the protected GitHub environments confirmed the generated approval/HMAC/project coordinates remain present. `preview-identity` still lacks 17 required secret inputs plus the Supabase project variable, and `preview-commercial` still lacks 13 required secret inputs. No plaintext secret or credential value was read, printed, persisted, copied, or changed.
- Protected Stage 5 identity/commercial execution remains `NOT_RUN`. EvoLink lost-response reconciliation and the three Creem commercial contracts remain `UNVERIFIED`; protected private-storage, Redis, database-role, Google-account, Worker-host, and formal-domain evidence has not been substituted with local tests. Generation and billing remain OFF, and Stage 6 remains `NOT_RUN`.
- The formal `www.vowpic.com` deployment observed before this change still served the older runtime shape; this cleanup does not claim a Production deployment or formal-domain acceptance.
- No Subagent, protected workflow, Production workflow, domain/DNS mutation, environment-variable mutation, secret read/write, database mutation, payment, email, Provider submission, or customer-data operation occurred.

## 2026-07-16 - Retired Admin generation-action cleanup

### Goal and evidence

- Remove active Admin UI controls that still called permanently retired generation endpoints and therefore could only produce a `410 Gone` response in the overseas Web SaaS.
- The central retired-route contract confirms `POST /api/v1/admin/generation_probe` and `POST /api/v1/admin/orders/{order_id}/regenerate` are side-effect-free compatibility tombstones, while the Admin overview and order detail pages still exposed their controls and client calls.

### Changes

- Removed the real-generation probe form, result gallery, state, request code, and probe-only styles from the Admin overview. Replaced its stale subtitle with the remaining verified operational scope.
- Removed the disabled regenerate control, response type, state, and request code from Admin order detail. Replaced the order-page subtitle that still promised a failed-generation restart with the remaining read-only inspection scope. The backend tombstones and response compatibility fields remain unchanged.
- Added a closed Web-only regression that prevents either retired endpoint, its client handler, or its stale product copy from returning to active Admin pages.

### Verification

- The new regression failed before implementation on all retired Admin markers and passed after cleanup.
- The Web-only and Admin management suites passed 31/31; frontend typecheck passed; Vitest passed 8/8; and the real Web build completed.
- Local browser rendering verified `/admin` and `/admin/orders` use the corrected operational copy, retain the normal Admin layout, and no longer advertise either retired generation action. The browser check found and drove removal of the stale order-page restart claim before final verification.
- The fresh hash-locked baseline exited 0: `pip check` passed; all 740 backend tests completed with 707 passing and 33 explicit external/environment skips; generated API types were byte-deterministic; frontend typecheck passed; Vitest passed 8/8; and the real Web build completed. The shared project virtual environment's Pillow 11.0.0 produced three unrelated errors before the locked run; the repository locks Pillow 12.3.0, and the isolated locked environment passed those tests.
- The baseline correctly recorded `UNCOMMITTED_WORKTREE`, `runtime_alignment=NOT_RUN`, and `release_eligible=false` because local Windows/Python 3.11.9/Node 24.2.0 differs from the protected Linux/Python 3.11.15/Node 24.17.0 release runtime.

### External boundary

- This change does not activate, call, or verify EvoLink, Creem, Supabase, Redis, Google, protected Preview, or Production. Existing external Stage 5/6 gates remain unchanged and are not represented as locally complete.
- No Subagent, protected workflow, Production workflow, domain/DNS mutation, environment-variable mutation, secret read/write, database mutation, payment, email, Provider submission, or customer-data operation occurred.

## 2026-07-16 - Post-merge Preview and external-state audit

### Goal and evidence

- Verify the merged Admin cleanup on GitHub, the latest Vercel Preview, the formal domain, and the protected GitHub environment inventories without reading or changing secret plaintext.
- PR #7 merged reviewed source `6391ed6863fed40342d5a75c089cd088aaacbc52` into `main` as `eb185a98b14fd48a7593d7862763b816226326a4`. All nine PR checks passed, and the independent post-merge `main` CI run `29493316104` completed successfully.
- Vercel Preview deployment `FJ5wDLg13zb1nL6xxkPLp5DNbimG` is READY for source `6391ed6`. Browser verification confirmed the current `VowPic` title and corrected Admin overview/order copy. Its `/health/ready` correctly remains not ready because the browse-only deployment lacks a canonical runtime bundle, approved Preview role, acceptance HMAC, and control-plane database URL.

### External readback

- Vercel Production remains deployment `8ryc7dh5XjocPnPjyw1Yq9ZkqGTA` from source `52208b66fda5ab1a327c3af7d3840eabe74016fd`, not the merged source. `vowpic.com` has a valid `307` redirect to `www.vowpic.com`, and `www.vowpic.com` remains the valid Production domain. The formal Admin order page still served the old failed-generation restart claim, proving Production has not been synchronized.
- The Vercel project still exposes a public Blob store, `webdev-inspiration-hub-blob`; it is not the private Supabase/S3-compatible acceptance store required by the current architecture. Existing project variables include legacy Production/Preview EvoLink and Blob settings, but they do not satisfy the protected Preview runtime-role contract.
- `preview-identity`, `preview-commercial`, and `production` are protected GitHub environments restricted to `main` with required review. Inventory comparison against the workflow source found `preview-identity` missing 17 of 21 required secrets plus `SUPABASE_PROJECT_REF`, `preview-commercial` missing 13 of 19 required secrets, and `production` missing all 22 required secrets. The identity, commercial, safe-baseline, and Production protected workflows have never run.
- EvoLink's current official quickstart returns a generated task ID and the task API can query only `GET /v1/tasks/{task_id}`. The official pages expose no idempotency key, client request ID, correlation field, or account task-list lookup. A submission whose response is lost therefore cannot be safely rediscovered from the published contract; `EVOLINK_SUBMISSION_RECONCILIATION` remains `UNVERIFIED`.

### Result and boundary

- The repository fix, PR, merge, and post-merge CI are complete. Automatic Preview proves the current Web artifact only; it is not Stage 5 evidence. Production remains intentionally unchanged because `vercel.json` disables automatic `main` deployment and the protected staged release prerequisites are absent.
- Protected Stage 5 identity/commercial, Stage 6, Creem Test Mode, Provider lost-response reconciliation, private storage, isolated Redis, production staged deployment, and formal-domain acceptance remain `NOT_RUN`/`UNVERIFIED`. Generation, authenticated upload, billing, private download, Partner Invite, and Google auth remain OFF.
- No secret value, browser session state, customer record, storage object, Provider request, payment, email, database, domain, Vercel setting, GitHub environment, or Production deployment was changed during this audit. No Subagent was used.

## 2026-07-16 - Formal-domain legacy exposure audit

### Goal and evidence

- Continue the Production read-only verification after confirming that the formal domain still served an older deployment, and distinguish a stale visual artifact from an active contract exposure.
- Safe GET requests only were sent to `https://www.vowpic.com`; no POST, PATCH, DELETE, authenticated customer request, payment, Provider call, or state-changing probe was used.
- `/api/v1/ops/config` returned HTTP 200 with legacy capability state including `remote_join=true`, `local_recommendations=true`, and `director_mode=true`. `/api/v1/ops/readiness` returned HTTP 200 with `commercial_ready=true` under the retired readiness model rather than the current release-bound evidence contract.
- `/api/v1/session/safe-baseline-probe/status` returned HTTP 200 instead of the required 410; `/api/v1/live_portrait/list`, `/api/v1/leads/list`, and `/api/v1/leads/export.csv` returned 401 instead of 410; and a synthetic nonexistent `/api/v1/users/{id}` request returned 404 instead of 410.
- `/api/v1/recommendations/local_studios` returned HTTP 200 with three public legacy local-vendor recommendation records. The current overseas Web SaaS excludes this product surface and requires the route to be a side-effect-free 410 tombstone.

### Repository result

- Updated the Production acceptance truth document to record the confirmed exposure and prevent the legacy `commercial_ready=true` response from being treated as current acceptance evidence.
- Rechecked the current tombstone source, safe-baseline verifier, regression tests, and risk-lockdown runbook. Current `main` already owns the retired routes and requires the affected permanent routes to return 410 before authentication or business lookup; no Production code patch is missing for this finding.
- The runbook requires seven Vercel Firewall route groups (`auth_upload`, `generation`, `credit_checkout`, `subscription`, `partner_invite`, `retired_addons`, and `leads_recommendations`), exact configuration snapshot/readback, a short-lived runner bypass, signed source/run/project/domain-bound evidence, and preservation of signed Creem webhook, incident evidence, reconciliation, and logout paths.

### External boundary and required disposition

- The old Production must not be replaced by directly promoting the ordinary browse-only Preview: that deployment is not API-ready and lacks the protected runtime identity, control-plane database, private storage, Redis, Provider, payment, and evidence inputs.
- The protected `production` GitHub environment has none of the 22 workflow-required secrets, so dispatching the production workflow now would be an expected `NOT_RUN`/failure rather than containment or release.
- No Vercel Firewall, deployment, alias, domain, environment variable, secret, database, storage, Redis, Provider, or payment state was changed. External containment remains unapplied pending explicit authorization for the exact firewall mutation; the safe next disposition is to apply and read back the seven bounded deny groups, then provision the protected release inputs and execute the one-time safe-baseline sequence.
- No Subagent was used.

## 2026-07-16 - Hobby-compatible Firewall evidence contract

### Goal and evidence

- Continue preparing the authorized-risk boundary without mutating Production, and verify whether the seven logical edge-lockdown groups can actually be installed on the current Vercel account.
- Read-only Vercel dashboard inspection confirmed the project is owned by a Hobby team, has zero existing custom Firewall rules, has no system bypass rule, and offers the normal custom-rule creation surface. Vercel's current official WAF documentation limits Hobby to three custom rules.
- The release requires a short-lived custom WAF bypass because a system-mitigation bypass does not bypass project custom rules. Seven separate deny rules plus that bypass were therefore impossible under the current plan.

### Changes

- Kept the seven security boundaries as independently reported logical groups, but deterministically packed them into at most two physical deny rules and reserved the third Hobby slot for the release runner bypass.
- Added exact logical group boundaries and explicit preserved webhook/reconciliation/status/evidence/readiness/logout paths to the risk-lockdown runbook. Shared physical rules must remove and publish one logical OR condition group at a time; the physical rule is removed only after its final group passes application-guard and no-side-effect verification.
- Updated the authenticated edge-report verifier to count unique deny rule IDs and reject more than two. A repeated physical rule ID across logical groups is intentional, while all seven logical groups remain mandatory and independently read back.

### Verification and boundary

- The updated contract test was run red first because the verifier did not expose or enforce physical-rule capacity. After implementation, the focused edge-lockdown report test passed and `git diff --check` passed.
- No Firewall draft, rule, bypass, publish, audit-log restore, domain, deployment, environment variable, secret, database, payment, Provider request, or customer data was changed. No Subagent was used.

## 2026-07-16 - Production deployment readback and honest order failure state

### Goal and Production evidence

- Build and deploy exact merged `main` source `3a21556e2baa1838bde75acdca7be23425306ff9`, then verify the formal VowPic domain without treating a static Web success as commercial acceptance.
- Vercel deployment `Gtm6HjfeEXzkcrpxKE4WMPYD8Dmq` built from exact source `3a21556` in the Production environment and reached `Ready`. The staged deployment was then explicitly aliased to `webdev-inspiration-hub.vercel.app` and `www.vowpic.com` after the dashboard displayed the exact target deployment, source, and domains.
- Public readback proved `https://vowpic.com/` returns `307` to `https://www.vowpic.com/`, the formal homepage returns `200`, and the new JS/CSS assets return `200` with immutable caching. The prior asset name is absent from the new HTML.
- `/health` returns `200` liveness, while `/api/v1/ops/readiness` correctly returns `503`. Deployment logs bind that failure to missing protected runtime inputs: canonical runtime bundle, approved Production release role, acceptance-identity HMAC, and distinct control-plane database URL. The GitHub `production` environment still has zero protected secrets, so the formal protected release remains `NOT_RUN` rather than being relabeled complete.
- Both published compound Firewall deny rules remain active. One unbypassed representative request for each of the seven logical risk groups returned `403` with `X-Vercel-Mitigated: deny`; the homepage, assets, liveness, and readiness paths were not denied.

### Changes

- Fixed the public order gallery so a non-authentication API failure is rendered as an explicit retryable error before the empty-gallery state. A `401` or `403` remains the distinct sign-in-required state.
- Added a focused pure-state test for authentication failures, the observed `503` runtime failure, and the localized fallback message. The test was run red first before the resolver existed.

### Verification and boundary

- Focused Vitest passed 3/3. Full frontend Vitest passed 11/11, `vue-tsc --noEmit` passed, the real Uni-app Web build completed, and `git diff --check` passed. The known upstream Dart Sass legacy-JavaScript-API warnings remain visible.
- Browser verification on the formal domain confirmed Home and Create render the browse-only safety state, Account reports the deployment-not-ready error, and Login reports Google auth unavailable. Before this fix, Orders incorrectly rendered an empty gallery during the same `503`; the new bundle has not yet been merged or redeployed.
- No environment variable, secret, database role/schema/data, Supabase object, Redis instance, Provider request, payment, email, or customer record was changed. No rollback to the unsafe pre-kill-switch deployment occurred. No Subagent was used.

## 2026-07-17 - Least-privilege Production database and protected release closure

### Goal and evidence

- Replace the unsafe assumption that one administrator database URL could serve inventory, migration, application runtime, and control-plane writes. The one-time bootstrap now creates a transaction-read-only inventory login, a scoped migration owner/login, and disabled fixed application logins without reusing or exposing the legacy administrator URL.
- The protected workflow inventories and restores the exact legacy source before a `0006 -> 0012` bridge, persists sanitized evidence before mutation, then reruns normal inventory/restore before the atomic `0012 -> 0013` reservation.
- Vercel's current Project API and the pinned CLI source establish `autoAssignCustomDomains`, `link.deployHooks`, the staged Firewall draft endpoints/actions, and `PATCH /v1/projects/{id}/protection-bypass` as the platform contracts used by the automation.

### Changes

- Added exact database role/privilege contracts, scoped password rotation, reconnect-based per-login/per-table proof, runtime elevated-role drift checks, PostgreSQL statement auditing, and direct stdin-only publication of `DATABASE_URL`, `CONTROL_PLANE_DATABASE_URL`, and `CLEANUP_CRON_TOKEN` as Vercel Production Sensitive variables.
- Added a workflow-owned PostgreSQL 17 loopback restore target. Raw dumps, PostgreSQL data, logs, and passwords remain under runner temp and are deleted on every path; only sanitized inventory/restore evidence is uploaded.
- Replaced impossible static edge-report secrets with job-bound Vercel Firewall creation, draft/publish/readback, seven logical group probes packed into two Hobby-compatible deny rules, an ephemeral first-priority custom bypass, exact application-guard handoff, database no-side-effect snapshots, signed reports, and `always()` bypass cleanup.
- Added protected Vercel automation-bypass creation/readback using one GitHub header-pair secret. An unrelated existing automation bypass fails before mutation and is never silently revoked.
- Split database publication from read-only role proof and split edge contract, platform adapter, probes, and orchestration so each module has one bounded responsibility.

### Verification

- The hash-locked backend environment completed 770 tests successfully with 35 explicit external-service skips. The focused database/release/edge/workflow suites completed 136/136, and the final workflow contract suite completed 67/67.
- A fresh local PostgreSQL 17 cluster migrated through the reviewed schema. The real control-plane RLS suite passed 6/6. The complete bootstrap and scoped rotation reconnected through both application logins, proved the exact 16-table SQL/RLS surface, observed 24 runtime statements and zero DDL, then removed the verified temporary cluster.
- Frontend Vitest passed 11/11, `vue-tsc --noEmit` passed, and the real Web SaaS build completed. Git Bash `bash -n` accepted the isolated restore script; Python parsed the workflow YAML; `git diff --check` is required again before commit.
- No Subagent was used.

### Protected state and remaining external gate

- GitHub `production` now contains independent generated `EDGE_EVIDENCE_HMAC_KEY`, `RUNTIME_AUDIT_HMAC_KEY`, `CLEANUP_CRON_TOKEN`, `SAFE_BASELINE_APPROVAL_ID`, `SAFE_BASELINE_BUILD_ARTIFACT_KEY_B64`, and `VERCEL_AUTOMATION_BYPASS_HEADER`; only names/timestamps were read back. Secret plaintext was not printed, saved, or passed in command arguments.
- The existing `PRODUCTION_READ_ONLY_DATABASE_URL` is not accepted as proof of the new inventory login and must be replaced. `PRODUCTION_MIGRATION_DATABASE_URL` remains absent. Both must come from the new Supabase SQL-Editor bootstrap result; the legacy administrator URL must not be copied into either slot.
- No Production Supabase role/schema/data migration, Vercel environment mutation, Vercel Firewall publish, deployment, Promote, alias/domain change, Provider request, payment, email, storage object, Redis state, customer data, or Proxifier setting was changed during this implementation pass.

## 2026-07-17 - Hosted Supabase inventory-role compatibility repair

### Goal and evidence

- A real Supabase SQL Editor execution disproved the earlier inventory-role contract: hosted `postgres` has `CREATEROLE` but is not superuser, so PostgreSQL correctly rejects creating or altering a role with `BYPASSRLS`. The failed function call rolled back and created no login.
- The reviewed target migration also creates `vowpic_identity_owner` and `vowpic_identity_service`; a correctly constrained `NOCREATEROLE` migration login cannot create those cluster roles itself. The bootstrap must precreate them and grant the migration owner only the membership needed for the reviewed identity owner transfers.

### Changes

- Replaced the inventory login's `BYPASSRLS` dependency with NOBYPASSRLS plus one exact permissive, SELECT-only policy per public RLS table. Inventory evidence now proves the exact authenticated role, no memberships or ownership, complete table/sequence read grants, exact RLS policy coverage, zero write grants, read-only transaction/default, and SQLSTATE `25006` on a write probe.
- Added an idempotent policy reconciler before protected inventory and inside the atomic `0012 -> 0014 + RESERVED` transaction. The bootstrap now precreates both identity NOLOGIN roles while keeping the migration login `NOCREATEROLE`.
- Added `pg_dump --enable-row-security`. The isolated Admin performs restore and comparison so forced RLS cannot reject `COPY`; the non-superuser disposable target owner remains separately verified. Missing role names referenced by restored policies are created as isolated NOLOGIN placeholders and deleted during mandatory cleanup.

### Verification and boundary

- Focused inventory/login/restore/workflow tests passed 102/102 after red-first failures.
- A fresh PostgreSQL 17 instance migrated `base -> 0006`, ran the revised bootstrap, reconnected through the generated-role shapes, and migrated through the exact restricted login to `0014`. Policy reconciliation proved 8/8 RLS tables; real inventory completed; custom-format dump/restore compared all 25 tables, row counts, foreign keys, ledger, and URL checksum; the disposable database and role were removed.
- A separate two-cluster PostgreSQL 17 rehearsal repeated the legacy `0006` inventory/restore topology. An RLS fixture forced two missing policy-role placeholders to be created on the isolated target; restore comparison passed, both placeholders were dropped, and target role readback returned zero remaining policy roles.
- No Production role, schema, data, GitHub secret, Vercel setting, deployment, domain, external Provider, customer record, or Proxifier state was changed in this repair pass. Production execution remains gated on review/merge and protected workflow success. No Subagent was used.

## 2026-07-17 - Hosted Supabase password-rotation compatibility repair

### Goal and evidence

- A second real Supabase SQL Editor execution reached the password-rotation statement and failed with SQLSTATE `42501`: hosted `postgres` may create the least-privilege roles, but an `ALTER ROLE` that explicitly repeats `NOSUPERUSER` or `NOBYPASSRLS` is still treated as a protected role-attribute change. The function call rolled back; the generated passwords and role changes did not become valid.
- The minimum compatible operation is to validate the existing role attributes first and then alter only `LOGIN`, `PASSWORD`, and `VALID UNTIL`. The same restriction applies to the later application-login rotation function.

### Changes

- Added a pre-rotation fail-closed check for inventory, migration, runtime, and control-writer logins. Any superuser, database-creator, role-creator, replication, RLS-bypass, or non-inheriting role is rejected before a password is changed.
- Reduced the four password-rotation statements to the non-privileged login/password/expiry fields. The application rotation function independently repeats its elevated-attribute check on every call.
- Added regression assertions that protected attributes may appear in role creation and verification, but never in an `ALTER ROLE` password rotation.

### Verification and boundary

- Focused database-login and runtime-audit tests passed 9/9. The correctly invoked backend suite passed 773/773 with 35 explicit external-service skips. `git diff --check` passed before the worklog update.
- A disposable local PostgreSQL container demoted `postgres` to non-superuser with `CREATEROLE`, created a least-privilege login, and successfully executed the password-only rotation; readback proved the admin remained non-superuser and the target had no superuser, role-creator, or RLS-bypass attribute. The container was removed.
- An exact PostgreSQL 17 container rerun was unavailable because the image is not cached and Docker Desktop could not reach Docker Hub without changing its proxy. No proxy or Proxifier setting was changed. The exact hosted-PostgreSQL proof therefore remains the guarded Supabase rerun after review and merge.
- No Subagent was used.

## 2026-07-17 - Hosted Supabase migration-owner SET compatibility repair

### Goal and evidence

- The next real Supabase SQL Editor execution passed the password-only role rotation and failed transactionally with SQLSTATE `42501` on `ALTER TABLE public.click_stats OWNER TO vowpic_migration_owner`: the hosted `postgres` session was not able to `SET ROLE` to the new owner.
- PostgreSQL 17 automatically gives a non-superuser `CREATEROLE` creator `ADMIN TRUE, SET FALSE, INHERIT FALSE`. PostgreSQL also requires `SET ROLE` capability for `ALTER ... OWNER TO`, so the bootstrap must add the missing membership option explicitly.

### Changes

- Granted `CURRENT_USER` membership in `vowpic_migration_owner` with `SET TRUE` and `INHERIT FALSE` before any ownership transfer. This is limited to the already-authorized SQL Editor/bootstrap identity and does not add inherited data privileges to an application or migration login.
- Added a regression assertion that the SET-capable grant exists before the first ownership transfer.

### Verification and boundary

- The failed hosted execution was atomic and rolled back, so it created no valid login or password. Focused database-login/runtime-audit tests passed 9/9, and the full backend unittest discovery completed with exit code 0. CI plus a reviewed merge are required before the guarded Supabase rerun.
- No Subagent was used. No Proxifier setting was read or changed.

## 2026-07-17 - Hosted Supabase protected-role self-grant repair

### Goal and evidence

- The merged explicit `GRANT vowpic_migration_owner TO CURRENT_USER ... SET TRUE` terminated the hosted Supabase SQL Editor management connection. A rollback probe proved that `CREATE ROLE` itself succeeds, while adding that explicit grant reproduces the connection termination.
- A second rollback probe set PostgreSQL 17 `createrole_self_grant` to `set` before creating the owner and proved with `pg_has_role(..., 'SET')` that the SQL Editor authority receives the required SET capability without targeting the protected current role with a GRANT.

### Changes

- Request SET membership transaction-locally before creating `vowpic_migration_owner`.
- Removed the hosted-incompatible explicit grant to `CURRENT_USER` and added a fail-closed SET capability check for an already-existing owner.
- Updated the regression contract to require the self-grant setting and capability check before any ownership transfer, and to reject the explicit current-user grant.

### Verification and boundary

- Both hosted probes deliberately raised exceptions after their assertions, so their role DDL rolled back and no probe role remained. The production bootstrap attempt that exposed the failure also rolled back completely.
- Focused database-login/runtime-audit tests passed 9/9 and `git diff --check` passed. The unlocked local Python 3.13 full suite is not a valid release baseline: it collected 773 tests but retained one OpenAPI snapshot failure and three Pillow API errors that are absent from the repository's locked Python 3.11 CI environment; the protected branch checks remain the authoritative full regression gate.
- No secret value was returned or persisted during diagnosis. No Proxifier setting was read or changed.

## 2026-07-17 - Migration-owner default-privilege execution repair

### Goal and evidence

- The merged self-grant repair let the real bootstrap pass role creation, password configuration, grants, and object ownership transfer. The transaction then failed explicitly with SQLSTATE `42501` because the protected Supabase SQL Editor identity could not change default privileges for `vowpic_migration_owner`.
- A hosted rollback probe proved that the SQL Editor identity can `SET LOCAL ROLE` to a newly self-granted owner, apply that owner's default table privileges, `RESET ROLE`, and reach a deliberate rollback exception.

### Changes

- Wrapped only the two migration-owner default-privilege statements in `SET LOCAL ROLE vowpic_migration_owner` and `RESET ROLE`.
- Removed the `FOR ROLE` form that was still evaluated against the protected SQL Editor identity, while keeping the same inventory SELECT-only defaults.
- Added ordering assertions and a regression rejection for the hosted-incompatible `FOR ROLE` form.

### Verification and boundary

- The hosted probe deliberately raised an exception after the default-privilege assertion, so both probe roles and their ACL changes rolled back. The failed production bootstrap also rolled back completely and returned no credential payload.
- No Proxifier setting was read or changed.

## 2026-07-17 - Production bootstrap ownership idempotency repair

### Goal and evidence

- The first successful hosted bootstrap transferred public relations and routines to `vowpic_migration_owner`. A later credential-rotation rerun then failed transactionally with SQLSTATE `42501` on `ALTER TABLE public.account_risk_events OWNER TO vowpic_migration_owner`, because the protected SQL Editor identity no longer owned that already-migrated table.
- The failed rerun rolled back its password rotations, so the previously committed database roles and passwords remained unchanged.

### Changes

- Excluded relations already owned by `vowpic_migration_owner` from the relation ownership loop.
- Excluded routines already owned by `vowpic_migration_owner` from the routine ownership loop.
- Added ordering assertions that both owner filters are present before their corresponding dynamic ownership statements.

### Verification and boundary

- The change preserves first-run ownership transfer while making subsequent credential rotations idempotent for already-migrated objects. Extension-owned and revision-specific exclusions remain unchanged.
- No secret value was returned or persisted from the failed rerun. No Proxifier setting was read or changed.

## 2026-07-17 - Bootstrap-managed routine ownership exclusion

### Goal and evidence

- After relation/routine owner idempotency was merged, the hosted rerun progressed past the previously failing table and then failed transactionally with SQLSTATE `42501` while replacing `public.vowpic_runtime_statement_audit()`.
- Read-only Production catalog evidence showed the two bootstrap-managed signatures are `vowpic_runtime_statement_audit()` and `vowpic_rotate_application_database_logins(runtime_password text, writer_password text)`. Both are intentionally recreated as `SECURITY DEFINER` helpers and owned by `postgres`, so they must not enter the general migration-owner routine loop.

### Changes

- Excluded the exact zero-argument runtime-audit function from the general routine ownership transfer.
- Excluded the exact two-text-argument application-login rotation function from the same loop.
- Added regression assertions that both signature-specific exclusions occur before the dynamic routine ownership statement.

### Verification and boundary

- The exclusions are signature-specific and do not exempt unrelated overloads or application routines from the migration-owner contract.
- The failed hosted rerun rolled back password rotation and routine ownership changes. No secret value was returned or persisted, and no Proxifier setting was read or changed.

## 2026-07-17 - Linux release-tool lock completeness repair

### Goal and evidence

- Protected run `29575028918` proved the exact reviewed `main` SHA and completed the hash-locked Python install, then stopped before every database, edge, Vercel, deploy, Promote, and domain step.
- The Ubuntu runner's `npm ci` rejected the committed release-tool lock because the Linux optional dependency graph required `@emnapi/core@1.11.2` and `@emnapi/runtime@1.11.2`, but neither package was present in the lock. The later artifact upload also failed because an early tooling failure had not created the evidence directory.

### Changes

- Added both required Emscripten N-API peer packages as exact release-tool development dependencies and committed their integrity-checked lock entries. This makes the Linux optional graph explicit instead of relying on a Windows-generated omission.
- Added a sanitized early-failure evidence record only when no earlier evidence file exists. The existing fail-closed artifact upload remains unchanged and can no longer overwrite the primary failure with a missing-directory error.
- Extended the release contract tests to pin both versions, require their SHA-512 integrity entries, and require the early-failure record before diagnostic upload.

### Verification and boundary

- The complete workflow contract suite passed 67/67. A clean local `npm ci --ignore-scripts` installed 298 release-tool packages, and the installed CLI read back exact version `56.2.0`.
- An npm Linux/x64/glibc dry-run accepted the repaired lock and resolved the Linux platform packages without the two missing-package errors from the protected runner. `git diff --check` passed before this worklog update.
- The failed protected run made no Production database, Vercel, Firewall, deployment, alias, domain, Provider, customer-data, or Proxifier change. No Subagent was used.

## 2026-07-17 - Production database CA scope repair

### Goal and evidence

- Protected run `29576156344` completed the repaired release-tool install and discovered the Production schema boundary, then failed on the first GitHub ref recheck with `CERTIFICATE_VERIFY_FAILED`.
- Readback proved `main` still matched the exact reviewed source. The failure was caused by publishing the Supabase database CA as global `SSL_CERT_FILE`, which replaced the HTTPS trust chain used by `httpx` before any database policy reconciliation or migration began.

### Changes and verification

- Scoped the pinned Supabase CA to libpq/PostgreSQL via `PGSSLROOTCERT`. GitHub, Vercel, S3, npm, and other HTTPS clients continue using the runner's normal system trust store.
- Added a contract assertion for the PostgreSQL-only environment variable and a regression rejection for global `SSL_CERT_FILE` in the protected workflow.
- The failed run stopped before every database policy mutation, migration, Vercel mutation, Firewall publish, deployment, Promote, alias, and domain step. Its sanitized early-failure artifact uploaded successfully. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - Asyncpg scoped database CA loading

### Goal and evidence

- Protected run `29576570930` passed GitHub ref verification and the read-only RLS-policy reconciliation, then failed before writing inventory evidence because `asyncpg` does not consume libpq's `PGSSLROOTCERT` automatically.
- The database TLS handshake reported a self-signed certificate in the chain. The workflow had correctly stopped using global `SSL_CERT_FILE`, so HTTPS trust remained intact and no migration or deployment step ran.

### Changes and boundary

- The shared async database TLS builder now explicitly adds the existing `PGSSLROOTCERT` file to its database-only SSL context while retaining CA verification and hostname verification. A missing configured file fails closed before a connection attempt.
- Added focused tests proving the scoped CA is loaded into the `asyncpg` context and a missing file is rejected. The workflow continues to use the same pinned and checksum-verified Supabase CA without changing global HTTPS trust.
- The failed run's RLS reconciliation was read back successfully but no legacy inventory, restore, schema migration, reservation, Vercel mutation, Firewall publish, deployment, Promote, alias, or domain action completed. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - Isolated restore runner socket repair

### Goal and evidence

- Protected run `29576965688` completed the real legacy Production inventory through the new read-only login, then initialized the isolated PostgreSQL 17 cluster but could not start its local server.
- The PGDG Ubuntu package can default Unix sockets to the distribution-owned runtime directory. The GitHub runner starts the disposable cluster as the unprivileged `runner` user, while the script previously constrained only TCP listen address and port.

### Changes and boundary

- Bound the disposable server's Unix socket to its runner-owned, per-run `PGDATA` directory. TCP remains restricted to `127.0.0.1` and the existing validated restore port.
- On startup failure only, the script now emits at most 80 lines from the pre-credential server log before mandatory cleanup. The database passwords are not applied until after this diagnostic boundary, and the raw log is still deleted on every path.
- Extended the workflow contract to require the private socket location, bounded startup diagnostic, diagnostic-before-password ordering, and existing cleanup. The failed run completed no restore, schema migration, reservation, Vercel mutation, Firewall publish, deployment, Promote, alias, or domain action. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - VowPic-only restore schema boundary

### Goal and evidence

- Protected run `29577449102` proved the private runner socket repair: PostgreSQL 17 started, the disposable role and database were created, and authentication was changed from temporary trust to SCRAM.
- The subsequent source dump failed because unrestricted `pg_dump` attempted to lock Supabase-managed `auth`, `storage`, and `realtime` relations. The inventory login intentionally has SELECT only on VowPic's `public` schema and must not gain access to platform-internal data.

### Changes and boundary

- Restricted the encrypted scratch dump to `--schema=public`, matching the inventory, migration, comparison, and application ownership boundary. Supabase platform schemas are neither requested nor granted to the VowPic inventory login.
- Added a regression assertion that every rehearsal dump is explicitly public-schema-only while retaining row-level-security enforcement, no-owner/no-ACL restore, isolated Admin restore, and mandatory raw-dump cleanup.
- The failed run did not complete a dump or restore and did not execute schema migration, reservation, Vercel mutation, Firewall publish, deployment, Promote, alias, or domain actions. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - Linux edge-lockdown script entrypoint repair

### Goal and evidence

- Protected run `29577828484` completed the legacy Production inventory, the first isolated PostgreSQL 17 restore rehearsal, the exact `0006 -> 0012` bridge, the post-bridge read-only inventory, and a second isolated restore rehearsal.
- The run then failed before its first Vercel operation because direct execution of `scripts/release/manage_edge_lockdown.py` set Python's import path to `scripts/release`, so its repository-package import raised `ModuleNotFoundError: No module named 'scripts'`. The unconditional bypass-cleanup step failed at the same pre-main import boundary.
- Because both failures occurred while importing the module, neither path constructed a Vercel client, created a Firewall draft, published a rule, or created a runner bypass. Reservation, application-login publication, build, deploy, Promote, alias, and formal-domain verification did not start. Production remains safely bridged at schema `0012`.

### Changes and verification

- Added the repository root to the script import path before its `scripts.release` imports, following the existing release-script entrypoint pattern.
- Added a subprocess regression that removes inherited `PYTHONPATH` and invokes the exact workflow form from the repository root. The direct `--help` entrypoint now exits zero and exposes `cleanup-bypass`.
- The focused edge manager and protected workflow contract suites passed 75/75. `git diff --check` passed. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - Fail-closed bootstrap edge-bypass proof

### Goal and evidence

- Protected run `29578442846` correctly recognized the already-bridged `0012` Production schema, skipped every legacy bridge step, and repeated the read-only inventory plus isolated PostgreSQL 17 restore rehearsal successfully.
- The repaired edge manager published and read back the ephemeral runner bypass plus the two managed deny rules. All seven unbypassed logical route probes returned the required edge denial. The bypassed authentication probe reached the existing application and returned the exact fail-closed `503/runtime_not_ready` boundary instead of the post-install `410/auth_method_retired` guard.
- The workflow stopped before protected-evidence persistence, reservation, application-login publication, build, deploy, Promote, alias, and formal-domain handoff. Its unconditional cleanup succeeded and removed the runner bypass; the two deny rules remain as the intended safe containment.

### Changes and verification

- The initial lockdown now accepts either the final application guard or the exact `503/runtime_not_ready` response as proof that the runner bypass reached the currently fail-closed application. No other `503` code is accepted.
- The later per-group handoff remains strict: it still requires the exact final application status/code and matching before/after database snapshots after each deny group is removed. The bootstrap allowance cannot satisfy formal-domain handoff or completion.
- Added regressions for the full initial deny/bypass HTTP sequence, the exact fail-closed allowance, and default/handoff rejection of that allowance. Focused edge and workflow contract tests passed, and `git diff --check` passed. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - Migration-owner forced-RLS control-plane repair

### Goal and evidence

- Protected run `29579027746` passed the schema-`0012` inventory, isolated restore rehearsal, authenticated edge lockdown, protected evidence upload/readback, and final main-head recheck.
- The atomic reservation transaction upgraded through `20260710_0013` and `20260712_0014`, then the insert into `release_activations` was rejected by forced RLS. That migration created policies for `vowpic_runtime` and `vowpic_control_writer` but omitted the existing least-privilege `vowpic_migration_owner` used by the same transaction.
- Transactional DDL rolled back both revisions and the failed insert together. Production therefore remains at `0012` with no reservation row, application-login publication, build, deploy, Promote, alias, or formal-domain handoff. The unconditional runner-bypass cleanup passed; the two deny rules remain as intended containment.

### Changes and verification

- The control-plane migration now validates or creates the exact `NOLOGIN`, `NOBYPASSRLS` migration-owner shape, grants only control-plane DML, and creates one forced-RLS `FOR ALL` policy for that role on each of the eight control-plane tables. Runtime and control-writer policy boundaries remain unchanged.
- Added a real non-superuser, non-role-creator, non-RLS-bypass migration login to the PostgreSQL integration test. Its database default role becomes `vowpic_migration_owner`; readback proves the exact role shape and eight policies before the exact reservation insert succeeds.
- A fresh isolated PostgreSQL 17 cluster migrated from empty through head and passed all 7 control-plane RLS integration tests, including the new reservation path. The server stopped and its test data/log were deleted. Focused schema/workflow tests passed 74/74 and `git diff --check` passed. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - Linux Vercel automation-bypass entrypoint repair

### Goal and evidence

- Protected run `29579791731` proved the migration-owner RLS repair by atomically upgrading Production from `0012` through `0014` and reserving the install. It also created and published the two least-privilege application logins.
- The next step failed before constructing a Vercel client because direct execution of `scripts/release/ensure_vercel_automation_bypass.py` could not resolve its `scripts.release` import. The workflow preserved sanitized failure evidence and removed the ephemeral runner bypass; no build, deployment, Promote, alias, or formal-domain handoff began.

### Changes and verification

- Added the repository root to that direct script's import path before its package import, matching the established release-entrypoint contract.
- Added a subprocess regression that removes inherited `PYTHONPATH` and invokes the exact script with `--help` from the repository root. The focused bypass and protected-workflow contract suites passed 73/73, and `git diff --check` passed. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - Unbound Production reservation adoption contract

### Goal and evidence

- Protected run `29580419600` reached schema `0014` and then failed in the read-only preflight with `CONFLICTING_INSTALL`. The existing `RESERVED` row belonged to the earlier reviewed run/SHA that had completed migration and application-login publication before a release-script import failure; a fixed descendant SHA therefore could not enter the existing same-run-only recovery path.
- The failed retry stopped before inventory, edge mutation, application-login rotation, Vercel bypass mutation, build, deploy, Promote, alias, and formal-domain handoff. It preserved sanitized failure diagnostics and did not attempt a runner bypass.

### Changes and verification

- Added a controlled adoption path only for a `RESERVED` row with no runtime bundle, manifest, build artifact, report, API/worker deployment, target snapshot, or acceptance-fault binding. Bound or later-phase activations remain conflicting and immutable to this path.
- Adoption requires the reserving SHA to be a Git ancestor of the exact reviewed checkout, the same protected approval, exact prior source/run/version coordinates, the migration advisory lock, row lock, and a version CAS. The durable preflight/edge artifact is written before mutation and becomes the new evidence-chain head while preserving the prior evidence reference inside the preflight report.
- Focused release/workflow tests passed 75/75; the PostgreSQL integration extension is wired into CI's real database job and was locally collected with its seven explicit environment-dependent skips. Python compilation, YAML parsing, Git ancestry proof, and `git diff --check` passed. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - Supavisor password-propagation retry boundary

### Goal and evidence

- Protected run `29581521675` successfully completed the new read-only takeover preflight, authenticated edge lockdown, durable reservation evidence, exact-main recheck, and atomic adoption of the unbound Production reservation.
- The next application-login step committed a new password rotation, then the Supavisor session pool returned `password authentication failed` for the runtime login before any Vercel environment write. This is the documented pooler credential-propagation/cache boundary; the workflow preserved failure evidence and removed its runner bypass.

### Changes and verification

- Added four bounded proof attempts over 105 seconds, and only for an exact password-authentication failure when both generated role URLs target `*.pooler.supabase.com`. Direct-database authentication failures, timeouts, privilege mismatches, and every other error still fail immediately.
- Vercel publication remains after the application logins authenticate and their exact SQL surfaces pass, so unverified credentials are not promoted. Focused login/release tests passed 85/85 with seven explicit database-environment skips; Python compilation and `git diff --check` passed. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - Preserve independent Vercel automation bypasses

### Goal and evidence

- Protected run `29582211168` proved the bounded Supavisor repair: both least-privilege application logins authenticated through the pooler, passed their SQL-surface checks, and were published as Vercel Production Sensitive variables.
- The next step stopped before build or deployment because the project already had a different automation-bypass secret. Vercel's current contract explicitly supports multiple project secrets for independent automation tools, so treating every additional secret as a conflict was stricter than the platform contract and blocked the reviewed VowPic secret without improving ownership safety.

### Changes and verification

- Reconciliation now preserves all pre-existing automation bypasses, creates the protected VowPic target only when absent, and requires exact readback of the prior key set plus that target. It also fails if any pre-existing metadata changed, so it neither overwrites nor revokes an unknown integration.
- The report contains only counts and the existing target fingerprint; no bypass plaintext is logged or persisted. Focused release coverage passed 96/96, and the full backend suite passed 788 tests with 36 explicit environment-dependent skips. Python compilation, workflow YAML parsing, and `git diff --check` passed. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - Exact Vercel bypass-secret format contract

### Goal and evidence

- Protected run `29583036219` passed least-privilege database-login authentication and publication, then Vercel rejected creation of the protected automation bypass with HTTP 400 before any build or deployment.
- Vercel's official Provider contract requires an exact 32-character alphanumeric secret. The local parser incorrectly admitted 32-256 characters plus underscore and hyphen, so an invalid protected input crossed local validation and failed only at the external mutation boundary.

### Changes and verification

- The parser now accepts only the exact Vercel format and rejects 31/33-character and underscore/hyphen cases before constructing an API client. Existing secret redaction and no-plaintext reporting remain unchanged.
- The protected GitHub Production header secret must be rotated to a cryptographically generated 32-character alphanumeric value after this contract passes review; generation is process-only and the value must never be printed or persisted. Focused release coverage passed 96/96, the full backend suite passed 788 tests with 36 explicit environment-dependent skips, and Python compilation plus `git diff --check` passed. No existing Vercel bypass is revoked. No Proxifier setting was read or changed, and no Subagent was used.

## 2026-07-17 - Deterministic uv tooling for local Vercel builds

### Goal and evidence

- Protected run `29583942291` passed least-privilege database-login authentication and publication, created and read back the protected Vercel automation bypass, and reached the first `vercel build --prod`. The frontend build completed, but Vercel CLI 56.2.0 then failed before producing `.vercel/output` because `uv` was absent from the runner `PATH`.
- The exact official Vercel CLI 56.2.0 source fixes its Python builder uv version at `0.10.11` and requires at least `0.9.25`. The official Astral version manifest publishes SHA-256 `5a360b0de092ddf4131f5313d0411b48c4e95e8107e40c3f8f2e9fcb636b3583` for the `0.10.11` Linux x86_64 GNU archive.
- The failed run did not upload or bind a build artifact and did not deploy, Promote, change the formal-domain alias, or begin the formal handoff. Its unconditional runner-bypass cleanup passed.

### Changes and verification

- The protected workflow now installs `uv 0.10.11` through official `astral-sh/setup-uv` v8.3.2 pinned to full commit `11f9893b081a58869d3b5fccaea48c9e9e46f990`, disables the unrelated uv dependency cache, downloads from the official GitHub release, and checks the exact official archive checksum.
- The release-tooling step requires exact `uv 0.10.11` output before installing any other tooling or invoking Vercel. A closed contract test proves the immutable action pin, version, checksum, source, cache setting, singleton action use, and ordering before the first build.
- Focused release coverage passed 97/97, the full backend suite passed 789 tests with 36 explicit environment-dependent skips, workflow YAML parsing, Python compilation, and `git diff --check` passed. No application runtime dependency, cloud configuration, secret, domain, or Proxifier setting was changed, and no Subagent was used.

## 2026-07-17 - GitHub build-artifact digest boundary normalization

### Goal and evidence

- Protected run `29584924777` proved the deterministic uv repair: Vercel's complete predeploy build passed, the exact `.vercel/output` was encrypted, and GitHub artifact upload plus durable-output checks passed.
- Binding then failed before the registration script ran because upload-artifact v7 emits its `artifact-digest` output as 64 lowercase hexadecimal characters, while the internal database and GitHub REST evidence contract intentionally stores the canonical `sha256:<64 lowercase hex>` form. The workflow incorrectly required the canonical form without converting the action output.
- No build manifest was bound and no staged deployment, Promote, formal-domain change, or edge handoff began. Sanitized failure evidence was preserved and the ephemeral runner bypass cleanup passed.

### Changes and verification

- The bind step now treats new-upload and recovered-artifact digests as separate trust boundaries. A new action output must be exact raw 64-hex and is prefixed once; a recovered REST digest must already be exact canonical form. The final value is revalidated before the unchanged database CAS.
- A closed workflow contract proves both branch-specific input formats, the single normalization, the final canonical validation, and removal of the unsafe direct fallback expression. Focused release coverage passed 98/98, the full backend suite passed 790 tests with 36 explicit environment-dependent skips, workflow YAML parsing, Python compilation, and `git diff --check` passed.
- No database schema, application runtime code, cloud setting, secret, domain, or Proxifier setting was changed, and no Subagent was used.

## 2026-07-17 - Process-only liveness and controlled STAGED verifier recovery

### Goal and evidence

- Protected run `29585772996` bound the exact encrypted build and created/bound Vercel deployment `dpl_31bfGCZPSbB6qgerkBR1cnVUVcky` at source `67424b55d40c639bfc9e7c195e907e24b473abb9`, runtime bundle `rtb_7c423610369548d43aa73a248410e9e96521e65cca891fe37e0065a13ea72629`, then stopped during staged verification with `runtime source_sha mismatch`. Vercel's authenticated deployment page independently showed the exact bound deployment as READY with source `67424b5`; no Promote, formal-domain switch, formal handoff, or completion occurred, and runner-bypass cleanup passed.
- The verifier read immutable runtime coordinates from `/health`, while the application and `collect_runtime_report.py` deliberately require `/health` to return only the exact process-liveness object. The public runtime attestation already exists at `/version`. The mismatch was therefore an absent-field verifier defect, not Vercel source drift.
- The activation is already `STAGED` and its runtime/build/deployment coordinates are immutable. A different workflow run could not resume it, while rerunning the old workflow would reuse the old verifier. Clearing the row, rebuilding, redeploying, or changing the deployed source would violate the recorded recovery contract.

### Changes and verification

- The safe-baseline verifier now requires the exact process-only `/health` payload and obtains `source_sha`, `runtime_bundle_id`, and `deployment_id` from `/version`. Regression coverage rejects extra liveness state and mismatched version coordinates.
- Added one narrowly scoped STAGED-verifier adoption. The old runtime source remains unchanged; a distinct current-main runner must be its Git ancestor, and the entire diff must be modified files from the exact release-control/test/runbook allowlist, including the workflow, registration script, and verifier. Application, migration, dependency, configuration, addition, deletion, or rename changes fail closed.
- Both read-only preflight and the locked/version-CAS mutation validate that boundary. The mutation changes only workflow run/attempt, durable evidence URL, and version; runtime bundle, manifest, encrypted build-artifact coordinates, deployment ID/URL, role, phase, and snapshots remain unchanged. The new protected workflow checks out and repeatedly verifies `runner_sha` as current main while continuing to bind runtime and formal evidence to the immutable `source_sha`.
- New contract cases were run red against the prior implementation and green after the repair. `backend.tests.test_ci_release_contract` passed 75/75; the full backend suite passed 794 tests with 36 explicit environment-dependent skips. Workflow YAML parsing, Python compilation, and `git diff --check` passed. One initial system-Python invocation was invalid because that interpreter lacked `httpx`; the repository `.venv` established the valid 71-test pre-change baseline and all reported post-change results.
- No database schema, application runtime code, runtime dependency, cloud setting, secret, domain, or Proxifier setting was changed, and no Subagent was used. Production remains at the recorded STAGED deployment until the reviewed recovery workflow passes its protected approval, staged verification, Promote, formal-domain verification, edge handoff, and terminal CAS.

## 2026-07-17 - Fail-closed STAGED runtime-secret repair and exact-source rebuild

### Goal and evidence

- Protected run `29593539251` adopted verification ownership of the immutable STAGED deployment and reached the runtime DDL-audit collector, then failed because `google_oauth_intent` returned `runtime_not_ready` instead of the expected disabled-capability response. The authenticated Vercel runtime log identified the exact fail-closed startup reason: `ACCEPTANCE_IDENTITY_HMAC_KEY` was absent from the Production project.
- The Vercel settings readback proved the variable did not exist. A new 64-character random value was written as a Production Sensitive variable and the same unprinted value was written to the repository Actions secret; Vercel readback showed the Production-only sensitive key and GitHub listed the synchronized secret timestamp. The existing deployment remains immutable and cannot consume the new variable without a new deployment.
- No Promote, formal-domain switch, formal handoff, or terminal CAS occurred. The recorded deployment remains STAGED and edge containment remains in force.

### Changes and verification

- Added a protected config proof that pulls the exact Vercel Production environment, requires both sides to contain at least 32 characters, compares them in constant time, and persists only sanitized coordinates and PASS metadata. No secret value or digest is printed or written to evidence.
- Added a one-purpose STAGED rearm for the fully bound, unpromoted, report-free safe-baseline row. It requires the same approval, exact old source/run/version CAS, release-control-only descendant proof, fresh durable edge/config evidence, the existing lock/statement timeouts, the migration advisory lock, and an ACCESS EXCLUSIVE table lock. The regression trigger is disabled only inside that transaction, `updated_at` is advanced explicitly, the old binding is retained by durable preflight evidence and coordinate hash, the row is cleared to an unbound reservation for the same workflow run's next attempt, and the trigger is restored before commit.
- The repair attempt intentionally exits after the atomic rearm. Its next GitHub attempt rebuilds from a detached worktree at the original immutable runtime `source_sha`, rechecks the synchronized Production secret, encrypts and binds the new build output, and then follows the unchanged staged, Promote, formal-domain, edge-handoff, and completion gates.
- The focused release contract baseline passed 75/75 before the change and 78/78 after it. The full backend suite passed 797 tests with 36 explicit environment-dependent skips. Workflow YAML parsing, Python compilation, and `git diff --check` passed. No application runtime code, dependency, schema revision, domain, feature state, or Proxifier setting was changed, and no Subagent was used.

## 2026-07-17 - Correct fail-closed proof for unreadable Vercel Sensitive values

- Protected run `29597021010` failed before reservation evidence, database mutation, rebuild, deploy, or domain change because Vercel correctly did not expose `ACCEPTANCE_IDENTITY_HMAC_KEY` through `vercel pull`.
- Vercel documents Sensitive values as unreadable after creation and decryptable only during build/runtime. The release control therefore no longer attempts an impossible plaintext comparison and does not downgrade the variable.
- The repaired proof requires exact Vercel metadata (`sensitive`, Production-only, no branch/custom environment) plus a same-rotation non-sensitive SHA-256 companion matched in constant time to the protected GitHub secret. The protected rearm step force-upserts only that computed companion, then pulls it back for proof; it never prints or writes the secret itself. Sanitized evidence contains neither the secret nor its fingerprint and is bound to project/team/source/runner/run coordinates.
- The same proof runs before STAGED rearm and again inside the detached immutable-source build. The failed run remains a safe diagnostic attempt; a new reviewed runner commit and new protected run are required.
- The focused release contract passed 78/78, the full backend suite passed 797 tests with 36 environment-dependent skips, Python compilation and workflow YAML parsing passed, and `git diff --check` was clean after the correction.

## 2026-07-17 - Self-contained Vercel prebuild and bound-RESERVED recovery

### Goal and evidence

- Protected run `29600476644`, attempt 2, rebuilt the immutable runtime source, encrypted and bound artifact `8414944333`, then failed before any Vercel deployment was created. Vercel CLI reported that a hashed frontend asset referenced from the recovered prebuild did not exist.
- The workflow archived `.vercel/output` with its source-tree symlinks intact. The encrypted artifact therefore retained link paths but not the linked frontend bytes; recovering it in another checkout made those links dangling. The activation remained `RESERVED`, with no deployment, Promote, domain switch, formal handoff, or completion.
- The first reviewed recovery run `29603359658` stopped in the read-only preflight before evidence upload or mutation because the internal preflight allowlist omitted its new `TAKEOVER_RESERVED_BUILD` classifier result. The workflow parser already accepted the state, but the registration preflight correctly failed closed instead of crossing a mutation boundary.

### Changes and verification

- The build now materializes all in-source Vercel output links into ordinary files/directories before hashing, encryption, and upload. Broken, cyclic, unsupported, out-of-source, pre-existing-destination, and still-linked outputs fail closed.
- Added one exact recovery for a bound but undeployed `RESERVED` prebuild. It preserves source and business state, requires a reviewed release-control-only descendant plus exact approval/run/version/artifact CAS, clears only the invalid manifest/artifact coordinates under the advisory and table locks, restores the regression trigger, and stops for an attempt-2 rebuild. It cannot operate after any deployment coordinate or later-phase state exists.
- The preflight now explicitly accepts the bound-build recovery state and runs the same release-control-only descendant proof before returning it; a regression test exercises the complete read-only preflight rather than only the classifier. The focused release contract passed 83 tests with one Windows-only symlink-capability skip; the full backend suite passed 801 tests with 37 explicit environment/platform-dependent skips. Python compilation, workflow YAML structure parsing, and `git diff --check` passed. No Production domain or Proxifier setting was changed by the repair itself, and no Subagent was used.

## 2026-07-17 - Forward-only RESERVED prebuild repair and adm-zip closure

### Goal and evidence

- PR #39's frontend check exposed a newly reviewed high-severity `adm-zip < 0.6.0` advisory. The frontend lock still resolved `@dcloudio/uni-cli-shared` through `adm-zip 0.5.16`; continuing to rebuild the old reserved source would therefore preserve a known vulnerable ZIP parser.
- The existing activation is a bound `RESERVED` prebuild with an invalid, non-self-contained encrypted artifact, but it has no Vercel deployment, runtime bundle, report, worker, target snapshot, or fault state. No deployed runtime source therefore exists to preserve.

### Changes and verification

- The frontend override and lock now resolve `adm-zip 0.6.0`. The exact override and resolved lock version are covered by the dependency contract.
- The bound-prebuild repair is now forward-only: the old source must be an ancestor of a distinct exact current-main source, and the runner must equal that new source. The complete old-to-new diff must contain the workflow, registration/verifier scripts, frontend manifest and lock, and may otherwise contain only their contract test and release documentation; every path must be modified rather than added, deleted, or renamed. The two dependency files are parsed from both commits and must differ only by adding the exact `adm-zip: 0.6.0` override and replacing the single locked package entry with the reviewed version, integrity, engine, and registry coordinates.
- The locked/version-CAS transaction now matches the exact old source/run/version/artifact coordinates, requires every deployment and later-state field to remain empty, atomically updates `source_sha`, clears only the invalid manifest/artifact binding, transfers run ownership, and restores the regression trigger before commit. A same-source different-run takeover is rejected.
- The focused release contract ran 85 cases: 84 passed and one Windows-only symlink-capability case was explicitly skipped. The full backend suite succeeded across 804 cases with 37 explicit environment/platform skips. A clean `npm ci --ignore-scripts` then passed typecheck, all 11 frontend unit tests, and the Production web build; the official npm audit endpoint reported zero vulnerabilities and `npm ls` proved the only transitive `adm-zip` is overridden to 0.6.0. Python compilation and `git diff --check` passed. No Production resource, domain, secret, or Proxifier setting was changed by this code repair, and no Subagent was used.

## 2026-07-18 - Self-contained Vercel deploy-root artifact

### Goal and evidence

- Protected run `29628518382`, attempt 3, successfully rebuilt and durably bound source `9aadae87ceae13d5dd65b324d8460bec88c2fb21`, then stopped before deployment when Vercel CLI reported `File does not exist: "frontend/dist/build/h5/assets/admin-D5y0mAf4.css"`. Deployment recovery had already proved there was no matching Vercel deployment, so no deployment, Promote, formal-domain switch, handoff, or terminal CAS occurred.
- Vercel CLI 56.2.0's prebuilt collector reads every function `.vc-config.json` and appends each repository-relative `filePathMap` value to the upload list. Materializing only filesystem symlinks inside `.vercel/output` therefore did not make the encrypted artifact self-contained.

### Changes and verification

- The materializer now creates one isolated deploy root containing the exact `.vercel/output`, the exact Vercel `project.json` binding, and every unique `filePathMap` file at its required repository-relative path. It rejects missing, absolute, non-canonical, out-of-source, protected-metadata, non-file, malformed-config, wrong-project, and residual-symlink inputs before hashing or upload.
- The manifest now binds the complete deploy root. The encrypted tar preserves that root as one top-level directory, recovery verifies the same complete-directory hash, and `vercel deploy --prebuilt` runs from that isolated root instead of the source checkout.
- A second one-time forward repair is restricted to the exact failed source above and modified files from the workflow, materializer, contract-test, and release-documentation allowlist. The existing exact old-source/run/version/artifact CAS and undeployed-RESERVED checks remain unchanged.
- The release contract passed 86 cases with one Windows-only symlink-capability skip. The full backend suite passed 805 cases with 37 explicit environment/platform skips. Frontend typecheck, all 11 unit tests, and the Production web build passed; the Production dependency audit reported zero vulnerabilities and the locked transitive `adm-zip` remained exactly 0.6.0. YAML parsing, Python compilation, and `git diff --check` passed.
- A local read-only Vercel reproduction initially created an empty temporary project because the detached worktree had no project binding. That exact project was immediately deleted (`204`) and a follow-up read returned `404`; no deployment or domain was attached. The subsequent reproduction used the exact existing `webdev-inspiration-hub` project binding. Proxifier was not changed or restarted, and no Subagent was used.

## 2026-07-18 - Permit only Vercel's generated Python deployment files

- Protected run `29630491616` attempt 2 proved `vercel build --prod` completed, then the deploy-root materializer rejected a generated `.vercel/...` `filePathMap` reference before artifact persistence or deployment. Vercel Python 6.50.0 creates its deployment virtual environment under `.vercel/python/.venv`, and Vercel CLI 56.2.0 records filesystem references relative to the repository root in `filePathMap`.
- The protected-metadata guard now permits regular files only beneath the two documented generated Python virtual-environment shapes: `.vercel/python/.venv/**` and `.vercel/python/services/<service>/.venv/**`. It continues to reject every other `.vercel/**` path, all `.git/**` and `.env*` paths, missing/non-file references, source escapes, and resolved symlink escapes. The deploy root still copies the exact `project.json` separately after checking the protected project and organization IDs.
- The contract fixture now includes a generated Python virtual-environment reference and proves its bytes are materialized at the exact path. The existing tests continue to prove project/env metadata and source escapes are rejected.
- The focused release contract passed all 86 cases with one Windows-only symlink-capability skip, and the full backend suite passed all 805 cases with 37 explicit environment/platform skips. Python compilation and `git diff --check` passed.
- Because attempt 1 had already atomically advanced the undeployed RESERVED source from `9aadae87ceae13d5dd65b324d8460bec88c2fb21` to `6f5ef3936a527051c8cd7d242e0d3bed581ae011` before attempt 2 exposed the generated-Python-path guard, the next forward repair is separately constrained to that exact previous source. Its complete previous-to-new diff must modify the materializer and its contract test and may otherwise modify only release documentation; the workflow, dependencies, application code, and every deployment/state field remain outside the allowlist and fail closed.
- The added forward-repair contract brought the focused release suite to 87 passing cases with one explicit Windows symlink skip and the full backend suite to 806 passing cases with 37 explicit skips. Python compilation and `git diff --check` passed again.

## 2026-07-18 - Complete the pinned Vercel Python generated-file boundary

- Protected run `29631192946` first atomically adopted the still-unbound RESERVED install from source `6f5ef3936a527051c8cd7d242e0d3bed581ae011` into exact current-main source/run `744ee54a62343cc83c36d7903fc32a719c82dbe1` and then rebuilt successfully. The materializer still stopped before encryption, artifact upload, binding, deployment, Promote, or domain handoff because Vercel also emitted a generated `.vercel/python/pycache/**` filesystem reference.
- Pinned Vercel Python 6.50.0 compiles application and dependency bytecode into `.vercel/python/pycache` and exports those regular files as `FileFsRef`; Vercel CLI 56.2.0 then records their repository-relative source paths in `filePathMap`. The protected boundary therefore permits regular files anywhere below the builder-owned `.vercel/python/**` tree, while still rejecting `.vercel/project.json`, `.vercel/.env*`, all other `.vercel/**`, `.git/**`, any `.env*` segment, missing/non-file paths, and declared or resolved escapes.
- The contract fixture now materializes both venv and pycache generated files byte-for-byte and still proves protected project metadata is rejected. A separate exact forward-repair profile is pinned to previous source `744ee54a62343cc83c36d7903fc32a719c82dbe1`; only the materializer, its contract test, and release documentation are allowed to differ.
- The focused release contract passed all 88 cases with one explicit Windows symlink skip, and the full backend suite passed all 807 cases with 37 explicit environment/platform skips. Python compilation and `git diff --check` passed.

## 2026-07-18 - Materialize only the three reviewed public env examples

- Protected run `29631559920` atomically adopted the still-unbound RESERVED install from source `744ee54a62343cc83c36d7903fc32a719c82dbe1` into exact source/run `0cf0296e90b46b5be43c91253a1f7c4e9b96f1a5`, rebuilt successfully, and still stopped before encryption, artifact persistence, binding, deployment, Promote, or domain handoff on the protected-metadata guard.
- After the complete builder-owned `.vercel/python/**` tree was permitted, the guard's only remaining reachable protected category was an `.env*` path: pinned Vercel Python 6.50.0 collects application files with `glob("**")`, while Git proves the immutable source contains exactly three tracked public examples at `.env.example`, `backend/.env.example`, and `frontend/.env.example`. The builder excludes `.git/**` and `.vercel/**` application inputs, so those three reviewed examples account for the remaining generated `filePathMap` references.
- The materializer now permits only those three exact public-example paths. `.env`, `.env.local`, `.env.production.local`, any other or nested `.env*` path, protected Vercel metadata, missing/non-file paths, and declared or resolved escapes remain rejected. Tests prove all three examples are copied byte-for-byte and an unreviewed `secrets/.env.example` still fails closed.
- A separate exact forward-repair profile is pinned to previous source `0cf0296e90b46b5be43c91253a1f7c4e9b96f1a5`; only the materializer, its contract test, and release documentation are allowed to differ.
- The focused release contract passed all 89 cases with one explicit Windows symlink skip, and the full backend suite passed all 808 cases with 37 explicit environment/platform skips. Python compilation and `git diff --check` passed.

## 2026-07-18 - Bundle the pinned Supabase CA for the Vercel runtime

### Goal and evidence

- Protected run `29633220611` built, persisted, deployed, and bound source `9868401e52024fc347bb23ad0bca98858a2901f1` as STAGED deployment `dpl_8ihCdkWzz7SsrcFhN4DFPYSWnraW`, then stopped before Promote or formal-domain handoff when its `/health` probe returned 500.
- The deployment's Vercel runtime traceback proves application startup failed in the schema-readiness query because `asyncpg` rejected the Supabase TLS chain with `SSLCertVerificationError: self-signed certificate in certificate chain`. The GitHub runner already used the checksum-pinned Supabase CA through `PGSSLROOTCERT`; the Vercel function did not contain that runner-local file.

### Changes and verification

- Added the official Supabase Root 2021 CA as a public, versioned runtime asset with SHA-256 `700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7`. The database-only TLS context uses it only for Supabase hosts when `PGSSLROOTCERT` is unset; explicit PostgreSQL CA paths still take precedence, non-Supabase TLS retains the system trust store, and CA plus hostname verification remain mandatory.
- The Vercel function explicitly includes the certificate. Focused tests prove its exact hash, Supabase-only trust scope, unchanged explicit-CA behavior, and Vercel bundle declaration.
- Added a one-use STAGED runtime-TLS recovery fence pinned to the exact failed source above. It accepts only the reviewed eight-path diff, requires the new runtime and release controller to be the same descendant main commit, records durable evidence before the database CAS, clears the invalid deployment/build binding atomically back to RESERVED, advances the source, and exits before the next attempt rebuilds. Config-repair and runtime-TLS repair modes are mutually exclusive and state-bound.
- Ten focused database, security, Vercel-bundle, diff-allowlist, preflight, CAS, and workflow tests passed. Python syntax compilation without bytecode, YAML/JSON parsing, certificate hashing, and `git diff --check` passed. The local managed read-only sandbox could not create temporary test directories, so the complete writable-runner suite remains a required CI gate before merge. No Production alias, formal domain, Proxifier setting, or customer data was changed, and no Subagent was used.

## 2026-07-18 - Preserve STAGED repair authorization across its fenced rerun

- Protected run `29635414827` attempt 1 successfully changed the exact invalid TLS deployment from STAGED to RESERVED, advanced the activation to source `be4cd9c019b7ccdd4917573b819a513f7db8ccfc`, cleared deployment `dpl_8ihCdkWzz7SsrcFhN4DFPYSWnraW`, recorded `next_workflow_attempt=2`, and intentionally exited 75.
- Attempt 2 then exposed a workflow-only guard defect: the persisted runtime-TLS repair input was rejected because the activation was now correctly `RETRY_RESERVED`, even though the activation attempt had already been fenced to the same current attempt.
- The preflight now emits a narrow `staged_rearm_retry_allowed` signal only for `RETRY_RESERVED`, an activation attempt equal to `GITHUB_RUN_ATTEMPT`, and an attempt greater than 1. Both mutually exclusive STAGED repair inputs accept that signal; first-attempt misuse and every other activation state remain rejected.
- The focused regression test passed. The release-contract module ran 94 tests: 86 passed and 8 could not create temporary directories in the managed read-only shell; no assertion failed. The writable GitHub CI suite remains the authoritative full regression gate before the next Production run.

## 2026-07-18 - Repair STAGED runtime source attestation and schema-readiness privilege

- Protected run `29636094459` reached the STAGED runtime audit with deployment `dpl_HxJ6vQjgu1RsGnbZNayhuT9VAbeB`, then failed closed because `/version` returned an empty `source_sha`. The prebuilt deploy injected `RUNTIME_BUNDLE_ID` but wrote the source only to Vercel metadata, which is not a runtime environment variable.
- The deploy now injects the immutable `SOURCE_SHA` alongside the existing runtime bundle. Deployment recovery uses the protected Vercel bypass to read each READY metadata match's `/version`; only a deployment whose runtime source, bundle, and platform deployment ID all match is reusable. The known metadata-only bad deployment is excluded, while an unreadable attestation still fails closed instead of authorizing a duplicate.
- The verifier reports a missing, malformed, or non-JSON runtime source attestation explicitly. Tests cover the deploy command, protected bypass, paginated recovery, runtime mismatch exclusion, and verifier failure paths.
- Production readiness also proved that `vowpic_app_runtime` needs read-only access to `public.alembic_version`. The reviewed database contract now grants only `SELECT` through `vowpic_runtime`, revokes direct PUBLIC/writer/anon/authenticated access, proves the runtime can read a non-empty revision, and proves both application logins have no schema-revision write privilege.
- The existing Production ACL was repaired separately under the table owner and read back before this code change. The final focused regression passed 108 cases with one Windows symlink-capability skip, and the full backend suite passed 818 cases with 37 explicit environment/platform skips. Frontend typecheck, all 11 unit tests, and the Production SaaS Web build passed; `pip check`, Python/YAML/JSON parsing, `git diff --check`, and a credential-shaped diff scan also passed.
- The local npm audit could not produce a vulnerability result: the configured mirror does not implement the audit API, and the official registry request timed out through the existing network path. No proxy or Proxifier setting was changed. The repository's GitHub CI `npm audit --omit=dev --audit-level=low` gate remains mandatory before merge, followed by the protected Production rerun and formal-domain verification.

## 2026-07-18 - Pin the prebuilt runtime's Vercel Git source coordinate

- Protected run `29640135684` attempt 2 built and durably bound manifest `0a3476e2f9a320e4fc8722f1adcca8af1b19a09d99bc5e1439457ce3f11680d6`, deployed a new READY Vercel target, and then failed closed because its public runtime identity did not match the reviewed source coordinate. No deployment was bound or promoted.
- The runtime config intentionally accepts both `VERCEL_GIT_COMMIT_SHA` and `SOURCE_SHA`, but Pydantic's alias order selects the Vercel system variable first. Vercel documents that system value as the commit which triggered the deployment; in this fenced recovery, that is the newer release controller rather than the immutable source used to build the prebuilt artifact. A local settings reproduction with both exact SHAs selected the controller SHA and reproduced the mismatch.
- The protected deploy now pins both accepted runtime aliases to the same reviewed source SHA. The workflow contract requires the Vercel-specific override exactly once, while the metadata and live `/version` recovery checks remain independent fail-closed evidence.
- The focused release contract passed 96 cases with one Windows symlink-capability skip, and the full backend suite passed 818 cases with 37 explicit environment/platform skips. Python/YAML parsing, `git diff --check`, and the credential-shaped diff scan passed.

## 2026-07-18 - Transfer one bound RESERVED build to a reviewed control-only run

- After run `29640135684` attempt 2 failed before binding a deployment, the activation remained RESERVED with the exact source, manifest, encrypted build Artifact ID/digest, approval, and empty deployment fields. PR #48 necessarily produced a newer release-controller commit, while GitHub reruns remain pinned to the workflow definition and SHA of the original run.
- The previous state classifier allowed a new run to adopt only a completely unbound RESERVED row. A same-source, different-run, bound-but-undeployed activation therefore classified as `CONFLICTING_INSTALL`, even when the cumulative source-to-controller diff passed the existing STAGED release-control allowlist.
- A new narrow `TAKEOVER_RESERVED_CONTROL` path accepts only the already-reviewed bound/undeployed RESERVED shape. It re-proves the control-only descendant and Production Sensitive runtime-secret evidence, then uses an exact version/source/run/manifest/Artifact ID/digest/approval CAS to update only run ownership, attempt, evidence reference, timestamp, and version. It cannot change or clear the source, build, runtime, deployment, promotion, or customer-data coordinates.
- Cross-run artifact recovery remains bound to the original artifact owner run, attempt, name, ID, digest, encryption associated data, and manifest. The transfer path skips the normal same-run bind operation only after all six coordinates match the preserved activation; it cannot rebuild or silently substitute an artifact.
- The focused release contract passed 99 cases with one Windows symlink-capability skip, and the expanded full backend suite passed 821 cases with 37 explicit environment/platform skips. Python/YAML parsing, `pip check`, `git diff --check`, and the credential-shaped diff scan passed.

## 2026-07-18 - Close legacy deployment recovery and same-run retry gaps

### Goal and evidence

- Protected run `29640956210` attempt 1 transferred the exact bound RESERVED build to the reviewed controller and recovered the encrypted artifact, then stopped before deployment because the prior metadata-matching deployment's `/version` attestation became unreadable. That deployment had already returned a live identity mismatch when run `29640135684` created it, but the existing metadata did not distinguish deployments created before and after the Vercel Git-source override.
- Attempt 2 remained on the same source, workflow run, manifest, and artifact, but failed before any external mutation because the historical config-repair input guard accepted only an activation attempt equal to the current GitHub attempt. The bound control takeover intentionally recorded attempt 1, while a GitHub rerun advanced to attempt 2.

### Changes and verification

- New deployments now carry `vowpicRuntimeIdentityContract=vowpic-runtime-identity-v1`, and recovery requires that marker in addition to exact source, runtime bundle, manifest, role, READY state, and live `/version` coordinates. The known pre-contract deployment is therefore excluded without trusting or deleting it; post-contract candidates still require live attestation.
- Runtime attestation performs three bounded attempts with 1- and 3-second delays. Exhaustion remains fail-closed and reports only the deployment ID, attempt count, and sanitized failure category.
- A same-run retry may retain the historical config-repair input only when the activation is still RESERVED, its recorded run matches `GITHUB_RUN_ID`, its attempt is positive and older than the current attempt, the exact canonical manifest/artifact coordinates remain bound, and all runtime/deployment coordinates remain empty. The input causes no repair mutation in this state.
- The pre-change focused release contract passed 99 cases with one Windows symlink-capability skip. Post-change, the focused contract passed 101 cases with one Windows symlink-capability skip, and the full backend suite passed 823 cases with 37 explicit environment/platform skips. Python/YAML parsing, `pip check`, `git diff --check`, and the credential-shaped diff scan passed; GitHub CI and the protected Production recovery remain required before this entry is final.

## 2026-07-18 - Recover the original owner of a database-bound build Artifact

### Goal and evidence

- Protected run `29641566605` passed 27 control steps, including the bound RESERVED takeover, least-privilege database login proof, Vercel automation bypass, and runtime-bundle computation. It then stopped before download, deploy, Promote, or domain handoff because the workflow searched for `vowpic-safe-baseline-29640956210-1-build`.
- The database-bound Artifact ID `8428394104` still exists, is unexpired until `2026-10-16`, has the exact stored digest, and is named `vowpic-safe-baseline-29640135684-2-build`. The prior control takeover changed the activation's controller run to `29640956210/1`; the workflow incorrectly reused those mutable control coordinates as the immutable Artifact owner.

### Changes and verification

- A bound build is now resolved directly through GitHub's exact Artifact-ID endpoint using the database-bound ID and digest. Its immutable name must match the safe-baseline build grammar, and the owner run encoded in that name must equal the API's `workflow_run.id`; the validated name supplies the original run and attempt needed for download and encryption associated data.
- HTTP 404 or an expired Artifact remains confirmed absence. API failures, malformed metadata, ID/digest mismatch, an invalid name, or an owner mismatch still fail closed and cannot trigger a rebuild of a manifest-bound activation.
- The old preflight outputs that aliased controller run/attempt as Artifact-owner coordinates were removed. Unbound recovery retains the existing name/run lookup; bound recovery cannot use it.
- The pre-change focused resolver and release contract passed 109 cases with one Windows symlink-capability skip. Post-change, the focused pair passed 111 cases with one Windows symlink-capability skip, and the full backend suite passed 825 cases with 37 explicit environment/platform skips. Python/YAML parsing, `pip check`, `git diff --check`, and the real GitHub metadata read for Artifact `8428394104` passed; GitHub CI and protected Production recovery remain required before this entry is final.

## 2026-07-18 - Bind staged state-changing probes to the formal Web origin

### Goal and evidence

- Protected run `29642011835` successfully recovered Artifact `8428394104`, created and attested deployment `dpl_FVkPCACPJMJPYeUCe54S7ZUfBJj8`, and recorded it as STAGED. The runtime DDL audit then stopped before Promote because `google_oauth_intent` expected the all-OFF `503` response but received `403`.
- The OAuth intent route requires an exact allowed browser Origin before evaluating the capability flag. The verifier derived that header from the temporary Vercel deployment URL, while the Production runtime permits only its configured formal Web origin. The route's rate-limit response is `429`, so the observed `403` is the fail-closed Origin boundary rather than rate limiting.

### Changes and verification

- Both audit and verification CLIs now require an explicit HTTPS request origin. Staged probes continue to target the exact deployment URL through the protected Vercel bypass but send `PRODUCTION_BASE_URL` as their Origin; formal-domain probes use the same formal URL for both coordinates. The application Origin policy and its exact-match validation were not relaxed.
- The DDL collector threads the explicit origin into the shared guarded-route verifier. The STAGED control-descendant fence now requires the collector change and permits its focused contract test, so a newer controller cannot fail after CI merely because the reviewed control-only diff contains these files.
- The pre-change focused baseline passed 5/5. Post-change, the focused request-origin and collector baseline passed 4/4, the complete release-control pair passed 105 cases with one Windows-only skip, and the full backend suite passed 826 cases with 37 explicit environment/platform skips. Python compilation, YAML parsing, CLI contract inspection, `pip check`, `git diff --check`, and a credential-shaped added-line scan passed. GitHub CI and the protected STAGED takeover remain required before promotion; the formal domain was not switched during this code repair.

## 2026-07-18 - Preserve the pre-identity Production baseline during Google probes

### Goal and evidence

- Protected run `29642556981` recovered and attested deployment `dpl_FVkPCACPJMJPYeUCe54S7ZUfBJj8`, then stopped before Promote because the staged `google_exchange` probe expected an authentication response but received `500`.
- A read-only Production inventory proved that the registered revision is intentionally still `20260712_0014`, before the identity tables and OAuth-intent schema exist. The runtime login had only `vowpic_runtime`; applying a later identity migration early would have violated the reviewed safe-baseline contract.
- The Google session-exchange route attempted to consume an OAuth intent before evaluating the globally disabled `GOOGLE_AUTH` capability. It therefore touched the intentionally absent future schema instead of returning the all-OFF baseline response.

### Changes and verification

- Google session exchange now resolves the global capability immediately after the Origin guard and returns the existing `503 capability_disabled` response before reading the OAuth cookie or touching identity persistence. Cohort evaluation still proceeds when the global capability is enabled.
- The runtime login contract now includes the empty NOLOGIN `vowpic_identity_service` group. A controlled Production transaction added only that membership and updated the existing password-rotation function so future rotations preserve it; it changed no password, customer data, domain, migration revision, or Vercel setting. The postcondition readback was `RUNTIME_IDENTITY_MEMBERSHIP_REPAIRED`, revision `20260712_0014`, memberships `vowpic_identity_service` and `vowpic_runtime`, with identity schema `ABSENT_BY_SAFE_BASELINE_CONTRACT`.
- A protected release proof verifies the exact runtime memberships and requires the identity schema to remain absent at revision `20260712_0014`. Later revisions must instead prove exact table privileges and forced RLS.
- Because the staged deployment is immutable and the application source changed, a narrowly fenced schema-compatibility rearm accepts only the exact reviewed old source and cumulative file set, atomically returns the activation from STAGED to RESERVED, and requires the same workflow run to rebuild before any promotion.
- Focused release-control regression passed 140 cases with one Windows-only skip and 253 subtests. The full backend suite passed 794 cases with 37 explicit environment/platform skips and 1,078 subtests. Python compilation, YAML parsing, dependency consistency, `git diff --check`, and a credential-shape scan passed. GitHub CI, protected rearm/rebuild, Production promotion, and formal-domain verification remain required before this repair is complete. No Subagent was used.

## 2026-07-18 - Keep login cleanup compatible with the pre-identity schema

### Goal and evidence

- Protected run `29643797578` attempt 1 atomically returned the invalid STAGED activation to an unbound RESERVED state and intentionally stopped before rebuild. Attempt 2 then stopped at login provisioning before Vercel environment publication, build, deploy, Promote, or domain mutation because PostgreSQL reported `relation "public.user_identities" does not exist`.
- The login-rotation transaction correctly preserves the safe-baseline revision `20260712_0014`, where identity tables are intentionally absent. Direct-privilege cleanup had nevertheless issued `REVOKE ALL ON TABLE` for every future identity table without first checking whether the relation existed; the exception rolled the transaction back.

### Changes and verification

- Direct-login privilege cleanup now resolves every reviewed table through `to_regclass` and skips only relations that do not exist. Existing business, control-plane, readiness, and later identity tables still receive the same exact direct-privilege revocation.
- A focused regression proves that an existing business table is revoked while the absent pre-migration `user_identities` table is not referenced by `REVOKE`. The focused database/release suite passed 116 cases with one Windows-only skip and 241 subtests; the full backend suite passed 795 cases with 37 explicit environment/platform skips and 1,078 subtests. Python compilation and `git diff --check` passed. Production retry, build, deployment, Promote, and formal-domain verification remain required. No Subagent was used.

## 2026-07-18 — Preserve the retired legacy-user contract across Vercel slash normalization

- Added a side-effect-free `POST /api/v1/users` tombstone alongside the existing
  `POST /api/v1/users/` tombstone. Production release run `29644189529` proved
  that the staged Vercel path could reach the no-slash form and return `405`,
  while the release contract requires a stable `410 legacy_user_route_retired`.
- Extended the web-only route contract so both legacy URL forms must be owned by
  the centralized retired router and return the same unauthenticated `410`
  response without resolving database dependencies.
- Verified both URL forms in-process as `410 legacy_user_route_retired`.
  After the exact rewrite was added, the directly affected release,
  risk-lockdown, feature-flag, runtime-DDL, and Vercel rewrite suites passed 70
  tests. The unchanged main baseline had already passed the full 795 backend
  tests; the clean GitHub CI suite remains the merge gate for this patch.
- The first PR Preview proved the backend alias alone was insufficient:
  `/api/v1/users` reached FastAPI while `/api/v1/users/` still fell through to
  the frontend rewrite and returned `405`. Added an exact Vercel rewrite for the
  trailing-slash legacy path before the generic API and SPA fallbacks, plus a
  regression contract for the destination and ordering.

## 2026-07-18 — Fence the one-time STAGED route-compatibility rearm

- Added a source-changing STAGED repair mode pinned to failed Production source
  `55eaeeea0748a96c7d040d9465bd64dd9bfbfd2e`. It accepts only the reviewed
  Vercel/FastAPI tombstone repair path set and verifies the two backend route
  aliases plus the exact Vercel rewrite ordering.
- The protected workflow requires an explicit route-repair input, persists
  evidence before the CAS, clears only a fully bound unpromoted STAGED record,
  advances it to the reviewed descendant in `RESERVED`, and exits `75` so only
  the next attempt can build and deploy the repaired source.
- Verified the pinned seven-path diff exactly matches the failed source through
  the reviewed repair, the workflow YAML and Python AST parse successfully, and
  the four directly affected takeover/diff/CAS/workflow tests pass. The clean
  GitHub CI suite remains the merge gate.

## 2026-07-18 — Keep cleanup paused for the SAFE_BASELINE runtime role

- PR #56 merged as `630dc1e1089ac7939fdfcb30a914bd2cb04d1771`.
  Protected run `29647174291` attempt 1 then completed the reviewed one-time
  route-compatibility CAS: it replaced the failed source `55eaeeea...` with the
  merge commit, recorded `STAGED_REARMED`, and intentionally exited `75`.
  Attempt 2 built and bound the new STAGED deployment and passed runtime
  identity membership before failing closed in the runtime DDL audit with
  `cleanup is not explicitly paused`.
- The authoritative safe-baseline contract requires the cleanup endpoint to
  remain an authenticated `503 cleanup_paused` surface even though Task 11 has
  installed the durable deletion state machine for later Preview and commercial
  release roles. The runtime route previously authenticated the cron token and
  immediately executed that later state machine without checking
  `RELEASE_ROLE`.
- Added an explicit hosted-runtime release-role fence. `SAFE_BASELINE` and any
  invalid hosted role now return `503 cleanup_paused` before retention,
  database commit, or object deletion. `PREVIEW_IDENTITY`,
  `PREVIEW_COMMERCIAL`, `COMMERCIAL_7A`, and `CONTRACT_7B` retain the durable
  cleanup path; local development behavior is unchanged.
- The new red proof failed with `HTTPException not raised`. After the fix, the
  direct role-bound checks and real ASGI HTTP contract passed 3/3; the complete
  risk-lockdown suite passed 33/33; the affected baseline-verifier and
  feature-flag route suites passed 13/13. GitHub CI and the protected
  Production rearm/rebuild remain required before this repair is complete.

## 2026-07-18 - Fence the one-time STAGED cleanup-pause rearm

- PR #57 merged the cleanup release-role fence as
  `75c2df8d371d205013edf5ab190fbf04a58ef920`. The immutable failed STAGED
  activation still records source `630dc1e1089ac7939fdfcb30a914bd2cb04d1771`,
  so the repaired application cannot replace that deployment without an
  explicit source-changing CAS.
- Added a dedicated cleanup-pause repair mode pinned to that exact previous
  source. Its cumulative diff allowlist contains only the cleanup router,
  cleanup behavior tests, this release-control script and workflow, their
  contract tests, and this worklog.
- The source validator also parses the reviewed cleanup router. It requires the
  exact post-baseline execution-role allowlist, excludes `SAFE_BASELINE`,
  requires the `cleanup_paused` failure code, and proves cron authentication and
  the release-role guard both run before retention, commit, or deletion calls.
- The protected workflow accepts one explicit cleanup-pause input, rejects
  conflicting repair modes, persists the existing durable reservation
  evidence, performs the same exact version/source/run CAS, and exits `75` so
  only the next attempt of the same run can build the reviewed source. Three
  focused tests were red before implementation and passed after it. Full
  release-control regression, GitHub CI, protected rearm/rebuild, promotion,
  and formal-domain verification remain required.

## 2026-07-18 - Resume the exact PROMOTED baseline through the protected formal audit

- Protected safe-baseline run `29649808124` attempt 2 passed the immutable build, staged deployment, runtime identity, cleanup-pause audit, staged verification, durable staged evidence, and the only Promote request. It then failed before edge handoff because the formal-domain runtime DDL collector omitted the configured Vercel automation-bypass header and observed edge `403` instead of the application-level `503` contract.
- The formal collector now uses the existing protected bypass header only while collecting the pre-handoff application audit. The final handoff remains unbypassed and must remove/read back every deny and runner-bypass rule before verifying the public formal domain.
- Added a one-time PROMOTED verifier takeover pinned to source `12d5b0f7de5a7c85adb12662790badab5b541006`. It accepts exactly five modified control/evidence files, atomically transfers only run ownership/evidence and version, preserves every deployed coordinate and the PROMOTED phase, and resumes as `RETRY_PROMOTED`; rebuild, deploy, and another Promote remain impossible on this path.
- The six directly affected tests passed, and all 67 non-temporary-file safe-baseline contract tests passed in an in-memory overlay; the remaining five filesystem tests could not create a temporary directory under this restricted client. Python AST and workflow YAML parsing passed. GitHub CI and the protected Production recovery remain required before this repair is complete. No Subagent was used.

## 2026-07-18 - Route the formal audit through the actual custom edge bypass

- Production recovery run `29669545740` proved the exact current-main checkout,
  PROMOTED ownership transfer, immutable deployment reuse, and promotion
  reconciliation, then reproduced `403` at the formal runtime-DDL collector.
  The step already sent `x-vercel-protection-bypass`; inspection of the installed
  firewall contract proved the active deny groups instead require the separate
  ephemeral `x-vowpic-release-bypass` value stored only in
  `$RUNNER_TEMP/edge-bypass-state.json`.
- The collector now accepts that Runner-private state path, rejects non-regular,
  oversized, non-private POSIX files and mismatched schema/host/header/value
  contracts, and combines both protected headers only in memory. Neither header
  is copied into its signed report or console result. The public handoff
  verifier remains bypass-free.
- The second ownership transfer is pinned to failed verifier
  `16c1ff31cb91e7c34887494c860e20a79033e7c7` and activation owner run
  `29669545740`; its cumulative source diff admits only the seven exact
  workflow/registration/collector/test/runbook/worklog files. Build, deployment,
  source, runtime identity, Promote, and phase remain immutable.
- Pre-change baseline: the three runtime-DDL collector tests and 115 release
  contract tests passed in the existing project environment. Post-change, the
  combined focused suites passed 120/120 with one existing conditional skip;
  Python compilation, workflow YAML parsing, whitespace checks, and the exact
  seven-file cumulative takeover fence passed; the complete backend discovery
  passed 846/846 with 37 existing conditional integration skips. GitHub CI,
  protected Production recovery, and no-bypass formal-domain verification are
  still required before this repair is complete. No Subagent was used.

## 2026-07-18 - Canonicalize the uploaded formal-evidence digest

- PR #60 merged as `4db0f8e2a887d20c0046d6d9ec2b680d8942c4f7`;
  all nine PR checks passed. Protected recovery run `29670063140` then passed
  the exact PROMOTED takeover, runtime/deployment reuse, promotion
  reconciliation, formal runtime-DDL audit, and public no-bypass edge handoff.
- The run stopped before `FORMAL_VERIFIED` because
  `actions/upload-artifact@v7` returned bare digest
  `<64 lowercase hex>`, while `github_artifact_evidence.py` correctly rejected
  anything other than canonical `sha256:<64 lowercase hex>`. No application,
  deployment, domain, database, or edge-handoff verification failed.
- The workflow now adds the required `sha256:` prefix exactly at that action
  output boundary. The next ownership transfer is pinned to failed verifier
  `4db0f8e2a887d20c0046d6d9ec2b680d8942c4f7` and owner run `29670063140`;
  stale owners are explicitly rejected. The runtime source, deployment,
  Promote, edge handoff, and activation phase remain immutable.
- A focused contract was red against the uncorrected workflow. Post-change, the
  three directly affected takeover/reference contracts passed, the combined
  collector/release suites passed 120/120 with one existing conditional skip,
  Python compilation, workflow YAML parsing, whitespace and the exact
  seven-file cumulative takeover fence passed, and full backend discovery
  passed 846/846 with 37 existing conditional integration skips. GitHub CI,
  protected recovery through `COMPLETED`, and final domain verification remain
  required. No Subagent was used.

## 2026-07-18 - Build the audited COMMERCIAL_7A migration toolchain

- Added canonical, detached-HMAC Production inventory contracts with freshness,
  read-only proof, source-database identity, schema/revision, relationship, and
  reconciliation checks. The verifier validates the complete inventory schema
  before any migration child can use it.
- Added durable Production parent/child migration fencing, immutable resume
  contracts, per-batch evidence revalidation, lease/fence checks, deterministic
  identity/commercial/generation/media classifications, and create-once
  sanitized reports. Failed write batches roll back without advancing their
  checkpoint.
- Added exact additive revision `20260710_0020`, bounded PostgreSQL timeouts,
  forward-fix-only failure evidence, and no automatic downgrade. Legacy
  generation facts never invent runtime provenance; unresolved identities,
  ownership, runnable jobs, or media references remain blocking.
- Added allowlisted private-media copy/switch and old-public deletion tooling
  with separate public-read/public-delete/private-write/private-read
  credentials, deterministic object binding, read-back checksums, dry-run
  binding to a caller-precommitted report digest, non-redirecting probes,
  ephemeral mode-0600 raw URL manifests, and two-location invalidation
  verification.
- Added reusable `data-migration.yml` callable only by the exact committed
  `production-release.yml` main source. It validates caller/run/source,
  immutable inventory/manifest coordinates, operation bounds, and the
  Production lease/fence before writes. Evidence upload is now gated on an
  explicit successful sanitized-report validation output.
- Focused migration tests passed 37/37 with 68 subtests; the final combined
  migration and release-control suites passed 136/136 with one existing
  conditional skip and 274 subtests; the plan's direct unittest command passed
  26/26; all 12 release entrypoints expose a credential-free help path; Python
  compilation, JavaScript syntax, workflow YAML parsing, and whitespace checks
  passed. Final full backend discovery passed 836/836 with 37 existing
  conditional integration skips and 1127 subtests.
- This task created tooling, contracts, tests, and workflow code only. It did
  not open Production credentials, execute inventory/schema/backfill/media
  migration, delete any object, probe any legacy URL, deploy, or change the
  formal domain. Those actions remain in Task 29 and require the exact final
  reviewed source/evidence chain. No Subagent was used.

## 2026-07-19 - Close the COMMERCIAL_7A evidence gaps without fabricating Production readiness

### Goal and evidence

- Rechecked the authoritative Task 29 acceptance matrix against the current
  release workflow, collectors, browser flow, provider contracts, and tests.
  The safe-baseline Production recovery is already complete; this change set
  does not rebuild, deploy, promote, change a domain, migrate Production, or
  enable a commercial capability.
- Current committed provider facts remain deliberately blocking:
  `release/provider-contracts.json` has all four contracts `UNVERIFIED`, and
  `release/worker-host-contract.json` is `NOT_APPROVED` with no executable host.
- Creem's current official documentation says one-time-payment refunds are
  performed through the Creem Dashboard. Its published API reference lists
  checkout, transaction-read, subscription, and other endpoints, but does not
  publish a refund-creation endpoint:
  `https://docs.creem.io/features/one-time-payment`,
  `https://docs.creem.io/merchant-of-record/finance/refunds-and-chargebacks`,
  and `https://docs.creem.io/api-reference/introduction`.
  Therefore a Production refund API contract cannot be invented or marked
  verified.
- EvoLink's current official image-generation contract still returns a task ID
  only after a successful `POST /v1/images/generations`, and its only published
  task lookup is `GET /v1/tasks/{task_id}`. The current request and task pages
  contain no documented idempotency key, `request_id`, or client-correlation
  lookup:
  `https://docs.evolink.ai/en/api-manual/image-series/gpt-image-2/gpt-image-2-image-generation`
  and
  `https://docs.evolink.ai/en/api-manual/task-management/get-task-detail`.
  This does not resolve the submit-success/response-loss ambiguity, so the
  Evolink contract correctly remains `UNVERIFIED`.

### Changes

- Bound browser checkout polling to VowPic's persisted purchase ID and moved
  the provider checkout coordinate to the read-only database collector. The
  browser no longer guesses a Creem checkout ID from a third-party URL shape.
- Added exact subscription evidence collection for stable paid transaction,
  invoice uniqueness, cancellation confirmation, reversal, entitlement, and
  signed test-mode anomaly/dispute facts.
- Added a signed two-stage six-case quality-review handoff. Preparation binds
  the exact source/runtime/deployment/manifest/user plus exact order/job/
  selected-candidate/final-master coordinates, expires after two hours,
  creates a zeroed draft, and stops Worker dispatch before the protected
  handoff. The reviewer submits only complete non-placeholder rubric scores;
  the protected review job keeps both signing keys inside GitHub and signs the
  exact bound draft. The final job verifies the request/review and
  candidate-to-final-master lineage before quality acceptance and only then
  restores Worker dispatch. The operator procedure is recorded in
  `docs/operations/production-quality-review.md`.
- Added a pre-effect provider-readiness gate. Production cannot build a Worker,
  migrate, deploy, or charge while any required provider contract is not
  source-bound `VERIFIED`, while Creem refund creation lacks a documented
  official endpoint, or while the Worker host contract is unapproved.
- Removed the obsolete `scripts/run_prod_generation_acceptance.mjs` runtime
  path and corrected the authoritative plan/design references to the linked
  commercial acceptance runner. Removed 16 untracked local SAFE_BASELINE
  artifact copies under `.tmp`; the authoritative protected-run artifacts and
  recorded release coordinates remain unchanged.

### Verification

- Full backend discovery:
  `python -m unittest discover -s backend/tests -t backend -p test*.py`
  passed 976 tests with 37 explicit conditional integration/platform skips
  after the final review-handoff and final-master lineage changes.
- Focused release, quality-handoff, and Web-only cleanup regression passed
  56/56; the final quality collector/runner/handoff subset passed 17/17.
- Earlier focused acceptance/provider regression passed 84/84; the provider
  and workflow hardening subset passed 67/67; the subscription/provider subset
  passed 10/10.
- Frontend unit tests passed 13/13. Frontend typecheck and Web build passed.
  The build reported only the existing Sass legacy-API and Vite CJS
  deprecation warnings.
- Python compilation, Node syntax checks, workflow YAML parsing,
  `git diff --check`, the dead-runner scan, and local `.tmp` residual scan
  passed. No credential plaintext was read, printed, or persisted.

### Honest status and next gate

- Status is **code closure in review**, not `7a release accepted` and not
  `Production accepted`.
- The three remaining external gates are: an actually approved executable
  long-running Worker host; genuine source-bound Evolink/Creem provider
  evidence including a valid refund execution path; and the authorized human
  review of the exact six generated Production cases. The current provider
  preflight stops before effects, so none of these gaps can silently produce a
  partial commercial deployment.
- The Production formal domain remains on the verified SAFE_BASELINE with
  commercial capabilities OFF. Task 30/7b must not start until Task 29 reaches
  durable `7A_ACCEPTED`. No Subagent was used.

### PR CI correction

- PR #63's first CI run exposed two paths not exercised by the earlier local
  suite: local accessibility collection imported the protected Production
  scenario before Playwright applied its skip, and the real PostgreSQL
  control-plane test still expected eight migration-owner policies after three
  new control tables raised the exact set to eleven.
- Production origin validation is now lazy inside the protected scenario, so
  unrelated local accessibility collection has no Production-environment
  dependency. The PostgreSQL assertion now checks the exact eleven-table
  policy set instead of a stale count.
- The corrected PostgreSQL integration contract passed 7/7. Frontend
  typecheck, 13 unit tests, six real Firefox accessibility routes, and 151
  focused CI/release contract tests passed with one existing Windows privilege
  skip. The local Chromium download remained unavailable because the existing
  network path timed out; GitHub's browser-install job had succeeded, so the
  pushed CI rerun remains the authoritative cross-browser result.
- The second CI run proved the frontend fix and all four real PostgreSQL
  contracts, then exposed that the final backend discovery still ran from the
  `backend/` directory and therefore could not import five new repository-root
  `scripts.release` modules. CI now discovers `backend/tests` from the
  repository root with `backend` as its package top level. The exact corrected
  discovery passed 977 tests with 37 explicit conditional skips, and a static
  contract prevents the CI working directory from regressing.
