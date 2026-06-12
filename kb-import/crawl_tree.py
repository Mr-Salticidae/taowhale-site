# -*- coding: utf-8 -*-
"""BFS 爬取钉钉公开知识库的目录树（仅结构，不含正文）。"""
import json, re, sys, time, urllib.request

ROOT = "7QG4Yx2JpL9nb1oZHgXpmoy0J9dEq3XD"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
BASE = "https://alidocs.dingtalk.com/i/nodes/"

def fetch(uuid):
    req = urllib.request.Request(BASE + uuid, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")

def json_blobs(html):
    """提取含 dentryUuid 的 script 中的 JSON 对象。"""
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.S):
        s = m.group(1)
        if "dentryUuid" not in s:
            continue
        # 找到第一个 '{'，做括号配平截取
        start = s.find("{")
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(s[start:], start):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        yield json.loads(s[start:i + 1])
                    except Exception:
                        pass
                    break

def walk(obj, found):
    if isinstance(obj, dict):
        if "dentryUuid" in obj and "name" in obj:
            u = obj["dentryUuid"]
            prev = found.get(u, {})
            found[u] = {
                "uuid": u,
                "name": obj.get("name") or prev.get("name"),
                "parent": obj.get("parentDentryUuid") or prev.get("parent"),
                "type": obj.get("dentryType") or prev.get("type"),
                "hasChildren": obj.get("hasChildren", prev.get("hasChildren")),
            }
        for v in obj.values():
            walk(v, found)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, found)

def extract_nodes(html):
    found = {}
    for blob in json_blobs(html):
        walk(blob, found)
    return found

def main():
    all_nodes = {}
    visited = set()
    queue = [ROOT]
    while queue:
        uuid = queue.pop(0)
        if uuid in visited:
            continue
        visited.add(uuid)
        try:
            html = fetch(uuid)
        except Exception as e:
            print("FETCH FAIL", uuid, e, file=sys.stderr)
            continue
        nodes = extract_nodes(html)
        new = 0
        for u, n in nodes.items():
            if u not in all_nodes:
                all_nodes[u] = n
                new += 1
            else:
                for k, v in n.items():
                    if v and not all_nodes[u].get(k):
                        all_nodes[u][k] = v
            if n.get("hasChildren") and u not in visited:
                queue.append(u)
        print(f"visited={len(visited)} queue={len(queue)} nodes={len(all_nodes)} (+{new})")
        time.sleep(0.4)
    with open(r"E:\AIGC工作站\whalesea-site\kb-import\tree.json", "w", encoding="utf-8") as f:
        json.dump(all_nodes, f, ensure_ascii=False, indent=1)
    print("TOTAL:", len(all_nodes))

if __name__ == "__main__":
    main()
