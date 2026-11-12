"""结构化画图：模型只给几十个 token 的 JSON，服务端生成 SVG。

为什么要有这个工具：以前画图全靠 render_html，模型得逐字生成几 KB 的 HTML
（实测 2,000 tokens ≈ 12 秒）。画数据结构图 / 流程图 / 架构图这种「有固定套路」
的图，让模型只描述结构（几十个 token），排版和配色由这里负责：
  - 生成快（1 秒级）
  - 风格统一，深色节点/红色节点这些语义色能对上
  - 数据合法就一定能画出来，不会因为 CSS 写错炸掉
创意海报、界面草图、动画这类仍然用 render_html。
"""
import html as _html
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
    s = str(text if text is not None else "")
    # 模型常常把 < > 写成 &lt; &gt;；HTML 实体还原后再统一转义，避免双重转义
    for _ in range(3):
        _u = _html.unescape(s)
        if _u == s:
            break
        s = _u
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
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


def _para(x, y, w, h, label, color=None):
    """输入/输出：平行四边形。"""
    fill, stroke, text_color = _color_of(color)
    sk = min(w * 0.18, h * 0.55)
    pts = (f"{x - w / 2:.1f},{y + h / 2:.1f} {x - w / 2 + sk:.1f},{y - h / 2:.1f} "
           f"{x + w / 2:.1f},{y - h / 2:.1f} {x + w / 2 - sk:.1f},{y + h / 2:.1f}")
    return (f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>'
            f'<text x="{x:.1f}" y="{y + 4.5:.1f}" font-size="{FONT}" fill="{text_color}" '
            f'text-anchor="middle" font-family="system-ui, -apple-system, Segoe UI, '
            f'PingFang SC, Microsoft YaHei, sans-serif">{_esc(label)}</text>')


def _box(x, y, w, h, label, color=None, shape="rect", bold=False):
    """画一个居中的节点，x/y 是中心点。"""
    fill, stroke, text_color = _color_of(color)
    rx = 10
    if shape == "pill":
        rx = h / 2
    elif shape == "circle":
        rx = w / 2
    elif shape in ("square", "sharp"):
        rx = 0
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
FLOW_VGAP = 46       # 主流程上下节点的间距
FLOW_BGAP = 16       # 两侧分支链节点之间的间距
FLOW_HGAP = 34       # 菱形顶点到两侧分支框的水平距离
FLOW_IO_EXTRA = 18   # 输入输出矩形比普通处理框稍宽一点


def _flow_kind(n):
    return (n.get("kind") or "process").strip().lower()


def _flow_io(kind):
    return kind in ("io", "input", "output", "输入", "输出", "输入输出", "inout")


def _flow_node_width(n):
    w, _h = _node_size(n["label"])
    if _flow_io(_flow_kind(n)):
        w += FLOW_IO_EXTRA
    if _flow_kind(n) in ("decision", "判断", "条件"):
        w += 34
    return w


def _flow_structure(nodes, edges, start):
    """把「一条主脊 + 菱形左右两分支 + 汇合点」的结构抽出来；形状不符就返回 None。"""
    by_id = {n["id"]: n for n in nodes}
    ids = [n["id"] for n in nodes]
    succ = {nid: [] for nid in ids}
    pred = {nid: [] for nid in ids}
    elabel = {}
    for a, b, label in edges:
        if a in by_id and b in by_id:
            succ[a].append(b)
            pred[b].append(a)
            elabel[(a, b)] = label

    root = str(start) if start and str(start) in by_id else None
    if root is None:
        roots = [nid for nid in ids if not pred[nid]] or [ids[0]]
        if len(roots) != 1:
            return None
        root = roots[0]

    spine = []
    decisions = {}
    seen = set()
    cur = root
    guard = 0
    while cur is not None:
        guard += 1
        if guard > len(ids) + 1 or cur in seen:
            return None
        seen.add(cur)
        spine.append(cur)
        outs = succ.get(cur, [])
        if not outs:
            break
        if len(outs) == 1:
            cur = outs[0]
            continue
        if len(outs) != 2:
            return None

        # 菱形：两条分支各是一条直链，且终点必须是同一个汇合点（pred>=2 的节点）。
        info = []
        for child in outs:
            node = child
            chain = []
            local = set()
            ok = True
            while node is not None:
                if node in local or node in seen:
                    ok = False
                    break
                local.add(node)
                if len(pred[node]) >= 2:
                    break
                chain.append(node)
                outs2 = succ.get(node, [])
                if len(outs2) != 1:
                    node = None
                    break
                node = outs2[0]
            if not ok:
                return None
            info.append({"child": child, "label": elabel.get((cur, child)) or "",
                         "chain": chain, "merge": node})

        if info[0]["merge"] is None or info[0]["merge"] != info[1]["merge"]:
            return None
        merge = info[0]["merge"]
        if merge in seen:
            return None

        # 黑板上：F 在左、T 在右；模型要是给了「否/是」也按这个摆。
        def side(label):
            s = str(label or "").strip().lower()
            if s in ("f", "n", "否", "no", "0", "假", "false"):
                return "left"
            if s in ("t", "y", "是", "yes", "1", "真", "true"):
                return "right"
            return ""

        left, right = info[1], info[0]
        if side(left["label"]) == "right" and side(right["label"]) == "left":
            left, right = right, left
        decisions[cur] = {"left": left, "right": right, "merge": merge}
        cur = merge
    return {"by_id": by_id, "spine": spine, "decisions": decisions}


def _flow_side_svg(nodes, edges, start, has_title):
    """两侧分支布局；返回 (body, width, height) 或 None。"""
    st = _flow_structure(nodes, edges, start)
    if not st:
        return None
    by_id = st["by_id"]
    spine = st["spine"]
    decisions = st["decisions"]

    def w_of(nid):
        return _flow_node_width(by_id[nid])

    def h_of(nid):
        return _node_size(by_id[nid]["label"])[1]

    def draw_node(nid, x, y):
        n = by_id[nid]
        kind = _flow_kind(n)
        w, h = _node_size(n["label"])
        color = n.get("color")
        if kind in ("start", "end", "起", "止"):
            return _box(x, y, w + 16, h, n["label"], color or "green", shape="pill")
        if kind in ("decision", "判断", "条件"):
            return _diamond(x, y, w + 34, h + 18, n["label"], color)
        if _flow_io(kind):
            # 输入/输出：矩形（不用平行四边形）
            return _box(x, y, w + FLOW_IO_EXTRA, h, n["label"], color, shape="square")
        return _box(x, y, w, h, n["label"], color, shape="square")

    top = 50 if has_title else 12
    cursor = float(top)
    ypos = {}
    branch_pos = {}
    geoms = {}
    max_ext = 0.0

    for nid in spine:
        if nid in decisions:
            w, h = _node_size(by_id[nid]["label"])
            dw, dh = w + 34, h + 18
            cy = cursor + dh / 2
            ypos[nid] = cy
            dec = decisions[nid]
            left, right = dec["left"], dec["right"]

            def col_width(chain):
                return max([w_of(c) for c in chain] or [0])

            cw_l, cw_r = col_width(left["chain"]), col_width(right["chain"])
            branch_top = cy + dh / 2 + 22

            def place_chain(chain, sign, cw):
                if not chain or not cw:
                    return None
                col = sign * (dw / 2 + FLOW_HGAP + cw / 2)
                yy = branch_top
                for c in chain:
                    ch = h_of(c)
                    branch_pos[c] = (col, yy + ch / 2)
                    yy += ch + FLOW_BGAP
                return yy - FLOW_BGAP

            bottom_l = place_chain(left["chain"], -1, cw_l)
            bottom_r = place_chain(right["chain"], 1, cw_r)
            bottoms = [b for b in (bottom_l, bottom_r) if b is not None]
            bottom = max(bottoms + [cy + dh / 2])
            junction_y = bottom + FLOW_VGAP
            cursor = junction_y + FLOW_VGAP

            max_ext = max(max_ext, dw / 2)
            for chain, sign, cw in ((left["chain"], -1, cw_l), (right["chain"], 1, cw_r)):
                if cw:
                    max_ext = max(max_ext, dw / 2 + FLOW_HGAP + cw)
            geoms[nid] = {"cy": cy, "dw": dw, "dh": dh, "cw_l": cw_l, "cw_r": cw_r,
                          "branch_top": branch_top, "junction_y": junction_y}
        else:
            w, h = _node_size(by_id[nid]["label"])
            cy = cursor + h / 2
            ypos[nid] = cy
            cursor = cy + h / 2 + FLOW_VGAP
            max_ext = max(max_ext, w / 2)

    body = []

    def hline(x1, x2, y):
        body.append(f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" '
                    f'stroke="{LINE}" stroke-width="1.5"/>')

    # 主脊连线
    for i, nid in enumerate(spine[:-1]):
        nxt = spine[i + 1]
        if nxt not in ypos:
            continue
        y2 = ypos[nxt] - (geoms[nxt]["dh"] / 2 if nxt in geoms else h_of(nxt) / 2)
        if nid in geoms:
            y1 = geoms[nid]["junction_y"]
        else:
            y1 = ypos[nid] + h_of(nid) / 2
        body.append(_arrow(0, y1, 0, y2))

    # 菱形两边的分支
    for nid, dec in decisions.items():
        g = geoms[nid]
        cy, dw, dh = g["cy"], g["dw"], g["dh"]
        junction_y = g["junction_y"]
        for side_name, sign in (("left", -1), ("right", 1)):
            info = dec[side_name]
            label = info["label"]
            # F/T 标签：写在菱形顶点两侧
            if label:
                lx = sign * (dw / 2 + 8)
                body.append(
                    f'<text x="{lx:.1f}" y="{cy - 10:.1f}" font-size="12" fill="#5a6480" '
                    f'text-anchor="{"end" if sign < 0 else "start"}" '
                    f'font-family="system-ui, sans-serif">{_esc(label)}</text>')
            chain = info["chain"]
            sx = sign * dw / 2
            if not chain:
                # 空分支：从菱形侧面绕出去再回来，汇合到下方节点
                x_side = sign * (dw / 2 + FLOW_HGAP)
                hline(sx, x_side, cy)
                body.append(f'<line x1="{x_side:.1f}" y1="{cy:.1f}" x2="{x_side:.1f}" '
                            f'y2="{junction_y:.1f}" stroke="{LINE}" stroke-width="1.5"/>')
                hline(x_side, 0, junction_y)
                continue
            cw = g["cw_l"] if sign < 0 else g["cw_r"]
            col = sign * (dw / 2 + FLOW_HGAP + cw / 2)
            first = chain[0]
            first_top = branch_pos[first][1] - h_of(first) / 2
            hline(sx, col, cy)
            body.append(_arrow(col, cy, col, first_top))
            for a, b in zip(chain, chain[1:]):
                ay = branch_pos[a][1] + h_of(a) / 2
                by = branch_pos[b][1] - h_of(b) / 2
                body.append(_arrow(col, ay, col, by))
            last = chain[-1]
            last_bottom = branch_pos[last][1] + h_of(last) / 2
            body.append(f'<line x1="{col:.1f}" y1="{last_bottom:.1f}" x2="{col:.1f}" '
                        f'y2="{junction_y:.1f}" stroke="{LINE}" stroke-width="1.5"/>')
            hline(col, 0, junction_y)
        # 汇合点小圆点
        body.append(f'<circle cx="0" cy="{junction_y:.1f}" r="3" fill="{INK}"/>')

    for nid in spine:
        x, y = 0, ypos[nid]
        body.append(draw_node(nid, x, y))
    for c, (x, y) in branch_pos.items():
        body.append(draw_node(c, x, y))

    width = max(300.0, 2 * max_ext + 48)
    height = cursor + 24
    # 坐标是以主轴 x=0 为中心的，画布得整体平移到正区间，否则左侧会被裁掉
    shift = max_ext + 24
    return (f'<g transform="translate({shift:.1f},0)">' + "".join(body) + '</g>'), width, height


# ---------------- 流程图：结构化自绘排版 ----------------
# Graphviz 的通用布局画不出教材/黑板那种「主轴 + 左右分支 + 汇合 + 回边」的结构化样式，
# 这里自己解析 sequence / if-else / do-until 结构，全部用同一套画法。
_FV_GAP = 40        # 主流程竖向间距
_FV_HGAP = 36       # 判断到左右分支列的水平距离
_FV_BGAP = 20       # 分支块底部到汇合横线的距离
_FV_MGAP = 18       # 汇合横线到下一个框的距离
_FV_LOOP = 26       # 循环回边离外框的距离


class _FlowBad(Exception):
    pass


def _flow_node_box(nv, nid):
    n = nv["by_id"][nid]
    w, h = _node_size(n["label"])
    kind = _flow_kind(n)
    if kind in ("start", "end", "起", "止"):
        return w + 16, h
    if kind in ("decision", "判断", "条件"):
        return w + 34, h + 18
    if _flow_io(kind):
        return w + FLOW_IO_EXTRA, h
    return w, h


