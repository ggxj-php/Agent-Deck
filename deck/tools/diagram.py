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
import re
import shutil
import subprocess
import threading

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


# ---------------- 公共：分层排版（按节点实际尺寸）----------------
LEVEL_GAP = 108          # 层与层之间的垂直距离
ROW_GAP = 34             # 同一层里节点之间的水平距离


def _layered_positions(nodes, edges, start=None):
    """按边做分层，返回 (pos, sizes, rows)。

    pos[id] = (cx, cy)；行内按实际节点宽度居中排布，避免「固定坐标」导致重叠。
    """
    ids = [n["id"] for n in nodes]
    index = {nid: i for i, nid in enumerate(ids)}
    preds = {nid: [] for nid in ids}
    succs = {nid: [] for nid in ids}
    for a, b, _ in edges:
        if a in index and b in index:
            succs[a].append(b)
            preds[b].append(a)

    root = str(start) if start and str(start) in index else None
    if root is None:
        roots = [nid for nid in ids if not preds[nid]] or [ids[0]]
        root = roots[0]

    level = {root: 0}
    queue = [root]
    while queue:
        cur = queue.pop(0)
        for nxt in succs.get(cur, []):
            if nxt not in level:
                level[nxt] = level[cur] + 1
                queue.append(nxt)
    # 环 / 孤岛：按前驱层级往上顶
    for _ in range(len(ids) + 1):
        changed = False
        for nid in ids:
            if nid not in level:
                level[nid] = 0
                changed = True
            want = max([level.get(p, 0) + 1 for p in preds[nid]] + [0])
            if level[nid] < want:
                level[nid] = want
                changed = True
        if not changed:
            break

    rows = {}
    for nid in ids:
        rows.setdefault(level[nid], []).append(nid)

    sizes = {n["id"]: _node_size(n["label"]) for n in nodes}
    max_row = 0.0
    for nids in rows.values():
        total = sum(sizes[nid][0] for nid in nids) + ROW_GAP * (len(nids) - 1)
        max_row = max(max_row, total)

    pos = {}
    for lvl, nids in rows.items():
        total = sum(sizes[nid][0] for nid in nids) + ROW_GAP * (len(nids) - 1)
        cursor = PAD + (max_row - total) / 2
        cy = PAD + 34 + lvl * LEVEL_GAP
        for nid in nids:
            w = sizes[nid][0]
            pos[nid] = (cursor + w / 2, cy)
            cursor += w + ROW_GAP
    width = max_row + PAD * 2
    height = PAD * 2 + 34 + max(0, max(level.values())) * LEVEL_GAP + NODE_H
    return pos, sizes, width, height


def _label_slots(edges_geom):
    """同一条水平线上挤了好几条连线时，标签会叠成一团——按组错开。"""
    groups = {}
    slots = []
    offsets = [0, 18, -18, 34, -34, 50, -50]
    for geom in edges_geom:
        key = round((geom[1] + geom[3]) / 2 / 12)
        idx = groups.get(key, 0)
        groups[key] = idx + 1
        slots.append(offsets[idx % len(offsets)])
    return slots


def _orth_arrow(x1, y1, x2, y2, label="", color=LINE, label_dy=0, label_dx=0):
    """正交折线箭头：从下沿出发 → 中间水平段 → 目标上沿。

    以前是「中心点直连 + 写死偏移」，节点一宽就穿进节点里；改成按节点边界走折线，
    文字标签也移到水平段旁边，不再压在节点或线上。
    """
    mid_y = (y1 + y2) / 2 if abs(y2 - y1) > 40 else y1
    d = (f"M {x1:.1f} {y1:.1f} L {x1:.1f} {mid_y:.1f} "
         f"L {x2:.1f} {mid_y:.1f} L {x2:.1f} {y2:.1f}")
    parts = [f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.5"/>']
    parts.append(_arrow(x2, mid_y, x2, y2, "", color=color))
    if label:
        lx = (x1 + x2) / 2 + label_dx
        ly = mid_y - 7 + label_dy
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="12" fill="#5a6480" '
                     f'text-anchor="middle" font-family="system-ui, sans-serif">'
                     f'{_esc(label)}</text>')
    return "".join(parts)


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


