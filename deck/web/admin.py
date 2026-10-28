"""后台管理：改模型、改 Key、管用户、看流量。挂在 /admin 和 /api/admin/* 底下。

**鉴权复用了用户账号**（不再是自己那套按天变的口令）：
必须先以普通用户身份登录，且那个账号的 is_admin = 1，才能进后台。
管理员可以在「用户管理」里给谁开权限、封号、改密码。

默认管理员在第一次启动时自动创建（见 auth.ensure_default_admin）。
"""
import io
import json
import os
import threading
import time
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel

from .. import auth, capabilities, config_report, db, geoip, llm, persona, settings, store, tools
from ..config import CONTEXT_COMPRESS_AT, EMBED_DIM
from ..kb import stats as kb_stats
from . import blacklist, sysinfo, traffic, update

# 启动后台线程对一次账：名单里有、UFW 里没有的补回去（正常不会发生）
blacklist.start_sync()

router = APIRouter()

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

def _current_user(request):
    """从 cookie 认人（不抛异常，认不出来返回 None）。"""
    return auth.resolve_token(request.cookies.get(auth.COOKIE_NAME))


def _admin(request):
    """必须登录 + 是管理员才放行。"""
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="先登录")
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="要管理员权限")
    request.state.user = user
    return user


def _require(request):
    return _admin(request)


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
def admin_page(request: Request):
    """后台页面：不是管理员就踢回登录页。"""
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/admin", status_code=303)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="要管理员权限")
    return FileResponse(os.path.join(STATIC_DIR, "admin.html"))


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
        raise HTTPException(status_code=400, detail="聊天和图片的模型名都得填")
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
        raise HTTPException(status_code=400, detail=f"{name}的接口地址要以 http:// 或 https:// 开头")
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
        raise HTTPException(status_code=400, detail="一个都没填，那就先不动它")
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
def traffic_state(request: Request, days: int = 7, overseas: int = 0,
                  scanners: int = 1, site: str = "www"):
    """访问日志统计：页面访问 / 独立 IP / 地区分布 / 热门路径 / 状态码。

    口径和命令行版 /root/taffy-traffic.py 一样：排掉本机、扫描器、腾讯网段，
    默认再排掉海外 IP（只要真实国内访客）。overseas=1 就全算上。
    结果在模块里缓存 60 秒，重复点不会反复扫日志。
    """
    _require(request)
    return traffic.snapshot(days=days, overseas=bool(overseas),
                            scanners=bool(scanners), site=site)


# 同一个 IP 半小时内分析过就直接给结果，别让人多点几次就多烧几次 token
_ip_cache = {}
_ip_cache_lock = threading.Lock()
IP_ANALYSIS_TTL = 1800

_IP_SYSTEM_PROMPT = (
    "你是网站运维安全分析师。根据给定的访问日志，写一份**具体**的行为分析。\n"
    "必须引用日志里真实出现的路径、次数、状态码、时间，不许编造。\n"
    "严格按下面格式输出（每项一行，标题后用中文冒号）：\n"
    "角色：<真实访客 / 搜索引擎爬虫 / 漏洞扫描器 / 账号爆破 / 内容采集器 / "
    "云厂商探测 / 网络空间测绘 / 其他>\n"
    "结论：<风险等级 高危/中危/低危，加一句为什么>\n"
    "具体在干什么：<把行为按时间顺序还原成 2~4 句，带上路径和次数，"
    "比如「先探 /x 3 次、再连打 /y 16 次」>\n"
    "有没有得手：<综合状态码 + 响应体大小判断。注意 HTTP 200 不等于成功："
    "很多接口失败也返回 200（例如登录失败会返回 {\"success\":false}，只有几十字节），"
    "真正的成功响应体通常大得多。敏感文件被读走会返回 2xx 且体积明显偏大。"
    "数据不足以判断就直说「无法确定」，不要猜>\n"
    "建议：<一句话：放行 / 观察 / 封禁，加简短理由>\n"
    "总字数控制在 250 字以内，不要客套。"
)


