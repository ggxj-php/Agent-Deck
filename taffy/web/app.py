"""网页后端：登录鉴权 + SSE 聊天 + 会话持久化。

会话历史存在 SQLite 里（见 taffy/store.py），**不再常驻内存**——每轮请求现读现跑现写，
内存只跟「同时有几个请求」有关，跟「有多少个会话」无关。

所有页面和接口默认都要登录，只有下面 PUBLIC_* 里的白名单例外；后台 /admin 走它自己
那套口令，不跟用户账号混在一起。
"""
import base64
import json
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import auth, captcha, context, db, settings, store, uploads
from .. import persona as persona_mod
from ..config import CONTEXT_COMPRESS_AT
from ..core import TaffyAgent
from ..kb import warmup
from ..tools import render as render_tool
from . import stickers
from .admin import router as admin_router
from .content import router as content_router

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _sse_payload(event):
    """把事件编成 SSE 的 data 段，顺手清掉没法 utf-8 编码的字符。

    json.dumps 不会报错，但带孤立代理字符（U+DC80–U+DCFF）的字符串在 Starlette
    真正 encode 的时候会抛 UnicodeEncodeError——那时响应头已经发出去了，前端只能
    看到一个断掉的流，表现为工具调用永远「运行中」。这里先编一遍，编不过就替换掉。
    """
    payload = json.dumps(event, ensure_ascii=False)
    try:
        payload.encode("utf-8")
        return payload
    except UnicodeEncodeError:
        return payload.encode("utf-8", "replace").decode("utf-8")


# ---------------- 限速 ----------------

class _Limiter:
    """内存里的滑动窗口限速。

    单进程够用；以后真开多 worker 得换成 Redis 或数据库计数，否则每个进程各算各的。
    """

    def __init__(self, window, limit):
        self.window = float(window)
        self.limit = int(limit)
        self._hits = {}
        self._lock = threading.Lock()

    def ok(self, key):
        now = time.time()
        with self._lock:
            if len(self._hits) > 5000:        # 兜底防内存涨爆
                self._hits.clear()
            stamps = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(stamps) >= self.limit:
                self._hits[key] = stamps
                return False
            stamps.append(now)
            self._hits[key] = stamps
            return True


_login_limiter = _Limiter(10 * 60, 10)     # 10 分钟内最多试 10 次
_register_limiter = _Limiter(3600, 5)      # 1 小时最多注册 5 个（按 IP）
_captcha_limiter = _Limiter(10 * 60, 40)   # 10 分钟最多刷 40 张验证码（按 IP）


# ---------------- 每会话的串行锁 ----------------
# 同一个会话的两条消息同时进来会交叉写历史，用进程内锁串一下。
# 锁本身很小，按会话 id 建，用完也不删（几千个 Lock 也就几百 KB）。
_locks = {}
_locks_guard = threading.Lock()


def _session_lock(session_id):
    with _locks_guard:
        lock = _locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _locks[session_id] = lock
        return lock


# ---------------- 鉴权 ----------------

# 这些路径不需要登录：登录页、静态资源、后台（后台自己校验口令）、探活
PUBLIC_PREFIXES = (
    "/static/", "/admin", "/api/admin",
    "/api/auth/login", "/api/auth/register", "/api/auth/captcha",
    "/api/ping", "/api/stickers", "/api/persona", "/favicon.ico",
)
PUBLIC_EXACT = {"/login"}


def _is_https(request):
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return proto.split(",")[0].strip().lower() == "https"


def _set_login_cookie(response, token, request):
    response.set_cookie(
        auth.COOKIE_NAME, token,
        max_age=auth.TOKEN_TTL,
        httponly=True,          # JS 读不到，XSS 也偷不走
        samesite="lax",         # 跨站 POST 不会带上，挡掉大部分 CSRF
        secure=_is_https(request),
        path="/",
    )


def _client_ip(request):
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _user_of(request):
    """从 request.state 拿当前用户；中间件保证已登录的请求一定有。"""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="还没登录喵")
    return user


# ---------------- 应用 ----------------


@asynccontextmanager
async def lifespan(_app):
    db.init()                        # 建表（幂等）
    try:
        # 第一次启动建默认人设（老的 prompt 会迁到 personas/taffy/）
        if persona_mod.ensure_default():
            print("[persona] 已生成默认人设 personas/taffy", flush=True)
    except Exception as exc:         # noqa: BLE001
        print(f"[persona] 初始化默认人设失败：{exc}", flush=True)
    try:
        # 第一次启动建默认管理员（密码见 .env 的 TAFFY_ADMIN_PASSWORD，没配就随机生成并打日志）
        auth.ensure_default_admin()
    except Exception as exc:         # noqa: BLE001
        print(f"[admin] 初始化默认管理员失败：{exc}", flush=True)
    threading.Thread(target=warmup, daemon=True).start()
    yield


