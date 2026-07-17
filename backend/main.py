"""鲸海拾贝官网后端 · FastAPI + SQLite
功能:邮箱+密码注册登录(pbkdf2 哈希 / HMAC 签名 token)、论坛(发帖/回帖/浏览计数)、静态站点托管
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "whale.db"))
SECRET = os.environ.get("JWT_SECRET")
if not SECRET:
    # 绝不回退到公开仓库里写死的固定密钥(否则任何人都能伪造登录 token)。
    # 未配置时生成一次性随机密钥:服务可正常启动,但重启后登录态失效——以此提醒运维设置固定值。
    SECRET = secrets.token_hex(32)
    print(
        "[安全警告] 未设置 JWT_SECRET 环境变量,已生成一次性随机密钥;"
        "重启后所有登录态将失效。生产环境务必设置固定且保密的 JWT_SECRET。",
        file=sys.stderr,
        flush=True,
    )
ADMIN_EMAIL = "admin@taowhale.local"
LEGACY_ADMIN_EMAIL = "admin@whalesea.local"  # 品牌更名前的旧管理员邮箱,启动时自动迁移
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")  # 设置后,启动时将官方账号密码重置为该值
STATIC_DIR = os.environ.get("STATIC_DIR", os.path.join(os.path.dirname(__file__), "static"))
TOKEN_TTL = 60 * 60 * 24 * 30  # 30 天

app = FastAPI(title="Taowhale API", docs_url="/api/docs", openapi_url="/api/openapi.json")


# ---------------- 数据库 ----------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            pw TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student',
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            cat TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0,
            views INTEGER NOT NULL DEFAULT 0,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL REFERENCES threads(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            content TEXT NOT NULL,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER NOT NULL REFERENCES groups(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            PRIMARY KEY (group_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS kb_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL DEFAULT 'all',
            group_id INTEGER REFERENCES groups(id),
            owner_id INTEGER REFERENCES users(id),
            author_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            cat TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT '',
            link TEXT NOT NULL DEFAULT '',
            sort INTEGER NOT NULL DEFAULT 0,
            created TEXT NOT NULL,
            updated TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kb_cats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            parent_id INTEGER REFERENCES kb_cats(id),
            sort INTEGER NOT NULL DEFAULT 0,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            cat TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT '',
            badge TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT '',
            extra TEXT NOT NULL DEFAULT '',
            link TEXT NOT NULL DEFAULT '',
            sort INTEGER NOT NULL DEFAULT 0,
            created TEXT NOT NULL
        );
        """
    )
    # 迁移:users 表补 banned 列(0=正常 1=封禁)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "banned" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN banned INTEGER NOT NULL DEFAULT 0")
    # 迁移:kb_docs 补 cat_id 列(两层分类树外键,NULL=未分类)
    kb_cols = [r[1] for r in conn.execute("PRAGMA table_info(kb_docs)").fetchall()]
    if "cat_id" not in kb_cols:
        conn.execute("ALTER TABLE kb_docs ADD COLUMN cat_id INTEGER REFERENCES kb_cats(id)")
    # 迁移:品牌更名,旧管理员邮箱改为新邮箱(无旧账号时为空操作)
    conn.execute("UPDATE users SET email=? WHERE email=?", (ADMIN_EMAIL, LEGACY_ADMIN_EMAIL))
    # 种子:官方账号 + 版规置顶帖
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        now = now_str()
        conn.execute(
            "INSERT INTO users(email,name,pw,role,created) VALUES(?,?,?,?,?)",
            (ADMIN_EMAIL, "鲸海拾贝官方", hash_pw(secrets.token_hex(16)), "official", now),
        )
        uid = conn.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()[0]
        conn.execute(
            "INSERT INTO threads(user_id,cat,title,content,pinned,created) VALUES(?,?,?,?,1,?)",
            (uid, "notice", "社区版规与发帖指南 v1.0",
             "占位:发帖分类规范、互评礼仪、商单信息发布规则。\n\n1. 互评先讲优点,再给可执行的修改建议。\n2. 答疑帖请附完整 prompt 与工具版本。\n3. 商业合作信息仅限商单大厅发布。", now),
        )
    # 种子:五类内容占位数据(与上线前的前端占位一致,后台可编辑替换)
    if conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0:
        now = now_str()
        seed = [
            # kind, title, summary, cat, icon, badge, tags, author, date, extra
            ("work", "《短片作品标题占位》", "占位简介:短片梗概、风格与时长,后续替换为真实内容。", "film", "🎬", "AI Film", "Seedance,MJ", "学员A", "2026-06", ""),
            ("work", "《MV 作品标题占位》", "占位简介:AI 原创歌曲 + 视觉 MV,附完整字幕工作流。", "mv", "🎵", "Music MV", "Suno,ffmpeg", "学员B", "2026-06", ""),
            ("work", "《海报作品标题占位》", "占位简介:比赛获奖 / 商用海报,附 prompt 与排版复盘。", "image", "🖼️", "Poster", "Midjourney", "学员C", "2026-05", ""),
            ("work", "《角色 IP 名称占位》", "占位简介:角色设定、一致性方案与系列内容企划。", "ip", "🧸", "Character IP", "sref,oref", "学员D", "2026-05", ""),
            ("work", "《短片作品标题占位》", "占位简介:短片梗概、风格与时长,后续替换为真实内容。", "film", "🎞️", "AI Film", "Kling", "学员E", "2026-04", ""),
            ("work", "《系列图作品标题占位》", "占位简介:主题系列创作,附题眼发散与构图意图层复盘。", "image", "🌅", "Series", "gpt-image", "学员F", "2026-04", ""),
            ("work", "《科普视频标题占位》", "占位简介:Remotion 数据驱动动画 + AI 配音解说。", "other", "📺", "Explainer", "Remotion", "学员G", "2026-03", ""),
            ("work", "《公益作品标题占位》", "占位简介:AI 音乐公益项目,从创作到发布的完整记录。", "mv", "🎤", "Charity", "Suno,WhisperX", "学员H", "2026-03", ""),
            ("work", "《实验作品标题占位》", "占位简介:新工具 / 新玩法探索性创作。", "other", "✨", "Experiment", "Lab", "学员I", "2026-02", ""),
            ("gig", "需求标题占位:品牌宣传短片 ×1", "需求简介占位:时长、风格、交付物与周期说明。", "open", "🎬", "", "", "", "DDL 2026-07", "¥ 0,000"),
            ("gig", "需求标题占位:产品主视觉海报 ×3", "需求简介占位:尺寸、平台、品牌规范说明。", "open", "🖼️", "", "", "", "DDL 2026-07", "¥ 0,000"),
            ("gig", "需求标题占位:品牌主题曲 + MV", "需求简介占位:曲风参考、歌词方向与使用场景。", "doing", "🎵", "", "", "", "DELIVERING", "¥ 0,000"),
            ("case", "案例标题占位:某品牌 AI 宣传片", "占位简介:需求背景 → 方案 → 交付成果 → 客户反馈。", "", "🤝", "Brand", "", "", "2026-05", ""),
            ("case", "案例标题占位:某店铺全套 AI 视觉", "占位简介:需求背景 → 方案 → 交付成果 → 客户反馈。", "", "🏪", "E-commerce", "", "", "2026-04", ""),
            ("case", "案例标题占位:公益歌曲 MV 项目", "占位简介:需求背景 → 方案 → 交付成果 → 社会反响。", "", "📣", "Charity", "", "", "2026-03", ""),
            ("doc", "文档标题占位:口语化需求 → 专业提示词的转换框架", "摘要占位:主体 → 外观 → 环境 → 构图意图 → 光影 → 风格的分层提示词写法。", "prompt", "📌", "", "", "", "2026-06", ""),
            ("doc", "文档标题占位:蒙眼剪辑法——不会剪辑软件也能精确出片", "摘要占位:Python + ffmpeg 的 AI 辅助剪辑闭环,从素材到成片。", "workflow", "🛠️", "", "", "", "2026-06", ""),
            ("doc", "文档标题占位:MJ 角色一致性四层金字塔", "摘要占位:从 sref/oref 到角色设定文档,跨图保持同一角色。", "workflow", "🧸", "", "", "", "2026-05", ""),
            ("doc", "文档标题占位:某获奖图复盘——它为什么能赢", "摘要占位:事实先行的复盘方法,提炼可迁移规则,防自我归因偏差。", "review", "🔁", "", "", "", "2026-05", ""),
            ("doc", "文档标题占位:AI 歌曲字幕自动化——Demucs + WhisperX 链路", "摘要占位:人声分离、词级对齐、双语 SRT 的完整工程实践。", "workflow", "🎤", "", "", "", "2026-04", ""),
            ("doc", "文档标题占位:主流 AI 视频工具横评(2026 上半年)", "摘要占位:Seedance / Sora / Kling / Runway 适用场景对比。", "tool", "⚖️", "", "", "", "2026-04", ""),
            ("doc", "文档标题占位:Prompt Battle 题眼发散方法", "摘要占位:比赛主题如何先发散再收束,尺度跃迁与巨物地貌化规则。", "prompt", "⚡", "", "", "", "2026-03", ""),
            ("doc", "文档标题占位:某商单项目复盘——从需求到验收", "摘要占位:商业项目的沟通、报价、交付与验收经验。", "review", "📊", "", "", "", "2026-03", ""),
            ("course", "课程标题占位:Midjourney 从入门到风格化", "占位:课时数、难度、学完能做什么。", "入门", "🖼️", "Image", "", "", "", "00 课时"),
            ("course", "课程标题占位:AI 短片创作全流程", "占位:课时数、难度、学完能做什么。", "进阶", "🎬", "Video", "", "", "", "00 课时"),
            ("course", "课程标题占位:Suno 配乐与歌曲创作", "占位:课时数、难度、学完能做什么。", "进阶", "🎵", "Music", "", "", "", "00 课时"),
            ("course", "课程标题占位:蒙眼剪辑法实战", "占位:课时数、难度、学完能做什么。", "进阶", "✂️", "Editing", "", "", "", "00 课时"),
            ("course", "课程标题占位:提示词工程系统课", "占位:课时数、难度、学完能做什么。", "入门", "📌", "Prompt", "", "", "", "00 课时"),
            ("course", "课程标题占位:商单实战与交付规范", "占位:课时数、难度、学完能做什么。", "实战", "💼", "Business", "", "", "", "00 课时"),
        ]
        conn.executemany(
            "INSERT INTO items(kind,title,summary,cat,icon,badge,tags,author,date,extra,created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [s + (now,) for s in seed],
        )
    # 种子:默认工作组
    if conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 0:
        now = now_str()
        conn.executemany(
            "INSERT INTO groups(name,created) VALUES(?,?)",
            [("课程组", now), ("班主任组", now), ("助教组", now)],
        )
    # 迁移:items 表旧知识文档(kind='doc')迁入 kb_docs 作为公开级文档
    if conn.execute("SELECT COUNT(*) FROM kb_docs").fetchone()[0] == 0:
        admin_row = conn.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()
        aid = admin_row[0] if admin_row else 1
        for r in conn.execute("SELECT * FROM items WHERE kind='doc'").fetchall():
            conn.execute(
                "INSERT INTO kb_docs(level,author_id,title,summary,cat,icon,tags,date,link,sort,created,updated) "
                "VALUES('all',?,?,?,?,?,?,?,?,?,?,?)",
                (aid, r["title"], r["summary"], r["cat"], r["icon"], r["tags"],
                 r["date"], r["link"], r["sort"], r["created"], r["created"]),
            )
        conn.execute("DELETE FROM items WHERE kind='doc'")
    # 知识库全文搜索:FTS5 外部内容虚表 + 同步触发器
    # 用 trigram 分词器,中英文均支持子串匹配(默认 unicode61 不切分中文)
    conn.executescript(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
            title, summary, content, tags,
            content='kb_docs', content_rowid='id', tokenize='trigram'
        );
        CREATE TRIGGER IF NOT EXISTS kb_fts_ai AFTER INSERT ON kb_docs BEGIN
            INSERT INTO kb_fts(rowid, title, summary, content, tags)
            VALUES (new.id, new.title, new.summary, new.content, new.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS kb_fts_ad AFTER DELETE ON kb_docs BEGIN
            INSERT INTO kb_fts(kb_fts, rowid, title, summary, content, tags)
            VALUES('delete', old.id, old.title, old.summary, old.content, old.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS kb_fts_au AFTER UPDATE ON kb_docs BEGIN
            INSERT INTO kb_fts(kb_fts, rowid, title, summary, content, tags)
            VALUES('delete', old.id, old.title, old.summary, old.content, old.tags);
            INSERT INTO kb_fts(rowid, title, summary, content, tags)
            VALUES (new.id, new.title, new.summary, new.content, new.tags);
        END;
        """
    )
    # 启动时重建索引,确保与 kb_docs 现状一致(外部内容表,幂等,226 篇成本极低)
    conn.execute("INSERT INTO kb_fts(kb_fts) VALUES('rebuild')")
    # 管理员激活:设置了 ADMIN_PASSWORD 时,启动即重置官方账号密码并确保可登录
    if ADMIN_PASSWORD:
        conn.execute(
            "UPDATE users SET pw=?, role='official', banned=0 WHERE email=?",
            (hash_pw(ADMIN_PASSWORD), ADMIN_EMAIL),
        )
    conn.commit()
    conn.close()


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 密码与 Token ----------------
def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120_000).hex()
    return salt + "$" + digest


def verify_pw(pw: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$")
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120_000).hex()
    return hmac.compare_digest(test, digest)


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(uid: int) -> str:
    payload = b64e(json.dumps({"uid": uid, "exp": time.time() + TOKEN_TTL}).encode())
    sig = b64e(hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest())
    return payload + "." + sig


def parse_token(token: str):
    try:
        payload, sig = token.split(".")
        good = b64e(hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(b64d(payload))
        if data.get("exp", 0) < time.time():
            return None
        return int(data["uid"])
    except Exception:
        return None


def current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "请先登录")
    uid = parse_token(authorization[7:])
    if uid is None:
        raise HTTPException(401, "登录已过期,请重新登录")
    conn = db()
    row = conn.execute("SELECT id,email,name,role,banned FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(401, "账号不存在")
    if row["banned"]:
        raise HTTPException(403, "账号已被封禁,如有疑问请联系官方")
    return dict(row)


def require_admin(user=Depends(current_user)):
    if user["role"] != "official":
        raise HTTPException(403, "仅官方账号可操作")
    return user


def optional_user(authorization: str = Header(None)):
    """可选登录:未登录/失效/被封禁返回 None,不抛错"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    uid = parse_token(authorization[7:])
    if uid is None:
        return None
    conn = db()
    row = conn.execute("SELECT id,email,name,role,banned FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    if not row or row["banned"]:
        return None
    return dict(row)


def user_group_ids(conn, uid) -> set:
    return {r[0] for r in conn.execute("SELECT group_id FROM group_members WHERE user_id=?", (uid,)).fetchall()}


def is_staff(conn, user) -> bool:
    """工作人员 = 导师/官方,或属于任一工作组"""
    if not user:
        return False
    if user["role"] in ("mentor", "official"):
        return True
    return len(user_group_ids(conn, user["id"])) > 0


def require_staff(user=Depends(current_user)):
    conn = db()
    ok = is_staff(conn, user)
    conn.close()
    if not ok:
        raise HTTPException(403, "仅工作人员可操作")
    return user


def public_user(u) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"]}


# ---------------- 模型 ----------------
class RegisterIn(BaseModel):
    email: str
    password: str
    name: str


class LoginIn(BaseModel):
    email: str
    password: str


class ThreadIn(BaseModel):
    title: str
    content: str
    cat: str


class ReplyIn(BaseModel):
    content: str


class BanIn(BaseModel):
    banned: bool


class RoleIn(BaseModel):
    role: str


class PinIn(BaseModel):
    pinned: bool


class ItemIn(BaseModel):
    title: str
    summary: str = ""
    cat: str = ""
    icon: str = ""
    badge: str = ""
    tags: str = ""
    author: str = ""
    date: str = ""
    extra: str = ""
    link: str = ""
    sort: int = 0


class KbDocIn(BaseModel):
    title: str
    summary: str = ""
    content: str = ""
    cat: str = ""
    cat_id: int = 0           # 直接指定分类(编辑器用,从分类树选)
    cat_path: list[str] = []  # 按名称路径解析/创建分类(导入用,如 ['04_方法论','01_角色一致性'])
    icon: str = ""
    tags: str = ""
    date: str = ""
    link: str = ""
    sort: int = 0
    level: str = "all"
    group_id: int = 0
    owner_id: int = 0


class KbImportIn(BaseModel):
    docs: list[KbDocIn]
    level: str = "all"          # 导入目标层级:all(公开) / group(组) / personal(个人)
    owner_id: int = 0           # personal 目标所有者(0=导入者自己)
    group_id: int = 0           # group 目标组
    purge: bool = False         # True 时先清空同目标范围旧文档,保证重跑幂等
    purge_public: bool = False  # 兼容旧字段:等价于 level=all 时的 purge


class KbCatIn(BaseModel):
    name: str
    parent_id: int = 0  # 0 = 顶层
    sort: int = 0


class GroupIn(BaseModel):
    name: str


class MemberIn(BaseModel):
    user_id: int


VALID_CATS = {"qa", "critique", "share", "notice"}
VALID_ROLES = {"student", "mentor", "official"}
VALID_KINDS = {"work", "gig", "case", "course"}
VALID_LEVELS = {"all", "group", "personal"}


def check_kind(kind: str):
    if kind not in VALID_KINDS:
        raise HTTPException(400, "内容类型不合法")


def check_item(body: ItemIn):
    if not 1 <= len(body.title.strip()) <= 120:
        raise HTTPException(400, "标题需 1-120 个字符")
    if len(body.summary) > 2000:
        raise HTTPException(400, "简介过长(最多 2000 字)")


# ---------------- 接口:健康 ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "service": "taowhale", "time": now_str()}


# ---------------- 接口:认证 ----------------
@app.post("/api/auth/register")
def register(body: RegisterIn):
    email = body.email.strip().lower()
    name = body.name.strip()
    if not re.match(r"^\S+@\S+\.\S+$", email):
        raise HTTPException(400, "邮箱格式不正确")
    if len(body.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if not 1 <= len(name) <= 24:
        raise HTTPException(400, "昵称需 1-24 个字符")
    conn = db()
    try:
        conn.execute(
            "INSERT INTO users(email,name,pw,role,created) VALUES(?,?,?,?,?)",
            (email, name, hash_pw(body.password), "student", now_str()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(400, "该邮箱已注册,请直接登录")
    row = conn.execute("SELECT id,email,name,role FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return {"token": make_token(row["id"]), "user": public_user(row)}


@app.post("/api/auth/login")
def login(body: LoginIn):
    email = body.email.strip().lower()
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    if not row or not verify_pw(body.password, row["pw"]):
        raise HTTPException(400, "邮箱或密码错误")
    if row["banned"]:
        raise HTTPException(403, "账号已被封禁,如有疑问请联系官方")
    return {"token": make_token(row["id"]), "user": public_user(row)}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return public_user(user)


# ---------------- 接口:论坛 ----------------
@app.get("/api/forum/threads")
def list_threads():
    conn = db()
    rows = conn.execute(
        """
        SELECT t.id, t.cat, t.title, t.content, t.pinned, t.views, t.created,
               u.name AS author_name, u.role AS author_role,
               (SELECT COUNT(*) FROM replies r WHERE r.thread_id = t.id) AS reply_count,
               COALESCE((SELECT MAX(r.created) FROM replies r WHERE r.thread_id = t.id), t.created) AS last_at
        FROM threads t JOIN users u ON u.id = t.user_id
        ORDER BY t.pinned DESC, last_at DESC
        """
    ).fetchall()
    conn.close()
    threads = []
    for r in rows:
        d = dict(r)
        d["excerpt"] = (d.pop("content") or "").replace("\n", " ")[:80]
        threads.append(d)
    return {"threads": threads}


@app.get("/api/forum/threads/{tid}")
def get_thread(tid: int):
    conn = db()
    conn.execute("UPDATE threads SET views = views + 1 WHERE id=?", (tid,))
    conn.commit()
    t = conn.execute(
        """
        SELECT t.*, u.name AS author_name, u.role AS author_role
        FROM threads t JOIN users u ON u.id = t.user_id WHERE t.id=?
        """,
        (tid,),
    ).fetchone()
    if not t:
        conn.close()
        raise HTTPException(404, "帖子不存在")
    replies = conn.execute(
        """
        SELECT r.id, r.content, r.created, u.name AS author_name, u.role AS author_role
        FROM replies r JOIN users u ON u.id = r.user_id
        WHERE r.thread_id=? ORDER BY r.created ASC
        """,
        (tid,),
    ).fetchall()
    conn.close()
    return {"thread": dict(t), "replies": [dict(r) for r in replies]}


@app.post("/api/forum/threads")
def create_thread(body: ThreadIn, user=Depends(current_user)):
    title = body.title.strip()
    content = body.content.strip()
    if not 4 <= len(title) <= 80:
        raise HTTPException(400, "标题需 4-80 个字符")
    if not 4 <= len(content) <= 10000:
        raise HTTPException(400, "正文需 4-10000 个字符")
    if body.cat not in VALID_CATS:
        raise HTTPException(400, "分类不合法")
    if body.cat == "notice" and user["role"] == "student":
        raise HTTPException(403, "公告仅官方/导师可发布")
    conn = db()
    cur = conn.execute(
        "INSERT INTO threads(user_id,cat,title,content,created) VALUES(?,?,?,?,?)",
        (user["id"], body.cat, title, content, now_str()),
    )
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return {"id": tid}


@app.post("/api/forum/threads/{tid}/replies")
def create_reply(tid: int, body: ReplyIn, user=Depends(current_user)):
    content = body.content.strip()
    if not 1 <= len(content) <= 5000:
        raise HTTPException(400, "回复需 1-5000 个字符")
    conn = db()
    t = conn.execute("SELECT id FROM threads WHERE id=?", (tid,)).fetchone()
    if not t:
        conn.close()
        raise HTTPException(404, "帖子不存在")
    cur = conn.execute(
        "INSERT INTO replies(thread_id,user_id,content,created) VALUES(?,?,?,?)",
        (tid, user["id"], content, now_str()),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return {"id": rid}


# ---------------- 接口:知识库(三级:完整公开 / 组专属 / 个人专属) ----------------
KB_LIST_COLS = (
    "d.id, d.level, d.group_id, d.owner_id, d.author_id, d.title, d.summary, d.cat, d.cat_id, d.icon, "
    "d.tags, d.date, d.link, d.sort, d.created, d.updated, "
    "au.name AS author_name, g.name AS group_name, ou.name AS owner_name, "
    "c.name AS cat_name, pc.name AS parent_cat_name, c.parent_id AS cat_parent_id"
)
KB_JOINS = (
    "FROM kb_docs d JOIN users au ON au.id = d.author_id "
    "LEFT JOIN groups g ON g.id = d.group_id LEFT JOIN users ou ON ou.id = d.owner_id "
    "LEFT JOIN kb_cats c ON c.id = d.cat_id LEFT JOIN kb_cats pc ON pc.id = c.parent_id"
)


@app.get("/api/kb/meta")
def kb_meta(user=Depends(optional_user)):
    """知识库导航元数据:是否工作人员、全部组、我的组、可浏览的工作人员列表"""
    conn = db()
    staff = is_staff(conn, user)
    out = {"staff": staff, "groups": [], "my_groups": [], "people": [], "me": user["id"] if user else 0}
    if staff:
        out["groups"] = [dict(r) for r in conn.execute("SELECT id,name FROM groups ORDER BY id").fetchall()]
        out["my_groups"] = sorted(user_group_ids(conn, user["id"]))
        out["people"] = [dict(r) for r in conn.execute(
            """
            SELECT DISTINCT u.id, u.name FROM users u
            LEFT JOIN group_members m ON m.user_id = u.id
            WHERE u.banned = 0 AND (u.role IN ('mentor','official') OR m.user_id IS NOT NULL)
            ORDER BY u.id
            """
        ).fetchall()]
    conn.close()
    return out


@app.get("/api/kb/cats")
def kb_cats(level: str = "all", group_id: int = 0, owner_id: int = 0, user=Depends(optional_user)):
    """知识库分类树(两层 parent_id)+ 每节点文档计数(含子级,按当前层级/权限范围统计)。"""
    conn = db()
    staff = is_staff(conn, user)
    conds, cargs = [], []
    if not staff:
        conds.append("level='all'")  # 非工作人员只统计公开级
    elif level:
        if level not in VALID_LEVELS:
            conn.close()
            raise HTTPException(400, "层级不合法")
        conds.append("level=?")
        cargs.append(level)
    if group_id:
        conds.append("group_id=?")
        cargs.append(group_id)
    if owner_id:
        conds.append("owner_id=?")
        cargs.append(owner_id)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    direct = {}
    for r in conn.execute(f"SELECT cat_id, COUNT(*) n FROM kb_docs {where} GROUP BY cat_id", cargs).fetchall():
        if r["cat_id"] is not None:
            direct[r["cat_id"]] = r["n"]
    uncat = conn.execute(
        f"SELECT COUNT(*) FROM kb_docs WHERE {' AND '.join(conds + ['cat_id IS NULL'])}", cargs
    ).fetchone()[0]
    cats = [dict(r) for r in conn.execute(
        "SELECT id,name,parent_id,sort FROM kb_cats ORDER BY sort, id").fetchall()]
    conn.close()
    by_parent = {}
    for c in cats:
        by_parent.setdefault(c["parent_id"], []).append(c)

    def build(c):
        kids = [build(k) for k in by_parent.get(c["id"], [])]
        cnt = direct.get(c["id"], 0) + sum(k["count"] for k in kids)
        return {**c, "count": cnt, "children": kids}

    tree = [build(c) for c in by_parent.get(None, [])]
    total = sum(t["count"] for t in tree) + uncat
    return {"cats": tree, "uncategorized": uncat, "total": total, "staff": staff}


@app.get("/api/kb/docs")
def kb_list(level: str = "", group_id: int = 0, owner_id: int = 0, cat: str = "", cat_id: int = 0,
            user=Depends(optional_user)):
    conn = db()
    staff = is_staff(conn, user)
    conds, args = [], []
    if not staff:
        conds.append("d.level='all'")  # 非工作人员只见公开级
    if level:
        if level not in VALID_LEVELS:
            conn.close()
            raise HTTPException(400, "层级不合法")
        conds.append("d.level=?")
        args.append(level)
    if group_id:
        conds.append("d.group_id=?")
        args.append(group_id)
    if owner_id:
        conds.append("d.owner_id=?")
        args.append(owner_id)
    if cat:
        conds.append("d.cat=?")
        args.append(cat)
    if cat_id == -1:  # 未分类
        conds.append("d.cat_id IS NULL")
    elif cat_id:  # 两层分类:选中节点 → 命中它自身 + 直接子级
        ids = kb_cat_filter_ids(conn, cat_id)
        conds.append("d.cat_id IN (%s)" % ",".join("?" * len(ids)))
        args += ids
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(
        f"SELECT {KB_LIST_COLS} {KB_JOINS} {where} ORDER BY d.sort DESC, d.id DESC", args
    ).fetchall()
    conn.close()
    return {"docs": [dict(r) for r in rows], "staff": staff}


@app.get("/api/kb/docs/{did}")
def kb_detail(did: int, user=Depends(optional_user)):
    conn = db()
    row = conn.execute(f"SELECT {KB_LIST_COLS}, d.content {KB_JOINS} WHERE d.id=?", (did,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "文档不存在")
    if row["level"] != "all" and not is_staff(conn, user):
        conn.close()
        raise HTTPException(403, "该文档仅工作人员可见")
    conn.close()
    return {"doc": dict(row)}


def check_kb_body(body: KbDocIn):
    if not 1 <= len(body.title.strip()) <= 120:
        raise HTTPException(400, "标题需 1-120 个字符")
    if len(body.summary) > 2000:
        raise HTTPException(400, "摘要过长(最多 2000 字)")
    if len(body.content.encode('utf-8')) > 50 * 1024 * 1024:
        raise HTTPException(400, "正文过大(最多 50MB)")
    if body.level not in VALID_LEVELS:
        raise HTTPException(400, "层级不合法")


def resolve_kb_target(conn, body: KbDocIn, user):
    """根据层级校验写入权限,返回 (group_id, owner_id)"""
    if body.level == "all":
        if user["role"] != "official":
            raise HTTPException(403, "公开文档仅官方账号可发布")
        return None, None
    if body.level == "group":
        if not body.group_id:
            raise HTTPException(400, "请选择所属组")
        if not conn.execute("SELECT id FROM groups WHERE id=?", (body.group_id,)).fetchone():
            raise HTTPException(404, "组不存在")
        if user["role"] != "official" and body.group_id not in user_group_ids(conn, user["id"]):
            raise HTTPException(403, "只能发布到自己所属的组")
        return body.group_id, None
    # personal
    owner = user["id"]
    if body.owner_id and body.owner_id != user["id"]:
        if user["role"] != "official":
            raise HTTPException(403, "只能发布到自己的个人库")
        if not conn.execute("SELECT id FROM users WHERE id=?", (body.owner_id,)).fetchone():
            raise HTTPException(404, "目标用户不存在")
        owner = body.owner_id
    return None, owner


# ---------------- 知识库分类树(kb_cats,两层:parent_id) ----------------
def kb_resolve_cat(conn, path):
    """按名称路径(如 ['04_方法论与洞察','01_角色一致性'])解析或创建分类,返回叶子 cat_id;空路径返回 None。"""
    parent = None
    cid = None
    for name in (path or []):
        name = (name or "").strip()
        if not name:
            break
        row = conn.execute("SELECT id FROM kb_cats WHERE name=? AND parent_id IS ?", (name, parent)).fetchone()
        if row:
            cid = row["id"]
        else:
            cid = conn.execute(
                "INSERT INTO kb_cats(name,parent_id,sort,created) VALUES(?,?,0,?)",
                (name, parent, now_str()),
            ).lastrowid
        parent = cid
    return cid


def kb_cat_filter_ids(conn, cat_id):
    """两层结构:选中某分类时,返回它自身 + 直接子级 id,用于列表过滤。"""
    ids = [cat_id] + [r["id"] for r in conn.execute(
        "SELECT id FROM kb_cats WHERE parent_id=?", (cat_id,)).fetchall()]
    return ids


def kb_resolve_cat_id(conn, body: KbDocIn):
    """从 body 解析 cat_id:优先 cat_path(解析/创建),否则用 cat_id(校验存在),都没有返回 None。"""
    if body.cat_path:
        return kb_resolve_cat(conn, body.cat_path)
    if body.cat_id:
        if not conn.execute("SELECT id FROM kb_cats WHERE id=?", (body.cat_id,)).fetchone():
            raise HTTPException(404, "分类不存在")
        return body.cat_id
    return None


@app.post("/api/kb/docs")
def kb_create(body: KbDocIn, user=Depends(require_staff)):
    check_kb_body(body)
    conn = db()
    try:
        gid, oid = resolve_kb_target(conn, body, user)
        cid = kb_resolve_cat_id(conn, body)
    except HTTPException:
        conn.close()
        raise
    now = now_str()
    cur = conn.execute(
        "INSERT INTO kb_docs(level,group_id,owner_id,author_id,title,summary,content,cat,cat_id,icon,tags,date,link,sort,created,updated) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (body.level, gid, oid, user["id"], body.title.strip(), body.summary, body.content,
         body.cat, cid, body.icon, body.tags, body.date, body.link, body.sort, now, now),
    )
    conn.commit()
    did = cur.lastrowid
    conn.close()
    return {"id": did}


@app.put("/api/kb/docs/{did}")
def kb_update(did: int, body: KbDocIn, user=Depends(require_staff)):
    check_kb_body(body)
    conn = db()
    row = conn.execute("SELECT id, author_id FROM kb_docs WHERE id=?", (did,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "文档不存在")
    if user["role"] != "official" and row["author_id"] != user["id"]:
        conn.close()
        raise HTTPException(403, "只能编辑自己发布的文档")
    try:
        gid, oid = resolve_kb_target(conn, body, user)
        cid = kb_resolve_cat_id(conn, body)
    except HTTPException:
        conn.close()
        raise
    conn.execute(
        "UPDATE kb_docs SET level=?,group_id=?,owner_id=?,title=?,summary=?,content=?,cat=?,cat_id=?,icon=?,tags=?,date=?,link=?,sort=?,updated=? "
        "WHERE id=?",
        (body.level, gid, oid, body.title.strip(), body.summary, body.content, body.cat, cid,
         body.icon, body.tags, body.date, body.link, body.sort, now_str(), did),
    )
    conn.commit()
    conn.close()
    return {"id": did, "updated": True}


@app.delete("/api/kb/docs/{did}")
def kb_delete(did: int, user=Depends(require_staff)):
    conn = db()
    row = conn.execute("SELECT id, author_id FROM kb_docs WHERE id=?", (did,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "文档不存在")
    if user["role"] != "official" and row["author_id"] != user["id"]:
        conn.close()
        raise HTTPException(403, "只能删除自己发布的文档")
    conn.execute("DELETE FROM kb_docs WHERE id=?", (did,))
    conn.commit()
    conn.close()
    return {"id": did, "deleted": True}


def kb_perm_conds(staff, level, group_id, owner_id, cat):
    """复用 kb_list 的权限+过滤条件,返回 (conds, args)。level 非法时抛 400。"""
    conds, args = [], []
    if not staff:
        conds.append("d.level='all'")  # 非工作人员只见公开级
    if level:
        if level not in VALID_LEVELS:
            raise HTTPException(400, "层级不合法")
        conds.append("d.level=?")
        args.append(level)
    if group_id:
        conds.append("d.group_id=?")
        args.append(group_id)
    if owner_id:
        conds.append("d.owner_id=?")
        args.append(owner_id)
    if cat:
        conds.append("d.cat=?")
        args.append(cat)
    return conds, args


@app.get("/api/kb/search")
def kb_search(q: str = "", level: str = "", group_id: int = 0, owner_id: int = 0, cat: str = "",
              user=Depends(optional_user)):
    """知识库全文搜索:>=3 字符走 FTS5 trigram(按相关度排序),更短走 LIKE 回退。权限同列表。"""
    q = (q or "").strip()
    if not q:
        return {"docs": [], "staff": False, "q": ""}
    conn = db()
    staff = is_staff(conn, user)
    try:
        conds, args = kb_perm_conds(staff, level, group_id, owner_id, cat)
    except HTTPException:
        conn.close()
        raise
    if len(q) >= 3:
        # 引号包裹作为短语,避免用户输入中的符号被当作 FTS5 操作符;trigram 做子串匹配
        match = '"' + q.replace('"', '""') + '"'
        joins = KB_JOINS + " JOIN kb_fts ON kb_fts.rowid = d.id"
        where = "WHERE " + " AND ".join(["kb_fts MATCH ?"] + conds)
        sql_args = [match] + args
        order = "ORDER BY rank"
    else:
        joins = KB_JOINS
        like = f"%{q}%"
        where = "WHERE " + " AND ".join(
            ["(d.title LIKE ? OR d.summary LIKE ? OR d.content LIKE ? OR d.tags LIKE ?)"] + conds)
        sql_args = [like, like, like, like] + args
        order = "ORDER BY d.sort DESC, d.id DESC"
    rows = conn.execute(f"SELECT {KB_LIST_COLS} {joins} {where} {order} LIMIT 200", sql_args).fetchall()
    conn.close()
    return {"docs": [dict(r) for r in rows], "staff": staff, "q": q}


@app.post("/api/kb/import")
def kb_import(body: KbImportIn, user=Depends(require_admin)):
    """批量导入文档(官方权限,事务化)。支持 all/group/personal 目标;purge 时先清空同目标范围旧文档以幂等。"""
    if not body.docs:
        raise HTTPException(400, "没有要导入的文档")
    if len(body.docs) > 1000:
        raise HTTPException(400, "单次导入最多 1000 篇,请分批")
    if body.level not in VALID_LEVELS:
        raise HTTPException(400, "层级不合法")
    for d in body.docs:
        check_kb_body(d)
    conn = db()
    try:
        gid = oid = None
        if body.level == "group":
            if not body.group_id:
                raise HTTPException(400, "请选择所属组")
            if not conn.execute("SELECT id FROM groups WHERE id=?", (body.group_id,)).fetchone():
                raise HTTPException(404, "组不存在")
            gid = body.group_id
        elif body.level == "personal":
            oid = body.owner_id or user["id"]
            if not conn.execute("SELECT id FROM users WHERE id=?", (oid,)).fetchone():
                raise HTTPException(404, "目标用户不存在")
        now = now_str()
        purged = 0
        if body.purge or body.purge_public:
            if body.level == "all":
                purged = conn.execute("DELETE FROM kb_docs WHERE level='all' AND author_id=?", (user["id"],)).rowcount
            elif body.level == "group":
                purged = conn.execute("DELETE FROM kb_docs WHERE level='group' AND group_id=?", (gid,)).rowcount
            else:
                purged = conn.execute("DELETE FROM kb_docs WHERE level='personal' AND owner_id=?", (oid,)).rowcount
        for d in body.docs:
            cid = kb_resolve_cat(conn, d.cat_path) if d.cat_path else (d.cat_id or None)
            conn.execute(
                "INSERT INTO kb_docs(level,group_id,owner_id,author_id,title,summary,content,cat,cat_id,icon,tags,date,link,sort,created,updated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (body.level, gid, oid, user["id"], d.title.strip(), d.summary, d.content, d.cat, cid,
                 d.icon, d.tags, d.date, d.link, d.sort, now, now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return {"imported": len(body.docs), "purged": purged, "level": body.level}


# ---------------- 接口:内容(作品/商单/案例/文档/课程) ----------------
ITEM_COLS = "id,kind,title,summary,cat,icon,badge,tags,author,date,extra,link,sort,created"


@app.get("/api/content/{kind}")
def list_items(kind: str):
    check_kind(kind)
    conn = db()
    rows = conn.execute(
        f"SELECT {ITEM_COLS} FROM items WHERE kind=? ORDER BY sort DESC, id DESC", (kind,)
    ).fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/content/{kind}")
def create_item(kind: str, body: ItemIn, admin=Depends(require_admin)):
    check_kind(kind)
    check_item(body)
    conn = db()
    cur = conn.execute(
        "INSERT INTO items(kind,title,summary,cat,icon,badge,tags,author,date,extra,link,sort,created) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, body.title.strip(), body.summary, body.cat, body.icon, body.badge, body.tags,
         body.author, body.date, body.extra, body.link, body.sort, now_str()),
    )
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return {"id": iid}


@app.put("/api/content/{kind}/{iid}")
def update_item(kind: str, iid: int, body: ItemIn, admin=Depends(require_admin)):
    check_kind(kind)
    check_item(body)
    conn = db()
    row = conn.execute("SELECT id FROM items WHERE id=? AND kind=?", (iid, kind)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "内容不存在")
    conn.execute(
        "UPDATE items SET title=?,summary=?,cat=?,icon=?,badge=?,tags=?,author=?,date=?,extra=?,link=?,sort=? "
        "WHERE id=?",
        (body.title.strip(), body.summary, body.cat, body.icon, body.badge, body.tags,
         body.author, body.date, body.extra, body.link, body.sort, iid),
    )
    conn.commit()
    conn.close()
    return {"id": iid, "updated": True}


@app.delete("/api/content/{kind}/{iid}")
def delete_item(kind: str, iid: int, admin=Depends(require_admin)):
    check_kind(kind)
    conn = db()
    row = conn.execute("SELECT id FROM items WHERE id=? AND kind=?", (iid, kind)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "内容不存在")
    conn.execute("DELETE FROM items WHERE id=?", (iid,))
    conn.commit()
    conn.close()
    return {"id": iid, "deleted": True}


# ---------------- 接口:管理后台(仅 official) ----------------
@app.get("/api/admin/stats")
def admin_stats(admin=Depends(require_admin)):
    conn = db()
    stats = {
        "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "banned_users": conn.execute("SELECT COUNT(*) FROM users WHERE banned=1").fetchone()[0],
        "threads": conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0],
        "replies": conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0],
        "views": conn.execute("SELECT COALESCE(SUM(views),0) FROM threads").fetchone()[0],
        "items": conn.execute("SELECT COUNT(*) FROM items").fetchone()[0],
        "kb_docs": conn.execute("SELECT COUNT(*) FROM kb_docs").fetchone()[0],
        "groups": conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0],
    }
    today = time.strftime("%Y-%m-%d")
    stats["users_today"] = conn.execute(
        "SELECT COUNT(*) FROM users WHERE created LIKE ?", (today + "%",)
    ).fetchone()[0]
    stats["threads_today"] = conn.execute(
        "SELECT COUNT(*) FROM threads WHERE created LIKE ?", (today + "%",)
    ).fetchone()[0]
    conn.close()
    return stats


@app.get("/api/admin/users")
def admin_users(admin=Depends(require_admin)):
    conn = db()
    rows = conn.execute(
        """
        SELECT u.id, u.email, u.name, u.role, u.banned, u.created,
               (SELECT COUNT(*) FROM threads t WHERE t.user_id = u.id) AS thread_count,
               (SELECT COUNT(*) FROM replies r WHERE r.user_id = u.id) AS reply_count
        FROM users u ORDER BY u.id DESC
        """
    ).fetchall()
    conn.close()
    return {"users": [dict(r) for r in rows]}


@app.post("/api/admin/users/{uid}/ban")
def admin_ban(uid: int, body: BanIn, admin=Depends(require_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "不能封禁自己")
    conn = db()
    row = conn.execute("SELECT id, role FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "用户不存在")
    if row["role"] == "official" and body.banned:
        conn.close()
        raise HTTPException(400, "不能封禁官方账号")
    conn.execute("UPDATE users SET banned=? WHERE id=?", (1 if body.banned else 0, uid))
    conn.commit()
    conn.close()
    return {"id": uid, "banned": body.banned}


@app.post("/api/admin/users/{uid}/role")
def admin_role(uid: int, body: RoleIn, admin=Depends(require_admin)):
    if body.role not in VALID_ROLES:
        raise HTTPException(400, "角色不合法")
    if uid == admin["id"]:
        raise HTTPException(400, "不能修改自己的角色")
    conn = db()
    row = conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "用户不存在")
    conn.execute("UPDATE users SET role=? WHERE id=?", (body.role, uid))
    conn.commit()
    conn.close()
    return {"id": uid, "role": body.role}


@app.post("/api/admin/threads/{tid}/pin")
def admin_pin(tid: int, body: PinIn, admin=Depends(require_admin)):
    conn = db()
    row = conn.execute("SELECT id FROM threads WHERE id=?", (tid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "帖子不存在")
    conn.execute("UPDATE threads SET pinned=? WHERE id=?", (1 if body.pinned else 0, tid))
    conn.commit()
    conn.close()
    return {"id": tid, "pinned": body.pinned}


@app.delete("/api/admin/threads/{tid}")
def admin_delete_thread(tid: int, admin=Depends(require_admin)):
    conn = db()
    row = conn.execute("SELECT id FROM threads WHERE id=?", (tid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "帖子不存在")
    conn.execute("DELETE FROM replies WHERE thread_id=?", (tid,))
    conn.execute("DELETE FROM threads WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return {"id": tid, "deleted": True}


@app.get("/api/admin/groups")
def admin_groups(admin=Depends(require_admin)):
    conn = db()
    groups = []
    for g in conn.execute("SELECT id,name,created FROM groups ORDER BY id").fetchall():
        members = conn.execute(
            """
            SELECT u.id, u.name, u.email, u.role FROM group_members m
            JOIN users u ON u.id = m.user_id WHERE m.group_id=? ORDER BY u.id
            """,
            (g["id"],),
        ).fetchall()
        d = dict(g)
        d["members"] = [dict(m) for m in members]
        d["doc_count"] = conn.execute("SELECT COUNT(*) FROM kb_docs WHERE group_id=?", (g["id"],)).fetchone()[0]
        groups.append(d)
    conn.close()
    return {"groups": groups}


@app.post("/api/admin/groups")
def admin_group_create(body: GroupIn, admin=Depends(require_admin)):
    name = body.name.strip()
    if not 1 <= len(name) <= 30:
        raise HTTPException(400, "组名需 1-30 个字符")
    conn = db()
    try:
        cur = conn.execute("INSERT INTO groups(name,created) VALUES(?,?)", (name, now_str()))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(400, "组名已存在")
    gid = cur.lastrowid
    conn.close()
    return {"id": gid, "name": name}


@app.delete("/api/admin/groups/{gid}")
def admin_group_delete(gid: int, admin=Depends(require_admin)):
    conn = db()
    if not conn.execute("SELECT id FROM groups WHERE id=?", (gid,)).fetchone():
        conn.close()
        raise HTTPException(404, "组不存在")
    n = conn.execute("SELECT COUNT(*) FROM kb_docs WHERE group_id=?", (gid,)).fetchone()[0]
    if n:
        conn.close()
        raise HTTPException(400, f"该组还有 {n} 篇文档,请先迁移或删除后再删组")
    conn.execute("DELETE FROM group_members WHERE group_id=?", (gid,))
    conn.execute("DELETE FROM groups WHERE id=?", (gid,))
    conn.commit()
    conn.close()
    return {"id": gid, "deleted": True}


@app.post("/api/admin/groups/{gid}/members")
def admin_group_add_member(gid: int, body: MemberIn, admin=Depends(require_admin)):
    conn = db()
    if not conn.execute("SELECT id FROM groups WHERE id=?", (gid,)).fetchone():
        conn.close()
        raise HTTPException(404, "组不存在")
    if not conn.execute("SELECT id FROM users WHERE id=?", (body.user_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "用户不存在")
    conn.execute("INSERT OR IGNORE INTO group_members(group_id,user_id) VALUES(?,?)", (gid, body.user_id))
    conn.commit()
    conn.close()
    return {"group_id": gid, "user_id": body.user_id, "added": True}


@app.delete("/api/admin/groups/{gid}/members/{uid}")
def admin_group_remove_member(gid: int, uid: int, admin=Depends(require_admin)):
    conn = db()
    conn.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?", (gid, uid))
    conn.commit()
    conn.close()
    return {"group_id": gid, "user_id": uid, "removed": True}


@app.delete("/api/admin/replies/{rid}")
def admin_delete_reply(rid: int, admin=Depends(require_admin)):
    conn = db()
    row = conn.execute("SELECT id FROM replies WHERE id=?", (rid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "回复不存在")
    conn.execute("DELETE FROM replies WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    return {"id": rid, "deleted": True}


# ---------------- 接口:知识库分类管理(仅 official) ----------------
def _cat_parent_or_none(conn, parent_id):
    """校验 parent_id 合法(存在且为顶层),返回 None 或合法父 id。"""
    pid = parent_id or None
    if pid:
        prow = conn.execute("SELECT id, parent_id FROM kb_cats WHERE id=?", (pid,)).fetchone()
        if not prow:
            raise HTTPException(404, "父分类不存在")
        if prow["parent_id"] is not None:
            raise HTTPException(400, "只支持两层分类,不能在子分类下再建子分类")
    return pid


@app.post("/api/admin/kb/cats")
def admin_cat_create(body: KbCatIn, admin=Depends(require_admin)):
    name = body.name.strip()
    if not 1 <= len(name) <= 60:
        raise HTTPException(400, "分类名需 1-60 字")
    conn = db()
    try:
        pid = _cat_parent_or_none(conn, body.parent_id)
    except HTTPException:
        conn.close()
        raise
    if conn.execute("SELECT id FROM kb_cats WHERE name=? AND parent_id IS ?", (name, pid)).fetchone():
        conn.close()
        raise HTTPException(400, "同级已有同名分类")
    cid = conn.execute("INSERT INTO kb_cats(name,parent_id,sort,created) VALUES(?,?,?,?)",
                       (name, pid, body.sort, now_str())).lastrowid
    conn.commit()
    conn.close()
    return {"id": cid}


@app.put("/api/admin/kb/cats/{cid}")
def admin_cat_update(cid: int, body: KbCatIn, admin=Depends(require_admin)):
    name = body.name.strip()
    if not 1 <= len(name) <= 60:
        raise HTTPException(400, "分类名需 1-60 字")
    conn = db()
    if not conn.execute("SELECT id FROM kb_cats WHERE id=?", (cid,)).fetchone():
        conn.close()
        raise HTTPException(404, "分类不存在")
    pid = body.parent_id or None
    try:
        if pid:
            if pid == cid:
                raise HTTPException(400, "不能把分类设为自己的子级")
            _cat_parent_or_none(conn, pid)
            if conn.execute("SELECT 1 FROM kb_cats WHERE parent_id=?", (cid,)).fetchone():
                raise HTTPException(400, "该分类下有子分类,不能移动为子级")
    except HTTPException:
        conn.close()
        raise
    if conn.execute("SELECT id FROM kb_cats WHERE name=? AND parent_id IS ? AND id<>?", (name, pid, cid)).fetchone():
        conn.close()
        raise HTTPException(400, "同级已有同名分类")
    conn.execute("UPDATE kb_cats SET name=?, parent_id=?, sort=? WHERE id=?", (name, pid, body.sort, cid))
    conn.commit()
    conn.close()
    return {"id": cid, "updated": True}


@app.delete("/api/admin/kb/cats/{cid}")
def admin_cat_delete(cid: int, admin=Depends(require_admin)):
    conn = db()
    if not conn.execute("SELECT id FROM kb_cats WHERE id=?", (cid,)).fetchone():
        conn.close()
        raise HTTPException(404, "分类不存在")
    if conn.execute("SELECT 1 FROM kb_cats WHERE parent_id=?", (cid,)).fetchone():
        conn.close()
        raise HTTPException(400, "该分类下还有子分类,请先删除或移走子分类")
    n = conn.execute("SELECT COUNT(*) FROM kb_docs WHERE cat_id=?", (cid,)).fetchone()[0]
    if n:
        conn.close()
        raise HTTPException(400, f"该分类下有 {n} 篇文档,请先移走再删除")
    conn.execute("DELETE FROM kb_cats WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return {"id": cid, "deleted": True}


# ---------------- 静态站点 ----------------
@app.get("/{full_path:path}")
def spa(full_path: str):
    # 优先返回 STATIC_DIR 下的真实文件(图片等资源),否则回退到 SPA 入口
    if full_path:
        base = os.path.abspath(STATIC_DIR)
        # 兼容 /static/ 前缀(md里引用的图片/视频用 /static/feishu_imgs/... 等)
        rel = full_path[len("static/"):] if full_path.startswith("static/") else full_path
        candidate = os.path.abspath(os.path.join(base, rel))
        if candidate.startswith(base + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


init_db()
