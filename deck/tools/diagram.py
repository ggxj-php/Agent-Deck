"""结构化画图：模型只给几十个 token 的 JSON，服务端生成 SVG。

为什么要有这个工具：以前画图全靠 render_html，模型得逐字生成几 KB 的 HTML
（实测 2,000 tokens ≈ 12 秒）。画数据结构图 / 流程图 / 架构图这种「有固定套路」
的图，让模型只描述结构（几十个 token），排版和配色由这里负责：
  - 生成快（1 秒级）
  - 风格统一，深色节点/红色节点这些语义色能对上
  - 数据合法就一定能画出来，不会因为 CSS 写错炸掉
创意海报、界面草图、动画这类仍然用 render_html。
"""
import json
import math

from . import render

# 画布参数
PAD = 24
NODE_H = 38
LEVEL_H = 92
GAP_X = 26
FONT = 13
MAX_NODES = 60
MAX_LABEL = 90

# 语义色：模型传 "red" / "black" / "green" / "blue" / "gray" / "gold"
PALETTE = {
    "red": ("#ffd9e3", "#d64570", "#7a1f3d"),
    "black": ("#3a3a46", "#23232c", "#ffffff"),
    "white": ("#ffffff", "#9a9aae", "#33333d"),
    "green": ("#d8f3e0", "#3f9a63", "#1d5a36"),
    "blue": ("#dbe9ff", "#3c73c4", "#1d4076"),
    "gold": ("#ffefc9", "#c39327", "#6b4d08"),
    "gray": ("#eceef2", "#a0a4b0", "#3d404a"),
}
INK = "#33333d"
LINE = "#8a8fa3"


def _esc(text):
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _label_len(text):
    """粗略估文字宽度：中文按 14px，英文数字按 8px。"""
    total = 0
    for ch in str(text):
        total += 14 if ord(ch) > 0x2E80 else 8
    return total


def _clip(text, limit=MAX_LABEL):
    text = str(text if text is not None else "").strip()
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _node_size(label):
    w = max(58, min(240, _label_len(label) + 28))
    return w, NODE_H


def _color_of(raw):
    key = str(raw or "").strip().lower()
    return PALETTE.get(key, PALETTE["white"])


def _box(x, y, w, h, label, color=None, shape="rect", bold=False):
    """画一个居中的节点，x/y 是中心点。"""
    fill, stroke, text_color = _color_of(color)
    rx = 10
    if shape == "pill":
        rx = h / 2
    elif shape == "circle":
        rx = w / 2
    parts = [f'<rect x="{x - w / 2:.1f}" y="{y - h / 2:.1f}" width="{w:.1f}" '
             f'height="{h:.1f}" rx="{rx:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>']
    weight = "600" if bold else "400"
    parts.append(f'<text x="{x:.1f}" y="{y + 4.5:.1f}" font-size="{FONT}" font-weight="{weight}" '
                 f'fill="{text_color}" text-anchor="middle" font-family="system-ui, -apple-system, '
                 f'Segoe UI, PingFang SC, Microsoft YaHei, sans-serif">{_esc(label)}</text>')
    return "".join(parts)


def _diamond(x, y, w, h, label, color=None):
    fill, stroke, text_color = _color_of(color or "gold")
    pts = f"{x:.1f},{y - h / 2:.1f} {x + w / 2:.1f},{y:.1f} {x:.1f},{y + h / 2:.1f} {x - w / 2:.1f},{y:.1f}"
    return (f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>'
            f'<text x="{x:.1f}" y="{y + 4.5:.1f}" font-size="{FONT}" fill="{text_color}" '
            f'text-anchor="middle" font-family="system-ui, sans-serif">{_esc(label)}</text>')


def _arrow(x1, y1, x2, y2, label="", dashed=False, color=LINE):
    """直线箭头，带可选标签（放在中点）。"""
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    parts = [f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
             f'stroke="{color}" stroke-width="1.5"{dash}/>']
    # 箭头三角
    ang = math.atan2(y2 - y1, x2 - x1)
    size = 8
    ax, ay = x2 - size * math.cos(ang), y2 - size * math.sin(ang)
    left = (ax + size * 0.55 * math.sin(ang), ay - size * 0.55 * math.cos(ang))
    right = (ax - size * 0.55 * math.sin(ang), ay + size * 0.55 * math.cos(ang))
    parts.append(f'<polygon points="{x2:.1f},{y2:.1f} {left[0]:.1f},{left[1]:.1f} '
                 f'{right[0]:.1f},{right[1]:.1f}" fill="{color}"/>')
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        parts.append(f'<text x="{mx:.1f}" y="{my - 6:.1f}" font-size="12" fill="#6b6f80" '
                     f'text-anchor="middle" font-family="system-ui, sans-serif">{_esc(label)}</text>')
    return "".join(parts)


def _canvas(w, h, body, title=""):
    head = ""
    if title:
        head = (f'<text x="{w / 2:.1f}" y="26" font-size="15" font-weight="600" fill="{INK}" '
                f'text-anchor="middle" font-family="system-ui, sans-serif">{_esc(title)}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" '
            f'width="100%" role="img">'
            f'<rect x="0" y="0" width="{w:.0f}" height="{h:.0f}" fill="none"/>{head}{body}</svg>')


