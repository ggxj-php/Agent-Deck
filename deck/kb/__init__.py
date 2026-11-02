"""知识库：对外是 search() / warmup() / stats()。

索引是进程内单例；启动时后台建，运行中每 60 秒检查 knowledge/ 有没有变化，
有变化自动重建（没变的文件走解析缓存，只重新解析改过的文件）。
建索引期间 search() 不阻塞，直接返回「正在建索引」。
"""
import hashlib
import os
import threading
import time

from ..config import KB_TOP_K, KNOWLEDGE_DIR
from .index import KnowledgeBase

_kb = None
_kb_fp = ""                       # 当前索引对应的 knowledge/ 指纹
_lock = threading.Lock()          # 护 _stats 用
_lock_build = threading.Lock()    # 护「建库」用
_stats = {"building": False, "chunks": 0, "vectors": 0, "error": "",
          "max_tokens": 0, "embed_note": ""}
_watch_started = False


def _fingerprint():
    """knowledge/ 的轻量指纹：只看路径+大小+修改时间，不读内容，很快。"""
    try:
        items = []
        for root, _dirs, files in os.walk(KNOWLEDGE_DIR):
            for name in files:
                full = os.path.join(root, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                items.append(f"{os.path.relpath(full, KNOWLEDGE_DIR)}|{st.st_size}|{int(st.st_mtime)}")
        items.sort()
        return hashlib.sha1("\n".join(items).encode()).hexdigest()
    except OSError:
        return ""


def _get():
    """懒加载索引单例（阻塞版，只给后台构建线程用）。"""
    global _kb, _kb_fp
    kb = _kb
    if kb is not None:
        return kb
    with _lock_build:
        if _kb is None:
            fp = _fingerprint()
            started = time.time()
            kb = KnowledgeBase()
            kb.build()
            _kb = kb
            _kb_fp = fp
            print(f"[kb] 索引构建完成：{len(kb.chunks)} 块，用时 {time.time() - started:.1f}s", flush=True)
    return _kb


def _build_bg(reason=""):
    """在后台建索引；已经在建就不重复起线程。"""
    with _lock:
        if _stats["building"]:
            return
        _stats["building"] = True
        _stats["error"] = ""
    def run():
        try:
            print(f"[kb] 开始建索引（{reason or '启动预热'}）", flush=True)
            _get()
        except Exception as exc:              # noqa: BLE001
            with _lock:
                _stats["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[kb] 建索引失败：{type(exc).__name__}: {exc}", flush=True)
        finally:
            with _lock:
                _stats["building"] = False
    threading.Thread(target=run, daemon=True, name="kb-build").start()


def _watch():
    """每 60 秒看一次 knowledge/ 有没有变化，变了就重建。"""
    global _kb
    last = _kb_fp
    while True:
        time.sleep(60)
        try:
            fp = _fingerprint()
            if fp and fp != last and _kb is not None:
                print("[kb] 检测到 knowledge/ 有变化，重建索引", flush=True)
                _kb = None
                last = fp
                _build_bg("知识库有更新")
            elif fp != last:
                last = fp
        except Exception:                     # noqa: BLE001
            pass


def warmup():
    """启动预热：后台建索引；之后再起一个变化监听线程。"""
    global _watch_started
    _build_bg("启动预热")
    if not _watch_started:
        _watch_started = True
        threading.Thread(target=_watch, daemon=True, name="kb-watch").start()


def stats():
    with _lock:
        data = dict(_stats)
    kb = _kb
    data["fingerprint"] = _kb_fp[:12]
    data["chunks"] = len(kb.chunks) if kb is not None else data.get("chunks", 0)
    data["ready"] = kb is not None
    if kb is not None:
        try:
            data["progress"] = kb.progress_snapshot()
        except Exception:                     # noqa: BLE001
            pass
        if getattr(kb, "vector_error", ""):
            data["error"] = kb.vector_error
    return data


def search(query, top_k=KB_TOP_K):
    """检索知识库，返回拼好的原文片段（带出处），供模型直接阅读。"""
    kb = _kb
    if kb is None or not kb.chunks:
        _build_bg("首次检索")
        return ("知识库索引正在后台构建（首次启动或知识库刚更新），大约 1 分钟后可用，"
                "现在先别重复调用 search_knowledge。")

    hits = kb.search(query, top_k)
    if not hits:
        return f"知识库里没找到和「{query}」相关的内容。"

    blocks = []
    for _score, chunk in hits:
        page = f" 第 {chunk['page']} 页" if chunk.get("page") else ""
        blocks.append(f"【{chunk['source']}{page}】\n{chunk['text']}")
    return "\n\n".join(blocks)