# ---------------- 2. 流程图 ----------------
def draw_flow(data, title=""):
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    if not nodes:
        raise ValueError("nodes 是空的，至少给一个节点")

    pos, sizes, width, height = _layered_positions(nodes, edges, data.get("start"))
    body = []
    geom = []
    for a, b, label in edges:
        if a in pos and b in pos:
            geom.append((a, b, pos[a][0], pos[a][1], pos[b][0], pos[b][1], label))
    slots = _label_slots([(g[2], g[3], g[4], g[5]) for g in geom])
    for (a, b, x1, y1, x2, y2, label), dy in zip(geom, slots):
        w1, h1 = sizes[a]
        w2, h2 = sizes[b]
        if abs(y2 - y1) < 1:                     # 同一层：水平箭头
            body.append(_arrow(x1 + w1 / 2 + 4, y1, x2 - w2 / 2 - 6, y2, label))
        else:
            body.append(_orth_arrow(x1, y1 + h1 / 2, x2, y2 - h2 / 2, label, label_dy=dy))
    for n in nodes:
        x, y = pos[n["id"]]
        w, h = sizes[n["id"]]
        kind = (n.get("kind") or "process").strip().lower()
        if kind in ("start", "end", "起", "止"):
            body.append(_box(x, y, w + 16, h, n["label"], n.get("color") or "green",
                             shape="pill"))
        elif kind in ("decision", "判断", "条件"):
            body.append(_diamond(x, y, w + 34, h + 18, n["label"], n.get("color")))
        else:
            body.append(_box(x, y, w, h, n["label"], n.get("color")))
    return _canvas(max(width, 300), max(height, 220), "".join(body), title)


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
def _dfd_kind(n):
    k = (n.get("kind") or "").strip().lower()
    if k in ("entity", "external", "外部实体", "实体"):
        return "entity"
    if k in ("store", "datastore", "数据存储", "存储"):
        return "store"
    return "process"


def draw_dataflow(data, title=""):
    """数据流图：先按连线分层（一般就是 外部实体 → 处理 → 数据存储），
    再把同层节点按实际宽度排开，连线走正交折线，避免以前写死坐标互相压。"""
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    if not nodes:
        raise ValueError("nodes 是空的，至少给一个节点")

    pos, sizes, width, height = _layered_positions(nodes, edges, data.get("start"))
    body = []

    geom = []
    for a, b, label in edges:
        if a in pos and b in pos:
            geom.append((a, b, pos[a][0], pos[a][1], pos[b][0], pos[b][1], label))
    slots = _label_slots([(g[2], g[3], g[4], g[5]) for g in geom])

    for (a, b, x1, y1, x2, y2, label), dy in zip(geom, slots):
        w1, h1 = sizes[a]
        w2, h2 = sizes[b]
        if abs(y2 - y1) < 1:                       # 同一层：水平箭头
            body.append(_arrow(x1 + w1 / 2 + 4, y1, x2 - w2 / 2 - 6, y2, label))
        else:
            body.append(_orth_arrow(x1, y1 + h1 / 2, x2, y2 - h2 / 2, label, label_dy=dy))

    for n in nodes:
        x, y = pos[n["id"]]
        w, h = sizes[n["id"]]
        k = _dfd_kind(n)
        if k == "entity":
            body.append(_box(x, y, w + 24, h, n["label"], n.get("color") or "blue"))
        elif k == "store":
            # 经典 DFD 数据存储画法：右侧开口的矩形（左右双竖线）
            fill, stroke, text_color = _color_of(n.get("color") or "gray")
            left, right = x - (w + 24) / 2, x + (w + 24) / 2
            body.append(
                f'<rect x="{left:.1f}" y="{y - h / 2:.1f}" width="{w + 24:.1f}" height="{h}" '
                f'fill="{fill}" stroke="none"/>'
                f'<line x1="{left:.1f}" y1="{y - h / 2:.1f}" x2="{left:.1f}" '
                f'y2="{y + h / 2:.1f}" stroke="{stroke}" stroke-width="2"/>'
                f'<line x1="{left + 7:.1f}" y1="{y - h / 2:.1f}" x2="{left + 7:.1f}" '
                f'y2="{y + h / 2:.1f}" stroke="{stroke}" stroke-width="1.2"/>'
                f'<line x1="{right - 7:.1f}" y1="{y - h / 2:.1f}" x2="{right - 7:.1f}" '
                f'y2="{y + h / 2:.1f}" stroke="{stroke}" stroke-width="1.2"/>'
                f'<line x1="{right:.1f}" y1="{y - h / 2:.1f}" x2="{right:.1f}" '
                f'y2="{y + h / 2:.1f}" stroke="{stroke}" stroke-width="2"/>'
                f'<text x="{x:.1f}" y="{y + 4.5:.1f}" font-size="{FONT}" fill="{text_color}" '
                f'text-anchor="middle" font-family="system-ui, sans-serif">{_esc(n["label"])}</text>')
        else:
            # 处理：圆角矩形（以前画圆，标签一长就溢出来）
            body.append(_box(x, y, w + 20, h, n["label"], n.get("color") or "green",
                             shape="rect"))
    return _canvas(max(width, 320), max(height, 220), "".join(body), title)


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

    row_count = (count + cols - 1) // cols
    row_h = [0.0] * row_count
    for i, it in enumerate(items):
        r = i // cols
        row_h[r] = max(row_h[r], it["h"])
    row_y = []
    acc = PAD + 40
    for h in row_h:
        row_y.append(acc + h / 2)
        acc += h + row_gap

    pos = {}
    for i, it in enumerate(items):
        c, r = i % cols, i // cols
        x = starts[c] + it["w"] / 2
        y = row_y[r]
        pos[it["name"]] = (x, y)
        it["x"], it["y"] = x, y

    total_h = acc + PAD

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


