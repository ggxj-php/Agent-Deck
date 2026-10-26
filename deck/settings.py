"""后台能改的运行配置：模型、接口地址、API Key、上下文上限、系统提示词。

项目根目录下的 admin.json 当覆盖层，没配的项就回落到 config.py / .env 的默认值。
改完立刻生效不用重启——llm.py 每次请求都来问一次当前值（系统提示词是开会话时问一次）。

聊天和图片是两套独立的连接：可以聊天用 GLM、发图用 DeepSeek，模型名、key、
接口地址各配各的，互不影响；某一套没配就回落到 config.py / .env 给它那套的默认值
（老版本只有一个 api_key，读进来当成聊天那套）。

这个文件里存着明文 key，所以已经写进 .gitignore 了，千万别提交。
"""
import json
import os
import threading

from .config import (
    API_KEY,
    BASE_URL,
    CONTEXT_LIMIT,
    EMBED_API_KEY,
    EMBED_BASE_URL,
    EMBED_MAX_TOKENS,
    EMBED_MODEL,
    MODEL,
    PROJECT_ROOT,
    SEARCH_TRANSLATE_MODEL,
    SYSTEM_PROMPT,
    TRANSLATE_API_KEY,
    TRANSLATE_BASE_URL,
    VISION_API_KEY,
    VISION_BASE_URL,
    VISION_MODEL,
)

SETTINGS_PATH = os.path.join(PROJECT_ROOT, "admin.json")

# 历史模型列表最多留这么多条，免得越攒越长
HISTORY_LIMIT = 30

# 「检查更新」从哪拉：gitee（默认，国内服务器连得上）/ github
UPDATE_REMOTES = ("gitee", "github")
DEFAULT_UPDATE_REMOTE = "gitee"

_lock = threading.Lock()
_cache = None


def _load():
    """读文件。文件不存在 / 坏了就当没配过，绝不让后台把服务搞挂。"""
    global _cache
    if _cache is not None:
        return _cache
    raw = {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fp:
            loaded = json.load(fp)
        if isinstance(loaded, dict):
            raw = loaded
    except (OSError, ValueError):
        raw = {}

    history = raw.get("model_history")
    _cache = {
        # 老版本只存了一个 api_key，当成聊天那套读
        "chat_key": str(raw.get("chat_key") or raw.get("api_key") or ""),
        "vision_key": str(raw.get("vision_key") or ""),
        "chat_base": str(raw.get("chat_base") or ""),
        "vision_base": str(raw.get("vision_base") or ""),
        "translate_base": str(raw.get("translate_base") or ""),
        "translate_key": str(raw.get("translate_key") or ""),
        "embed_key": str(raw.get("embed_key") or ""),
        "embed_base": str(raw.get("embed_base") or ""),
        "embed_model": str(raw.get("embed_model") or ""),
        # 向量模型单条输入上限（token）。0 / 缺省 = 自动探测
        "embed_max_tokens": raw.get("embed_max_tokens") or 0,
        "model": str(raw.get("model") or ""),
        "vision_model": str(raw.get("vision_model") or ""),
        "translate_model": str(raw.get("translate_model") or ""),
        "update_remote": str(raw.get("update_remote") or ""),
        # 向量那一路的总开关：True / False；没设过就是 None（= 按「配齐了没」自动判断）
        "embed_enabled": raw.get("embed_enabled"),
        # 后台改过的系统提示词（人设）。空串 = 没改过，用 config.py 里内置的那份
        "prompt": str(raw.get("prompt") or ""),
        # 当前用的人设 id（见 persona.py）。空 = 用默认人设
        "persona": str(raw.get("persona") or ""),
        # 共享约束提示词（所有身份卡共用）。空 = 用 config.CORE_PROMPT
        "core_prompt": str(raw.get("core_prompt") or ""),
        # 开启的工具分组；None = 没配过（= 全开）
        "tool_groups": (raw.get("tool_groups")
                        if isinstance(raw.get("tool_groups"), list) else None),
        "context_limit": raw.get("context_limit") or 0,
        "model_history": [m for m in history if isinstance(m, str)] if isinstance(history, list) else [],
    }
    return _cache


def _write(data):
    """先写临时文件再替换，避免写一半断电留下半截 JSON。

    临时文件一开始就用 0600 建，别先建再 chmod——那中间会有一段窗口期，含明文 key
    的临时文件是 0644，别的用户能读到。（Windows 上本来就认不了这个权限位。）
    """
    tmp = SETTINGS_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_PATH)


# ---------- 共享约束 ----------

def core_prompt():
    """共享约束提示词：后台改过就用改过的，否则用 config.py 内置那份。"""
    return _load()["core_prompt"]


def set_core_prompt(text):
    text = (text or "").strip()
    if len(text) > PROMPT_LIMIT:
        raise ValueError(f"太长了，最多 {PROMPT_LIMIT} 个字")
    with _lock:
        data = _load()
        data["core_prompt"] = text
        _write(data)
    return text


# ---------- 工具分组 ----------

def tool_groups():
    """后台开启的工具分组；没配过返回 None（= 全开）。"""
    return _load()["tool_groups"]


