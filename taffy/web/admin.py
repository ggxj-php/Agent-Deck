"""后台管理：登录、改模型、改 Key、改知识库的向量模型。挂在 /admin 和 /api/admin/* 底下。

口令是按天变的：md5(.env 里的 ADMIN_PASSWORD_PREFIX + 当天日期 YYMMDD)，日期按
UTC+8 算——服务器时钟是 UTC 也不会差一天。口令只在内存里比对，不落盘、不写日志。

**这里刻意不留任何默认前缀**：前缀是口令里唯一的秘密，写进代码就等于公开了。
没配的话登录直接拒绝，并提示去 .env 里配。

登录成功发一个随机 token 的 cookie，12 小时有效；进程重启就全失效，得重新登。
"""
import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import llm, settings
from ..config import ADMIN_PASSWORD_PREFIX, CONTEXT_COMPRESS_AT, EMBED_DIM
from ..kb import stats as kb_stats
from . import blacklist, sysinfo, traffic, update

# 启动后台线程对一次账：名单里有、UFW 里没有的补回去（正常不会发生）
blacklist.start_sync()

router = APIRouter()

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

COOKIE_NAME = "taffy_admin"
TOKEN_TTL = 12 * 3600
_TOKENS = {}
_TOKENS_LOCK = threading.Lock()


