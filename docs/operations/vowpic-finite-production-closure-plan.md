# VowPic 有限生产收口执行计划

状态：`LOCKED_FOR_EXECUTION`

更新日期：2026-07-23

## 1. 唯一目标

恢复并验证 VowPic 海外 SaaS 网站的真实生产链路：

`浏览器 → VowPic FastAPI 网站后端 → EvoLink → QA/结算/私有资产 → 用户下载`

本计划不引入 Railway、独立托管 Worker、Redis/ARQ 生成队列、微信、微信小程序或独立 H5 产品。

## 2. 不可变边界

1. EvoLink 密钥和调用只存在于 FastAPI 网站后端。
2. PostgreSQL 中的订单、尝试、任务、租约、结算和交付事实是唯一业务事实源。
3. Redis 只允许保留现有可选缓存/会话用途，不得成为生成或发布前提。
4. Preview 使用 Creem Test Mode；自动验收不执行真实扣款。
5. 复用现有 Supabase、Storage/Private Blob、Google 测试账号、EvoLink、Creem、GitHub 和 Vercel；只有实时库存明确证明缺失时才允许补建，不凭旧记录重复创建。
6. Production runtime、control、observation 使用专用最小权限数据库登录，不用旧管理员 URL 冒充。
7. R1 本地测试、完整 diff 复核和 R2 独立复审必须早于 push、PR 和 merge；受保护的 Preview Identity/Commercial 属于 R5，只能在 merge 后对唯一 merge SHA 执行，并且必须早于 Production。
8. 不修改、不重启 Proxifier。
9. 不打印、保存或提交 Secret、连接串、Cookie、浏览器登录态。
10. 同一根因连续两次失败且没有新证据时停止该路径，重新诊断，禁止盲目重试。

## 3. 当前已实现但尚未发布的本地改动

以下状态只表示当前工作树已有实现和直接测试，不表示 Production 已完成：

- `IMPLEMENTED_LOCAL`：网站后端直接提交 EvoLink，浏览器不直接调用 Provider。
- `IMPLEMENTED_LOCAL`：Provider 回调、订单进度和五分钟 GitHub 触发器都只调用 VowPic 后端，由后端完成对账、下载、QA、结算和交付。
- `IMPLEMENTED_LOCAL`：`SUBMITTING` 崩溃恢复、模糊网络错误、跨部署接管和回调先到竞态已收口。
- `IMPLEMENTED_LOCAL`：Production/observation 回滚改为使用签名的 SAFE_BASELINE 源码、运行时、部署和 schema 坐标，不再用目标坐标验证旧部署。
- `IMPLEMENTED_LOCAL`：缺少 observation 只读凭据时失败，不再返回绿色 no-op。
- `IMPLEMENTED_LOCAL`：Preview 必须证明与 Production 是不同 Supabase 项目，四类数据库凭据必须连接同一 Preview 数据库并满足预期角色。
- `IMPLEMENTED_LOCAL`：Preview 的真实 EvoLink 演练使用绑定精确 Vercel deployment 的专用回调域名；提交器不保存响应中的 task ID，只允许签名回调恢复 UNKNOWN，且 Stage-5 必须证明一次提交、一次 Provider 读取、回调绑定和终态清理。
- `IMPLEMENTED_LOCAL`：Production 会重新验证指定 Preview workflow run/attempt 的 GitHub 成功状态，重新哈希下载包中的每个 Gate 证据，并同时绑定 activation、runtime bundle、deployment 和 manifest；同一 source SHA 的其他运行不能被误选。
- `IMPLEMENTED_LOCAL`：Railway、独立 Worker、ARQ/Redis 生成队列、runtime-drain 残留及分叉运行时合同已从活动代码清理。

这些改动只有通过第 4 节全部门禁并合并到 main 后，才获得可发布资格。

## 4. 剩余项与固定执行顺序

