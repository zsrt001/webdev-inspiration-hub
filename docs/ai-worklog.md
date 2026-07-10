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
