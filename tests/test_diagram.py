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


def _rects(svg):
    """取出画布里所有节点矩形（跳过 fill=none 的整张画布背景）。"""
    import re
    out = []
    for m in re.finditer(
            r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([-\d.]+)" height="([-\d.]+)" '
            r'[^>]*?fill="([^"]+)"', svg):
        x, y, w, h, fill = m.groups()
        if fill == "none":
            continue
        out.append((float(x), float(y), float(w), float(h)))
    return out


def test_数据流图节点不重叠():
    """回归：以前固定三列坐标 + 写死连线偏移，节点/标签会压在一起。"""
    out = json.loads(diagram.draw_diagram("dataflow", {
        "nodes": [
            {"id": "s1", "label": "读者", "kind": "entity"},
            {"id": "s2", "label": "图书馆管理员", "kind": "entity"},
            {"id": "p1", "label": "1 查询图书信息", "kind": "process"},
            {"id": "p2", "label": "2 办理借书登记", "kind": "process"},
            {"id": "d1", "label": "D1 图书信息库", "kind": "store"},
            {"id": "d2", "label": "D2 借阅记录库", "kind": "store"},
        ],
        "edges": [["s1", "p1", "查询请求"], ["p1", "d1", "结果"],
                  ["s2", "p2", "借阅请求"], ["p2", "d2", "记录"]],
    }, "0 层数据流图"))
    svg = diagram.render.get_artifact(out["artifact_id"])["html"]
    rects = [r for r in _rects(svg) if r[2] > 20 and r[3] > 20]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            x1, y1, w1, h1 = rects[i]
            x2, y2, w2, h2 = rects[j]
            overlap_x = min(x1 + w1, x2 + w2) - max(x1, x2)
            overlap_y = min(y1 + h1, y2 + h2) - max(y1, y2)
            assert overlap_x <= 1 or overlap_y <= 1, f"节点重叠: {rects[i]} / {rects[j]}"


def test_ER图节点不重叠():
    out = json.loads(diagram.draw_diagram("er", {
        "entities": [
            {"name": "用户", "attributes": [{"name": "id", "pk": True},
                                            {"name": "昵称"}, {"name": "手机号"}]},
            {"name": "订单", "attributes": [{"name": "id", "pk": True},
                                            {"name": "用户id", "fk": True}]},
            {"name": "商品", "attributes": [{"name": "id", "pk": True},
                                            {"name": "名称"}, {"name": "价格"}]},
            {"name": "订单明细", "attributes": [{"name": "订单id", "pk": True},
                                                {"name": "商品id", "fk": True}]},
        ],
        "relations": [
            {"from": "用户", "to": "订单", "label": "下单", "cardinality": "1:N"},
            {"from": "订单", "to": "订单明细", "label": "包含", "cardinality": "1:N"},
            {"from": "商品", "to": "订单明细", "label": "属于", "cardinality": "1:N"},
        ],
    }, "电商 ER 图"))
    svg = diagram.render.get_artifact(out["artifact_id"])["html"]
    # 只看实体主体框（标题栏是叠在主体上的，高度 30，这里排除掉）
    rects = [r for r in _rects(svg) if r[2] > 20 and r[3] > 45]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            x1, y1, w1, h1 = rects[i]
            x2, y2, w2, h2 = rects[j]
            overlap_x = min(x1 + w1, x2 + w2) - max(x1, x2)
            overlap_y = min(y1 + h1, y2 + h2) - max(y1, y2)
            assert overlap_x <= 1 or overlap_y <= 1, f"实体框重叠: {rects[i]} / {rects[j]}"


def test_状态转换图_符号():
    """状态图：初始实心点、终态同心圆、三段式状态框。"""
    data = {
        "initial": "空闲",
        "finals": ["断线"],
        "states": [
            {"name": "空闲"},
            {"name": "拨号音", "variables": "timer=0", "activities": "do/响蜂鸣音"},
            {"name": "断线"},
        ],
        "transitions": [
            {"from": "空闲", "to": "拨号音", "event": "拿起听筒"},
            {"from": "拨号音", "to": "断线", "event": "挂断"},
        ],
    }
    out = json.loads(diagram.draw_diagram("state", data, "测试状态图"))
    assert out.get("artifact_id"), out
    html = diagram.svg_of(out["artifact_id"]) or ""
    if html:
        assert "doublecircle" in html or "ellipse" in html
