"""后台的内容管理接口：表情包库、workspace 文件、会话存档、知识库文档。

跟 admin.py 分开写只是为了让文件别太长：这块是「素材和文件」，那边是「配置和系统」。
登录校验直接复用 admin.py 的那一套。

上传一律走**请求体裸流 + 文件名放 query 里**，不用 multipart：这台服务器只有 2G，
多装一个 python-multipart 不值当，而前端本来就是我们自己写的，怎么传都行。
落盘全是流式的（一块 64KB），绝不吃内存。
"""
import os
import shutil
import tempfile
import threading

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from ..config import KNOWLEDGE_DIR, SESSIONS_DIR
from ..kb import loader as kb_loader
from ..sandbox import WORKSPACE, safe_path
from ..tools.files import delete_file
from . import sessions_store, stickers, update
from .admin import _require

router = APIRouter()

MAX_WORKSPACE_BYTES = 100 * 1024 * 1024    # 单个上传上限
MAX_LIST_ENTRIES = 800                     # 列目录最多列这么多条，免得一个大目录把页面撑死


async def _save_body(request, target, max_bytes, label):
    """把请求体流式写到 target，返回字节数。超限/出错都会把半截文件清掉。"""
    tmp = target + ".part"
    size = 0
    try:
        with open(tmp, "wb") as fp:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{label}太大了喵（上限 {max_bytes // 1024 // 1024}MB）",
                    )
                fp.write(chunk)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    if not size:
        os.remove(tmp)
        raise HTTPException(status_code=400, detail="文件是空的喵")
    os.replace(tmp, target)
    return size


# ---------- 表情包库 ----------

@router.get("/api/admin/stickers")
def stickers_list(request: Request):
    _require(request)
    return stickers.library()


class StickerName(BaseModel):
    name: str = ""


@router.post("/api/admin/stickers/delete")
def stickers_delete(req: StickerName, request: Request):
    _require(request)
    try:
        removed = stickers.remove(req.name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "removed": removed, "library": stickers.library()}


@router.post("/api/admin/stickers/upload")
async def stickers_upload(request: Request, name: str = Query("")):
    """传一张。文件名（= 心情 + 序号 + 后缀）由前端放在 name 里。"""
    _require(request)
    try:
        stickers.check_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    target = stickers.free_path(os.path.basename(name.replace("\\", "/")).strip())
    size = await _save_body(request, target, stickers.MAX_FILE_BYTES, "这张表情包")
    return {"ok": True, "name": os.path.basename(target), "size": size,
            "library": stickers.library()}


@router.post("/api/admin/stickers/import")
async def stickers_import(request: Request):
    """批量导入：请求体是一整个 zip。压缩包内文件名规则见 stickers.py 的说明。"""
    _require(request)
    handle, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="taffy_stickers_")
    os.close(handle)
    try:
        await _save_body(request, tmp_path, stickers.MAX_ZIP_BYTES, "压缩包")
        try:
            result = stickers.import_zip(tmp_path)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"这个压缩包解不开喵（要标准的 .zip）：{type(exc).__name__}: {exc}",
            )
        result["library"] = stickers.library()
        return result
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ---------- workspace 文件 ----------

def _ws_dir(path):
    """把相对路径解析成 workspace 里的目录，越界/不是目录就报错。"""
    try:
        full = safe_path(path or ".")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not os.path.isdir(full):
        raise HTTPException(status_code=404, detail=f"没有这个目录喵：{path}")
    return full


def _entry(full, name, base):
    item = {
        "name": name,
        "path": os.path.relpath(full, base).replace("\\", "/"),
        "dir": os.path.isdir(full),
    }
    try:
        stat = os.stat(full)
        item["size"] = stat.st_size
        item["mtime"] = int(stat.st_mtime)
    except OSError:
        item["size"] = 0
        item["mtime"] = 0
    return item