class IpAnalyzeRequest(BaseModel):
    ip: str
    days: int = 7


def _ip_prompt(d):
    """把 ip_detail 的材料压成一段给模型看的文字。"""
    g = d["geo"]
    lines = [
        f"IP：{d['ip']}",
        f"归属：{g['country']} {g['region']} {g['city']} / {g['isp']} / {g['as']}"
        + ("（机房/云主机）" if g.get("idc") else ""),
        f"时间窗：{d['first']} ~ {d['last']}，共 {d['count']} 次请求，"
        f"平均 {d.get('rate_per_min', 0)} 次/分钟",
        f"裸连服务器 IP（Host 头是 IP 不是域名）：{d.get('raw_host', 0)} 次",
        f"加载前端资源（/static 等）：{d.get('app_hits', 0)} 次（真人打开页面必然有）",
        "状态码：" + "、".join(f"{c}×{n}" for c, n in d["statuses"]),
        "访问路径：" + "、".join(f"{p}×{n}" for p, n in d["paths"]),
    ]
    if d.get("sensitive"):
        lines.append("敏感路径命中（路径 次数 状态码）：")
        for h in d["sensitive"]:
            st = "、".join(f"{c}×{n}" for c, n in h["statuses"])
            sz = "、".join(f"{s.split(':')[0]}→{s.split(':')[1]}B×{n}" for s, n in (h.get("sizes") or []))
            lines.append(f"  {h['path']} ×{h['count']}  → {st}   响应体: {sz}")
    if d.get("login"):
        lines.append("登录接口调用（路径 次数 状态码 响应体大小）：")
        for h in d["login"]:
            st = "、".join(f"{c}×{n}" for c, n in h["statuses"])
            sz = "、".join(f"{s.split(':')[0]}→{s.split(':')[1]}B×{n}" for s, n in (h.get("sizes") or []))
            lines.append(f"  {h['path']} ×{h['count']}  → {st}   响应体: {sz}")
    lines += [
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
        max_tokens=1200, thinking=False,
    )
    text = (text or "").strip() or "模型没给出分析结果"
    with _ip_cache_lock:
        _ip_cache[key] = (time.time(), text)
    return {"ok": True, "analysis": text, "cached": False, "detail": detail}


# ---------- 全量日志 AI 风险分析 ----------

_risk_cache = {}
_risk_cache_lock = threading.Lock()
RISK_ANALYSIS_TTL = 1800

_RISK_SYSTEM_PROMPT = (
    "你是网站安全分析师。给你一批从 nginx 访问日志里按规则粗筛出来的可疑 IP，"
    "请逐个判断风险。\n"
    "严格只输出 JSON 数组，不要任何解释、不要 Markdown 代码块：\n"
    '[{"ip":"1.2.3.4","risk":"high|medium|low","role":"不超过8个字","reason":"不超过40个字"}]\n'
    "要求：\n"
    "1. risk=high 表示正在主动攻击（撞库爆破、漏洞利用、路径穿越）；\n"
    "   medium 表示扫描探测（找敏感文件、摸端口）；low 表示可疑但危害小。\n"
    "2. 必须覆盖给出的每一个 IP，顺序照原样。\n"
    "3. 依据只能来自给定数据，不许编造。"
)


def _risk_prompt(cands):
    lines = [
        "格式：IP | 归属/ISP | 请求数 | 敏感路径命中 | 登录接口 | 裸连次数 | 状态码 | 路径 | UA",
        "",
    ]
    for c in cands:
        paths = "、".join(f"{p}×{n}" for p, n in c["paths"][:5])
        status = "、".join(f"{s}×{n}" for s, n in c["statuses"][:4])
        lines.append(
            f"{c['ip']} | {c['place'] or '未知'} / {c['isp'][:40]} | {c['requests']} | "
            f"{c['sensitive']} | {c['login']} | {c['rawhost']} | {status} | {paths} | {c['ua'][:70]}"
        )
    return "\n".join(lines)


