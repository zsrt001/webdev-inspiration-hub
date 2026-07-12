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

- `.venv\Scripts\python.exe -m unittest discover -s backend/tests -t . -q`: 363/363 passed with four PostgreSQL integration cases skipped by their explicit opt-in switch.
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
- Focused runtime/lockdown/commercial suites passed 70/70; the additional valid-config/missing-lifespan fail-closed case passed 1/1. Final `.venv\Scripts\python.exe -m unittest discover -s backend/tests -t . -q` passed 375/375 with four explicitly opt-in PostgreSQL integration cases skipped.
- `git diff --check` and the follow-up high-risk secret scan remained clean before the corrective commit.

### Remaining synchronization boundary

- The corrective commit, feature-branch push, replacement Preview deployment, and live HTTP re-verification remain pending at this log entry. A successful static root plus expected liveness/readiness/fail-closed API behavior is required before PR creation may continue.
- The GitHub connector cannot create the PR (`403 Resource not accessible by integration`), and neither available browser session is signed in. PR-only CI therefore remains `NOT_RUN` until authenticated PR creation is available.
- No `main` merge, protected release dispatch, Production deployment, alias promotion, DNS/domain mutation, payment, email, Provider request, or business-data write occurred.

### Subagent

- The same read-only review subagent re-opened only the follow-up patch and independently identified the strict-default guard and CORS ordering gaps. It wrote no files and created no child agent; the primary agent reproduced both failures, added red tests, implemented the lifecycle-state correction, and reran the affected and full suites.