def _digest(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _beijing_now():
    """按北京时间取「当前」。服务器跑在 UTC 上也不会把日期算错一天。"""
    return datetime.now(timezone.utc) + timedelta(hours=8)


def expected_password(day=None):
    """当天口令的 md5。动态的就是日期那一段；前缀没配的话这个值没意义，
    登录那条路会先拦下来。"""
    return _digest(ADMIN_PASSWORD_PREFIX + (day or _beijing_now()).strftime("%y%m%d"))


def check_password(password):
    """比对 md5，别拿明文字符串直接比。没配前缀一律不放行。"""
    if not ADMIN_PASSWORD_PREFIX:
        return False
    if not isinstance(password, str) or not password:
        return False
    return hmac.compare_digest(_digest(password), expected_password())


def _issue_token():
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _TOKENS_LOCK:
        for old, expire in list(_TOKENS.items()):
            if expire < now:
                del _TOKENS[old]
        _TOKENS[token] = now + TOKEN_TTL
    return token


def _logged_in(request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    with _TOKENS_LOCK:
        expire = _TOKENS.get(token)
    return bool(expire and expire > time.time())


def _require(request):
    if not _logged_in(request):
        raise HTTPException(status_code=401, detail="先登录喵")


def _state():
    return {
        "model": settings.chat_model(),
        "vision_model": settings.vision_model(),
        "translate_model": settings.translate_model(),
        "model_history": settings.model_history(),
        # key 本身不回给前端，只说它是从哪来的：admin（后台配的）/ env（.env 里的）。
        # 接口地址不是秘密，直接把当前生效的值给出去，页面拿它当占位提示。
        "chat_key_source": settings.key_source(vision=False),
        "vision_key_source": settings.key_source(vision=True),
        "chat_base_url": settings.chat_base_url(),
        "vision_base_url": settings.vision_base_url(),
        "chat_base_source": settings.base_source(vision=False),
        "vision_base_source": settings.base_source(vision=True),
        # 检索翻译那一路的连接：留空就跟聊天那套一样，所以键和地址都说清楚它们是哪来的
        "translate_base_url": settings.translate_base_url(),
        "translate_base_source": settings.translate_own_base(),
        "translate_key_source": settings.translate_own_key(),
        # 知识库向量检索那一路（跟聊天 / 图片两套没关系，是检索用的）
        "embed_model": settings.embed_model(),
        "embed_base_url": settings.embed_base_url(),
        "embed_key_source": settings.embed_key_source(),
        "embed_base_source": settings.embed_base_source(),
        # 向量那一路的总开关：None = 没设过（按「三样配齐了没」自动判断）
        "embed_switch": settings.embed_switch(),
        "embed_on": settings.embed_on(),
        "embed_dim": EMBED_DIM,
        # 单条输入上限（token）。0 = 自动探测，见 kb/embed.py
        "embed_max_tokens": settings.embed_max_tokens(),
        "kb": kb_stats(),
        # 对话上下文：模型的窗口有多大（聊天页那个百分比和自动压缩都按它算）
        "context_limit": settings.context_limit(),
        "context_compress_at": CONTEXT_COMPRESS_AT,
        # 检查更新那一页记住的厂家
        "update_remote": settings.update_remote(),
        "update_remotes": list(settings.UPDATE_REMOTES),
    }


@router.get("/admin")
def admin_page():
    return FileResponse(os.path.join(STATIC_DIR, "admin.html"))


class LoginRequest(BaseModel):
    password: str = ""


@router.post("/api/admin/login")
def login(req: LoginRequest, response: Response):
    if not ADMIN_PASSWORD_PREFIX:
        raise HTTPException(
            status_code=503,
            detail="后台口令还没配喵：在 .env 里加一行 ADMIN_PASSWORD_PREFIX=你自己定的一串，再重启服务",
        )
    if not check_password(req.password):
        raise HTTPException(status_code=401, detail="口令不对喵")
    response.set_cookie(
        COOKIE_NAME,
        _issue_token(),
        max_age=TOKEN_TTL,
        httponly=True,   # 前端 JS 读不到，少一个被偷的口子
        samesite="lax",
    )
    return {"ok": True}


@router.post("/api/admin/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    with _TOKENS_LOCK:
        _TOKENS.pop(token, None)
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/api/admin/state")
def state(request: Request):
    _require(request)
    return _state()


class ModelsRequest(BaseModel):
    model: str = ""
    vision_model: str = ""
    # 知识库检索前把中文问题翻成英文关键词用的模型，留空 = 跟聊天模型一样
    translate_model: str = ""


@router.post("/api/admin/models")
def save_models(req: ModelsRequest, request: Request):
    _require(request)
    model = req.model.strip()
    vision = req.vision_model.strip()
    if not model or not vision:
        raise HTTPException(status_code=400, detail="聊天和图片的模型名都得填喵")
    settings.update_models(model, vision, req.translate_model.strip())
    return _state()


class KeysRequest(BaseModel):
    # 都留空表示「这项不改」——只想换聊天 key 时不用把图片那套重填一遍
    chat_key: str = ""
    vision_key: str = ""
    chat_base_url: str = ""
    vision_base_url: str = ""
    # 检索翻译那一路：默认跟聊天共用，单独填就是让它走另一家（或者本机小模型）
    translate_key: str = ""
    translate_base_url: str = ""


def _check_base(name, value):
    value = value.strip()
    if value and not value.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail=f"{name}的接口地址要以 http:// 或 https:// 开头喵")
    return value


@router.post("/api/admin/keys")
def save_keys(req: KeysRequest, request: Request):
    _require(request)
    chat_base = _check_base("聊天", req.chat_base_url)
    vision_base = _check_base("图片", req.vision_base_url)
    translate_base = _check_base("检索翻译", req.translate_base_url)
    chat_key = req.chat_key.strip()
    vision_key = req.vision_key.strip()
    translate_key = req.translate_key.strip()
    if not any((chat_key, vision_key, translate_key, chat_base, vision_base, translate_base)):
        raise HTTPException(status_code=400, detail="一个都没填，那就先不动它喵")
    settings.update_keys(chat_key, vision_key, chat_base, vision_base,
                         translate_key_new=translate_key, translate_base=translate_base)
    return _state()


# ---------- 仪表盘 ----------

@router.get("/api/admin/sysinfo")
def sysinfo_state(request: Request):
    """服务器状态：CPU / 内存 / 磁盘 / 本进程占多少。仪表盘每隔几秒来拉一次。"""
    _require(request)
    return sysinfo.snapshot()


# ---------- 访问日志 / 流量 ----------

@router.get("/api/admin/traffic")
def traffic_state(request: Request, days: int = 7, overseas: int = 0):
    """访问日志统计：页面访问 / 独立 IP / 地区分布 / 热门路径 / 状态码。

    口径和命令行版 /root/taffy-traffic.py 一样：排掉本机、扫描器、腾讯网段，
    默认再排掉海外 IP（只要真实国内访客）。overseas=1 就全算上。
    结果在模块里缓存 60 秒，重复点不会反复扫日志。
    """
    _require(request)
    return traffic.snapshot(days=days, overseas=bool(overseas))


# 同一个 IP 半小时内分析过就直接给结果，别让人多点几次就多烧几次 token
_ip_cache = {}
_ip_cache_lock = threading.Lock()
IP_ANALYSIS_TTL = 1800

_IP_SYSTEM_PROMPT = (
    "你是网站运维安全分析师。根据给定的访问日志判断这个 IP 是什么角色。\n"
    "严格按下面三行输出，不要客套、不要多余的话：\n"
    "角色：<真实访客 / 搜索引擎爬虫 / 漏洞扫描器 / 账号爆破 / 内容采集器 / "
    "云厂商探测 / 网络空间测绘 / 其他>\n"
    "依据：<一两句，必须引用日志里的具体证据>\n"
    "建议：<一句话：放行 / 观察 / 封禁>\n"
    "依据必须只来自给定的日志数据，不许编造日志里没有的路径或行为；"
    "数据不足就直接说「证据不足」。"
)


class IpAnalyzeRequest(BaseModel):
    ip: str
    days: int = 7


def _ip_prompt(d):
    """把 ip_detail 的材料压成一段给模型看的文字。"""
    g = d["geo"]
    lines = [
        f"IP：{d['ip']}",
        f"归属：{g['country']} {g['region']} {g['city']} / {g['isp']} / {g['as']}",
        f"时间窗：{d['first']} ~ {d['last']}，共 {d['count']} 次请求",
        "状态码：" + "、".join(f"{c}×{n}" for c, n in d["statuses"]),
        "访问路径：" + "、".join(f"{p}×{n}" for p, n in d["paths"]),
        "User-Agent：" + " | ".join(ua for ua, _ in d["uas"]),
        "请求时间线（时间 状态码 路径）：",
    ]
    for s in d["samples"]:
        lines.append(f"  {s['t']} {s['status']} {s['path']}")
    return "\n".join(lines)


@router.get("/api/admin/ip-detail")
def ip_detail(request: Request, ip: str, days: int = 7):
    """某个 IP 这段时间的访问明细：路径 / UA / 状态码 / 时间线。"""
    _require(request)
    detail = traffic.ip_detail(ip, days=days)
    if detail.get("ok"):
        detail["banned"] = ip.strip() in blacklist.banned_ips()
    return detail


@router.post("/api/admin/ip-analyze")
def ip_analyze(req: IpAnalyzeRequest, request: Request):
    """让当前接入的模型分析这个 IP 在干什么。"""
    _require(request)
    key = (req.ip, max(1, min(int(req.days or 7), 90)))
    now = time.time()
    with _ip_cache_lock:
        hit = _ip_cache.get(key)
        if hit and now - hit[0] < IP_ANALYSIS_TTL:
            return {"ok": True, "analysis": hit[1], "cached": True}

    detail = traffic.ip_detail(req.ip, days=key[1])
    if not detail.get("ok"):
        raise HTTPException(status_code=400, detail=detail.get("error") or "读不到这个 IP 的记录")

    # thinking 关掉：这种短判断不需要绕弯子，省 token 也快
    text = llm.complete(
        [{"role": "system", "content": _IP_SYSTEM_PROMPT},
         {"role": "user", "content": _ip_prompt(detail)}],
        max_tokens=900, thinking=False,
    )
    text = (text or "").strip() or "模型没给出分析结果"
    with _ip_cache_lock:
        _ip_cache[key] = (time.time(), text)
    return {"ok": True, "analysis": text, "cached": False, "detail": detail}


# ---------- IP 黑名单 ----------

class BlacklistRequest(BaseModel):
    ip: str
    action: str = "add"        # add / remove
    reason: str = ""


def _actor_ip(request):
    """操作者自己的 IP（nginx 会把真实 IP 放在 X-Real-IP）。"""
    return (request.headers.get("x-real-ip")
            or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else ""))


@router.get("/api/admin/blacklist")
def blacklist_state(request: Request):
    """当前黑名单。"""
    _require(request)
    return {"ok": True, "bans": blacklist.list_bans()}


@router.post("/api/admin/blacklist")
def blacklist_change(req: BlacklistRequest, request: Request):
    """加 / 解封。加的时候拒绝本机、内网、以及操作者自己的 IP。"""
    _require(request)
    action = (req.action or "add").strip().lower()
    if action == "add":
        result = blacklist.ban(req.ip, reason=req.reason, actor=_actor_ip(request))
    elif action == "remove":
        result = blacklist.unban(req.ip)
    else:
        raise HTTPException(status_code=400, detail="action 只能是 add 或 remove")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "操作失败")
    result["bans"] = blacklist.list_bans()
    return result