# 最近画过的 SVG：前端要「内联显示」用，工具回执里不带（不然几千 token 白烧）
_SVG_CACHE = {}
_SVG_ORDER = []


def svg_of(aid):
    """按产物 id 取回 SVG 源码（卡片内联渲染用）。取不到返回空串。"""
    return _SVG_CACHE.get(str(aid or ""), "")


def _remember_svg(aid, svg):
    _SVG_CACHE[aid] = svg
    _SVG_ORDER.append(aid)
    while len(_SVG_ORDER) > 200:
        old_aid = _SVG_ORDER.pop(0)
        _SVG_CACHE.pop(old_aid, None)



# ---------------- Graphviz：布局交给专业工具 ----------------
# 自己写分层/正交连线/标签避让永远有边角 bug，这类图 Graphviz 做了几十年。
# 装了 dot 就用它排版；没装（或出错）就退回内置简易布局，功能不丢。
DOT_FONT = "Noto Sans CJK SC"
_DOT_BIN = shutil.which("dot")
_DOT_LOCK = threading.Lock()


def _inject_open_rects(svg, dot_src, prefix="_store_"):
    """在数据存储的文字外补一个「右侧开口的长方形」。

    graphviz 2.43 不支持 HTML label 的 sides（试过只画三边，结果还是整框），
    所以先把存储节点画成纯文字，再拿 dot -Tplain 的坐标自己补线：
    画上、下、左三条边，右边留开口——就是课本里数据存储那个符号。
    """
    if not svg or not _DOT_BIN:
        return svg
    try:
        with _DOT_LOCK:
            plain = subprocess.run([_DOT_BIN, "-Tplain"], input=dot_src.encode("utf-8"),
                                   capture_output=True, timeout=8)
        if plain.returncode != 0:
            return svg
    except Exception:                        # noqa: BLE001
        return svg
    lines = plain.stdout.decode("utf-8", "ignore").splitlines()
    height = 0.0
    boxes = []
    for line in lines:
        parts = line.split(" ")
        if parts[0] == "graph" and len(parts) >= 4:
            try:
                height = float(parts[3])
            except ValueError:
                continue
        elif parts[0] == "node" and len(parts) >= 6 and parts[1].startswith(prefix):
            try:
                x, y, w, h = (float(v) for v in parts[2:6])
            except ValueError:
                continue
            boxes.append((x, y, w, h))
    if not boxes:
        return svg
    parts_svg = []
    for x, y, w, h in boxes:
        cx, cy = x * 72.0, (height - y) * 72.0
        bw, bh = w * 72.0 + 26.0, h * 72.0 + 20.0
        left, top, bottom = cx - bw / 2, cy - bh / 2, cy + bh / 2
        right = cx + bw / 2
        parts_svg.append(
            f'<g class="dfd-store"><path d="M{left:.1f},{top:.1f} H{right:.1f} '
            f'M{left:.1f},{top:.1f} V{bottom:.1f} M{left:.1f},{bottom:.1f} H{right:.1f}" '
            'fill="none" stroke="#4b5563" stroke-width="1.8"/></g>'
        )
    return svg.replace("</svg>", "".join(parts_svg) + "</svg>", 1)