@router.get("/api/admin/workspace")
def workspace_list(request: Request, path: str = Query("")):
    """列 workspace 里某个目录。跟塔菲的 list_files 看的是同一个地方。"""
    _require(request)
    full = _ws_dir(path)
    try:
        names = sorted(os.listdir(full))
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"读目录失败：{exc}")
    entries = [_entry(os.path.join(full, name), name, WORKSPACE) for name in names]
    truncated = len(entries) > MAX_LIST_ENTRIES
    return {
        "dir": os.path.relpath(full, WORKSPACE).replace("\\", "/"),
        "entries": entries[:MAX_LIST_ENTRIES],
        "truncated": truncated,
    }


@router.post("/api/admin/workspace/upload")
async def workspace_upload(request: Request, path: str = Query("")):
    """传文件进 workspace。path 是相对 workspace 的目标路径（可以带子目录）。"""
    _require(request)
    rel = (path or "").replace("\\", "/").strip().lstrip("/")
    if not rel or rel.endswith("/"):
        raise HTTPException(status_code=400, detail="得给个文件名喵，比如 samples/dump.bin")
    try:
        target = safe_path(rel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if os.path.isdir(target):
        raise HTTPException(status_code=400, detail=f"{rel} 是个目录喵，得给个文件名")
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    size = await _save_body(request, target, MAX_WORKSPACE_BYTES, "这个文件")
    return {"ok": True, "path": rel, "size": size}


class WorkspaceDelete(BaseModel):
    path: str = ""
    recursive: bool = False


@router.post("/api/admin/workspace/delete")
def workspace_delete(req: WorkspaceDelete, request: Request):
    _require(request)
    result = delete_file(req.path.strip(), req.recursive)
    if result.startswith("错误"):
        raise HTTPException(status_code=400, detail=result)
    return {"ok": True, "message": result}


# ---------- 会话存档 ----------

@router.get("/api/admin/sessions")
def sessions_list(request: Request):
    _require(request)
    return {"dir": SESSIONS_DIR, "items": sessions_store.list_all()}


@router.get("/api/admin/sessions/{uuid}")
def sessions_read(uuid: str, request: Request):
    _require(request)
    try:
        text, truncated = sessions_store.read(uuid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="没有这份存档喵")
    return {"uuid": uuid, "text": text, "truncated": truncated}


class SessionName(BaseModel):
    uuid: str = ""


@router.post("/api/admin/sessions/delete")
def sessions_delete(req: SessionName, request: Request):
    _require(request)
    try:
        removed = sessions_store.remove(req.uuid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="没有这份存档喵")
    return {"ok": True, "removed": removed}


# ---------- 知识库文档 ----------
# knowledge/ 里放的就是检索用的原始文档。索引是**进程内**的（见 kb/index.py 的 _scan），
# 所以传完、删完都得重启服务才会重新扫一遍——这里改成「改完隔一会儿没新动静就自动重启」，
# 一次传好几个文件也只重启一次。

MAX_KNOWLEDGE_BYTES = 200 * 1024 * 1024   # 单个文档上限（流式落盘，不吃内存）
MAX_KNOWLEDGE_LIST = 2000                 # 最多列这么多条
RESTART_DELAY = 8.0                       # 改完隔这么久没新动静就重启

_restart_timer = None
_restart_lock = threading.Lock()


def _kn_path(rel):
    """把相对路径解析到 knowledge/ 里面，越界（../ 之类）直接报错。"""
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    full = os.path.normpath(os.path.join(KNOWLEDGE_DIR, rel))
    root = os.path.normpath(KNOWLEDGE_DIR)
    if full != root and not full.startswith(root + os.sep):
        raise HTTPException(status_code=400, detail="路径得在 knowledge/ 里面喵")
    return full, rel


def _schedule_restart():
    """安排一次重启，返回「能不能自动重启」。

    计时器每次都会被推后，所以连着传几个文件最后只重启一次。服务不是 systemd 管的
    就返回 False，前端会提示「自己重启一下」。
    """
    global _restart_timer
    if not update.service_active():
        return False
    with _restart_lock:
        if _restart_timer is not None:
            _restart_timer.cancel()
        _restart_timer = update.restart_later(RESTART_DELAY)
    return True


def _restart_note(auto):
    if auto:
        return (f"知识库改好了喵，{int(RESTART_DELAY)} 秒内没有新改动就自动重启、"
                "重新建索引（聊天会断一下，之后就是新的了）。")
    return (f"知识库改好了喵，但没检测到 systemd 在管 {update.SERVICE_NAME} 这个服务，"
            "自动重启不了——自己重启一下才进索引（索引是进程内建的）。")


@router.get("/api/admin/knowledge")
def knowledge_list(request: Request):
    """列 knowledge/ 里的文档（递归）。跟检索用的是同一批文件。"""
    _require(request)
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    files = []
    total = 0
    for folder, _, names in os.walk(KNOWLEDGE_DIR):
        for name in names:
            full = os.path.join(folder, name)
            if os.path.islink(full):     # 软链接不碰（可能指到知识库外面）
                continue
            try:
                stat = os.stat(full)
            except OSError:
                continue
            files.append({
                "path": os.path.relpath(full, KNOWLEDGE_DIR).replace("\\", "/"),
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
                "supported": kb_loader.supported(name),
            })
            total += stat.st_size
    files.sort(key=lambda item: item["path"])
    truncated = len(files) > MAX_KNOWLEDGE_LIST
    return {
        "dir": KNOWLEDGE_DIR,
        "files": files[:MAX_KNOWLEDGE_LIST],
        "total_bytes": total,
        "truncated": truncated,
        "extensions": list(kb_loader.extensions()),
        "max_bytes": MAX_KNOWLEDGE_BYTES,
    }


@router.post("/api/admin/knowledge/upload")
async def knowledge_upload(request: Request, path: str = Query("")):
    """传一个文档进 knowledge/。path 是相对 knowledge/ 的目标路径，可以带子目录。"""
    _require(request)
    rel = (path or "").replace("\\", "/").strip().lstrip("/")
    if not rel or rel.endswith("/"):
        raise HTTPException(status_code=400, detail="得给个文件名喵，比如 books/新书.pdf")
    if not kb_loader.supported(os.path.basename(rel)):
        raise HTTPException(
            status_code=400,
            detail=f"这个格式进不了索引喵，只认：{'、'.join(kb_loader.extensions())}",
        )
    target, rel = _kn_path(rel)
    if os.path.isdir(target):
        raise HTTPException(status_code=400, detail=f"{rel} 是个目录喵，得给个文件名")
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    size = await _save_body(request, target, MAX_KNOWLEDGE_BYTES, "这个文档")
    auto = _schedule_restart()
    return {"ok": True, "path": rel, "size": size,
            "restart": auto, "message": _restart_note(auto)}


@router.post("/api/admin/knowledge/delete")
def knowledge_delete(req: WorkspaceDelete, request: Request):
    """删文档；删目录要带 recursive=True。删不掉的（越界、软链接）一律拒绝。"""
    _require(request)
    full, rel = _kn_path(req.path)
    if not rel:
        raise HTTPException(status_code=400, detail="得指定删哪个文件喵")
    if full == os.path.normpath(KNOWLEDGE_DIR):
        raise HTTPException(status_code=400, detail="不能把整个知识库删掉喵")
    if os.path.islink(full):
        raise HTTPException(status_code=400, detail="这是个软链接喵，塔菲不敢动")
    if not os.path.exists(full):
        raise HTTPException(status_code=404, detail=f"没有这个文件喵：{rel}")
    try:
        if os.path.isdir(full):
            if not req.recursive:
                raise HTTPException(status_code=400,
                                    detail=f"{rel} 是个目录喵，要删得选「整目录一起删」")
            shutil.rmtree(full)
        else:
            os.remove(full)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"删不掉：{exc}")
    auto = _schedule_restart()
    return {"ok": True, "removed": rel,
            "restart": auto, "message": _restart_note(auto)}