def _norm_edges(raw):
    out = []
    for item in raw or []:
        if isinstance(item, dict):
            a, b = item.get("from"), item.get("to")
            label = item.get("label") or ""
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            a, b = item[0], item[1]
            label = item[2] if len(item) > 2 else ""
        else:
            continue
        if a is None or b is None:
            continue
        out.append((str(a), str(b), str(label or "")))
    return out


def _norm_nodes(raw):
    nodes = []
    for item in raw or []:
        if isinstance(item, dict):
            nid = item.get("id")
            label = item.get("label", nid)
            if nid is None:
                continue
            nodes.append({
                "id": str(nid),
                "label": _clip(label if label is not None else nid),
                "color": item.get("color"),
                "kind": str(item.get("kind") or "process"),
                "x": item.get("x"),
                "y": item.get("y"),
            })
        else:
            nodes.append({"id": str(item), "label": _clip(item), "color": None,
                          "kind": "process", "x": None, "y": None})
        if len(nodes) >= MAX_NODES:
            break
    return nodes


# ---------------- 1. 树 ----------------
def draw_tree(data, title=""):
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    if not nodes:
        raise ValueError("nodes 是空的，至少给一个节点")
    ids = {n["id"] for n in nodes}
    children = {n["id"]: [] for n in nodes}
    for a, b, _ in edges:
        if a in ids and b in ids and b not in children[a]:
            children[a].append(b)
    root = str(data.get("root") or nodes[0]["id"])
    if root not in ids:
        root = nodes[0]["id"]

    widths = {}

    def measure(nid):
        kids = children.get(nid, [])
        label_w, _ = _node_size(next(n["label"] for n in nodes if n["id"] == nid))
        if not kids:
            widths[nid] = label_w
            return label_w
        total = 0
        for i, kid in enumerate(kids):
            total += measure(kid)
            if i:
                total += GAP_X
        widths[nid] = max(label_w, total)
        return widths[nid]

    measure(root)
    pos = {}

    def place(nid, left, depth):
        w = widths[nid]
        pos[nid] = (left + w / 2, PAD + 40 + depth * LEVEL_H)
        kids = children.get(nid, [])
        if kids:
            total = sum(widths[k] for k in kids) + GAP_X * (len(kids) - 1)
            cursor = left + (w - total) / 2
            for kid in kids:
                place(kid, cursor, depth + 1)
                cursor += widths[kid] + GAP_X

    place(root, PAD, 0)
    # 防止有环：没摆到的节点挂到根下面
    for n in nodes:
        if n["id"] not in pos:
            pos[n["id"]] = (pos[root][0], pos[root][1])

    body = []
    for a, b, _ in edges:
        if a in pos and b in pos:
            x1, y1 = pos[a]
            x2, y2 = pos[b]
            body.append(_arrow(x1, y1 + NODE_H / 2, x2, y2 - NODE_H / 2))
    for n in nodes:
        x, y = pos[n["id"]]
        w, h = _node_size(n["label"])
        body.append(_box(x, y, w, h, n["label"], n.get("color")))
    height = PAD * 2 + 40 + max(y for _, y in pos.values()) - PAD + NODE_H
    width = max(widths[root] + PAD * 2, 240)
    return _canvas(width, height, "".join(body), title)


# ---------------- 2. 流程图 / 图（分层布局）----------------
def _layered(nodes, edges, root=None):
    ids = [n["id"] for n in nodes]
    index = {nid: i for i, nid in enumerate(ids)}
    preds = {nid: [] for nid in ids}
    succs = {nid: [] for nid in ids}
    for a, b, _ in edges:
        if a in index and b in index:
            succs[a].append(b)
            preds[b].append(a)
    start = str(root) if root and str(root) in index else None
    if start is None:
        starts = [nid for nid in ids if not preds[nid]] or [ids[0]]
        start = starts[0]

    level = {start: 0}
    queue = [start]
    while queue:
        cur = queue.pop(0)
        for nid in succs.get(cur, []):
            want = level[cur] + 1
            if nid not in level or level[nid] < want:
                level[nid] = want
                queue.append(nid)
    # 没走到的（环 / 孤岛）按前驱最大层级放
    for nid in ids:
        if nid not in level:
            level[nid] = 0
    for _ in range(len(ids) + 1):
        changed = False
        for nid in ids:
            want = max([level.get(p, 0) for p in preds[nid]] + [0])
            if nid not in level or level[nid] < want:
                level[nid] = want
                changed = True
        if not changed:
            break

    rows = {}
    for nid in ids:
        rows.setdefault(level[nid], []).append(nid)
    return rows, succs, preds


