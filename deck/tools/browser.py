"""把浏览器操控暴露成工具：打开、快照、点击、输入、滚动、取正文、抓图、截图。"""
import json

from .. import browser
from . import download as download_tool


def _download_link(path):
    """把 make_download_link 的 JSON 回执解析成真正的下载地址。"""
    try:
        raw = download_tool.make_download_link(path)
    except Exception as exc:                 # noqa: BLE001
        print(f"[browser] 生成下载链接失败：{exc}")
        return ""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data.get("full_url") or data.get("url") or ""
    except (ValueError, TypeError):
        pass
    return str(raw or "")


def _owner():
    return download_tool.current_owner() or 0


def _dumps(payload, limit=9000):
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > limit:
        text = text[:limit] + "…（结果太长已截断）"
    return text


def _elements_text(data):
    """把快照压成模型好读的表格文本，比 raw JSON 省 token。"""
    lines = [f"URL: {data.get('url', '')}", f"标题: {data.get('title', '')}", "元素："]
    for item in data.get("elements") or []:
        parts = [f"[{item.get('ref')}]", f"<{item.get('tag')}>"]
        if item.get("role"):
            parts.append(f"role={item['role']}")
        if item.get("type"):
            parts.append(f"type={item['type']}")
        if item.get("text"):
            parts.append(item["text"])
        if item.get("href"):
            parts.append(f"href={item['href'][:80]}")
        lines.append(" ".join(parts))
    head = (data.get("text_head") or "").strip()
    if head:
        lines.append("\n页面正文开头（可能被截断）：")
        lines.append(head)
    return "\n".join(lines)


def browser_open(url: str) -> str:
    """打开网址，返回页面快照（可交互元素带 ref 编号）。"""
    try:
        data = browser.manager().open(_owner(), url)
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    return _elements_text(data)


def browser_snapshot() -> str:
    """重新拿一次当前页面的快照。"""
    try:
        data = browser.manager().snapshot(_owner())
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    return _elements_text(data)


def browser_click(ref: int) -> str:
    """点击快照里编号为 ref 的元素。"""
    try:
        data = browser.manager().click(_owner(), ref)
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    if not data.get("ok"):
        return f"错误：{data.get('error')}"
    return _elements_text(data)


def browser_type(ref: int, text: str) -> str:
    """往编号为 ref 的输入框/文本域里填内容。"""
    try:
        data = browser.manager().type(_owner(), ref, text)
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    if not data.get("ok"):
        return f"错误：{data.get('error')}"
    return _dumps({"ok": True, "ref": ref, "filled": len(str(text))})


def browser_key(key: str) -> str:
    """按一个键（Enter / Tab / Escape / ArrowDown…），返回操作后的快照。"""
    try:
        data = browser.manager().key(_owner(), key)
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    return _elements_text(data)


def browser_scroll(dy: int = 700) -> str:
    """向下滚动 dy 像素（负数向上），返回滚动后的快照。"""
    try:
        data = browser.manager().scroll(_owner(), dy)
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    return _elements_text(data)


def browser_extract() -> str:
    """把当前页面正文纯文本取出来（最多 6000 字）。"""
    try:
        data = browser.manager().extract(_owner())
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    text = data.get("text") or ""
    note = "（内容过长，已截断）" if data.get("truncated") else ""
    return f"URL: {data.get('url')}\n标题: {data.get('title')}{note}\n\n{text}"


def browser_images() -> str:
    """抓当前页面上的所有图片，打包成 zip 并给下载链接。"""
    try:
        data = browser.manager().images(_owner())
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    if not data.get("ok"):
        return f"错误：{data.get('error')}"
    zip_path = data.get("zip_path") or ""
    link = _download_link(zip_path) if zip_path else ""
    return _dumps({
        "ok": True,
        "count": data.get("count"),
        "files": data.get("files"),
        "download_url": link or "（下载链接生成失败，但文件已保存）",
        "zip_path": zip_path,
        "page_url": data.get("page_url"),
    })


def browser_screenshot() -> str:
    """截当前页面（可视区域）并给下载链接。"""
    try:
        data = browser.manager().screenshot(_owner())
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    link = _download_link(data.get("path") or "")
    return _dumps({"ok": True, "url": data.get("url"), "title": data.get("title"),
                   "download_url": link or "（下载链接生成失败）"})


def browser_close() -> str:
    """关掉当前用户的浏览器（释放内存）。"""
    try:
        browser.manager().close(_owner())
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    return "浏览器已关闭"


SPECS = [
    {"type": "function", "function": {
        "name": "browser_open",
        "description": "用真实浏览器打开网址（能跑 JS、能登录、能点页面），返回带 ref 编号的可交互元素快照。"
                       "需要操作网页（登录、点击、填表、抓图）时用它，纯读文章优先用 open_url。",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "http/https 网址"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "browser_snapshot",
        "description": "重新获取当前页面快照（元素 ref 会变，点击前建议先看最新快照）。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "browser_click",
        "description": "点击快照里编号为 ref 的元素，返回点击后的页面快照。",
        "parameters": {"type": "object", "properties": {
            "ref": {"type": "integer", "description": "元素编号（来自 browser_open / browser_snapshot）"}},
            "required": ["ref"]}}},
    {"type": "function", "function": {
        "name": "browser_type",
        "description": "往编号为 ref 的输入框里填文字（填完可能需要 browser_key 按 Enter）。",
        "parameters": {"type": "object", "properties": {
            "ref": {"type": "integer", "description": "输入框编号"},
            "text": {"type": "string", "description": "要填的内容"}}, "required": ["ref", "text"]}}},
    {"type": "function", "function": {
        "name": "browser_key",
        "description": "在当前页面按一个键：Enter / Tab / Escape / ArrowDown 等。",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "键名，比如 Enter"}}, "required": ["key"]}}},
    {"type": "function", "function": {
        "name": "browser_scroll",
        "description": "滚动页面（懒加载图片、加载更多内容时用），返回新快照。",
        "parameters": {"type": "object", "properties": {
            "dy": {"type": "integer", "description": "滚动像素，默认 700，负数向上"}}}}},
    {"type": "function", "function": {
        "name": "browser_extract",
        "description": "取当前页面正文纯文本（最多 6000 字），适合读文章/帖子内容。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "browser_images",
        "description": "抓当前页面上的所有图片（含懒加载图、背景图、视频封面），打包 zip 并给下载链接。"
                       "抓小红书/微博图集、文章配图就用它。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "browser_screenshot",
        "description": "截当前页面可视区域，返回图片下载链接。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "browser_close",
        "description": "关掉浏览器释放内存（任务做完或长时间不用时调用）。",
        "parameters": {"type": "object", "properties": {}}}},
]
