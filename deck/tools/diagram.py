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
DFD_NOTATIONS = {
    # Yourdon / DeMarco：加工画圆，数据存储左侧双竖线
    "demarco": "demarco", "de marco": "demarco", "demarco-yourdon": "demarco",
    "yourdon": "demarco", "yourdon-demarco": "demarco", "circle": "demarco",
    "round": "demarco", "德马科": "demarco", "尤尔登": "demarco",
    "教材": "demarco", "圆": "demarco", "圆形": "demarco",
    # Gane / Sarson：加工画圆角矩形，数据存储左侧单竖线
    "gane": "gane", "gane-sarson": "gane", "sarson": "gane", "gane&sarson": "gane",
    "ganeandsarson": "gane", "rounded": "gane", "roundrect": "gane",
    "甘恩": "gane", "盖恩": "gane", "圆角": "gane", "圆角矩形": "gane",
}


def _dfd_notation(data):
    """DFD 画法：demarco（默认，教材那种）或 gane。

    模型可能把画法放在 notation / style / method 等不同键上，这里都收。
    """
    raw = ""
    for key in ("notation", "dfd_style", "draw_style", "style", "method", "symbol"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            raw = val.strip().lower()
            break
    return DFD_NOTATIONS.get(raw, "demarco")


def _dfd_kind(n):
    k = (n.get("kind") or "").strip().lower()
    if k in ("entity", "external", "外部实体", "实体"):
        return "entity"
    if k in ("store", "datastore", "数据存储", "存储"):
        return "store"
    return "process"


def draw_dataflow(data, title=""):
    """数据流图：先按连线分层（一般就是 外部实体 → 处理 → 数据存储），
    再把同层节点按实际宽度排开，连线走正交折线，避免以前写死坐标互相压。

    notation 决定画法：demarco（默认，加工画圆）/ gane（加工画圆角矩形）。
    """
    notation = _dfd_notation(data)
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
            # 数据存储：DeMarco 两条平行横线夹住名字，Gane 半开矩形（右侧开口）
            fill, stroke, text_color = _color_of(n.get("color") or "gray")
            left, right = x - (w + 24) / 2, x + (w + 24) / 2
            top, bottom = y - h / 2, y + h / 2
            d = (f"M{left:.1f},{top:.1f} H{right:.1f} "
                 f"M{left:.1f},{bottom:.1f} H{right:.1f}")
            if notation != "demarco":
                d += f" M{left:.1f},{top:.1f} V{bottom:.1f}"
            body.append(
                f'<rect x="{left:.1f}" y="{top:.1f}" width="{w + 24:.1f}" height="{h}" '
                f'fill="{fill}" stroke="none"/>'
                f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="1.8"/>'
                f'<text x="{x:.1f}" y="{y + 4.5:.1f}" font-size="{FONT}" fill="{text_color}" '
                f'text-anchor="middle" font-family="system-ui, sans-serif">{_esc(n["label"])}</text>')
        elif notation == "gane":
            # Gane/Sarson：加工画圆角矩形
            body.append(_box(x, y, w + 20, h, n["label"], n.get("color") or "green",
                             shape="rect"))
        else:
            # Yourdon/DeMarco：加工画正圆（名字短了才画得下，提示词里已经要求短名）
            side = max(w + 20, h)
            body.append(_box(x, y, side, side, n["label"], n.get("color") or "green",
                             shape="circle"))
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


def _inject_open_rects(svg, dot_src, prefix="_store_", notation="gane"):
    """把数据存储节点的矩形换成课本里的数据存储符号。

    graphviz 2.43 不支持 HTML label 的 sides，所以先按普通 box 渲染（这样布局会
    正常给它留位置、不会挤在一起），再把该节点 <g> 里的 polygon 换成我们要的线：

      - DeMarco：上下**两条平行横线**（名字夹在中间，左右都不封口）
      - Gane：**半开矩形**（上边、下边、左边，右侧开口）
    """
    if not svg:
        return svg
    # graphviz 的 <g id> 是 node1/node2… 这种自动编号，节点名在 <title> 里，
    # 所以要按 title 前缀找
    pattern = re.compile(r'(<g id="node\d+" class="node">)([\s\S]*?)(</g>)')
    changed = 0

    def fix(match):
        nonlocal changed
        head, body, tail = match.group(1), match.group(2), match.group(3)
        title = re.search(r"<title>([^<]*)</title>", body)
        if not title or not title.group(1).startswith(prefix):
            return match.group(0)
        poly = re.search(r'<polygon[^>]*points="([^"]+)"', body)
        if not poly:
            return match.group(0)
        nums = [float(v) for v in re.findall(r'-?\d+(?:\.\d+)?', poly.group(1))]
        if len(nums) < 8:
            return match.group(0)
        xs, ys = nums[0::2], nums[1::2]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        if notation == "demarco":
            # DeMarco：两条平行横线，名字夹在两条线中间
            path = (f'<path d="M{left:.1f},{top:.1f} H{right:.1f} '
                    f'M{left:.1f},{bottom:.1f} H{right:.1f}" '
                    'fill="none" stroke="#4b5563" stroke-width="1.8"/>')
        else:
            # Gane：半开矩形（上、下、左三条边，右边开口）
            path = (f'<path d="M{left:.1f},{top:.1f} H{right:.1f} '
                    f'M{left:.1f},{top:.1f} V{bottom:.1f} '
                    f'M{left:.1f},{bottom:.1f} H{right:.1f}" '
                    'fill="none" stroke="#4b5563" stroke-width="1.8"/>')
        body = body.replace(poly.group(0), path, 1)
        changed += 1
        return head + body + tail

    svg = pattern.sub(fix, svg)
    return svg



def _fix_hr_lines(svg):
    """graphviz 的 <hr/> 会输出高度为 0 的 polygon（4 个点 y 全相同），
    带描边时 miter 会在端点拉出斜刺，看起来像歪线。这里把它们换成干净的 <line>。"""
    if not svg:
        return svg
    pattern = re.compile(r'<polygon fill="#1f2023" stroke="#1f2023" points="([^"]+)"\s*/>')

    def repl(m):
        nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", m.group(1))]
        if len(nums) < 8:
            return m.group(0)
        xs, ys = nums[0::2], nums[1::2]
        if max(ys) - min(ys) > 0.01:
            return m.group(0)          # 不是横线，别碰
        return (f'<line x1="{min(xs):.1f}" y1="{sum(ys) / len(ys):.1f}" '
                f'x2="{max(xs):.1f}" y2="{sum(ys) / len(ys):.1f}" '
                'stroke="#1f2023" stroke-width="1.1"/>')

    return pattern.sub(repl, svg)


def _dot_svg(dot_src, timeout=15, engine="dot"):
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
            err = proc.stderr.decode("utf-8", "ignore")[:300]
            print(f"[diagram] graphviz 报错（rc={proc.returncode}）：{err}", flush=True)
            return ""
        svg = proc.stdout.decode("utf-8", "ignore")
    except Exception as exc:                 # noqa: BLE001
        _LAST_DOT_ERROR = f"{type(exc).__name__}: {exc}"
        print(f"[diagram] graphviz 失败：{_LAST_DOT_ERROR}", flush=True)
        return ""
    start = svg.find("<svg")
    if start < 0:
        print("[diagram] graphviz 输出里没有 <svg>", flush=True)
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
        lines.append(f'  "n_{_dot_label(n["id"])}" [{_dot_attr(attrs)}];')
    for a, b, label in edges:
        attrs = {"label": _dot_label(label)} if label else {}
        lines.append(f'  "n_{_dot_label(a)}" -> "n_{_dot_label(b)}" [{_dot_attr(attrs)}];')
    lines.append("}")
    return "\n".join(lines)


def _dot_dataflow(data):
    """DFD：外部实体=长方形，数据存储=开口长方形，加工按画法变：

      - demarco（默认，教材表 3-1）：加工画**正圆**
      - gane：加工画**圆角矩形**

    数据存储的框不交给 graphviz（2.43 的 HTML label sides 不生效），
    这里只画文字节点，渲染后由 _inject_open_rects 补「开口矩形」的框。
    """
    notation = _dfd_notation(data)
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
            # 留点上下留白：DeMarco 那两条平行线才是上下边框，别贴着字
            attrs = {"shape": "box", "style": "filled", "fillcolor": "#f8fafc",
                     "color": "#4b5563", "penwidth": 1.0, "fontsize": 13,
                     "margin": "0.26,0.17", "fontcolor": "#33333d",
                     "label": _dot_label(n["label"])}
        elif notation == "gane":
            attrs = {"shape": "box", "style": "rounded,filled", "fillcolor": "#d8f3e0",
                     "color": "#3f9a63", "fontcolor": "#1d5a36",
                     "label": _dot_label(n["label"])}
        else:
            attrs = {"shape": "circle", "style": "filled", "fillcolor": "#d8f3e0",
                     "color": "#3f9a63", "fontcolor": "#1d5a36",
                     "margin": "0.12,0.10", "label": _dot_label(n["label"])}
        lines.append(f'  {name_of[n["id"]]} [{_dot_attr(attrs)}];')
    for a, b, label in edges:
        attrs = {"label": _dot_label(label)} if label else {}
        na = name_of.get(a, f'n_{_dot_label(a)}')
        nb = name_of.get(b, f'n_{_dot_label(b)}')
        lines.append(f'  {na} -> {nb} [{_dot_attr(attrs)}];')
    lines.append("}")
    return "\n".join(lines)


# ---------------- 9. ER 图（教材 3.3.3 的陈氏画法）----------------
# 教材定义：实体=矩形（框里写实体名）、属性=椭圆（椭圆里写属性名）、
# 联系=菱形（菱形里写联系名），三者之间连**无向边**，联系两端标 1 / N / M。
# 这张图不交给 graphviz：属性要绕着所属实体摆一圈，dot 只会把它们排成一排，
# 结果就是属性互相压、菱形被挤成扁条。所以坐标、尺寸、连线裁剪全部自己算。

_ATTR_SEP = re.compile(r"[、,，;；/／|]")
_PAREN_TAIL = re.compile(r"^(.*?)[（(](.+)[）)]\s*$")
_CARD_TAIL = re.compile(r"([0-9mMnN])\s*[:：]\s*([0-9mMnN])\s*$")

ER_ENTITY_H = 42.0          # 实体矩形高
ER_ATTR_H = 30.0            # 属性椭圆高
ER_DIAMOND_RATIO = 0.62     # 菱形高 / 宽（保持是菱形，不会被拉成扁条）
ER_ROW_GAP = 96.0           # 上下两层之间留给联系菱形的空当


def _split_names(text):
    """「学号、姓名、性别」→ 三个名字。"""
    return [p.strip(" 　\t。.，,") for p in _ATTR_SEP.split(str(text))
            if p.strip(" 　\t。.，,")]


def _er_attrs(raw):
    """属性统一成 [{name, pk, fk}]。

    模型经常图省事，把一堆属性塞进一个字符串（「学号、姓名、性别」），
    这里一律拆成单个属性——教材里每个属性都要单独画一个椭圆。
    """
    out = []
    for item in raw or []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("label") or "").strip()
            if not name:
                continue
            pk = bool(item.get("pk") or item.get("primary") or item.get("is_pk"))
            fk = bool(item.get("fk") or item.get("foreign") or item.get("is_fk"))
            pieces = _split_names(name) if _ATTR_SEP.search(name) else [name]
            for piece in pieces:
                out.append({"name": piece, "pk": pk, "fk": fk})
        else:
            for piece in _split_names(item):
                out.append({"name": piece, "pk": False, "fk": False})
    return out[:10]


