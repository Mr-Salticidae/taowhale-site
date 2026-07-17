# -*- coding: utf-8 -*-
"""本地开发启动脚本:设好环境变量后起 uvicorn。
仅供本地测试(知识库个人库实验等);生产仍走 docker-compose。
    python backend/run_local.py
"""
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.chdir(HERE)

# 本地敏感配置从 .env.local 读取(该文件已 gitignore,不入库)。
# 模板见 .env.local.example;不存在时下方按需生成一次性本地值,绝不在仓库里写死密钥。
ENV_LOCAL = os.path.join(SITE, ".env.local")
if os.path.exists(ENV_LOCAL):
    with open(ENV_LOCAL, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

os.environ.setdefault("DB_PATH", os.path.join(SITE, "data", "local-kb.db"))
os.environ.setdefault("STATIC_DIR", os.path.join(SITE, "static"))
# 本地 JWT 密钥:未配置则每次启动随机生成(仅本机、不入库)
os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))
# 本地管理员密码:未在 .env.local 配置则临时随机生成并打印,避免在仓库里写死
if not os.environ.get("ADMIN_PASSWORD"):
    _pw = secrets.token_urlsafe(12)
    os.environ["ADMIN_PASSWORD"] = _pw
    print(f"[本地] 未设置 ADMIN_PASSWORD(可在 {ENV_LOCAL} 配置固定值),本次临时管理员密码: {_pw}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "3000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="warning")
