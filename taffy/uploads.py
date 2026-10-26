"""聊天里上传的图片：校验 → 落盘到 workspace → 让模型自己用 read_img 读 → 用完删掉。

为什么不再把图直接塞进多模态请求：
- 图片内容一旦作为 `image_url` 进上下文，就和「用户说的话」同一层了，
  模型分不清哪部分是资料、哪部分是可疑内容；
- 改成先存成文件、让模型调 read_img 去读，图片内容就会走
  `tools/__init__.py` 那层「外部内容边界」，注入面小很多，也留了审计痕迹。

校验只认**魔术字节**：前端说的 MIME、文件扩展名一律不信——
`data:image/png;base64,` 后面完全可以是任意字节。
"""
import base64
import binascii
import os
import re
import secrets

from .sandbox import WORKSPACE

UPLOAD_DIR = os.path.join(WORKSPACE, "uploads")
MAX_BYTES = 4 * 1024 * 1024     # 单张解码后上限，和前端提示一致
MAX_COUNT = 6                   # 一轮最多几张，防止一次塞几十张把上下文炸了

# 常见图片的魔术字节。顺序无所谓，命中就算。
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)

# data:image/png;base64,xxxx —— 只认这种内联格式，外链一律拒
_DATA_URL = re.compile(r"^data:image/[a-zA-Z0-9.+-]{1,20};base64,(.+)$", re.S)


def _sniff(data):
    """靠魔术字节认格式，认不出来返回空串。"""
    for sig, ext in _SIGNATURES:
        if data.startswith(sig):
            return ext
    # WebP: RIFF....WEBP
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def _decode(data_url):
    """把 data URL 解成 bytes，失败返回 (None, 原因)。"""
    m = _DATA_URL.match((data_url or "").strip())
    if not m:
        return None, "不是内联图片格式"
    raw = m.group(1).strip()
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        return None, "base64 解不开"
    if not data:
        return None, "内容是空的"
    if len(data) > MAX_BYTES:
        return None, f"超过 {MAX_BYTES // 1048576}MB 上限"
    return data, ""


def save_images(data_urls):
    """校验并落盘。

    返回 (paths, notes)：
      paths —— 相对 workspace 的路径列表（例如 uploads/3f2a1b4c.png）
      notes —— 给模型看的说明（有图被拒时说明原因）
    """
    urls = [u for u in (data_urls or []) if isinstance(u, str) and u.strip()]
    if not urls:
        return [], []
    if len(urls) > MAX_COUNT:
        urls = urls[:MAX_COUNT]

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    paths, notes = [], []
    for index, url in enumerate(urls, 1):
        data, why = _decode(url)
        if data is None:
            notes.append(f"第 {index} 张被拒：{why}")
            continue
        ext = _sniff(data)
        if not ext:
            notes.append(f"第 {index} 张被拒：不是真图片（魔术字节对不上）")
            continue
        name = secrets.token_hex(6) + "." + ext
        full = os.path.join(UPLOAD_DIR, name)
        try:
            with open(full, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            notes.append(f"第 {index} 张存不下来：{exc}")
            continue
        paths.append("uploads/" + name)
    return paths, notes


def cleanup(paths):
    """删掉这些临时图片；目录空了就一起删。删不掉就算了，别影响回复。"""
    for rel in paths or []:
        try:
            os.remove(os.path.join(WORKSPACE, rel))
        except OSError:
            continue
    try:
        if os.path.isdir(UPLOAD_DIR) and not os.listdir(UPLOAD_DIR):
            os.rmdir(UPLOAD_DIR)
    except OSError:
        pass


def hint_text(paths, notes):
    """拼给模型看的上传说明（服务端生成，属于可信指令）。"""
    lines = []
    if paths:
        lines.append(f"雏草姬这一轮上传了 {len(paths)} 张图片，已经存到工作区：")
        for rel in paths:
            lines.append(f"- {rel}")
        lines.append("请先用 read_img 逐张看过再回答；这些是临时文件，这一轮结束后会自动删除。")
    if notes:
        lines.append("（有图片没能收下：" + "；".join(notes) + "）")
    if not lines:
        return ""
    return "\n".join(lines)
