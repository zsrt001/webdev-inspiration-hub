# Production acceptance status

VowPic **尚未达到 Production accepted**。当前已实现的安全基线和 Preview 身份验收只证明受控范围内的工程合同，不能替代正式域名上的真实商业主链路验收。

## 当前可验证范围

- `safe-baseline-release.yml` 验证生产安全基线、全能力关闭、部署坐标、零运行时 DDL 和无业务数据副作用。
- `integration.yml` 在受保护的 Vercel Preview 上验证一次真实 Google PKCE 登录、本地 Cookie 会话、刷新轮换、退出和独立清理。
- 本地单元、集成和构建检查只能作为前置证据，不能把项目标记为生产可用。

以上两条发布流程都必须绑定精确 source SHA、runtime bundle、Vercel deployment、数据库 revision 和 create-once 证据。Preview 身份验收完成后必须恢复 Supabase 精确回调、关闭全部能力并清理验收业务数据。

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