def _er_parse(data):
    """把 ER 数据整理成（实体列表, 联系列表）。

    实体：{"name", "attributes": [{"name", "pk"?, "fk"?} | "姓名"]}
    联系：{"from", "to", "label", "cardinality": "M:N", "attributes": [...]}

    容错：模型常把联系属性写进联系名（「借阅(借阅时间/到期时间)」）或把基数
    写进名字（「借阅 M:N」），这里都拆出来，别让它挤在菱形里。
    """
    entities = []
    for ent in data.get("entities") or data.get("tables") or []:
        if isinstance(ent, dict):
            name = str(ent.get("name") or ent.get("label") or "").strip()
            raw_attrs = ent.get("attributes") or ent.get("fields") or []
        else:
            name, raw_attrs = str(ent).strip(), []
        if not name:
            continue
        entities.append({"name": name, "attributes": _er_attrs(raw_attrs)})

    known = {e["name"]: e["name"] for e in entities}
    for e in entities:                      # 名字多写/少写一个空格的宽松匹配
        known.setdefault(e["name"].replace(" ", ""), e["name"])

    relations = []
    raw_rels = (data.get("relations") or data.get("links")
                or data.get("relationships") or [])
    for rel in raw_rels:
        if not isinstance(rel, dict):
            continue
        a = str(rel.get("from") or rel.get("entity1") or rel.get("source") or "").strip()
        b = str(rel.get("to") or rel.get("entity2") or rel.get("target") or "").strip()
        a = known.get(a) or known.get(a.replace(" ", ""))
        b = known.get(b) or known.get(b.replace(" ", ""))
        if not (a and b):
            continue
        label = str(rel.get("label") or rel.get("name") or rel.get("type") or "").strip()
        card = str(rel.get("cardinality") or rel.get("card") or "").strip()
        raw_attrs = list(rel.get("attributes") or rel.get("fields") or [])
        # 「借阅(借阅时间/到期时间)」→ 联系名 + 联系自己的属性
        m = _PAREN_TAIL.match(label)
        if m:
            label = m.group(1).strip()
            raw_attrs.extend(_split_names(m.group(2)))
        # 「借阅 1:N」→ 联系名 + 基数
        if not card:
            m = _CARD_TAIL.search(label)
            if m:
                card = f"{m.group(1)}:{m.group(2)}"
                label = label[:m.start()].strip(" 　:：")
        card = card.replace("：", ":").upper().replace(" ", "")
        relations.append({
            "from": a, "to": b,
            "label": _clip(label or "联系", 6),
            "cardinality": card,
            "attributes": _er_attrs(raw_attrs)[:6],
        })
    return entities, relations


