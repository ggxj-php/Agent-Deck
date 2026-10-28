"""SQLite 存储：用户、登录态、会话、消息。

为什么把会话搬出内存：以前每个会话对应一个常驻的 DeckAgent，聊天历史全在 RAM 里，
开一百个会话就吃一百份内存，重启还全丢。现在每轮请求现从库里读、跑完写回去，
内存只跟「此刻正在跑的那几条」有关，会话数量不设上限。

单文件 SQLite + WAL，几千个用户毫无压力；真到并发撑不住再换 Postgres。
"""
import os
import sqlite3
import threading
from contextlib import contextmanager

from .config import PROJECT_ROOT

# 库文件放 .cache 下（已在 .gitignore 里），可以用环境变量换位置
DB_PATH = os.environ.get("TAFFY_DB_PATH", "").strip() or os.path.join(
    PROJECT_ROOT, ".cache", "taffy.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    invite_code   TEXT    NOT NULL UNIQUE,
    invited_by    TEXT    NOT NULL DEFAULT '',
    created_at    REAL    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,   -- 1 = 能进后台
    disabled      INTEGER NOT NULL DEFAULT 0,   -- 1 = 封号，登录/接口一律拒绝
    ban_until     REAL    NOT NULL DEFAULT 0,   -- 封到这个时间（0 = 永久；到点自动解封）
    note          TEXT    NOT NULL DEFAULT '',  -- 后台备注
    last_login    REAL    NOT NULL DEFAULT 0,
    last_active   REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL    NOT NULL,
    expires_at REAL    NOT NULL,
    ip         TEXT    NOT NULL DEFAULT '',
    ua         TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON auth_tokens(user_id);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT    NOT NULL DEFAULT '',
    cut_off    INTEGER NOT NULL DEFAULT 0,
    created_at REAL    NOT NULL,
    updated_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    payload    TEXT    NOT NULL,
    ts         REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS artifacts (
    -- render_html 产物：存下来，刷新页面 / 重启服务之后还能看到那张卡片
    id         TEXT    PRIMARY KEY,
    session_id TEXT    NOT NULL DEFAULT '',
    user_id    INTEGER NOT NULL DEFAULT 0,
    title      TEXT    NOT NULL DEFAULT '',
    html       TEXT    NOT NULL DEFAULT '',
    created_at REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id);
"""

_lock = threading.Lock()
_ready = False


def connect():
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn):
    """老库补列（建表语句只在第一次生效，后来加的字段得单独 ALTER）。"""
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    additions = {
        "is_admin": "INTEGER NOT NULL DEFAULT 0",
        "disabled": "INTEGER NOT NULL DEFAULT 0",
        "ban_until": "REAL NOT NULL DEFAULT 0",
        "note": "TEXT NOT NULL DEFAULT ''",
        "last_login": "REAL NOT NULL DEFAULT 0",
        "last_active": "REAL NOT NULL DEFAULT 0",
    }
    for name, ddl in additions.items():
        if name not in user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
    # 老用户以前没记 last_login（默认 0）：按「注册即上次登录」补成注册时间
    conn.execute("UPDATE users SET last_login = created_at WHERE last_login = 0")


def init():
    """建表（幂等）。第一次用到库的时候自动跑一次。"""
    global _ready
    with _lock:
        if _ready:
            return
        conn = connect()
        try:
            conn.executescript(_SCHEMA)
            _migrate(conn)
            conn.commit()
        finally:
            conn.close()
        _ready = True


@contextmanager
def cursor():
    """一个事务：正常结束提交，抛异常回滚。"""
    if not _ready:
        init()
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def health():
    """给自检用：能不能连上、有哪些表。"""
    try:
        conn = connect()
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        finally:
            conn.close()
        return {"ok": True, "path": DB_PATH, "tables": names}
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "path": DB_PATH, "error": str(exc)}
