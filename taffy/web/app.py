"""网页后端：把 TaffyAgent 的事件流用 SSE 吐给浏览器。

会话隔离靠 session_id：每个 id 对应一个独立的 TaffyAgent 实例，历史各自独立，
前端开新会话就是换一个 id。TaffyAgent 构造时第一条永远是 SYSTEM_PROMPT，
所以就算前端乱传也不会把人设弄丢。
"""
import json
import os
import threading
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import settings
from ..config import CONTEXT_COMPRESS_AT
from ..core import TaffyAgent
from ..kb import warmup
from . import sessions_store, stickers
from .admin import router as admin_router
from .content import router as content_router

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@asynccontextmanager
async def lifespan(_app):
    # 后台先把知识库索引建好（约几秒），第一个提问就不用干等
    threading.Thread(target=warmup, daemon=True).start()
    yield


app = FastAPI(title="Taffy Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# 后台管理：/admin 页面 + /api/admin/* 接口（配置与系统、内容管理两块）
app.include_router(admin_router)
app.include_router(content_router)


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    """页面和静态资源一律「每次先问服务器再用缓存」。

    不带 Cache-Control 时浏览器会自己猜一个缓存时长（按 last-modified 推），
    于是更新完代码后常常还是拿旧的 app.js / admin.html：页面看着是旧的、接口却是新的，
    查起来特别费劲。这里统一让它们带 ETag 回来校验——没变就 304，几乎不花流量。
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in ("/", "/admin"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# session_id -> {"agent": TaffyAgent, "lock": Lock}
_sessions = {}
_sessions_lock = threading.Lock()


class ChatRequest(BaseModel):
    session_id: str
    message: str = ""
    # 可选的内联图片（data:image/...;base64,...）。不落盘，这一轮跑完就丢。
    image: str | None = None


def _session(session_id):
    """按 id 取会话，没有就现场开一个。"""
    with _sessions_lock:
        entry = _sessions.get(session_id)
        if entry is None:
            entry = {"agent": TaffyAgent(), "lock": threading.Lock()}
            _sessions[session_id] = entry
        return entry


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/ping")
def ping():
    """前端从后台切回来时探活用，不碰会话。"""
    return {"ok": True}


@app.get("/api/stickers")
def sticker_library():
    """网页聊天要的表情包清单：{心情: [文件名, ...]}。

    直接读目录，所以后台往库里加一张、网页刷新一下就多一张；文件名带后缀，
    gif 那边就能当动图播。不带登录校验——这些文件本来就在 /static 下公开。
    """
    return stickers.library()["moods"]


@app.post("/api/session")
def new_session():
    """开一个新会话，返回它的 id。"""
    session_id = uuid.uuid4().hex
    _session(session_id)
    return {"session_id": session_id}


@app.get("/api/context")
def context_state(session_id: str = ""):
    """聊天页那个「上下文 xx%」：这段历史估出来用了多少 token、占窗口的几成。

    没聊过的会话就返回 known=false，前端显示 0% 或者干脆不显示（见 taffy/context.py）。
    """
    limit = settings.context_limit()
    with _sessions_lock:
        entry = _sessions.get(session_id)
    if entry is None:
        return {"known": False, "tokens": 0, "limit": limit, "percent": 0.0,
                "compress_at": CONTEXT_COMPRESS_AT, "messages": 0}
    return {"known": True, **entry["agent"].context_usage()}


class SaveRequest(BaseModel):
    session_id: str
    uuid: str = ""


@app.post("/api/session/save")
def save_session(req: SaveRequest):
    """把这轮会话的上下文存成 txt（存文件，不占内存），见 web/sessions_store.py。

    uuid 是主人自己起的文件名，所以要先查重：重了返回 409，让前端换个名字，
    绝不覆盖已经存过的那一份。
    """
    with _sessions_lock:
        entry = _sessions.get(req.session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="这个会话还没聊过喵，先说句话再存")
    agent = entry["agent"]
    # 浅拷贝一份再写：万一一轮回复正在追加历史，也不会写进去半条
    messages = list(agent.messages)
    try:
        # 顺手把「存的时候用了多少上下文」记进存档，导入时能对得上（见 sessions_store.load）
        return sessions_store.save(req.uuid, messages, agent.context_usage())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileExistsError:
        raise HTTPException(
            status_code=409,
            detail=f"已经有一份叫「{req.uuid}」的存档了喵，换个 uuid 再存（不会覆盖原来那份）",
        )


class ImportRequest(BaseModel):
    session_id: str
    uuid: str = ""


@app.post("/api/session/import")
def import_session(req: ImportRequest):
    """把一份存档的历史接回这个会话（见 web/sessions_store.py 的 save/load）。

    接回来的是**原始 messages**，所以聊天页那条「上下文 xx%」立刻变回存档时的用量——
    不然历史很长却显示 0%，自动压缩就一直不触发，下一轮直接撞超长。
    """
    try:
        messages, saved = sessions_store.load(req.uuid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"没有叫「{req.uuid}」的存档喵")
    entry = _session(req.session_id)
    agent = entry["agent"]
    # 跟聊天串行，别一边导入一边追加历史
    with entry["lock"]:
        # 第一条永远是当前的人设（TaffyAgent 自己保证的），后面接上存档里的对话
        agent.messages[:] = [agent.messages[0]] + messages
    return {
        "ok": True,
        "uuid": req.uuid,
        "messages": len(messages),
        "saved_context": saved,
        "history": sessions_store.display(messages),
        "context": agent.context_usage(),
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    """收一条消息，把 agent 的事件流原样转成 SSE 返回。"""
    entry = _session(req.session_id)

    def events():
        # 同一个会话串行处理，避免两条消息交叉写乱历史
        with entry["lock"]:
            for event in entry["agent"].ask_stream(req.message, req.image):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