def _flow_custom_svg(data, has_title=False):
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    # 模型常塞「汇合/合并」这种辅助节点，分支用线自然汇合即可，这里直接去掉
    _merge_labels = ("汇合", "合并", "汇合点", "合并点", "merge", "join")
    _drop = set()
    for n in nodes:
        if str(n.get("label") or "").strip().lower() in _merge_labels \
                and _flow_kind(n) not in ("decision", "判断", "条件", "start", "end", "start/end"):
            _drop.add(n["id"])
    for mid in _drop:
        ins = [(a, lab) for a, b, lab in edges if b == mid]
        outs = [(b, lab) for a, b, lab in edges if a == mid]
        if ins and outs:
            edges = [e for e in edges if e[0] != mid and e[1] != mid]
            for a, lab in ins:
                for b, olab in outs:
                    edges.append((a, b, lab or olab))
    nodes = [n for n in nodes if n["id"] not in _drop]
    if not nodes:
        return ""
    ids = [n["id"] for n in nodes]
    by_id = {n["id"]: n for n in nodes}
    succ = {nid: [] for nid in ids}
    labels = {}
    for a, b, lab in edges:
        if a in by_id and b in by_id:
            succ[a].append(b)
            labels[(a, b)] = lab
    nv = {"by_id": by_id}

    # 回边（do-until / while 循环）
    color = {}
    back = set()

    def dfs(u):
        color[u] = 1
        for v in succ.get(u, []):
            c = color.get(v)
            if c == 1:
                back.add((u, v))
            elif c is None:
                dfs(v)
        color[u] = 2

    for nid in ids:
        if color.get(nid) is None:
            dfs(nid)

    fwd = {nid: [v for v in succ.get(nid, []) if (nid, v) not in back] for nid in ids}

    # 模型常把「汇合后的公共部分」复制成两份独立子图。这里按（节点名, 类型, 各出边标签及下游结构）
    # 完全相同的子图做合并；有回边的图不合并（避免把不同的循环误合成一个）。
    if not back:
        _sig_cache = {}

        def _sub_sig(nid, stack):
            if nid in _sig_cache:
                return _sig_cache[nid]
            if nid in stack:
                return ("cycle",)
            stack.add(nid)
            outs = tuple(sorted((labels.get((nid, c), ""), _sub_sig(c, stack))
                                for c in fwd.get(nid, [])))
            stack.discard(nid)
            sig = ((by_id[nid].get("kind") or "").strip().lower(),
                   by_id[nid]["label"], outs)
            _sig_cache[nid] = sig
            return sig

        _canon = {}
        _first = {}
        _keep = []
        for nid in ids:
            s = _sub_sig(nid, set())
            if s in _first:
                _canon[nid] = _first[s]
            else:
                _first[s] = nid
                _canon[nid] = nid
                _keep.append(nid)
        if len(_keep) < len(ids):
            ids = _keep
            by_id = {nid: by_id[nid] for nid in ids}
            nv["by_id"] = by_id
            _succ = {nid: [] for nid in ids}
            _labels = {}
            for a in ids:
                for b in succ.get(a, []):
                    cb = _canon.get(b, b)
                    if cb not in _succ[a]:
                        _succ[a].append(cb)
                    _labels[(a, cb)] = labels.get((a, b), "")
            succ = _succ
            labels = _labels
            fwd = {nid: list(succ[nid]) for nid in ids}
            if data.get("start") in _canon:
                data = dict(data)
                data["start"] = _canon[data["start"]]

    def reach(start, graph):
        seen = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in by_id:
                continue
            seen.add(cur)
            stack.extend(graph.get(cur, []))
        return seen

    rev = {nid: [] for nid in ids}
    for a in ids:
        for b in fwd[a]:
            rev[b].append(a)

    # 自然循环：从 latch 反向走（不经过 header）能到的节点都属于循环体；
    # 这样嵌套循环的外层能包含内层节点，内层也不会被外层回边带偏。
    rev_all = {nid: [] for nid in ids}
    for a in ids:
        for b in succ.get(a, []):
            rev_all[b].append(a)
    loops = {}
    for latch, header in back:
        in_loop = {latch}
        stack = [latch]
        while stack:
            u = stack.pop()
            for p in rev_all.get(u, []):
                if p == header or p in in_loop:
                    continue
                in_loop.add(p)
                stack.append(p)
        in_loop.add(header)
        if latch not in in_loop:
            return ""
        exits = []
        for u in in_loop:
            for v in succ.get(u, []):
                if v not in in_loop:
                    exits.append((u, v, labels.get((u, v), "")))
        if len(exits) != 1:
            return ""
        eu, ev, elab = exits[0]
        if header in loops:
            return ""
        loops[header] = {"latch": latch, "nodes": in_loop, "exit": ev,
                         "exit_label": elab, "back_label": labels.get((latch, header), ""),
                         "exit_from_latch": eu == latch}

    # 判断的汇合点：在去掉回边的 DAG 上做后支配
    dag = {nid: list(fwd[nid]) for nid in ids}
    for header, info in loops.items():
        if not dag[info["latch"]]:
            dag[info["latch"]].append(info["exit"])
    exits = [nid for nid in ids if not dag[nid]]
    if len(exits) != 1:
        return ""
    exit_node = exits[0]
    allset = set(ids)
    pdom = {nid: set(allset) for nid in ids}
    pdom[exit_node] = {exit_node}
    changed = True
    while changed:
        changed = False
        for nid in ids:
            if nid == exit_node:
                continue
            succs = dag[nid]
            if not succs:
                new = {nid}
            else:
                inter = set(allset)
                for s in succs:
                    inter &= pdom.get(s, {s})
                new = {nid} | inter
            if new != pdom[nid]:
                pdom[nid] = new
                changed = True
    ipdom = {}
    for nid in ids:
        cand = [m for m in pdom[nid] if m != nid]
        if cand:
            ipdom[nid] = max(cand, key=lambda m: len(pdom[m]))

    def size(nid):
        return _flow_node_box(nv, nid)

    def span(b):
        """块相对自身中心的左右占用，用于算画布宽度（左右可能不对称）。"""
        k = b["k"]
        if k == "S":
            w, _h = size(b["nid"])
            return w / 2, w / 2
        if k == "Seq":
            if not b["items"]:
                return 0.0, 0.0
            sp = [span(x) for x in b["items"]]
            return max(x[0] for x in sp), max(x[1] for x in sp)
        if k == "If":
            dw, _dh = size(b["dec"])
            lw, _lh = measure(b["left"])
            rw, _rh = measure(b["right"])
            return dw / 2 + _FV_HGAP + lw, dw / 2 + _FV_HGAP + rw
        if k == "LoopIf":
            dw, _dh = size(b["hd"])
            bw, _bh = measure(b["body"])
            return dw / 2, dw / 2 + _FV_HGAP + bw + _FV_LOOP + 12
        if k == "LoopSeq":
            hw, _hh = size(b["hd"])
            bw, _bh = measure(b["body"])
            lw, _lh = size(b["latch"]) if b["latch"] else (0, 0)
            return max(hw, bw, lw) / 2 + _FV_LOOP + 12, max(hw, bw, lw) / 2
        if k == "Case":
            w, _h = measure(b)
            return w / 2, w / 2
        raise _FlowBad()

    def draw_node(nid, cx, cy):
        n = by_id[nid]
        kind = _flow_kind(n)
        w, h = _flow_node_box(nv, nid)
        color = n.get("color")
        if kind in ("start", "end", "起", "止"):
            return _box(cx, cy, w, h, n["label"], color or "green", shape="pill")
        if kind in ("decision", "判断", "条件"):
            return _diamond(cx, cy, w, h, n["label"], color)
        return _box(cx, cy, w, h, n["label"], color, shape="square")

    # ---- 块 ----
    def measure(b):
        k = b["k"]
        if k == "S":
            return size(b["nid"])
        if k == "Seq":
            ws = [measure(x)[0] for x in b["items"]] or [0]
            hs = [measure(x)[1] for x in b["items"]]
            return max(ws), sum(hs) + _FV_GAP * max(0, len(hs) - 1)
        if k == "If":
            dw, dh = size(b["dec"])
            lw, lh = measure(b["left"])
            rw, rh = measure(b["right"])
            w = dw + 2 * _FV_HGAP + lw + rw
            h = dh + _FV_GAP + max(lh, rh) + _FV_BGAP + _FV_MGAP
            return w, h
        if k == "LoopIf":
            dw, dh = size(b["hd"])
            bw, bh = measure(b["body"])
            return dw + _FV_HGAP + bw + _FV_LOOP + 12, dh + _FV_GAP + bh + _FV_BGAP
        if k == "LoopSeq":
            hw, hh = size(b["hd"])
            bw, bh = measure(b["body"])
            lw, lh = size(b["latch"]) if b["latch"] else (0, 0)
            return max(hw, bw, lw) + _FV_LOOP + 24, hh + _FV_GAP + bh + _FV_BGAP + max(lh, 0)
        if k == "Case":
            dw, dh = size(b["dec"])
            ws = [measure(br["body"])[0] for br in b["branches"]] or [0]
            hs = [measure(br["body"])[1] for br in b["branches"]] or [0]
            colw = max(ws) + _FV_HGAP
            return max(dw, colw * len(b["branches"])), dh + _FV_GAP + max(hs) + _FV_BGAP + _FV_MGAP
        raise _FlowBad()

    def draw(b, cx, top, nvout):
        k = b["k"]
        if k == "S":
            w, h = size(b["nid"])
            nvout.append(draw_node(b["nid"], cx, top + h / 2))
            return top + h
        if k == "Seq":
            y = top
            for i, item in enumerate(b["items"]):
                if i:
                    y += _FV_GAP
                    nvout.append(_arrow(cx, y - _FV_GAP, cx, y))
                y = draw(item, cx, y, nvout)
            return y
        if k == "If":
            dw, dh = size(b["dec"])
            lw, lh = measure(b["left"])
            rw, rh = measure(b["right"])
            cy = top + dh / 2
            nvout.append(draw_node(b["dec"], cx, cy))
            lL, lR = span(b["left"])
            rL, rR = span(b["right"])
            lx = cx - (dw / 2 + _FV_HGAP) - lR
            rx = cx + (dw / 2 + _FV_HGAP) + rL
            by = top + dh + _FV_GAP
            left_empty = b["left"]["k"] == "Seq" and not b["left"]["items"]
            right_empty = b["right"]["k"] == "Seq" and not b["right"]["items"]
            end_l = by if left_empty else draw(b["left"], lx, by, nvout)
            end_r = by if right_empty else draw(b["right"], rx, by, nvout)
            yb = top + dh + _FV_GAP + max(lh, rh) + _FV_BGAP
            # 菱形到分支
            lc = labels.get((b["dec"], b["lchild"]), "") if b["lchild"] else ""
            rc = labels.get((b["dec"], b["rchild"]), "") if b["rchild"] else ""
            if not lc and not rc:
                lc, rc = "F", "T"
            elif not lc:
                lc = "F"
            elif not rc:
                rc = "T"
            # 菱形左右顶点到分支列顶部
            if not left_empty:
                nvout.append(_polyline([(cx - dw / 2, cy), (lx, cy), (lx, by)]))
                nvout.append(_arrow_head(lx, by, 0, 1))

            if lc:
                nvout.append(f'<text x="{cx - dw / 2 - 8:.1f}" y="{cy - 10:.1f}" font-size="12" fill="#5a6480" text-anchor="end" font-family="system-ui, sans-serif">{_esc(lc)}</text>')
            if not right_empty:
                nvout.append(_polyline([(cx + dw / 2, cy), (rx, cy), (rx, by)]))
                nvout.append(_arrow_head(rx, by, 0, 1))

            if rc:
                nvout.append(f'<text x="{cx + dw / 2 + 8:.1f}" y="{cy - 10:.1f}" font-size="12" fill="#5a6480" text-anchor="start" font-family="system-ui, sans-serif">{_esc(rc)}</text>')
            # 分支底部到汇合
            if left_empty:
                ex = cx - dw / 2 - _FV_HGAP / 2
                nvout.append(_polyline([(cx - dw / 2, cy), (ex, cy), (ex, yb), (cx, yb)]))
            else:
                nvout.append(_polyline([(lx, end_l), (lx, yb), (cx, yb)]))
            if right_empty:
                ex = cx + dw / 2 + _FV_HGAP / 2
                nvout.append(_polyline([(cx + dw / 2, cy), (ex, cy), (ex, yb), (cx, yb)]))
            else:
                nvout.append(_polyline([(rx, end_r), (rx, yb), (cx, yb)]))
            nvout.append(_arrow_head(cx, yb, 0, 1) if False else "")
            nvout.append(_arrow(cx, yb, cx, yb + _FV_MGAP))
            return yb + _FV_MGAP
        if k == "LoopIf":
            dw, dh = size(b["hd"])
            bw, bh = measure(b["body"])
            cy = top + dh / 2
            nvout.append(draw_node(b["hd"], cx, cy))
            bx = cx + dw / 2 + _FV_HGAP + bw / 2
            by = top + dh + _FV_GAP
            nvout.append(_polyline([(cx + dw / 2, cy), (bx, cy), (bx, by)]))
            nvout.append(_arrow_head(bx, by, 0, 1))
            _tl = b.get("inside_label") or "T"
            if _tl:
                nvout.append(f'<text x="{cx + dw / 2 + 8:.1f}" y="{cy - 10:.1f}" font-size="12" fill="#5a6480" text-anchor="start" font-family="system-ui, sans-serif">{_esc(_tl)}</text>')
            bend = draw(b["body"], bx, by, nvout)
            # 回边：从循环体底部中间向上，接到 header 上方入线（教材画法），不戳菱形侧面
            loop_x = cx + dw / 2 + _FV_HGAP + bw + _FV_LOOP
            join_y = top - _FV_GAP / 2
            if join_y < top:
                nvout.append(_polyline([(cx, join_y), (cx, top)]))
            nvout.append(_polyline([(bx, bend), (loop_x, bend),
                                    (loop_x, join_y), (cx, join_y)]))
            nvout.append(_arrow_head(cx, join_y, -1, 0))
            bl = b.get("back_label") or ""
            if bl:
                nvout.append(f'<text x="{loop_x + 6:.1f}" y="{cy + 12:.1f}" font-size="12" fill="#5a6480" font-family="system-ui, sans-serif">{_esc(bl)}</text>')
            # 出口：菱形底部一路向下（接下一个块）
            ey = by + bh + _FV_BGAP
            nvout.append(_arrow(cx, cy + dh / 2, cx, ey))
            el = b.get("exit_label") or "F"
            if el:
                nvout.append(f'<text x="{cx + 8:.1f}" y="{cy + dh / 2 + 16:.1f}" font-size="12" fill="#5a6480" font-family="system-ui, sans-serif">{_esc(el)}</text>')
            return ey
        if k == "LoopSeq":
            hw, hh = size(b["hd"])
            nvout.append(draw_node(b["hd"], cx, top + hh / 2))
            y = top + hh + _FV_GAP
            nvout.append(_arrow(cx, top + hh, cx, y))
            bw, _bh = measure(b["body"])
            y = draw(b["body"], cx, y, nvout)
            # latch 判断框（循环条件），带出口和回边
            latch = b["latch"]
            lw, lh = size(latch)
            ly = y + _FV_BGAP
            nvout.append(draw_node(latch, cx, ly))
            back_label = b.get("back_label") or ""
            exit_label = b.get("exit_label") or ""
            # 回边：从 latch 左侧出去，向上回到 header 左侧
            loop_x = cx - bw / 2 - _FV_LOOP
            # 回边要接到 header 上方那条入线（教材画法），不要直接戳在框侧面
            join_y = top - _FV_GAP / 2
            nvout.append(_polyline([(cx - lw / 2, ly), (loop_x, ly),
                                    (loop_x, join_y), (cx, join_y)]))
            if back_label:
                nvout.append(f'<text x="{loop_x - 8:.1f}" y="{ly - 8:.1f}" font-size="12" fill="#5a6480" text-anchor="end" font-family="system-ui, sans-serif">{_esc(back_label)}</text>')
            ey = ly + lh / 2 + _FV_BGAP
            nvout.append(_arrow(cx, ly + lh / 2, cx, ey))
            if exit_label:
                nvout.append(f'<text x="{cx + 8:.1f}" y="{ly + lh / 2 + 16:.1f}" font-size="12" fill="#5a6480" font-family="system-ui, sans-serif">{_esc(exit_label)}</text>')
            return ey
        if k == "Case":
            dw, dh = size(b["dec"])
            cy = top + dh / 2
            nvout.append(draw_node(b["dec"], cx, cy))
            branches = b["branches"]
            ws = [measure(br["body"])[0] for br in branches]
            colw = max(ws or [0]) + _FV_HGAP
            xs = [cx + (i - (len(branches) - 1) / 2) * colw
                  for i in range(len(branches))]
            by = top + dh + _FV_GAP
            bar_y = top + dh + _FV_GAP / 2
            nvout.append(_polyline([(cx, top + dh), (cx, bar_y)]))
            ends = []
            for x, br in zip(xs, branches):
                nvout.append(_polyline([(cx, bar_y), (x, bar_y), (x, by)]))
                nvout.append(_arrow_head(x, by, 0, 1))
                lab = br.get("label") or ""
                if lab:
                    nvout.append(f'<text x="{x:.1f}" y="{by - 6:.1f}" font-size="12" fill="#5a6480" text-anchor="middle" font-family="system-ui, sans-serif">{_esc(lab)}</text>')
                ends.append(draw(br["body"], x, by, nvout))
            yb = (max(ends) if ends else by) + _FV_BGAP
            for x, ey in zip(xs, ends):
                nvout.append(_polyline([(x, ey), (x, yb), (cx, yb)]))
            nvout.append(_arrow(cx, yb, cx, yb + _FV_MGAP))
            return yb + _FV_MGAP
        raise _FlowBad()

    def _reaches(src, dst):
        seen = set()
        stack = [src]
        while stack:
            cur = stack.pop()
            if cur == dst:
                return True
            if cur in seen or cur not in by_id:
                continue
            seen.add(cur)
            stack.extend(fwd.get(cur, []))
        return False

    # ---- 解析结构 ----
    def parse(start, stop):
        items = []
        cur = start
        guard = 0
        while cur is not None and cur != stop:
            guard += 1
            if guard > len(ids) + 2 or cur not in by_id:
                raise _FlowBad()
            if cur in loops:
                info = loops[cur]
                if _flow_kind(by_id[cur]) in ("decision", "判断", "条件"):
                    inside = [v for v in fwd[cur] if v in info["nodes"]]
                    if len(inside) != 1:
                        raise _FlowBad()
                    body = parse(inside[0], info["latch"])
                    body = {"k": "Seq", "items": body["items"] + [{"k": "S", "nid": info["latch"]}]}
                    items.append({"k": "LoopIf", "hd": cur, "latch": info["latch"],
                                  "body": body, "back_label": info["back_label"],
                                  "exit_label": info["exit_label"],
                                  "inside_label": labels.get((cur, inside[0]), "")})
                else:
                    body = parse(fwd[cur][0], info["latch"]) if fwd[cur] else {"k": "Seq", "items": []}
                    items.append({"k": "LoopSeq", "hd": cur, "body": body,
                                  "latch": info["latch"],
                                  "back_label": info["back_label"],
                                  "exit_label": info["exit_label"]})
                cur = info["exit"]
                continue
            outs = fwd[cur]
            if not outs:
                items.append({"k": "S", "nid": cur})
                cur = None
                break
            if len(outs) == 1:
                items.append({"k": "S", "nid": cur})
                cur = outs[0]
                continue
            # 判断（支持任意多分支：if-else / case / switch）
            M = ipdom.get(cur)
            if stop is not None and (M is None or M == cur or not _reaches(M, stop)):
                M = stop
            if M is None or M == cur:
                raise _FlowBad()
            if len(outs) > 2:
                items.append({"k": "Case", "dec": cur, "branches": [
                    {"nid": v, "label": labels.get((cur, v), ""),
                     "body": parse(v, M)} for v in outs]})
                cur = M
                continue
            a, b = outs
            la, lb = labels.get((cur, a), ""), labels.get((cur, b), "")
            def is_f(x):
                return str(x or "").strip().upper() in ("F", "否", "N", "FALSE")
            if is_f(la) and not is_f(lb):
                left_child, right_child = a, b
            else:
                left_child, right_child = b, a
            items.append({"k": "If", "dec": cur, "lchild": left_child,
                          "rchild": right_child, "left": parse(left_child, M),
                          "right": parse(right_child, M)})
            cur = M
        return {"k": "Seq", "items": items}

    try:
        root = str(data.get("start")) if data.get("start") and str(data.get("start")) in by_id else None
        if root is None:
            roots = [nid for nid in ids if not any(a in by_id and b == nid for a in ids for b in fwd[a])]
            root = roots[0] if len(roots) == 1 else ids[0]
        b = parse(root, None)
        w, h = measure(b)
        left, right = span(b)
        out = []
        top = 46 if has_title else 16
        draw(b, left + 20, top, out)
        return _canvas(left + right + 40, top + h + 24, "".join(out), "")
    except _FlowBad:
        return ""
    except Exception:      # noqa: BLE001
        return ""


