# AI Wedding Studio

面向网页 Web / 小程序的 AI 婚纱照生成系统。

当前代码主链路已经收口为：

- 生成：`Jiekou + ComfyUI`
- 支付：`Creem`
- 主流程：`Smart Input Gatekeeper -> credits / payment -> async generation -> QA -> delivery`

## 仓库结构

- `backend/`
  - FastAPI 后端、Worker、ComfyUI 工作流、支付、风控、Leads、Admin。
- `frontend/`
  - Uni-app 前端，覆盖网页 Web 与小程序。
- `docs/`
  - PRD、实施任务清单、商用闭环说明与历史归档入口。

## 当前应优先查看的文档

- `docs/PRD.md`
- `docs/实施任务清单.md`
- `docs/实施任务清单_清洁版.md`
- `docs/商用闭环打通说明.md`
- `docs/生产预检与部署辅助.md`

## 运行前提

本仓库代码侧已基本收口，当前真正阻塞上线的主要是外部依赖与环境配置：

- `DATABASE_URL`
- `REDIS_URL`
- `COMFYUI_BASE_URL`
- 对象存储凭证
- `CREEM_*`
- `JIEKOU_API_KEY`
- 公网 `FRONTEND_BASE_URL`
- 公网 `WEBHOOK_BASE_URL`

更完整的说明见 `docs/商用闭环打通说明.md`。

## 开发说明

- 后端配置模板：`backend/.env.example`
- 商用就绪检查：`backend/scripts/check_commercial_readiness.py`
- 生产预检汇总：`backend/scripts/preflight_production.py`
- ComfyUI 工作流校验：`backend/scripts/validate_comfyui_workflows.py`

## 备注

- 部分历史文档原始版本存在编码污染，当前已通过入口页方式收口。
- 若需要追溯旧方案，请查看 Git 历史，不要继续基于污染文本维护。
