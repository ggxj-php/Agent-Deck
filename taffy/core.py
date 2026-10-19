"""Agent 主体：维护对话历史，驱动「模型 -> 工具 -> 模型」的循环。

对外主要是 ask_stream()：它是一个事件生成器，把思考、正文、工具调用逐段吐出来。
终端（ask/run）和网页后端都消费同一套事件，行为完全一致。
"""
import re

from . import context, settings
from .config import (EXIT_WORDS, MAX_ROUNDS, RESUME_INSTRUCTION, RESUME_WORDS,
                       STICKER_LIMIT)
from .llm import stream_chat
from .tools import TOOLS, execute
from .tools.sticker import MOODS

# 内联图片只认 data:image/...;base64,... 这种。上限按 base64 字符串长度算，
# 4MB 的原图编码后约 5.5MB，留点余量；再大就不收，免得一张巨图把内存顶爆。
_IMAGE_PREFIX = "data:image/"
_IMAGE_MAX_CHARS = 8 * 1024 * 1024

# 正文里的表情包标记，形如 [sticker:happy]。
#
# 为什么用「正文标记」而不是工具调用：工具调用只能挂在一条消息的最末尾，
# 后面没法再接正文，所以靠 send_sticker 工具永远做不出「文字-图-文字」。
# 改成让模型在正文里写标记，服务端边收流边切成 sticker 事件，
# 前端 contentChunk / addSticker 本来就是按顺序往气泡里追加，天然能交错。
_STICKER_MARK = re.compile(r"\[\s*sticker\s*:\s*([a-zA-Z_]+)\s*\]", re.I)
_MARK_PREFIX = "[sticker:"


class _StickerStream:
    """把正文流里的 [sticker:mood] 标记实时切出来，变成 content / sticker 事件。

    增量流有两个麻烦：
      1. 标记可能在两个 chunk 之间断开（"...[sti" + "cker:happy]"），不能急着显示；
      2. 标记只是文字，模型写成什么样都得兜住（多余空格、大小写、拼错的 mood）。
    所以攒着 buffer，能确定的立刻吐，疑似标记开头的尾巴留到下一块再说。
    """

    # 标记最长也就 "[sticker:confused]" 16 个字符，留点余量
    _TAIL_MAX = 24

    def __init__(self, limit, used=0):
        self.buf = ""
        self.limit = limit
        self.used = used
        self.dropped = 0        # 超过上限被丢掉几张

    def feed(self, text):
        """喂一段增量，返回 [(kind, value)]，kind 是 'content' 或 'sticker'。"""
        self.buf += text or ""
        events = []
        while True:
            match = _STICKER_MARK.search(self.buf)
            if match:
                if match.start():
                    events.append(("content", self.buf[:match.start()]))
                mood = match.group(1).lower()
                if mood in MOODS and self.used < self.limit:
                    self.used += 1
                    events.append(("sticker", mood))
                else:
                    self.dropped += 1
                self.buf = self.buf[match.end():]
                continue

            # 没有完整标记，检查结尾是不是标记的开头，是就先留着
            cut = self.buf.rfind("[")
            if cut != -1:
                tail = self.buf[cut:]
                if len(tail) <= self._TAIL_MAX and (
                    _MARK_PREFIX.startswith(tail) or tail.startswith(_MARK_PREFIX)
                ):
                    if cut:
                        events.append(("content", self.buf[:cut]))
                    self.buf = tail
                    return events

            if self.buf:
                events.append(("content", self.buf))
                self.buf = ""
            return events

    def flush(self):
        """一条消息结束了，把还攒着的尾巴当正文吐出来。"""
        out = [("content", self.buf)] if self.buf else []
        self.buf = ""
        return out


def _valid_image(image):
    """只接受前端传来的内联图片 data URL，别的（外部链接、任意字符串）一律拒绝。"""
    return (
        isinstance(image, str)
        and image.startswith(_IMAGE_PREFIX)
        and len(image) <= _IMAGE_MAX_CHARS
    )


def _is_resume(text):
    """用户这句是不是「继续」这类指令（用来接着上一轮被截断的回复写）。"""
    if not isinstance(text, str):
        return False
    cleaned = text.strip().strip("。．.!！~～,，、 \t\n").lower()
    return cleaned in RESUME_WORDS