def set_tool_groups(groups):
    """存工具分组开关。传 None / 空列表 = 全关。"""
    with _lock:
        data = _load()
        data["tool_groups"] = [str(g) for g in (groups or [])]
        _write(data)


# ---------- 人设 ----------

def persona_id():
    """当前用的人设 id（没配过就是空串）。"""
    return _load()["persona"]


def set_persona(pid):
    """切人设。_load() 返回的就是缓存本身，改完写盘即可。"""
    with _lock:
        data = _load()
        data["persona"] = (pid or "").strip().lower()
        _write(data)


# ---------- 模型 ----------

def chat_model():
    """平时聊天用的模型。"""
    return _load()["model"] or MODEL


def vision_model():
    """发图片时用的模型。"""
    return _load()["vision_model"] or VISION_MODEL


def model_for(vision):
    """有图片就走图片模型，纯文字走聊天模型，两个都在后台单独配。"""
    return vision_model() if vision else chat_model()


def translate_model():
    """检索前把中文问题翻成英文关键词用的模型。

    留空就回落到聊天那个模型——只做一件小事，不值得为它非配不可。
    """
    return _load()["translate_model"] or SEARCH_TRANSLATE_MODEL


def model_history():
    """用过的模型，新的在前，后台拿它当候选列表。"""
    return list(_load()["model_history"])


def remember_model(name):
    """记下这次真正用到的模型。已经记过就不重复写盘。"""
    name = (name or "").strip()
    if not name:
        return
    with _lock:
        data = _load()
        if name in data["model_history"]:
            return
        data["model_history"].insert(0, name)
        del data["model_history"][HISTORY_LIMIT:]
        _write(data)


def update_models(model, vision, translate):
    """存三个模型名，顺手记进历史列表。

    translate（检索翻译用的模型）允许留空，那就跟着聊天模型走。
    """
    model = (model or "").strip()
    vision = (vision or "").strip()
    translate = (translate or "").strip()
    with _lock:
        data = _load()
        data["model"] = model
        data["vision_model"] = vision
        data["translate_model"] = translate
        for name in (model, vision, translate):
            if name and name not in data["model_history"]:
                data["model_history"].insert(0, name)
        del data["model_history"][HISTORY_LIMIT:]
        _write(data)


# ---------- Key ----------

def chat_key():
    """聊天那套的 key：后台配过就用后台的，否则用 .env 里的。"""
    return _load()["chat_key"] or API_KEY


def vision_key():
    """图片那套的 key。没单独配就用 .env / config.py 里给图片那套配的默认值。"""
    return _load()["vision_key"] or VISION_API_KEY


def key_for(vision=False):
    return vision_key() if vision else chat_key()


def key_source(vision=False):
    """key 是哪来的，后台拿来提示用：admin（后台配的）/ env（.env 里的）。"""
    return "admin" if _load()["vision_key" if vision else "chat_key"] else "env"


# ---------- 接口地址 ----------

def chat_base_url():
    """聊天那套的接口地址。"""
    return _load()["chat_base"] or BASE_URL


def vision_base_url():
    """图片那套的接口地址。没单独配就用 config.py 给图片那套配的默认值。"""
    return _load()["vision_base"] or VISION_BASE_URL


def base_url_for(vision=False):
    return vision_base_url() if vision else chat_base_url()


def base_source(vision=False):
    """接口地址是哪来的，后台拿来提示用：admin（后台配的）/ default（config.py 里的）。"""
    return "admin" if _load()["vision_base" if vision else "chat_base"] else "default"


# ---------- 检索翻译那一路的连接 ----------
# 「查知识库之前把中文问题翻成英文关键词」用的（见 kb/translate.py）。默认跟聊天那套
# 共用地址和 key，单独配是为了让它走另一家，或者本机跑的小模型（那样不花钱、也不怕限流）。

def translate_base_url():
    """翻译那一路的接口地址。没单独配就跟聊天那套一样。"""
    return _load()["translate_base"] or TRANSLATE_BASE_URL or chat_base_url()


def translate_key():
    """翻译那一路的 key。没单独配就跟聊天那套一样。"""
    return _load()["translate_key"] or TRANSLATE_API_KEY or chat_key()


def translate_own_base():
    """地址是不是单独配过（后台拿来提示用）。"""
    return "admin" if _load()["translate_base"] else ("env" if TRANSLATE_BASE_URL else "chat")


def translate_own_key():
    """key 是不是单独配过。"""
    return "admin" if _load()["translate_key"] else ("env" if TRANSLATE_API_KEY else "chat")


def update_keys(chat_key_new, vision_key_new, chat_base, vision_base,
                translate_key_new="", translate_base=""):
    """换 key / 换接口地址。

    每一项留空就表示「这项不改」——这样只想换聊天 key 的时候，不用把图片那套
    重新填一遍。
    """
    with _lock:
        data = _load()
        if chat_key_new:
            data["chat_key"] = chat_key_new
        if vision_key_new:
            data["vision_key"] = vision_key_new
        if chat_base:
            data["chat_base"] = chat_base
        if vision_base:
            data["vision_base"] = vision_base
        if translate_key_new:
            data["translate_key"] = translate_key_new
        if translate_base:
            data["translate_base"] = translate_base
        _write(data)