| 阶段 | 当前状态 | 只允许执行的动作 | 退出证据 |
| --- | --- | --- | --- |
| R1 本地完整验证 | `LOCAL_VERIFIED_CI_INTEGRATIONS_PENDING` | 对当前代码重跑后端全套、前端 typecheck/unit/Web build、关键 E2E、契约和 diff 检查；本地没有真实 PostgreSQL/Private Storage/Creem 受保护凭据的项目明确记为 `NOT_RUN`，由 PR CI 与 Preview 强制执行 | 当前后端 `1060 passed, 41 skipped, 1484 subtests`；41 项逐项证明为外部集成 `NOT_RUN`，不冒充 PASS；前端 typecheck、25 个 unit、Web build、Chromium/Firefox 共 14 个主链路与 a11y 测试通过；OpenAPI 确定性快照和生成类型一致；`.tmp/` 仅保留不提交的当前任务合同 |
| R3A Preview 非 Google 实时库存 | `COMPLETED` | 只读回验 GitHub/Vercel/Supabase/Creem/EvoLink 当前元数据；复用现有 Preview 项目、Private Blob、四类数据库登录、Creem Test 和 EvoLink 配置 | 不读取 Secret 明文；Preview/Production ref 不同；四类 DB 登录、Blob、EvoLink 和 Creem Test 配置均存在并通过只读/最小权限证明 |
| R2 最终独立复审 | `IN_PROGRESS` | 向独立 reviewer 提供用户原始约束、当前完整 diff、最新测试证据和验收清单；发现项只修对应根因，行为写入后旧复审立即失效并重审 | 最新独立 reviewer 对当前最终哈希给出 P0=0、P1=0、P2=0、P3=0；该结论后未发生行为写入 |
| R4 PR/CI 与受控合并 | `PENDING_R2` | push 修复分支；创建 PR；等待 CI；最终 diff 复核；只读确认仓库配置和 Vercel 项目当前都禁止 `main` Git 自动 Production 部署；然后合并 main | PR CI 通过；最终 diff 与已复审内容一致；Vercel 自动 Production 部署禁止证据当前有效；取得唯一 merge SHA，且合并本身未部署 Production |
| R3B 最终 Google Preview 身份门 | `PENDING_R4_CI` | 只有代码、独立复审和 PR CI 全部无问题后，才接入两套现有 Google 测试账号；可由隔离自动化安全生成 storage state 并写入受保护 Secret，或由用户本人按最小清单完成 | 两个账号 subject/email 均不同；两个 Preview 环境仅新增同一组 5 个 Google Secret；不读取或持久化密码/2FA，不把 Cookie/令牌写入仓库或日志 |
| R5 Preview Identity/Commercial | `PENDING_R3B` | 对 R4 的唯一 merge SHA 运行 Integration；Identity 完成后再运行 Commercial；至少一单必须从真实创建页完成 Google 登录态、私有照片上传、幂等提交、预览页轮询、READY 展示和页面下载，禁止只用 API helper 替代页面验收 | GitHub run `completed/success` 且 head branch=`main`、head SHA=merge SHA；所有 Preview release Gate 当前 PASS；Creem Test 支付、订阅、取消、退款、真实生成、页面下载、导出、删除、Partner Invite 和可访问性证据完整；cleanup=`CLEANED` |
| R6 Production 凭据前置与工作流内数据门 | `PENDING_R5` | 触发前只读核验并补齐 Production workflow 所需的最小权限凭据、库存和恢复演练输入；随后用同一 merge SHA 与 R5 的 Preview run/attempt 触发唯一 `production-release.yml`。schema、identity、commercial、generation、media backfill/约束和迁移写入只能由该 workflow 内部调用 `data-migration.yml` 执行，不能在工作流外另起一条迁移路径 | workflow 内库存和恢复报告通过；revision 精确为 0020；runtime/control/observation 角色证明通过；无管理员 URL 替代；所有数据门完成后才进入 staged acceptance |
| R7 同一 Production 工作流的验收、正式域名与观察 | `PENDING_R6` | R6 数据门通过后继续同一次 Production workflow 的 staged acceptance、人工质量门和 Promote；验证正式域名版本坐标；从真实页面执行 Google 登录、隔离上传、幂等下单、EvoLink 生成、QA、READY 展示、私有下载、账本和删除；正式激活后先有界恢复 legacy generation work，再对旧 generation/payment outbox 做只读库存和受控退休，随后持续观察并验证回滚基线 | Production 与正式域名 source SHA=merge SHA；真实页面主链路全部通过；已由底层权威状态证明处理的旧 envelope 幂等审计退休，未知/冲突保持阻断，退休后 mandatory outbox active_count=0；观察到终态；回滚报告绑定签名 SAFE_BASELINE；无 P0/P1/P2/P3 残留 |