def _sticker_moods(args):
    """从 send_sticker 的参数里取出这次要发哪几张表情包。

    正常是 moods 数组（一次调用发多张）；也兼容模型直接塞 mood 字符串或
    单个字符串的旧写法，免得它一换参数格式就发不出图。
    """
    if not isinstance(args, dict):
        return []
    raw = args.get("moods")
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, (list, tuple)):
        return [m for m in raw if isinstance(m, str) and m]
    single = args.get("mood")
    if isinstance(single, str) and single:
        return [single]
    return []


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
        # 上一轮是不是被截断/中断了。是的话，用户发「继续」就接着写而不是重开一轮。
        self.cut_off = False

    def context_usage(self):
        """当前上下文用量，网页版拿它显示「上下文 xx%」。"""
        return context.usage(self.messages, settings.context_limit())

    def ask_stream(self, user_input: str, image: str = None, thinking: bool = None):
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
          cut         {reason}                     回复被截断/中断了，可以发「继续」接着写
          done        {context}                    这一轮结束（附带当前上下文用量）
        """
        # 历史快把模型的上下文窗口填满时，先把旧的那段压成摘要（见 taffy/context.py）。
        # 放在追加用户消息之前：这句刚说的话不该被压掉。压失败也照常往下走。
        squeezed = context.compress_if_needed(self.messages)
        if squeezed and squeezed.get("ok"):
            yield {"type": "compressed", **squeezed}

        # 上一轮被截断/中断了，这句又是「继续」→ 让模型接着写，别当新问题重开
        resume = self.cut_off and _is_resume(user_input)
        self.cut_off = False

        image_slot = None
        if resume:
            self.messages.append({"role": "user", "content": RESUME_INSTRUCTION})
        elif image:
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
        # 网页上那个「思考」开关：不传就按「开」处理，跟以前的行为一致。
        think = True if thinking is None else bool(thinking)
        vision = image_slot is not None
        model = settings.model_for(vision=vision)
        api_key = settings.key_for(vision=vision)
        base_url = settings.base_url_for(vision=vision)

        finished = False
        last_round = ""      # 最近一轮已经吐出去的原始正文，流被掐断时用来补历史
        try:
            stickers_sent = 0
            for _ in range(MAX_ROUNDS):
                message = None
                finish_reason = None
                chunks = []
                splitter = _StickerStream(STICKER_LIMIT, stickers_sent)
                try:
                    for kind, payload in stream_chat(
                        _sendable(self.messages), TOOLS, model,
                        api_key=api_key, base_url=base_url, thinking=think,
                    ):
                        if kind == "message":
                            message = payload
                        elif kind == "finish":
                            finish_reason = payload
                        elif kind == "thinking":
                            yield {"type": "thinking", "text": payload}
                        else:
                            chunks.append(payload)
                            # 正文里可能夹着 [sticker:mood]，边收边切开
                            for ev, value in splitter.feed(payload):
                                if ev == "content":
                                    yield {"type": "content", "text": value}
                                else:
                                    yield {"type": "sticker", "mood": value}
                    # 这一轮收尾了，把还没吐的尾巴补上
                    for ev, value in splitter.flush():
                        yield {"type": "content", "text": value}
                except Exception as exc:
                    last_round = "".join(chunks)
                    yield {"type": "error", "message": f"模型请求失败：{exc}"}
                    return
                last_round = "".join(chunks)
                stickers_sent = splitter.used

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
                    finished = True
                    if finish_reason == "length":
                        # 输出被 max_tokens 截断了：正文只写了一半，提示可以发「继续」
                        self.cut_off = True
                        yield {"type": "cut", "reason": "输出达到长度上限被截断"}
                    yield {"type": "done", "context": self.context_usage()}
                    return

                # 这一轮是不是只调了发表情包？表情包是「收尾动作」，发完这一轮就该结束。
                #
                # 以前不管什么工具都会继续下一轮：模型收到「已发送 xx 表情包」的回执，
                # 又以为得再回一遍（人格里还写着「每条回复都要发一张」），于是每轮写一整段
                # 正文 + 再发一张图，一个提问能刷出十几段话和十几张图（实测 40KB 正文）。
                sticker_only = all(
                    (c.get("function") or {}).get("name") == "send_sticker" for c in calls
                )

                for call in calls:
                    function = call["function"]
                    name = function["name"]
                    yield {"type": "tool_start", "name": name, "arguments": function["arguments"]}

                    args, result = execute(name, function["arguments"])

                    if name == "send_sticker":
                        # 一次调用可以带多张（moods 数组），逐个发出去。
                        # 表情包不是让模型念出来的，所以走单独的 sticker 事件。
                        moods = _sticker_moods(args)
                        sent = 0
                        for mood in moods:
                            if stickers_sent >= STICKER_LIMIT:
                                break
                            stickers_sent += 1
                            sent += 1
                            yield {"type": "sticker", "mood": mood}

                        if sent == 0:
                            # 硬上限：这一轮已经发够了，回执改成提醒，图不再发出去
                            result = "这一轮的表情包已经发够了，不用再发了"
                            yield {"type": "tool_end", "name": name, "args": args, "result": result}
                        elif sent < len(moods):
                            result = f"（这轮最多发 {STICKER_LIMIT} 张，多出来的没发）"
                    else:
                        yield {"type": "tool_end", "name": name, "args": args, "result": result}

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    })

                # 收尾：正文写完了、工具只调了表情包 → 这一轮到此为止，
                # 不再给模型「接着说」的机会，这是治刷屏的关键一步。
                if sticker_only and message.get("content"):
                    finished = True
                    if finish_reason == "length":
                        self.cut_off = True
                        yield {"type": "cut", "reason": "输出达到长度上限被截断"}
                    yield {"type": "done", "context": self.context_usage()}
                    return

            finished = True
            yield {"type": "content", "text": "喵呜…工具调太多次了，塔菲脑子转不动了喵"}
            yield {"type": "done", "context": self.context_usage()}
        finally:
            # 图看完了就把原始数据抹掉，history 里只留一句说明
            if image_slot is not None:
                self.messages[image_slot] = {
                    "role": "user",
                    "content": (user_input + "\n" if user_input else "") + "[图片已阅，数据已清除]",
                }

            # 这一轮没正常结束（网络掐断 / 浏览器关了 / 生成器被取消）：
            # 把已经说出去的那段正文补进历史，用户发「继续」时模型才知道说到哪儿了。
            if not finished and last_round.strip():
                self.messages.append({"role": "assistant", "content": last_round})
                self.cut_off = True

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
