"""Agent 主体：维护对话历史，驱动「模型 -> 工具 -> 模型」的循环。

对外主要是 ask_stream()：它是一个事件生成器，把思考、正文、工具调用逐段吐出来。
终端（ask/run）和网页后端都消费同一套事件，行为完全一致。
"""
import json
import re
import sys

from . import context, settings, uploads
from .config import EXIT_WORDS, MAX_ROUNDS, RESUME_INSTRUCTION, RESUME_WORDS, STICKER_LIMIT
from .llm import stream_chat
from .tools import active_specs, execute
from .tools.render import append_marks, strip_marks
from .tools.sticker import MOODS, pick_sticker

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
class _LeakGuard:
    """挡住「把助手的底层设定 / 系统提示词原文吐出来」这种泄露。

    流式输出没法等全文再检查，所以先扣住最后 HOLD 个字：每次新内容进来，
    对整段缓冲（含上一轮扣下的尾巴）扫一遍，命中就把那段换成一句提示，
    再把前面的正常内容放出去。扣 80 字足以让跨 chunk 的原文被拼齐。
    """

    HOLD = 80

    def __init__(self, system_prompt=""):
        pats = []
        for piece in re.split(r"[。；\n]", system_prompt or ""):
            piece = piece.strip()
            # 太短的片段容易误伤正常回答，只认 20 字以上的原文
            if len(piece) >= 20:
                pats.append(piece)
        pats.sort(key=len, reverse=True)   # 长的先替换，免得被短的切碎
        self._pats = pats
        self._buf = ""
        self.hit = False

    def feed(self, text):
        if not self._pats or not text:
            return text
        self._buf = self._scrub(self._buf + text)
        if len(self._buf) <= self.HOLD:
            return ""
        cut = len(self._buf) - self.HOLD
        out, self._buf = self._buf[:cut], self._buf[cut:]
        return out

    def flush(self):
        out = self._scrub(self._buf)
        self._buf = ""
        return out

    def _scrub(self, text):
        for pat in self._pats:
            if pat in text:
                self.hit = True
                text = text.replace(pat, "…（这段涉及内部设定，不展开）")
        return text


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


_IMAGE_MARK = re.compile(r"\[图片 #([0-9a-f]{6,32})")


def _rehydrate(messages, max_images=3, window=2):
    """把最近一两轮里带 [图片 #id] 标记的消息，重新塞回图片（多模态）。

    图片不常驻历史——几百 KB 的 base64 每轮重发太贵。但「刚从上下文里掉出去的图」
    模型应该有短期记忆，所以最近一两轮由服务端自动放回去，不用它自己判断要不要重看。
    超过这个窗口就只剩摘要了，细节得靠 read_img 回看原图。
    """
    seen_users = 0
    for index in range(len(messages) - 1, 0, -1):
        message = messages[index]
        if message.get("role") != "user":
            continue
        seen_users += 1
        if seen_users > window:
            break
        content = message.get("content")
        if not isinstance(content, str) or "[图片 #" not in content:
            continue
        ids = _IMAGE_MARK.findall(content)[:max_images]
        parts = [{"type": "text", "text": content}]
        for aid in ids:
            url = uploads.data_url(aid)
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
        if len(parts) == 1:
            return messages        # 一张都取不到（过期清掉了），原样发
        out = list(messages)
        out[index] = {"role": "user", "content": parts}
        return out
    return messages


_PLAIN_STICKER = re.compile(r"\[\s*sticker\s*:\s*([a-zA-Z_]+)\s*\]", re.I)


def _name_sticker_marks(text, picked):
    """按出现顺序把 [sticker:心情] 换成 [sticker:心情:文件名]。"""
    it = iter(picked)

    def repl(match):
        try:
            _mood, name = next(it)
        except StopIteration:
            return match.group(0)
        mood = match.group(1).lower()
        return f"[sticker:{mood}:{name}]" if name else match.group(0)

    return _PLAIN_STICKER.sub(repl, text)


def _strip_sticker_names(text):
    """发给模型前把标记里的文件名去掉（模型不需要知道具体哪张图）。"""
    return re.sub(r"\[sticker:([a-zA-Z_]+):[^\]]+\]", r"[sticker:\1]", text)


def _sendable(messages):
    """发请求前剔掉非法的 assistant 消息。

    API 要求 assistant 消息至少有 content 或 tool_calls，两者都没有会直接 400。
    历史里万一混进过这种空消息（旧版本遇到过：思考吃光 max_tokens，正文没输出），
    整个会话之后每次都会 400，这里过滤一下让它自己恢复，不用重开会话。
    """
    out = []
    for message in messages:
        if message.get("role") == "assistant" and isinstance(message.get("content"), str):
            # 正文里可能有画图产物标记（前端渲染卡片用）和表情包文件名，模型都不需要看到
            if "[[artifact:" in message["content"] or "[sticker:" in message["content"]:
                message = dict(message)
                message["content"] = _strip_sticker_names(strip_marks(message["content"]))
        if (message.get("role") != "assistant"
                or message.get("content")
                or message.get("tool_calls")):
            out.append(message)
    return out


