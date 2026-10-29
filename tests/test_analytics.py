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
        "daily_messages": [{"day": "2026-10-28", "n": 20}],
        "recent_session_titles": ["帮我做 PPT", "讲一下傅里叶变换"],
        "users": [{"name": "alice", "messages_7d": 10, "sessions": 2}],
    }
    messages = analytics.build_analysis_prompt(stats)
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "产品运营分析师" in messages[0]["content"]
    assert "alice" in messages[1]["content"]
    assert "帮我做 PPT" in messages[1]["content"]
    assert "## 二、活跃分层" in messages[0]["content"]