def _er_entity_size(name):
    """实体矩形：只要装得下实体名，别做太大。"""
    return max(92.0, _label_len(name) + 40), ER_ENTITY_H


def _er_diamond_size(label):
    """联系菱形：宽按联系名算，**高跟着宽走**。

    以前宽高写死 1.15 / 0.55，名字一长只有宽度被拉大，菱形就被压成一根扁条。
    菱形内能放字的区域只有宽的一半，所以系数给到 1.6，高度固定取宽的 0.62。
    """
    w = max(72.0, _label_len(label) * 1.6 + 36)
    return w, w * ER_DIAMOND_RATIO


def _er_attr_size(name):
    """属性椭圆：横向长椭圆。"""
    return max(66.0, _label_len(name) + 34), ER_ATTR_H


def _er_ring_radius(base_r, sizes, ratio=0.88):
    """绕一圈要多大半径：按每个椭圆占的角宽，一圈排得下为止。"""
    r = max(58.0, base_r)
    for _ in range(50):
        span = sum(2 * math.asin(min(0.985, (s[0] / 2 + 9) / r)) for s in sizes)
        if span <= 2 * math.pi * ratio:
            break
        r *= 1.06
    return r


def _er_place_attrs(sizes, radius, occupied):
    """按每个椭圆实际占的角度依次摆一圈（所以不会互相压），
    起始角挑「离连线方向最远」的那个相位。"""
    if not sizes:
        return []
    spans = [2 * math.asin(min(0.985, (s[0] / 2 + 9) / radius)) for s in sizes]
    gap = max(0.0, 2 * math.pi - sum(spans)) / len(sizes)
    best = None
    for k in range(72):
        start = 2 * math.pi * k / 72
        angs, cur, cost = [], start, 0.0
        for sp in spans:
            mid = cur + sp / 2
            angs.append(mid)
            for occ in occupied:
                d = abs((mid - occ + math.pi) % (2 * math.pi) - math.pi)
                if d < 0.75:
                    cost += (0.75 - d) ** 2
            cur += sp + gap
        if best is None or cost < best[0]:
            best = (cost, angs)
    return best[1]


