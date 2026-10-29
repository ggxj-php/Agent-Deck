"""模型接入层：只负责发请求，不掺业务逻辑。"""
import random
import threading
import time

from openai import OpenAI

from . import settings
from . import usage as usage_mod
from .config import (
    LLM_MAX_RETRIES,
    LLM_RETRY_BASE_MS,
    LLM_RETRY_JITTER,
    LLM_RETRY_MAX_MS,
    MAX_TOKENS,
    THINKING_PARAM,
)
from .llm_errors import INVALID, RETRYABLE, LLMError, QuotaExceeded, classify

_clients = {}
_clients_lock = threading.Lock()

# 没读到 key 时统一报这一句：光靠 OpenAI 客户端自己抛的错，用户看不出要去哪配。
_NO_KEY = ("没读到聊天用的 API Key：请在本项目的 .env 里配 DEEPSEEK_API_KEY，"
           "或者打开网页后台把 key 填上，然后再试一次。")


def _client(api_key, base_url):
    """按 (key, 接口地址) 建客户端。

    key 和地址都能在后台改，所以不能像以前那样在 import 时建一次就完事；
    这里缓存起来，换 key / 换地址之后的第一次请求就会自动用新的。

    聊天和图片可能是两家服务商（比如聊天 GLM、图片 DeepSeek），所以缓存键要带上
    地址——两个客户端各留着，不用每轮来回重建。
    """
    slot = (api_key, base_url)
    with _clients_lock:
        client = _clients.get(slot)
        if client is None:
            # 上游偶尔会「握手成功但一直不吐数据」：不设超时就会挂到 SDK 默认的 10 分钟，
            # 前端等不住先断了，用户看到的就是「一直思考中 / 断在半路」。
            # 这里给每段数据一个 60 秒的空闲上限，兜住卡死的连接。
            client = OpenAI(
                api_key=api_key, base_url=base_url,
                timeout=45.0,       # 流式下等价于「45 秒收不到新数据就报错」（前端 60 秒才判超时，要抢在前面）
                max_retries=0,
            )
            _clients[slot] = client
        return client


def _think_extra(thinking):
    """要「关思考」时带的额外参数；开着就返回 None。

    只在明确要关、且配置里指定了参数名时才发。开着就什么都不发——各家默认就是
    思考，少发一个参数就少一分跟别的服务商不兼容的风险。
    """
    if thinking or not THINKING_PARAM:
        return None
    return {THINKING_PARAM: False}


def complete(messages: list, model: str = "", api_key: str = "", base_url: str = "",
             max_tokens: int = 1024, thinking: bool = True):
    """一次性把回答收完，给内部的小任务用（比如检索前把问题翻成英文关键词）。

    max_tokens 不能太小：带思考的模型思维链也吃这个额度，给几百的话正文一个字都
    轮不到，返回空字符串（这个坑在对话那边踩过一次，见 stream_chat）。
    """
    model = (model or "").strip() or settings.chat_model()
    key = (api_key or "").strip() or settings.chat_key()
    base = (base_url or "").strip() or settings.chat_base_url()
    if not key:
        raise RuntimeError(_NO_KEY)
    extra = _think_extra(thinking)
    # 先按要求的带参数发；要是不认这个参数（400），去掉它再试一次——
    # 一个小任务不该因为参数不兼容就整条路废掉。
    attempts = ([{"extra_body": extra}, {}] if extra else [{}])
    reply = None
    last_error = None
    for kwargs in attempts:
        try:
            reply = _client(key, base).chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0,     # 这类任务要的是稳定，不要发挥
                **kwargs,
            )
            break
        except Exception as exc:      # noqa: BLE001
            last_error = exc
    if reply is None:
        raise last_error
    if not reply.choices:
        return ""
    return (reply.choices[0].message.content or "").strip()


def _backoff_seconds(attempt, retry_after=0.0):
    """退避：有 Retry-After 就听服务端的，否则 500ms 起步指数退避 + 10% 抖动。"""
    if retry_after and retry_after > 0:
        return min(float(retry_after), LLM_RETRY_MAX_MS / 1000.0)
    delay = min(LLM_RETRY_BASE_MS / 1000.0 * (2 ** attempt), LLM_RETRY_MAX_MS / 1000.0)
    return delay * (1 + random.uniform(-LLM_RETRY_JITTER, LLM_RETRY_JITTER))


def _log_retry(code, attempt, delay, detail=""):
    print(f"[llm] {code} 第 {attempt + 1} 次重试，等 {delay:.1f}s"
          f"{(' | ' + str(detail)[:160]) if detail else ''}", flush=True)


def _record_usage(usage_obj, model):
    """把 provider 返回的 usage 记到用量统计里（拿不到就算了）。"""
    if not usage_obj:
        return
    try:
        usage_mod.record(
            prompt_tokens=getattr(usage_obj, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage_obj, "completion_tokens", 0) or 0,
            model=model,
        )
    except Exception:                     # noqa: BLE001
        pass


