# AI Wedding Studio 3.0 技术手册（历史归档入口）

> 状态：历史归档说明  
> 说明：原始版本属于早期旗舰版技术手册，部分技术描述已经过时，不再作为当前运行与部署依据。

---

## 当前应优先查看

1. `docs/商用闭环打通说明.md`
   - 当前运行前提、环境变量、严格就绪检查与上线要求。
2. `docs/ComfyUI_生产依赖清单.md`
   - 当前 ComfyUI 生产工作流、节点依赖、模型与校验脚本。
3. `docs/实施任务清单_清洁版.md`
   - 当前阶段完成度、遗留问题与外部阻塞项。
4. `docs/PRD.md`
   - 功能目标、优先级与产品闭环定义。

---

## 原手册为什么失效

- 原文默认引用了旧链路：
  - `OpenRouter`
  - `Replicate`
  - `backend/app/services/ai_service.py`
- 当前仓库默认主链路已经收口为：
  - `Jiekou + ComfyUI`
  - `Creem`
- 旧手册里的部分调优建议、故障排查路径和代码入口已经不再对应当前代码。

---

## 当前技术判断

- 代码侧主链路已经基本收口。
- 当前上线阻塞主要是：
  - `DATABASE_URL`
  - `REDIS_URL`
  - `COMFYUI_BASE_URL`
  - 对象存储凭证
  - `CREEM_*`
  - `JIEKOU_API_KEY`
  - 公网 `FRONTEND_BASE_URL`
  - 公网 `WEBHOOK_BASE_URL`

---

## 维护原则

- 不再继续修补旧手册内容。
- 后续若需要新的技术手册，应直接基于当前主链路重写，而不是在本文件上追加历史兼容说明。