def _er_half(kind, w, h, ang):
    """从节点中心沿 ang 方向走到边界要几步（用来把连线裁到框边，不钻进节点里）。"""
    ca, sa = abs(math.cos(ang)), abs(math.sin(ang))
    if kind == "rect":
        return min(w / 2 / max(ca, 1e-6), h / 2 / max(sa, 1e-6))
    if kind == "diamond":
        return 1.0 / (ca / (w / 2) + sa / (h / 2) + 1e-9)
    return 1.0 / math.sqrt((ca / (w / 2)) ** 2 + (sa / (h / 2)) ** 2 + 1e-9)


def _er_link(p1, s1, k1, p2, s2, k2, label="", color=LINE, width=1.35, font_size=12.5):
    """两个节点之间的无向连线（ER 图不画箭头），标签放在靠 p1 的一端。"""
    (x1, y1), (x2, y2) = p1, p2
    ang = math.atan2(y2 - y1, x2 - x1)
    d1 = _er_half(k1, s1[0], s1[1], ang)
    d2 = _er_half(k2, s2[0], s2[1], ang + math.pi)
    ax, ay = x1 + d1 * math.cos(ang), y1 + d1 * math.sin(ang)
    bx, by = x2 - d2 * math.cos(ang), y2 - d2 * math.sin(ang)
    parts = [f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
             f'stroke="{color}" stroke-width="{width}"/>']
    if label:
        lx = ax + 24 * math.cos(ang)
        ly = ay + 24 * math.sin(ang)
        # 往垂直方向让开一点，1 / N / M 才不会压在线上
        ox, oy = -math.sin(ang) * 14, math.cos(ang) * 14
        parts.append(f'<text x="{lx + ox:.1f}" y="{ly + oy + 4.5:.1f}" font-size="{font_size}" '
                     f'font-weight="600" fill="#8a5a12" text-anchor="middle" '
                     f'font-family="system-ui, sans-serif">{_esc(label)}</text>')
    return "".join(parts)


def _er_entity_node(x, y, w, h, name):
    """实体：矩形，框里只写实体名。"""
    return (f'<rect x="{x - w / 2:.1f}" y="{y - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="4" fill="#dbe9ff" stroke="#3c73c4" stroke-width="1.8"/>'
            f'<text x="{x:.1f}" y="{y + 5:.1f}" font-size="13.5" font-weight="600" '
            f'fill="#1d4076" text-anchor="middle" '
            f'font-family="system-ui, sans-serif">{_esc(name)}</text>')


def _er_diamond_node(x, y, w, h, name):
    """联系：菱形，框里只写联系名（选课 / 任教 / 借阅）。"""
    pts = (f"{x:.1f},{y - h / 2:.1f} {x + w / 2:.1f},{y:.1f} "
           f"{x:.1f},{y + h / 2:.1f} {x - w / 2:.1f},{y:.1f}")
    return (f'<polygon points="{pts}" fill="#ffefc9" stroke="#c39327" stroke-width="1.8"/>'
            f'<text x="{x:.1f}" y="{y + 4.5:.1f}" font-size="13" fill="#6b4d08" '
            f'text-anchor="middle" font-family="system-ui, sans-serif">{_esc(name)}</text>')


def _er_attr_node(x, y, w, h, name, pk=False):
    """属性：椭圆，名字写在椭圆里；主键加下划线（教材写法）。"""
    text_w = _label_len(name)
    parts = [f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{w / 2:.1f}" ry="{h / 2:.1f}" '
             f'fill="#ffffff" stroke="#8a8fa3" stroke-width="1.3"/>',
             f'<text x="{x:.1f}" y="{y + 4.5:.1f}" font-size="12.5" fill="#3d404a" '
             f'text-anchor="middle" font-family="system-ui, sans-serif">{_esc(name)}</text>']
    if pk:
        parts.append(f'<line x1="{x - text_w / 2:.1f}" y1="{y + 8.5:.1f}" '
                     f'x2="{x + text_w / 2:.1f}" y2="{y + 8.5:.1f}" '
                     f'stroke="#3d404a" stroke-width="1"/>')
    return "".join(parts)


def _er_levels(names, adj):
    """挑一个根做 BFS，让「最挤的那一层」实体最少（图才不会宽到看不清字）。"""
    best = None
    for root in names:
        level = {root: 0}
        queue = [root]
        while queue:
            cur = queue.pop(0)
            for nb in adj[cur]:
                if nb not in level:
                    level[nb] = level[cur] + 1
                    queue.append(nb)
        for n in names:
            level.setdefault(n, 0)
        rows = {}
        for n in names:
            rows.setdefault(level[n], []).append(n)
        score = (max(len(v) for v in rows.values()), max(level.values()))
        if best is None or score < best[0]:
            best = (score, level)
    return best[1]