# ---------------- 判定表（教材图 5-7 / 表 5-2 格式） ----------------
def _dt_rows(raw):
    rows = []
    groups = []

    def add_group(grp, labels):
        labels = [str(x) for x in labels]
        seen = set()
        labels = [x for x in labels if not (x in seen or seen.add(x))]
        if groups and grp and groups[-1][0] == grp:
            g, start, count = groups[-1]
            existing = {rows[i]["label"] for i in range(start, start + count)}
            labels = [x for x in labels if x not in existing]
            if not labels:
                return
            for label in labels:
                rows.append({"group": grp, "label": label})
            groups[-1] = (grp, start, count + len(labels))
            return
        start = len(rows)
        for label in labels:
            rows.append({"group": grp, "label": label})
        groups.append((grp, start, len(labels)))

    for item in raw or []:
        if isinstance(item, dict):
            grp = str(item.get("group") or item.get("组") or "")
            items = item.get("items") or item.get("列表")
            if items:
                add_group(grp, items)
            else:
                _label = str(item.get("label") or item.get("name") or "").strip()
                if not grp and _label:
                    _pos = next((k for k, ch in enumerate(_label) if ch.isdigit()), -1)
                    _prefix = _label[:_pos].strip() if _pos > 0 else ""
                    if _prefix:
                        grp = _prefix
                        _label = _label[_pos:].strip()
                add_group(grp, [_label])
        elif isinstance(item, (list, tuple)):
            add_group("", list(item))
        else:
            add_group("", [item])
    return rows, groups


def draw_dtable(data, title=""):
    """判定表：条件/动作分组、Y/N/—、动作打 √，按教材表 5-2 的样式画。"""
    conds, cgroups = _dt_rows(data.get("conditions") or data.get("条件"))
    acts, agroups = _dt_rows(data.get("actions") or data.get("动作"))
    rules = data.get("rules") or data.get("规则") or []
    if not conds or not acts or not rules:
        raise ValueError("判定表至少要给 conditions / actions / rules")

    vals = []
    execs = []
    for i, rule in enumerate(rules):
        if isinstance(rule, dict):
            rv = rule.get("values") or rule.get("conditions") or rule.get("条件值") or []
            ra = rule.get("actions") or rule.get("动作") or []
        else:
            rv = list(rule or [])
            ra = []
        _row = [str(x).strip() for x in rv][:len(conds)]
        if len(_row) < len(conds):
            _row += ["-"] * (len(conds) - len(_row))
        vals.append(_row)
        chosen = set()
        if isinstance(ra, dict):
            ra = list(ra.values())
        for item in ra if isinstance(ra, (list, tuple, set)) else [ra]:
            if isinstance(item, bool):
                if item:
                    chosen.add(len(chosen))
            elif isinstance(item, int):
                chosen.add(item)
            else:
                text = str(item).strip()
                for j, row in enumerate(acts):
                    if row["label"] == text:
                        chosen.add(j)
                if text.isdigit():
                    chosen.add(int(text))
        if not ra and isinstance(rule, dict):
            on = rule.get("on") or rule.get("执行")
            if isinstance(on, dict):
                for k, v in on.items():
                    if v:
                        for j, row in enumerate(acts):
                            if row["label"] == str(k):
                                chosen.add(j)
        execs.append(chosen)

    # 多选一的条件组：组里已经有一个 Y 时，其余列按 N 渲染（模型常把 N 误写成 -）
    for _vals in vals:
        for _grp, _start, _count in cgroups:
            if _count < 2:
                continue
            _seg = _vals[_start:_start + _count]
            if sum(1 for v in _seg if v == "Y") == 1:
                for _k in range(_start, _start + _count):
                    if _vals[_k] != "Y":
                        _vals[_k] = "N"

    cond_col = 46
    group_w = max(72, max((_label_len(g) for g, _, _ in cgroups + agroups), default=60) + 16)
    item_w = max(120, max(_label_len(r["label"]) for r in conds + acts) + 20)
    rule_w = max(36, max(len(str(i + 1)) for i in range(len(rules))) * 10 + 16)
    x1 = cond_col
    x2 = x1 + group_w
    x3 = x2 + item_w
    total_w = x3 + rule_w * len(rules)
    head1 = 32
    head2 = 30
    row_h = 34
    cond_h = row_h * len(conds)
    act_h = row_h * len(acts)
    yc = head1 + head2
    ya = yc + cond_h
    total_h = ya + act_h

    out = []
    out.append(_ink_rect(0, 0, total_w, total_h))
    # 表头
    out.append(_ink_rect(0, 0, x3, yc))
    out.append(_fit_text(x3 / 2, yc / 2, ["选项"], x3 - 12))
    out.append(_ink_rect(x3, 0, total_w - x3, head1))
    out.append(_fit_text((x3 + total_w) / 2, head1 / 2, ["规则"], total_w - x3 - 12))
    for j in range(len(rules)):
        rx = x3 + j * rule_w
        out.append(_ink_rect(rx, head1, rule_w, head2))
        out.append(_fit_text(rx + rule_w / 2, head1 + head2 / 2, [str(j + 1)], rule_w - 6))
    # 竖线
    out.append(_ink_line(x1, yc, x1, total_h))
    out.append(_ink_line(x2, yc, x2, total_h))
    out.append(_ink_line(x3, 0, x3, total_h))
    for j in range(1, len(rules)):
        out.append(_ink_line(x3 + j * rule_w, head1, x3 + j * rule_w, total_h))
    # 条件
    out.append(_fit_text(x1 / 2, yc + cond_h / 2, ["条件"], x1 - 8))
    for grp, start, count in cgroups:
        gy = yc + start * row_h
        out.append(_ink_rect(x1, gy, group_w, count * row_h))
        out.append(_fit_text(x1 + group_w / 2, gy + count * row_h / 2,
                             _wrap_text(grp, 10), group_w - 10))
    for i, row in enumerate(conds):
        ry = yc + i * row_h
        out.append(_ink_rect(x2, ry, item_w, row_h))
        out.append(_fit_text(x2 + item_w / 2, ry + row_h / 2,
                             _wrap_text(row["label"], 18), item_w - 10))
        if i:
            out.append(_ink_line(x2, ry, total_w, ry))
        for j in range(len(rules)):
            rx = x3 + j * rule_w
            value = vals[j][i] if i < len(vals[j]) else ""
            value = {"—": "-", "–": "-", "－": "-"}.get(value, value)
            out.append(_fit_text(rx + rule_w / 2, ry + row_h / 2, [value], rule_w - 6,
                                 bold=True))
    # 动作
    out.append(_ink_line(0, yc, total_w, yc))
    out.append(_fit_text(x1 / 2, ya + act_h / 2, ["动作"], x1 - 8))
    for grp, start, count in agroups:
        gy = ya + start * row_h
        out.append(_ink_rect(x1, gy, group_w, count * row_h))
        out.append(_fit_text(x1 + group_w / 2, gy + count * row_h / 2,
                             _wrap_text(grp, 10), group_w - 10))
    for i, row in enumerate(acts):
        ry = ya + i * row_h
        out.append(_ink_rect(x2, ry, item_w, row_h))
        out.append(_fit_text(x2 + item_w / 2, ry + row_h / 2,
                             _wrap_text(row["label"], 18), item_w - 10))
        if i:
            out.append(_ink_line(x2, ry, total_w, ry))
        for j in range(len(rules)):
            if i in execs[j]:
                rx = x3 + j * rule_w
                out.append(_fit_text(rx + rule_w / 2, ry + row_h / 2, ["√"], rule_w - 6,
                                     bold=True))
    out.append(_ink_line(0, ya, total_w, ya))
    legend_lines = []
    if 0 < len(rules) <= 12:
        for j in range(len(rules)):
            parts = []
            for _grp, start, count in cgroups:
                seg = vals[j][start:start + count]
                picked = [conds[i]["label"] for i in range(start, start + count)
                          if i < len(vals[j]) and vals[j][i] == "Y"]
                if picked:
                    parts.append("+".join(picked))
                elif seg and all(v == "-" for v in seg):
                    parts.append("（不关心）")
                else:
                    _ns = [conds[i]["label"] for i in range(start, start + count)
                           if i < len(vals[j]) and vals[j][i] == "N"]
                    parts.append("、".join(_ns) + "不成立" if _ns else "（条件缺失）")
            done = [acts[i]["label"] for i in sorted(execs[j]) if i < len(acts)]
            legend_lines.append(
                "规则 %d：%s → %s" % (j + 1, "；".join(parts),
                                    "、".join(done) if done else "（无动作）"))
    legend_h = len(legend_lines) * 18 + (10 if legend_lines else 0)
    note = str(data.get("note") or data.get("description") or data.get("说明") or "").strip()
    extra = 0
    body = "".join(out)
    if note:
        note_lines = _wrap_text(note, 42)
        extra = len(note_lines) * 20 + 14
        note_svg = []
        for i, line in enumerate(note_lines):
            note_svg.append(_fit_text(total_w / 2, 10 + i * 20 + 9, [line], total_w - 20))
        body = "".join(note_svg) + f'<g transform="translate(0,{extra})">' + body + "</g>"
    if legend_lines:
        legend_svg = []
        for i, line in enumerate(legend_lines):
            legend_svg.append(
                f'<text x="10" y="{extra + total_h + 12 + i * 18:.1f}" '
                f'font-size="12" fill="#111111" font-family="system-ui, sans-serif">'
                f'{_esc(line)}</text>')
        body = body + "".join(legend_svg)
    return _sheet(total_w, total_h + extra + legend_h, body, title)


