# Supabase 接入说明

本项目把 Supabase 用作两层能力：

- Postgres 数据库：存储用户、积分、订单、支付和生成记录。
- Auth 身份认证：H5 使用 Google OAuth 登录，后端校验 Supabase access token 后映射成本地业务用户。

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
http://localhost:3000/**
https://your-domain.com/**
https://*.vercel.app/**
```

## Auth 环境变量

前端 `frontend/.env.local`：

```env
Frontend Supabase VITE variables are intentionally not used. Keep API keys on the backend.
```

后端 `backend/.env`：

```env
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_ANON_KEY=your-supabase-anon-public-key
SUPABASE_JWT_SECRET=
SUPABASE_JWT_AUDIENCE=authenticated
```

后端支持两种校验方式：

- 配置 `SUPABASE_JWT_SECRET`：后端本地校验 HS256 JWT。
- 不配置 `SUPABASE_JWT_SECRET`，但配置 `SUPABASE_ANON_KEY`：后端调用 Supabase `/auth/v1/user` 验证 token。

第一版建议用 `SUPABASE_ANON_KEY`，配置简单；后续生产环境可再切到 JWT secret 或 JWKS 方案。

## 本地用户映射

Google 登录成功后：

1. 前端拿到 Supabase `access_token`。
2. 请求业务 API 时带 `Authorization: Bearer <access_token>`。
3. 后端校验 token。
4. 后端按 Supabase `sub` 创建或更新本地 `users` 记录。
5. 订单、积分、充值、生成记录继续绑定本地 `users.id`。

本地 `users.openid` 会使用：

```text
supabase:<supabase-user-id>
```

这样现有订单和积分代码可以平滑沿用。

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

应用启动时会执行 `Base.metadata.create_all` 自动创建缺失表，并对 `users` 添加 Supabase Auth 所需字段。生产环境建议后续切到 Alembic 管理迁移，避免长期依赖自动建表。