def draw_flow(data, title=""):
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    if not nodes:
        raise ValueError("nodes 是空的，至少给一个节点")
    rows, succs, _ = _layered(nodes, edges, data.get("start"))
    label_of = {n["id"]: n["label"] for n in nodes}
    sizes = {nid: _node_size(label_of[nid]) for nid in label_of}

    pos = {}
    max_row_w = 0
    for lvl, nids in rows.items():
        total = sum(sizes[nid][0] for nid in nids) + GAP_X * (len(nids) - 1)
        max_row_w = max(max_row_w, total)
    for lvl, nids in rows.items():
        total = sum(sizes[nid][0] for nid in nids) + GAP_X * (len(nids) - 1)
        cursor = PAD + (max_row_w - total) / 2
        y = PAD + 40 + lvl * LEVEL_H
        for nid in nids:
            w, _ = sizes[nid]
            pos[nid] = (cursor + w / 2, y)
            cursor += w + GAP_X

    body = []
    for a, b, label in edges:
        if a not in pos or b not in pos:
            continue
        x1, y1 = pos[a]
        x2, y2 = pos[b]
        if abs(y2 - y1) < 1:                     # 同一层：水平箭头
            body.append(_arrow(x1 + sizes[a][0] / 2, y1, x2 - sizes[b][0] / 2, y2, label))
        else:
            body.append(_arrow(x1, y1 + NODE_H / 2, x2, y2 - NODE_H / 2, label))
    for n in nodes:
        x, y = pos[n["id"]]
        w, h = _node_size(n["label"])
        kind = n.get("kind") or "process"
        if kind in ("start", "end", "起", "止"):
            body.append(_box(x, y, w, h, n["label"], n.get("color") or "green", shape="pill"))
        elif kind in ("decision", "判断", "条件"):
            body.append(_diamond(x, y, w + 30, h + 16, n["label"], n.get("color")))
        else:
            body.append(_box(x, y, w, h, n["label"], n.get("color")))
    height = PAD * 2 + 40 + max(y for _, y in pos.values()) + NODE_H
    width = max(max_row_w + PAD * 2, 260)
    return _canvas(width, height, "".join(body), title)


# ---------------- 3. 通用图（可给坐标，否则环形布局）----------------
def draw_graph(data, title=""):
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    if not nodes:
        raise ValueError("nodes 是空的")
    size = {n["id"]: _node_size(n["label"]) for n in nodes}
    has_pos = all(n.get("x") is not None and n.get("y") is not None for n in nodes)
    pos = {}
    if has_pos:
        for n in nodes:
            pos[n["id"]] = (float(n["x"]) + PAD, float(n["y"]) + PAD)
        width = max(x for x, _ in pos.values()) + PAD * 2
        height = max(y for _, y in pos.values()) + PAD * 2
    else:
        radius = max(110.0, 42.0 * math.sqrt(len(nodes)))
        cx = cy = radius + PAD + 20
        for i, n in enumerate(nodes):
            ang = -math.pi / 2 + 2 * math.pi * i / max(1, len(nodes))
            pos[n["id"]] = (cx + radius * math.cos(ang), cy + radius * math.sin(ang))
        width = height = radius * 2 + PAD * 2 + 60

    body = []
    for a, b, label in edges:
        if a not in pos or b not in pos:
            continue
        x1, y1 = pos[a]
        x2, y2 = pos[b]
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy) or 1
        r1 = max(size[a]) / 2
        r2 = max(size[b]) / 2 + 6
        body.append(_arrow(x1 + dx / dist * r1, y1 + dy / dist * r1,
                           x2 - dx / dist * r2, y2 - dy / dist * r2, label))
    for n in nodes:
        x, y = pos[n["id"]]
        w, h = size[n["id"]]
        body.append(_box(x, y, w, h, n["label"], n.get("color"),
                         shape="circle" if n.get("kind") == "circle" else "rect"))
    return _canvas(width, height, "".join(body), title)


# ---------------- 4. 数据流图（DFD）----------------
DFD_COLS = {"entity": 0, "process": 1, "store": 2}


