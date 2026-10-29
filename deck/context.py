"""对话上下文的用量估算 + 自动压缩。

网页版聊天是「一个会话一个 DeckAgent，历史全留在内存里、每轮都整段发给模型」，
聊久了会出两个问题：内存一直涨，而且迟早把模型的上下文窗口撑爆——超了模型直接 400，
那一轮就废了，之后每轮都废。这里管两件事：

  usage()              估算当前用了多少 token，给聊天页显示「上下文 xx%」用；
  compress_if_needed() 用到窗口的八成（config.CONTEXT_COMPRESS_AT）时，把较早的那段
                       历史交给模型压成一段摘要，只留最近的若干条原文。

估算只是「估」——不装 tokenizer（那要拉一堆依赖，2G 的服务器不划算）：中文按 1 字
1 token，其它字符按 4 个 1 token，每条消息再加一点固定开销。宁可估多、宁可早压，
也不能估少了让请求 400。

压缩失败（网络挂了、模型抽风）就当没压过，原样放行——绝不能因为压缩把聊天搞挂。
"""
import re
import threading

from . import llm, settings
from .config import (
    CONTEXT_COMPRESS_AT,
    CONTEXT_HEADROOM_TOKENS,
    CONTEXT_RETAIN_RATIO,
    MAX_TOKENS,
)

# 摘要请求本身最多吐多少 token。摘要不该长，给太多了反而把成本顶上去。
SUMMARY_MAX_TOKENS = 1024

# 送给模型做摘要的原文上限（字符）。超了就从两头掐：前面的留着（早期约定在这里），
# 后面的也留着（最近的事），中间用省略号接上。
MAX_SOURCE_CHARS = 40000

# 单条工具结果的截断长度。工具输出动辄几千行，全塞给摘要模型纯属浪费。
TOOL_SNIPPET_CHARS = 400

# 同一时刻只让一个会话压（压缩要调模型，慢；并发压缩会把内存和费用顶上去）。
_compress_lock = threading.Lock()

SUMMARY_SYSTEM = """你在帮一个中文聊天机器人整理对话历史。下面是你之前和用户聊的内容，
现在要把前面这部分压成一段摘要，好腾出上下文空间继续聊。

要求：
- 用中文，第三人称陈述，不要寒暄、不要复述要求。
- 务必留住：聊过的事实和结论、代码/命令的最终结果、知识库回答的出处文件名、
  用户的偏好和明确要求、还没做完的事。
- 丢掉：寒暄、重复的来回、思考过程、工具的中间输出（只留结论）。
- 按时间顺序写，可以用短条目。控制在 400 字以内。
- 只输出摘要本身，不要加「摘要：」这类前缀。"""


