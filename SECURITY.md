# 安全说明 · 密钥与部署

本仓库是**公开仓库**,任何写进代码/配置文件的密钥都等于公开。所有敏感值一律走环境变量,**绝不入库**。

## 涉及的敏感环境变量

| 变量 | 用途 | 说明 |
|---|---|---|
| `JWT_SECRET` | 登录 token 的 HMAC 签名密钥 | **必须**为随机长串。一旦泄露,任何人可伪造任意用户(含管理员)的登录 token。生成:`openssl rand -hex 32` |
| `ADMIN_PASSWORD` | 官方账号 `admin@taowhale.local` 登录密码 | 设置后,后端每次启动会把官方账号密码**重置**为该值。用强密码。 |

代码中的取值约定(`backend/main.py`):
- `JWT_SECRET` 未设置时,**不再**回退到任何写死的默认值,而是生成一次性随机密钥并打印告警(重启后登录态失效)——以此强制运维显式配置,杜绝"用已知密钥上线"。
- `ADMIN_PASSWORD` 未设置时为空,即不重置官方账号密码。

## 这些值放在哪

- **本地开发**(`backend/run_local.py`):从仓库根目录的 `.env.local` 读取(已 gitignore)。模板见 [`.env.local.example`](.env.local.example)。未配置项会在本地随机生成。
- **生产部署**(`docker-compose.yml`):从部署目录的 `.env` 读取(已 gitignore)。模板见 [`.env.example`](.env.example)。compose 用 `${VAR:?...}` 语法,**未设置直接报错拒绝启动**,防止占位符/弱默认上线。

`.gitignore` 已忽略 `.env`、`.env.local`、`.env.*.local`;仓库里只保留 `*.example` 模板。

## 部署 / 重装服务器的标准步骤

```bash
# 1. 拉取代码
git clone <repo> && cd taowhale-site        # 或 git pull

# 2. 生成密钥文件(.env 不入库)
echo "JWT_SECRET=$(openssl rand -hex 32)" > .env
echo "ADMIN_PASSWORD=你的强密码" >> .env       # 纯字母数字,避免空格/$ " ' \ 等特殊字符
chmod 600 .env                               # 仅 root 可读

# 3. 启动
docker compose up -d --build

# 4. 核验运行中的密钥已生效(不显示全文)
docker exec whalesea sh -c '
  case "$JWT_SECRET" in
    please-change-me|CHANGE_ME_TO_RANDOM_64_CHARS|"") echo "❌ 弱/空密钥,未生效";;
    *) echo "✅ JWT_SECRET 已设置,长度=${#JWT_SECRET}";;
  esac'
```

## 何时需要轮换 JWT_SECRET

出现以下任一情况,按上面第 2–3 步用**新的** `openssl rand -hex 32` 重置 `JWT_SECRET` 并重启:
- 怀疑密钥泄露,或曾经用过写死/默认/占位值(如 `please-change-me`、`CHANGE_ME_*`)上线;
- 更换服务器、迁移部署;
- 定期轮换。

轮换后所有现有登录态失效(用户需重新登录),同时任何已被伪造的 token 立即作废。

## 切勿

- ❌ 把 `JWT_SECRET` / `ADMIN_PASSWORD` 的真实值写进 `docker-compose.yml`、`run_local.py` 或任何入库文件;
- ❌ 把 `.env` / `.env.local` 提交进 git;
- ❌ 复用其它环境的密钥。

---

### 变更记录

- **2026-06-22**:修复 `JWT_SECRET` 回退公开默认值 `please-change-me` 的隐患(公开仓库可被伪造 token);`run_local.py` 移除写死凭证;`docker-compose.yml` 改 `.env` 注入并强制非空。线上(`/opt/whalesea`)已轮换为 64 位随机 `JWT_SECRET`。