def _dot_svg(dot_src, timeout=8, engine="dot"):
    """把 DOT 交给 graphviz 生成响应式 SVG（失败返回空串，调用方退回内置布局）。

    engine 默认 dot；ER 图的属性布点要用 neato 才会按指定坐标画。
    """
    args = []
    if "pin=true" in dot_src:
        engine, args = "neato", ["-n2"]      # 坐标已在 DOT 里，只让 neato 连边
    binary = _DOT_BIN
    if engine != "dot":
        binary = shutil.which(engine) or _DOT_BIN
    if not binary:
        return ""
    try:
        with _DOT_LOCK:
            proc = subprocess.run([binary, "-Tsvg", *args], input=dot_src.encode("utf-8"),
                                  capture_output=True, timeout=timeout)
        if proc.returncode != 0:
            return ""
        svg = proc.stdout.decode("utf-8", "ignore")
    except Exception:                        # noqa: BLE001
        return ""
    start = svg.find("<svg")
    if start < 0:
        return ""
    svg = svg[start:]
    head_end = svg.find(">")
    head = svg[:head_end]
    head = re.sub(r'\s(?:width|height)="[^"]*"', "", head)
    head = head.replace("<svg", '<svg width="100%"', 1)
    return head + svg[head_end:]


def _dot_attr(attrs):
    return " ".join(f'{k}="{v}"' for k, v in attrs.items() if v not in (None, ""))


def _dot_colors(color):
    fill, stroke, text = _color_of(color)
    return {"fillcolor": fill, "color": stroke, "fontcolor": text}


