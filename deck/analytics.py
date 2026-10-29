"""后台「用户活跃度与行为分析」：分页工具 + 活跃度汇总 + 给模型的分析提示词。

分析是只读的：只统计 users / sessions / messages 表，不碰任何账号状态。
"""
import json
import math
import time

from . import db


def page_bounds(total, page=1, size=10):
    """算分页边界：返回 (page, size, pages, offset)。"""
    total = max(0, int(total or 0))
    size = max(5, min(50, int(size or 10)))
    pages = max(1, math.ceil(total / size))
    page = max(1, min(int(page or 1), pages))
    return page, size, pages, (page - 1) * size


def _days_ago(now, ts):
    if not ts:
        return None
    return round(max(0.0, now - float(ts)) / 86400.0, 1)


def collect_user_activity(now=None, trend_days=14):
    """汇总每个用户的活跃度指标 + 全局趋势，给后台展示和 AI 分析用。"""
    now = time.time() if now is None else now
    week = now - 7 * 86400
    month = now - 30 * 86400

    with db.cursor() as conn:
        users = [dict(row) for row in conn.execute(
            "SELECT id, name, created_at, last_login, last_active, is_admin, disabled, "
            "       ban_until, note FROM users ORDER BY COALESCE(last_active, 0) DESC, id")]
        sess_rows = {row["user_id"]: dict(row) for row in conn.execute(
            "SELECT user_id, COUNT(*) AS n, MAX(updated_at) AS last_at "
            "FROM sessions GROUP BY user_id")}
        msg_rows = {row["user_id"]: dict(row) for row in conn.execute(
            "SELECT s.user_id AS user_id, COUNT(m.id) AS n, "
            "       SUM(CASE WHEN m.ts >= ? THEN 1 ELSE 0 END) AS n7, "
            "       SUM(CASE WHEN m.ts >= ? THEN 1 ELSE 0 END) AS n30, "
            "       COUNT(DISTINCT CASE WHEN m.ts >= ? "
            "             THEN date(m.ts, 'unixepoch', 'localtime') END) AS days30, "
            "       MAX(m.ts) AS last_ts "
            "FROM messages m JOIN sessions s ON s.id = m.session_id "
            "GROUP BY s.user_id", (week, month, month))}
        trend = [dict(row) for row in conn.execute(
            "SELECT date(ts, 'unixepoch', 'localtime') AS day, COUNT(*) AS n "
            "FROM messages WHERE ts >= ? GROUP BY day ORDER BY day",
            (now - trend_days * 86400,))]
        titles = [str(row["title"]) for row in conn.execute(
            "SELECT title FROM sessions WHERE title IS NOT NULL AND title != '' "
            "ORDER BY updated_at DESC LIMIT 25")]

    rows = []
    for user in users:
        sess = sess_rows.get(user["id"]) or {}
        msg = msg_rows.get(user["id"]) or {}
        sessions = int(sess.get("n") or 0)
        messages = int(msg.get("n") or 0)
        last_active = user.get("last_active") or msg.get("last_ts")
        rows.append({
            "name": user["name"],
            "role": "管理员" if user.get("is_admin") else "普通用户",
            "disabled": bool(user.get("disabled")),
            "created_days_ago": _days_ago(now, user.get("created_at")),
            "last_active_days_ago": _days_ago(now, last_active),
            "sessions": sessions,
            "messages": messages,
            "messages_7d": int(msg.get("n7") or 0),
            "active_days_30d": int(msg.get("days30") or 0),
            "avg_messages_per_session": round(messages / sessions, 1) if sessions else 0.0,
            "note": (user.get("note") or "")[:40],
        })

    active_24h = sum(1 for r in rows if (r["last_active_days_ago"] is not None
                                        and r["last_active_days_ago"] <= 1))
    active_7d = sum(1 for r in rows if (r["last_active_days_ago"] is not None
                                        and r["last_active_days_ago"] <= 7))
    new_7d = sum(1 for r in rows if (r["created_days_ago"] is not None
                                     and r["created_days_ago"] <= 7))
    silent_7d = sum(1 for r in rows if r["sessions"] > 0
                    and (r["last_active_days_ago"] is None
                         or r["last_active_days_ago"] > 7))
    never = sum(1 for r in rows if r["sessions"] == 0)
    total_messages = sum(r["messages"] for r in rows)
    total_sessions = sum(r["sessions"] for r in rows)
    return {
        "generated_at": now,
        "totals": {
            "users": len(rows),
            "admins": sum(1 for r in rows if r["role"] == "管理员"),
            "disabled": sum(1 for r in rows if r["disabled"]),
            "new_7d": new_7d,
            "active_24h": active_24h,
            "active_7d": active_7d,
            "silent_7d": silent_7d,
            "never_active": never,
            "sessions": total_sessions,
            "messages": total_messages,
        },
        "daily_messages": trend,
        "recent_session_titles": titles,
        "users": sorted(rows, key=lambda r: (
            r["last_active_days_ago"] if r["last_active_days_ago"] is not None else 9e9,
            r["messages"])),
    }


ANALYSIS_SYSTEM = """你是一名产品运营分析师，在给一个自建 AI 助手（网页 + 安卓 App）做用户活跃度与行为分析。
你会收到一份统计 JSON（用户数、活跃、留存、消息量、会话标题等），不要编造数据里没有的东西。

输出中文 Markdown，结构固定（每节都要有，没有数据的节写「暂无数据」）：
## 一、整体概况
用 3~5 句话概括用户规模、活跃度、消息量趋势。
## 二、活跃分层
按「高活跃 / 中度 / 沉默 / 从未使用」给人数和典型特征（可引用用户名）。
## 三、使用行为特征
从会话标题和消息量推断他们在用助手做什么、使用时段/频次特征。
## 四、风险与流失预警
指出沉默用户、异常使用（比如消息极少/极多）、可能流失的信号。
## 五、运营建议
3~6 条可执行建议，每条一句话，能落到具体动作（引导、功能推荐、回访等）。

要求：只做分析，不要输出代码；不要泄露密码/token 等敏感信息；总长度控制在 800 字以内。"""


def build_analysis_prompt(stats):
    """把统计 JSON 拼成模型请求体（system + user）。"""
    payload = {
        "统计时间": time.strftime("%Y-%m-%d %H:%M", time.localtime(stats["generated_at"])),
        "总量": stats["totals"],
        "每日消息量": stats["daily_messages"],
        "最近会话标题": stats["recent_session_titles"],
        "用户明细": stats["users"],
    }
    return [
        {"role": "system", "content": ANALYSIS_SYSTEM},
        {"role": "user",
         "content": "统计 JSON：\n```json\n"
                    + json.dumps(payload, ensure_ascii=False, indent=1)
                    + "\n```\n请按要求输出分析。"},
    ]