app = FastAPI(title="Taffy Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(admin_router)
app.include_router(content_router)


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    """页面和静态资源一律「每次先问服务器再用缓存」。"""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in ("/", "/admin", "/login"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def require_login(request, call_next):
    """没登录的人一律进不来（除了白名单）。

    页面重定向到 /login；接口直接 401，前端看到 401 就跳登录页。
    """
    path = request.url.path
    if path in PUBLIC_EXACT or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return await call_next(request)

    user = auth.resolve_token(request.cookies.get(auth.COOKIE_NAME))
    if user and auth.is_banned(user):
        user = None                  # 被封号（含临时封禁未到期）的账号，登录态还在也不放行
    if not user:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "还没登录喵"}, status_code=401)
        return RedirectResponse(url="/login", status_code=303)

    request.state.user = user
    return await call_next(request)


# ---------------- 页面 ----------------


@app.get("/")
def index(request: Request):
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/login")
def login_page(request: Request):
    if auth.resolve_token(request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse(url="/", status_code=303)
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))


# ---------------- 账号 ----------------


class AuthRequest(BaseModel):
    name: str = ""
    password: str = ""
    invite: str = ""
    # 只有注册要填：图形验证码的 id 和用户看到的答案
    captcha_id: str = ""
    captcha: str = ""


@app.get("/api/auth/captcha")
def get_captcha(request: Request):
    """发一张图形验证码。注册前要先拿到它，不然注册接口一律拒绝。"""
    ip = _client_ip(request)
    if not _captcha_limiter.ok(ip):
        raise HTTPException(status_code=429, detail="验证码刷太多次了喵，过一会儿再试")
    ident, data_url = captcha.new_data_url(ip)
    return {"id": ident, "image": data_url, "ttl": captcha.TTL}


@app.post("/api/auth/register")
def register(req: AuthRequest, request: Request):
    ip = _client_ip(request)
    if not _register_limiter.ok(ip):
        raise HTTPException(status_code=429, detail="注册太频繁了喵，过一会儿再试")
    ok, why = captcha.verify(req.captcha_id, req.captcha)
    if not ok:
        raise HTTPException(status_code=400, detail=why)
    user, err = auth.register(req.name, req.password, req.invite)
    if err:
        raise HTTPException(status_code=400, detail=err)
    token = auth.issue_token(user["id"], ip, request.headers.get("user-agent", ""))
    resp = JSONResponse({"ok": True, "user": auth.public_user(user)})
    _set_login_cookie(resp, token, request)
    return resp