def draw_er(data, title=""):
    """E-R 图（教材陈氏画法）：实体矩形、联系菱形、属性椭圆，三者之间无向边。

    排版思路：
      1. 按联系分层（自动挑「最宽层最窄」的根），同层实体左右排开；
      2. 联系菱形放在两端实体连线的中点，层与层之间专门留一条空当给它；
      3. 属性按各自占的角度绕实体摆一圈，整体旋转避开连线方向，互不叠压；
      4. 连线一律裁到节点边界，不钻进框里。
    """
    entities, relations = _er_parse(data)
    if not entities:
        raise ValueError("entities 是空的，至少给一个实体")

    names = [e["name"] for e in entities]
    dim = {e["name"]: _er_entity_size(e["name"]) for e in entities}
    asize = {e["name"]: [_er_attr_size(a["name"]) for a in e["attributes"]]
             for e in entities}

    adj = {n: [] for n in names}
    for rel in relations:
        adj[rel["from"]].append(rel["to"])
        adj[rel["to"]].append(rel["from"])

    level = _er_levels(names, adj)
    rows = {}
    for n in names:
        rows.setdefault(level[n], []).append(n)

    # 属性圈半径：既要绕得下一圈，也不能压到实体框
    rad = {}
    for e in entities:
        sizes = asize[e["name"]]
        if not sizes:
            rad[e["name"]] = 0.0
            continue
        w, h = dim[e["name"]]
        rad[e["name"]] = _er_ring_radius(
            max(w, h) / 2 + max(s[0] for s in sizes) / 2 + 26, sizes)

    # 联系自己的属性也要绕菱形一圈，层间空当得按它算
    rsizes, rrad = {}, {}
    row_gap = ER_ROW_GAP
    for j, rel in enumerate(relations):
        dw, dh = _er_diamond_size(rel["label"])
        sizes = [_er_attr_size(a["name"]) for a in rel["attributes"]]
        rsizes[j] = sizes
        rrad[j] = 0.0
        if sizes:
            rrad[j] = _er_ring_radius(max(dw, dh) / 2 + max(s[0] for s in sizes) / 2 + 22,
                                      sizes)
            row_gap = max(row_gap, 2 * (rrad[j] + 18) + dh)

    # 层内顺序：按上一层邻居的平均槽位排，减少连线交叉
    order, slot = {}, {}
    for lv in sorted(rows):
        row = rows[lv]
        if lv and len(row) > 1:
            def keyf(n):
                near = [slot[o] for o in adj[n] if o in slot]
                return sum(near) / len(near) if near else 0.5
            row = sorted(row, key=keyf)
        order[lv] = row
        for i, n in enumerate(row):
            slot[n] = i / max(1, len(row) - 1)

    def gap_between(a, b):
        """同一层里相邻实体之间的空隙：中间要放联系菱形，所以留够。"""
        if b not in adj[a]:
            return 52.0
        label = next((r["label"] for r in relations
                      if {r["from"], r["to"]} == {a, b}), "")
        return max(52.0, _er_diamond_size(label)[0] + 88)

    row_w = {}
    for lv, row in order.items():
        total = sum(2 * rad[n] + dim[n][0] for n in row)
        total += sum(gap_between(row[i], row[i + 1]) for i in range(len(row) - 1))
        row_w[lv] = total
    max_w = max(row_w.values()) if row_w else 0.0

    pos = {}
    y = PAD + 34
    for lv in sorted(order):
        row = order[lv]
        row_h = max(2 * rad[n] + dim[n][1] for n in row)
        cy = y + row_h / 2
        x = PAD + max(0.0, (max_w - row_w[lv]) / 2)
        for i, n in enumerate(row):
            w, _ = dim[n]
            pos[n] = (x + rad[n] + w / 2, cy)
            x += 2 * rad[n] + w
            if i + 1 < len(row):
                x += gap_between(n, row[i + 1])
        y += row_h + row_gap
    bottom = y - row_gap

    # 联系菱形的落点：两端连线的中点；跨层的错开，别叠在一起
    relgeo, placed = [], []
    for j, rel in enumerate(relations):
        ax, ay = pos[rel["from"]]
        bx, by = pos[rel["to"]]
        dw, dh = _er_diamond_size(rel["label"])
        same_row = abs(ay - by) < 1
        mx, my = (ax + bx) / 2, (ay if same_row else (ay + by) / 2)
        if not same_row:
            for _ in range(6):
                if all(math.hypot(mx - px, my - py) > (dw + pw) / 2 + 34
                       for px, py, pw in placed):
                    break
                my += dh + 26
        placed.append((mx, my, dw))
        relgeo.append((mx, my, dw, dh))

    def attr_points(cx, cy, radius, sizes, occupied):
        return [(cx + radius * math.cos(a), cy + radius * math.sin(a))
                for a in _er_place_attrs(sizes, radius, occupied)]

    body = []
    # 先画线（节点后画，压住线头）
    for e in entities:
        n = e["name"]
        if not asize[n]:
            continue
        cx, cy = pos[n]
        occupied = []
        for rel, (mx, my, dw, dh) in zip(relations, relgeo):
            if n in (rel["from"], rel["to"]):
                occupied.append(math.atan2(my - cy, mx - cx))
        for (ax, ay), s in zip(attr_points(cx, cy, rad[n], asize[n], occupied), asize[n]):
            body.append(_er_link((cx, cy), dim[n], "rect", (ax, ay), s, "ellipse",
                                 color="#9aa0ae", width=1.1))

    for j, rel in enumerate(relations):
        mx, my, dw, dh = relgeo[j]
        tail, head = "", ""
        if ":" in rel["cardinality"]:
            tail, head = [p.strip() for p in rel["cardinality"].split(":", 1)]
        body.append(_er_link(pos[rel["from"]], dim[rel["from"]], "rect",
                             (mx, my), (dw, dh), "diamond", label=tail, width=1.4))
        body.append(_er_link(pos[rel["to"]], dim[rel["to"]], "rect",
                             (mx, my), (dw, dh), "diamond", label=head, width=1.4))
        if rsizes[j]:
            occupied = [math.atan2(pos[rel["from"]][1] - my, pos[rel["from"]][0] - mx),
                        math.atan2(pos[rel["to"]][1] - my, pos[rel["to"]][0] - mx)]
            for (ax, ay), s in zip(attr_points(mx, my, rrad[j], rsizes[j], occupied),
                                   rsizes[j]):
                body.append(_er_link((mx, my), (dw, dh), "diamond", (ax, ay), s, "ellipse",
                                     color="#9aa0ae", width=1.1))

    for e in entities:
        n = e["name"]
        w, h = dim[n]
        cx, cy = pos[n]
        body.append(_er_entity_node(cx, cy, w, h, n))

    for j, rel in enumerate(relations):
        mx, my, dw, dh = relgeo[j]
        body.append(_er_diamond_node(mx, my, dw, dh, rel["label"]))

    for e in entities:
        n = e["name"]
        if not asize[n]:
            continue
        cx, cy = pos[n]
        occupied = []
        for rel, (mx, my, dw, dh) in zip(relations, relgeo):
            if n in (rel["from"], rel["to"]):
                occupied.append(math.atan2(my - cy, mx - cx))
        for (ax, ay), s, attr in zip(attr_points(cx, cy, rad[n], asize[n], occupied),
                                     asize[n], e["attributes"]):
            body.append(_er_attr_node(ax, ay, s[0], s[1], attr["name"], attr["pk"]))

    for j, rel in enumerate(relations):
        if not rsizes[j]:
            continue
        mx, my, dw, dh = relgeo[j]
        occupied = [math.atan2(pos[rel["from"]][1] - my, pos[rel["from"]][0] - mx),
                    math.atan2(pos[rel["to"]][1] - my, pos[rel["to"]][0] - mx)]
        for (ax, ay), s, attr in zip(attr_points(mx, my, rrad[j], rsizes[j], occupied),
                                     rsizes[j], rel["attributes"]):
            body.append(_er_attr_node(ax, ay, s[0], s[1], attr["name"], False))

    width = max(max_w + PAD * 2, 300.0)
    height = bottom + PAD
    svg = _canvas(width, height, "".join(body), title)
    # 按原始尺寸显示：ER 图常常很窄（两三个实体竖着排），
    # 用 width=100% 会被拉到满屏、字大得离谱；窄屏由外层 CSS 的 max-width 兜底。
    return svg.replace('width="100%"', f'width="{width:.0f}"', 1)


