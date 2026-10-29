"""后台用户分析：分页边界 + 分析提示词构造。"""
from deck import analytics


def test_分页边界():
    assert analytics.page_bounds(0) == (1, 10, 1, 0)
    assert analytics.page_bounds(17, 1, 10) == (1, 10, 2, 0)
    assert analytics.page_bounds(17, 2, 10) == (2, 10, 2, 10)
    # 越界纠正
    assert analytics.page_bounds(17, 99, 10)[0] == 2
    assert analytics.page_bounds(17, -3, 10)[0] == 1
    # size 夹在 5~50
    assert analytics.page_bounds(100, 1, 1)[1] == 5
    assert analytics.page_bounds(100, 1, 999)[1] == 50


def test_分析提示词结构():
    stats = {
        "generated_at": 1793270000.0,
        "totals": {"users": 3, "active_7d": 2, "messages": 50},
        "daily_activity": [{"day": "2026-10-28", "messages": 20, "active_users": 2}],
        "recent_session_titles": ["帮我做 PPT", "讲一下傅里叶变换"],
        "users": [{"name": "alice", "messages_7d": 10, "sessions": 2,
               "recent_topics": ["帮我做 PPT"]}],
    }
    messages = analytics.build_analysis_prompt(stats)
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "产品运营分析师" in messages[0]["content"]
    assert "alice" in messages[1]["content"]
    assert "帮我做 PPT" in messages[1]["content"]
    assert "## 二、活跃与留存分析" in messages[0]["content"]
    assert "## 七、运营建议（按优先级）" in messages[0]["content"]
    assert "1500~2500 字" in messages[0]["content"]


def test_连续活跃天数():
    import time as _time
    now = _time.time()
    day = _time.strftime("%Y-%m-%d", _time.localtime(now))
    yday = _time.strftime("%Y-%m-%d", _time.localtime(now - 86400))
    _yday2 = _time.strftime("%Y-%m-%d", _time.localtime(now - 2 * 86400))
    assert analytics._streak_days({day, yday}, now) == 2
    assert analytics._streak_days({yday}, now) == 1
    assert analytics._streak_days(set(), now) == 0


def test_消息特征识别():
    user_msg = analytics._message_flags(
        '{"role":"user","content":"[图片 #abc uploads/a.jpg] 看看这个"}')
    assert user_msg["role"] == "user" and user_msg["has_attachment"] is True
    tool_msg = analytics._message_flags(
        '{"role":"assistant","content":"好","tool_calls":[{"id":"1"}]}')
    assert tool_msg["tool_calls"] == 1
    sticker = analytics._message_flags('{"role":"assistant","content":"[sticker:happy]"}')
    assert sticker["has_sticker"] is True
