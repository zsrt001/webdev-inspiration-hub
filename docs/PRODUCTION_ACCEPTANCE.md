# Production acceptance status

VowPic 的**网站安全上线已完成**，但尚未达到 Production accepted；完整商业状态仍是
**`Production accepted = NOT_RUN`**。网站发布证据不能替代 Google 登录、真实生成、
Creem 受控支付或持续观察证据；Stage 5/6 商业验收仍未执行。

## 网站生产里程碑证据（2026-08-04）

- 该次里程碑的精确源码为 `e997eaac0bd3b216ea6d630296f2efe9484798ac`。对应 `main` CI
  run `30909657234` 全部成功，包含后端、真实 PostgreSQL 合同、前端类型/单测、
  Web build、浏览器无障碍和质量总门。
- 受保护的网站发布 run `30910009734` 成功；在该次复验时，正式域名
  `https://www.vowpic.com` 的 `/version` 返回同一 source SHA、deployment
  `dpl_2JvXxNivMPBDZ1ez7HmU3o4cnCYG`、role `COMMERCIAL_7A` 和 schema
  `20260710_0020`。这里的 role 是运行包类型，不表示商业验收已经通过。
- `/health/ready` 返回 HTTP 200、`ready=true`；`/api/v1/ops/readiness`
  返回 HTTP 200、`commercial_ready=true` 且没有 blocker。运行时和控制面分别以
  `vowpic_app_runtime`、`vowpic_control_writer_login` 身份连接，不使用管理员 URL。
- Production 数据库只有 revision `20260710_0020`；七个生产 capability 全部
  `OFF` 且没有 deployment/runtime/activation 绑定；迁移临时 RLS policy 已清除。
  四条历史 pending purchase 均已回填 `intent_state=UNKNOWN`，没有伪造支付终态。
- 首页、登录、注册、隐私、退款和条款页均返回 HTTP 200。公开配置中 Google、
  上传、生成、积分包、订阅、私密下载和 Partner Invite 七项能力全部关闭；支持渠道
  可用。
- 旧 partner session、Live Portrait、leads/CRM、旧 user route 和本地影楼推荐
  均在正式域名先于业务查询返回 HTTP 410，不再暴露 2026-07-16 记录的旧产品面。
- 发布后两小时的 Vercel Production 日志中没有 error 级记录，也没有 HTTP 500。

这组证据证明该网站里程碑提供的是安全的 browse-only Web SaaS 和可用 FastAPI
后端，而不是旧 Production。它没有触发 Provider POST、支付、Google 登录或用户数据
写入。

每次后续 `main` 发布后，实时状态必须重新以正式域名 `/version`、最新成功的受保护
网站发布 run、健康接口和发布后日志共同核验；不得把本节的历史 SHA 当作未来部署的
当前值。

## 当前明确暂缓的验收

用户要求先完成网站并暂不进行生图。以下项目因此保持 `NOT_RUN`，不能被解释为
当前网站部署失败，也不能被写成 PASS：

- 两套 Google 测试身份的 Preview Identity 验收；
- Preview Commercial 的真实上传、EvoLink 生成、页面轮询、QA 和私密下载；
- Creem Test/Production 的订阅、取消和退款受控验收；
- 正式域名上的真实生成主链路与最终账户清理；
- 商业能力激活、持久 observation 和商业回滚演练。

所有相关 capability 在暂缓期间保持 OFF，因此这些未执行链路没有向匿名用户暴露，
也不会阻断当前网站浏览、法律页面、健康检查或支持渠道。

## 禁止的验收捷径

- 不得用浏览器 `X-Admin-Token`、通用 Bearer、旧 OpenID、游客身份或硬编码白名单
  代替普通 Google 用户。
- 不得用 Admin generation probe 代替用户下单、计费、生成、质检和私密交付链路。
- 不得把健康检查、构建、页面打开、mock、静态样例或单一 smoke 结果写成完整商业
  生产验收通过。
- 不得在缺少真实支付、真实 Provider、真实私密存储和正式域名业务证据时生成
  `passed: true` 的商业验收报告。

## `Production accepted` 的后续门槛

只有用户恢复商业验收后，按有限生产收口计划完成以下证据，才能把状态从
`NOT_RUN` 更新为 `Production accepted`：

- 正式域名上的普通 Google 用户登录、刷新、退出、退出后拒绝和再次登录；
- 受控低额支付、账本、退款/债务抵扣、订阅及 webhook 幂等链路；
- 单人、本机双人和金婚重塑的真实上传、下单、异步生成、质量检查、私密资产和授权
  下载；
- Provider 超时/未知结果恢复、重复提交防护、失败退款和有界清理；
- 能力逐项 canary、持久观察、商业回滚证明和最终签署。

证据不得包含 token、原始邮箱、对象 key、永久 URL 或 Provider 原始敏感载荷。
