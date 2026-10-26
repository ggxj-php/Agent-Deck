"""图形验证码：注册时用，挡脚本批量注册。

- Pillow 画图（服务器上装了 DejaVu 字体，用它渲染；没有字体就退回默认字体）
- 答案只放**内存**：这玩意量大、寿命短（5 分钟）、一次一用，带边界的字典最合适；
  重启丢光也无所谓（前端刷新一下就有新的）
- 一次一用、错 5 次作废、按 IP 限速（在 app.py 里）
"""
import base64
import hashlib
import io
import random
import secrets
import threading
import time

TTL = 300              # 5 分钟
MAX_ATTEMPTS = 5       # 同一个码最多试几次
MAX_ENTRIES = 5000     # 内存里最多存这么多条，超了清最老的

# 去掉 0/O/1/I/B/8 这些容易看错的
ALPHABET = "ACDEFGHJKLMNPQRSTUVWXY2345679"
CODE_LEN = 4
WIDTH, HEIGHT = 132, 46

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)

_lock = threading.Lock()
_store = {}


def _hash(code):
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def _font(size):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:                     # noqa: BLE001
        return None


def _draw(code):
    """画一张 132×46 的验证码 PNG（字符带随机旋转 + 干扰线 + 噪点）。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (WIDTH, HEIGHT), (255, 248, 252))
    draw = ImageDraw.Draw(img)
    font = _font(30)

    step = (WIDTH - 24) / max(1, len(code))
    for index, char in enumerate(code):
        glyph = Image.new("RGBA", (40, 46), (0, 0, 0, 0))
        glyph_draw = ImageDraw.Draw(glyph)
        color = (random.randint(50, 140), random.randint(30, 110), random.randint(70, 160))
        try:
            glyph_draw.text((6, 2), char, font=font, fill=color + (255,))
        except TypeError:                 # 老版本 Pillow 的 fill 不要 alpha
            glyph_draw.text((6, 2), char, font=font, fill=color)
        glyph = glyph.rotate(random.randint(-20, 20), resample=Image.BICUBIC)
        img.paste(glyph, (int(10 + index * step), random.randint(0, 4)), glyph)

    # 干扰线 / 噪点
    for _ in range(4):
        draw.line(
            [(random.randint(0, WIDTH), random.randint(0, HEIGHT)),
             (random.randint(0, WIDTH), random.randint(0, HEIGHT))],
            fill=(random.randint(150, 220), random.randint(150, 220), random.randint(160, 230)),
            width=1,
        )
    for _ in range(110):
        draw.point((random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1)),
                   fill=(random.randint(170, 230), random.randint(170, 230), random.randint(180, 240)))

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _sweep(now):
    """清过期的；还太多就清最早的一批。调用时已经持锁。"""
    for key, item in list(_store.items()):
        if item["expire"] < now or item["tries"] >= MAX_ATTEMPTS:
            _store.pop(key, None)
    if len(_store) > MAX_ENTRIES:
        for key in list(_store)[:len(_store) - MAX_ENTRIES]:
            _store.pop(key, None)


def new(ip=""):
    """生成一张新验证码：返回 (id, PNG bytes)。"""
    code = "".join(random.choice(ALPHABET) for _ in range(CODE_LEN))
    ident = secrets.token_urlsafe(12)
    png = _draw(code)
    now = time.time()
    with _lock:
        _sweep(now)
        _store[ident] = {"hash": _hash(code), "expire": now + TTL,
                         "tries": 0, "ip": ip or ""}
    return ident, png


def new_data_url(ip=""):
    """给前端直接用的 data URL 版本。"""
    ident, png = new(ip)
    return ident, "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def verify(ident, answer):
    """校验，返回 (ok, 原因)。

    对了一次就作废（防重放）；错了次数 +1，超过上限直接作废。
    """
    ident = (ident or "").strip()
    answer = (answer or "").strip()
    if not ident or not answer:
        return False, "要填图形验证码"
    now = time.time()
    with _lock:
        item = _store.get(ident)
        if not item:
            return False, "验证码过期了，点图换一张"
        if item["expire"] < now:
            _store.pop(ident, None)
            return False, "验证码过期了，点图换一张"
        if item["tries"] >= MAX_ATTEMPTS:
            _store.pop(ident, None)
            return False, "这个验证码试太多次了，换一张"
        item["tries"] += 1
        if not secrets.compare_digest(item["hash"], _hash(answer)):
            return False, "图形验证码不对"
        _store.pop(ident, None)           # 一次一用
        return True, ""


def stats():
    with _lock:
        return {"stored": len(_store), "ttl": TTL, "attempts": MAX_ATTEMPTS}