class DeckAgent:
    """一个助手会话。对话历史存在实例里，所以同一个实例能记住上下文。"""

    def __init__(self, messages=None, cut_off=False, owner=None):
        """messages 是这条会话已经存下来的历史（不含 system 那条）。

        网页版现在**不再把 agent 常驻内存**：每轮请求从 SQLite 读出历史、灌进来跑完、
        再写回去，所以内存跟「同时有几个请求」有关，跟「有多少个会话」无关。
        owner 是用户 id，上传的图片按人隔离。
        """
        # 第一条永远是系统提示词，网页版每个会话都从这句开始，不会丢人设。
        # 现问一次（而不是 import 时定死）：后台改了提示词，新会话立刻就是新的，
        # 不用重启；已经在聊的会话还留着它自己那份（第一条早就定下来了）。
        self.messages = [{"role": "system", "content": settings.system_prompt()}]
        if messages:
            self.messages.extend(messages)
        # 上一轮是不是被截断/中断了。是的话，用户发「继续」就接着写而不是重开一轮。
        self.cut_off = bool(cut_off)
        # 用户 id：上传图片按人隔离用
        self.owner = owner

    def context_usage(self):
        """当前上下文用量，网页版拿它显示「上下文 xx%」。"""
        return context.usage(self.messages, settings.context_limit())

    def ask_stream(self, user_input: str, image: str = None, thinking: bool = None,
                   images: list = None):
        """把用户这句话丢给模型，边跑边吐事件，最多跑 MAX_ROUNDS 轮工具。

        image / images 是这一轮上传的图片（内联 data URL，可以多张）。
        处理方式和以前不同：先校验魔术字节，真的图片才落到 workspace/uploads/，
        让模型自己调 read_img 去看——这样图片内容会走「外部内容边界」，
        比把图直接塞进多模态请求安全。这一轮结束（正常结束 / 报错 / 被取消）
        都会把落盘的临时图片删掉，不在服务器上留东西。

        事件类型：
          thinking    {text}                       思考过程增量
          content     {text}                       回答正文增量
          tool_start  {name, arguments}            模型决定调用工具
          tool_end    {name, args, result}         工具执行完的结果
          sticker     {mood}                       助手发了一张表情包
          compressed  {dropped, before_tokens, ...} 历史太长，旧的那段被压成摘要了
          error       {message}                    出错了
          cut         {reason}                     回复被截断/中断了，可以发「继续」接着写
          done        {context}                    这一轮结束（附带当前上下文用量）
        """
        # 历史快把模型的上下文窗口填满时，先把旧的那段压成摘要（见 deck/context.py）。
        # 放在追加用户消息之前：这句刚说的话不该被压掉。压失败也照常往下走。
        squeezed = context.compress_if_needed(self.messages)
        if squeezed and squeezed.get("ok"):
            yield {"type": "compressed", **squeezed}

        # 上一轮被截断/中断了，这句又是「继续」→ 让模型接着写，别当新问题重开
        resume = self.cut_off and _is_resume(user_input)
        self.cut_off = False

        # 这一轮上传的图片：魔术字节校验 → 解码重编码（洗 polyglot、剥 EXIF）
        # → 落盘到 workspace/uploads/ → 归一化后的图直接进多模态请求
        incoming = []
        if image:
            incoming.append(image)
        if images:
            incoming.extend(images)
        refs, image_notes = uploads.save(incoming, owner=self.owner)
        summaries = uploads.summarize(refs) if refs else {}
        markers = uploads.marker_text(refs, summaries, image_notes)

        image_slot = None
        if resume:
            self.messages.append({"role": "user", "content": RESUME_INSTRUCTION})
        elif refs:
            text = user_input or "看看这几张图"
            if markers:
                text = text + "\n\n" + markers
            parts = [{"type": "text", "text": text}]
            for ref in refs:
                url = uploads.data_url(ref["id"])
                if url:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
            self.messages.append({"role": "user", "content": parts})
            image_slot = len(self.messages) - 1
        else:
            content = user_input or ""
            if markers:
                content = (content + "\n\n" if content else "") + markers
            self.messages.append({"role": "user", "content": content})

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
                # 每轮都新起一个：工具轮之间各自 flush
                guard = _LeakGuard(settings.system_prompt())
                picked = []          # 这一轮发出去的表情包（心情, 文件名）
                try:
                    for kind, payload in stream_chat(
                        _sendable(_rehydrate(self.messages)), active_specs(), model,
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
                                    out = guard.feed(value)
                                    if out:
                                        yield {"type": "content", "text": out}
                                else:
                                    name = pick_sticker(value)
                                    picked.append((value, name))
                                    yield {"type": "sticker", "mood": value, "name": name}
                    # 这一轮收尾了，把还没吐的尾巴补上
                    for ev, value in splitter.flush():
                        if ev == "content":
                            out = guard.feed(value)
                            if out:
                                yield {"type": "content", "text": out}
                        else:
                            name = pick_sticker(value)
                            picked.append((value, name))
                            yield {"type": "sticker", "mood": value, "name": name}
                    # **必须无条件 flush**：guard 扣着最后 80 个字防泄漏，
                    # 如果这一轮正好以 [sticker:x] 结尾，上面的 splitter.flush() 是空的，
                    # 只靠循环里那个 guard.flush() 就永远轮不到 → 最后一段话被吞掉。
                    # （表现：界面上话说到一半就断了，但库里存的正文是完整的）
                    tail = guard.flush()
                    if tail:
                        yield {"type": "content", "text": tail}
                    if guard.hit:
                        print("[leak-guard] 拦下一段疑似系统提示原文", file=sys.stderr)
                except Exception as exc:
                    last_round = "".join(chunks)
                    yield {"type": "error", "message": f"模型请求失败：{exc}"}
                    return
                last_round = "".join(chunks)
                stickers_sent = splitter.used

                if message is None:
                    yield {"type": "error", "message": "模型没有返回任何内容"}
                    return

                # 把这一轮挑中的表情文件名写进正文标记：刷新后照标记渲染，永远是同一张
                if picked and isinstance(message.get("content"), str) and "[sticker:" in message["content"]:
                    message = dict(message)
                    message["content"] = _name_sticker_marks(message["content"], picked)

                # 兜底：空正文又没工具调用的消息一旦进历史，后面每轮都会 400
                if not message.get("content") and not message.get("tool_calls"):
                    yield {"type": "error", "message": "模型这一轮没吐内容，跳过它，再问一次就好"}
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

                    # 画图的产物要在前端插一张卡片，所以除了回执再单独发个事件
                    if name == "render_html":
                        try:
                            payload = json.loads(result)
                        except Exception:
                            payload = {}
                        if payload.get("artifact_id"):
                            aid = payload["artifact_id"]
                            title = payload.get("title") or ""
                            yield {"type": "artifact", "id": aid, "title": title}
                            # 把产物标记写进「刚调工具的那条 assistant」正文末尾：
                            # 刷新后卡片才会回到原来的位置（在那段话之后、后文之前），
                            # 而不是被统一追加到整条回复的最末尾。
                            for item in reversed(self.messages):
                                if item.get("role") == "assistant":
                                    item["content"] = append_marks(
                                        item.get("content") or "", [(aid, title)])
                                    break

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
                            yield {"type": "sticker", "mood": mood,
                                   "name": pick_sticker(mood)}

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
            yield {"type": "content", "text": "呜…工具调太多次了，助手脑子转不动了"}
            yield {"type": "done", "context": self.context_usage()}
        finally:
            # 这一轮跑完，把多模态大块换成「标记 + 摘要」：
            # 图还在 workspace/uploads/，最近一两轮会自动 rehydrate 回来，
            # 更久远的只能靠摘要回忆，细节让助手自己 read_img 回看原图。
            if image_slot is not None:
                self.messages[image_slot] = {
                    "role": "user",
                    "content": ((user_input + "\n") if user_input else "") + markers,
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
                    print("\n助手: ", end="", flush=True)
                    answer_started = True
                print(event["text"], end="", flush=True)
            elif kind == "tool_start":
                answer_started = thinking_started = False
                print(f"\n[工具] {event['name']} 参数 {event['arguments']}")
            elif kind == "tool_end":
                print(f"[结果] {event['result'][:100]}")
            elif kind == "sticker":
                print(f"[表情包] 助手发了一张 {event['mood']} 表情包")
            elif kind == "error":
                answer_started = thinking_started = False
                print(f"\n[出错] {event['message']}")

        print()

    def run(self):
        """终端交互循环"""
        print("助手: 用户来啦～想聊什么直接说。算法题、代码、嵌入式、单片机（STM32 那种）、物联网、计算机组成原理、数字取证这些助手都懂，输入 exit 就拜拜")
        while True:
            try:
                user_input = input("\n你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n助手: 拜拜~")
                break

            if not user_input:
                continue
            if user_input.lower() in EXIT_WORDS:
                print("助手: 那助手先溜啦，关注永雏助手谢谢！")
                break

            self.ask(user_input)
