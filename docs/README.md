# 文档索引

VowPic 是海外响应式 Web SaaS。当前执行只以以下文档为准：

1. `docs/PRD.md`：产品范围、用户流程和验收口径。
2. `docs/operations/vowpic-finite-production-closure-plan.md`：当前唯一有限收口顺序、边界和退出证据。
3. `docs/ARCHITECTURE.md`：Web、FastAPI 网站后端执行器、数据和外部服务边界。
4. `docs/SECURITY.md`：身份、权限、媒体、支付、Provider 和密钥边界。
5. `docs/OPERATIONS_RUNBOOK.md`：Preview、发布、迁移、回滚和事故处理。
6. `docs/PRODUCTION_ACCEPTANCE.md`：当前生产验收状态和证据要求。
7. `docs/operations/risk-lockdown-runbook.md`：安全基线和受保护发布细节。
8. `docs/ai-worklog.md`：已经执行的修改、验证结果和外部 `NOT_RUN` 项。

`docs/superpowers/specs/2026-07-10-vowpic-commercial-closure-design.md` 和
`docs/superpowers/plans/2026-07-10-vowpic-commercial-closure-implementation.md`
是已被有限收口计划取代的历史审计资料，只用于追溯，不能提供当前命令、配置或 PASS 证据。未运行的真实外部链路必须保持 `NOT_RUN`；本地测试、普通 Vercel Preview 或 UI 可打开都不等于 `Production accepted`。
