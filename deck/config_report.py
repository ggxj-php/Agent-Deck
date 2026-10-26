"""配置总览：把所有「部署时可调」的设置在**一个地方**列出来。

这个模块**只读**——不改变任何配置，只是把散在 `.env` / `admin.json` / `config.py`
里的值汇总成一份带说明的清单，后台「配置总览」直接渲染。

为什么需要它：以前改个东西得先猜「这是 .env 里的、还是后台里的？改完要不要重启？」
现在打开后台就能看到每项的：当前值 / 默认值 / 在哪改 / 要不要重启。
"""
import os

from . import config as cfg
from . import persona, settings

_MASK = "sk-***"


def _mask(value):
    """密钥只露个尾巴，别在页面上完整显示。"""
    value = str(value or "")
    if not value:
        return ""
    return _MASK if len(value) < 12 else f"{value[:5]}…{value[-4:]}"


def _kb_stats():
    try:
        return len(os.listdir(cfg.KNOWLEDGE_DIR))
    except OSError:
        return 0


# 每一项：key 唯一；group 用来分组；source 是「在哪改」；restart 表示改完要不要重启
_ITEMS = [
    # ---------- 模型与接口 ----------
    dict(group="模型与接口", key="chat_model", label="聊天模型", source="后台 → 模型",
         get=lambda: settings.chat_model(), default=cfg.MODEL, restart=False,
         desc="纯文字那几轮用这个模型"),
    dict(group="模型与接口", key="vision_model", label="看图模型", source="后台 → 模型",
         get=lambda: settings.vision_model(), default=cfg.VISION_MODEL, restart=False,
         desc="带图那轮 / read_img / read_pdf 抽图用这个"),
    dict(group="模型与接口", key="translate_model", label="检索翻译模型",
         source="后台 → 模型（留空=跟聊天同款）",
         get=lambda: settings.translate_model() or "(跟聊天模型一样)",
         default=cfg.SEARCH_TRANSLATE_MODEL or "(跟聊天模型一样)", restart=False,
         desc="把中文问题翻成英文关键词，跨语言检索用"),
    dict(group="模型与接口", key="chat_base", label="聊天接口地址", source="后台 → 接口与 Key",
         get=lambda: settings.chat_base_url(), default=cfg.BASE_URL, restart=False,
         desc="任何 OpenAI 兼容的 /chat/completions 都行"),
    dict(group="模型与接口", key="chat_key", label="聊天 Key", source="后台 → 接口与 Key",
         get=lambda: _mask(settings.chat_key()), default="(见 .env DEEPSEEK_API_KEY)",
         restart=False, secret=True, desc="不落盘到日志，页面只显示尾巴"),
    dict(group="模型与接口", key="context_limit", label="上下文上限（token）",
         source="后台 → 模型", get=lambda: settings.context_limit(), default=1000000,
         restart=False, desc="填太大压缩来得晚、可能撞模型上限；填小了压得早、多花摘要钱"),
    dict(group="模型与接口", key="update_remote", label="更新源", source="后台 → 检查更新",
         get=lambda: settings.update_remote(), default=settings.DEFAULT_UPDATE_REMOTE,
         restart=False, desc="Gitee 国内服务器连得上，GitHub 稳"),

    # ---------- 人设 ----------
    dict(group="人设", key="persona", label="当前身份卡", source="后台 → 身份卡",
         get=lambda: f"{persona.current()['id']}（{persona.current().get('name', '')}）",
         default=persona.DEFAULT_ID, restart=False,
         desc="换身份卡立刻生效，聊天页刷新就能看到新名字 / 欢迎语"),
    dict(group="人设", key="persona_dir", label="身份卡目录", source="代码 / 磁盘",
         get=lambda: persona.PERSONAS_DIR, default="personas/", restart=False,
         desc="每张身份卡一个子目录：persona.json + prompt.md + stickers/"),

    # ---------- 工具 ----------
    dict(group="工具", key="active_tools", label="模型能看到的工具",
         source="后台 → 工具开关",
         get=lambda: f"{len(_active_tools()[0])} 个（共 {len(cfg_tools())} 个）",
         default="全部开启", restart=False,
         desc="关掉的组模型看不见、硬调也会被拦"),

    # ---------- 服务器 / 路径 ----------
    dict(group="服务器", key="site_www", label="WWW 站点域名", source=".env TAFFY_SITE_WWW",
         get=lambda: cfg.SITE_WWW, default="www.example.com", restart=True,
         desc="访问流量面板按这个域名分站统计"),
    dict(group="服务器", key="site_api", label="API 站点域名", source=".env TAFFY_SITE_API",
         get=lambda: cfg.SITE_API, default="api.example.com", restart=True,
         desc="同上，one-api 那套"),
    dict(group="服务器", key="db_path", label="SQLite 库文件", source=".env TAFFY_DB_PATH",
         get=lambda: __import__("deck.db", fromlist=["DB_PATH"]).DB_PATH,
         default=".cache/taffy.db", restart=True,
         desc="用户 / 会话 / 消息都在这；备份直接拷这个文件"),
    dict(group="服务器", key="knowledge_dir", label="知识库目录", source="磁盘",
         get=lambda: f"{cfg.KNOWLEDGE_DIR}（{_kb_stats()} 个条目）",
         default="knowledge/", restart=True,
         desc="自己放文档，仓库里不带；改完要重启重建索引"),
    dict(group="服务器", key="workspace", label="工作区（沙箱根）", source="磁盘",
         get=lambda: cfg.PROJECT_ROOT + "/workspace", default="workspace/", restart=False,
         desc="所有文件工具 / 跑代码都只能在这里动手"),
    dict(group="服务器", key="admin_name", label="默认管理员用户名",
         source=".env TAFFY_ADMIN_NAME",
         get=lambda: __import__("deck.auth", fromlist=["DEFAULT_ADMIN_NAME"]).DEFAULT_ADMIN_NAME,
         default="admin", restart=True,
         desc="只在第一次启动建库时生效（已有管理员就不动了）"),

    # ---------- 运行参数 ----------
    dict(group="运行参数", key="max_rounds", label="单次提问最多跑几轮工具",
         source="config.py MAX_ROUNDS", get=lambda: cfg.MAX_ROUNDS, default=100,
         restart=True, desc="防死循环的上限，一般用不到"),
    dict(group="运行参数", key="sticker_limit", label="一次回复最多几张表情包",
         source="config.py STICKER_LIMIT", get=lambda: cfg.STICKER_LIMIT, default=3,
         restart=True, desc="治刷屏用"),
    dict(group="运行参数", key="render_limit", label="render_html 产物上限",
         source="config.py", get=lambda: cfg.RENDER_LIMIT, default=200, restart=True,
         desc="超过就淘汰最早的；产物在内存里，重启就没了"),
    dict(group="运行参数", key="upload_ttl", label="上传图片保留时长",
         source="uploads.py TTL_SECONDS",
         get=lambda: __import__("deck.uploads", fromlist=["TTL_SECONDS"]).TTL_SECONDS // 3600,
         default=24, restart=True, desc="单位小时，过期自动清"),
]


def cfg_tools():
    try:
        from . import tools

        return tools.TOOLS
    except Exception:                 # noqa: BLE001
        return []


def _active_tools():
    try:
        from . import tools

        return tools.active_specs(), tools.TOOLS
    except Exception:                 # noqa: BLE001
        return [], []


def report():
    """给后台「配置总览」用的清单。"""
    groups = {}
    for item in _ITEMS:
        try:
            value = item["get"]()
        except Exception as exc:      # noqa: BLE001
            value = f"(读不到：{exc})"
        groups.setdefault(item["group"], []).append({
            "key": item["key"],
            "label": item["label"],
            "value": "" if value is None else str(value),
            "default": str(item.get("default", "")),
            "source": item["source"],
            "desc": item.get("desc", ""),
            "secret": bool(item.get("secret")),
            "restart": bool(item.get("restart")),
        })
    return {"groups": [{"name": name, "items": items} for name, items in groups.items()],
            "count": len(_ITEMS)}
