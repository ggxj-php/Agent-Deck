"""Agent 主体：维护对话历史，驱动「模型 -> 工具 -> 模型」的循环。

对外主要是 ask_stream()：它是一个事件生成器，把思考、正文、工具调用逐段吐出来。
终端（ask/run）和网页后端都消费同一套事件，行为完全一致。
"""
from . import context, settings
from .config import EXIT_WORDS, MAX_ROUNDS
from .llm import stream_chat
from .tools import TOOLS, execute

# 内联图片只认 data:image/...;base64,... 这种。上限按 base64 字符串长度算，
# 4MB 的原图编码后约 5.5MB，留点余量；再大就不收，免得一张巨图把内存顶爆。
_IMAGE_PREFIX = "data:image/"
_IMAGE_MAX_CHARS = 8 * 1024 * 1024


def _valid_image(image):
    """只接受前端传来的内联图片 data URL，别的（外部链接、任意字符串）一律拒绝。"""
    return (
        isinstance(image, str)
        and image.startswith(_IMAGE_PREFIX)
        and len(image) <= _IMAGE_MAX_CHARS
    )


def _sendable(messages):
    """发请求前剔掉非法的 assistant 消息。

    API 要求 assistant 消息至少有 content 或 tool_calls，两者都没有会直接 400。
    历史里万一混进过这种空消息（旧版本遇到过：思考吃光 max_tokens，正文没输出），
    整个会话之后每次都会 400，这里过滤一下让它自己恢复，不用重开会话。
    """
    return [
        message for message in messages
        if message.get("role") != "assistant"
        or message.get("content")
        or message.get("tool_calls")
    ]