def draw_dataflow(data, title=""):
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    if not nodes:
        raise ValueError("nodes 是空的")

    def kind_of(n):
        k = (n.get("kind") or "").strip().lower()
        if k in ("entity", "external", "外部实体", "实体"):
            return "entity"
        if k in ("store", "datastore", "数据存储", "存储"):
            return "store"
        return "process"

    cols = {0: [], 1: [], 2: []}
    for n in nodes:
        cols[DFD_COLS[kind_of(n)]].append(n)

    # 每列等距排布，列内垂直居中
    col_w = {"entity": 120, "process": 140, "store": 130}
    gap = 70
    x_of = {0: PAD + col_w["entity"] / 2,
            1: PAD + col_w["entity"] + gap + col_w["process"] / 2,
            2: PAD + col_w["entity"] + gap + col_w["process"] + gap + col_w["store"] / 2}
    count = max(len(v) for v in cols.values()) or 1
    height_needed = PAD * 2 + 60 + count * LEVEL_H
    pos = {}
    for ci, item_list in cols.items():
        total_h = len(item_list) * LEVEL_H
        start_y = PAD + 50 + (height_needed - PAD * 2 - 60 - total_h) / 2 + LEVEL_H / 2
        for i, n in enumerate(item_list):
            pos[n["id"]] = (x_of[ci], start_y + i * LEVEL_H)

    body = []
    for a, b, label in edges:
        if a not in pos or b not in pos:
            continue
        x1, y1 = pos[a]
        x2, y2 = pos[b]
        if abs(y1 - y2) < 1:
            body.append(_arrow(x1 + 60, y1, x2 - 65, y2, label))
        else:
            mid_x = (x1 + x2) / 2
            body.append(
                f'<path d="M {x1 + 60:.1f} {y1:.1f} L {mid_x:.1f} {y1:.1f} '
                f'L {mid_x:.1f} {y2:.1f} L {x2 - 68:.1f} {y2:.1f}" fill="none" '
                f'stroke="{LINE}" stroke-width="1.5"/>')
            body.append(_arrow(x2 - 76, y2, x2 - 68, y2, label))
    for n in nodes:
        x, y = pos[n["id"]]
        k = kind_of(n)
        label = n["label"]
        w, h = _node_size(label)
        if k == "entity":
            body.append(_box(x, y, col_w["entity"], h, label, n.get("color") or "blue"))
        elif k == "store":
            # 数据存储：左右两条竖线 + 中间标签（经典 DFD 画法）
            fill, stroke, text_color = _color_of(n.get("color") or "gray")
            left, right = x - col_w["store"] / 2, x + col_w["store"] / 2
            body.append(
                f'<rect x="{left:.1f}" y="{y - h / 2:.1f}" width="{col_w["store"]}" '
                f'height="{h}" fill="{fill}" stroke="none"/>'
                f'<line x1="{left:.1f}" y1="{y - h / 2:.1f}" x2="{left:.1f}" y2="{y + h / 2:.1f}" '
                f'stroke="{stroke}" stroke-width="2"/>'
                f'<line x1="{left + 8:.1f}" y1="{y - h / 2:.1f}" x2="{left + 8:.1f}" '
                f'y2="{y + h / 2:.1f}" stroke="{stroke}" stroke-width="1.4"/>'
                f'<line x1="{right - 8:.1f}" y1="{y - h / 2:.1f}" x2="{right - 8:.1f}" '
                f'y2="{y + h / 2:.1f}" stroke="{stroke}" stroke-width="1.4"/>'
                f'<line x1="{right:.1f}" y1="{y - h / 2:.1f}" x2="{right:.1f}" y2="{y + h / 2:.1f}" '
                f'stroke="{stroke}" stroke-width="2"/>'
                f'<text x="{x:.1f}" y="{y + 4.5:.1f}" font-size="{FONT}" fill="{text_color}" '
                f'text-anchor="middle" font-family="system-ui, sans-serif">{_esc(label)}</text>')
        else:
            body.append(_box(x, y, col_w["process"], h, label, n.get("color") or "green",
                             shape="circle"))
    width = x_of[2] + col_w["store"] / 2 + PAD
    return _canvas(width, height_needed, "".join(body), title)


# ---------------- 5. 架构图（分层）----------------
def draw_arch(data, title=""):
    layers = data.get("layers") or data.get("nodes") or []
    if not layers:
        raise ValueError("layers 是空的，至少给一层")
    cols = []
    for layer in layers:
        if isinstance(layer, dict):
            name = str(layer.get("name") or layer.get("label") or "")
            items = layer.get("items") or layer.get("nodes") or []
        else:
            name, items = "", layer if isinstance(layer, list) else [layer]
        items = [str(i.get("label") if isinstance(i, dict) else i) for i in items][:8]
        cols.append((name, items))

    box_w, box_h, gap = 128, 34, 12
    left = PAD + 96
    max_items = max(len(items) for _, items in cols) or 1
    width = left + max_items * (box_w + gap) + PAD
    y = PAD + 40
    body = []
    for name, items in cols:
        row_w = len(items) * (box_w + gap) - gap
        if name:
            body.append(f'<text x="{PAD:.1f}" y="{y + 17:.1f}" font-size="13" font-weight="600" '
                        f'fill="{INK}" font-family="system-ui, sans-serif">{_esc(name)}</text>')
        start_x = left + (width - left - PAD - row_w) / 2
        for i, item in enumerate(items):
            x = start_x + i * (box_w + gap) + box_w / 2
            body.append(_box(x, y + box_h / 2, box_w, box_h, _clip(item, 18), "blue"))
        y += box_h + gap + 18
    # 层与层之间画个向下箭头，表示调用/依赖方向
    for i in range(len(cols) - 1):
        mid_y = PAD + 40 + (i + 1) * (box_h + gap + 18) - gap / 2 - 9
        body.append(_arrow(left + 40, mid_y - 6, left + 40, mid_y + 6))
    height = y + PAD
    return _canvas(width, height, "".join(body), title)