def draw_flow(data, title=""):
    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    if not nodes:
        raise ValueError("nodes 是空的，至少给一个节点")

    side = _flow_side_svg(nodes, edges, data.get("start"), bool(title))
    if side is not None:
        sbody, sw, sh = side
        return _canvas(max(sw, 300), max(sh, 220), sbody, title)
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
        elif kind in ("io", "input", "output", "输入", "输出", "输入输出", "inout"):
            # 输入/输出：矩形（不再画平行四边形）
            body.append(_box(x, y, w + 18, h, n["label"], n.get("color"),
                            shape="square"))
        else:
            # 标准流程图：处理框是直角矩形（开始/结束才用圆角 pill）
            body.append(_box(x, y, w, h, n["label"], n.get("color"), shape="square"))
    return _canvas(max(width, 300), max(height, 220), "".join(body), title)


# ---------------- 2b. 盒图（N-S 图） / PAD（问题分析图） ----------------

NS_HEAD = 64        # 选择 / 分支的条件带高度（F/T 画在带内的左右三角形里）
NS_ROW = 34         # 多分支的「值」那一行
NS_COND = 34        # （保留）
NS_LOOP_PAD = 34    # 循环体旁边留的回路条宽度
NS_LOOP_COND = 42   # while / until 的条件带高度

PAD_BOX = 34        # PAD 单个处理框高度
PAD_GAP = 9         # PAD 顺序结构之间的竖向间距
PAD_HGAP = 30       # PAD 主线和右侧分支之间的距离


def _wrap_text(label, per_line=20):
    """按字数拆行（中文 14px、英文 8px 大致对齐教材的窄框）。"""
    label = str(label if label is not None else "").strip()
    if not label:
        return [""]
    lines = []
    for para in label.split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
            continue
        for i in range(0, len(para), per_line):
            lines.append(para[i:i + per_line])
    return lines or [""]


def _lines_w(lines):
    return max(_label_len(line) for line in lines)


def _lines_h(lines):
    return max(34, len(lines) * 18 + 10)


def _fit_text(cx, cy, lines, w, color=INK, bold=False):
    """居中画多行；单行超宽就用 textLength 压进格子，避免溢出。"""
    n = len(lines)
    first = cy - (n - 1) * 9 + 4.5
    out = []
    for i, line in enumerate(lines):
        extra = ""
        if _label_len(line) > max(20.0, w - 10):
            extra = (f' textLength="{max(20.0, w - 10):.1f}" '
                     f'lengthAdjust="spacingAndGlyphs"')
        weight = "600" if bold else "400"
        out.append(
            f'<text x="{cx:.1f}" y="{first + i * 18:.1f}" font-size="{FONT}" '
            f'font-weight="{weight}" fill="{color}" text-anchor="middle" '
            f'font-family="system-ui, -apple-system, Segoe UI, PingFang SC, '
            f'Microsoft YaHei, sans-serif"{extra}>{_esc(line)}</text>')
    return "".join(out)


def _sheet(w, h, body, title, margin=26):
    top = 46 if title else margin
    cw, ch = w + margin * 2, top + h + margin
    return _canvas(cw, ch,
                   f'<rect x="0" y="0" width="{cw:.0f}" height="{ch:.0f}" '
                   f'fill="#ffffff"/>'
                   f'<g transform="translate({margin},{top})">{body}</g>', title)


def _ink_rect(x, y, w, h):
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'fill="#ffffff" stroke="{INK}" stroke-width="1.5"/>')


def _ink_line(x1, y1, x2, y2):
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{INK}" stroke-width="1.3"/>')


def _ink_text(x, y, text, color=INK, bold=False, anchor="start"):
    weight = "600" if bold else "400"
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{FONT}" font-weight="{weight}" '
            f'fill="{color}" text-anchor="{anchor}" font-family="system-ui, sans-serif">'
            f'{_esc(text)}</text>')


# ---------------- 盒图（N-S 图） ----------------

def _ns_steps(data):
    steps = data.get("steps")
    if steps is None:
        steps = data.get("root")
    return steps or []


def _ns_node(step):
    if isinstance(step, dict):
        return step
    return {"kind": "process", "label": str(step)}


def _ns_measure_seq(steps):
    w, h = 0, 0
    for step in steps or []:
        sw, sh = _ns_measure(_ns_node(step))
        w = max(w, sw)
        h += sh
    return w, h


def _ns_measure(node):
    kind = str(node.get("kind") or "process").strip().lower()
    if kind in ("if", "choice", "select", "选择", "判断", "分支"):
        lw, lh = _ns_measure_seq(node.get("else") or node.get("false") or [])
        rw, rh = _ns_measure_seq(node.get("then") or node.get("true") or [])
        bw = max(lw, rw, 100)
        return max(210, bw * 2), NS_HEAD + max(lh, rh)
    if kind in ("case", "multi", "多分支"):
        branches = node.get("branches") or []
        sizes = [_ns_measure_seq(b.get("body") or []) for b in branches]
        bw = max([s[0] for s in sizes] + [90])
        bh = max([s[1] for s in sizes] or [34])
        return max(230, bw * max(1, len(branches))), NS_HEAD + NS_ROW + bh
    if kind in ("while", "当型", "当型循环"):
        cw = max(80, _label_len(node.get("condition") or "") + 24)
        bw, bh = _ns_measure_seq(node.get("body") or [])
        return NS_LOOP_PAD + max(bw, cw, 120), NS_LOOP_COND + max(bh, 34)
    if kind in ("until", "直到型", "直到型循环"):
        cw = max(80, _label_len(node.get("condition") or "") + 24)
        bw, bh = _ns_measure_seq(node.get("body") or [])
        return NS_LOOP_PAD + max(bw, cw, 120), max(bh, 34) + NS_LOOP_COND
    if kind in ("ref", "nested", "引用", "嵌套", "子图"):
        return max(90, _label_len(node.get("label") or "") + 40), 42
    lines = _wrap_text(node.get("label"))
    return max(100, min(440, _lines_w(lines) + 20)), _lines_h(lines)

def _ns_draw_seq(steps, x, y, w, out):
    yy = y
    for step in steps or []:
        node = _ns_node(step)
        _, sh = _ns_measure(node)
        _ns_draw(node, x, yy, w, sh, out)
        yy += sh
    return yy - y


def _ns_draw(node, x, y, w, h, out):
    kind = str(node.get("kind") or "process").strip().lower()
    if kind in ("if", "choice", "select", "选择", "判断", "分支"):
        out.append(_ink_rect(x, y, w, h))
        half = w / 2
        sep = y + NS_HEAD
        out.append(_ink_line(x, sep, x + w, sep))
        out.append(f'<polygon points="{x:.1f},{y:.1f} {x + w:.1f},{y:.1f} '
                   f'{x + half:.1f},{sep:.1f}" fill="none" stroke="{INK}" '
                   f'stroke-width="1.5"/>')
        out.append(_fit_text(x + half, y + 22,
                             _wrap_text(node.get("condition") or "", 24), w - 30))
        out.append(_fit_text(x + w * 0.16, sep - 14, ["F"], w * 0.30,
                             color=INK, bold=True))
        out.append(_fit_text(x + w * 0.84, sep - 14, ["T"], w * 0.30,
                             color=INK, bold=True))
        out.append(_ink_line(x + half, sep, x + half, y + h))
        _ns_draw_seq(node.get("else") or node.get("false") or [], x, sep, half, out)
        _ns_draw_seq(node.get("then") or node.get("true") or [], x + half, sep, half, out)
        return
    if kind in ("case", "multi", "多分支"):
        branches = node.get("branches") or []
        out.append(_ink_rect(x, y, w, h))
        sep = y + NS_HEAD
        out.append(_ink_line(x, sep, x + w, sep))
        out.append(f'<polygon points="{x:.1f},{y:.1f} {x + w:.1f},{y:.1f} '
                   f'{x + 2 * w / 3:.1f},{sep:.1f} {x + w / 3:.1f},{sep:.1f}" '
                   f'fill="none" stroke="{INK}" stroke-width="1.5"/>')
        out.append(_fit_text(x + w / 2, y + 22,
                             _wrap_text(node.get("condition") or "", 24), w - 24))
        n = max(1, len(branches))
        col = w / n
        by = sep + NS_ROW
        out.append(_ink_line(x, by, x + w, by))
        for i, branch in enumerate(branches):
            cx = x + col * i
            value = str(branch.get("value") or branch.get("label") or (i + 1))
            out.append(_fit_text(cx + col / 2, sep + NS_ROW / 2, [value], col,
                                 color=INK, bold=True))
            if i:
                out.append(_ink_line(cx, sep, cx, y + h))
            _ns_draw_seq(branch.get("body") or [], cx, by, col, out)
        return
    if kind in ("while", "当型", "当型循环"):
        m = NS_LOOP_PAD
        body_top = y + NS_LOOP_COND
        out.append(_ink_rect(x, y, w, h))
        out.append(_ink_line(x + m, body_top, x + w, body_top))
        out.append(_ink_line(x + m, body_top, x + m, y + h))
        out.append(_fit_text(x + m + (w - m) / 2, y + NS_LOOP_COND / 2,
                             _wrap_text(node.get("condition") or "", 18), w - m - 12))
        _ns_draw_seq(node.get("body") or [], x + m, body_top, w - m, out)
        return
    if kind in ("until", "直到型", "直到型循环"):
        m = NS_LOOP_PAD
        body_bottom = y + h - NS_LOOP_COND
        x2 = x + w - m
        out.append(_ink_rect(x, y, w, h))
        out.append(_ink_line(x2, y, x2, body_bottom))
        out.append(_ink_line(x, body_bottom, x2, body_bottom))
        _ns_draw_seq(node.get("body") or [], x, y, w - m, out)
        out.append(_fit_text(x + (w - m) / 2, body_bottom + NS_LOOP_COND / 2,
                             _wrap_text(node.get("condition") or "", 18), w - m - 12))
        return
    if kind in ("ref", "nested", "引用", "嵌套", "子图"):
        out.append(f'<ellipse cx="{x + w / 2:.1f}" cy="{y + h / 2:.1f}" '
                   f'rx="{w / 2 - 3:.1f}" ry="{h / 2 - 4:.1f}" fill="#ffffff" '
                   f'stroke="{INK}" stroke-width="1.5"/>')
        out.append(_fit_text(x + w / 2, y + h / 2,
                             _wrap_text(node.get("label") or "", 16), w - 16))
        return
    lines = _wrap_text(node.get("label"))
    out.append(_ink_rect(x, y, w, h))
    out.append(_fit_text(x + w / 2, y + h / 2, lines, w - 10))

def draw_ns(data, title=""):
    steps = _ns_steps(data)
    if not steps:
        raise ValueError("steps 是空的，至少给一个节点")
    w, h = _ns_measure_seq(steps)
    w = max(w, 120)
    out = []
    _ns_draw_seq(steps, 0, 0, w, out)
    return _sheet(w, h, "".join(out), title)


# ---------------- PAD（问题分析图） ----------------

def _pad_steps(data):
    steps = data.get("steps")
    if steps is None:
        steps = data.get("root")
    return steps or []


def _pad_node(step):
    if isinstance(step, dict):
        return step
    return {"kind": "process", "label": str(step)}


def _pad_cond_size(node):
    lines = _wrap_text(node.get("condition") or "", 18)
    return max(96, min(300, _lines_w(lines) + 20)), _lines_h(lines)