## 5. R1 验证矩阵

R1 必须覆盖：

1. 后端完整 pytest/unittest；明确区分通过、跳过和未运行。
2. PostgreSQL 集成测试；本机缺少数据库时不得把 skip 当 PASS，CI 必须执行适用的真实数据库门。
3. 前端 typecheck、unit、Web build。
4. Preview/Production/release-observation/generation-recovery YAML 解析。
5. Python 编译、JSON 解析、OpenAPI 一致性。
6. 后端 EvoLink 单次提交、未知状态不重放、回调竞态、租约 fencing、崩溃恢复。
7. Preview 项目隔离、最小权限登录、证据逐文件重新哈希。
8. Production 目标/回滚基线坐标分离、缺失凭据 fail-closed、正式域名版本绑定。
9. Railway、独立 Worker、ARQ、Redis 生成队列及重复运行时合同的活动引用扫描。
10. Secret/连接串/临时文件/浏览器状态不进入 diff。

任何失败只能修对应根因；不得删除门禁、放宽断言、用 mock 代替要求的真实验收或新增架构。

## 6. R3 配置处理规则

1. 先查询当前状态，再决定是否需要写入。
2. 现有资源可用时直接复用；不因旧计划写着“缺失”而重建。
3. Preview Supabase 证明必须同时满足：
   - 数据库 URL 中的 project ref 等于当前 Preview ref；
   - Preview ref 不等于 Production ref；
   - migration/runtime/control writer/control reader 使用四个独立 scoped login；
   - 四个连接实际落到同一数据库和服务器；
   - control reader 默认只读；
   - 所有登录均非 superuser、非 BYPASSRLS、非 CREATEDB/CREATEROLE。
4. Creem 只验证 Test API、Test webhook 和两个 Test 产品；不得切换 live charge。
5. EvoLink 只需要现有生图 API Key、base URL、模型和真实 Provider 证据，不要求平台提供额外“幂等合同”文件。
6. GitHub/Vercel 只写 Secret 名称对应的受保护值，不回显内容。

### 6.1 2026-07-23 实时库存结论

以下结论来自当前 GitHub/Vercel 元数据、GitHub Actions 日志和正式域名只读接口，不来自旧记录：

