# Production acceptance status

VowPic **尚未达到 Production accepted**。仓库已经实现安全基线、受保护 Preview 和分层证据工具，但真实 Stage 5/6 验收尚未执行；工具存在不能替代正式域名上的真实商业主链路证据。

## 当前可验证范围

- PR CI 已验证锁定依赖、PostgreSQL migration/RLS/并发、后端、OpenAPI、前端类型/单测/真实 Web 构建/无障碍和 Worker 镜像。
- `safe-baseline-release.yml` 已实现生产安全基线、全能力关闭、部署坐标、零运行时 DDL 和无业务数据副作用的受保护验证合同；没有对应的受保护运行证据时状态仍是 `NOT_RUN`。
- `integration.yml` 已实现真实 Google PKCE、私有媒体、隔离 Worker/Redis、Provider fetch、运行时绑定和取消安全清理；当前 GitHub 环境仍缺少隔离数据库角色、Google 状态、私有存储、Redis、Vercel、Supabase 和 Provider secret，因此 Stage 5 是 `NOT_RUN`。
- 普通 Vercel Preview 只证明 browse-only Web/API 构建可部署，不得替代受保护 Preview。

以上发布流程都必须绑定精确 source SHA、runtime bundle、Vercel deployment、数据库 revision 和 create-once 证据。Preview 验收完成后必须恢复 Supabase 精确回调、关闭全部能力并清理验收业务数据。

## 当前正式域名风险状态

2026-07-16 对 `https://www.vowpic.com` 的只读 GET 核验确认，正式域名仍由旧 Production deployment 提供服务，并且与当前 Web SaaS 安全合同不一致：

- `/api/v1/ops/config` 仍公开报告 `remote_join=true`、`local_recommendations=true` 和 `director_mode=true`。
- `/api/v1/session/{id}/status`、`/api/v1/live_portrait/list`、`/api/v1/leads/list`、`/api/v1/leads/export.csv` 和 `/api/v1/users/{id}` 没有命中当前先于认证和业务查询返回 `410 Gone` 的永久墓碑合同。
- `/api/v1/recommendations/local_studios` 可匿名返回三条已退役的本地影楼推荐记录，而当前产品明确不提供本地影楼推荐。
- `/api/v1/ops/readiness` 仍按旧逻辑报告 `commercial_ready=true`；该结果没有当前 source/runtime、受保护 Preview、私有存储、隔离 Redis、Provider、支付或正式证据绑定，不能视为发布就绪。

这不是当前 `main` 的实现缺口；当前代码和测试已覆盖对应 `410` 墓碑及能力关闭合同。风险来自旧 Production 尚未通过受保护安全基线流程替换。临时 Vercel Firewall 锁定和正式发布都属于外部项目状态变更；在实际锁定、读回、签名留证以及受保护发布完成前，Production 必须标记为 **存在已确认暴露、未 accepted**，不得以普通 Preview Promote 代替。

## 禁止的验收捷径

- 不得用浏览器 `X-Admin-Token`、通用 Bearer、旧 OpenID、游客身份或硬编码白名单代替普通 Google 用户。
- 不得用 Admin generation probe 代替用户下单、计费、生成、质检和私密交付链路。
- 不得把健康检查、构建、页面打开、mock、静态样例或单一 smoke 结果写成生产验收通过。
- 不得在缺少真实支付、真实 Provider、真实私密存储和正式域名证据时生成 `passed: true` 的商业验收报告。

## Production accepted 的后续门槛

只有获批实施计划中的生产发布与验收阶段完成后，才能更新本文件并执行正式验收。至少需要：

- 正式域名上的普通 Google 用户登录、刷新、退出、退出后拒绝和再次登录。
- 真实低额支付、账本、退款/债务抵扣、订阅及 webhook 幂等链路。
- 单人、本机双人和金婚重塑的真实上传、下单、异步生成、质量检查、私密资产和授权下载。
- Provider 超时/未知结果恢复、重复提交防护、失败退款和有界清理。
- 数据迁移、回滚基线、正式域名切换、能力逐项 canary、至少 24 小时的持久观察和最终签署。
- 证据不得包含 token、原始邮箱、对象 key、永久 URL 或 Provider 原始敏感载荷。

在上述合同尚未实现和实跑前，任何生产验收命令都应视为不存在，而不是使用旧脚本或旧凭据凑出绿色结果。