def _parse_risk_json(text):
    """把模型返回的 JSON 抠出来；被包了 ```json 也认。"""
    if not text:
        return []
    s = text.strip()
    if s.startswith("```"):
        # 去掉围栏
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.endswith("```"):
            s = s[:-3]
    start = s.find("[")
    end = s.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(s[start:end + 1])
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("ip"):
            out.append({
                "ip": str(item.get("ip")).strip(),
                "risk": str(item.get("risk") or "medium").strip().lower(),
                "role": str(item.get("role") or "").strip()[:20],
                "reason": str(item.get("reason") or "").strip()[:80],
            })
    return out


class RiskScanRequest(BaseModel):
    days: int = 7
    site: str = "all"      # all / www / api


@router.post("/api/admin/risk-scan")
def risk_scan(req: RiskScanRequest, request: Request):
    """全量日志粗筛 + AI 终审：找出高危 IP，按风险排好序给前端。"""
    _require(request)
    days = max(1, min(int(req.days or 7), 90))
    site = traffic.norm_site(req.site or "all")
    key = (days, site)
    now = time.time()
    with _risk_cache_lock:
        hit = _risk_cache.get(key)
        if hit and now - hit[0] < RISK_ANALYSIS_TTL:
            return {"ok": True, "cached": True, **hit[1]}

    scan = traffic.risk_candidates(days=days, limit=30, site=site)
    if not scan.get("ok"):
        raise HTTPException(status_code=400, detail=scan.get("error") or "读不到日志")
    cands = scan.get("candidates") or []
    if not cands:
        return {"ok": True, "cached": False, "total_scanned": scan.get("total_scanned", 0),
                "items": [], "note": "这段时间没有可疑 IP"}

    text = llm.complete(
        [{"role": "system", "content": _RISK_SYSTEM_PROMPT},
         {"role": "user", "content": _risk_prompt(cands)}],
        max_tokens=1600, thinking=False,
    )
    judged = {j["ip"]: j for j in _parse_risk_json(text)}

    # 模型没覆盖到的，按打分兜底标 medium，绝不留空
    items = []
    for c in cands:
        j = judged.get(c["ip"]) or {}
        items.append({
            **c,
            "risk": j.get("risk") or "medium",
            "role": j.get("role") or "",
            "reason": j.get("reason") or "模型未给出，按规则判定",
        })
    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: (order.get(x["risk"], 1), -x["score"]))

    payload = {
        "total_scanned": scan.get("total_scanned", 0),
        "site": site,
        "site_label": traffic.SITE_LABELS.get(site, "全部站点"),
        "items": items,
        "raw_model_output": text,
    }
    with _risk_cache_lock:
        _risk_cache[key] = (time.time(), payload)
    return {"ok": True, "cached": False, **payload}


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
        raise HTTPException(status_code=400, detail="只能从 gitee 或者 github 拉")
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
        raise HTTPException(status_code=400, detail="只能从 gitee 或者 github 拉")
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
                       f"或者填 0（自动探测）",
            )
        settings.set_embed_max_tokens(req.embed_max_tokens)
    if req.embed_enabled is not None:
        settings.set_embed_enabled(req.embed_enabled)
    if not (model or base or key):
        # 只拨了开关 / 只改了上限也算改过，别当成「什么都没填」拒掉
        if req.embed_enabled is None and req.embed_max_tokens is None:
            raise HTTPException(status_code=400, detail="一个都没填，那就先不动它")
        return _state()
    settings.update_embed(model, base, key)
    return _state()


# ---------- 对话上下文 ----------
# 模型的上下文窗口有多大。聊天页那个「上下文 xx%」和自动压缩（见 deck/context.py）
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
            detail=f"上下文上限得在 {CONTEXT_MIN} ~ {CONTEXT_MAX} 之间",
        )
    settings.set_context_limit(req.limit)
    return _state()


