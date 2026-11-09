"""render_html 工具：让助手把 HTML 画出来，直接渲染在聊天里。

安全是这里的核心，一共四层：

1. **服务端清洗**：用 lxml 重建 DOM，删掉能加载外部资源 / 跳转 / 提权的标签和属性，
   并顺手记下「模型到底写了什么危险东西」写进审计日志。
2. **CSP 响应头**：产物单独走 `/api/artifact/<id>`，返回时带
   `sandbox allow-scripts` + `default-src 'none'` 之类，就算直接在新标签页打开，
   它也跑在独立的不透明源里，读不到本站的 cookie / localStorage。
3. **前端 iframe sandbox**：`<iframe sandbox="allow-scripts">`（不带 allow-same-origin），
   脚本能跑但摸不到父页面。
4. **审计钩子**：每次渲染都往 `.cache/render_audit.jsonl` 追加一条，
   记录时间、大小、sha256、清洗时发现的风险项；同时打一条 journald 日志。
"""
import hashlib
import json
import os
import re
import threading
import time
import uuid

from lxml import html as lxml_html

from ..config import PROJECT_ROOT, RENDER_AUDIT_ENABLED, RENDER_LIMIT, RENDER_MAX_BYTES, RENDER_TTL

# 产物单独取走后要带的 CSP。sandbox 指令是关键：即使有人把 /api/artifact/xx
# 直接丢进新标签页打开，文档也跑在不透明源里，碰不到本站的 cookie 和 localStorage。
ARTIFACT_CSP = (
    "sandbox allow-scripts; "
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; "
    "img-src data: blob:; "
    "font-src data:; "
    "media-src data:; "
    "connect-src 'none'; "
    "child-src 'none'; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'self'"
)

AUDIT_PATH = os.path.join(PROJECT_ROOT, ".cache", "render_audit.jsonl")

# 能自己拉网络 / 跳走 / 提权的标签，一律删掉（清完记进 findings，方便审计）
_DROP_TAGS = ("iframe", "frame", "frameset", "object", "embed", "applet",
              "base", "link", "meta")

# URL 类属性里出现这些协议就不安全
_BAD_SCHEMES = re.compile(r"^\s*(javascript|vbscript|data:text/html|file)\s*:", re.I)

# 审计时顺手记一下模型有没有写这些「看着像想搞事」的东西（只是记录，不拦）
_SUSPICIOUS = (
    ("cookie", re.compile(r"document\s*\.\s*cookie", re.I)),
    ("localstorage", re.compile(r"localStorage|sessionStorage", re.I)),
    ("fetch", re.compile(r"\bfetch\s*\(", re.I)),
    ("xhr", re.compile(r"XMLHttpRequest|\.sendBeacon\s*\(", re.I)),
    ("websocket", re.compile(r"new\s+WebSocket", re.I)),
    ("eval", re.compile(r"\beval\s*\(|new\s+Function\s*\(", re.I)),
    ("external-img", re.compile(r"<img[^>]+src\s*=\s*[\"']https?:", re.I)),
    ("external-css", re.compile(r"<link[^>]+href\s*=\s*[\"']https?:", re.I)),
    ("parent-access", re.compile(r"window\s*\.\s*(parent|top|opener)", re.I)),
)

