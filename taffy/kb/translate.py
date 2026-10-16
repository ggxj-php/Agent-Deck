"""把中文问题翻成英文检索词——不用向量模型，也能让中文提问命中英文教材。

为什么需要：knowledge/books/ 里全是英文教材，而词匹配是**按字面对上**的。
「内存取证」和 "memory forensics" 在词表上没有交集，中文提问打到英文书上就是 0 分。
向量模型能解决这个问题（语义相近），但要花钱；这里换个不花钱的办法：检索之前
让模型把问题写成一条英文检索式，再拿它去查英文教材。

和向量那一路的关系：检索时**原查询永远单独占一路**，英文那一路只是往结果里补，
所以结构上不可能比现在差——原来的中文资料一个都不会被挤掉。

走哪套连接：默认是聊天那套的接口地址和 key，但模型名、地址、key 都能在后台单独指定
（指到又小又快的模型上，或者指本机跑的小模型，省钱也不怕限流）。凭什么失败都退回原查询：
没配 key、接口挂了、返回的东西看不懂、模型不听话回了中文——一律返回空，检索照常只用
原查询，绝不让翻译把检索搞挂。
"""
import re
import threading

from .. import settings
from ..llm import complete

# 同一个问题反复问就别反复花钱了，缓存最近这些条（只缓存成功的）
_CACHE_LIMIT = 200
_cache = {}
_lock = threading.Lock()
# 最近一次翻译失败的原因。设计上失败就静默退回原查询（绝不能把检索搞挂），但一声不吭
# 就没法排查「英文检索那一路怎么没生效」，所以留个记录（见 last_error）。
_last_error = ""

# 中文（含扩展区）——没有中文的问句本来就是英文，不用翻
_CJK = re.compile(r"[\u3400-\u9fff]")

# 提问里带的这些尾巴对检索没意义，先削掉，少几个 token
_TRIM = re.compile(r"(请问|帮我|麻烦|谢谢|一下|怎么样|是什么|怎么|如何|喵|吗|呢|吧|\s)+$")

_SYSTEM = """You turn a Chinese question into English keywords for searching an English technical textbook index.

Rules:
- Output ONE single line: English words only, separated by spaces.
- 5 to 12 words: the core terms first, then a few synonyms the textbook might use.
- Use the wording an English textbook would use, not a literal translation.
- Include the standard abbreviation if there is one (IoT, I2C, BST, RTOS...).
- No numbering, no quotes, no punctuation, no Chinese, no explanation.

Examples:
内存取证里易失性数据怎么采集 -> memory forensics volatile data acquisition order of volatility live response
二叉搜索树的时间复杂度是多少 -> binary search tree BST time complexity average worst case
物联网参考架构分成哪几层 -> internet of things IoT reference architecture layer model perception network application
I2C 总线的起始条件是什么 -> I2C bus start condition SDA SCL master slave
"""


def _clean(reply):
    """从模型回复里挑出一行能用的英文检索词，挑不出就返回空。"""
    for line in (reply or "").splitlines():
        line = line.strip().strip("\"'`")
        line = re.sub(r"^[\-*\d.)\s]+", "", line)   # 去掉 "1. " "- " 这类前缀
        if not line or _CJK.search(line):
            continue                                # 还带中文就是没听指令，别拿去检索
        words = re.findall(r"[A-Za-z][A-Za-z0-9+#._]*", line)
        if words:
            return " ".join(words[:24])
    return ""


def to_english(query):
    """返回一句英文检索词；翻不出来返回空字符串。"""
    query = (query or "").strip()
    if len(query) < 2 or not _CJK.search(query):
        return ""

    with _lock:
        cached = _cache.get(query)
    if cached is not None:
        return cached

    try:
        reply = complete(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": _TRIM.sub("", query) or query}],
            model=settings.translate_model(),
            api_key=settings.translate_key(),
            base_url=settings.translate_base_url(),
            # 把中文问题写成一行英文关键词，是纯粹的格式转换，不需要思考。
            # 实测开着思考时：光是翻译就要多花 2 秒，个别模型思考吃满额度后
            # 正文还会返回空字符串（那样英文检索这一路就白瞎了）。
            thinking=False,
        )
    except Exception as exc:
        # 退回只用原查询是设计成这样的（翻译绝不能把检索搞挂），但别一声不吭：把
        # 最近一次失败记下来，排查「英文检索那一路怎么没生效」时有据可查。
        global _last_error
        _last_error = f"{type(exc).__name__}: {exc}"
        return ""

    english = _clean(reply)
    if not english:
        return ""
    with _lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[query] = english
    return english


def last_error():
    """最近一次翻译失败的原因；没失败过就是空串。给排查用（翻译失败本身是静默的）。"""
    return _last_error