_CJK_RE = re.compile("[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def text_tokens(text):
    """按「中文 1 字 1 token、其它 4 字符 1 token」估。

    知识库那边也用它：按向量模型的输入上限反推「一块能有多少字」时，
    用的就是同一个估法——宁可估多，也不能让块超了模型的限度被截断。
    """
    # 用正则把 CJK 全替掉、量剩下的长度，比逐字符的 Python 循环快得多（估算要频繁调用）
    other = len(_CJK_RE.sub("", text))
    cjk = len(text) - other
    return cjk + (other + 3) // 4


def _message_tokens(message):
    """一条消息的估算值。带图的按一张图固定几百 token 算（图片那一轮跑完就被抹掉了）。"""
    content = message.get("content")
    total = 4  # 每条消息的角色、分隔符那点固定开销
    if isinstance(content, str):
        total += text_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                total += text_tokens(part.get("text") or "")
            elif part.get("type") == "image_url":
                total += 800
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        total += text_tokens(str(function.get("name") or ""))
        total += text_tokens(str(function.get("arguments") or ""))
    return total


def estimate(messages):
    """估算整段历史用了多少 token。"""
    return sum(_message_tokens(item) for item in messages)


def compaction_policy(window=None, output_reserve=None):
    """算「什么时候压、压完留多少原文」（照 DSH dsh-compaction-basic 的公式）。

    trigger = floor(min(W × 0.8, W − 输出预留 − 65536))
    retain  = (W − 输出预留) × 0.16

    留 64k 头寸是 DSH 的默认值：下一轮请求、工具定义、模型输出都要占地方，
    贴着窗口上限发请求必输。W 是模型真实窗口（后台「上下文窗口」配），不是拍脑袋的数。
    """
    window = int(window or settings.context_limit() or 0)
    output_reserve = int(MAX_TOKENS if output_reserve is None else output_reserve)
    trigger = min(int(window * CONTEXT_COMPRESS_AT),
                  window - output_reserve - CONTEXT_HEADROOM_TOKENS)
    trigger = max(1024, trigger)
    retain = max(512, int(max(0, window - output_reserve) * CONTEXT_RETAIN_RATIO))
    return {"window": window, "output_reserve": output_reserve,
            "trigger": trigger, "retain_tokens": retain}


def _keep_recent_for(messages, retain_tokens):
    """按「保留多少个 token 的最近原文」反推保留几条消息（至少 2 条）。"""
    kept = 0
    count = 0
    for message in reversed(messages[1:]):
        kept += _message_tokens(message)
        count += 1
        if kept >= retain_tokens:
            break
    return max(2, min(count, max(2, len(messages) - 2)))


def usage(messages, limit, compress_at=None):
    """给前端看的用量：用了多少、占窗口百分之多少、到了几成就自动压。

    读不到的时候（还没接过话）tokens 是 0，前端照常显示 0%。
    """
    tokens = estimate(messages)
    limit = max(1, int(limit or 0))
    policy = compaction_policy(limit)
    trigger = int(compress_at if compress_at is not None else policy["trigger"])
    percent = round(tokens * 100.0 / limit, 1)
    return {
        "tokens": tokens,
        "limit": limit,
        "percent": min(percent, 100.0),
        "compress_at": trigger,
        "compress_percent": round(min(trigger * 100.0 / limit, 100.0), 1),
        "messages": len(messages),
    }


def _cut_index(messages, keep_recent):
    """找压缩的切点：切点之前的压成摘要，之后的保留原文。

    API 要求 tool 消息必须紧跟在带 tool_calls 的那条 assistant 后面，所以断点只能落在
    「用户消息」上——从 keep_recent 往前（往更早处）退，退到第一条用户消息为止。
    找不到就返回 None（比如整段历史里一条用户消息都没有），那就别压。
    """
    start = max(1, len(messages) - keep_recent)
    for index in range(start, 0, -1):
        message = messages[index]
        if message.get("role") == "user":
            return index
    return None


def _render(messages):
    """把要压缩的这段历史写成给摘要模型看的纯文本。"""
    lines = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                part.get("text") or "" for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        text = (content or "").strip()
        if role == "user":
            if text:
                lines.append(f"用户：{text}")
        elif role == "assistant":
            if text:
                lines.append(f"助手：{text}")
            names = [
                (call.get("function") or {}).get("name") or "?"
                for call in message.get("tool_calls") or []
            ]
            if names:
                lines.append(f"（助手调用了工具：{'、'.join(names)}）")
        elif role == "tool":
            snippet = text[:TOOL_SNIPPET_CHARS]
            if len(text) > TOOL_SNIPPET_CHARS:
                snippet += "…（后面截掉了）"
            lines.append(f"（工具结果：{snippet}）")
    return "\n".join(lines)


def _tighten(text):
    """原文太长就掐两头：开头（早期约定）和结尾（最近的事）都留一点。"""
    if len(text) <= MAX_SOURCE_CHARS:
        return text
    head = int(MAX_SOURCE_CHARS * 0.6)
    tail = MAX_SOURCE_CHARS - head
    return text[:head] + "\n……（中间省略）……\n" + text[-tail:]


def _summarize(text):
    """让模型把这段历史压成一段摘要。失败抛异常，由调用方接住。"""
    reply = llm.complete(
        [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": _tighten(text)},
        ],
        model=settings.chat_model(),
        api_key=settings.chat_key(),
        base_url=settings.chat_base_url(),
        max_tokens=SUMMARY_MAX_TOKENS,
    )
    return (reply or "").strip()


def compress_if_needed(messages, limit=None, keep_recent=None, force=False):
    """到压缩线就把旧历史压成摘要，返回压缩信息；没压返回 None。

    force=True 用于「模型说上下文溢出了」之后的兜底压缩：这时不管到没到阈值，
    只要还能压就压，然后把请求重试一次。

    直接改 messages（就地替换成压缩后的列表），因为会话历史是 DeckAgent 实例持有的。
    摘要那一步失败就原样留着不动，返回 None，让这一轮照常发出去。
    """
    limit = int(limit or settings.context_limit() or 0)
    if limit <= 0 or len(messages) < 3:
        return None
    policy = compaction_policy(limit)
    if keep_recent is None:
        keep_recent = _keep_recent_for(messages, policy["retain_tokens"])
    tokens = estimate(messages)
    if not force and tokens < policy["trigger"]:
        return None

    cut = _cut_index(messages, keep_recent)
    # 切点太靠前（等于没保住最近的东西）或者压根找不到，就别压了：
    # 宁可这一轮撑过去，也不能把历史压得只剩摘要。
    if cut is None or cut < 2:
        return None

    head = messages[1:cut]
    source = _render(head)
    if len(source) < 200:      # 没什么好压的
        return None

    with _compress_lock:
        # 拿锁的时候可能已经有人压过了，再量一次，别重复压
        if not force and estimate(messages) < policy["trigger"]:
            return None
        try:
            summary = _summarize(source)
        except Exception as exc:                      # 压缩失败不能影响聊天
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not summary:
            return {"ok": False, "error": "摘要模型没返回内容"}

        tail = messages[cut:]
        note = {
            "role": "user",
            "content": "【之前聊过的内容已经压成摘要了，接着聊就好】\n" + summary,
        }
        messages[:] = [messages[0], note] + list(tail)

    after = estimate(messages)
    return {
        "ok": True,
        "dropped": len(head),
        "kept": len(tail),
        "before_tokens": tokens,
        "after_tokens": after,
        "trigger": policy["trigger"],
        "forced": bool(force),
    }