# ---------- 检查更新 ----------

@router.get("/api/admin/update")
def update_check(request: Request, remote: str = ""):
    _require(request)
    provider = (remote or settings.update_remote()).strip().lower()
    if provider not in settings.UPDATE_REMOTES:
        raise HTTPException(status_code=400, detail="只能从 gitee 或者 github 拉喵")
    info = update.check(provider)
    # 真跑通了才把这次选的厂家记下来；检查失败就保留原来的，免得下次打开指向一个拉不通的源
    if not info.get("error"):
        settings.set_update_remote(provider)
    return info


class UpdateRequest(BaseModel):
    # 前端选了哪个厂家就传哪个，同时也会记进配置，下次打开还是它
    remote: str = ""


@router.post("/api/admin/update")
def update_apply(req: UpdateRequest, request: Request):
    """拉最新版。成功的话这个进程会在两秒后被 systemd 重启成新代码。"""
    _require(request)
    provider = (req.remote or settings.update_remote()).strip().lower()
    if provider not in settings.UPDATE_REMOTES:
        raise HTTPException(status_code=400, detail="只能从 gitee 或者 github 拉喵")
    result = update.apply(provider)
    # 拉成功了才把这次选的厂家记下来；失败就保留原来的，免得下次自动挑一个拉不通的源
    if result.get("ok"):
        settings.set_update_remote(provider)
    return result


