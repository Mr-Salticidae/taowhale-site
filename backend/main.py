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
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "whale.db"))
SECRET = os.environ.get("JWT_SECRET", "please-change-me")
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


VALID_CATS = {"qa", "critique", "share", "notice"}
VALID_ROLES = {"student", "mentor", "official"}
VALID_KINDS = {"work", "gig", "case", "doc", "course"}


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


# ---------------- 静态站点 ----------------
@app.get("/{full_path:path}")
def spa(full_path: str):
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


init_db()
