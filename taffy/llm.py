"""模型接入层：只负责发请求，不掺业务逻辑。"""
import threading

from openai import OpenAI

from . import settings
from .config import MAX_TOKENS, THINKING_PARAM

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
            client = OpenAI(api_key=api_key, base_url=base_url)
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


def stream_chat(messages: list, tools: list, model: str = "",
                api_key: str = "", base_url: str = "", thinking: bool = True):
    """流式发一次请求，边收边吐增量。

    依次产出 ("thinking", 片段) / ("content", 片段)，最后产出
    ("message", 拼好的 assistant 消息)，那条消息可以直接塞回 messages。

    model / api_key / base_url 留空就用后台配的聊天那套；带图的轮次由 core
    把图片那套的三个值传进来。

    thinking=False 时带上「关思考」参数：网页上的开关走这里。开了就不带参数，
    行为跟以前完全一样。
    """
    model = (model or "").strip() or settings.chat_model()
    settings.remember_model(model)
    key = (api_key or "").strip() or settings.chat_key()
    base = (base_url or "").strip() or settings.chat_base_url()
    if not key:
        raise RuntimeError(_NO_KEY)

    extra = _think_extra(thinking)
    kwargs = {"extra_body": extra} if extra else {}
    try:
        stream = _client(key, base).chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=MAX_TOKENS,
            stream=True,
            **kwargs,
        )
    except Exception as exc:      # noqa: BLE001
        if extra:
            raise RuntimeError(
                f"{exc}（可能是这个接口不认 {THINKING_PARAM} 参数，"
                "把网页上的「思考」开关打开再试一次）"
            ) from exc
        raise

    text_parts = []
    calls = {}  # index -> {"id", "name", "arguments"}，流式下 tool_call 是分片下发的
    finish = None

    for chunk in stream:
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
        # 要是 finish 也不是 length，那就是流被中途掐断的（网络/代理），报个清楚的错，
        # 让 core 去处理——绝不能把 content 为 None 的消息塞进历史，那会让会话之后永远 400。
        if finish == "length":
            raise RuntimeError(
                "模型光思考没吐正文就结束了（输出被 max_tokens 截断，可以在 config.py 里调大 MAX_TOKENS）"
            )
        raise RuntimeError(f"模型没返回任何内容（finish_reason={finish}）")

    yield "message", message