class EmbedRequest(BaseModel):
    embed_model: str = ""
    embed_base_url: str = ""
    embed_key: str = ""
    # 总开关：None = 这次不动它（前端不传就是这个）
    embed_enabled: bool | None = None
    # 单条输入上限（token）：None = 这次不动；0 = 回到自动探测
    embed_max_tokens: int | None = None


# 单条输入上限的合理范围。下限挡着别填个 8（块会碎成几个字，检索质量直接崩）
EMBED_TOKENS_MIN = 32
EMBED_TOKENS_MAX = 1_000_000


@router.post("/api/admin/embed")
def save_embed(req: EmbedRequest, request: Request):
    _require(request)
    base = _check_base("向量模型", req.embed_base_url)
    model = req.embed_model.strip()
    key = req.embed_key.strip()
    if req.embed_max_tokens is not None:
        if req.embed_max_tokens != 0 and not EMBED_TOKENS_MIN <= req.embed_max_tokens <= EMBED_TOKENS_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"单条输入上限得在 {EMBED_TOKENS_MIN} ~ {EMBED_TOKENS_MAX} 之间，"
                       f"或者填 0（自动探测）喵",
            )
        settings.set_embed_max_tokens(req.embed_max_tokens)
    if req.embed_enabled is not None:
        settings.set_embed_enabled(req.embed_enabled)
    if not (model or base or key):
        # 只拨了开关 / 只改了上限也算改过，别当成「什么都没填」拒掉
        if req.embed_enabled is None and req.embed_max_tokens is None:
            raise HTTPException(status_code=400, detail="一个都没填，那就先不动它喵")
        return _state()
    settings.update_embed(model, base, key)
    return _state()


# ---------- 对话上下文 ----------
# 模型的上下文窗口有多大。聊天页那个「上下文 xx%」和自动压缩（见 taffy/context.py）
# 都按这个数算，改完立刻生效，不用重启。

CONTEXT_MIN = 4096
CONTEXT_MAX = 2_000_000


class ContextRequest(BaseModel):
    limit: int = 0


@router.post("/api/admin/context")
def save_context(req: ContextRequest, request: Request):
    _require(request)
    if not CONTEXT_MIN <= req.limit <= CONTEXT_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"上下文上限得在 {CONTEXT_MIN} ~ {CONTEXT_MAX} 之间喵",
        )
    settings.set_context_limit(req.limit)
    return _state()


# ---------- 系统提示词（人设）----------
# 整段可改，改过的存在 admin.json 里（不进 git，所以更新代码不会冲掉）。
# 取的时机是开会话时（见 core.TaffyAgent.__init__）：改完新开的会话就是新的、不用重启，
# 已经在聊的会话还留着它自己那份。所以「当前生效的那份」和「某个会话在用的那份」
# 不一定一样，后台只负责显示/改前者。


class PromptRequest(BaseModel):
    # 空串（或只有空白）= 恢复成 config.py 里内置的那份
    text: str = ""


def _prompt_payload():
    """当前生效的提示词 + 它是哪来的 + 上限。单独走一个接口，不塞进 _state()——
    整段提示词有几千字，每次刷配置都带一遍太浪费。"""
    custom = settings.prompt_text()
    return {
        "text": settings.system_prompt(),
        "custom": bool(custom),
        "limit": settings.PROMPT_LIMIT,
    }


@router.get("/api/admin/prompt")
def prompt_state(request: Request):
    _require(request)
    return _prompt_payload()


@router.post("/api/admin/prompt")
def save_prompt(req: PromptRequest, request: Request):
    _require(request)
    try:
        settings.set_prompt(req.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _prompt_payload()