def _pad_measure_seq(steps):
    w, h = 0, 0
    first = True
    for step in steps or []:
        sw, sh = _pad_measure(_pad_node(step))
        w = max(w, sw)
        h = sh if first else h + PAD_GAP + sh
        first = False
    return w, max(h, PAD_BOX)


def _pad_measure(node):
    kind = str(node.get("kind") or "process").strip().lower()
    if kind in ("if", "choice", "select", "选择", "判断", "分支"):
        tw, th = _pad_measure_seq(node.get("then") or node.get("true") or [])
        fw, fh = _pad_measure_seq(node.get("else") or node.get("false") or [])
        cw, ch = _pad_cond_size(node)
        return cw + PAD_HGAP + max(tw, fw), th + PAD_GAP + ch + PAD_GAP + fh
    if kind in ("case", "multi", "多分支"):
        branches = node.get("branches") or []
        values = [str(b.get("value") or b.get("label") or (i + 1))
                  for i, b in enumerate(branches)]
        lw = max([_label_len(v) for v in values] + [24])
        bx = lw + PAD_HGAP
        bw, bh = 0, 0
        first = True
        for b in branches:
            sw, sh = _pad_measure_seq(b.get("body") or [])
            bw = max(bw, sw)
            bh = sh if first else bh + PAD_GAP + sh
            first = False
        return bx + max(bw, 80), max(bh, PAD_BOX)
    if kind in ("while", "当型", "当型循环", "until", "直到型", "直到型循环"):
        cw, ch = _pad_cond_size(node)
        bw, bh = _pad_measure_seq(node.get("body") or [])
        return cw + PAD_HGAP + bw, max(ch, bh)
    if kind in ("def", "refine", "细化", "定义"):
        lines = _wrap_text(node.get("label") or "")
        aw = max(96, min(300, _lines_w(lines) + 20))
        bw, bh = _pad_measure_seq(node.get("body") or [])
        return aw + 44 + bw, max(_lines_h(lines), bh)
    lines = _wrap_text(node.get("label"))
    return max(96, min(300, _lines_w(lines) + 20)), _lines_h(lines)


def _pad_box(x, y, lines, w=None):
    if w is None:
        w = max(96, min(300, _lines_w(lines) + 20))
    h = _lines_h(lines)
    return _ink_rect(x, y, w, h) + _fit_text(x + w / 2, y + h / 2, lines, w - 10), w, h


def _pad_draw_seq(steps, x, y, out):
    yy = y
    w = 0
    first = True
    for step in steps or []:
        if not first:
            yy += PAD_GAP
        sw, sh = _pad_draw(_pad_node(step), x, yy, out)
        w = max(w, sw)
        yy += sh
        first = False
    return w, yy - y


def _pad_draw(node, x, y, out):
    kind = str(node.get("kind") or "process").strip().lower()
    if kind in ("if", "choice", "select", "选择", "判断", "分支"):
        tw, th = _pad_measure_seq(node.get("then") or node.get("true") or [])
        fw, fh = _pad_measure_seq(node.get("else") or node.get("false") or [])
        cw, ch = _pad_cond_size(node)
        bx = x + cw + PAD_HGAP
        _pad_draw_seq(node.get("then") or node.get("true") or [], bx, y, out)
        cy = y + th + PAD_GAP
        lines = _wrap_text(node.get("condition") or "", 18)
        out.append(_ink_rect(x, cy, cw, ch))
        out.append(_fit_text(x + cw / 2, cy + ch / 2, lines, cw - 10))
        fy = cy + ch + PAD_GAP
        _pad_draw_seq(node.get("else") or node.get("false") or [], bx, fy, out)
        total_h = th + PAD_GAP + ch + PAD_GAP + fh
        out.append(_ink_line(x, y, x, y + total_h))
        out.append(_ink_line(x + cw, cy + ch / 2, bx, y + th))
        out.append(_ink_line(x + cw, cy + ch / 2, bx, fy))
        out.append(_ink_text(x + cw + PAD_HGAP * 0.4,
                             (cy + ch / 2 + y + th) / 2 - 5, "T",
                             color="#555a6b", bold=True, anchor="middle"))
        out.append(_ink_text(x + cw + PAD_HGAP * 0.4,
                             (cy + ch / 2 + fy) / 2 + 15, "F",
                             color="#555a6b", bold=True, anchor="middle"))
        return cw + PAD_HGAP + max(tw, fw), total_h
    if kind in ("case", "multi", "多分支"):
        branches = node.get("branches") or []
        values = [str(b.get("value") or b.get("label") or (i + 1))
                  for i, b in enumerate(branches)]
        lw = max([_label_len(v) for v in values] + [24])
        bx = x + lw + PAD_HGAP
        yy = y
        w = bx
        for i, b in enumerate(branches):
            bw, bh = _pad_draw_seq(b.get("body") or [], bx, yy, out)
            ccy = yy + min(PAD_BOX, bh) / 2
            out.append(_ink_line(x, ccy, bx, ccy))
            out.append(_ink_text(x + 4, ccy - 5, values[i]))
            yy += bh + PAD_GAP
            w = max(w, bx + bw)
        total_h = max(PAD_BOX, yy - y - PAD_GAP)
        out.append(_ink_line(x, y, x, y + total_h))
        return w, total_h
    if kind in ("while", "当型", "当型循环", "until", "直到型", "直到型循环"):
        cw, ch = _pad_cond_size(node)
        bw, bh = _pad_measure_seq(node.get("body") or [])
        bx = x + cw + PAD_HGAP
        _pad_draw_seq(node.get("body") or [], bx, y, out)
        h = max(ch, bh)
        cy = y + (h - ch) / 2
        lines = _wrap_text(node.get("condition") or "", 18)
        out.append(_ink_rect(x, cy, cw, ch))
        out.append(_fit_text(x + cw / 2, cy + ch / 2, lines, cw - 10))
        out.append(_ink_line(x, y, x, y + h))
        out.append(_ink_line(x + cw, cy + ch / 2, bx, cy + ch / 2))
        return cw + PAD_HGAP + bw, h
    if kind in ("def", "refine", "细化", "定义"):
        lines = _wrap_text(node.get("label") or "")
        aw = max(96, min(300, _lines_w(lines) + 20))
        ah = _lines_h(lines)
        bx = x + aw + 44
        bw, bh = _pad_draw_seq(node.get("body") or [], bx, y, out)
        out.append(_ink_rect(x, y, aw, ah))
        out.append(_fit_text(x + aw / 2, y + ah / 2, lines, aw - 10))
        out.append(_ink_line(x + aw, y + ah / 2, bx, y + min(PAD_BOX, bh) / 2))
        out.append(_ink_text(x + aw + 6, y + ah / 2 - 4, "def", color="#555a6b"))
        return bx + bw, max(ah, bh)
    lines = _wrap_text(node.get("label"))
    w = max(96, min(300, _lines_w(lines) + 20))
    out.append(_ink_rect(x, y, w, _lines_h(lines)))
    out.append(_fit_text(x + w / 2, y + _lines_h(lines) / 2, lines, w - 10))
    return w, _lines_h(lines)


def draw_pad(data, title=""):
    steps = _pad_steps(data)
    if not steps:
        raise ValueError("steps 是空的，至少给一个节点")
    w, h = _pad_measure_seq(steps)
    w = max(w, 140)
    out = []
    _pad_draw_seq(steps, 0, 0, out)
    return _sheet(w, h, "".join(out), title)


# ---------------- 2c. 流程图：直角正交排版 ----------------

FLOW_PX = 72.0


def _wrap_px(label, max_w):
    """按像素宽度拆行：英文单词、数字、运算符不拆开，中文按字拆。"""
    text = str(label if label is not None else "")
    if not text:
        return [""]
    lines = []
    for para in text.split("\n"):
        tokens = re.findall(r"[A-Za-z0-9_.]+|[<>!=+\-*/&|^~%]+|[^\x00-\x7f\s]|\s+", para)
        cur = ""
        for tok in tokens:
            if not tok:
                continue
            if tok.isspace():
                if cur:
                    cur += " "
                continue
            if _label_len(cur + tok) > max(24.0, max_w) and cur.strip():
                lines.append(cur.rstrip())
                cur = tok
            else:
                cur += tok
        lines.append(cur if cur else "")
    return lines or [""]


