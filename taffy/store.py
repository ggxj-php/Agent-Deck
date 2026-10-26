"""会话与消息的持久化（SQLite）。

一条会话 = sessions 一行 + messages 若干行。消息整条存成 JSON（payload 列）——
assistant 消息可能带 tool_calls，tool 消息带 tool_call_id 和长结果，结构不固定，
用 JSON 最省事，以后加字段也不用改表。
"""
import json
import time
import uuid

from . import db

LIST_LIMIT = 200          # 侧边栏最多列这么多条，再多就不现实了
TITLE_MAX = 24


def _title_from(text):
    """拿用户第一句话当标题。"""
    text = " ".join((text or "").split())
    if not text:
        return "新会话"
    return text[:TITLE_MAX] + ("…" if len(text) > TITLE_MAX else "")


def create(user_id, title=""):
    sid = uuid.uuid4().hex
    now = time.time()
    with db.cursor() as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, cut_off, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (sid, user_id, title or "新会话", now, now),
        )
    return {"id": sid, "title": title or "新会话", "messages": 0,
            "created_at": now, "updated_at": now}


def list_for_user(user_id):
    """侧边栏用：按最近更新排序，带消息条数。"""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT s.id, s.title, s.created_at, s.updated_at, "
            "       (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS msg_count "
            "FROM sessions s WHERE s.user_id = ? "
            "ORDER BY s.updated_at DESC LIMIT ?",
            (user_id, LIST_LIMIT),
        ).fetchall()
    return [{"id": r["id"], "title": r["title"], "messages": r["msg_count"],
             "created_at": r["created_at"], "updated_at": r["updated_at"]} for r in rows]


def get(user_id, session_id):
    """按 id 取会话，顺带校验归属（不是自己的当不存在）。"""
    with db.cursor() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def delete(user_id, session_id):
    with db.cursor() as conn:
        cur = conn.execute(
            "DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
        return cur.rowcount > 0


def rename(user_id, session_id, title):
    title = " ".join((title or "").split())[:60] or "新会话"
    with db.cursor() as conn:
        conn.execute(
            "UPDATE sessions SET title = ? WHERE id = ? AND user_id = ?",
            (title, session_id, user_id),
        )


def load_messages(session_id):
    """把一条会话的全部消息读出来（不含 system 提示词）。"""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT payload FROM messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
    out = []
    for row in rows:
        try:
            item = json.loads(row["payload"])
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def save_messages(session_id, messages, cut_off=False, first_user_text=""):
    """整条会话的消息覆盖写。

    消息量不大（几十到几百条），整条重写比做增量 diff 简单可靠；
    顺便把 updated_at / cut_off / 标题一起更新。
    """
    now = time.time()
    payloads = [(session_id, index, json.dumps(item, ensure_ascii=False), now)
                for index, item in enumerate(messages or [])]
    with db.cursor() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        if payloads:
            conn.executemany(
                "INSERT INTO messages (session_id, seq, payload, ts) VALUES (?, ?, ?, ?)",
                payloads,
            )
        conn.execute(
            "UPDATE sessions SET updated_at = ?, cut_off = ? WHERE id = ?",
            (now, 1 if cut_off else 0, session_id),
        )
        if first_user_text:
            # 只改「还没起过名」的会话，别把用户改过的标题冲掉
            conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ? AND (title = '' OR title = '新会话')",
                (_title_from(first_user_text), session_id),
            )


def touch(session_id):
    """只更新时间戳（会话有过动静但不是被整条改写的场景）。"""
    with db.cursor() as conn:
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?",
                     (time.time(), session_id))


def count_for_user(user_id):
    with db.cursor() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?",
                           (user_id,)).fetchone()
    return row["n"] if row else 0

def _content_of(message):
    """把一条消息的 content 拍成纯文本。带图那轮 content 是个列表，这里只留一句说明。"""
    content = message.get("content")
    if isinstance(content, list):
        texts = [part.get("text", "") for part in content
                 if isinstance(part, dict) and part.get("type") == "text"]
        has_image = any(isinstance(part, dict) and part.get("type") == "image_url"
                        for part in content)
        text = "\n".join(t for t in texts if t)
        return (text + "\n[图片]" if has_image else text).strip()
    return (content or "").strip()


def display(messages):
    """给网页聊天区看的精简版：只留用户说的和塔菲的正文，工具那些不摆出来。"""
    items = []
    for message in messages:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _content_of(message)
        if text:
            items.append({"role": role, "text": text})
    return items
