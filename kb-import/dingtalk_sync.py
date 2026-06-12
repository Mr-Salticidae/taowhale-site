# -*- coding: utf-8 -*-
"""
钉钉知识库 → 本地 markdown 同步脚本（方案 B：钉钉开放平台官方 API）。

用法：
  set DINGTALK_CLIENT_ID=xxx
  set DINGTALK_CLIENT_SECRET=xxx
  set DINGTALK_OPERATOR_UNIONID=xxx   (有知识库权限的成员 unionId，部分接口需要)
  python dingtalk_sync.py --probe     # 凭证到手先跑这个：验证 token 并探测各端点
  python dingtalk_sync.py             # 正式同步：拉取整库 → raw/*.md + manifest.json

注意：
- 钉钉文档站是 JS 渲染，编写时无法在线核对端点细节；带 [VERIFY] 注释的端点
  在 --probe 模式下逐一探测，按实际响应修正后再正式同步。
- 全部用 python 标准库，无第三方依赖；本机或服务器均可运行。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.dingtalk.com"
KB_NAME = os.environ.get("KB_NAME", "AIGC研修班知识库")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifest.json")


class DingTalk:
    def __init__(self, client_id, client_secret, operator=None):
        self.cid = client_id
        self.sec = client_secret
        self.operator = operator
        self._token = None
        self._token_ts = 0

    def token(self):
        if self._token and time.time() - self._token_ts < 6000:
            return self._token
        body = json.dumps({"appKey": self.cid, "appSecret": self.sec}).encode()
        req = urllib.request.Request(
            API + "/v1.0/oauth2/accessToken", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        self._token = data["accessToken"]
        self._token_ts = time.time()
        return self._token

    def call(self, method, path, params=None, body=None):
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "x-acs-dingtalk-access-token": self.token(),
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")

    # ---------- 端点（凭证到手后用 --probe 核实） ----------

    def list_spaces(self, next_token=None):
        # [VERIFY] 获取知识库列表
        return self.call("GET", "/v1.0/wiki/spaces", params={
            "operatorId": self.operator, "nextToken": next_token, "maxResults": 50})

    def list_nodes(self, space_id, parent_node_id=None, next_token=None):
        # [VERIFY] 获取知识库节点列表（parent 为空 = 根）
        return self.call("GET", f"/v1.0/wiki/spaces/{space_id}/nodes", params={
            "operatorId": self.operator, "parentNodeId": parent_node_id,
            "nextToken": next_token, "maxResults": 50})

    def get_node(self, node_id):
        # [VERIFY] 查询知识库节点信息
        return self.call("GET", f"/v1.0/wiki/nodes/{node_id}", params={
            "operatorId": self.operator})

    def export_doc_markdown(self, doc_key):
        # [VERIFY] 导出文档为 markdown。可能是异步任务（提交 → 轮询 → 下载 url）。
        # 候选 1：钉钉文档服务端 API
        task = self.call("POST", f"/v1.0/doc/documents/{doc_key}/export", body={
            "operatorId": self.operator, "targetFormat": "markdown"})
        task_id = task.get("taskId") or task.get("id")
        if not task_id:
            return task  # 同步返回内容的情形
        for _ in range(60):
            time.sleep(2)
            st = self.call("GET", f"/v1.0/doc/export/tasks/{task_id}",
                           params={"operatorId": self.operator})
            if st.get("status") in ("FINISHED", "success", "SUCCESS"):
                url = st.get("downloadUrl") or st.get("url")
                with urllib.request.urlopen(url, timeout=60) as r:
                    return {"markdown": r.read().decode("utf-8", "ignore")}
            if st.get("status") in ("FAILED", "fail"):
                raise RuntimeError(f"export failed: {st}")
        raise TimeoutError("export task timeout")


def probe(dt):
    """逐项探测：token → 知识库列表端点的若干候选。打印结果供修正端点。"""
    print("[1] token ...", end=" ")
    try:
        tok = dt.token()
        print("OK", tok[:8] + "...")
    except Exception as e:
        print("FAIL", e)
        print("→ 检查 Client ID/Secret 是否正确、应用是否已发布。")
        return
    candidates = [
        ("GET", "/v1.0/wiki/spaces", {"operatorId": dt.operator, "maxResults": 20}, None),
        ("POST", "/v1.0/wiki/spaces/query", None, {"operatorId": dt.operator, "maxResults": 20}),
        ("GET", "/v2.0/wiki/spaces", {"operatorId": dt.operator, "maxResults": 20}, None),
        ("GET", "/v1.0/storage/spaces", {"unionId": dt.operator, "maxResults": 20}, None),
    ]
    for method, path, params, body in candidates:
        print(f"[2] {method} {path} ...", end=" ")
        try:
            data = dt.call(method, path, params=params, body=body)
            print("OK →", json.dumps(data, ensure_ascii=False)[:300])
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}", e.read()[:200])
        except Exception as e:
            print("FAIL", e)
    print("\n按上面探测结果修正脚本中 [VERIFY] 端点后，去掉 --probe 正式运行。")


def sanitize(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "untitled"


def sync(dt):
    os.makedirs(OUT_DIR, exist_ok=True)
    # 1. 找目标知识库
    spaces, nt = [], None
    while True:
        page = dt.list_spaces(nt)
        spaces += page.get("spaces", page.get("items", []))
        nt = page.get("nextToken")
        if not nt:
            break
    target = next((s for s in spaces if s.get("name") == KB_NAME), None)
    if not target:
        print("找不到知识库:", KB_NAME, "；可见库:", [s.get("name") for s in spaces])
        sys.exit(1)
    space_id = target.get("spaceId") or target.get("id")
    print("知识库:", KB_NAME, space_id)

    # 2. BFS 节点树
    manifest = []
    queue = [(None, [])]  # (parentNodeId, path names)
    while queue:
        parent, path = queue.pop(0)
        nt = None
        while True:
            page = dt.list_nodes(space_id, parent, nt)
            for node in page.get("nodes", page.get("items", [])):
                name = node.get("name") or node.get("title") or "untitled"
                node_id = node.get("nodeId") or node.get("id")
                doc_key = node.get("docKey") or node.get("dentryUuid")
                ntype = (node.get("type") or node.get("dentryType") or "").lower()
                has_children = node.get("hasChildren")
                rel = path + [sanitize(name)]
                if ntype in ("file", "doc", "document", "adoc") or doc_key and not has_children:
                    manifest.append({
                        "title": re.sub(r"\.(adoc|able)$", "", name),
                        "path": "/".join(path),
                        "nodeId": node_id, "docKey": doc_key,
                        "file": "/".join(rel) + ".md",
                        "level": "all",  # 默认公开级，导入前可在 manifest 里逐条改
                    })
                if has_children or ntype in ("folder", "dir"):
                    queue.append((node_id, rel))
            nt = page.get("nextToken")
            if not nt:
                break

    print("发现文档", len(manifest), "篇，开始导出 ...")
    # 3. 逐篇导出 markdown
    ok = 0
    for i, m in enumerate(manifest, 1):
        dst = os.path.join(OUT_DIR, m["file"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            res = dt.export_doc_markdown(m["docKey"])
            md = res.get("markdown") or res.get("content") or ""
            with open(dst, "w", encoding="utf-8") as f:
                f.write(md)
            m["chars"] = len(md)
            ok += 1
            print(f"[{i}/{len(manifest)}] OK {m['file']} ({len(md)} 字)")
        except Exception as e:
            m["error"] = str(e)
            print(f"[{i}/{len(manifest)}] FAIL {m['file']}: {e}")
        time.sleep(0.5)

    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"完成：{ok}/{len(manifest)} 篇 → {OUT_DIR}；清单 → {MANIFEST}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只探测端点，不同步")
    args = ap.parse_args()
    cid = os.environ.get("DINGTALK_CLIENT_ID")
    sec = os.environ.get("DINGTALK_CLIENT_SECRET")
    if not cid or not sec:
        print("请先设置环境变量 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET")
        sys.exit(1)
    dt = DingTalk(cid, sec, os.environ.get("DINGTALK_OPERATOR_UNIONID"))
    if args.probe:
        probe(dt)
    else:
        sync(dt)


if __name__ == "__main__":
    main()
