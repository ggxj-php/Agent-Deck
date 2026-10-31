"""用户反馈（issue）：提交问题、看自己的状态、后台采纳/打回。

三条硬规则，别绕过：

1. **登录后才能提交**，只能看见自己提的。鉴权直接用 app.py 那个 `require_login`
   中间件塞进 `request.state.user` 的 dict，不另起一套。
2. **图片存在 `.cache/issues/<id>/` 里，不走 /static**——反馈里可能有用户隐私，
   静态目录是公网可读的。读取走 `/api/issues/image/...`，本人或管理员才给。
3. **管理员处理完（采纳/打回）立刻把图片删掉**，库里只留状态和备注。
   用户仍然看得到「已采纳/已打回 + 管理员回复」，但图片不再占空间、也不留在服务器上。

图片一律走 data URL 提交（和聊天上传同一套习惯），服务端流式落地，不碰临时文件。
"""
import base64
import json
import os
import re
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import PROJECT_ROOT
from ..db import cursor
from .admin import _require

router = APIRouter()

ISSUE_DIR = os.path.join(PROJECT_ROOT, ".cache", "issues")

TITLE_MAX = 120
BODY_MAX = 8000
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 4 * 1024 * 1024        # 单张上限，和聊天上传一致
ADMIN_NOTE_MAX = 500

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_PENDING, STATUS_ACCEPTED, STATUS_REJECTED)

# data:image/png;base64,xxxx
_DATA_URL = re.compile(r"^data:image/([a-zA-Z0-9.+-]{1,20});base64,(.+)$", re.S)
_SAFE_EXT = {"png", "jpg", "jpeg", "webp", "gif"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL DEFAULT 0,
    user_name  TEXT    NOT NULL DEFAULT '',
    title      TEXT    NOT NULL DEFAULT '',
    body       TEXT    NOT NULL DEFAULT '',
    images     TEXT    NOT NULL DEFAULT '[]',
    status     TEXT    NOT NULL DEFAULT 'pending',
    admin_note TEXT    NOT NULL DEFAULT '',
    created_at REAL    NOT NULL DEFAULT 0,
    updated_at REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_issues_user ON issues(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status, created_at DESC);
"""

_ready = False


def _ensure():
    """建表（幂等）。放在这个模块里而不是 db.py，是为了部署时只多一个文件。"""
    global _ready
    if _ready:
        return
    with cursor() as conn:
        conn.executescript(_SCHEMA)
    _ready = True


def _now():
    return time.time()


def _row_to_dict(row, with_body=True):
    item = {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "admin_note": row["admin_note"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "user_name": row["user_name"],
        "images": json.loads(row["images"] or "[]"),
    }
    if with_body:
        item["body"] = row["body"]
    return item


# ---------------- 图片 ----------------

def _sniff(data):
    """按文件头认格式，别信前端说的 mime。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return ""


def _decode_image(raw):
    """data URL -> (bytes, ext)。解不开就抛 400，别把坏图存进去。"""
    match = _DATA_URL.match((raw or "").strip())
    if not match:
        raise HTTPException(status_code=400, detail="图片得是 data:image/...;base64 这种写法")
    try:
        data = base64.b64decode(match.group(2), validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="这张图片的 base64 解不开")
    if not data:
        raise HTTPException(status_code=400, detail="这张图片是空的")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"单张图片不能超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB",
        )
    ext = _sniff(data)
    if not ext or ext not in _SAFE_EXT:
        raise HTTPException(status_code=400, detail="只支持 png / jpg / webp / gif")
    return data, ext


def _issue_dir(issue_id):
    return os.path.join(ISSUE_DIR, str(int(issue_id)))


def _drop_images(issue_id, names):
    """把这条反馈的图片删干净（处理完就要删，用户明确要求）。"""
    folder = _issue_dir(issue_id)
    for name in names:
        path = os.path.join(folder, os.path.basename(name))
        try:
            os.remove(path)
        except OSError:
            pass
    try:
        os.rmdir(folder)          # 空了就把目录也收掉
    except OSError:
        pass


# ---------------- 用户侧 ----------------

class IssueIn(BaseModel):
    title: str = ""
    body: str = ""
    images: list[str] = []        # data URL 列表