def _dot_plain(dot_src, timeout=15):
    if not _DOT_BIN:
        return ""
    try:
        proc = subprocess.run([_DOT_BIN, "-Tplain"], input=dot_src.encode("utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=timeout)
    except Exception:               # noqa: BLE001
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode("utf-8", "replace")


def _polyline(points, color=LINE):
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return (f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="1.4" stroke-linejoin="round"/>')


def _arrow_head(x, y, dx, dy, color=LINE):
    ang = math.atan2(dy, dx)
    size = 7.0
    left = (x - size * math.cos(ang - 0.42), y - size * math.sin(ang - 0.42))
    right = (x - size * math.cos(ang + 0.42), y - size * math.sin(ang + 0.42))
    return (f'<polygon points="{x:.1f},{y:.1f} {left[0]:.1f},{left[1]:.1f} '
            f'{right[0]:.1f},{right[1]:.1f}" fill="{color}" stroke="{color}"/>')


def _pick_label(lx, ly, text, rects, ignore=()):
    """标签尽量不压节点：碰撞就往上下/左右挪一点。

    ignore 里的节点（一般是这条边的源节点）不算障碍——T/F 标签本来就贴在
    决定框旁边，菱形/节点的包围盒比实际形状大，拿它当障碍会把标签顶到别的线上。
    """
    w = max(16.0, _label_len(str(text)) + 8)
    skip = set(ignore)
    for ddx, ddy in ((0, 0), (0, -16), (0, 16), (-w, 0), (w, 0),
                     (0, -30), (0, 30), (-w, -16), (w, -16)):
        x, y = lx + ddx, ly + ddy
        hit = False
        for i, (x1, y1, x2, y2) in enumerate(rects):
            if i in skip:
                continue
            if (x + w / 2 > x1 and x - w / 2 < x2
                    and y + 4 > y1 and y - 12 < y2):
                hit = True
                break
        if not hit:
            return x, y
    return lx, ly


def _edge_label(x, y, text):
    w = max(16.0, _label_len(str(text)) + 8)
    return (f'<rect x="{x - w / 2:.1f}" y="{y - 11:.1f}" width="{w:.1f}" height="16" '
            f'fill="#ffffff"/>'
            f'<text x="{x:.1f}" y="{y + 1:.1f}" font-size="12" fill="#5a6480" '
            f'text-anchor="middle" font-family="system-ui, sans-serif">'
            f'{_esc(text)}</text>')


def _flow_svg(data):
    """Graphviz 只算节点位置；符号和直角连线自己画（输入输出=平行四边形）。"""
    dot_src = _dot_flow(data)
    plain = _dot_plain(dot_src) if dot_src else ""
    if not plain:
        return ""
    rows = plain.splitlines()
    head = rows[0].split()
    if len(head) < 4:
        return ""
    gw, gh = float(head[2]), float(head[3])
    raw_nodes = _norm_nodes(data.get("nodes"))
    raw_edges = _norm_edges(data.get("edges"))
    idx = {n["id"]: i for i, n in enumerate(raw_nodes)}
    geo = []
    for line in rows[1:]:
        parts = line.split()
        if parts and parts[0] == "node" and len(parts) >= 6:
            geo.append({"x": float(parts[2]), "y": float(parts[3]),
                        "w": float(parts[4]) * FLOW_PX,
                        "h": float(parts[5]) * FLOW_PX})
    if len(geo) != len(raw_nodes):
        return ""
    for i, g in enumerate(geo):
        # Graphviz 算的框偏紧，留点余量，避免短表达式被挤成两行；
        # 再按文字宽估一遍（`Int X = 1` 这种别被拆成两行）。
        g["w"] += 16.0
        g["h"] += 8.0
        g["w"] = max(g["w"], _label_len(raw_nodes[i]["label"]) + 34)
    mx, my = 70.0, 40.0
    cw = gw * FLOW_PX + mx * 2
    ch = gh * FLOW_PX + my * 2
    for g in geo:
        g["cx"] = mx + g["x"] * FLOW_PX
        g["cy"] = my + (gh - g["y"]) * FLOW_PX
    rects = [(g["cx"] - g["w"] / 2, g["cy"] - g["h"] / 2,
              g["cx"] + g["w"] / 2, g["cy"] + g["h"] / 2) for g in geo]
    out = []
    for i, n in enumerate(raw_nodes):
        g = geo[i]
        cx, cy, w, h = g["cx"], g["cy"], g["w"], g["h"]
        kind = str(n.get("kind") or "process").strip().lower()
        if kind in ("start", "end", "起", "止"):
            fill, stroke, tcolor = PALETTE["green"]
            out.append(f'<rect x="{cx - w / 2:.1f}" y="{cy - h / 2:.1f}" width="{w:.1f}" '
                       f'height="{h:.1f}" rx="{h / 2:.1f}" fill="{fill}" stroke="{stroke}" '
                       f'stroke-width="1.6"/>')
            tw = w - 12
        elif kind in ("decision", "判断", "条件"):
            fill, stroke, tcolor = PALETTE["gold"]
            out.append(f'<polygon points="{cx:.1f},{cy - h / 2:.1f} {cx + w / 2:.1f},{cy:.1f} '
                       f'{cx:.1f},{cy + h / 2:.1f} {cx - w / 2:.1f},{cy:.1f}" '
                       f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>')
            tw = w * 0.72
        elif kind in ("io", "input", "output", "输入", "输出", "输入输出", "inout"):
            fill, stroke, tcolor = _color_of(n.get("color"))
            # 输入/输出：矩形（不再画平行四边形）
            out.append(f'<rect x="{cx - w / 2:.1f}" y="{cy - h / 2:.1f}" width="{w:.1f}" '
                       f'height="{h:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>')
            tw = w - 18
        else:
            fill, stroke, tcolor = _color_of(n.get("color"))
            out.append(f'<rect x="{cx - w / 2:.1f}" y="{cy - h / 2:.1f}" width="{w:.1f}" '
                       f'height="{h:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>')
            tw = w - 18
        out.append(_fit_text(cx, cy, _wrap_px(n.get("label"), tw), tw, color=tcolor))
    # ---- 用 Graphviz 自己算的走线（绕开节点），不再手工画折线 ----
    # 以前只拿 Graphviz 的节点坐标、边自己画直角线：回边会从框中间穿过去，
    # 长边也会压住中间的框。这里直接解析 dot -Tplain 的 edge 折线。
    _edge_label_map = {}
    for _a, _b, _lab in raw_edges:
        _edge_label_map.setdefault((_a, _b), _lab)
    _splines = {}
    for _line in rows[1:]:
        _p = _line.split()
        if not _p or _p[0] != "edge" or len(_p) < 6:
            continue
        try:
            _ia = int(_p[1].lstrip("n"))
            _ib = int(_p[2].lstrip("n"))
            _n = int(_p[3])
        except ValueError:
            continue
        if _ia >= len(raw_nodes) or _ib >= len(raw_nodes) or _n < 2:
            continue
        if 4 + 2 * _n > len(_p):
            continue
        _pts = [(float(_p[4 + 2 * _k]), float(_p[5 + 2 * _k])) for _k in range(_n)]
        _rest = _p[4 + 2 * _n:]
        _lpos = None
        if len(_rest) >= 2:
            try:
                _lpos = (float(_rest[0]), float(_rest[1]))
            except ValueError:
                _lpos = None
        _splines[(_ia, _ib)] = (_pts, _lpos)
    if _splines:
        for (_ia, _ib), (_pts, _lpos) in _splines.items():
            if len(_pts) < 2:
                continue
            _tp = [(mx + _x * FLOW_PX, my + (gh - _y) * FLOW_PX) for _x, _y in _pts]
            out.append(_polyline(_tp))
            _dx = _tp[-1][0] - _tp[-2][0]
            _dy = _tp[-1][1] - _tp[-2][1]
            _ln = math.hypot(_dx, _dy) or 1.0
            out.append(_arrow_head(_tp[-1][0], _tp[-1][1], _dx / _ln, _dy / _ln))
            _label = _edge_label_map.get((raw_nodes[_ia]["id"], raw_nodes[_ib]["id"]), "")
            if _label:
                if _lpos:
                    _lx = mx + _lpos[0] * FLOW_PX
                    _ly = my + (gh - _lpos[1]) * FLOW_PX
                else:
                    _lx, _ly = _tp[len(_tp) // 2]
                out.append(f'<text x="{_lx:.1f}" y="{_ly - 8:.1f}" font-size="12" '
                           f'fill="#5a6480" text-anchor="middle" '
                           f'font-family="system-ui, sans-serif">{_esc(_label)}</text>')
        return _canvas(cw, ch, "".join(out), "")

    right_ch = mx + gw * FLOW_PX + 34
    left_ch = mx - 34

    def _draw_arrow(pts):
        out.append(_polyline(pts))
        dx = pts[-1][0] - pts[-2][0]
        dy = pts[-1][1] - pts[-2][1]
        ln = math.hypot(dx, dy) or 1.0
        out.append(_arrow_head(pts[-1][0], pts[-1][1], dx / ln, dy / ln))

    def _label(lx, ly, txt, ignore=()):
        if not txt:
            return
        px, py = _pick_label(lx, ly, txt, rects, ignore)
        out.append(_edge_label(px, py, txt))

    def kind_of(i):
        return str(raw_nodes[i].get("kind") or "process").strip().lower()

    def is_decision(i):
        return kind_of(i) in ("decision", "判断", "条件")

    def bottom(i):
        return geo[i]["cy"] + geo[i]["h"] / 2

    def top(i):
        return geo[i]["cy"] - geo[i]["h"] / 2

    forward_list, back_list, same_list = [], [], []
    for a, b, label in raw_edges:
        if a not in idx or b not in idx:
            continue
        ia, ib = idx[a], idx[b]
        ga, gb = geo[ia], geo[ib]
        if gb["cy"] > ga["cy"] + 2:
            forward_list.append((ia, ib, label))
        elif gb["cy"] < ga["cy"] - 2:
            back_list.append((ia, ib, label))
        else:
            same_list.append((ia, ib, label))

    # 每个目标来源的最低底部（回边并线用）
    sb_max = {}
    for ia, ib, label in forward_list:
        sb_max[ib] = max(sb_max.get(ib, 0.0), bottom(ia))

    # ---- 同层直连 ----
    for ia, ib, label in same_list:
        ga, gb = geo[ia], geo[ib]
        if gb["cx"] >= ga["cx"]:
            p1 = (ga["cx"] + ga["w"] / 2, ga["cy"])
            p2 = (gb["cx"] - gb["w"] / 2 - 1, gb["cy"])
            d = (1, 0)
        else:
            p1 = (ga["cx"] - ga["w"] / 2, ga["cy"])
            p2 = (gb["cx"] + gb["w"] / 2 + 1, gb["cy"])
            d = (-1, 0)
        out.append(_polyline([p1, p2]))
        out.append(_arrow_head(p2[0], p2[1], d[0], d[1]))
        _label((p1[0] + p2[0]) / 2, ga["cy"] - 8, label, (ia,))

    by_src = {}
    for ia, ib, label in forward_list:
        by_src.setdefault(ia, []).append((ib, label))

    # ---- 多分支：同一判断 ≥3 条正向边，画横向分叉线 ----
    fanout = set()
    for ia, items in by_src.items():
        if len(items) < 3 or not is_decision(ia):
            continue
        ga = geo[ia]
        tops = [top(ib) for ib, _ in items]
        bar_y = bottom(ia) + 16
        if bar_y > min(tops) - 16:
            continue
        xs = [geo[ib]["cx"] for ib, _ in items]
        out.append(_polyline([(ga["cx"], bottom(ia)), (ga["cx"], bar_y)]))
        out.append(_polyline([(min(xs), bar_y), (max(xs), bar_y)]))
        for (ib, label), tx in zip(items, xs):
            ty = top(ib)
            out.append(_polyline([(tx, bar_y), (tx, ty - 1)]))
            out.append(_arrow_head(tx, ty - 1, 0, 1))
            _label(tx + 12, (bar_y + ty) / 2, label, (ia,))
            fanout.add((ia, ib))

    # ---- 决策两步都朝下时，F/否 改侧出 ----
    forced_side = set()
    for ia, items in by_src.items():
        if len(items) != 2 or not is_decision(ia):
            continue
        ga = geo[ia]
        dxs = [geo[ib]["cx"] - ga["cx"] for ib, _ in items]
        if all(abs(d) < max(30.0, ga["w"] * 0.45) for d in dxs):
            pick = None
            for ib, label in items:
                if str(label or "").strip().upper() in ("F", "否", "N", "FALSE"):
                    pick = ib
            if pick is None:
                pick = items[1][0]
            forced_side.add((ia, pick))

    # ---- 正向边：单条 / 汇合主干 ----
    targets = {}
    for ia, ib, label in forward_list:
        if (ia, ib) in fanout:
            continue
        targets.setdefault(ib, []).append((ia, label))

    for ib, srcs in targets.items():
        gb = geo[ib]
        tb = (gb["cx"], top(ib))
        if len(srcs) == 1:
            ia, label = srcs[0]
            ga = geo[ia]
            sb = (ga["cx"], bottom(ia))
            dx = gb["cx"] - ga["cx"]
            if is_decision(ia) and ((ia, ib) in forced_side
                                    or abs(dx) > max(30.0, ga["w"] * 0.45)):
                if (ia, ib) in forced_side and abs(dx) < 30:
                    sx = ga["cx"] + ga["w"] / 2
                    jog = sx + 26
                    pts = [(sx, ga["cy"]), (jog, ga["cy"]),
                           (jog, tb[1] - 18), (gb["cx"], tb[1] - 18), tb]
                    _draw_arrow(pts)
                    _label(sx + 14, ga["cy"] - 9, label, (ia,))
                else:
                    side = 1 if dx >= 0 else -1
                    sx = ga["cx"] + side * ga["w"] / 2
                    _draw_arrow([(sx, ga["cy"]), (gb["cx"], ga["cy"]), tb])
                    _label(sx + side * 22, ga["cy"] - 9, label, (ia,))
            elif abs(dx) < 2:
                _draw_arrow([sb, tb])
                _label(ga["cx"] + 14, (sb[1] + tb[1]) / 2, label, (ia,))
            else:
                midy = (sb[1] + tb[1]) / 2
                _draw_arrow([sb, (ga["cx"], midy), (gb["cx"], midy), tb])
                _label((ga["cx"] + gb["cx"]) / 2, midy - 8, label, (ia,))
        else:
            # 汇合：各支先到同一水平线，再并成一条主干、一个箭头
            sbs = [(geo[ia]["cx"], bottom(ia)) for ia, _ in srcs]
            low = max(y for _, y in sbs)
            join_y = low + 16
            if join_y > tb[1] - 14:
                join_y = (low + tb[1]) / 2
            for (ia, label), (sx, sy) in zip(srcs, sbs):
                out.append(_polyline([(sx, sy), (sx, join_y), (gb["cx"], join_y)]))
                if label:
                    _label(sx + 14, (sy + join_y) / 2, label, (ia,))
            _draw_arrow([(gb["cx"], join_y), tb])

    # ---- 回边：并入目标上方那条入线的中段（教材循环画法）----
    for ia, ib, label in back_list:
        ga, gb = geo[ia], geo[ib]
        t = top(ib)
        if ib in sb_max:
            join_y = (sb_max[ib] + t) / 2 + 4
        else:
            join_y = t - 24
        if ga["cx"] >= gb["cx"]:
            chx = right_ch
            p1 = (ga["cx"] + ga["w"] / 2, ga["cy"])
            d = (-1, 0)
        else:
            chx = left_ch
            p1 = (ga["cx"] - ga["w"] / 2, ga["cy"])
            d = (1, 0)
        pts = [p1, (chx, ga["cy"]), (chx, join_y), (gb["cx"], join_y)]
        out.append(_polyline(pts))
        out.append(_arrow_head(gb["cx"], join_y, d[0], d[1]))
        _label((p1[0] + chx) / 2, ga["cy"] - 8, label, (ia,))

    return _canvas(cw, ch, "".join(out), "")


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
    idx = {n["id"]: i for i, n in enumerate(nodes)}
    lines = [_dot_graph(rankdir="TB", splines="polyline", nodesep=0.5, ranksep=0.62)]
    for i, n in enumerate(nodes):
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
        elif kind in ("io", "input", "output", "输入", "输出", "输入输出", "inout"):
            attrs.update({"shape": "box", "style": "filled",
                          "fillcolor": "#ffffff", "color": "#9a9aae",
                          "fontcolor": "#33333d"})
        else:
            attrs.update(_dot_colors(n.get("color")))
        lines.append(f'  "n{i}" [{_dot_attr(attrs)}];')
    for a, b, label in edges:
        if a not in idx or b not in idx:
            continue
        attrs = {"label": _dot_label(label)} if label else {}
        if label:
            attrs.update({"fontsize": "10", "labeldistance": "1.2", "labelangle": "0"})
        lines.append(f'  "n{idx[a]}" -> "n{idx[b]}" [{_dot_attr(attrs)}];')
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


# ---------------- 判定树 / 决策树 ----------------
DTREE_ACTION_KINDS = {"action", "动作", "结果", "leaf", "result", "操作", "输出", "行为"}


def _dtree_is_action(node):
    return str(node.get("kind") or "").strip().lower() in DTREE_ACTION_KINDS


def _dtree_normalize(data):
    """把两种入参都归一成 (nodes, edges, root)：

    1) 扁平：{nodes:[{id,label,kind?}], edges:[{from,to}], root?}
    2) 嵌套：{root:{label, children:[...], action?: "..."}}
    """
    root_spec = data.get("root")
    if isinstance(root_spec, dict):
        nodes, edges = [], []

        def walk(obj, parent=None):
            nid = "n%d" % len(nodes)
            label = str(obj.get("label") or obj.get("name") or "").strip()
            kind = "action" if _dtree_is_action(obj) else "condition"
            nodes.append({"id": nid, "label": label, "kind": kind})
            if parent is not None:
                edges.append((parent, nid, ""))
            for ch in obj.get("children") or []:
                walk(ch if isinstance(ch, dict) else {"label": str(ch)}, nid)
            act = obj.get("action")
            if isinstance(act, str) and act.strip():
                walk({"label": act.strip(), "kind": "action"}, nid)
            return nid

        root = walk(root_spec)
        return nodes, edges, root

    nodes = _norm_nodes(data.get("nodes"))
    edges = _norm_edges(data.get("edges"))
    for n in nodes:
        n["kind"] = "action" if _dtree_is_action(n) else "condition"
    ids = {n["id"] for n in nodes}
    incoming = {b for _, b, _ in edges}
    root = str(data.get("root") or "")
    if not root or root not in ids:
        root = next((n["id"] for n in nodes if n["id"] not in incoming),
                    nodes[0]["id"] if nodes else "")
    return nodes, edges, root


def _dtree_children(nodes, edges):
    kids = {n["id"]: [] for n in nodes}
    for a, b, _label in edges:
        if a in kids and b in kids:
            kids[a].append(b)
    return kids


def _dtree_kinds(nodes, kids, root):
    """节点类型：显式 action 或没孩子的非根节点都算动作（判定树叶子=动作）。"""
    out = {}
    for n in nodes:
        nid = n["id"]
        if n.get("kind") in DTREE_ACTION_KINDS:
            out[nid] = "action"
        elif nid != root and not kids.get(nid):
            out[nid] = "action"
        else:
            out[nid] = "condition"
    return out


def _dtree_text(data):
    """教材图 5-8 那种大括号格式的纯文本。"""
    nodes, edges, root = _dtree_normalize(data)
    if not nodes or not root:
        return ""
    by_id = {n["id"]: n for n in nodes}
    kids = _dtree_children(nodes, edges)
    kinds = _dtree_kinds(nodes, kids, root)
    lines = []

    def walk(nid, depth, seen):
        if nid not in by_id or nid in seen:
            return
        seen = seen | {nid}
        label = by_id[nid]["label"]
        children = [k for k in kids.get(nid, []) if k in by_id and k not in seen]
        if not children:
            lines.append("  " * depth + label)
            return
        cond = [k for k in children if kinds.get(k) != "action"]
        acts = [k for k in children if kinds.get(k) == "action"]
        if not cond:
            for k in acts:
                lines.append("  " * depth + label + " ┈ " + by_id[k]["label"])
            return
        lines.append("  " * depth + label + " {")
        for k in children:
            if kinds.get(k) == "action":
                lines.append("  " * (depth + 1) + label + " ┈ " + by_id[k]["label"])
            else:
                walk(k, depth + 1, seen)
        lines.append("  " * depth + "}")

    walk(root, 0, set())
    return "\n".join(lines)


def _tex_escape(text):
    """标签里可能破坏 LaTeX 的字符先转义。"""
    special = {
        "\\": "\\textbackslash{}", "&": "\\&", "%": "\\%", "$": "\\$",
        "#": "\\#", "_": "\\_", "{": "\\{", "}": "\\}",
        "~": "\\textasciitilde{}", "^": "\\textasciicircum{}",
    }
    return "".join(special.get(ch, ch) for ch in str(text or ""))


def _dtree_latex(data):
    """教材图 5-8 那种大括号格式的 LaTeX（前端渲染成书上的大括号样式）。

    分支标签（是/否、T/F）会写进去：动作行是「是 ⇢ 包邮」，条件行是「否 → 偏远地区? {…}」。
    """
    nodes, edges, root = _dtree_normalize(data)
    if not nodes or not root:
        return ""
    by_id = {n["id"]: n for n in nodes}
    kids = _dtree_children(nodes, edges)
    kinds = _dtree_kinds(nodes, kids, root)
    labels = {(a, b): str(lab).strip() for a, b, lab in edges if str(lab or "").strip()}
    # 分类分支（合法用户 / 管理员…）的 label 和子节点名一样时是重复的，去掉；
    # 条件分支（正确用户名密码 / 错误用户名密码、是/否、≥99）保留。
    labels = {k: v for k, v in labels.items() if v and v != by_id[k[1]]["label"]}
    sep = " \\\\ "

    def cell(nid, seen):
        if nid not in by_id or nid in seen:
            return ""
        seen = seen | {nid}
        label = _tex_escape(by_id[nid]["label"])
        children = [k for k in kids.get(nid, []) if k in by_id and k not in seen]
        if not children:
            return "\\text{%s}" % label
        cond = [k for k in children if kinds.get(k) != "action"]
        if not cond:
            if len(children) == 1:
                k = children[0]
                branch = labels.get((nid, k)) or label
                return "\\text{%s}\\ \\dashrightarrow\\ \\text{%s}" % (
                    _tex_escape(branch), _tex_escape(by_id[k]["label"]))
            rows = []
            for k in children:
                branch = labels.get((nid, k)) or label
                rows.append("\\text{%s}\\ \\dashrightarrow\\ \\text{%s}"
                            % (_tex_escape(branch), _tex_escape(by_id[k]["label"])))
            return "\\text{%s}\\left\\{\\begin{array}{l}%s\\end{array}\\right." % (
                label, sep.join(rows))
        rows = []
        for k in children:
            branch = labels.get((nid, k))
            if kinds.get(k) == "action":
                rows.append("\\text{%s}\\ \\dashrightarrow\\ \\text{%s}"
                            % (_tex_escape(branch or label),
                               _tex_escape(by_id[k]["label"])))
            elif branch:
                rows.append("\\text{%s}\\ \\rightarrow\\ %s"
                            % (_tex_escape(branch), cell(k, seen)))
            else:
                rows.append(cell(k, seen))
        return "\\text{%s}\\left\\{\\begin{array}{l}%s\\end{array}\\right." % (
            label, sep.join(rows))

    return cell(root, set())


_BT_FS = 15
_BT_LH = 24
_BT_GAP = 6
_BT_ARROW = 32
_BT_BRACE = 12
_BT_PAD = 8
_BT_TGAP = 4


def _bt_w(text):
    total = 0.0
    for ch in str(text or ""):
        total += _BT_FS if ord(ch) > 0x2E80 else _BT_FS * 0.56
    return total


def _dtree_brace_svg(data):
    """教材图 5-8 那种大括号格式的 SVG（网页和 App 都当图片显示）。"""
    nodes, edges, root = _dtree_normalize(data)
    if not nodes or not root:
        return ""
    by_id = {n["id"]: n for n in nodes}
    kids = _dtree_children(nodes, edges)
    kinds = _dtree_kinds(nodes, kids, root)
    labels = {(a, b): str(lab).strip() for a, b, lab in edges if str(lab or "").strip()}
    labels = {k: v for k, v in labels.items() if v and v != by_id[k[1]]["label"]}

    def row_w(k, branch):
        return _bt_w(branch) + _BT_ARROW + _BT_TGAP + _bt_w(by_id[k]["label"])

    def rows_of(nid):
        out = []
        for k in [x for x in kids.get(nid, []) if x in by_id]:
            branch = labels.get((nid, k))
            if kinds.get(k) == "action":
                out.append((k, branch, True))
            else:
                out.append((k, branch, False))
        return out

    def size(nid, seen):
        if nid not in by_id or nid in seen:
            return 0.0, _BT_LH
        seen = seen | {nid}
        label = by_id[nid]["label"]
        rows = [r for r in rows_of(nid) if r[0] not in seen]
        if not rows:
            return _bt_w(label), _BT_LH
        if not [r for r in rows if not r[2]]:
            if len(rows) == 1 and not rows[0][1]:
                k = rows[0][0]
                return _bt_w(label) + _BT_ARROW + _BT_TGAP + _bt_w(by_id[k]["label"]), _BT_LH
            rw = max(row_w(r[0], r[1] or label) for r in rows)
            bh = len(rows) * _BT_LH + (len(rows) - 1) * _BT_GAP
            return _bt_w(label) + _BT_PAD + _BT_BRACE + _BT_PAD + rw, bh
        bw = 0.0
        bh = 0.0
        for i, (k, branch, action) in enumerate(rows):
            if action:
                cw, ch = row_w(k, branch), _BT_LH
            elif not branch:
                cw, ch = size(k, seen)
            else:
                iw, ih = size(k, seen)
                cw, ch = _bt_w(branch) + _BT_ARROW + _BT_TGAP + iw, ih
            bw = max(bw, cw)
            bh += ch + (_BT_GAP if i else 0)
        return _bt_w(label) + _BT_PAD + _BT_BRACE + _BT_PAD + bw, max(_BT_LH, bh)

    parts = []

    def ink(x, cy, s, color="#2b2f3a", weight="400"):
        parts.append(f'<text x="{x:.1f}" y="{cy + 5.2:.1f}" font-size="{_BT_FS}" '
                     f'fill="{color}" font-weight="{weight}">{_esc(s)}</text>')

    def arrow(x1, cy):
        x2 = x1 + _BT_ARROW
        parts.append(f'<line x1="{x1:.1f}" y1="{cy:.1f}" x2="{x2 - 6:.1f}" y2="{cy:.1f}" '
                     f'stroke="#6b6f80" stroke-width="1.3" stroke-dasharray="5 4"/>')
        parts.append(f'<path d="M {x2:.1f},{cy:.1f} l -7,-3.2 l 0,6.4 z" fill="#6b6f80"/>')

    def brace(bx, top, bh):
        mid = top + bh / 2.0
        parts.append(
            f'<path d="M {bx + 7:.1f},{top:.1f} Q {bx:.1f},{top:.1f} {bx:.1f},{top + 7:.1f} '
            f'L {bx:.1f},{mid - 6:.1f} Q {bx:.1f},{mid:.1f} {bx - 6:.1f},{mid:.1f} '
            f'Q {bx:.1f},{mid:.1f} {bx:.1f},{mid + 6:.1f} L {bx:.1f},{top + bh - 7:.1f} '
            f'Q {bx:.1f},{top + bh:.1f} {bx + 7:.1f},{top + bh:.1f}" fill="none" '
            f'stroke="#2b2f3a" stroke-width="1.4"/>')

    def row(k, branch, action, x, ry):
        ink(x, ry, branch, "#4a4f5c")
        arrow(x + _bt_w(branch) + 3, ry)
        ink(x + _bt_w(branch) + _BT_ARROW + _BT_TGAP, ry,
            by_id[k]["label"], "#1f7a45" if action else "#2b2f3a")

    def draw(nid, x, cy, seen):
        if nid not in by_id or nid in seen:
            return
        seen = seen | {nid}
        label = by_id[nid]["label"]
        rows = [r for r in rows_of(nid) if r[0] not in seen]
        if not rows:
            return ink(x, cy, label)
        if not [r for r in rows if not r[2]]:
            if len(rows) == 1 and not rows[0][1]:
                k = rows[0][0]
                ink(x, cy, label)
                arrow(x + _bt_w(label) + 3, cy)
                ink(x + _bt_w(label) + _BT_ARROW + _BT_TGAP, cy,
                    by_id[k]["label"], "#1f7a45")
                return
            ink(x, cy, label)
            bh = len(rows) * _BT_LH + (len(rows) - 1) * _BT_GAP
            top = cy - bh / 2.0
            bx = x + _bt_w(label) + _BT_PAD
            brace(bx, top, bh)
            cx = bx + _BT_BRACE + _BT_PAD
            for i, (k, branch, _action) in enumerate(rows):
                ry = top + i * (_BT_LH + _BT_GAP) + _BT_LH / 2.0
                row(k, branch or label, True, cx, ry)
            return
        ink(x, cy, label)
        heights = []
        bh = 0.0
        for i, (k, branch, action) in enumerate(rows):
            ch = _BT_LH if action else size(k, seen)[1]
            heights.append(ch)
            bh += ch + (_BT_GAP if i else 0)
        top = cy - bh / 2.0
        bx = x + _bt_w(label) + _BT_PAD
        brace(bx, top, bh)
        cx = bx + _BT_BRACE + _BT_PAD
        cur = top
        for (k, branch, action), ch in zip(rows, heights):
            ry = cur + ch / 2.0
            if action:
                row(k, branch or label, True, cx, ry)
            elif not branch:
                draw(k, cx, ry, seen)
            else:
                ink(cx, ry, branch, "#4a4f5c")
                arrow(cx + _bt_w(branch) + 3, ry)
                draw(k, cx + _bt_w(branch) + _BT_ARROW + _BT_TGAP, ry, seen)
            cur += ch + _BT_GAP

    w, h = size(root, set())
    W = int(w + 24)
    H = int(h + 24)
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
                 f'viewBox="0 0 {W} {H}">')
    parts.append('<g font-family="system-ui,-apple-system,\'PingFang SC\',\'Microsoft YaHei\',sans-serif">')
    draw(root, 12, 12 + h / 2.0, set())
    parts.append('</g></svg>')
    return "".join(parts)


def draw_dtree(data, title=""):
    """判定树/决策树：条件白框 + 动作绿框，层级排布，动作连线用虚线。"""
    nodes, edges, root = _dtree_normalize(data)
    if not nodes:
        raise ValueError("nodes 是空的，至少给一个根节点")
    by_id = {n["id"]: n for n in nodes}
    kids = _dtree_children(nodes, edges)
    kinds = _dtree_kinds(nodes, kids, root)

    order, depth = [], {}
    seen = set()

    def collect(nid, d):
        if nid in seen or nid not in by_id:
            return
        seen.add(nid)
        depth[nid] = d
        ch = [k for k in kids.get(nid, []) if k in by_id]
        if not ch:
            order.append(nid)
        for k in ch:
            collect(k, d + 1)

    collect(root, 0)
    if not order:
        return _canvas(360, 150, "", title)

    sizes = {n["id"]: (max(96.0, _label_len(n["label"]) + 34), 40.0) for n in nodes}
    xpos, cursor = {}, 34.0
    for nid in order:
        w, _h = sizes[nid]
        xpos[nid] = cursor + w / 2
        cursor += w + 26

    def place(nid):
        nonlocal cursor
        if nid in xpos:
            return xpos[nid]
        ch = [k for k in kids.get(nid, []) if k in by_id and k in depth]
        if not ch:
            w, _h = sizes[nid]
            xpos[nid] = cursor + w / 2
            cursor += w + 26
        else:
            xs = [place(k) for k in ch]
            xpos[nid] = sum(xs) / len(xs)
        return xpos[nid]

    place(root)
    base_y = 66.0 if title else 42.0
    ypos = {nid: base_y + depth.get(nid, 0) * 102.0 for nid in xpos}

    body = []
    for a, b, _label in edges:
        if a not in xpos or b not in ypos:
            continue
        x1, y1 = xpos[a], ypos[a] + 20
        x2, y2 = xpos[b], ypos[b] - 20
        mid = (y1 + y2) / 2
        dash = ' stroke-dasharray="5 4"' if kinds.get(b) == "action" else ""
        body.append(
            f'<polyline points="{x1:.1f},{y1:.1f} {x1:.1f},{mid:.1f} '
            f'{x2:.1f},{mid:.1f} {x2:.1f},{y2:.1f}" fill="none" '
            f'stroke="{LINE}" stroke-width="1.5"{dash} stroke-linejoin="round"/>')
        body.append(_arrow_head(x2, y2, 0, 1))
        if _label and _label != by_id[b]["label"]:
            body.append(
                f'<text x="{(x1 + x2) / 2:.1f}" y="{mid - 5:.1f}" font-size="12" '
                f'fill="#6b6f80" text-anchor="middle" '
                f'font-family="system-ui, sans-serif">{_esc(_label)}</text>')

    for nid in xpos:
        n, (w, h) = by_id[nid], sizes[nid]
        color = "green" if kinds.get(nid) == "action" else "white"
        body.append(_box(xpos[nid], ypos[nid], w, h, n["label"],
                         color=color, bold=(nid == root)))

    width = max(xpos[nid] + sizes[nid][0] / 2 for nid in xpos) + 34
    height = max(ypos[nid] + 20 for nid in xpos) + 30
    return _canvas(width, height, "".join(body), title)


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
    "ns": draw_ns,
    "n-s": draw_ns,
    "pad": draw_pad,
    "graph": draw_graph,
    "dtree": draw_dtree,
    "dtable": draw_dtable,
    "dt": draw_dtable,
    "判定表": draw_dtable,
    "决策表": draw_dtable,
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
    "盒图": "ns", "ns图": "ns", "n-s图": "ns", "n-s": "ns", "ns": "ns",
    "nassi": "ns", "nassi-shneiderman": "ns", "nsd": "ns",
    "pad": "pad", "pad图": "pad", "问题分析图": "pad", "问题分析": "pad",
    "图": "graph", "关系图": "graph", "graph": "graph",
    "dtree": "dtree", "decision_tree": "dtree", "decisiontree": "dtree",
    "decision tree": "dtree", "判定树": "dtree", "决策树": "dtree",
    "判定树图": "dtree", "决策树图": "dtree", "dt": "dtree",
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
            "error": f"不认识的图类型「{kind}」。可选：tree（树）/ flow（流程图）/ ns（盒图）/ dtable（判定表）/ pad（问题分析图）/ graph（关系图）/ "
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
    if key in ("flow", "flowchart"):
        # 统一自绘：解析 sequence / if-else / do-until，全部用同一套画法
        try:
            svg = _flow_custom_svg(data, bool(title))
        except Exception as exc:         # noqa: BLE001
            print(f"[diagram] flow 结构化排版失败：{type(exc).__name__}: {exc}", flush=True)
            svg = ""
        if not svg:
            return json.dumps({
                "error": "流程图结构无法自动排版。请检查数据：单一入口、单一出口、"
                         "分支最终汇合到同一个点、循环只有一条回边且回边指向循环头。修正后重新调用 draw_diagram。",
            }, ensure_ascii=False)
    builder = GRAPHVIZ_BUILDERS.get(key) if not svg else None
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
    # er 不走 graphviz（属性绕实体一圈的教材画法 graphviz 排不出来），直接用内置布局
    if key == "er" and not svg:
        try:
            svg = drawer(data, "")
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except Exception as exc:                 # noqa: BLE001
            return json.dumps({"error": f"画图失败：{type(exc).__name__}: {exc}"},
                              ensure_ascii=False)
    if not svg and key in ("state", "dataflow"):
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
    if key in ("flow", "flowchart"):
        _fn = _norm_nodes(data.get("nodes"))
        _fe = _norm_edges(data.get("edges"))
        _out = {}
        for _a, _b, _lab in _fe:
            _out.setdefault(_a, []).append((_b, _lab))
        _bad = []
        _dec_labels = []
        for _n in _fn:
            _k = (_n.get("kind") or "").strip().lower()
            if _k in ("decision", "判断", "条件"):
                _dec_labels.append(str(_n.get("label") or "").strip())
                _targets = [b for b, _ in _out.get(_n["id"], [])]
                if len(_targets) < 2 or len(set(_targets)) < 2:
                    _bad.append(_n["label"])
        _dups = sorted({x for x in _dec_labels if x and _dec_labels.count(x) > 1})
        if _dups:
            payload["warning"] = (
                "同一个判断出现了多次（" + "、".join(_dups[:3]) + "）：分支汇合后的公共部分只画一次，"
                "两条线并回来再往下，不要给每条分支复制一份相同的后续流程。")
        if _bad:
            payload["warning"] = (
                "判断框必须有两条出边（T/F 直接标在判断框的两条出边上），"
                "不要把公共节点塞在判断和分支之间，也别让下一层再分 T/F。问题判断框："
                + "、".join(_bad[:3]))
    if key == "dtree":
        try:
            payload["book_format_latex"] = _dtree_latex(data)
        except Exception as exc:                 # noqa: BLE001
            payload["book_format_latex"] = ""
            payload["warning"] = f"判定树 LaTeX 生成失败：{type(exc).__name__}: {exc}"
        try:
            brace = _dtree_brace_svg(data)
            if brace:
                head = ('<!doctype html><html><head><meta charset="utf-8"><style>'
                        'html,body{margin:0;padding:0;background:transparent;}'
                        'svg{display:block;margin:0 auto;max-width:100%;height:auto;}'
                        '</style></head><body>')
                bid = render._store(head + brace + "</body></html>", "教材大括号格式")
                _remember_svg(bid, brace)
                payload["book_artifact_id"] = bid
                payload["book_title"] = "教材大括号格式（参照吴俊杰、张淼《软件工程》）"
        except Exception:                    # noqa: BLE001
            payload["book_artifact_id"] = ""
        _bad = [n["label"] for n in _dtree_normalize(data)[0]
                if ("?" in n["label"] or "？" in n["label"] or "是否" in n["label"])]
        if _bad:
            payload["warning"] = (
                "节点标签尽量写实体/属性名词，不要写问句（如「会员?」→「会员」），"
                "判断结果放到 edges[].label（如「是会员 / 不是会员」）。问题节点："
                + "、".join(_bad[:3]))
        payload["note"] = (
            "已经画好两张卡片：通用树形图 + 教材大括号格式（都是图片，网页和 App 都能正常显示）。"
            "正文顺序：① 先说明「上面这张卡片是通用树形图（判定树/决策树的图形表示）」；"
            "② 再说明「下面这张是教材里的大括号格式（格式参照吴俊杰、张淼《软件工程》，内容按本题场景）」。"
            "**不要再贴 LaTeX、SVG、HTML，也不要自己写 $$ ... $$**；两张卡片会自动显示。"
            "教材只提供格式，不要说「教材原文」。"
        )
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
                "画**数据结构图（二叉树/红黑树/堆）、流程图、盒图（N-S 图）、PAD（问题分析图）、关系图、数据流图(DFD)、"
                "架构图、时序图、柱状/折线/饼图、时间线**优先用它——只要几十个 token，"
                "比 render_html 快一个数量级，而且配色统一。"
                "别自己手写 SVG！创意海报、界面草图、动画、自由排版才用 render_html。\n"
                "type 取值与 data 结构：\n"
                "  tree: {nodes:[{id,label,color?}], edges:[{from,to}], root?}  "
                "color: red/black/green/blue/gold/gray/white（红黑树就用 red / black）\n"
                "  flow: {nodes:[{id,label,kind?:start|process|decision|io|end}], "
                "edges:[{from,to,label?}], start?}\n"
                "    程序流程图符号：开始/结束 kind=start/end（圆角），处理框 kind=process（矩形），"
                "判断 kind=decision（菱形），**输入/输出 kind=io 也用矩形（不要平行四边形）**；"
                "连线由工具排成直角正交（横平竖直），不用自己写坐标。\n"
                "    **判断框的分支画在菱形左右两侧**：按黑板样式 T 在右、F 在左，"
                "两支的框放在菱形两侧，各自向下走，在下方汇合点汇合后再继续往下；"
                "不会把两条分支都挤在正下方。\n"
                "**框里的式子照题目给的伪代码写**：题目写了 `Integer X=0`、`AND`/`OR`、"
                "`>=`/`<=`、`ZPUT(A,B,C)`、`Print X`，就原样抄（别缩成 `X=0`、别把 `>=` 改成 `>`、"
                "别把 `AND`/`OR` 改成 `&`/`|`/「且」「或」、别翻译函数名）；题目没给伪代码时才用短符号："
                "赋值 `flag=1`、逻辑与/或 `&`/`|`、真/假 `1`/`0`，条件末尾问号可留可去。"
                "每个框里的式子尽量短。\n"
                "    **决策分支的连线标签统一用英文 T / F（T=是、F=否），别写「是」「否」。**\n"
                "    **判断框必须有两条出边**：T/F 直接标在判断框的两条出边上，各自连到分支节点或汇合点；"
                "不要把公共节点塞在判断和分支之间，也不要让下一层的框再分 T/F（写错工具会提示你重画）。\n"
                "    **分支汇合后公共部分只画一次**：两分支先并回一条线，再画后面的公共判断/动作；"
                "不要传「汇合/合并」辅助节点，分支用线自然汇合即可。\n"
                "不要给每条分支复制一份相同的后续流程。\n"
                "    **图上文字用原始字符**：条件/标签直接写 C<10、X>1000、A>1，"
                "不要写 &lt; &gt; 这类 HTML 实体（工具会自动转义一次）。\n"
                "    **伪代码一行一个框**：连续两条声明/赋值别并成一个框（两条 Integer 就画两个框）。\n"
                "  dtable: 判定表（教材表 5-2 格式）：{conditions:[{group?, label} 或 {group, items:[...]}], "
                "actions:[{group?, label} 或 {group, items:[...]}], rules:[{values:[Y/N/-...], actions:[执行的动作行下标/名称...]}], "
                "note?（表格上方一句说明，写清规则 1..N 代表什么）}\n"
                "    **判定表必须带标题和上方说明**：用 title 写表名，note 写一句「规则 1–N 代表……」，"
                "不要只画 1..N 编号不解释。\n"
                "    严格按教材：左上「选项」右上「规则」+ 1..N 列；左侧「条件」「动作」分组；"
                "    条件值只写 Y / N / -（半角减号，- 表示不关心/无关；不要 T/F、真/假、√、长破折号 —）。\n"
                "    **条件成组**：用 {group, items:[...]}，或每行带相同 group（连续同组会自动合并）。\n"
                "    **动作也要分组**：组名照题目写（如「课时津贴费」），行里只写数值；"
                "不要省略组名或把「津贴 80 元/课时」整串写进行里。\n"
                "    **多选一的条件组**：选中的行写 Y，组内其余行写 N；整组不关心才全写 -；"
                "    不要用 - 代替 N（工具会把有 Y 的组里其余列渲染成 N）。\n"
                "  ns: 盒图（N-S 图）：{steps:[...]}，节点 kind 取 "
                "process{label} / if{condition, then, else} / case{condition, branches:[{value, body}]} / "
                "while{condition, body} / until{condition, body} / ref{label}；"
                "if 的 then 画在右边（T）、else 画在左边（F），和教材一致；"
                "**盒图没有开始/结束框**：不要传 Start/Stop/开始/结束 这类节点。\n"
                "**题目是 do...until / 直到型循环时用 kind=until（body=循环体 S，condition=条件 P），"
                "不要画成 if**；do...while / 当型循环用 kind=while；嵌套简化用 ref 椭圆。\n"
                "  pad: 问题分析图（PAD）：{steps:[...]}，节点结构和 ns 一样；"
                "程序从左边竖线开始从上往下排，分支/循环体画在右侧\n"
                "  graph: {nodes:[{id,label,color?,x?,y?,kind?:circle}], edges:[{from,to,label?}]}"
                "（不给坐标就自动环形布局）\n"
                "  dtree: 判定树/决策树：{nodes:[{id,label,kind?:condition|action}], "
                "edges:[{from,to,label?}], root?}；节点省略 kind，动作/结果节点标 action（绿框），"
                "叶子没写 kind 也会自动当动作。**节点写实体/属性名词，不要写问句**（「会员?」写成「会员」）；"
                "**分支连线 label 写取值**（是会员/不是会员、≥99/<99、管理员/教职工/学生）。"
                "**分层顺序按教材：先大类（合法/非法用户）→ 子类（管理员/会员/普通用户）→ "
                "条件（正确/错误用户名密码）→ 动作**；别把条件提到最前面、也别把用户组塞在条件下面；"
                "条件值直接写成分支 label（正确用户名密码/错误用户名密码），别再插一层「用户名密码」节点。"
                "**不要「域名状态/保留名单/归属/用户组」这类概括中间节点**，直接把下一层分类值写成子节点或分支。"
                "域名例：域名注册→格式合法/格式不合法；格式合法→未注册/已注册；未注册→保留域名/非保留域名；"
                "已注册→本人所有/他人所有；不要再套「域名格式/域名状态/保留名单/归属」节点。\n"
                "工具会生成**两张图片卡片**：通用树形图 + 教材大括号格式（格式参照吴俊杰、张淼"
                "《软件工程》，内容按本题场景；网页和 App 都能显示）；正文先说明上图是通用树形图，"
                "再说明下面是教材大括号格式；**别再贴 LaTeX/SVG/HTML**；教材只提供格式，别说「教材原文」；两种格式都要给。\n"
                "  dataflow: {nodes:[{id,label,kind:entity|process|store}], "
                "edges:[{from,to,label?}], notation?:demarco|gane}\n"
                "    **画数据流图先定画法**：用户没说就先用 ask_user 问一句，"
                "选项给「DeMarco（加工画圆）」和「Gane（加工画圆角矩形）」；"
                "用户选完把结果写进 notation（demarco / gane），不写默认 demarco。"
                "两种画法的数据存储符号也不同（DeMarco 两条平行横线 / Gane 半开矩形），"
                "工具按 notation 自动画，你只标 kind=store 就行。\n"
                "    数据流图一次只画一层：要画多层就分多次调用 draw_diagram，title 写清"
                "「0 层/1 层/2 层」；2 层加工编号用父号前缀（1 层 1、2、3…，2 层 1.1、1.2…），"
                "子图的输入输出要和父图里对应加工的输入输出一致。\n"
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