# ---------- 系统提示词（人设）----------
# 整段可改，改过的存在 admin.json 里（不进 git，所以更新代码不会冲掉）。
# 取的时机是开会话时（见 core.DeckAgent.__init__）：改完新开的会话就是新的、不用重启，
# 已经在聊的会话还留着它自己那份。所以「当前生效的那份」和「某个会话在用的那份」
# 不一定一样，后台只负责显示/改前者。


class PromptRequest(BaseModel):
    # 空串（或只有空白）= 恢复成 config.py 里内置的那份
    text: str = ""


def _prompt_payload():
    """当前人设的提示词 + 它属于哪个人设 + 上限。

    单独走一个接口，不塞进 _state()——整段提示词有几千字，每次刷配置都带一遍太浪费。
    """
    cur = persona.current()
    return {
        "text": persona.system_prompt(),
        "persona": cur["id"],
        "persona_name": cur.get("name") or cur["id"],
        "limit": settings.PROMPT_LIMIT,
    }


@router.get("/api/admin/prompt")
def prompt_state(request: Request):
    _require(request)
    return _prompt_payload()


@router.post("/api/admin/prompt")
def save_prompt(req: PromptRequest, request: Request):
    """存的是**当前人设**的提示词。"""
    _require(request)
    meta, err = persona.save(persona.current_id(), {"prompt": req.text})
    if err:
        raise HTTPException(status_code=400, detail=err)
    return _prompt_payload()

# ---------------- 用户管理 ----------------

_NAME_ERR = "用户名 3~20 位，只能中文 / 字母 / 数字 / _ -"


def _user_row(row):
    item = dict(row)
    item["is_admin"] = bool(item.get("is_admin"))
    item["disabled"] = bool(item.get("disabled"))
    item["ban_until"] = float(item.get("ban_until") or 0)
    item["banned"] = auth.is_banned(item)          # 临时封禁到期后这里就是 False
    item["sessions"] = store.count_for_user(item["id"])
    item["last_ip"] = item.get("last_ip") or ""
    item["ip_location"] = geoip.lookup(item["last_ip"])
    return item


@router.get("/api/admin/users")
def list_users(request: Request):
    """用户列表：权限、封号状态、邀请码、注册时间、最后登录、会话数。"""
    me = _require(request)
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT id, name, invite_code, invited_by, created_at, is_admin, disabled, "
            "       ban_until, note, last_login, last_active, "
            "       (SELECT ip FROM auth_tokens t WHERE t.user_id = users.id "
            "        ORDER BY t.created_at DESC LIMIT 1) AS last_ip "
            "FROM users ORDER BY id"
        ).fetchall()
    return {"users": [_user_row(row) for row in rows], "me": me["id"],
            "total": len(rows)}


class UserCreate(BaseModel):
    name: str = ""
    password: str = ""
    is_admin: bool = False
    note: str = ""


@router.post("/api/admin/users")
def create_user(req: UserCreate, request: Request):
    """管理员直接开号（不用邀请码）。"""
    _require(request)
    user, err = auth.create_user(req.name, req.password, invited_by="admin",
                                 is_admin=req.is_admin, note=req.note)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True, "user": auth.public_user(user)}


class UserUpdate(BaseModel):
    is_admin: bool | None = None
    disabled: bool | None = None
    # 封多久（分钟）。0 = 永久；不传就按 disabled 走（True 永久 / False 解封）
    ban_minutes: int | None = None
    note: str | None = None
    password: str | None = None