# ---------------- 6. 时序图 ----------------
def draw_sequence(data, title=""):
    actors = [str(a.get("label") if isinstance(a, dict) else a)
              for a in (data.get("actors") or data.get("participants") or [])]
    messages = data.get("messages") or []
    if not actors:
        # 从消息里推参与者
        for m in messages:
            for key in ("from", "to"):
                if isinstance(m, dict) and m.get(key) is not None and str(m[key]) not in actors:
                    actors.append(str(m[key]))
    if not actors:
        raise ValueError("actors 是空的，至少给两个参与者")
    actors = actors[:8]
    col_gap = 150
    width = PAD * 2 + col_gap * (len(actors) - 1) + 100
    x_of = {a: PAD + 50 + i * col_gap for i, a in enumerate(actors)}
    head_y = PAD + 40
    top_y = head_y + 40
    step_y = 54
    height = top_y + step_y * max(1, len(messages)) + PAD

    body = []
    for a in actors:
        body.append(_box(x_of[a], head_y + 16, max(90, _label_len(a) + 30), 34, a, "blue"))
        body.append(f'<line x1="{x_of[a]:.1f}" y1="{head_y + 34:.1f}" x2="{x_of[a]:.1f}" '
                    f'y2="{height - PAD:.1f}" stroke="{LINE}" stroke-width="1.2" '
                    f'stroke-dasharray="5 4"/>')
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        src, dst = str(m.get("from")), str(m.get("to"))
        if src not in x_of or dst not in x_of:
            continue
        y = top_y + i * step_y
        text = _clip(m.get("text") or m.get("label") or "", 26)
        x1, x2 = x_of[src], x_of[dst]
        body.append(_arrow(x1, y, x2, y, text, dashed=bool(m.get("dashed")),
                           color="#5a6480"))
    return _canvas(width, height, "".join(body), title)