def _state_lines(raw):
    """状态变量 / 活动表统一成若干行文本。

    支持：字符串（可含 \n）、列表（元素是字符串或 {"event": "do", "action": "响铃"}）。
    活动表按课本语法输出成「事件名(参数表)/动作表达式」，例如 do/响铃。
    """
    if not raw:
        return []
    if isinstance(raw, str):
        return [line.strip() for line in raw.splitlines() if line.strip()]
    lines = []
    for item in raw if isinstance(raw, (list, tuple)) else [raw]:
        if isinstance(item, dict):
            event = str(item.get("event") or item.get("name") or "").strip()
            action = str(item.get("action") or item.get("expr") or "").strip()
            params = str(item.get("params") or "").strip()
            if event and params:
                event = f"{event}({params})"
            lines.append(f"{event}/{action}" if action else event)
        else:
            text = str(item).strip()
            if text:
                lines.append(text)
    return [line for line in lines if line]


def _dot_state(data):
    """状态转换图（课本画法）：
    - 初始状态：实心小圆点，引出一条箭头指向第一个状态；
    - 终态：同心圆（graphviz 的 doublecircle）；
    - 中间状态：圆角矩形，按「状态名 / 状态变量 / 活动表」分三栏（后两栏可省略）；
    - 箭头上的文字是事件表达式。
    """
    raw_states = data.get("states") or data.get("nodes") or []
    if not raw_states:
        return ""
    states = []
    for item in raw_states:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("label") or "").strip()
            if not name:
                continue
            states.append({
                "name": name,
                "variables": item.get("variables") or item.get("vars") or item.get("value") or "",
                "activities": (item.get("activities") or item.get("activity")
                               or item.get("do") or item.get("action") or ""),
                "kind": str(item.get("kind") or "").lower(),
            })
        else:
            name = str(item).strip()
            if name:
                states.append({"name": name, "variables": "", "activities": "", "kind": ""})
    if not states:
        return ""
    finals = data.get("finals") or data.get("final") or data.get("final_states") or []
    if isinstance(finals, str):
        finals = [finals]
    final_ids = {str(f) for f in finals}
    if not final_ids:
        # 模型经常漏给终态：名字里带这些词的，按终态（同心圆）画，别让图没有终止
        final_ids = {st["name"] for st in states
                     if re.search(r"(断线|挂断|关闭|结束|完成|终止|销毁|已取消|已退款|关机)", st["name"])}
    initial = str(data.get("initial") or "").strip()
    if not initial:
        initial = states[0]["name"]
    # 模型常把初始状态（如「新建」）只写在 initial 里、忘了放进 states：
    # 那就补一个状态节点，不然初始圆点没地方连。
    if initial and all(st["name"] != initial for st in states):
        states.insert(0, {"name": initial, "variables": "", "activities": "", "kind": ""})

    lines = [_dot_graph(rankdir="TB", splines="polyline", nodesep=0.55, ranksep=0.75, pad=0.45)]
    ids = {}
    for i, st in enumerate(states):
        nid = f"s{i}"
        ids[st["name"]] = nid
        kind = st["kind"]
        if kind in ("initial", "start", "初始", "起点") or st["name"] in ("初始", "初态", "起点"):
            lines.append(f'  {nid} [shape=point, width=0.18, height=0.18, fixedsize=true, '
                         f'style="filled", fillcolor="#1f2023", color="#1f2023", label=""];')
            continue
        # 终态不在这里画：统一在最下面画一个同心圆，所有终态都连到它
        # 状态框按课本/课件标准画法：上=状态名（必有），中=状态变量，下=活动表。
        # 中间层和下层是可选内容，有就画、没有就不画（课件里「新建」就只有名字一层）。
        variable_lines = _state_lines(st["variables"])
        activity_lines = _state_lines(st["activities"])
        # 变量层只保留「真的用得上」的：有赋值（timer=0）或者活动表里引用了它。
        # 模型常常给所有状态都塞个 timer，但该状态的活动根本不碰 timer，
        # 那种就该按两层画（状态名 + 活动表）。
        if variable_lines:
            activity_text = " ".join(activity_lines)
            kept = []
            for line in variable_lines:
                name = re.split(r"[=:：]", line, 1)[0].strip()
                if "=" in line or ":" in line or "：" in line:
                    kept.append(line)
                elif name and re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])",
                                        activity_text):
                    kept.append(line)
            variable_lines = kept
        # 三栏固定：状态名 / 状态变量 / 活动表。
        # 活动表里有多条（比如 entry 一行、do 一行）时，用 <br/> 放在**同一栏**里，
        # 不能一格一行，否则就变成四五层了。
        rows = [f'<tr><td><b>{_esc(st["name"])}</b></td></tr>']
        if variable_lines:
            rows.append('<tr><td>' + "<br/>".join(_esc(line) for line in variable_lines) + '</td></tr>')
        if activity_lines:
            rows.append('<tr><td>' + "<br/>".join(_esc(line) for line in activity_lines) + '</td></tr>')
        # 单个圆角外框 + 内部横线：table 不画边框，行与行之间用 <hr/> 画横线。
        # 之前 cellborder=1 会在圆角框里再套一个方框，成了双层边框，和课本不一致。
        label = ('<<table border="0" cellborder="0" cellspacing="0" cellpadding="5">'
                 + "<hr/>".join(rows) + "</table>>")
        lines.append(f'  {nid} [shape=box, style="rounded", fillcolor="#ffffff", '
                     f'color="#1f2023", penwidth=1.2, label={label}];')
    # 初始箭头：标签用题目里真实的触发事件（initial_event），没有就不标；
    # 「初始事件」只是课本符号图里的说明文字，不是图内容
    if initial in ids:
        start_label = str(data.get("initial_event") or data.get("start_event")
                          or data.get("initial_action") or "").strip()
        lines.append('  __start__ [shape=point, width=0.18, height=0.18, fixedsize=true, '
                     'style="filled", fillcolor="#1f2023", color="#1f2023", label=""];')
        if start_label:
            lines.append(f'  __start__ -> {ids[initial]} [label="{_dot_label(start_label)}"];')
        else:
            lines.append(f'  __start__ -> {ids[initial]} [label=""];')
    raw_transitions = data.get("transitions") or data.get("edges") or []
    if not raw_transitions and len(states) > 1:
        # 模型有时只给状态列表、不给转移关系。这时按列表顺序串成链，
        # 至少能画出有方向的流程图；比没有连线、所有状态挤在一排好。
        raw_transitions = [[states[i]["name"], states[i + 1]["name"], ""]
                           for i in range(len(states) - 1)]
    for tr in raw_transitions:
        if isinstance(tr, dict):
            a = str(tr.get("from") or tr.get("source") or "")
            b = str(tr.get("to") or tr.get("target") or "")
            ev = str(tr.get("event") or tr.get("label") or "")
            guard = str(tr.get("guard") or "").strip()
            action = str(tr.get("action") or "").strip()
            if guard:
                ev = f"{ev}[{guard}]"
            if action:
                ev = f"{ev}/{action}"
        else:
            seq = list(tr) if isinstance(tr, (list, tuple)) else []
            if len(seq) < 2:
                continue
            a, b = str(seq[0]), str(seq[1])
            ev = seq[2] if len(seq) > 2 else ""
        if a in ids and b in ids:
            lines.append(f'  {ids[a]} -> {ids[b]} [label="{_dot_label(ev)}"];')
    # 统一的终态：只画一个实心同心圆，多个终态各自连一条线过来（课本/课件都是这样）
    terminal_states = [st["name"] for st in states
                       if st["name"] in final_ids or st.get("kind") in ("final", "end")]
    if terminal_states:
        lines.append('  __final__ [shape=doublecircle, width=0.3, height=0.3, '
                     'style="filled", fillcolor="#ffe066", color="#1f2023", label=""];')
        for name in terminal_states:
            if name in ids:
                lines.append(f'  {ids[name]} -> __final__ [label=""];')
    lines.append("}")
    return "\n".join(lines)


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
    "arch": _dot_arch,
    "state": _dot_state,
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
    "state": draw_flow,
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
    "状态图": "state", "状态转换图": "state", "状态机": "state", "state": "state",
    "statechart": "state", "stm": "state", "fsm": "state",
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
                     f"timeline（时间线）/ er（ER 图）/ state（状态转换图）。要自由创意排版就用 render_html。"
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
                 "actors", "messages", "labels", "values", "kind", "root", "start",
                 "notation", "dfd_style", "draw_style", "style", "method", "symbol")
                if k in kwargs and kwargs[k] is not None}
    if not isinstance(data, dict) or not data:
        return json.dumps({"error": "data 是空的，至少要给节点/层/条目"}, ensure_ascii=False)
    if key == "dataflow" and not any(
            data.get(k) for k in ("notation", "dfd_style", "draw_style", "style",
                                  "method", "symbol")):
        # 模型有时把画法放在顶层参数上（draw_diagram(type=..., notation="gane")）
        for k in ("notation", "dfd_style", "draw_style", "style", "method", "symbol"):
            if kwargs.get(k):
                data["notation"] = kwargs[k]
                break
    dfd_notation = _dfd_notation(data)
    svg = ""
    builder = GRAPHVIZ_BUILDERS.get(key)
    if builder:
        try:
            dot_src = builder(data)
            if dot_src:
                svg = _dot_svg(dot_src)
                if svg and key == "state":
                    svg = _fix_hr_lines(svg)
                if svg and key == "dataflow":
                    svg = _inject_open_rects(svg, dot_src, notation=dfd_notation)
            if not svg:
                # 偶发失败（比如 dot 恰好超时）再给一次机会，别直接退回旧画法
                print(f"[diagram] {key} 第一次渲染失败，重试一次", flush=True)
                svg = _dot_svg(dot_src, timeout=20)
                if svg and key == "state":
                    svg = _fix_hr_lines(svg)
                if svg and key == "dataflow":
                    svg = _inject_open_rects(svg, dot_src, notation=dfd_notation)
        except Exception as exc:         # noqa: BLE001
            print(f"[diagram] {key} 构建 DOT 失败：{type(exc).__name__}: {exc}", flush=True)
            svg = ""
    if not svg and key in ("er", "state", "dataflow"):
        # 这几类退回旧画法会画成完全不同的样式（比如 ER 变成表格），不如直接报错让模型重画
        return json.dumps({
            "error": "graphviz 渲染失败（可能超时），请重新调用一次 draw_diagram；"
                     "如果连续失败，把数据精简一点再试。",
        }, ensure_ascii=False)
    if not svg:
        # 其它类型退回内置简易布局，功能不丢
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
    payload = {
        "ok": True,
        "artifact_id": aid,
        "title": str(title or ""),
        "note": "已经画成卡片给用户看了，正文里一两句话说明画的是什么就行，别再贴任何 SVG/HTML。",
    }
    if key == "state":
        transitions = (data.get("transitions") or data.get("edges") or [])
        if not transitions:
            payload["warning"] = (
                "你没给 transitions，图里的状态转移是按 states 顺序自动连的（所以箭头没有事件表达式）。"
                "请补上真实的 transitions（每条含 from / to / event），重新调用一次 draw_diagram。"
            )
    return json.dumps(payload, ensure_ascii=False)


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
                "edges:[{from,to,label?}], notation?:demarco|gane}\n"
                "    **画数据流图先定画法**：用户没说就先用 ask_user 问一句，"
                "选项给「DeMarco（加工画圆）」和「Gane（加工画圆角矩形）」；"
                "用户选完把结果写进 notation（demarco / gane），不写默认 demarco。"
                "两种画法的数据存储符号也不同（DeMarco 两条平行横线 / Gane 半开矩形），"
                "工具按 notation 自动画，你只标 kind=store 就行。\n"
                "  arch: {layers:[{name, items:[\"模块\", ...]}]}（从上到下的分层架构）\n"
                "  sequence: {actors:[\"客户端\",\"服务端\"], messages:[{from,to,text,dashed?}]}\n"
                "  chart: {kind:bar|line|pie, labels:[...], values:[...]}\n"
                "  timeline: {items:[{time,text}]}\n"
                "  er: （教材陈氏画法：实体=矩形、联系=菱形、属性=椭圆）"
                "{entities:[{name, attributes:[{name, pk?}]}], "
                "relations:[{from,to,label,cardinality?,attributes?}]}\n"
                "    ER 图规矩：菱形里只写联系名（2~4 字，如「选课」「借阅」「任教」）；"
                "实体和联系的属性一律画成椭圆挂上去，不要写进名字里、也不要塞进括号；"
                "联系自己的属性（借阅时间、到期时间…）放 relations[].attributes；"
                "cardinality 按语义写 1:1 / 1:N / M:N，多对多就直接写 M:N，别硬拆。\n"
                "一个图最多 60 个节点、40 字以内的标签，节点太多就拆成两张图。"
                "title 写一句图名（比如「红黑树插入后结构」）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "图类型：tree / flow / graph / dataflow / arch / "
                                       "sequence / chart / timeline / er / state（状态转换图）",
                    },
                    "title": {"type": "string", "description": "图名，可选"},
                    "notation": {
                        "type": "string",
                        "description": "只对数据流图有效：demarco（加工画圆，默认）/ "
                                       "gane（加工画圆角矩形）。也可以写在 data 里。",
                    },
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