@router.patch("/api/admin/users/{user_id}")
def update_user(user_id: int, req: UserUpdate, request: Request):
    """改权限 / 封号 / 备注 / 重置密码。

    两条护栏：不能把自己降权或封号（免得把自己锁在门外），
    也不能把最后一个管理员干掉。
    """
    me = _require(request)
    target = auth.find_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="没有这个用户")

    if target["id"] == me["id"]:
        if req.is_admin is False:
            raise HTTPException(status_code=400, detail="不能取消自己的管理员权限")
        if req.disabled or req.ban_minutes is not None:
            raise HTTPException(status_code=400, detail="不能封禁自己")

    if target.get("is_admin") and req.is_admin is False:
        with db.cursor() as conn:
            others = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE is_admin = 1 AND id != ?",
                (target["id"],)).fetchone()["n"]
        if not others:
            raise HTTPException(status_code=400, detail="这是最后一个管理员，不能降权")

    with db.cursor() as conn:
        if req.is_admin is not None:
            conn.execute("UPDATE users SET is_admin = ? WHERE id = ?",
                         (1 if req.is_admin else 0, target["id"]))
        if req.disabled is not None:
            conn.execute("UPDATE users SET disabled = ? WHERE id = ?",
                         (1 if req.disabled else 0, target["id"]))
        if req.note is not None:
            conn.execute("UPDATE users SET note = ? WHERE id = ?",
                         (req.note[:200], target["id"]))
    # 封禁：优先按 ban_minutes 走（0 = 永久），其次按 disabled（True 永久 / False 解封）
    if req.ban_minutes is not None:
        auth.set_ban(target["id"], req.ban_minutes)      # 内部会踢掉他的登录态
    elif req.disabled is False:
        auth.clear_ban(target["id"])
    elif req.disabled is True:
        auth.set_ban(target["id"], 0)

    if req.password:
        err = auth.set_password(target["id"], req.password)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if target["id"] != me["id"]:
            auth.revoke_all(target["id"])       # 改了别人密码就把他的登录态全踢掉
    return {"ok": True}


@router.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, request: Request):
    me = _require(request)
    if user_id == me["id"]:
        raise HTTPException(status_code=400, detail="不能删自己")
    target = auth.find_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="没有这个用户")
    if target.get("is_admin"):
        with db.cursor() as conn:
            others = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE is_admin = 1 AND id != ?",
                (user_id,)).fetchone()["n"]
        if not others:
            raise HTTPException(status_code=400, detail="这是最后一个管理员，不能删")
    with db.cursor() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"ok": True}


# ---------------- 人设管理 ----------------

class PersonaRequest(BaseModel):
    id: str = ""
    name: str = ""
    self_name: str = ""
    user_name: str = ""
    welcome: str = ""
    placeholder: str = ""
    status_online: str = ""
    status_thinking: str = ""
    stickers: str = ""
    theme: str = ""
    icon: str = ""
    prompt: str | None = None


@router.get("/api/admin/personas")
def personas_list(request: Request):
    _require(request)
    return {"current": persona.current_id(), "personas": persona.list_personas()}


@router.get("/api/admin/personas/{pid}")
def persona_detail(pid: str, request: Request):
    _require(request)
    if not persona.exists(pid):
        raise HTTPException(status_code=404, detail="没有这个人设")
    meta = persona._meta(pid)
    meta["is_current"] = pid == persona.current_id()
    meta["stickers_count"] = len(persona.sticker_files(pid))
    return meta


@router.post("/api/admin/personas")
def persona_create(req: PersonaRequest, request: Request):
    _require(request)
    if not persona.safe_id(req.id):
        raise HTTPException(status_code=400,
                            detail="id 只能是 2~24 位小写字母 / 数字 / _ -，且以字母开头")
    if persona.exists(req.id):
        raise HTTPException(status_code=409, detail="这个人设 id 已经有了")
    data = req.model_dump()
    data.pop("id", None)
    meta, err = persona.save(req.id, data)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True, "persona": meta}


@router.patch("/api/admin/personas/{pid}")
def persona_update(pid: str, req: PersonaRequest, request: Request):
    _require(request)
    if not persona.exists(pid):
        raise HTTPException(status_code=404, detail="没有这个人设")
    data = req.model_dump()
    data.pop("id", None)
    meta, err = persona.save(pid, data)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True, "persona": meta}