# ---------- 向量检索（embedding）----------
# 知识库检索的第二路，见 kb/embed.py。这里只管存，用不用得上由 embed.enabled() 判断。

def embed_model():
    """知识库检索用的向量模型。"""
    return _load()["embed_model"] or EMBED_MODEL


def embed_base_url():
    """向量模型的接口地址（任何 OpenAI 兼容的 /embeddings 都行）。"""
    return _load()["embed_base"] or EMBED_BASE_URL


def embed_key():
    """向量模型的 key。"""
    return _load()["embed_key"] or EMBED_API_KEY


def embed_key_source():
    """key 哪来的，后台拿来提示：admin（后台配的）/ env（.env 里的）。"""
    return "admin" if _load()["embed_key"] else "env"


def embed_base_source():
    return "admin" if _load()["embed_base"] else "default"


def embed_switch():
    """向量那一路的总开关：True 开 / False 关 / None 没设过。

    None 表示「跟着配置走」——模型、地址、key 三样配齐了就算启用（老版本的行为）。
    """
    value = _load()["embed_enabled"]
    return value if isinstance(value, bool) else None


def embed_on():
    """向量那一路现在到底用不用：开关关了就不用；开关开着/没设过，还得三样配齐才行。"""
    if not (embed_model() and embed_base_url() and embed_key()):
        return False
    switch = embed_switch()
    return True if switch is None else switch


def set_embed_enabled(value):
    """拨总开关。存下来即可，索引要重启才重建（见 kb/index.py）。"""
    with _lock:
        data = _load()
        data["embed_enabled"] = bool(value)
        _write(data)


def update_embed(model, base_url, key):
    """改向量模型 / 地址 / key。留空表示「这项不改」。"""
    with _lock:
        data = _load()
        if model:
            data["embed_model"] = model
        if base_url:
            data["embed_base"] = base_url
        if key:
            data["embed_key"] = key
        _write(data)


def embed_max_tokens():
    """向量模型单条输入的上限（token）。0 = 自动探测，见 kb/embed.py。"""
    try:
        value = int(_load()["embed_max_tokens"])
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else EMBED_MAX_TOKENS


def set_embed_max_tokens(value):
    """存这个上限。0 / 负数 = 回到「自动探测」。改完要重建索引才生效。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return
    with _lock:
        data = _load()
        data["embed_max_tokens"] = value if value > 0 else 0
        _write(data)


# ---------- 检查更新 ----------

def update_remote():
    """「检查更新」从哪拉：gitee / github。没配过就是 gitee。"""
    name = _load()["update_remote"]
    return name if name in UPDATE_REMOTES else DEFAULT_UPDATE_REMOTE


def set_update_remote(name):
    """记住了就存下来，下次打开后台还是它。"""
    name = (name or "").strip().lower()
    if name not in UPDATE_REMOTES:
        return
    with _lock:
        data = _load()
        data["update_remote"] = name
        _write(data)


# ---------- 对话上下文 ----------
# 模型能吃多少 token。聊天页那个「上下文 xx%」和自动压缩都按它算（见 deck/context.py）。

def context_limit():
    """当前生效的上下文窗口大小（token）。后台没配过就用 config.py / .env 里的。"""
    try:
        value = int(_load()["context_limit"])
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else CONTEXT_LIMIT


def set_context_limit(value):
    """改上下文窗口大小。存下来立刻生效——每次估算都现问一次。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return
    if value <= 0:
        return
    with _lock:
        data = _load()
        data["context_limit"] = value
        _write(data)


# ---------- 系统提示词（人设）----------
# 后台能整段改。改过的存在 admin.json 里（不进 git），所以更新代码不会把它冲掉。
# 取用的时机是「开会话的时候」（见 core.DeckAgent.__init__）：改完新开的会话就是新的，
# 已经在聊的那个还留着旧的那份——它的历史第一条在开会话时就已经定下来了。

PROMPT_LIMIT = 40000       # 整段提示词的字数上限，挡住误粘贴一整本书进来


def prompt_text():
    """后台改过的系统提示词。没改过就是空串。"""
    return _load()["prompt"].strip()


def system_prompt():
    """实际生效的系统提示词。

    现在提示词归「人设」管（persona.py），每个身份卡一份；这里保留函数名，
    是为了让 core.py / admin.py 那些老调用点不用改。
    """
    from . import persona

    try:
        return persona.build_prompt()
    except Exception:                    # noqa: BLE001
        return prompt_text() or SYSTEM_PROMPT


def set_prompt(text):
    """存后台改的系统提示词。传空串（或只有空白）= 清掉改动，回到内置那份。

    太长直接拒——admin.json 每次读写都是整份，塞个几兆进去会把后台卡死。
    """
    text = (text or "").strip()
    if len(text) > PROMPT_LIMIT:
        raise ValueError(f"提示词太长了，最多 {PROMPT_LIMIT} 个字（现在 {len(text)} 个）")
    with _lock:
        data = _load()
        data["prompt"] = text
        _write(data)
    return text
