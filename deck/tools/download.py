"""临时下载链接：Agent 把 workspace 里的文件做成一条带时效的下载地址给用户。

安全设计：
  1. token 32 位随机十六进制（不可枚举）；
  2. 文件路径必须落在 workspace 内（用 sandbox.safe_path 校验，防 ../ 与符号链接越界）；
  3. 绑定上传者/会话所属用户：只有本人登录态能下载，别人拿到链接也打不开；
  4. 20 分钟过期，过期即失效，同时有后台定期清理；
  5. 响应强制 attachment + nosniff + no-store + 严格 CSP，避免被当网页执行。
"""
import json
import os
import secrets
import threading
import time

from ..sandbox import WORKSPACE, safe_path

TTL_SECONDS = 20 * 60          # 20 分钟后失效
MAX_ENTRIES = 500              # 内存里最多留这么多条，超出按最早丢

_LOCK = threading.Lock()
_LINKS = {}                    # token -> {"path": 相对路径, "name": 文件名, "owner": 用户 id, "expires": 时间戳}
_ORDER = []                    # 按创建顺序，便于按上限淘汰

_OWNER = threading.local()     # 当前请求的用户 id（core 在跑工具前 set_owner）


def set_owner(user_id):
    """core 每次提问前设置，保证生成的链接只属于当前用户。"""
    _OWNER.value = user_id


def current_owner():
    return getattr(_OWNER, "value", None)


def sweep(now=None):
    """清掉过期条目（后台线程 + 每次访问都会顺手调）。"""
    now = time.time() if now is None else now
    removed = 0
    with _LOCK:
        for token, item in list(_LINKS.items()):
            if item["expires"] <= now:
                _LINKS.pop(token, None)
                removed += 1
        while _ORDER:
            token = _ORDER[0]
            if token in _LINKS:
                break
            _ORDER.pop(0)
    return removed


def _looks_inside(rel_path, full):
    """确认解析后的真实路径还在 workspace 里（防符号链接跳出）。"""
    root = os.path.realpath(WORKSPACE)
    return os.path.realpath(full).startswith(root + os.sep)


def lookup(token, user_id):
    """按 token 取下载信息；过期 / 不存在 / 不是本人一律返回 None。"""
    sweep()
    token = str(token or "")
    if not token or len(token) > 64:
        return None
    with _LOCK:
        item = _LINKS.get(token)
        if not item:
            return None
        if item["expires"] <= time.time():
            _LINKS.pop(token, None)
            return None
        if item["owner"] is not None and user_id is not None and int(item["owner"]) != int(user_id):
            return None
        return dict(item)


def make_download_link(path: str) -> str:
    """把 workspace 里的文件做成 20 分钟有效的下载链接（返回 JSON 回执给模型）。"""
    raw = str(path or "").strip()
    if not raw:
        return json.dumps({"error": "要传文件路径（相对 workspace）"}, ensure_ascii=False)
    try:
        full = safe_path(raw)
    except Exception as exc:                   # noqa: BLE001
        return json.dumps({"error": f"路径不合法：{exc}"}, ensure_ascii=False)
    if not _looks_inside(raw, full) or not os.path.isfile(full):
        return json.dumps({"error": "这个文件不存在（只能给 workspace 里的文件生成下载链接）"},
                          ensure_ascii=False)
    size = os.path.getsize(full)
    if size > 50 * 1024 * 1024:
        return json.dumps({"error": "文件超过 50MB，不方便走临时链接"}, ensure_ascii=False)

    rel = os.path.relpath(full, WORKSPACE).replace(os.sep, "/")
    name = os.path.basename(full)
    token = secrets.token_hex(16)
    owner = current_owner()
    item = {"path": rel, "name": name, "owner": owner,
            "expires": time.time() + TTL_SECONDS, "size": size}
    with _LOCK:
        _LINKS[token] = item
        _ORDER.append(token)
        while len(_ORDER) > MAX_ENTRIES:
            old = _ORDER.pop(0)
            _LINKS.pop(old, None)
    return json.dumps({
        "ok": True,
        "url": f"/d/{token}",
        "full_url": f"/d/{token}/{name}",
        "filename": name,
        "expires_in_minutes": TTL_SECONDS // 60,
        "note": ("下载链接 %d 分钟内有效、只有当前用户能打开，别把它当成长期地址；"
                 "把链接原样发给用户就行。" % (TTL_SECONDS // 60)),
    }, ensure_ascii=False)


def file_path(item):
    """给下载接口用：返回真实绝对路径（已校验在 workspace 内）。"""
    full = safe_path(item["path"])
    if not _looks_inside(item["path"], full):
        return ""
    return full


SPECS = [
    {
        "type": "function",
        "function": {
            "name": "make_download_link",
            "description": (
                "把 workspace 里的文件做成一条有时效的下载链接发给用户（20 分钟有效，只有当前用户能打开）。"
                "用户要「下载 / 保存 / 导出」某个你生成或改好的文件时用它；"
                "路径写相对 workspace 的相对路径（比如 notes/report.md）。"
                "返回里带 url，把它原样写进回答给用户即可。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 workspace 的文件路径"},
                },
                "required": ["path"],
            },
        },
    },
]