1. 正式域名当前仍运行已知 `SAFE_BASELINE`，主页、版本接口和严格健康检查均为 HTTP 200；商业能力开关保持 OFF，因此当前没有因本次未合并改动造成生产事故。
2. Production 已存在 runtime/control 专用数据库登录、Private Blob、Creem、EvoLink、Supabase、Vercel 等大部分受保护配置；不得重建这些资源。
3. 当前 Production 数据库尚无 `release_observation_recoveries` 表，导致 observation 登录创建流程失败；必须在 R6 通过受控迁移补齐 schema 后再创建 observation 登录。
4. `preview-identity` 需要 17 个 Secret 和 2 个变量；当前可用 12 个 Secret、2 个变量，只缺两套 Google 测试身份对应的 5 个 Secret，不缺变量。
5. `preview-commercial` 需要 26 个 Secret 和 5 个变量；当前可用 21 个 Secret、5 个变量，只缺同一组 5 个 Google Secret。`PREVIEW_PROVIDER_OWNER_USER_ID` 已改为由 Identity 成功证据和最小权限 control reader 自动解析，不再是手工 Secret。
6. Creem Test Mode、两个 Test 产品、专用 Test API Key 和 Test webhook 已现场验证并写入 `preview-commercial`；EvoLink 现有生图 Key 已通过官方 `credits` 与 `models` 只读接口验证；新建 Vercel Private Blob 已限定为 Preview 并写入两个 Preview 环境。
7. Supabase 项目 `wmgtpmonuzhkfyxsqspx` 的实际名称为 `ai山海/爱山海`，不是 VowPic Preview；未向该项目写入 VowPic 数据，并已恢复到本次核验前的暂停状态。
8. 已创建专用 VowPic Preview Supabase 项目 `zyrxfcdqszfmkkkicgqq`，与 Production `ucqdgdjituqzzsnfprqd` 明确隔离。Preview 已受控迁移到 `20260710_0020`；migration/runtime/control writer/control reader 四个独立 scoped login 已通过同库、同服务器、非 superuser、非 BYPASSRLS、非 CREATEDB/CREATEROLE 和 reader 默认只读证明。47 张业务表、8 张身份表、67 张 RLS 表及身份序列的权限面均已验证，四条连接串已写入两个 Preview 环境，短期 DPAPI 交接文件已删除。
9. 已创建团队作用域、1 年到期的 Vercel 专用访问令牌并写入两个 Preview 环境；已创建 Preview 专用保护绕过密钥并设为 Vercel 系统环境变量。诊断中进入工具输出的两条旧绕过密钥已撤销，Production 绕过密钥已轮换并同步更新 GitHub `production` 环境；Vercel 当前只保留一条 Preview 和一条 Production 新密钥。
10. Google 测试账号按用户要求延期到 R4 CI 全绿后的最后身份门。先前启动的隔离临时 Edge 会话已停止，进程、临时 profile、storage state、状态文件和采集器均已清理，尚未写入任何 Google Secret。后续仍禁止读取浏览器本地 Cookie/会话数据库或用空 storage state 冒充真实会话。
11. Vercel `gitProviderOptions.createDeployments` 已由 `enabled` 受控改为 `disabled` 并只读回验；merge 不会触发 Vercel Git 自动部署。GitHub `main` 由 active ruleset `Protect main release path` 保护：禁止删除和非快进，要求 PR、讨论解决，并以 strict `quality-gate` 为必需检查。
12. 远端尚无任何 Integration 或 Manual Production release 成功运行；不得把本地测试或旧 SAFE_BASELINE 当作 R5/R7 验收。
13. 两个 Preview workflow 会在运行时通过 Supabase Management API 只读解析唯一 publishable/legacy anon 公钥并注入 `SUPABASE_URL` 与 `SUPABASE_ANON_KEY`；secret/service-role/歧义库存一律 fail-closed，不再依赖手工复制公开 key。

### 6.2 精确补齐清单

`preview-identity` 只补以下缺失项：

- Secret：`PREVIEW_GOOGLE_EMAIL`、`PREVIEW_GOOGLE_STORAGE_STATE_B64`、`PREVIEW_GOOGLE_SUBJECTS_B64`、`PREVIEW_SECOND_GOOGLE_EMAIL`、`PREVIEW_SECOND_GOOGLE_STORAGE_STATE_B64`。
- Variable：无。

`preview-commercial` 在复用已验证的同一 Preview 资源后，只补以下缺失项：

- Secret：`PREVIEW_GOOGLE_EMAIL`、`PREVIEW_GOOGLE_STORAGE_STATE_B64`、`PREVIEW_GOOGLE_SUBJECTS_B64`、`PREVIEW_SECOND_GOOGLE_EMAIL`、`PREVIEW_SECOND_GOOGLE_STORAGE_STATE_B64`。
- Variable：无。

Commercial 的 provider owner UUID 在 Identity 成功后由工作流使用 `PREVIEW_CONTROL_READ_DATABASE_URL` 从精确 activation/binding 自动解析，只把 UUID 写入当前 job 环境，不再维护跨运行 Secret。

Production 只补当前工作流真实引用但环境中缺失的项；其中数据库凭据必须在 R6 由最小权限角色生成，不复制管理员 URL：

