"""聊天上传的图片：校验 → 归一化 → 落盘 → 历史里留「标记 + 摘要」。

方案 B（对齐大厂做法，规模缩小版）：

1. **现场用**：归一化后的图直接进这一轮的多模态请求 —— 精度最高；
2. **落盘**：写到 `workspace/uploads/<id>.<ext>`，同时记进 `.cache/upload_index.json`；
3. **历史里只留标记**：这一轮跑完，把多模态大块换成
   `[图片 #id uploads/id.jpg：一句话摘要]`，省 token 也留了线索；
4. **最近 1~2 轮自动 rehydrate**：发请求前把最近的图重新塞回多模态，
   模型「看得见」，不用它自己判断要不要看；
5. **更久远的**：只有摘要，需要细节时人格会让它 `read_img uploads/<id>.jpg` 回看原图；
6. **20 分钟 TTL**：过期文件自动清掉（后台每 5 分钟扫一次），不在服务器上无限堆积。

校验仍然只认魔术字节 + 解码重编码（洗 polyglot、剥 EXIF）。
"""
import base64
import binascii
import json
import os
import re
import secrets
import time

from .config import PROJECT_ROOT
from .sandbox import WORKSPACE

UPLOAD_DIR = os.path.join(WORKSPACE, "uploads")
INDEX_PATH = os.path.join(PROJECT_ROOT, ".cache", "upload_index.json")

MAX_BYTES = 4 * 1024 * 1024
MAX_COUNT = 6
MAX_SIDE = 8000
MAX_AFTER_ENCODE = 3 * 1024 * 1024
TTL_SECONDS = 20 * 60          # 上传的图片只留 20 分钟，过期自动清掉
SUMMARY_MAX_TOKENS = 500

_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)
_DATA_URL = re.compile(r"^data:image/[a-zA-Z0-9.+-]{1,20};base64,(.+)$", re.S)


# ---------------- 校验 / 归一化 ----------------

def _sniff(data):
    for sig, ext in _SIGNATURES:
        if data.startswith(sig):
            return ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def _decode(data_url):
    m = _DATA_URL.match((data_url or "").strip())
    if not m:
        return None, "不是内联图片格式"
    try:
        data = base64.b64decode(m.group(1).strip(), validate=False)
    except (binascii.Error, ValueError):
        return None, "base64 解不开"
    if not data:
        return None, "内容是空的"
    if len(data) > MAX_BYTES:
        return None, f"超过 {MAX_BYTES // 1048576}MB 上限"
    return data, ""


def _normalize(data):
    """解码 + 重新编码：洗掉附加数据、剥掉 EXIF。返回 (bytes, 扩展名, 错误)。"""
    try:
        import io

        from PIL import Image
    except ImportError:
        ext = _sniff(data)
        return (data, ext, "") if ext else (None, "", "没装 Pillow，没法解码校验")

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:            # noqa: BLE001
        return None, "", f"解不开（{exc}）"
    if max(img.size) > MAX_SIDE:
        return None, "", f"尺寸太大（{img.size[0]}x{img.size[1]}，上限 {MAX_SIDE}px）"


    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info)
    buf = io.BytesIO()
    try:
        if has_alpha:
            img.save(buf, "PNG", optimize=True)
            ext = "png"
        else:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(buf, "JPEG", quality=90, optimize=True)
            ext = "jpg"
    except Exception as exc:            # noqa: BLE001
        return None, "", f"重编码失败（{exc}）"

    out = buf.getvalue()
    if len(out) > MAX_AFTER_ENCODE:
        try:
            scale = (MAX_AFTER_ENCODE / len(out)) ** 0.5
            img2 = img.resize((max(1, int(img.size[0] * scale)),
                               max(1, int(img.size[1] * scale))))
            buf = io.BytesIO()
            img2.convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
            out, ext = buf.getvalue(), "jpg"
        except Exception:               # noqa: BLE001
            pass
    return out, ext, ""


# ---------------- 索引 ----------------

def _load_index():
    try:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_index(index):
    try:
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        tmp = INDEX_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False)
        os.replace(tmp, INDEX_PATH)
    except OSError:
        pass


def owner_of(aid):
    """这张图是哪个用户传的（没记录返回 None）。"""
    item = _load_index().get(aid or "")
    return (item or {}).get("owner")