@router.post("/api/issues")
def submit_issue(req: IssueIn, request: Request):
    """提交一条反馈。图片先落盘再入库，入库失败会把刚写的图删掉。"""
    _ensure()
    user = getattr(request.state, "user", None) or {}
    uid = int(user.get("id") or 0)
    if not uid:
        raise HTTPException(status_code=401, detail="还没登录")

    title = (req.title or "").strip()
    body = (req.body or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题别空着")
    if len(title) > TITLE_MAX:
        raise HTTPException(status_code=400, detail=f"标题最多 {TITLE_MAX} 个字")
    if not body:
        raise HTTPException(status_code=400, detail="正文别空着")
    if len(body) > BODY_MAX:
        raise HTTPException(status_code=400, detail=f"正文最多 {BODY_MAX} 个字")
    if len(req.images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"最多带 {MAX_IMAGES} 张图")

    decoded = [_decode_image(raw) for raw in req.images]

    now = _now()
    with cursor() as conn:
        cur = conn.execute(
            "INSERT INTO issues (user_id, user_name, title, body, images, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, '[]', ?, ?, ?)",
            (uid, (user.get("name") or ""), title, body, STATUS_PENDING, now, now),
        )
        issue_id = cur.lastrowid

    names = []
    if decoded:
        folder = _issue_dir(issue_id)
        os.makedirs(folder, exist_ok=True)
        try:
            for index, (data, ext) in enumerate(decoded, start=1):
                name = f"{index}.{ext}"
                with open(os.path.join(folder, name), "wb") as fp:
                    fp.write(data)
                names.append(name)
        except Exception:
            _drop_images(issue_id, names)
            with cursor() as conn:
                conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
            raise

    with cursor() as conn:
        conn.execute(
            "UPDATE issues SET images = ? WHERE id = ?",
            (json.dumps(names), issue_id),
        )
        row = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
    return {"ok": True, "issue": _row_to_dict(row)}


@router.get("/api/issues/mine")
def my_issues(request: Request):
    """我提过的反馈，新的在前。图片只给名字，真图另走 image 接口。"""
    _ensure()
    user = getattr(request.state, "user", None) or {}
    uid = int(user.get("id") or 0)
    if not uid:
        raise HTTPException(status_code=401, detail="还没登录")
    with cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM issues WHERE user_id = ? ORDER BY created_at DESC LIMIT 200",
            (uid,),
        ).fetchall()
    return {"ok": True, "issues": [_row_to_dict(row) for row in rows]}


@router.get("/api/issues/image/{issue_id}/{name}")
def issue_image(issue_id: int, name: str, request: Request):
    """这条反馈的图片：本人或管理员才能看。"""
    _ensure()
    user = getattr(request.state, "user", None) or {}
    uid = int(user.get("id") or 0)
    if not uid:
        raise HTTPException(status_code=401, detail="还没登录")
    with cursor() as conn:
        row = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="没有这条反馈")
    if not user.get("is_admin") and int(row["user_id"]) != uid:
        raise HTTPException(status_code=403, detail="这不是你的反馈")
    safe = os.path.basename(name or "")
    if safe not in json.loads(row["images"] or "[]"):
        raise HTTPException(status_code=404, detail="这条反馈没有这张图")
    path = os.path.join(_issue_dir(issue_id), safe)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="图片已经不在了")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=600"})


# ---------------- 管理侧 ----------------

@router.get("/api/admin/issues")
def admin_issues(request: Request, status: str = "", limit: int = 300):
    """后台列表。[status] 传 pending / accepted / rejected，空就是全部。"""
    _require(request)
    _ensure()
    status = (status or "").strip()
    sql = "SELECT * FROM issues"
    args = []
    if status in STATUSES:
        sql += " WHERE status = ?"
        args.append(status)
    sql += " ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, created_at DESC LIMIT ?"
    args.append(max(1, min(int(limit or 300), 1000)))
    with cursor() as conn:
        rows = conn.execute(sql, args).fetchall()
        counts = {
            row["status"]: row["n"]
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM issues GROUP BY status")
        }
    return {
        "ok": True,
        "issues": [_row_to_dict(row) for row in rows],
        "counts": {key: counts.get(key, 0) for key in STATUSES},
    }


class DecisionIn(BaseModel):
    id: int = 0
    action: str = ""              # accept / reject
    note: str = ""                # 回给用户的话


@router.post("/api/admin/issues/decision")
def admin_decide(req: DecisionIn, request: Request):
    """采纳或打回。处理完的图片当场删掉，库里只留状态 + 备注。"""
    _require(request)
    _ensure()
    action = (req.action or "").strip().lower()
    if action not in ("accept", "reject"):
        raise HTTPException(status_code=400, detail="action 只能是 accept 或 reject")
    status = STATUS_ACCEPTED if action == "accept" else STATUS_REJECTED
    note = (req.note or "").strip()[:ADMIN_NOTE_MAX]
    with cursor() as conn:
        row = conn.execute("SELECT * FROM issues WHERE id = ?", (req.id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="没有这条反馈")
        names = json.loads(row["images"] or "[]")
        conn.execute(
            "UPDATE issues SET status = ?, admin_note = ?, images = '[]', updated_at = ? WHERE id = ?",
            (status, note, _now(), req.id),
        )
    _drop_images(req.id, names)
    return {"ok": True, "status": status, "removed_images": len(names)}


@router.post("/api/admin/issues/delete")
def admin_delete(req: DecisionIn, request: Request):
    """彻底删掉一条（连图）。主要给「垃圾提交」用。"""
    _require(request)
    _ensure()
    with cursor() as conn:
        row = conn.execute("SELECT * FROM issues WHERE id = ?", (req.id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="没有这条反馈")
        names = json.loads(row["images"] or "[]")
        conn.execute("DELETE FROM issues WHERE id = ?", (req.id,))
    _drop_images(req.id, names)
    return {"ok": True, "removed_images": len(names)}
