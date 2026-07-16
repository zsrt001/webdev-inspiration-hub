# VowPic

VowPic 是面向海外用户的响应式 Web SaaS，用于生成、购买和私密交付 AI 婚纱影像。

当前仓库正在按已批准的商业闭环设计分阶段加固。编译成功、测试通过或页面可打开都不等于已经完成生产验收；只有受保护的发布流程、真实服务集成和正式域名验收全部通过后，才能标记为 `Production accepted`。

## 产品边界

- 公开身份入口只使用 Google OAuth（由 Supabase Auth 承接）。
- 当前公开创作模式为单人、本机双人和金婚重塑。
- 积分、支付、异步生成、质量检查和交付由 FastAPI 后端统一控制。
- 已退役的公开 API 由一个无副作用的 tombstone router 明确返回 HTTP 410。
- 不提供微信生态版本、游客账户、公开密码登录、匿名远程合拍、Live Portrait、本地影楼推荐或线索 CRM。

## 技术结构

- `frontend/`：Vue 3 + Uni-app 的浏览器前端。
- `backend/`：FastAPI API、PostgreSQL/Alembic、积分账本、Creem 支付、Redis/ARQ Worker、生成与质量检查服务。
- `docs/`：当前 PRD、获批设计、分阶段实施计划、运行手册和工程留痕。

`frontend/package.json` 中的 `uni -p h5` 是 Uni-app 固定的 Web 编译目标标识，不代表产品定位为“H5 项目”，也不引入独立移动端产品。

## 权威文档

- 当前产品需求：`docs/PRD.md`
- 获批商业闭环设计：`docs/superpowers/specs/2026-07-10-vowpic-commercial-closure-design.md`
- 分阶段实施计划：`docs/superpowers/plans/2026-07-10-vowpic-commercial-closure-implementation.md`
- 当前架构：`docs/ARCHITECTURE.md`
- 安全边界：`docs/SECURITY.md`
- 运维与发布：`docs/OPERATIONS_RUNBOOK.md`
- 风险锁定运行手册：`docs/operations/risk-lockdown-runbook.md`
- 工程留痕：`docs/ai-worklog.md`

旧版 PRD、部署说明和历史工作日志只用于追溯，不得覆盖上述当前合同。

## 本地验证

后端（先进入 `backend`，以便 `app` 包按项目约定解析）：

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

前端：

```powershell
Set-Location frontend
npm ci --ignore-scripts
npm run typecheck
npm run test:unit
npm run build:web
npm run test:a11y
```

Vitest 单元/组件测试和基于真实 Web 构建的 Playwright 无障碍测试都是 CI 必跑项。受保护 Preview 的 Google、私有媒体、Provider、支付和完整浏览器主流程仍需使用隔离资源单独验收；本地通过不能替代这些外部证据。

## 配置与发布

- 后端配置模板：`backend/.env.example`
- Supabase/Google 身份配置：`docs/SUPABASE_SETUP.md`
- 生产验收状态与未达门槛：`docs/PRODUCTION_ACCEPTANCE.md`
- 安全部署与回滚步骤：`docs/operations/risk-lockdown-runbook.md`

不要仅凭本地 `.env`、构建产物或历史部署记录推断正式环境状态；生产结论必须绑定精确 source SHA、部署、数据库 revision、Worker 制品和验收证据。