def sweep(ttl=TTL_SECONDS):
    """删掉超过 TTL 的图片和索引项。返回删了几个。"""
    now = time.time()
    index = _load_index()
    removed = 0
    for aid, item in list(index.items()):
        path = os.path.join(UPLOAD_DIR, item.get("file") or "")
        old = now - float(item.get("ts") or 0) > ttl
        if old or not os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
            index.pop(aid, None)
            removed += 1
    # 目录里没登记的孤儿文件也顺手清掉
    try:
        known = {item.get("file") for item in index.values()}
        for name in os.listdir(UPLOAD_DIR):
            if name not in known:
                try:
                    os.remove(os.path.join(UPLOAD_DIR, name))
                except OSError:
                    pass
    except OSError:
        pass
    if removed:
        _save_index(index)
    return removed


# ---------------- 存 / 取 ----------------

def save(data_urls, owner=None):
    """校验并落盘。返回 (refs, notes)。

    refs 里每项：{"id", "path"（相对 workspace）, "mime"}
    """
    urls = [u for u in (data_urls or []) if isinstance(u, str) and u.strip()]
    if not urls:
        return [], []
    if len(urls) > MAX_COUNT:
        urls = urls[:MAX_COUNT]

    sweep()
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    index = _load_index()
    refs, notes = [], []

    for number, url in enumerate(urls, 1):
        data, why = _decode(url)
        if data is None:
            notes.append(f"第 {number} 张被拒：{why}")
            continue
        if not _sniff(data):
            notes.append(f"第 {number} 张被拒：不是真图片（魔术字节对不上）")
            continue
        clean, ext, why = _normalize(data)
        if clean is None:
            notes.append(f"第 {number} 张被拒：{why}")
            continue

        aid = secrets.token_hex(6)
        fname = aid + "." + ext
        full = os.path.join(UPLOAD_DIR, fname)
        try:
            with open(full, "wb") as fh:
                fh.write(clean)
        except OSError as exc:
            notes.append(f"第 {number} 张存不下来：{exc}")
            continue

        index[aid] = {"file": fname, "ts": time.time(), "size": len(clean),
                      "mime": "image/png" if ext == "png" else "image/jpeg",
                      "owner": owner}
        refs.append({"id": aid, "path": "uploads/" + fname,
                     "mime": index[aid]["mime"]})
    _save_index(index)
    return refs, notes


def data_url(aid):
    """按 id 把图读回来，转成 data URL（rehydrate 用）。取不到返回空串。"""
    item = _load_index().get(aid)
    if not item:
        return ""
    path = os.path.join(UPLOAD_DIR, item.get("file") or "")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return ""
    if not raw:
        return ""
    return "data:" + (item.get("mime") or "image/jpeg") + ";base64," + \
        base64.b64encode(raw).decode("ascii")


# ---------------- 摘要 ----------------

def summarize(refs):
    """给这一批图各写一句 40 字以内的摘要（一次视觉调用，逐行返回）。

    拿不到就返回空字典，标记里只留路径，不影响主流程。
    """
    if not refs:
        return {}
    try:
        from . import llm, settings

        parts = [{
            "type": "text",
            "text": ("下面有 %d 张图。请用中文给每张写一句 40 字以内的摘要，"
                     "按顺序一行一张，格式：1. xxx\n不要客套，不要多余说明。" % len(refs)),
        }]
        for ref in refs:
            url = data_url(ref["id"])
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
        if len(parts) == 1:
            return {}
        text = llm.complete(
            [{"role": "user", "content": parts}],
            model=settings.vision_model(),
            api_key=settings.key_for(vision=True),
            base_url=settings.base_url_for(vision=True),
            max_tokens=SUMMARY_MAX_TOKENS,
            thinking=False,
        )
    except Exception:              # noqa: BLE001
        return {}

    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    out = {}
    for index, ref in enumerate(refs):
        if index >= len(lines):
            break
        line = re.sub(r"^\d+[.、)）]\s*", "", lines[index]).strip()
        if line:
            out[ref["id"]] = line[:80]
    return out


def marker_text(refs, summaries=None, notes=None):
    """拼进历史的那段标记文本。"""
    summaries = summaries or {}
    lines = []
    for ref in refs or []:
        summary = summaries.get(ref["id"]) or "（没生成摘要）"
        lines.append(f"[图片 #{ref['id']} {ref['path']}：{summary}]")
    if notes:
        lines.append("（有图片没能收下：" + "；".join(notes) + "）")
    return "\n".join(lines)