def _dot_label(text):
    return (str(text if text is not None else "")
            .replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n"))


def _dot_graph(rankdir="TB", splines="ortho", nodesep=0.45, ranksep=0.6, pad=0.22):
    return (
        "digraph G {\n"
        f'  graph [rankdir={rankdir}, bgcolor="transparent", splines={splines}, '
        f'nodesep={nodesep}, ranksep={ranksep}, pad={pad}, fontname="{DOT_FONT}"];\n'
        '  node [shape=box, style="rounded,filled", fontname="' + DOT_FONT + '", fontsize=13, '
        'color="#9a9aae", fillcolor="#ffffff", fontcolor="#33333d", penwidth=1.4, '
        'margin="0.16,0.09"];\n'
        '  edge [color="#7d8396", fontname="' + DOT_FONT + '", fontsize=11, arrowsize=0.7, '
        'penwidth=1.3, fontcolor="#4d5262"];\n'
    )


def _dot_tree(data):
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    if not nodes:
        return ""
    lines = [_dot_graph(rankdir="TB", splines="polyline", ranksep=0.55)]
    for n in nodes:
        attrs = {"label": _dot_label(n["label"])}
        attrs.update(_dot_colors(n.get("color")))
        if len(n["label"]) <= 3:
            attrs.update({"shape": "circle", "width": "0.62", "fixedsize": "false"})
        lines.append(f'  "n_{_dot_label(n["id"])}" [{_dot_attr(attrs)}];')
    for a, b, _ in edges:
        lines.append(f'  "n_{_dot_label(a)}" -> "n_{_dot_label(b)}";')
    lines.append("}")
    return "\n".join(lines)


def _dot_flow(data):
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    if not nodes:
        return ""
    lines = [_dot_graph(rankdir="TB", splines="polyline", nodesep=0.5, ranksep=0.75)]
    for n in nodes:
        kind = (n.get("kind") or "process").strip().lower()
        attrs = {"label": _dot_label(n["label"])}
        if kind in ("start", "end", "起", "止"):
            attrs.update({"shape": "box", "style": "rounded,filled",
                          "fillcolor": "#d8f3e0", "color": "#3f9a63",
                          "fontcolor": "#1d5a36"})
        elif kind in ("decision", "判断", "条件"):
            attrs.update({"shape": "diamond", "style": "filled",
                          "fillcolor": "#ffefc9", "color": "#c39327",
                          "fontcolor": "#6b4d08", "margin": "0.04,0.02"})
        else:
            attrs.update(_dot_colors(n.get("color")))
        lines.append(f'  "n_{_dot_label(n["id"])}" [{_dot_attr(attrs)}];')
    for a, b, label in edges:
        attrs = {"label": _dot_label(label)} if label else {}
        if label:
            attrs.update({"fontsize": "10", "labeldistance": "1.2", "labelangle": "0"})
        lines.append(f'  "n_{_dot_label(a)}" -> "n_{_dot_label(b)}" [{_dot_attr(attrs)}];')
    lines.append("}")
    return "\n".join(lines)


def _dot_graph_plain(data):
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    if not nodes:
        return ""
    lines = [_dot_graph(rankdir="LR", splines="spline", ranksep=0.7)]
    for n in nodes:
        attrs = {"label": _dot_label(n["label"]),
                 "shape": "ellipse" if n.get("kind") == "circle" else "box"}
        attrs.update(_dot_colors(n.get("color")))
        lines.append(f'  {name_of[n["id"]]} [{_dot_attr(attrs)}];')
    for a, b, label in edges:
        attrs = {"label": _dot_label(label)} if label else {}
        lines.append(f'  {name_of.get(a, "n_" + _dot_label(a))} -> '
                     f'{name_of.get(b, "n_" + _dot_label(b))} [{_dot_attr(attrs)}];')
    lines.append("}")
    return "\n".join(lines)


def _dot_dataflow(data):
    """DFD（教材画法）：外部实体=长方形，加工=椭圆形，数据存储=半开长方形。

    数据存储的框不交给 graphviz（2.43 的 HTML label sides 不生效），
    这里只画文字节点，渲染后由 _inject_open_rects 补「右侧开口」的矩形。
    """
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    if not nodes:
        return ""
    lines = [_dot_graph(rankdir="LR", splines="polyline", nodesep=0.6, ranksep=0.95)]
    name_of = {}
    for n in nodes:
        k = _dfd_kind(n)
        name_of[n["id"]] = (f"_store_{_dot_label(n['id'])}" if k == "store"
                            else f"n_{_dot_label(n['id'])}")
        if k == "entity":
            attrs = {"shape": "box", "style": "filled", "fillcolor": "#dbe9ff",
                     "color": "#3c73c4", "fontcolor": "#1d4076",
                     "label": _dot_label(n["label"])}
        elif k == "store":
            attrs = {"shape": "none", "margin": "0.30,0.20", "fontsize": 13,
                     "fontcolor": "#33333d", "label": _dot_label(n["label"])}
        else:
            attrs = {"shape": "ellipse", "style": "filled", "fillcolor": "#d8f3e0",
                     "color": "#3f9a63", "fontcolor": "#1d5a36",
                     "label": _dot_label(n["label"])}
        lines.append(f'  {name_of[n["id"]]} [{_dot_attr(attrs)}];')
    for a, b, label in edges:
        attrs = {"label": _dot_label(label)} if label else {}
        na = name_of.get(a, f'n_{_dot_label(a)}')
        nb = name_of.get(b, f'n_{_dot_label(b)}')
        lines.append(f'  {na} -> {nb} [{_dot_attr(attrs)}];')
    lines.append("}")
    return "\n".join(lines)


def _er_parse(data):
    """把 ER 数据整理成 (实体列表, 关系列表)。

    实体：{"name", "attributes": [{"name", "pk"?, "fk"?} | "名字"]}
    关系：{"from", "to", "label", "cardinality": "N:M"}
    """
    entities, relations = [], []
    for ent in data.get("entities") or data.get("tables") or []:
        if isinstance(ent, dict):
            name = str(ent.get("name") or ent.get("label") or "").strip()
            attrs = ent.get("attributes") or ent.get("fields") or []
        else:
            name, attrs = str(ent).strip(), []
        if not name:
            continue
        entities.append({"name": name, "attributes": [a for a in attrs
                                                      if (a.get("name") if isinstance(a, dict) else a)]})
    for rel in data.get("relations") or data.get("links") or []:
        if not isinstance(rel, dict):
            continue
        a, b = str(rel.get("from") or "").strip(), str(rel.get("to") or "").strip()
        if a and b:
            relations.append({"from": a, "to": b,
                              "label": str(rel.get("label") or rel.get("name") or ""),
                              "cardinality": str(rel.get("cardinality") or "")})
    return entities, relations


def _er_skeleton_positions(entities, relations):
    """先用 dot 把「实体 + 关系菱形」的骨架排好，拿到每个节点的坐标（英寸）。"""
    lines = [_dot_graph(rankdir="TB", splines="line", nodesep=2.1, ranksep=2.7)]
    ids = {}
    for i, ent in enumerate(entities):
        ids[ent["name"]] = f"e{i}"
        lines.append(f'  e{i} [shape=box, width=1.0, height=0.45, '
                     f'label="{_dot_label(ent["name"])}"];')
    for j, rel in enumerate(relations):
        rid = f"r{j}"
        lines.append(f'  {rid} [shape=diamond, width=1.15, height=0.55, '
                     f'label="{_dot_label(rel["label"] or "关系")}"];')
        lines.append(f'  {ids[rel["from"]]} -> {rid} [arrowhead=none];')
        lines.append(f'  {rid} -> {ids[rel["to"]]};')
    lines.append("}")
    src = "\n".join(lines)
    binary = _DOT_BIN
    if not binary:
        return {}
    try:
        with _DOT_LOCK:
            proc = subprocess.run([binary, "-Tplain"], input=src.encode("utf-8"),
                                  capture_output=True, timeout=8)
        if proc.returncode != 0:
            return {}
        pos = {}
        for line in proc.stdout.decode("utf-8", "ignore").splitlines():
            parts = line.split(" ")
            if parts[0] == "node" and len(parts) >= 4:
                try:
                    pos[parts[1]] = (float(parts[2]), float(parts[3]))
                except ValueError:
                    continue
        return pos
    except Exception:                        # noqa: BLE001
        return {}


def _dot_er(data):
    """ER 图（经典 Chen 画法，按课本排版）：
    实体 = 长方形；关系 = 菱形；属性 = 椭圆形，绕着所属实体一圈摆放。
    两遍布局：先 dot 排实体/关系骨架，再用 neato 按固定坐标画，
    属性和实体之间用不带箭头的细线连，关系两侧标 1 / N / M。
    """
    entities, relations = _er_parse(data)
    if not entities:
        return ""
    pos = _er_skeleton_positions(entities, relations)

    PT = 72.0                              # neato 的 pos 用「点」，1 英寸 = 72 点

    def coord(name, fallback=(0.0, 0.0)):
        x, y = pos.get(name, fallback)
        return x * PT, y * PT

    lines = [
        "graph G {",
        '  graph [bgcolor="transparent", splines=false, overlap=false, '
        f'pad=0.3, fontname="{DOT_FONT}"];',
        '  node [fontname="' + DOT_FONT + '", fontsize=13, penwidth=1.4];',
        '  edge [color="#9aa0ae", penwidth=1.2, fontname="' + DOT_FONT + '", fontsize=11];',
    ]
    # 实体
    ent_ids = {}
    for i, ent in enumerate(entities):
        x, y = coord(f"e{i}")
        ent_ids[ent["name"]] = f"e{i}"
        lines.append(f'  e{i} [shape=box, width=1.0, height=0.45, fixedsize=true, '
                     f'pos="{x:.3f},{y:.3f}!", pin=true, style="filled", fillcolor="#dbe9ff", '
                     f'color="#3c73c4", fontcolor="#1d4076", label="{_dot_label(ent["name"])}"];')
    # 关系
    rel_ids = {}
    for j, rel in enumerate(relations):
        x, y = coord(f"r{j}")
        rel_ids[j] = f"r{j}"
        lines.append(f'  r{j} [shape=diamond, width=1.15, height=0.55, fixedsize=true, '
                     f'pos="{x:.3f},{y:.3f}!", pin=true, style="filled", fillcolor="#ffefc9", '
                     f'color="#c39327", fontcolor="#6b4d08", label="{_dot_label(rel["label"] or "关系")}"];')
    # 关系连线 + 基数
    for j, rel in enumerate(relations):
        card = rel["cardinality"]
        tail, head = ("", "")
        if ":" in card:
            tail, head = [x.strip() for x in card.split(":", 1)]
        lines.append(f'  {ent_ids[rel["from"]]} -- r{j} [taillabel="{tail}", '
                     f'labeldistance=1, fontcolor="#6b4d08"];')
        lines.append(f'  r{j} -- {ent_ids[rel["to"]]} [taillabel="{head}", '
                     f'labeldistance=1, fontcolor="#6b4d08"];')
    # 属性：绕实体一圈（从正上方开始顺时针），半径随属性数变大
    for i, ent in enumerate(entities):
        ex, ey = coord(f"e{i}")
        attrs = ent["attributes"][:12]
        count = len(attrs)
        if not count:
            continue
        radius = (1.15 + 0.09 * count) * PT
        for aidx, attr in enumerate(attrs):
            if isinstance(attr, dict):
                aname = str(attr.get("name") or attr.get("label") or "")
                pk = bool(attr.get("pk") or attr.get("primary"))
                fk = bool(attr.get("fk") or attr.get("foreign"))
            else:
                aname, pk, fk = str(attr), False, False
            if not aname:
                continue
            mark = " PK" if pk else (" FK" if fk else "")
            angle = -math.pi / 2 + (2 * math.pi * aidx / count)
            ax = ex + radius * math.cos(angle)
            ay = ey - radius * math.sin(angle) * 0.78
            nid = f"a{i}_{aidx}"
            lines.append(f'  {nid} [shape=ellipse, fixedsize=true, width=0.95, height=0.42, '
                         f'pos="{ax:.3f},{ay:.3f}!", pin=true, style="filled", fillcolor="#ffffff", '
                         f'color="#9aa0ae", fontcolor="#3d404a", fontsize=12, '
                         f'label="{_dot_label(aname + mark)}"];')
            lines.append(f'  e{i} -- {nid} [arrowhead=none, color="#9aa0ae"];')
    lines.append("}")
    # neato 固定坐标渲染在大图上会画出毛边/虚线伪影，这里改让 dot 自己排：
    # 把 pos / pin / fixedsize 去掉，符号（矩形/菱形/椭圆）保持不变。
    out = []
    for line in "\n".join(lines).split("\n"):
        line = re.sub(r'pos="[^"]*", ', "", line)
        line = line.replace("pin=true, ", "").replace("fixedsize=true, ", "")
        out.append(line)
    return "\n".join(out)


def _dot_arch(data):
    layers = data.get("layers") or []
    if not layers:
        return ""
    lines = [_dot_graph(rankdir="TB", splines="line", nodesep=0.35, ranksep=0.55)]
    lines.append('  node [shape=box, style="rounded,filled", fillcolor="#dbe9ff", '
                 'color="#3c73c4", fontcolor="#1d4076"];')
    prev_ids = []
    for li, layer in enumerate(layers):
        if isinstance(layer, dict):
            lname = str(layer.get("name") or layer.get("label") or "")
            items = layer.get("items") or layer.get("nodes") or []
        else:
            lname, items = "", (layer if isinstance(layer, list) else [layer])
        ids = []
        for ii, item in enumerate(items[:8]):
            text = str(item.get("label") if isinstance(item, dict) else item)
            nid = f"l{li}_{ii}"
            ids.append(nid)
            lines.append(f'  {nid} [label="{_dot_label(_clip(text, 18))}"];')
        if ids:
            lines.append("  {rank=same; " + "; ".join(ids) + "}")
        if lname:
            lines.append(f'  subgraph cluster_{li} {{ label="{_dot_label(lname)}"; '
                         f'style="rounded"; color="#d8e2f0"; fontcolor="#4d5262"; '
                         + " ".join(ids) + " }")
        for prev in prev_ids:
            lines.append(f'  {prev} -> {ids[0]} [style=invis];')
        prev_ids = ids
    lines.append("}")
    return "\n".join(lines)


GRAPHVIZ_BUILDERS = {
    "tree": _dot_tree,
    "flow": _dot_flow,
    "graph": _dot_graph_plain,
    "dataflow": _dot_dataflow,
    "er": _dot_er,
    "arch": _dot_arch,
}


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
    svg = ""
    builder = GRAPHVIZ_BUILDERS.get(key)
    if builder:
        try:
            dot_src = builder(data)
            if dot_src:
                svg = _dot_svg(dot_src)
                if svg and key == "dataflow":
                    svg = _inject_open_rects(svg, dot_src)
        except Exception:                # noqa: BLE001
            svg = ""
    if not svg:
        # 没装 graphviz（或它出错）时退回内置简易布局，功能不丢
        try:
            svg = drawer(data, "")      # 图名由卡片标题显示，SVG 里不再重复画
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except Exception as exc:                 # noqa: BLE001
            return json.dumps({"error": f"画图失败：{type(exc).__name__}: {exc}"},
                              ensure_ascii=False)

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
    _remember_svg(aid, svg)
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
                "  er: （经典 Chen 画法：实体=矩形、关系=菱形、属性=椭圆）"
                "{entities:[{name, attributes:[{name, pk?, fk?}]}], "
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