@app.post("/api/auth/login")
def login(req: AuthRequest, request: Request):
    ip = _client_ip(request)
    if not _login_limiter.ok(ip + "|" + (req.name or "")):
        raise HTTPException(status_code=429, detail="试太多次了喵，歇 10 分钟再来")
    user = auth.authenticate(req.name, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码不对喵")
    token = auth.issue_token(user["id"], ip, request.headers.get("user-agent", ""))
    resp = JSONResponse({"ok": True, "user": auth.public_user(user)})
    _set_login_cookie(resp, token, request)
    return resp


@app.post("/api/auth/logout")
def logout(request: Request):
    auth.revoke_token(request.cookies.get(auth.COOKIE_NAME))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


class PasswordRequest(BaseModel):
    old_password: str = ""
    new_password: str = ""


@app.post("/api/auth/password")
def change_password(req: PasswordRequest, request: Request):
    """改自己的密码（要先验旧密码）。"""
    user = _user_of(request)
    err = auth.change_password(user["id"], req.old_password, req.new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    user = _user_of(request)
    return {
        "user": auth.public_user(user),
        "sessions": store.count_for_user(user["id"]),
        "db": db.health()["ok"],
    }


# ---------------- 通用 ----------------


@app.get("/api/ping")
def ping():
    """前端从后台切回来时探活用，不碰会话。"""
    return {"ok": True}


@app.get("/api/persona")
def persona_info():
    """当前人设的公开信息：名字、称呼、头像、欢迎语、状态文案。

    是公开的——聊天页拿它来渲染标题、欢迎语和输入框提示。
    """
    return persona_mod.public()


@app.get("/api/persona/avatar")
def persona_avatar(pid: str = ""):
    """当前（或指定）人设的头像。"""
    path = persona_mod.avatar_path(pid or None)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/persona/icon")
def persona_icon(pid: str = ""):
    """网页图标（favicon）：跟着身份卡换。"""
    path = persona_mod.icon_path(pid or None)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/persona/sticker/{name}")
def persona_sticker(name: str):
    """当前人设的表情包文件。"""
    base = persona_mod.stickers_dir()
    safe = os.path.basename(name.replace("\\", "/"))
    full = os.path.join(base, safe)
    if not safe or not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="没有这张表情包喵")
    return FileResponse(full, headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/stickers")
def sticker_library():
    """网页聊天要的表情包清单：{心情: [文件名, ...]}。"""
    return stickers.library()["moods"]


@app.get("/api/artifact/{artifact_id}")
def get_artifact(artifact_id: str):
    """把塔菲画的那张图取出来给 iframe 渲染。"""
    item = render_tool.get_artifact(artifact_id)
    if not item:
        raise HTTPException(status_code=404, detail="这张图过期或者不存在了喵")
    return Response(
        content=item["html"],
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": render_tool.ARTIFACT_CSP,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/uploads/{image_id}")
def get_upload(image_id: str, request: Request):
    """会话历史里那些图片：按 id 取回来给前端渲染缩略图。

    只能取自己传的（owner 校验），别人的 id 一律当不存在。
    """
    user = _user_of(request)
    owner = uploads.owner_of(image_id)
    if owner is not None and int(owner) != int(user["id"]):
        raise HTTPException(status_code=404, detail="图片不存在喵")
    url = uploads.data_url(image_id)
    if not url or "," not in url:
        raise HTTPException(status_code=404, detail="图片过期了喵")
    header, payload = url.split(",", 1)
    mime = header[5:].split(";")[0] or "image/jpeg"
    try:
        raw = base64.b64decode(payload)
    except Exception:                       # noqa: BLE001
        raise HTTPException(status_code=500, detail="图片读坏了喵")
    return Response(content=raw, media_type=mime,
                    headers={"Cache-Control": "private, max-age=3600"})


# ---------------- 会话 ----------------


class NewSessionRequest(BaseModel):
    title: str = ""


@app.get("/api/sessions")
def list_sessions(request: Request):
    user = _user_of(request)
    return {"sessions": store.list_for_user(user["id"])}


@app.post("/api/sessions")
def create_session(req: NewSessionRequest, request: Request):
    user = _user_of(request)
    return {"session": store.create(user["id"], req.title)}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, request: Request):
    user = _user_of(request)
    if not store.delete(user["id"], session_id):
        raise HTTPException(status_code=404, detail="会话不存在喵")
    return {"ok": True}


class RenameRequest(BaseModel):
    title: str = ""


@app.patch("/api/sessions/{session_id}")
def rename_session(session_id: str, req: RenameRequest, request: Request):
    user = _user_of(request)
    if not store.get(user["id"], session_id):
        raise HTTPException(status_code=404, detail="会话不存在喵")
    store.rename(user["id"], session_id, req.title)
    return {"ok": True}


@app.get("/api/sessions/{session_id}/messages")
def session_messages(session_id: str, request: Request):
    """侧边栏切换会话时，把这条会话的历史取回来渲染。"""
    user = _user_of(request)
    sess = store.get(user["id"], session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在喵")
    messages = store.load_messages(session_id)
    full = [{"role": "system", "content": settings.system_prompt()}] + messages
    usage = context.usage(full, settings.context_limit())
    return {
        "session": {"id": sess["id"], "title": sess["title"]},
        "history": store.display(messages),   # 只留用户和塔菲的正文
        "context": usage,
    }


@app.get("/api/context")
def context_state(request: Request, session_id: str = ""):
    """聊天页那个「上下文 xx%」。"""
    user = _user_of(request)
    limit = settings.context_limit()
    sess = store.get(user["id"], session_id) if session_id else None
    if not sess:
        return {"known": False, "tokens": 0, "limit": limit, "percent": 0.0,
                "compress_at": CONTEXT_COMPRESS_AT, "messages": 0}
    messages = store.load_messages(session_id)
    full = [{"role": "system", "content": settings.system_prompt()}] + messages
    return {"known": True, **context.usage(full, limit)}


# ---------------- 聊天 ----------------


class ChatRequest(BaseModel):
    session_id: str
    message: str = ""
    image: str | None = None           # 老字段，兼容旧前端（等价于 images 一张）
    images: list[str] | None = None    # 一次可以传多张
    thinking: bool | None = None


@app.post("/api/chat")
def chat(req: ChatRequest, request: Request):
    """收一条消息，把 agent 的事件流原样转成 SSE 返回。

    这里**不再有常驻的 agent**：每个请求现从库里读历史、建一个 TaffyAgent、跑完写回去。
    """
    user = _user_of(request)
    sess = store.get(user["id"], req.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在喵（可能被删了，刷新一下）")

    lock = _session_lock(req.session_id)
    owner_id = user["id"]

    def events():
        # 同一个会话串行处理，避免两条消息交叉写乱历史
        with lock:
            history = store.load_messages(req.session_id)
            agent = TaffyAgent(messages=history, cut_off=bool(sess["cut_off"]), owner=owner_id)
            is_first = not history
            try:
                for event in agent.ask_stream(req.message, req.image, req.thinking, req.images):
                    yield f"data: {_sse_payload(event)}\n\n"
            finally:
                # 不管正常结束、报错还是浏览器中途断开，都把这次跑出来的历史落库
                try:
                    store.save_messages(
                        req.session_id, agent.messages[1:], agent.cut_off,
                        first_user_text=(req.message if is_first else ""),
                    )
                except Exception:            # noqa: BLE001
                    pass                     # 落库失败别把已经吐出去的流搞断
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