@router.post("/api/admin/personas/{pid}/activate")
def persona_activate(pid: str, request: Request):
    _require(request)
    try:
        persona.set_current(pid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "current": persona.current_id(), "persona": persona.public()}


@router.delete("/api/admin/personas/{pid}")
def persona_delete(pid: str, request: Request):
    _require(request)
    ok, err = persona.delete(pid)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True, "current": persona.current_id()}


# ---------------- 服务器能力 ----------------

@router.get("/api/admin/capabilities")
def capabilities_state(request: Request):
    """这台机器能提供哪些运维功能（流量 / 黑名单 / 一键更新 / 境外拦截）。

    后台按这个隐藏用不了的面板，换台机器部署也不会满屏报错。
    """
    _require(request)
    return capabilities.cached()


# ---------------- 工具开关 ----------------

class ToolGroupsRequest(BaseModel):
    groups: list[str] = []


@router.get("/api/admin/tools")
def tools_state(request: Request):
    """工具分组开关：每组有哪些工具、开没开、现在一共开了几个。"""
    _require(request)
    return tools.groups_payload()


@router.post("/api/admin/tools")
def tools_save(req: ToolGroupsRequest, request: Request):
    _require(request)
    settings.set_tool_groups(req.groups)
    return tools.groups_payload()


# ---------------- 配置总览 ----------------

@router.get("/api/admin/config")
def config_overview(request: Request):
    """把散在 .env / admin.json / config.py 里的配置汇总成一张表（只读）。

    每项都标了「在哪改」「要不要重启」，省得猜。
    """
    _require(request)
    try:
        return config_report.report()
    except Exception as exc:          # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"配置总览生成失败：{exc}")


# ---------------- 共享约束提示词 ----------------

class CorePromptRequest(BaseModel):
    text: str = ""


def _core_payload():
    from ..config import CORE_PROMPT

    custom = settings.core_prompt()
    return {
        "text": custom or CORE_PROMPT,
        "custom": bool(custom.strip()),
        "default": CORE_PROMPT,
        "limit": settings.PROMPT_LIMIT,
    }


@router.get("/api/admin/core-prompt")
def core_prompt_state(request: Request):
    """所有身份卡共用的硬约束。换身份卡不会动这一段。"""
    _require(request)
    return _core_payload()


@router.post("/api/admin/core-prompt")
def core_prompt_save(req: CorePromptRequest, request: Request):
    _require(request)
    try:
        settings.set_core_prompt(req.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _core_payload()


# ---------------- 身份卡导入 / 导出 ----------------

_EXPORT_SKIP = (".pyc", ".tmp")


@router.get("/api/admin/personas/{pid}/export")
def persona_export(pid: str, request: Request):
    """把一张身份卡打包成 zip：persona.json + prompt.md + stickers/*。"""
    _require(request)
    pid = persona.safe_id(pid)
    if not pid or not persona.exists(pid):
        raise HTTPException(status_code=404, detail="没有这个人设")
    buf = io.BytesIO()
    base = persona._dir(pid)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(base):
            for name in files:
                if name.endswith(_EXPORT_SKIP):
                    continue
                full = os.path.join(root, name)
                rel = os.path.relpath(full, base)
                try:
                    zf.write(full, rel)
                except OSError:
                    continue
    data = buf.getvalue()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="persona-{pid}.zip"',
            "Content-Length": str(len(data)),
        },
    )


MAX_IMPORT_BYTES = 50 * 1024 * 1024