class TaffyAgent:
    """一个塔菲会话。对话历史存在实例里，所以同一个实例能记住上下文。"""

    def __init__(self):
        # 第一条永远是系统提示词，网页版每个会话都从这句开始，不会丢人设。
        # 现问一次（而不是 import 时定死）：后台改了提示词，新会话立刻就是新的，
        # 不用重启；已经在聊的会话还留着它自己那份（第一条早就定下来了）。
        self.messages = [{"role": "system", "content": settings.system_prompt()}]

    def context_usage(self):
        """当前上下文用量，网页版拿它显示「上下文 xx%」。"""
        return context.usage(self.messages, settings.context_limit())

    def ask_stream(self, user_input: str, image: str = None):
        """把用户这句话丢给模型，边跑边吐事件，最多跑 MAX_ROUNDS 轮工具。

        image 是可选的图片 data URL。带了图就按图文混合发给模型，但这一轮
        跑完会立刻把历史里的图片数据换掉：图只在内存里过一趟，不落盘、也不
        会在后续每一轮被反复上传。

        事件类型：
          thinking    {text}                       思考过程增量
          content     {text}                       回答正文增量
          tool_start  {name, arguments}            模型决定调用工具
          tool_end    {name, args, result}         工具执行完的结果
          sticker     {mood}                       塔菲发了一张表情包
          compressed  {dropped, before_tokens, ...} 历史太长，旧的那段被压成摘要了
          error       {message}                    出错了
          done        {context}                    这一轮结束（附带当前上下文用量）
        """
        # 历史快把模型的上下文窗口填满时，先把旧的那段压成摘要（见 taffy/context.py）。
        # 放在追加用户消息之前：这句刚说的话不该被压掉。压失败也照常往下走。
        squeezed = context.compress_if_needed(self.messages)
        if squeezed and squeezed.get("ok"):
            yield {"type": "compressed", **squeezed}

        image_slot = None
        if image:
            if not _valid_image(image):
                yield {"type": "error", "message": "这张图塔菲收不了喵（只支持常见图片格式，且不超过 4MB）"}
                return
            self.messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": user_input or "看看这张图喵"},
                    {"type": "image_url", "image_url": {"url": image}},
                ],
            })
            image_slot = len(self.messages) - 1
        else:
            self.messages.append({"role": "user", "content": user_input})

        # 带图这一轮整套走图片那套配置（模型 + key + 接口地址），纯文字走聊天那套，
        # 两套在后台能分开配（比如聊天 GLM、图片 DeepSeek）。
        # 工具循环里图片还在历史里，所以整轮都用同一套，不会中途换来换去。
        vision = image_slot is not None
        model = settings.model_for(vision=vision)
        api_key = settings.key_for(vision=vision)
        base_url = settings.base_url_for(vision=vision)

        try:
            for _ in range(MAX_ROUNDS):
                message = None
                try:
                    for kind, payload in stream_chat(
                        _sendable(self.messages), TOOLS, model,
                        api_key=api_key, base_url=base_url,
                    ):
                        if kind == "message":
                            message = payload
                        elif kind == "thinking":
                            yield {"type": "thinking", "text": payload}
                        else:
                            yield {"type": "content", "text": payload}
                except Exception as exc:
                    yield {"type": "error", "message": f"模型请求失败：{exc}"}
                    return

                if message is None:
                    yield {"type": "error", "message": "模型没有返回任何内容"}
                    return

                # 兜底：空正文又没工具调用的消息一旦进历史，后面每轮都会 400
                if not message.get("content") and not message.get("tool_calls"):
                    yield {"type": "error", "message": "模型这一轮没吐内容，跳过它喵，再问一次就好"}
                    return

                self.messages.append(message)
                calls = message.get("tool_calls")
                if not calls:
                    yield {"type": "done", "context": self.context_usage()}
                    return

                for call in calls:
                    function = call["function"]
                    name = function["name"]
                    yield {"type": "tool_start", "name": name, "arguments": function["arguments"]}

                    args, result = execute(name, function["arguments"])

                    # 表情包不是让模型念出来的，是直接发出去，所以单独给个事件
                    if name == "send_sticker" and isinstance(args, dict) and args.get("mood"):
                        yield {"type": "sticker", "mood": args["mood"]}
                    else:
                        yield {"type": "tool_end", "name": name, "args": args, "result": result}

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    })

            yield {"type": "content", "text": "喵呜…工具调太多次了，塔菲脑子转不动了喵"}
            yield {"type": "done", "context": self.context_usage()}
        finally:
            # 图看完了就把原始数据抹掉，history 里只留一句说明
            if image_slot is not None:
                self.messages[image_slot] = {
                    "role": "user",
                    "content": (user_input + "\n" if user_input else "") + "[图片已阅，数据已清除]",
                }

    def ask(self, user_input: str):
        """终端版：把事件流直接打到屏幕上。"""
        answer_started = False
        thinking_started = False

        for event in self.ask_stream(user_input):
            kind = event["type"]
            if kind == "thinking":
                if not thinking_started:
                    print("\n思考: ", end="", flush=True)
                    thinking_started = True
                print(event["text"], end="", flush=True)
            elif kind == "content":
                if not answer_started:
                    print("\n塔菲: ", end="", flush=True)
                    answer_started = True
                print(event["text"], end="", flush=True)
            elif kind == "tool_start":
                answer_started = thinking_started = False
                print(f"\n[工具] {event['name']} 参数 {event['arguments']}")
            elif kind == "tool_end":
                print(f"[结果] {event['result'][:100]}")
            elif kind == "sticker":
                print(f"[表情包] 塔菲发了一张 {event['mood']} 表情包")
            elif kind == "error":
                answer_started = thinking_started = False
                print(f"\n[出错] {event['message']}")

        print()

    def run(self):
        """终端交互循环"""
        print("塔菲: 雏草姬来啦喵～想聊什么直接说喵。算法题、代码、嵌入式、单片机（STM32 那种）、物联网、计算机组成原理、数字取证这些塔菲都懂喵，输入 exit 就拜拜喵")
        while True:
            try:
                user_input = input("\n你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n塔菲: 拜拜喵~")
                break

            if not user_input:
                continue
            if user_input.lower() in EXIT_WORDS:
                print("塔菲: 那塔菲先溜啦，关注永雏塔菲谢谢喵！")
                break

            self.ask(user_input)
