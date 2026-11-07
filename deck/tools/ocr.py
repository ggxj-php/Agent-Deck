"""文字识别（OCR）：调自建的 xiaomi-orc-0 服务，把图片里的字转成文本。

和 read_img 的分工：read_img 是让视觉模型「看图说话」，给的是摘要，整页字抄不全；
这里是真的逐字识别，适合扫描件、票据 / 发票、表单、截图里的小字、手写。

这个模型准确率一般（数字、编号、金额、人名最容易错），所以返回里带一句提醒，
让模型自己核对再引用，别直接把识别结果当事实念给用户。

接口地址和 token 都存在后台设置里（「接口设置 → 文字识别」），改完立刻生效。

自建服务的约定：
    POST <url>?token=xxx&mode=text|doc[&max_tokens=N]
    form-data: file=<图片文件>
    200 {"ok": true, "text": "...", "seconds": 25.3, "mode": "text", "max_tokens": 512}
    401 {"ok": false, "error": "bad token"}
    400 {"ok": false, "error": "empty or too large (max 30MB)"}
    500 {"ok": false, "error": "model failed"}
"""
import os

import requests

from .. import settings
from ..sandbox import safe_path
from .docs import _sniff_mime

TIMEOUT = 180                  # 实测一张小票 20~30 秒，整页文档更久，别卡太紧
MAX_BYTES = 30 * 1024 * 1024   # 服务端上限 30MB
MIN_BYTES = 1024               # 小于 1KB 的「图」不是正经图
DEFAULT_TOKENS = 2048          # mode=doc 不给 max_tokens 时的兜底
MAX_TOKENS_CAP = 8192
MAX_CHARS = 12000              # 一次最多回多少字（再长模型也读不完）
MODES = ("text", "doc")


def _endpoint():
    url = settings.ocr_url().strip()
    if not url:
        raise RuntimeError("还没配 OCR 接口地址（后台「接口设置 → 文字识别」里填一下）")
    return url


def ocr_image(path: str, mode: str = "text", max_tokens: int = 0) -> str:
    """把工作区里的一张图交给 OCR 服务，返回识别出的文字。"""
    path = str(path or "").strip()
    if not path:
        return "错误：path 不能为空（填工作区里的图片路径，比如 uploads/xxx.jpg）"
    mode = (str(mode or "text").strip().lower() or "text")
    if mode not in MODES:
        return f"错误：mode 只能填 text（默认，单屏/短内容）或 doc（整页文档），收到的是 {mode}"
    try:
        full = safe_path(path)
    except ValueError as exc:
        return f"错误：{exc}"
    if not os.path.isfile(full):
        return f"错误：文件不存在 {path}"
    size = os.path.getsize(full)
    if size > MAX_BYTES:
        return (f"错误：图片 {size / 1048576:.1f} MB，超过 OCR 服务的 30 MB 上限，"
                f"先压小一点再识别")
    if size < MIN_BYTES:
        return f"错误：图片只有 {size} 字节，太小了，多半不是正经图"
    try:
        with open(full, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return f"读不了这个文件：{exc}"
    mime = _sniff_mime(data)
    if not mime:
        return "错误：这不像是图片（魔数不认识）。OCR 只收 png / jpg / gif / webp / bmp"
    try:
        url = _endpoint()
    except RuntimeError as exc:
        return f"错误：{exc}"

    params = {"mode": mode}
    token = settings.ocr_token().strip()
    if token:
        params["token"] = token
    want = 0
    try:
        want = int(max_tokens or 0)
    except (TypeError, ValueError):
        want = 0
    if mode == "doc" and not want:
        want = DEFAULT_TOKENS
    if want:
        params["max_tokens"] = max(256, min(want, MAX_TOKENS_CAP))

    try:
        resp = requests.post(url, params=params,
                             files={"file": (os.path.basename(full), data, mime)},
                             timeout=TIMEOUT)
    except requests.Timeout:
        return (f"错误：OCR 服务 {TIMEOUT} 秒都没返回——多半是图太大或者服务在排队，"
                f"过一会儿再试，或者先压小图")
    except requests.RequestException as exc:
        return f"错误：连不上 OCR 服务（{url}）：{exc}"
    try:
        payload = resp.json()
    except ValueError:
        return (f"错误：OCR 服务返回的不是 JSON（HTTP {resp.status_code}）："
                f"{resp.text[:160]}")

    if resp.status_code == 401:
        return "错误：OCR token 不对，让管理员到后台「接口设置 → 文字识别」改一下"
    if not payload.get("ok"):
        detail = str(payload.get("error") or resp.text[:160])
        if resp.status_code == 400:
            return f"错误：OCR 服务不收这张图（{detail}）"
        return (f"错误：OCR 没认出来（HTTP {resp.status_code}，{detail}）。"
                f"换张更清晰、更正的图再试一次；纯照片/图形本来就没字，那种用 read_img")

    text = str(payload.get("text") or "").strip()
    if not text:
        return "这张图 OCR 一个字都没认出来（可能没文字、太糊或者角度太歪）。要看内容用 read_img"
    clipped = ""
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
        clipped = f"\n…（太长，只留了前 {MAX_CHARS} 字；要全量就分区域识别）"
    secs = payload.get("seconds")
    cost = f"，耗时 {secs:.1f}s" if isinstance(secs, (int, float)) else ""
    return (f"【{path}】OCR 结果（xiaomi-orc-0，mode={payload.get('mode') or mode}{cost}）\n"
            f"{text}{clipped}\n"
            f"（提醒：OCR 可能有错字，数字、编号、金额、日期、人名尤其容易错，表格还可能错行；"
            f"引用进回答前对照原图核一遍，拿不准就跟用户确认。）")


SPECS = [
    {"type": "function", "function": {
        "name": "ocr_image",
        "description": (
            "把图片里的文字逐字识别出来（OCR，自建 xiaomi-orc-0）。适合：整页扫描件 / 截图、"
            "票据 / 发票 / 菜单 / 表单、代码截图、手写、表格里的小字——这些用 read_img 只能拿到摘要，"
            "抄不全，用它。只要大意、找按钮在哪就别用（一次要 20~30 秒）。"
            "注意：这个模型准确率一般，数字 / 编号 / 金额 / 日期 / 人名容易错，"
            "抄进回答前要对照原图核对，拿不准就跟用户确认。"
        ),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string",
                     "description": "工作区里的图片路径，例如 uploads/abc.jpg"},
            "mode": {"type": "string",
                     "description": "text=默认，单屏或短内容；doc=整页文档/长文档，版面更完整"},
            "max_tokens": {"type": "integer",
                           "description": "可选。doc 模式文字很多时调大（比如 4096），"
                                          "默认 2048，最大 8192"}},
            "required": ["path"]}}},
]