@router.post("/api/admin/personas/import")
async def persona_import(request: Request, pid: str = ""):
    """导入一张身份卡（zip）。

    请求体直接是 zip 文件（不是 multipart），省得再引一个依赖。
    pid 不传就按包里的 persona.json 里的 id。
    """
    _require(request)
    length = int(request.headers.get("content-length") or 0)
    if length > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="压缩包太大（上限 50MB）")
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="压缩包太大（上限 50MB）")

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="这不是一个 zip 包")

    # 先找 persona.json，确定要建成的 id
    meta = None
    for candidate in ("persona.json", "persona/persona.json"):
        try:
            with zf.open(candidate) as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                meta = loaded
                prefix = candidate[: len(candidate) - len("persona.json")]
                break
        except (KeyError, ValueError):
            continue
    if meta is None:
        raise HTTPException(status_code=400, detail="包里没有 persona.json ")

    target = persona.safe_id(pid or meta.get("id") or "")
    if not target:
        raise HTTPException(status_code=400, detail="id 不合法（小写字母开头，2~24 位）")
    if persona.exists(target):
        raise HTTPException(status_code=409, detail=f"身份卡 id「{target}」已经有了")

    base = persona._dir(target)
    os.makedirs(base, exist_ok=True)
    extracted = 0
    for item in zf.infolist():
        name = item.filename.replace("\\", "/")
        if prefix and name.startswith(prefix):
            name = name[len(prefix):]
        if not name or name.endswith("/") or ".." in name or name.startswith("/"):
            continue
        parts = name.split("/")
        # 只允许 persona.json / prompt.md / 图片（表情包 / 头像 / 图标）
        lower = name.lower()
        if not (lower.endswith((".json", ".md")) or lower.endswith(persona_sticker_exts())):
            continue
        if parts[0].startswith("."):
            continue
        dest = os.path.join(base, *parts)
        if not os.path.abspath(dest).startswith(os.path.abspath(base) + os.sep):
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zf.open(item) as src, open(dest, "wb") as dst:
            dst.write(src.read(8 * 1024 * 1024))
        extracted += 1
    if not extracted:
        raise HTTPException(status_code=400, detail="包里没有可导入的文件")

    persona._cache["key"] = None
    saved, err = persona.save(target, {})
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True, "id": target, "files": extracted}


def persona_sticker_exts():
    try:
        from .stickers import EXTS

        return tuple(EXTS)
    except Exception:                    # noqa: BLE001
        return (".png", ".jpg", ".jpeg", ".gif", ".webp")


MAX_ASSET_BYTES = 8 * 1024 * 1024


@router.post("/api/admin/personas/{pid}/asset")
async def persona_asset(pid: str, request: Request, kind: str = "avatar"):
    """上传身份卡的头像 / 网页图标。

    请求体直接是图片（不是 multipart）。头像会缩到 512px 存成 PNG，
    图标同样，保证所有浏览器都能当 favicon 用。
    """
    _require(request)
    pid = persona.safe_id(pid)
    if not pid or not persona.exists(pid):
        raise HTTPException(status_code=404, detail="没有这个人设")
    if kind not in ("avatar", "icon"):
        raise HTTPException(status_code=400, detail="kind 只能是 avatar 或 icon")
    length = int(request.headers.get("content-length") or 0)
    if length > MAX_ASSET_BYTES:
        raise HTTPException(status_code=413, detail="图片太大（上限 8MB）")
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    if len(raw) > MAX_ASSET_BYTES:
        raise HTTPException(status_code=413, detail="图片太大（上限 8MB）")

    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as img:
            img.load()
            img = img.convert("RGBA")
            limit = 512
            img.thumbnail((limit, limit))
            path = os.path.join(persona._dir(pid), f"{kind}.png")
            img.save(path, "PNG")
            size = img.size
    except HTTPException:
        raise
    except Exception:                    # noqa: BLE001
        raise HTTPException(status_code=400, detail="这不是一张能识别的图片")

    persona.save(pid, {kind: f"{kind}.png"})
    persona._cache["key"] = None
    return {"ok": True, "kind": kind, "size": list(size),
            "url": f"/api/persona/{kind}?pid={pid}"}