# ---------------- 7. 图表（柱 / 折线 / 饼）----------------
def draw_chart(data, title=""):
    kind = (data.get("kind") or data.get("chart") or "bar").strip().lower()
    labels = [str(x) for x in (data.get("labels") or [])]
    values = []
    for v in data.get("values") or []:
        try:
            values.append(float(v))
        except (TypeError, ValueError):
            values.append(0.0)
    if len(labels) < len(values):
        labels += [str(i + 1) for i in range(len(labels), len(values))]
    if not values:
        raise ValueError("values 是空的")
    pairs = list(zip(labels, values))[:14]

    if kind in ("pie", "donut", "饼", "饼图"):
        total = sum(v for _, v in pairs) or 1
        r, cx, cy = 110, PAD + 130, PAD + 150
        colors = ["#ffb3c9", "#9fc6ff", "#a8e6c1", "#ffdf9e", "#c9b6ff",
                  "#ffc29e", "#9fe6e6", "#e6b3d8"]
        body, ang = [], -math.pi / 2
        for i, (label, value) in enumerate(pairs):
            sweep = 2 * math.pi * (value / total)
            x1, y1 = cx + r * math.cos(ang), cy + r * math.sin(ang)
            x2, y2 = cx + r * math.cos(ang + sweep), cy + r * math.sin(ang + sweep)
            large = 1 if sweep > math.pi else 0
            body.append(f'<path d="M {cx} {cy} L {x1:.1f} {y1:.1f} A {r} {r} 0 {large} 1 '
                        f'{x2:.1f} {y2:.1f} Z" fill="{colors[i % len(colors)]}" '
                        f'stroke="#fff" stroke-width="2"/>')
            ang += sweep
        lx = cx + r + 30
        for i, (label, value) in enumerate(pairs):
            ly = PAD + 60 + i * 24
            body.append(f'<rect x="{lx}" y="{ly - 11}" width="12" height="12" rx="3" '
                        f'fill="{colors[i % len(colors)]}"/>')
            body.append(f'<text x="{lx + 20}" y="{ly}" font-size="13" fill="{INK}" '
                        f'font-family="system-ui, sans-serif">{_esc(_clip(label, 16))}  '
                        f'{value:g}</text>')
        width = lx + 220
        height = max(cy + r + PAD, PAD + 60 + len(pairs) * 24 + PAD)
        return _canvas(width, height, "".join(body), title)

    chart_w, chart_h = 420, 240
    left, top = PAD + 50, PAD + 50
    max_v = max(abs(v) for _, v in pairs) or 1
    body = []
    # 坐标轴
    body.append(f'<line x1="{left}" y1="{top + chart_h}" x2="{left + chart_w}" '
                f'y2="{top + chart_h}" stroke="{LINE}" stroke-width="1.4"/>')
    body.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}" '
                f'stroke="{LINE}" stroke-width="1.4"/>')
    if kind in ("line", "折线", "折线图"):
        step = chart_w / max(1, len(pairs) - 1) if len(pairs) > 1 else chart_w
        pts = []
        for i, (_, value) in enumerate(pairs):
            x = left + (i * step if len(pairs) > 1 else chart_w / 2)
            y = top + chart_h - (value / max_v) * (chart_h - 20)
            pts.append((x, y))
        body.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) +
                    '" fill="none" stroke="#d64570" stroke-width="2.4"/>')
        for (x, y), (label, value) in zip(pts, pairs):
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#d64570"/>')
            body.append(f'<text x="{x:.1f}" y="{top + chart_h + 20:.1f}" font-size="12" '
                        f'fill="{INK}" text-anchor="middle" font-family="system-ui, sans-serif">'
                        f'{_esc(_clip(label, 10))}</text>')
            body.append(f'<text x="{x:.1f}" y="{y - 10:.1f}" font-size="11" fill="#6b6f80" '
                        f'text-anchor="middle" font-family="system-ui, sans-serif">{value:g}</text>')
    else:
        slot = chart_w / max(1, len(pairs))
        bar_w = min(56, slot * 0.6)
        for i, (label, value) in enumerate(pairs):
            x = left + slot * i + slot / 2
            h = (abs(value) / max_v) * (chart_h - 20)
            y = top + chart_h - h
            body.append(f'<rect x="{x - bar_w / 2:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                        f'height="{h:.1f}" rx="6" fill="#9fc6ff" stroke="#3c73c4"/>')
            body.append(f'<text x="{x:.1f}" y="{y - 8:.1f}" font-size="11" fill="#6b6f80" '
                        f'text-anchor="middle" font-family="system-ui, sans-serif">{value:g}</text>')
            body.append(f'<text x="{x:.1f}" y="{top + chart_h + 20:.1f}" font-size="12" '
                        f'fill="{INK}" text-anchor="middle" font-family="system-ui, sans-serif">'
                        f'{_esc(_clip(label, 10))}</text>')
    return _canvas(left + chart_w + PAD, top + chart_h + 40 + PAD, "".join(body), title)


# ---------------- 8. 时间线 ----------------
def draw_timeline(data, title=""):
    items = data.get("items") or data.get("events") or []
    if not items:
        raise ValueError("items 是空的")
    rows = []
    for it in items[:16]:
        if isinstance(it, dict):
            rows.append((str(it.get("time") or it.get("date") or ""),
                         _clip(it.get("text") or it.get("label") or "", 42)))
        else:
            rows.append(("", str(it)))
    step = 58
    width = 520
    height = PAD * 2 + 40 + step * len(rows)
    line_x = PAD + 130
    body = []
    body.append(f'<line x1="{line_x}" y1="{PAD + 40}" x2="{line_x}" y2="{height - PAD}" '
                f'stroke="{LINE}" stroke-width="2" stroke-dasharray="6 5"/>')
    for i, (when, text) in enumerate(rows):
        y = PAD + 50 + i * step
        body.append(f'<circle cx="{line_x}" cy="{y}" r="6" fill="#d64570" '
                    f'stroke="#fff" stroke-width="2"/>')
        if when:
            body.append(f'<text x="{line_x - 20}" y="{y + 5}" font-size="13" font-weight="600" '
                        f'fill="{INK}" text-anchor="end" font-family="system-ui, sans-serif">'
                        f'{_esc(_clip(when, 12))}</text>')
        body.append(f'<text x="{line_x + 22}" y="{y + 5}" font-size="13" fill="{INK}" '
                    f'font-family="system-ui, sans-serif">{_esc(text)}</text>')
    return _canvas(width, height, "".join(body), title)


# ---------------- 9. ER 图（实体-关系）----------------
def draw_er(data, title=""):
    """实体-关系图：实体（属性表）+ 关系连线（带 1:1 / 1:N / N:M 基数）。

    data = {
      "entities": [{"name": "用户", "attributes": [
          {"name": "id", "pk": true}, {"name": "昵称"}, {"name": "部门id", "fk": true}]}],
      "relations": [{"from": "用户", "to": "订单", "label": "下单",
                     "cardinality": "1:N"}]
    }
    """
    entities = data.get("entities") or data.get("tables") or []
    relations = data.get("relations") or data.get("links") or []
    if not entities:
        raise ValueError("entities 是空的，至少给一个实体")
    entities = entities[:14]

    # 每个实体：标题 + 属性行
    items = []
    for ent in entities:
        if isinstance(ent, dict):
            name = _clip(ent.get("name") or ent.get("label") or "", 24)
            attrs = ent.get("attributes") or ent.get("fields") or []
        else:
            name, attrs = _clip(ent, 24), []
        rows = []
        for attr in attrs[:12]:
            if isinstance(attr, dict):
                aname = _clip(attr.get("name") or attr.get("label") or "", 26)
                pk = bool(attr.get("pk") or attr.get("primary"))
                fk = bool(attr.get("fk") or attr.get("foreign"))
            else:
                aname, pk, fk = _clip(attr, 26), False, False
            rows.append((aname, pk, fk))
        width = max(150, _label_len(name) + 40,
                    max([_label_len(r[0]) + 60 for r in rows] or [0]))
        height = 34 + max(1, len(rows)) * 22 + 12
        items.append({"name": name, "rows": rows, "w": width, "h": height})

    # 网格布局：2~4 个实体排两列，更多排三列
    count = len(items)
    cols = 2 if count <= 4 else (3 if count <= 9 else 4)
    col_gap, row_gap = 110, 70
    col_w = [0.0] * cols
    for i, it in enumerate(items):
        col_w[i % cols] = max(col_w[i % cols], it["w"])
    # 每列 x 起点
    starts = []
    cursor = PAD
    for w in col_w:
        starts.append(cursor)
        cursor += w + col_gap
    total_w = cursor - col_gap + PAD

    pos = {}
    row_y = {}
    for i, it in enumerate(items):
        c, r = i % cols, i // cols
        y = PAD + 40 + row_y.get(r, 0) + it["h"] / 2
        row_y[r] = row_y.get(r, 0) + it["h"] + row_gap
        x = starts[c] + it["w"] / 2
        pos[it["name"]] = (x, y)
        it["x"], it["y"] = x, y

    total_h = PAD * 2 + 40 + max(row_y.values() or [0])

    body = []
    # 先画关系线，再画实体框（框压线，更像正规 ER 图）
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        a, b = str(rel.get("from")), str(rel.get("to"))
        if a not in pos or b not in pos:
            continue
        x1, y1 = pos[a]
        x2, y2 = pos[b]
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy) or 1
        body.append(_arrow(x1 + dx / dist * 20, y1 + dy / dist * 20,
                           x2 - dx / dist * 20, y2 - dy / dist * 20,
                           str(rel.get("label") or ""), dashed=True, color="#5a6480"))
        card = str(rel.get("cardinality") or "").strip()
        if card:
            for end, sign in ((0, card.split(":")[0] if ":" in card else card),
                              (1, card.split(":")[1] if ":" in card else "")):
                if not sign:
                    continue
                px, py = (x1 + dx / dist * 30, y1 + dy / dist * 30) if end == 0 else \
                         (x2 - dx / dist * 30, y2 - dy / dist * 30)
                body.append(f'<text x="{px:.1f}" y="{py - 6:.1f}" font-size="12" fill="#d64570" '
                            f'text-anchor="middle" font-weight="600" '
                            f'font-family="system-ui, sans-serif">{_esc(sign.strip())}</text>')

    for it in items:
        x, y, w, h = it["x"], it["y"], it["w"], it["h"]
        left, top = x - w / 2, y - h / 2
        body.append(f'<rect x="{left:.1f}" y="{top:.1f}" width="{w:.1f}" height="{h:.1f}" '
                    f'rx="8" fill="#ffffff" stroke="#8a8fa3" stroke-width="1.6"/>')
        body.append(f'<rect x="{left:.1f}" y="{top:.1f}" width="{w:.1f}" height="30" rx="8" '
                    f'fill="#dbe9ff" stroke="#8a8fa3" stroke-width="1.6"/>')
        body.append(f'<rect x="{left:.1f}" y="{top + 22:.1f}" width="{w:.1f}" height="8" '
                    f'fill="#dbe9ff"/>')
        body.append(f'<text x="{x:.1f}" y="{top + 20:.1f}" font-size="13.5" font-weight="600" '
                    f'fill="#1d4076" text-anchor="middle" font-family="system-ui, sans-serif">'
                    f'{_esc(it["name"])}</text>')
        for j, (aname, pk, fk) in enumerate(it["rows"]):
            ry = top + 30 + 16 + j * 22
            prefix = "🔑 " if pk else ("FK " if fk else "")
            weight = "600" if pk else "400"
            color = "#6b4d08" if pk else ("#3c73c4" if fk else INK)
            body.append(f'<text x="{left + 12:.1f}" y="{ry:.1f}" font-size="12.5" '
                        f'font-weight="{weight}" fill="{color}" '
                        f'font-family="system-ui, sans-serif">{_esc(prefix + aname)}</text>')
            if pk and aname:
                # 主键下划线
                body.append(f'<line x1="{left + 12:.1f}" y1="{ry + 3:.1f}" '
                            f'x2="{left + 12 + _label_len(aname) + 18:.1f}" y2="{ry + 3:.1f}" '
                            f'stroke="{color}" stroke-width="1"/>')
        if not it["rows"]:
            body.append(f'<text x="{left + 12:.1f}" y="{top + 52:.1f}" font-size="12.5" '
                        f'fill="#9a9aae" font-family="system-ui, sans-serif">（还没有属性）</text>')

    if title:
        body.append(f'<text x="{total_w / 2:.1f}" y="26" font-size="15" font-weight="600" '
                    f'fill="{INK}" text-anchor="middle" '
                    f'font-family="system-ui, sans-serif">{_esc(title)}</text>')
    return _canvas(total_w, total_h, "".join(body), "")


