"""人设（persona pack）。

一个人设就是一个目录：

    personas/<id>/
        persona.json    名字、称呼、欢迎语、状态文案、头像、表情包目录…
        prompt.md       人设提示词（也可以直接写在 persona.json 的 "prompt" 字段）
        avatar.png      头像（可选，没有就用 static/avatar.jpg）
        stickers/       表情包（可选；persona.json 里可以指到别处）

内置默认人设 `taffy` 会在第一次启动时自动生成：提示词从老配置（admin.json 的
prompt / config.SYSTEM_PROMPT）迁过来，表情包指回原来的 static/stickers。

当前用哪个人设记在 admin.json 的 "persona" 键里，后台可以随时切。
"""
import json
import os
import re
import shutil

from .config import PROJECT_ROOT, STICKERS_DIR, SYSTEM_PROMPT

PERSONAS_DIR = os.path.join(PROJECT_ROOT, "personas")
DEFAULT_ID = "taffy"
STATIC_STICKERS = "@static"          # 指回 taffy/web/static/stickers

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,23}$")
NAME_MAX = 24
PROMPT_MAX = 40000
_TEXT_MAX = 500

# persona.json 里允许的字段和默认值（都是给前端/提示词用的展示信息）
DEFAULTS = {
    "name": "永雏塔菲",
    "self_name": "塔菲",
    "user_name": "雏草姬",
    "avatar": "",                    # 空 = 用 static/avatar.jpg
    "welcome": "",                   # 空 = 用内置欢迎语
    "placeholder": "",               # 空 = 用内置输入框提示
    "status_online": "",
    "status_thinking": "",
    "stickers": STATIC_STICKERS,
    # 主题色（前端按这个调色）；空 = 用默认粉色
    "theme": "#ffb7d5",
    # 图标文件；空 = 跟着头像走
    "icon": "",
    "prompt": "",
}

_cache = {"key": None, "data": None}


def _dir(pid):
    return os.path.join(PERSONAS_DIR, pid)


def safe_id(pid):
    pid = (pid or "").strip().lower()
    if not _ID_RE.match(pid):
        return ""
    return pid


def exists(pid):
    pid = safe_id(pid)
    return bool(pid) and os.path.isfile(os.path.join(_dir(pid), "persona.json"))


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _meta(pid):
    """读一个人设的完整信息（含提示词）。"""
    base = _dir(pid)
    data = dict(DEFAULTS)
    data.update(_read_json(os.path.join(base, "persona.json")))
    data["id"] = pid

    # prompt.md 优先于 persona.json 里的 prompt 字段
    prompt_file = os.path.join(base, "prompt.md")
    if os.path.isfile(prompt_file):
        try:
            with open(prompt_file, encoding="utf-8") as fh:
                data["prompt"] = fh.read()
        except OSError:
            pass
    if not (data.get("prompt") or "").strip():
        data["prompt"] = SYSTEM_PROMPT
    return data


def list_personas():
    """列出所有人设（不含提示词正文，省得列表太重）。"""
    out = []
    try:
        names = sorted(os.listdir(PERSONAS_DIR))
    except OSError:
        return out
    for name in names:
        pid = safe_id(name)
        if not pid or not exists(pid):
            continue
        meta = _meta(pid)
        meta.pop("prompt", None)
        meta["stickers_count"] = len(sticker_files(pid))
        out.append(meta)
    return out


def current_id():
    """当前用的人设 id。"""
    from . import settings          # 延迟导入，避免循环依赖

    pid = safe_id(settings.persona_id())
    if pid and exists(pid):
        return pid
    # 配置里指的没了（被人删了）→ 退回默认 / 第一个存在的
    if exists(DEFAULT_ID):
        return DEFAULT_ID
    for item in list_personas():
        return item["id"]
    return DEFAULT_ID


def set_current(pid):
    from . import settings

    pid = safe_id(pid)
    if not exists(pid):
        raise ValueError("没有这个人设喵")
    settings.set_persona(pid)
    _cache["key"] = None
    return pid


