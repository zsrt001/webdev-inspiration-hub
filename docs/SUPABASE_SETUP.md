# Supabase 接入说明

本项目把 Supabase 用作两层能力：

- Postgres 数据库：存储用户、积分、订单、支付和生成记录。
- Auth 身份认证：Web 应用使用 Google PKCE；后端只在一次性应用 intent 交换中校验短暂的 Supabase access token，然后签发本地 HttpOnly Cookie 会话。

业务数据仍由 FastAPI 后端统一读写。前端不能直接写订单、积分或支付表。

## 数据库配置

在 `backend/.env` 配置 Supabase Postgres 连接：

```env
DATABASE_URL=postgresql://postgres.your-project-ref:your-password@aws-0-your-region.pooler.supabase.com:5432/postgres?sslmode=require
```

后端会自动处理：

- 将 `postgresql://` 转成 SQLAlchemy asyncpg 使用的 `postgresql+asyncpg://`。
- 将 `sslmode=require` 转成 asyncpg SSL 参数。
- 对包含特殊字符的用户名/密码做 URL 兼容处理。

验证数据库：

```powershell
cd backend
python scripts/check_supabase.py
```

也可以启动后端后访问：

```text
GET http://127.0.0.1:8001/health/ready
```

返回 `200` 且 `database.ok=true` 表示数据库链路可用。

本地单进程调试可以使用上述单一 `DATABASE_URL`。受保护 Preview 和 Production 不得复用该简化方式，必须在同一个 Supabase/PostgreSQL 项目中提供彼此不同的登录角色：

- `PREVIEW_RUNTIME_DATABASE_URL`：FastAPI 网站后端的应用运行角色；
- `PREVIEW_CONTROL_PLANE_DATABASE_URL`：受约束的 feature/release 状态写入角色；
- `PREVIEW_MIGRATION_DATABASE_URL`：仅工作流可用的 migration 管理角色；
- `PREVIEW_CONTROL_READ_DATABASE_URL`：仅用于解析不可变 release 坐标的只读角色。

运行角色和控制写入角色必须是非 owner、非 superuser、非 `BYPASSRLS`，且不能互相继承对方的固定角色组。migration URL 不得注入 Vercel API、网站后端运行环境或 Provider proof。受保护工作流会回读当前登录名、角色属性、组成员和目标数据库；角色不独立时直接失败。

## Google 登录配置

Supabase 控制台：

1. 打开 `Authentication -> Sign In / Providers -> Google`。
2. 填入 Google Cloud OAuth 的 `Client ID` 和 `Client Secret`。
3. 保持 `Skip nonce checks` 关闭。
4. 保持 `Allow users without an email` 关闭。
5. 保存并启用 Google。

Google Cloud OAuth Client：

```text
Authorized JavaScript origins:
http://127.0.0.1:3000
http://localhost:3000

Authorized redirect URIs:
https://your-project-ref.supabase.co/auth/v1/callback
```

部署到正式域名后，在 Google Cloud 追加：

```text
https://your-domain.com
```

Supabase `Authentication -> URL Configuration` 中追加：

```text
http://127.0.0.1:3000/pages/auth/callback
http://localhost:3000/pages/auth/callback
https://your-domain.com/pages/auth/callback
```

不要加入 `https://*.vercel.app/**` 或正式域名 glob。受保护的 Preview 身份验收只会临时追加本次 deployment 的精确 `/pages/auth/callback` URL；独立清理任务必须按持久化快照恢复完整 allowlist 并回读确认。

## Auth 环境变量

前端不配置 Supabase VITE 变量。后端只在 Google 能力获准时，通过 `/api/v1/ops/public_config` 返回 Supabase URL 和 publishable key；浏览器不会获得 JWT secret 或服务端管理凭据。

后端 `backend/.env`：

```env
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_ANON_KEY=your-supabase-anon-public-key
SUPABASE_JWT_SECRET=
SUPABASE_JWT_AUDIENCE=authenticated
```

后端始终调用 Supabase `/auth/v1/user` 回读当前用户，并把 broker 返回的用户、Google provider、邮箱验证状态与 JWT 的 `sub`、`session_id`、`iat`、issuer、audience 做一致性校验。项目仍使用 HS256 时，配置 `SUPABASE_JWT_SECRET` 会增加本地签名校验，但不能跳过 broker 回读。

## 本地用户映射

Google 登录成功后：

1. 浏览器先向后端申请一次性 OAuth intent，并把 intent 状态保存在当前标签页的 `sessionStorage`。
2. Supabase 完成 Google PKCE 后，浏览器只把短暂的 `access_token` 与 intent token 发送到同源 `/api/v1/auth/supabase/session`。
3. 后端验证 exact Origin、一次性浏览器绑定、broker 会话和部署绑定的首登资格；成功时按 `(provider, subject)` 映射本地身份。
4. 后端签发短时 access Cookie、可轮换 refresh Cookie 和独立 CSRF Cookie；响应体不返回本地 bearer token。
5. 浏览器丢弃临时 Supabase client 引用，后续业务 API 只使用 Cookie、exact Origin 和 CSRF，不发送 `Authorization` 用户凭据。

在兼容迁移窗口内，本地 `users.openid` 暂时使用：

```text
supabase:<supabase-user-id>
```

这只是数据库内部的只读 legacy alias，用于兼容现有订单和积分关联；它不得出现在公开 API、前端身份状态或授权判断中，并将在身份迁移与零引用验证完成后的 contract migration 中删除。

## 积分账本

新增 `credit_transactions` 作为不可变积分流水。所有积分变化都必须写流水：

- `WELCOME_BONUS`
- `PURCHASE`
- `GENERATION_DEBIT`
- `GENERATION_REFUND`
- `ADMIN_GRANT`
- `ADMIN_DEDUCT`
- `ADJUSTMENT`

`user_credits.balance` 是当前余额快照，`credit_transactions` 是对账依据。

## 表结构

数据库结构由 Alembic migration 管理。应用启动和普通 API 请求不得执行 `create_all`、`ALTER TABLE` 或其他运行时 DDL；部署前必须核对当前 revision 与目标 migration 集合。
