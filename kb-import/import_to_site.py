# -*- coding: utf-8 -*-
"""
把 dingtalk_sync.py 产出的 raw/*.md + manifest.json 导入网站 kb_docs。

用法：
  set TAOWHALE_ADMIN_PASSWORD=xxx
  python import_to_site.py --dry-run                 # 只打印将要发生什么
  python import_to_site.py                           # 正式导入（默认打到生产）
  python import_to_site.py --base http://127.0.0.1:8081   # 打到本地测试

行为：
- 以官方账号登录（admin@taowhale.local），逐条 POST /api/kb/docs；
- 已存在同标题文档时 PUT 更新（幂等，可反复跑）；
- level/group_id/owner_id 取自 manifest 每条记录（默认 all 公开级，
  导入前可手动编辑 manifest.json 调整分级）；
- cat 取路径第一段（如 AI工具操作知识库），tags 取完整路径；
- 正文超过后端 50000 字上限时截断并警告。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.json")
RAW = os.path.join(HERE, "raw")
MAX_CONTENT = 50000


def api(base, method, path, token=None, body=None):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} → HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://43.128.2.110")
    ap.add_argument("--email", default="admin@taowhale.local")
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pw = os.environ.get("TAOWHALE_ADMIN_PASSWORD")
    if not pw and not args.dry_run:
        print("请先设置环境变量 TAOWHALE_ADMIN_PASSWORD（服务器 docker-compose.yml 里的 ADMIN_PASSWORD）")
        sys.exit(1)

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    docs = [m for m in manifest if not m.get("error")]
    print(f"清单 {len(manifest)} 条，可导入 {len(docs)} 条（跳过导出失败 {len(manifest) - len(docs)} 条）")

    token = None
    existing = {}
    if not args.dry_run:
        login = api(args.base, "POST", "/api/auth/login", body={"email": args.email, "password": pw})
        token = login.get("token") or login.get("access_token")
        if not token:
            print("登录响应里没有 token：", login)
            sys.exit(1)
        cur = api(args.base, "GET", "/api/kb/docs", token=token)
        existing = {d["title"]: d["id"] for d in cur.get("docs", [])}
        print(f"登录成功；线上已有 {len(existing)} 篇")

    created = updated = skipped = 0
    for i, m in enumerate(docs, 1):
        src = os.path.join(RAW, m["file"])
        if not os.path.exists(src):
            print(f"[{i}] SKIP 文件缺失 {m['file']}")
            skipped += 1
            continue
        content = open(src, encoding="utf-8").read()
        if len(content) > MAX_CONTENT:
            print(f"[{i}] WARN {m['title']} 正文 {len(content)} 字超上限，截断到 {MAX_CONTENT}")
            content = content[:MAX_CONTENT]
        summary = content.strip().replace("\n", " ")[:120]
        body = {
            "title": m["title"][:120],
            "summary": summary,
            "content": content,
            "cat": (m["path"].split("/") or [""])[0],
            "tags": m["path"],
            "level": m.get("level", "all"),
            "group_id": m.get("group_id", 0),
            "owner_id": m.get("owner_id", 0),
        }
        if args.dry_run:
            print(f"[{i}] DRY {body['level']:8s} cat={body['cat'] or '-':20s} {body['title']} ({len(content)} 字)")
            continue
        if m["title"][:120] in existing:
            api(args.base, "PUT", f"/api/kb/docs/{existing[m['title'][:120]]}", token=token, body=body)
            updated += 1
            print(f"[{i}] UPDATE {m['title']}")
        else:
            api(args.base, "POST", "/api/kb/docs", token=token, body=body)
            created += 1
            print(f"[{i}] CREATE {m['title']}")
    print(f"完成：新建 {created}，更新 {updated}，跳过 {skipped}")


if __name__ == "__main__":
    main()
