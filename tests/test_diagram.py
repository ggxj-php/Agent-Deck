"""draw_diagram：每种图都能画出来，且不吞内容 / 不炸。"""
import json

from deck.tools import diagram


def test_树_红黑树():
    out = json.loads(diagram.draw_diagram("tree", {
        "nodes": [
            {"id": "r", "label": "30", "color": "black"},
            {"id": "l", "label": "15", "color": "red"},
            {"id": "rr", "label": "45", "color": "black"},
        ],
        "edges": [["r", "l"], ["r", "rr"]],
    }, "红黑树"))
    assert out["ok"] and len(out["artifact_id"]) == 16


def test_流程图带判断():
    out = json.loads(diagram.draw_diagram("flow", {
        "nodes": [
            {"id": "s", "label": "开始", "kind": "start"},
            {"id": "d", "label": "x > 0 ?", "kind": "decision"},
            {"id": "y", "label": "输出正数"},
        ],
        "edges": [{"from": "s", "to": "d"}, {"from": "d", "to": "y", "label": "是"}],
    }, "判断流程"))
    assert out["ok"]


def test_数据流图():
    out = json.loads(diagram.draw_diagram("dataflow", {
        "nodes": [
            {"id": "u", "label": "用户", "kind": "entity"},
            {"id": "p", "label": "登录校验", "kind": "process"},
            {"id": "db", "label": "用户表", "kind": "store"},
        ],
        "edges": [["u", "p", "账号密码"], ["p", "db", "查询"]],
    }, "登录数据流"))
    assert out["ok"]


def test_架构图():
    out = json.loads(diagram.draw_diagram("arch", {
        "layers": [
            {"name": "接入层", "items": ["Nginx", "CDN"]},
            {"name": "服务层", "items": ["FastAPI", "Worker"]},
            {"name": "存储层", "items": ["SQLite", "Redis"]},
        ],
    }, "系统架构"))
    assert out["ok"]


def test_时序图():
    out = json.loads(diagram.draw_diagram("sequence", {
        "actors": ["客户端", "服务端"],
        "messages": [{"from": "客户端", "to": "服务端", "text": "POST /api/chat"},
                     {"from": "服务端", "to": "客户端", "text": "SSE 流式返回", "dashed": True}],
    }, "聊天时序"))
    assert out["ok"]


def test_图表三种():
    for kind in ("bar", "line", "pie"):
        out = json.loads(diagram.draw_diagram("chart", {
            "kind": kind,
            "labels": ["O(n)", "O(log n)", "O(1)"],
            "values": [100, 7, 1],
        }, f"{kind} 对比"))
        assert out["ok"], kind


def test_时间线():
    out = json.loads(diagram.draw_diagram("timeline", {
        "items": [{"time": "2024", "text": "立项"}, {"time": "2025", "text": "上线"}],
    }, "项目时间线"))
    assert out["ok"]


def test_不认识类型给提示():
    out = json.loads(diagram.draw_diagram("spaceship", {"nodes": []}))
    assert "error" in out and "render_html" in out["error"]


def test_空数据报错():
    out = json.loads(diagram.draw_diagram("tree", {"nodes": []}))
    assert "error" in out


def test_标签转义不注入():
    out = json.loads(diagram.draw_diagram("flow", {
        "nodes": [{"id": "a", "label": "<script>alert(1)</script>"}],
    }))
    assert out["ok"]
    html = diagram.render.get_artifact(out["artifact_id"])["html"]
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_ER图():
    out = json.loads(diagram.draw_diagram("er", {
        "entities": [
            {"name": "用户", "attributes": [{"name": "id", "pk": True}, {"name": "昵称"}]},
            {"name": "订单", "attributes": [{"name": "id", "pk": True},
                                            {"name": "用户id", "fk": True},
                                            {"name": "金额"}]},
        ],
        "relations": [{"from": "用户", "to": "订单", "label": "下单",
                       "cardinality": "1:N"}],
    }, "电商 ER 图"))
    assert out["ok"]
    html = diagram.render.get_artifact(out["artifact_id"])["html"]
    assert "用户" in html and "订单" in html and "下单" in html