def current():
    """当前人设的完整信息（带 mtime 缓存，聊天每轮都要用）。"""
    pid = current_id()
    path = os.path.join(_dir(pid), "persona.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    key = (pid, mtime)
    if _cache["key"] != key:
        _cache["data"] = _meta(pid)
        _cache["key"] = key
    return _cache["data"]


def system_prompt():
    """当前人设的提示词（**不含**共享约束，后台编辑身份卡时看到的就是这个）。"""
    return current().get("prompt") or SYSTEM_PROMPT


def _unavailable_tools_note():
    """把后台关掉的工具组写成一段提醒，免得模型以为自己还有那些工具。"""
    try:
        from . import tools

        groups = tools.TOOL_GROUPS
        enabled = tools.enabled_groups()
        off = []
        for gid, info in groups.items():
            if gid in enabled:
                continue
            off.append(f"{info['label']}（" + "、".join(info["tools"]) + "）")
        if not off:
            return ""
        return ("\n\n【当前不可用的工具】下面这些工具现在被后台关掉了，"
                "你**没有**这些能力：别调用、也别假装能调，用户问起就说这组工具被关了。\n"
                + "\n".join("- " + line for line in off))
    except Exception:                    # noqa: BLE001
        return ""


def build_prompt():
    """模型实际收到的系统提示词 = 共享约束 + 身份卡 + 不可用工具提醒。

    分两层的好处：换身份卡（名字/语气/欢迎语）不会把安全约束也换掉；
    关掉一组工具时也能在这里补一句「你现在没有这些工具」。
    """
    from . import settings
    from .config import CORE_PROMPT

    core = ""
    try:
        core = (settings.core_prompt() or "").strip()
    except Exception:                    # noqa: BLE001
        core = ""
    if not core:
        core = CORE_PROMPT
    persona_part = (current().get("prompt") or "").strip()
    parts = [part for part in (core, persona_part) if part]
    return "\n\n".join(parts) + _unavailable_tools_note()


def public(pid=None):
    """给前端用的公开信息（名字、头像、欢迎语这些）。"""
    meta = current() if pid is None else _meta(pid)
    pid = meta["id"]
    return {
        "id": pid,
        "name": meta.get("name") or DEFAULTS["name"],
        "self_name": meta.get("self_name") or DEFAULTS["self_name"],
        "user_name": meta.get("user_name") or DEFAULTS["user_name"],
        "avatar": f"/api/persona/avatar?pid={pid}",
        "welcome": meta.get("welcome") or "",
        "placeholder": meta.get("placeholder") or "",
        "status_online": meta.get("status_online") or "",
        "status_thinking": meta.get("status_thinking") or "",
        "theme": meta.get("theme") or "#ffb7d5",
        "icon": f"/api/persona/icon?pid={pid}",
    }


def avatar_path(pid=None):
    """头像文件路径；没配就返回内置的 static/avatar.jpg。"""
    meta = current() if pid is None else _meta(pid)
    name = os.path.basename(meta.get("avatar") or "")
    if name:
        path = os.path.join(_dir(meta["id"]), name)
        if os.path.isfile(path):
            return path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "web", "static", "avatar.jpg")


def icon_path(pid=None):
    """网页图标：配了 icon 用 icon，没有就跟头像一样。"""
    meta = current() if pid is None else _meta(pid)
    name = os.path.basename(meta.get("icon") or "")
    if name:
        path = os.path.join(_dir(meta["id"]), name)
        if os.path.isfile(path):
            return path
    return avatar_path(pid)


def stickers_dir(pid=None):
    """表情包目录。"""
    meta = current() if pid is None else _meta(pid)
    value = (meta.get("stickers") or STATIC_STICKERS).strip()
    if value == STATIC_STICKERS:
        return STICKERS_DIR
    if value.startswith("/") or ".." in value:
        return STICKERS_DIR
    path = os.path.join(_dir(meta["id"]), value)
    os.makedirs(path, exist_ok=True)
    return path


def sticker_files(pid=None):
    try:
        return [n for n in os.listdir(stickers_dir(pid))
                if os.path.isfile(os.path.join(stickers_dir(pid), n))]
    except OSError:
        return []