# 让 iframe 自己把尺寸报给父页面，并且**按比例缩放到父页面给的空间里**。
# 为什么缩放放在 iframe 里做：iframe 是 sandbox="allow-scripts"（不带 allow-same-origin），
# 父页面读不到它的 document，只能靠 postMessage 把可用宽高传进来。
_RESIZE_SCRIPT = """
(function () {
  var MAX_H = 720;          // 父页面会通过 taaffy-fit 消息覆盖
  var MAX_W = 0;            // 0 = 用自己视口的宽度

  function naturalSize() {
    var b = document.body, d = document.documentElement;
    return {
      w: Math.max(b ? b.scrollWidth : 0, d ? d.scrollWidth : 0,
                  d ? d.clientWidth : 0, b ? b.offsetWidth : 0),
      h: Math.max(b ? b.scrollHeight : 0, d ? d.scrollHeight : 0,
                  d ? d.clientHeight : 0, b ? b.offsetHeight : 0)
    };
  }

  function resetTransform() {
    if (!document.body) return;
    document.body.style.transform = '';
    document.body.style.transformOrigin = '';
    document.body.style.width = '';
  }

  function report(h) {
    try {
      parent.postMessage({ type: 'taffy-artifact-height', height: Math.round(h) }, '*');
    } catch (e) { /* 父页面不给就算了 */ }
  }

  // 按可用宽高等比缩放：画得太大就整体缩小，长宽比不变，不会被裁掉
  function fit() {
    if (!document.body) return;
    resetTransform();
    var vw = MAX_W || window.innerWidth || document.documentElement.clientWidth || 360;
    var size = naturalSize();
    var scale = Math.min(1, vw / Math.max(1, size.w), MAX_H / Math.max(1, size.h));
    if (scale < 0.995) {
      // 先按缩放后的等效宽度排版，再整体缩放，避免文字被横向挤成一条
      document.body.style.transformOrigin = 'top left';
      document.body.style.width = Math.ceil(vw / scale) + 'px';
      document.body.style.transform = 'scale(' + scale + ')';
      var again = naturalSize();
      if (again.h > 0) {
        report(again.h * scale);
        return;
      }
    }
    report(size.h * scale);
  }

  window.addEventListener('message', function (ev) {
    var data = ev.data;
    if (!data || data.type !== 'taffy-artifact-fit') return;
    if (Number(data.maxHeight) > 0) MAX_H = Number(data.maxHeight);
    if (Number(data.width) > 0) MAX_W = Number(data.width);
    fit();
  });

  fit();
  window.addEventListener('load', fit);
  setTimeout(fit, 120);
  setTimeout(fit, 500);
  window.addEventListener('resize', fit);
})();
"""

# 产物里塞一点基础样式，免得模型没写 CSS 时太丑（模型自己的 CSS 会覆盖它）
_BASE_STYLE = """
html, body { margin: 0; padding: 0; }
body { min-height: 40px; font-family: -apple-system, "PingFang SC",
       "Microsoft YaHei", sans-serif; }
"""

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "render_html",
            "description": (
                "把一段 HTML 直接渲染成一张可视化卡片给用户看——画图、示意图、"
                "流程图、图表、海报、动画都用它，比用文字描述清楚一万倍。"
                "传完整的 HTML 片段或整页都行：CSS 写在 <style> 里，SVG 直接写，"
                "要动起来可以用内联 <script>（canvas 也行）。"
                "注意：不能加载外部资源（外链图片 / 字体 / 脚本 / CSS 都会被拦掉），"
                "所以图案请用 SVG / CSS / canvas 自己画，颜色和字体用系统自带的。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "html": {
                        "type": "string",
                        "description": "完整 HTML 内容（可以包含 <style> 和 <script>）",
                    },
                    "title": {
                        "type": "string",
                        "description": "这张图画的是什么，一句话，会显示在卡片标题上",
                    },
                },
                "required": ["html"],
            },
        },
    },
]


# ---------------- 产物存储 ----------------

_ARTIFACTS = {}
_ORDER = []
_LOCK = threading.Lock()


def _store(doc: str, title: str) -> str:
    aid = uuid.uuid4().hex[:16]
    with _LOCK:
        _ARTIFACTS[aid] = {"html": doc, "title": title, "ts": time.time()}
        _ORDER.append(aid)
        while len(_ORDER) > RENDER_LIMIT:
            old = _ORDER.pop(0)
            _ARTIFACTS.pop(old, None)
    # 图一画完就把 PNG 渲染好（后台线程）：App 第一次请求就能直接命中缓存，
    # 不用等 Chromium 冷启动那两三秒，也就不会因为超时退回 SVG
    if "<svg" in doc:
        threading.Thread(
            target=artifact_png, args=(aid, doc), daemon=True, name=f"png-{aid}"
        ).start()
    return aid


def get_artifact(aid: str):
    """给 web 层取产物用；内存里没有就去库里捞（刷新 / 重启之后还能看）。"""
    if not aid or not re.fullmatch(r"[0-9a-f]{16}", aid):
        return None
    item = _ARTIFACTS.get(aid)
    if item and time.time() - item["ts"] > RENDER_TTL:
        with _LOCK:
            _ARTIFACTS.pop(aid, None)
        item = None
    if item:
        return item
    try:                                  # 回落到数据库
        from .. import db

        with db.cursor() as conn:
            row = conn.execute(
                "SELECT html, title, created_at FROM artifacts WHERE id = ?", (aid,)
            ).fetchone()
        if row:
            return {"html": row["html"], "title": row["title"], "ts": row["created_at"]}
    except Exception:                     # noqa: BLE001
        return None
    return None


