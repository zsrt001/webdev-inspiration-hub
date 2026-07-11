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