def _clean_text(value, limit=_TEXT_MAX):
    return " ".join(str(value or "").split())[:limit]


def save(pid, data):
    """新建或更新一个人设。返回 (meta, error)。"""
    pid = safe_id(pid)
    if not pid:
        return None, "id 只能是 2~24 位小写字母 / 数字 / _ -，且以字母开头"
    base = _dir(pid)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, "persona.json")

    old = _read_json(path)
    merged = dict(DEFAULTS)
    merged.update(old)
    for key in ("name", "self_name", "user_name", "welcome", "placeholder",
                "status_online", "status_thinking", "stickers", "avatar",
                "theme", "icon"):
        if key in data:
            merged[key] = _clean_text(data[key])
    if "name" in data and not merged["name"]:
        return None, "名字不能为空喵"
    theme = str(merged.get("theme") or "").strip()
    if theme and not re.match(r"^#[0-9a-fA-F]{6}$", theme):
        return None, "主题色要写成 #rrggbb 这种格式喵"
    merged["theme"] = theme or "#ffb7d5"

    prompt = data.get("prompt")
    if prompt is not None:
        prompt = str(prompt)
        if len(prompt) > PROMPT_MAX:
            return None, "提示词太长了喵（最多 4 万字）"
        merged["prompt"] = prompt
        # 有内容就写 prompt.md（好编辑），同时把 json 里的清空避免两份不一致
        prompt_file = os.path.join(base, "prompt.md")
        try:
            if prompt.strip():
                with open(prompt_file, "w", encoding="utf-8") as fh:
                    fh.write(prompt)
                merged["prompt"] = ""
            elif os.path.isfile(prompt_file):
                os.remove(prompt_file)
        except OSError as exc:
            return None, f"提示词写不进去：{exc}"

    merged["id"] = pid
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        return None, f"存不下来：{exc}"
    _cache["key"] = None
    meta = _meta(pid)
    meta.pop("prompt", None)
    return meta, ""


def delete(pid):
    pid = safe_id(pid)
    if not pid or not exists(pid):
        return False, "没有这个人设喵"
    if pid == current_id():
        return False, "正在用的人设不能删喵，先切到别的"
    if len(list_personas()) <= 1:
        return False, "至少要留一个人设喵"
    try:
        shutil.rmtree(_dir(pid))
    except OSError as exc:
        return False, f"删不掉：{exc}"
    _cache["key"] = None
    return True, ""


WELCOME_FALLBACK = (
    "雏草姬来啦喵～我是{name}，有什么想聊的直接说就好喵。\n\n"
    "算法题、代码、知识库里的资料都可以问我；左上角 ☰ 是侧边栏，"
    "你所有的会话都在里面，随时切回来接着聊；点顶栏名字能进用户中心。"
)


def ensure_default():
    """第一次启动时把默认人设（taffy）建出来。

    提示词从老配置迁移：admin.json 里的 prompt 优先，其次 config.SYSTEM_PROMPT。
    已经在跑的老部署也是这条路，升级后行为不变。
    """
    os.makedirs(PERSONAS_DIR, exist_ok=True)
    if exists(DEFAULT_ID):
        return False
    from . import settings

    prompt = ""
    try:
        prompt = settings.prompt_text() or ""
    except Exception:                    # noqa: BLE001
        prompt = ""
    if not prompt.strip():
        prompt = SYSTEM_PROMPT
    save(DEFAULT_ID, {
        "name": "永雏塔菲",
        "self_name": "塔菲",
        "user_name": "雏草姬",
        "welcome": WELCOME_FALLBACK.format(name="永雏塔菲"),
        "placeholder": "和塔菲说点什么喵…",
        "status_online": "在线喵",
        "status_thinking": "正在思考喵…",
        "stickers": STATIC_STICKERS,
        "prompt": prompt,
    })
    try:
        if not settings.persona_id():
            settings.set_persona(DEFAULT_ID)
    except Exception:                    # noqa: BLE001
        pass
    return True