def persist_artifact(aid: str, session_id: str, user_id: int, title: str, html: str):
    """把产物写进库，这样刷新 / 重启后卡片还在。"""
    if not aid or not html:
        return False
    try:
        from .. import db

        with db.cursor() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO artifacts "
                "(id, session_id, user_id, title, html, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (aid, session_id or "", int(user_id or 0), title or "", html, time.time()),
            )
        return True
    except Exception:                     # noqa: BLE001
        return False


# ---------------- 产物栅格化（给 App 看的 PNG） ----------------

# 网页端是浏览器直接吃 SVG，App 端靠 androidsvg 解析——那套解析器对 `&gt;` / `&lt;`
# 这类字符实体处理不对，流程图里的 `X>0` 会变成 `X0`，而且 App 保存图片时拿到的是
# SVG 源码，相册解不出来就是一张白图。所以统一在这儿用 Chromium（跟网页端同一个引擎）
# 栅格化成 PNG：字形、换行、字体回退全部跟网页一模一样，App 端只当普通位图显示/保存。
PNG_DIR = os.path.join(PROJECT_ROOT, ".cache", "artifacts-png")
# 给全屏放大留余量：正常手机屏宽 1080px，2600px 的图放大 6 倍也不虚
PNG_MAX_WIDTH = 2600
# 缓存留多久（产物本身有 TTL，这里只防目录无限长）
PNG_KEEP_SECONDS = 7 * 24 * 3600
# 同时只允许一个 Chromium 在跑（这台机器 4 核 3.8G，还要留给聊天和接管浏览器）
_PNG_LOCK = threading.Lock()

_VIEWBOX = re.compile(
    r'<svg[^>]*?viewBox="\s*(-?[\d.]+)[\s,]+(-?[\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)', re.I)


def _svg_box(html_text: str):
    """从产物 HTML 里抠出 <svg> 的 viewBox；没有就返回 None（不是矢量图）。"""
    match = _VIEWBOX.search(html_text or "")
    if not match:
        return None
    try:
        _, _, width, height = (float(g) for g in match.groups())
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _png_prune():
    """顺手清掉过期的 PNG 缓存（7 天），别让 .cache 一直长。"""
    cutoff = time.time() - PNG_KEEP_SECONDS
    try:
        for entry in os.scandir(PNG_DIR):
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                try:
                    os.remove(entry.path)
                except OSError:
                    pass
    except OSError:
        pass


def artifact_png(aid: str, html_text: str):
    """把产物里的 <svg> 栅格化成 PNG，返回文件路径；失败/不是矢量图返回 None。

    第一次请求时渲染并落盘，之后直接读缓存（产物是不可变的，同一个 id 内容不会变）。
    """
    if not aid or not html_text:
        return None
    path = os.path.join(PNG_DIR, f"{aid}.png")
    if os.path.exists(path) and os.path.getsize(path) > 1024:
        return path
    box = _svg_box(html_text)
    if not box:
        return None
    width, height = box
    scale = min(4.0, max(1.5, PNG_MAX_WIDTH / width))
    view_w = int(min(PNG_MAX_WIDTH, max(600, round(width * scale))))
    view_h = int(round(height * view_w / width)) + 8

    with _PNG_LOCK:
        if os.path.exists(path) and os.path.getsize(path) > 1024:
            return path
        os.makedirs(PNG_DIR, exist_ok=True)
        # 临时文件名也必须以 .png 结尾：Playwright 靠扩展名决定截图格式
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp.png"
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
                try:
                    page = browser.new_page(
                        viewport={"width": view_w, "height": view_h},
                        device_scale_factor=1,
                    )
                    page.set_content(html_text, wait_until="load")
                    page.wait_for_timeout(160)          # 等字体/布局稳定
                    page.locator("svg").first.screenshot(path=tmp)
                finally:
                    browser.close()
            if not os.path.exists(tmp) or os.path.getsize(tmp) < 1024:
                raise RuntimeError("截图是空的")
            os.replace(tmp, path)
        except Exception as exc:                    # noqa: BLE001
            print(f"[artifact-png] {aid} 渲染失败：{type(exc).__name__}: {exc}", flush=True)
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return None
    _png_prune()
    print(f"[artifact-png] {aid} 已生成 {view_w}x{view_h}", flush=True)
    return path


# 存进消息正文里的产物标记：[[artifact:16位id|标题]]
_ARTIFACT_MARK_STRIP = re.compile(r"\n*\[\[artifact:[0-9a-f]{16}\|[^\]\n]*\]\]\n*")


