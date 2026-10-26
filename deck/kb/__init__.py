"""知识库：对外是 search() / warmup() / stats()，索引在第一次用到时懒加载。

索引是进程内单例，只建一次。运行中往 knowledge/ 里加了文件不会自动生效，
重启一下就好。

检索走几路（原查询词匹配 + 英文检索词 + 向量），细节见 index.py。索引在后台线程里建：
建好之前 search() 拿到的是个半成品；索引本身是同步就绪的，向量那一路算好了下一问就自动用上。
"""
import threading

from ..config import KB_TOP_K
from .index import KnowledgeBase

_kb = None
_lock = threading.Lock()          # 护 _stats 用
_lock_build = threading.Lock()    # 护「建库」用，跟 _lock 分开，别把建库的长时间持锁拖累 stats
# 后台页面拿它显示索引进度：还在建吗、向量算了多少、有没有出错、块按多长收口
_stats = {"building": False, "chunks": 0, "vectors": 0, "error": "",
          "max_tokens": 0, "embed_note": ""}


def _get():
    """懒加载索引单例。

    两个线程同时第一次用到知识库时，不加锁会各建一次库（双份向量钱 + 双份内存）；
    而且必须等 build() 完整跑完才发布 _kb，否则别的线程会拿到「词索引还没组装」的
    半成品，把「正在建索引」显示成「知识库是空的」。所以这里用双检锁，并且只在建完
    之后才把 _kb 对外可见。
    """
    global _kb
    kb = _kb
    if kb is not None:
        return kb
    with _lock_build:
        if _kb is None:
            kb = KnowledgeBase()
            kb.build()          # 构建期间 _kb 仍是 None，别的线程进不来
            _kb = kb            # 只在建完之后才发布
    return _kb


def warmup():
    """提前把索引建好，别让第一个提问卡在建索引上。"""
    with _lock:
        _stats["building"] = True
    try:
        kb = _get()
        with _lock:
            _stats["chunks"] = len(kb.chunks)
            _stats["vectors"] = kb.vector_count()
            _stats["error"] = kb.vector_error
            _stats["max_tokens"] = kb.max_tokens
            _stats["embed_note"] = kb.embed_note
    except Exception as exc:
        with _lock:
            _stats["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        with _lock:
            _stats["building"] = False


def stats():
    """索引状态，给网页后台看的。

    建的过程中还会带上实时进度（建到哪个文件、算了多少块向量、请求多少次、重试多少次），
    后台那张「向量模型」卡每 5 秒刷一次就能看到它在动。
    """
    with _lock:
        data = dict(_stats)
    kb = _kb
    if kb is not None:
        data["progress"] = kb.progress_snapshot()
        # 检索时才暴露的向量故障（_rank_vector 现写进 kb.vector_error）也得能到后台：
        # warmup 建好之后的那一问才出的问题，不在这刷新就永远看不到。
        if kb.vector_error:
            data["error"] = kb.vector_error
    return data


def search(query, top_k=KB_TOP_K):
    """检索知识库，返回拼好的原文片段（带出处），供模型直接阅读。"""
    kb = _get()

    if not kb.chunks:
        return (
            "知识库是空的。把文档放进项目根目录的 knowledge/ 文件夹再试，"
            "支持 pdf / docx / html / md / txt。"
        )

    hits = kb.search(query, top_k)
    if not hits:
        return f"知识库里没找到和「{query}」相关的内容。"

    # 不打分数：各路融合后的分数是个很小的名次分，写出来反而会让人误判相关性
    blocks = []
    for _score, chunk in hits:
        page = f" 第 {chunk['page']} 页" if chunk.get("page") else ""
        blocks.append(f"【{chunk['source']}{page}】\n{chunk['text']}")
    return "\n\n".join(blocks)