DRAWERS = {
    "tree": draw_tree,
    "flow": draw_flow,
    "flowchart": draw_flow,
    "graph": draw_graph,
    "dataflow": draw_dataflow,
    "dfd": draw_dataflow,
    "arch": draw_arch,
    "architecture": draw_arch,
    "sequence": draw_sequence,
    "chart": draw_chart,
    "timeline": draw_timeline,
    "er": draw_er,
    "erd": draw_er,
}

KIND_ALIAS = {
    "树": "tree", "树形": "tree", "tree": "tree",
    "流程图": "flow", "流程": "flow", "flowchart": "flow", "flow": "flow",
    "图": "graph", "关系图": "graph", "graph": "graph",
    "数据流图": "dataflow", "数据流": "dataflow", "dfd": "dataflow", "dataflow": "dataflow",
    "架构图": "arch", "架构": "arch", "arch": "arch", "architecture": "arch",
    "时序图": "sequence", "时序": "sequence", "sequence": "sequence",
    "图表": "chart", "chart": "chart", "柱状图": "chart", "折线图": "chart", "饼图": "chart",
    "时间线": "timeline", "timeline": "timeline",
    "er图": "er", "er": "er", "erd": "er", "实体关系图": "er", "ER图": "er",
}


def draw_diagram(kind=None, data=None, title="", **kwargs) -> str:
    """结构化画图入口：返回和 render_html 一样的 JSON 回执（带 artifact_id）。

    兼容模型几种常见给法：
      - 参数名可能是 type / diagram / kind；
      - 数据可能包在 data 里，也可能把 nodes / edges / layers / entities 直接放顶层；
      - data 可能是 JSON 字符串。
    （以前只认 kind 这个参数名，模型按 schema 传 type 就 TypeError，然后它退回
      render_html 白等十几秒——所以这里必须宽容。）
    """
    raw_kind = str(kind or kwargs.get("type") or kwargs.get("diagram")
                   or kwargs.get("diagram_type") or "").strip().lower()
    key = KIND_ALIAS.get(raw_kind) or raw_kind
    drawer = DRAWERS.get(key)
    if drawer is None:
        return json.dumps({
            "error": f"不认识的图类型「{kind}」。可选：tree（树）/ flow（流程图）/ graph（关系图）/ "
                     f"dataflow（数据流图）/ arch（架构图）/ sequence（时序图）/ chart（图表）/ "
                     f"timeline（时间线）/ er（ER 图）。要自由创意排版就用 render_html。"
        }, ensure_ascii=False)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = None
    if not isinstance(data, dict) or not data:
        # 顶层直接给 nodes / edges / layers / entities 也认
        data = {k: kwargs[k] for k in
                ("nodes", "edges", "layers", "entities", "relations", "items",
                 "actors", "messages", "labels", "values", "kind", "root", "start")
                if k in kwargs and kwargs[k] is not None}
    if not isinstance(data, dict) or not data:
        return json.dumps({"error": "data 是空的，至少要给节点/层/条目"}, ensure_ascii=False)
    try:
        svg = drawer(data, str(title or "").strip())
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    except Exception as exc:                     # noqa: BLE001
        return json.dumps({"error": f"画图失败：{type(exc).__name__}: {exc}"}, ensure_ascii=False)

    doc = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<style>'
        'html,body{margin:0;padding:0;background:transparent;}'
        'body{font-family:system-ui,-apple-system,"Segoe UI","PingFang SC",'
        '"Microsoft YaHei",sans-serif;overflow-x:auto;}'
        'svg{display:block;margin:0 auto;max-width:100%;height:auto;}'
        '</style></head><body>' + svg + '</body></html>'
    )
    script = getattr(render, "_RESIZE_SCRIPT", "")
    if script:
        doc = doc.replace("</body>", f"<script>{script}</script></body>", 1)
    aid = render._store(doc, str(title or ""))
    return json.dumps({
        "ok": True,
        "artifact_id": aid,
        "title": str(title or ""),
        "note": "已经画成卡片给用户看了，正文里一两句话说明画的是什么就行，别再贴任何 SVG/HTML。",
    }, ensure_ascii=False)