def append_marks(text: str, items) -> str:
    """把这一轮产生的产物标记拼到消息末尾。"""
    marks = "".join(
        "\n\n[[artifact:%s|%s]]" % (aid, (title or "").replace("|", "/")[:40])
        for aid, title in (items or [])
    )
    return (text or "") + marks


def strip_marks(text: str):
    """喂给模型之前把产物标记去掉（模型不用看到这个）。"""
    if not text:
        return text or ""
    return _ARTIFACT_MARK_STRIP.sub("\n", text).strip()


# ---------------- 清洗 ----------------

def _sanitize(raw: str):
    """重建 DOM，去掉危险的东西。返回 (干净的整页 HTML, 风险清单)。"""
    findings = {}

    def hit(key, n=1):
        findings[key] = findings.get(key, 0) + n

    try:
        doc = lxml_html.document_fromstring(raw)
    except Exception:
        # 解析不了就当纯文本塞进去，绝不原样返回
        return (f"<!DOCTYPE html><html><body><pre>"
                f"{lxml_html.escape(raw)}</pre></body></html>"), {"parse-failed": 1}

    # 1. 删危险标签
    for tag in _DROP_TAGS:
        for el in doc.xpath(f"//{tag}"):
            hit(f"removed-{tag}")
            try:
                el.drop_tree()
            except Exception:
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)

    # 2. 外链脚本删掉（内联脚本留着，跑在沙箱里）
    for el in doc.xpath("//script[@src]"):
        hit("removed-script-src")
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)

    # 3. 属性清理：javascript: 这类协议、以及 form 提交动作
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            low = attr.lower()
            val = el.attrib.get(attr) or ""
            if low in ("srcset", "formaction", "action", "ping", "background"):
                del el.attrib[attr]
                hit("removed-attr-" + low)
                continue
            if low in ("href", "src", "data", "poster") and _BAD_SCHEMES.match(val):
                del el.attrib[attr]
                hit("removed-bad-scheme")

    # 4. 记录可疑特征（只记不拦，方便审计追溯）
    text = lxml_html.tostring(doc, encoding="unicode")
    for key, rx in _SUSPICIOUS:
        if rx.search(text):
            hit("flag-" + key)

    return text, findings


def _audit(entry: dict):
    """审计钩子：写 JSONL + journald 各一份。"""
    if not RENDER_AUDIT_ENABLED:
        return
    line = json.dumps(entry, ensure_ascii=False)
    try:
        os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    try:
        import logging
        logging.getLogger("taffy.render").info("render %s", line)
    except Exception:
        pass


# ---------------- 工具本体 ----------------

def render_html(html: str, title: str = "") -> str:
    """把 HTML 渲染成聊天里的一张卡片。返回给模型的回执是 JSON。"""
    raw = (html or "").strip()
    if not raw:
        return json.dumps({"error": "html 是空的，没东西可画"}, ensure_ascii=False)

    size = len(raw.encode("utf-8"))
    if size > RENDER_MAX_BYTES:
        _audit({"ts": int(time.time()), "verdict": "rejected", "reason": "too-large",
                "bytes": size, "limit": RENDER_MAX_BYTES})
        return json.dumps({
            "error": f"HTML 太大了（{size} 字节，上限 {RENDER_MAX_BYTES}）。"
                     f"简化一下再画，别塞这么多内容。"
        }, ensure_ascii=False)

    clean, findings = _sanitize(raw)

    # 把基础样式和「上报高度」的脚本塞进去
    head_extra = f"<style>{_BASE_STYLE}</style>"
    tail_extra = f"<script>{_RESIZE_SCRIPT}</script>"
    if "</head>" in clean:
        clean = clean.replace("</head>", head_extra + "</head>", 1)
    else:
        clean = head_extra + clean
    if "</body>" in clean:
        clean = clean.replace("</body>", tail_extra + "</body>", 1)
    else:
        clean = clean + tail_extra

    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    aid = _store(clean, title or "")
    final_size = len(clean.encode("utf-8"))

    _audit({
        "ts": int(time.time()),
        "verdict": "ok",
        "id": aid,
        "title": (title or "")[:80],
        "bytes": size,
        "final_bytes": final_size,
        "sha256": sha,
        "findings": findings,
    })

    return json.dumps({
        "ok": True,
        "artifact_id": aid,
        "title": title or "",
        "note": "已经渲染成卡片给用户看了，正文里不用再把 HTML 贴一遍，"
                "用一两句话说明你画了什么就行。",
    }, ensure_ascii=False)
