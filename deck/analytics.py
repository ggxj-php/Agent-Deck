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


def _local(ts, fmt="%H"):
    try:
        return time.strftime(fmt, time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return ""


def _message_flags(payload):
    """从消息 payload 里抠行为特征：角色、工具调用、附件、表情包。"""
    role = ""
    text = ""
    tool_calls = 0
    try:
        data = json.loads(payload or "{}")
        role = str(data.get("role") or "")
        content = data.get("content")
        text = content if isinstance(content, str) else str(content or "")
        tool_calls = len(data.get("tool_calls") or [])
    except (ValueError, TypeError):
        text = str(payload or "")
    return {
        "role": role,
        "tool_calls": tool_calls,
        "has_attachment": ("uploads/" in text or "[图片" in text or "[附件" in text),
        "has_sticker": "[sticker:" in text,
        "chars": len(text),
    }


def _streak_days(days, now):
    """连续活跃天数：从今天（或昨天）往回数。days 是 date 字符串集合。"""
    if not days:
        return 0
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    yesterday = time.strftime("%Y-%m-%d", time.localtime(now - 86400))
    if today in days:
        cursor = now
    elif yesterday in days:
        cursor = now - 86400
    else:
        return 0
    streak = 0
    while True:
        key = time.strftime("%Y-%m-%d", time.localtime(cursor))
        if key not in days:
            return streak
        streak += 1
        cursor -= 86400


def collect_user_activity(now=None, trend_days=30):
    """汇总每个用户的活跃度/行为指标 + 全局趋势，给后台展示和 AI 分析用。"""
    now = time.time() if now is None else now
    week = now - 7 * 86400

    with db.cursor() as conn:
        users = [dict(row) for row in conn.execute(
            "SELECT id, name, created_at, last_login, last_active, is_admin, disabled, "
            "       ban_until, note FROM users ORDER BY COALESCE(last_active, 0) DESC, id")]
        session_rows = [dict(row) for row in conn.execute(
            "SELECT id, user_id, title, created_at, updated_at FROM sessions")]
        message_rows = [dict(row) for row in conn.execute(
            "SELECT session_id, payload, ts FROM messages")]

    sessions_of = {}
    for sess in session_rows:
        sessions_of.setdefault(sess["user_id"], []).append(sess)
    msgs_of_session = {}
    for msg in message_rows:
        msgs_of_session.setdefault(msg["session_id"], []).append(msg)

    per_user = {}
    hour_hist = [0] * 24
    weekday_hist = [0] * 7
    daily = {}
    for sess in session_rows:
        user = per_user.setdefault(sess["user_id"], {
            "days": set(), "days7": set(), "msg": 0, "user_msg": 0,
            "assistant_msg": 0, "tool_calls": 0, "attachments": 0,
            "stickers": 0, "chars": 0, "first_ts": None, "last_ts": None,
            "sessions": 0, "session_msgs": [], "session_minutes": [], "msg7": 0,
        })
        user["sessions"] += 1
        session_msgs = msgs_of_session.get(sess["id"], [])
        # 会话时长按「这条会话里第一条到最后一条消息」算：
        # 直接拿 sessions.created_at→updated_at 会把「几天后再来续聊」算成几千分钟。
        stamps = sorted(float(m.get("ts") or 0) for m in session_msgs)
        stamps = [ts for ts in stamps if ts > 0]
        created_at = float(sess.get("created_at") or 0)
        if stamps and created_at > 0:
            # 从「会话创建」到最后一条消息，最多算 6 小时（跨天续聊不算时长）
            span = min(max(0.0, stamps[-1] - created_at), 6 * 3600)
            if span > 0:
                user["session_minutes"].append(span / 60.0)
        user["session_msgs"].append(len(session_msgs))
        for msg in session_msgs:
            ts = float(msg.get("ts") or 0)
            if ts <= 0:
                continue
            flags = _message_flags(msg.get("payload"))
            user["msg"] += 1
            if ts >= week:
                user["msg7"] += 1
            user["chars"] += flags["chars"]
            user["tool_calls"] += flags["tool_calls"]
            user["attachments"] += 1 if flags["has_attachment"] else 0
            user["stickers"] += 1 if flags["has_sticker"] else 0
            if flags["role"] == "user":
                user["user_msg"] += 1
            elif flags["role"] == "assistant":
                user["assistant_msg"] += 1
            user["first_ts"] = ts if user["first_ts"] is None else min(user["first_ts"], ts)
            user["last_ts"] = ts if user["last_ts"] is None else max(user["last_ts"], ts)
            day_key = _local(ts, "%Y-%m-%d")
            user["days"].add(day_key)
            if ts >= week:
                user["days7"].add(day_key)
            hour = _local(ts, "%H")
            if hour.isdigit():
                hour_hist[int(hour)] += 1
            weekday = time.localtime(ts).tm_wday
            weekday_hist[weekday] += 1
            daily.setdefault(day_key, {"n": 0, "users": set()})
            daily[day_key]["n"] += 1
            daily[day_key]["users"].add(sess["user_id"])

    rows = []
    for user_row in users:
        uid = user_row["id"]
        info = per_user.get(uid) or {}
        sessions = int(info.get("sessions") or 0)
        messages = int(info.get("msg") or 0)
        last_ts = max([float(user_row.get("last_active") or 0), info.get("last_ts") or 0])
        sess_msgs = info.get("session_msgs") or []
        minutes = info.get("session_minutes") or []
        rows.append({
            "name": user_row["name"],
            "role": "管理员" if user_row.get("is_admin") else "普通用户",
            "disabled": bool(user_row.get("disabled")),
            "created_days_ago": _days_ago(now, user_row.get("created_at")),
            "last_active_days_ago": _days_ago(now, last_ts),
            "sessions": sessions,
            "messages": messages,
            "messages_user": int(info.get("user_msg") or 0),
            "messages_assistant": int(info.get("assistant_msg") or 0),
            "messages_7d": int(info.get("msg7") or 0),
            "tool_calls": int(info.get("tool_calls") or 0),
            "attachments": int(info.get("attachments") or 0),
            "stickers": int(info.get("stickers") or 0),
            "avg_chars_per_message": round((info.get("chars") or 0) / messages, 0)
                                      if messages else 0,
            "active_days_7d": len(info.get("days7") or ()),
            "active_days_30d": len(info.get("days") or ()),
            "streak_days": _streak_days(info.get("days") or set(), now),
            "avg_messages_per_session": round(messages / sessions, 1) if sessions else 0.0,
            "longest_session_messages": max(sess_msgs) if sess_msgs else 0,
            "avg_session_minutes": round(sum(minutes) / len(minutes), 1) if minutes else 0.0,
            "recent_topics": [
                str(sess.get("title") or "").strip()[:40]
                for sess in sorted(sessions_of.get(uid, []),
                                   key=lambda x: float(x.get("updated_at") or 0),
                                   reverse=True)
                if str(sess.get("title") or "").strip()][:3],
            "note": (user_row.get("note") or "")[:40],
        })

    def _count(predicate):
        return sum(1 for row in rows if predicate(row))

    def _active_within(days):
        return _count(lambda r: r["last_active_days_ago"] is not None
                      and r["last_active_days_ago"] <= days)

    # 留存：按「注册后是否在第 1 / 7 天窗口内用过」
    created = {user_row["id"]: float(user_row.get("created_at") or 0) for user_row in users}
    first_uses = {}
    for uid, info in per_user.items():
        stamps = [float(msg.get("ts") or 0)
                  for sess in sessions_of.get(uid, [])
                  for msg in msgs_of_session.get(sess["id"], [])]
        stamps = [ts for ts in stamps if ts > 0]
        if stamps:
            first_uses[uid] = (min(stamps), sorted(stamps))
    day1_base = day1_ok = day7_base = day7_ok = 0
    for uid, created_at in created.items():
        if not created_at or uid not in first_uses:
            continue
        first_ts, stamps = first_uses[uid]
        if now - created_at < 2 * 86400:
            continue
        day1_base += 1
        if any(first_ts < ts <= created_at + 86400 for ts in stamps):
            day1_ok += 1
        if now - created_at >= 8 * 86400:
            day7_base += 1
            if any(ts <= created_at + 7 * 86400 for ts in stamps):
                day7_ok += 1

    trend = [{"day": key, "messages": value["n"], "active_users": len(value["users"])}
             for key, value in sorted(daily.items())]
    totals = {
        "users": len(rows),
        "admins": _count(lambda r: r["role"] == "管理员"),
        "disabled": _count(lambda r: r["disabled"]),
        "new_7d": _count(lambda r: r["created_days_ago"] is not None
                         and r["created_days_ago"] <= 7),
        "active_24h": _active_within(1),
        "active_7d": _active_within(7),
        "active_30d": _active_within(30),
        "silent_7d": _count(lambda r: r["sessions"] > 0
                            and (r["last_active_days_ago"] is None
                                 or r["last_active_days_ago"] > 7)),
        "never_active": _count(lambda r: r["sessions"] == 0),
        "sessions": sum(r["sessions"] for r in rows),
        "messages": sum(r["messages"] for r in rows),
        "user_messages": sum(r["messages_user"] for r in rows),
        "tool_calls": sum(r["tool_calls"] for r in rows),
        "attachments": sum(r["attachments"] for r in rows),
        "avg_messages_per_active_user": round(
            sum(r["messages"] for r in rows) / max(1, _active_within(30)), 1),
        "avg_session_minutes": round(
            sum(r["avg_session_minutes"] for r in rows if r["sessions"]) /
            max(1, _count(lambda r: r["sessions"] > 0)), 1),
        "day1_retention": round(day1_ok * 100.0 / day1_base, 1) if day1_base else None,
        "day7_retention": round(day7_ok * 100.0 / day7_base, 1) if day7_base else None,
        "retention_base_users": {"day1": day1_base, "day7": day7_base},
        "peak_hours": sorted(
            [{"hour": h, "messages": n} for h, n in enumerate(hour_hist) if n],
            key=lambda item: -item["messages"])[:5],
        "weekday_messages": [
            {"weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][i],
             "messages": n} for i, n in enumerate(weekday_hist)],
    }
    titles = [sess["title"] for sess in sorted(
        session_rows, key=lambda s: float(s.get("updated_at") or 0), reverse=True)
        if (sess.get("title") or "").strip()][:30]
    return {
        "generated_at": now,
        "totals": totals,
        "daily_activity": trend,
        "recent_session_titles": titles,
        "users": sorted(rows, key=lambda r: (
            r["last_active_days_ago"] if r["last_active_days_ago"] is not None else 9e9,
            r["messages"])),
    }


ANALYSIS_SYSTEM = """你是一名资深产品运营分析师，在给一个自建 AI 助手（网页 + 安卓 App）做
「用户活跃度与行为分析」。你会收到一份统计 JSON（用户、会话、消息、留存、时段分布、
每人行为指标、最近会话标题）。**只能基于 JSON 里的数据推断，不能编造数字**；
数据不足时明确写「数据不足」，不要硬凑。

输出中文 Markdown 报告，严格按下面结构，每节都要有内容：

## 结论摘要
用 3 条 bullet 概括最关键的发现（每条一行，带关键数字）。

## 一、核心指标总览
一个 Markdown 表格，列出：总用户、7 日新增、DAU/WAU、活跃率、留存（次日/7 日）、
会话数、消息数、人均消息、人均会话时长。所有比率带 %。

## 二、活跃与留存分析
- 活跃分层：高活跃（7 日活跃 ≥5 天）/ 中度 / 沉默（>7 天未用）/ 从未使用，各多少人、占比；
- 留存解读：次日/7 日留存率说明什么，样本量多少；
- 点名列举 3~5 个沉默用户（如有），给出各自最后活跃时间。

## 三、使用行为画像
- 时段分布：高峰时段（引用 peak_hours）、工作日/周末差异（weekday_messages）；
- 会话与消息：人均会话长度、最长会话、人均会话时长、用户/助手消息比例；
- 工具与附件：工具调用次数、图片/文件上传次数（推断使用场景）；
- 主题分布：从 recent_session_titles 归纳 3~5 类主要用途，每类举 1~2 个例子。

## 四、分账号解读（必须逐个账号写）
先给一个 Markdown 表格，**覆盖 JSON 里的每一个用户**（一个都不能漏）：
| 账号 | 分层 | 最近活跃 | 会话 | 消息 | 主要用途（从 recent_topics 概括） | 风险/备注 |
表格里「主要用途」要具体（比如「做 PPT」「问嵌入式题」「闲聊」，不要写「使用产品」）；
「风险/备注」写沉默、异常高频、只用一次等信号。

表格之后，对**重点账号**（高活跃前 3 + 沉默用户 + 从未使用）每人再写 1~2 句解读，
必须点名并引用他的具体数据或会话主题。

## 五、用户分层与画像
按「重度用户 / 尝鲜用户 / 沉默用户 / 未激活用户」四类，各写 2~3 句特征（可点名），
并说明各层对产品的价值。

## 六、风险与异常
- 流失风险：哪些用户/时间段有流失信号；
- 异常使用：超高频、超长会话、反复报错尝试等（没有就写「暂未发现」）；
- 数据盲区：这份统计覆盖不到什么（比如没算 token 成本、没区分端）。

## 七、运营建议（按优先级）
5~8 条，每条格式：**优先级**｜动作｜面向人群｜预期指标。必须可落地（例如具体引导话术、
功能推荐、回访节点），不要写空话。

要求：总长度 1500~2500 字；多用表格和列表；引用具体数字和用户名；
不要输出代码块；不要泄露密码/token 等敏感信息。"""


def build_analysis_prompt(stats):
    """把统计 JSON 拼成模型请求体（system + user）。"""
    payload = {
        "统计时间": time.strftime("%Y-%m-%d %H:%M", time.localtime(stats["generated_at"])),
        "总量": stats["totals"],
        "每日活跃（消息数/活跃用户数）": stats["daily_activity"],
        "最近会话标题": stats["recent_session_titles"],
        "用户明细": stats["users"],
    }
    return [
        {"role": "system", "content": ANALYSIS_SYSTEM},
        {"role": "user",
         "content": "统计 JSON：\n```json\n"
                    + json.dumps(payload, ensure_ascii=False, indent=1)
                    + "\n```\n请按系统提示的结构输出详细分析报告。"},
    ]