- `production-observation`：`CLEANUP_CRON_TOKEN`、`OBSERVATION_READ_DATABASE_URL`、`OBSERVATION_WRITE_DATABASE_URL`、`RELEASE_EVIDENCE_HMAC_KEY`。
- `production-observation-emergency`：`OBSERVATION_EMERGENCY_DATABASE_URL`。
- `production-recovery`：`PRODUCTION_ACCEPTANCE_APPROVAL_ID`、`PRODUCTION_MIGRATION_DATABASE_URL`、`VERCEL_TOKEN`。
- `production` 的 Google 验收态、EvoLink、Supabase 管理令牌和支持渠道缺项在 R6 写入前逐项从现有平台验证；无法证明现有值时保持商业能力 OFF，不用占位符或 Production 管理员凭据替代。

### 6.3 R3/R2/R4 一次性执行顺序

以下完成项不重跑、不重建、不轮换：专用 VowPic Preview 项目、0020 迁移、四类最小权限数据库登录、Vercel 自动化令牌、Preview/Production 保护绕过密钥、Vercel Git 自动部署禁用和 GitHub `main` ruleset。后续只允许只读回验这些状态。

1. 对当前代码执行最终 R1 全量证据复核，包括真实创建页→预览页→下载的本地浏览器合同测试；任何产品代码写入都会使这一步失效并重跑。
2. 完成 `preview-identity`、`preview-commercial`、Preview 项目 ref、四类数据库角色、Vercel Git 自动部署禁用和 GitHub ruleset 的只读盘点；不重复创建、迁移、轮换或写入已正确状态。
3. 由独立 reviewer 对当前最终哈希执行 R2；只有 P0=0、P1=0、P2=0、P3=0 才进入 Git。
4. 提交 PR 并等待完整 CI；CI 未通过不合并。合并前最后一次只读确认 Vercel Git 自动部署仍为 `disabled`、GitHub `main` 必需检查仍生效。
5. 取得唯一 merge SHA 后，才执行两套现有 Google 测试身份的最后安全接入，把 6.2 的 5 个 Secret 写入两个 Preview 环境；不读取或持久化密码、2FA、Cookie 或令牌明文。用户可选择本人完成。
6. Google 身份门通过后，严格执行 R5→R6→R7；任何失败都停在该 Gate，只修该根因，不扩架构、不重复 EvoLink 提交。

## 7. R4-R7 发布顺序

```text
最新本地全量验证
→ 当前外部库存与受保护配置核验
→ 对最终稳定哈希执行独立复审
→ push 修复分支
→ PR CI
→ 最终 diff 复核
→ 当前仓库与 Vercel 项目均证明 main Git 自动 Production 部署已禁用
→ merge main
→ 最后接入或由用户本人验收两套 Google 测试身份
→ 对同一 merge SHA 运行并验证 Preview Identity/Commercial（含真实创建页→预览页→下载）
→ 只读核验并补齐 Production workflow 所需的最小权限凭据、库存和恢复演练输入
→ 以同一 merge SHA 与该 Preview run/attempt 触发唯一 Production workflow
→ 在该 workflow 内依次完成 R6 数据门
→ 继续同一 workflow 的 R7 staged acceptance 和 Promote
→ 正式域名 source/runtime/deployment 校验
→ 正式域名真实页面 SaaS 主链路
→ observation
→ SAFE_BASELINE rollback 证明
→ 最终残留审计
```

任一步失败：

- 后续能力保持 OFF；
- 不重复 Provider POST；
- 不重新 Promote；
- 只回滚到已经签名验证的 SAFE_BASELINE；
- 修复该步根因后，从其最近的耐久检查点继续。

## 8. 完成定义

只有同时满足以下条件，任务才可标记完成：

1. 当前完整 diff 经过最新全量回归和独立复审，0 个 P0/P1/P2/P3。
2. Preview Identity/Commercial 的当前 GitHub run 成功且证据逐文件复核通过。
3. Production 数据库、最小权限登录、迁移和恢复演练当前通过。
4. PR 已合并，Production 与正式域名服务同一 merge SHA。
5. 正式域名真实 SaaS 主链路当前通过。
6. observation 和 SAFE_BASELINE rollback 证据当前通过。
7. 最终活动引用、Secret、临时文件和未完成状态扫描无阻断项。

在此之前只能报告实际阶段状态，不使用“已上线”“Production 完成”或“无风险遗留”。