def _consume_stream(stream, model):
    """把一次流的增量翻译成事件；顺带把 usage 记下来。"""
    text_parts = []
    calls = {}  # index -> {"id", "name", "arguments"}，流式下 tool_call 是分片下发的
    pending_sent = set()   # 已经发过「正在准备工具」事件的下标
    finish = None

    iterator = iter(stream)
    while True:
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        usage_obj = getattr(chunk, "usage", None)
        if usage_obj is not None:
            yield "usage", {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
            }
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        if choice.finish_reason:
            finish = choice.finish_reason
        delta = choice.delta
        if delta is None:
            continue

        # 推理模型会先把思考过程放在 reasoning_content 里
        thought = getattr(delta, "reasoning_content", None)
        if thought:
            yield "thinking", thought

        if delta.content:
            text_parts.append(delta.content)
            yield "content", delta.content

        for piece in delta.tool_calls or []:
            slot = calls.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
            if piece.id:
                slot["id"] = piece.id
            if piece.function:
                if piece.function.name:
                    slot["name"] = piece.function.name
                    # 模型开始生成这个工具的参数了（画图时那一大段 HTML 可能要十几秒），
                    # 先告诉前端「正在准备 —— 别让气泡空着像卡死」
                    if piece.index not in pending_sent:
                        pending_sent.add(piece.index)
                        yield "tool_pending", piece.function.name
                if piece.function.arguments:
                    slot["arguments"] += piece.function.arguments
    text = "".join(text_parts)
    message = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [
            {
                "id": slot["id"] or f"call_{index}",
                "type": "function",
                "function": {"name": slot["name"], "arguments": slot["arguments"]},
            }
            for index, slot in sorted(calls.items())
        ]
    if not message["content"] and not message.get("tool_calls"):
        # 一个字都没吐出来。stream 正常结束的情况下，多半是思考把 max_tokens 吃光了；
        # 要是 finish 也不是 length，那就是流被中途掐断的（网络/代理），报个清楚的错。
        if finish == "length":
            raise LLMError(
                "EMPTY_RESPONSE",
                "模型光思考没吐正文就结束了（输出被 max_tokens 截断，可以在 config.py 里调大 MAX_TOKENS）",
            )
        raise LLMError("EMPTY_RESPONSE", f"模型没返回任何内容（finish_reason={finish}）")

    yield "finish", finish
    yield "message", message


def stream_chat(messages: list, tools: list, model: str = "",
                api_key: str = "", base_url: str = "", thinking: bool = True):
    """流式发一次请求，边收边吐增量；出错按分类决定「重试 / 放弃 / 让上层压缩」。

    依次产出 ("thinking", 片段) / ("content", 片段) / ("tool_pending", 名字)，
    最后产出 ("finish", reason) 和 ("message", 拼好的 assistant 消息)。

    重试规则（照 DSH dsh-llm-retry）：
      - 只重试 RATE_LIMIT / SERVER / TIMEOUT / TRANSPORT / EMPTY_RESPONSE
      - 指数退避 500ms→10s，10% 抖动，尊重服务端 Retry-After，最多 5 次
      - 已经开始往外吐内容的不重试（重试会把半截话重复一遍）
      - QUOTA（额度没了）不重试，直接把话说明白
      - CONTEXT_OVERFLOW 抛给 core，让它压缩历史后重试一次
    """
    model = (model or "").strip() or settings.chat_model()
    settings.remember_model(model)
    key = (api_key or "").strip() or settings.chat_key()
    base = (base_url or "").strip() or settings.chat_base_url()
    if not key:
        raise RuntimeError(_NO_KEY)

    extra = _think_extra(thinking)
    kwargs = {"extra_body": extra} if extra else {}
    attempt = 0
    dropped_extra = False
    while True:
        emitted = False
        try:
            stream = _client(key, base).chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=MAX_TOKENS,
                stream=True,
                stream_options={"include_usage": True},
                **kwargs,
            )
            for event in _consume_stream(stream, model):
                emitted = True
                yield event
            return
        except Exception as exc:                  # noqa: BLE001
            if isinstance(exc, QuotaExceeded):
                usage_mod.record(ok=False)
            err = classify(exc)
            # 有些代理不认 extra_body：参数不合法就把它去掉再试一次（只试一次）
            if (err.code == INVALID and extra and not dropped_extra and not emitted):
                dropped_extra = True
                kwargs = {}
                continue
            if err.code in RETRYABLE and not emitted and attempt < LLM_MAX_RETRIES:
                delay = _backoff_seconds(attempt, err.retry_after)
                _log_retry(err.code, attempt, delay, err.detail)
                attempt += 1
                time.sleep(delay)
                continue
            raise err
