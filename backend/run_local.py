# -*- coding: utf-8 -*-
"""本地开发启动脚本:设好环境变量后起 uvicorn。
仅供本地测试(知识库个人库实验等);生产仍走 docker-compose。
    python backend/run_local.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.chdir(HERE)

os.environ.setdefault("DB_PATH", os.path.join(SITE, "data", "local-kb.db"))
os.environ.setdefault("ADMIN_PASSWORD", "local-admin-2026")
os.environ.setdefault("JWT_SECRET", "local-dev-secret-key-not-for-production")
os.environ.setdefault("STATIC_DIR", os.path.join(SITE, "static"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, log_level="warning")