SPECS = [
    {
        "type": "function",
        "function": {
            "name": "draw_diagram",
            "description": (
                "结构化画图：给节点/连线/层级这些数据，服务端生成规范的 SVG 图。"
                "画**数据结构图（二叉树/红黑树/堆）、流程图、关系图、数据流图(DFD)、"
                "架构图、时序图、柱状/折线/饼图、时间线**优先用它——只要几十个 token，"
                "比 render_html 快一个数量级，而且配色统一。"
                "别自己手写 SVG！创意海报、界面草图、动画、自由排版才用 render_html。\n"
                "type 取值与 data 结构：\n"
                "  tree: {nodes:[{id,label,color?}], edges:[{from,to}], root?}  "
                "color: red/black/green/blue/gold/gray/white（红黑树就用 red / black）\n"
                "  flow: {nodes:[{id,label,kind?:start|process|decision|end}], "
                "edges:[{from,to,label?}], start?}\n"
                "  graph: {nodes:[{id,label,color?,x?,y?,kind?:circle}], edges:[{from,to,label?}]}"
                "（不给坐标就自动环形布局）\n"
                "  dataflow: {nodes:[{id,label,kind:entity|process|store}], "
                "edges:[{from,to,label?}]}\n"
                "  arch: {layers:[{name, items:[\"模块\", ...]}]}（从上到下的分层架构）\n"
                "  sequence: {actors:[\"客户端\",\"服务端\"], messages:[{from,to,text,dashed?}]}\n"
                "  chart: {kind:bar|line|pie, labels:[...], values:[...]}\n"
                "  timeline: {items:[{time,text}]}\n"
                "  er: {entities:[{name, attributes:[{name, pk?, fk?}]}], "
                "relations:[{from,to,label?,cardinality?}]}（ER 图，cardinality 写 1:1 / 1:N / N:M）\n"
                "一个图最多 60 个节点、40 字以内的标签，节点太多就拆成两张图。"
                "title 写一句图名（比如「红黑树插入后结构」）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "图类型：tree / flow / graph / dataflow / arch / "
                                       "sequence / chart / timeline / er",
                    },
                    "title": {"type": "string", "description": "图名，可选"},
                    "data": {
                        "type": "object",
                        "description": "图的数据（节点 / 边 / 层 / 消息等，见 description）",
                    },
                },
                "required": ["type", "data"],
            },
        },
    },
]
