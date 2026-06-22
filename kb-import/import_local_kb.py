# -*- coding: utf-8 -*-
"""
把本地个人知识库(E:/knowledge-base)的 markdown 批量导入网站知识库(kb_docs)。

这是「第 1 步:接入个人库测试」的导入脚本,走站点新增的批量端点 POST /api/kb/import。
与钉钉用的 import_to_site.py 区别:数据源是本地 *.md 目录(非 manifest.json),
并且一次性事务化批量导入 + purge 重跑幂等。

用法:
  set TAOWHALE_ADMIN_PASSWORD=xxx
  python import_local_kb.py --dry-run                  # 预览将导入什么,不连后端
  python import_local_kb.py                            # 导入到本地 http://127.0.0.1:8000
  python import_local_kb.py --base http://43.128.2.110 # 导入到生产

行为:
- 递归遍历 --root 下所有 *.md,排除 node_modules/.git/dist/build 等噪音目录;
- cat = 路径第一段(顶层目录),tags = 相对路径,title = 首个 # 标题或文件名(去重);
- summary = 首个非标题段落;content = 全文(超 50000 字截断);
- 分批 POST /api/kb/import,首批 purge_public=True(清掉本账号旧公开文档),可反复跑。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_ROOT = r"E:/knowledge-base"
MAX_CONTENT = 50000
MAX_SUMMARY = 200
BATCH = 60
EXCLUDE_DIRS = {"node_modules", ".git", "dist", "build", ".obsidian", "__pycache__",
                ".vscode", ".claude", ".github"}  # .claude=实时 skill 安装目录,与 07_skill存档 重复
GENERIC_STEMS = {"readme", "index", "skill", "索引", "readme.zh-cn", "_index"}
# 根目录的仓库元文件:授权 / Agent 协作记忆 / MOC 入口地图(站内分类树已替代),不作为学习内容导入
NOISE_ROOT_STEMS = {"license", "claude", "readme"}
SKIP_TOPS = {"00_仓库维护"}               # 仓库维护类,非学习内容,整目录跳过
FLAT_TOPS = {"06_代码", "07_skill存档"}   # 扁平为单层(不建子分类)
SKILL_TOP = "07_skill存档"                # 每个 skill 文件夹只保留一篇主文档


def clean_cat(name):
    """去掉分类名的排序前缀 0N_(如 04_方法论与洞察 → 方法论与洞察)。"""
    import re
    return re.sub(r"^\d+[_\-\s]+", "", name).strip() or name


# skill 存档的中文标题/简介(SKILL.md 原文是英文 slug + 触发语,人工凝练为直观中文)
SKILL_META = {
    "ai-short-film-breakdown": ("AI 短片类型判断与拉片", "识别 AI 短片类型、选择叙事路径、规避 AI 技术短板的创作策略工作流。"),
    "ai-short-film-screenwriting": ("AI 短片剧作辅助", "把灵感/主题/现实素材发展成可制作的短片方案,并诊断故事单薄、情绪闭环与制作难度。"),
    "aigc-poster-layout": ("AIGC 作品宣传海报排版", "把定稿核心图/角色锚点图做成各平台传播海报,尤其适合不能改脸、AI 重绘会漂移的角色/IP 作品。"),
    "aigc-postmortem": ("AIGC 创作复盘", "写出事实准确、判断清晰的作品复盘文档,防止自我归因偏差。"),
    "aigc-prompt-optimizer": ("AIGC 提示词优化", "把口语化/模糊的创作需求优化成适合具体工具的专业提示词,支持出图反馈、二选一、视觉诊断与迭代改写。"),
    "blind-editing-workflow": ("蒙眼剪辑法", "用 Python + ffmpeg 让不会剪辑软件的人按卡点精确出片,把 AIGC 图片/视频素材合成 MV、角色 PV。"),
    "character-consistency-mj": ("Midjourney 角色一致性", "用四层金字塔结构(sref/oref/账号审美)维持 AI 角色/IP 跨图一致性。"),
    "content-publish-sop": ("AIGC 内容发布 SOP", "发布前做入场票审计,并给出快手/网易云/B站等平台的适配与文案建议。"),
    "maieutic-deepseek-adapter": ("苏格拉底共学·国内平台适配包", "把 Maieutic 共学法迁移到 DeepSeek/Dify/Coze/通义/豆包/Kimi 等国内可调用环境。"),
    "maieutic-skill": ("苏格拉底式共学", "通过提问帮人澄清问题、选择学习/创作路径、反思迷茫状态,并按需做信息收集。"),
    "prompt-master": ("提示词大师", "为各类 AI 工具(LLM/图像/视频/编程 Agent)生成优化后的专业提示词。"),
    "remotion": ("Remotion 极简知识卡片视频", "用 Remotion + React 生成极简知识卡片流风格视频,内置设计系统、动效规则与场景模板。"),
    "remotion-skill": ("Remotion 科普解说视频工作流", "把笔记/大纲/脚本转成数据驱动的扁平矢量科普视频生产方案。"),
    "song-caption-mv-workflow": ("AI 歌曲 MV 与字幕自动化", "Suno 作品的 MV 制作、无字版导出、Demucs 人声分离、WhisperX 词级对齐与中英双语 SRT 生成。"),
    "subtask-receipt-writer": ("子任务交接回执撰写", "子任务完成后按交接规范写回执/回函/收口简报,把成果回流给主会话。"),
    "suno-music-brief": ("Suno 配乐 Brief", "把项目配乐需求转化为 Suno 的 Simple Mode 探索 brief 与 Custom Mode 固化 brief。"),
}
CAT_ICON = {  # 顶层目录前缀 → emoji,纯展示
    "00": "🗂", "01": "🎨", "02": "🎛", "03": "📌",
    "04": "🧭", "05": "🖼", "06": "💻", "07": "🧩",
}


def api(base, method, path, token=None, body=None):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} → HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")


def strip_frontmatter(text):
    """剥离 Obsidian/YAML frontmatter(开头 --- ... --- 块),便于取真实 H1 与干净正文。"""
    if text.startswith("---"):
        lines = text.splitlines()
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1:]).lstrip("\n")
    return text


IMPORTANCE_MAP = {"低": "⭐", "中": "⭐⭐", "高": "⭐⭐⭐", "较高": "⭐⭐⭐",
                  "很高": "⭐⭐⭐⭐", "最高": "⭐⭐⭐⭐⭐", "极高": "⭐⭐⭐⭐⭐"}
TOOL_CANON = {"midjourney": "MJ", "mj": "MJ", "nano": "Nano"}  # 同义/大小写归一


def normalize_tag(t):
    """规整标签:重要度文字→星级;工具去版本后缀(_niji7/_v5.5/_Custom_Mode)并归一同义。"""
    if "/" not in t:
        return t
    ax, val = t.split("/", 1)
    if ax == "重要度":
        val = IMPORTANCE_MAP.get(val, val)        # 文字→星;已是星保持原样
    elif ax == "工具":
        val = val.split("_")[0]                    # 去版本/变体后缀
        val = TOOL_CANON.get(val.lower(), val)     # 同义归一
    return ax + "/" + val


def parse_frontmatter_tags(raw):
    """从开头 --- 块解析 tags(支持 inline [a, b] 与 block "- a" 两种 YAML 写法),返回干净 list。"""
    if not raw.startswith("---"):
        return []
    lines = raw.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return []
    fm = lines[1:end]
    for j, line in enumerate(fm):
        s = line.strip()
        if not s.startswith("tags:"):
            continue
        val = s[len("tags:"):].strip()
        if val.startswith("[") and val.endswith("]"):
            items = val[1:-1].split(",")
        elif val:
            items = val.split(",")
        else:  # block 形式:后续缩进的 "- item" 行
            items = []
            for k in range(j + 1, len(fm)):
                bs = fm[k].strip()
                if bs.startswith("- "):
                    items.append(bs[2:])
                elif bs == "":
                    continue
                else:
                    break
        return [t.strip().strip("'\"") for t in items if t.strip()]
    return []


def first_heading(text):
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
        if s:  # 第一段非空行不是标题就放弃(避免把正文当标题)
            return ""
    return ""


SUMMARY_SECTIONS = ("一句话总结", "一句话", "核心结论", "结论先行", "总结", "概述", "简介",
                    "tl;dr", "tldr", "摘要")


def _is_sep(s):
    """是否为 markdown 分隔线/水平线(---、===、***、空)。"""
    return s.replace("-", "").replace("=", "").replace("*", "").replace(" ", "") == ""


def _clean(s):
    """去掉行首列表/引用/强调符号,做干净摘要文本。"""
    s = s.lstrip(">").strip().lstrip("-*+ ")
    return s.replace("**", "").replace("`", "").replace("*", "").strip()


def extract_summary(text):
    """提取摘要:优先「一句话总结/概述/结论」小节的首段,回退第一段正文。"""
    lines = text.splitlines()
    # 1) 找总结类小节,取其下首个有内容的行
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#"):
            title = s.lstrip("# ").strip().lower()
            # 去掉"一、二、"等前缀再比对
            t2 = title.lstrip("一二三四五六七八九十0123456789、.() ")
            if any(h in title or h in t2 for h in SUMMARY_SECTIONS):
                for j in range(i + 1, len(lines)):
                    t = lines[j].strip()
                    if t.startswith("#"):
                        break
                    if not t or t.startswith("```") or t.startswith("|") or _is_sep(t):
                        continue
                    c = _clean(t)
                    if c:
                        return c[:MAX_SUMMARY]
                break
    # 2) 回退:第一段正文(跳过标题/引用元信息/代码围栏/表格行/分隔线)
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("```") or s.startswith(">") or s.startswith("|") or _is_sep(s):
            continue
        c = _clean(s)
        if c:
            return c[:MAX_SUMMARY]
    return ""


def collect(root):
    docs = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if not fn.lower().endswith(".md"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            parts = rel.split("/")
            dirs = parts[:-1]                 # 目录段(不含文件名)
            stem = os.path.splitext(fn)[0]
            if not dirs and stem.lower() in NOISE_ROOT_STEMS:
                continue                       # 跳过根目录的 LICENSE / CLAUDE / README 元文件
            raw_top = dirs[0] if dirs else ""
            if raw_top in SKIP_TOPS:
                continue                       # 跳过仓库维护类整目录
            if raw_top == SKILL_TOP and len(dirs) < 2:
                continue                       # 跳过 07 顶层索引文件(只收各 skill 文件夹内的)
            if raw_top in FLAT_TOPS:           # 扁平顶层:不分子类,去 0N_ 前缀
                cat_path = [clean_cat(raw_top)] if raw_top else []
            else:                              # 其余:两级,各级去 0N_ 前缀
                cat_path = [clean_cat(s) for s in dirs[:2]]
            leaf = cat_path[-1] if cat_path else "未分类"
            try:
                raw = open(full, encoding="utf-8-sig").read()  # utf-8-sig 自动去除 BOM
            except UnicodeDecodeError:
                raw = open(full, encoding="utf-8", errors="ignore").read().lstrip("﻿")
            text = strip_frontmatter(raw)
            heading = first_heading(text)
            base = heading or stem
            parent = dirs[-1] if dirs else leaf
            # 通用文件名(README/SKILL/index…)用父目录消歧
            if stem.lower() in GENERIC_STEMS or len(base) < 2:
                base = f"{parent} · {base}" if base else parent
            content = text if len(text) <= MAX_CONTENT else text[:MAX_CONTENT]
            # frontmatter 标签:规整(重要度→星/工具去版本)后去重保序
            tags = list(dict.fromkeys(normalize_tag(t) for t in parse_frontmatter_tags(raw)))
            mtime = ""
            try:
                import time
                mtime = time.strftime("%Y-%m", time.localtime(os.path.getmtime(full)))
            except Exception:
                pass
            docs.append({
                "title": base[:120],
                "summary": extract_summary(text),
                "content": content,
                "cat": leaf,                        # 显示用叶子分类名
                "cat_path": cat_path,               # 分类路径,后端解析/建分类
                "icon": CAT_ICON.get(raw_top[:2], "📄"),
                "tags": ",".join(tags),             # 导航维度:类型标签
                "date": mtime,
                "link": "",
                "sort": 0,
                "_rel": rel,
                "_skill": dirs[1] if (raw_top == SKILL_TOP and len(dirs) >= 2) else None,
                "_stem": stem,
                "_truncated": len(text) > MAX_CONTENT,
            })
    # 07_skill存档:每个 skill 文件夹只保留一篇主文档(SKILL > README > 第一篇)
    from collections import defaultdict
    by_skill, rest = defaultdict(list), []
    for d in docs:
        if d["_skill"]:
            by_skill[d["_skill"]].append(d)
        else:
            rest.append(d)
    for skill, grp in by_skill.items():
        grp.sort(key=lambda d: 0 if d["_stem"].lower() == "skill" else (1 if d["_stem"].lower() == "readme" else 2))
        pick = grp[0]
        if skill in SKILL_META:  # 用人工凝练的中文标题/简介覆盖英文原文
            pick["title"], pick["summary"] = SKILL_META[skill]
        rest.append(pick)
    docs = rest
    docs.sort(key=lambda d: (d["_rel"]))
    # 标题去重:同名追加父目录,再不行追加序号
    seen = {}
    for d in docs:
        t = d["title"]
        if t in seen:
            seen[t] += 1
            parent = d["cat_path"][-1] if d["cat_path"] else ""
            cand = f"{t}（{parent}）"[:120] if parent else f"{t} #{seen[t]}"[:120]
            if cand in seen:
                cand = f"{t} #{seen[t]}"[:120]
            d["title"] = cand
            seen[cand] = 1
        else:
            seen[t] = 1
    return docs


def prune_empty_cats(base, token):
    """删除没有任何文档的空分类(重导改名后会残留旧空分类)。子类先删,多轮直到稳定。
    走 admin DELETE 接口,后端只会删真正无文档且无子类的分类,有数据的分类会被拒绝(安全)。"""
    removed = 0
    for _ in range(6):
        tree = api(base, "GET", "/api/kb/cats", token=token)
        nodes = []
        def walk(cs):
            for c in cs:
                walk(c.get("children") or [])
                nodes.append(c)  # 后序:叶子在前
        walk(tree.get("cats") or [])
        any_del = False
        for c in nodes:
            try:
                api(base, "DELETE", f"/api/admin/kb/cats/{c['id']}", token=token)
                removed += 1
                any_del = True
            except RuntimeError:
                pass  # 有文档/有子类 → 后端拒绝,跳过
        if not any_del:
            break
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--email", default="admin@taowhale.local")
    ap.add_argument("--level", default="all", choices=["all", "group", "personal"],
                    help="导入目标层级:all 公开 / group 组库 / personal 个人库")
    ap.add_argument("--owner-id", type=int, default=0, help="personal 目标所有者(默认导入者自己)")
    ap.add_argument("--group-id", type=int, default=0, help="group 目标组 id")
    ap.add_argument("--no-prune", action="store_true", help="不清理空分类(默认导入后清理改名残留的空分类)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    docs = collect(args.root)
    trunc = sum(1 for d in docs if d.pop("_truncated", False))
    for d in docs:
        for k in ("_rel", "_skill", "_stem"):
            d.pop(k, None)   # 清理内部字段,后端不需要
    # 按顶层分类统计(两级树的第一层)
    by_top = {}
    sub = {}
    for d in docs:
        top = d["cat_path"][0] if d["cat_path"] else "未分类"
        by_top[top] = by_top.get(top, 0) + 1
        if len(d["cat_path"]) >= 2:
            sub.setdefault(top, set()).add(d["cat_path"][1])
    print(f"扫描 {args.root}:共 {len(docs)} 篇 markdown（已排除 {'/'.join(sorted(EXCLUDE_DIRS))}）")
    for c in sorted(by_top):
        print(f"  {by_top[c]:4d}  {c}  （子分类 {len(sub.get(c, []))} 个）")
    tagged = sum(1 for d in docs if d["tags"])
    print(f"  含 frontmatter 标签的文档:{tagged} 篇")
    if trunc:
        print(f"  其中 {trunc} 篇正文超 {MAX_CONTENT} 字已截断")

    if args.dry_run:
        print("\n--dry-run 预览前 12 条:")
        for d in docs[:12]:
            path = " / ".join(d["cat_path"]) or "(未分类)"
            print(f"  [{path}] {d['title']}  | tags: {d['tags'] or '-'}")
        print("\n(未连接后端。去掉 --dry-run 即真正导入)")
        return

    pw = os.environ.get("TAOWHALE_ADMIN_PASSWORD")
    if not pw:
        print("请先设置环境变量 TAOWHALE_ADMIN_PASSWORD（= 后端 ADMIN_PASSWORD）")
        sys.exit(1)

    login = api(args.base, "POST", "/api/auth/login", body={"email": args.email, "password": pw})
    token = login.get("token") or login.get("access_token")
    if not token:
        print("登录失败,响应:", login)
        sys.exit(1)
    print(f"登录成功 @ {args.base}")

    print(f"导入目标层级:{args.level}"
          + (f" · owner_id={args.owner_id or '自己'}" if args.level == "personal" else "")
          + (f" · group_id={args.group_id}" if args.level == "group" else ""))
    total_imported = total_purged = 0
    for i in range(0, len(docs), BATCH):
        chunk = docs[i:i + BATCH]
        body = {"docs": chunk, "purge": i == 0, "level": args.level,  # 仅首批清空同目标旧文档
                "owner_id": args.owner_id, "group_id": args.group_id}
        r = api(args.base, "POST", "/api/kb/import", token=token, body=body)
        total_imported += r.get("imported", 0)
        total_purged += r.get("purged", 0)
        print(f"  批 {i // BATCH + 1}: 导入 {r.get('imported', 0)} 篇"
              + (f"（清空同目标旧 {r.get('purged', 0)} 篇）" if i == 0 else ""))
    print(f"\n完成:共导入 {total_imported} 篇到「{args.level}」库,清空旧 {total_purged} 篇。")
    if not args.no_prune:
        pruned = prune_empty_cats(args.base, token)
        print(f"清理空分类:{pruned} 个")


if __name__ == "__main__":
    main()
