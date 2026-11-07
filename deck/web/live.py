"""把一轮对话从 HTTP 连接上摘下来。

以前 agent 直接在 StreamingResponse 的生成器里跑：浏览器一关、App 切后台、地铁里
信号断一下，Starlette 就把生成器关掉，模型和工具跟着全停，用户回来只看到半截回复。
现在改成后台线程跑，事件先进这块小缓冲，谁连着谁订阅——断线只是少一个订阅者，
活儿照跑；重连凭 `since` 还能从断点接着收。

缓冲只在内存里，跑完留 5 分钟就丢；真正的结果早写进 SQLite 了，丢的只是中间过程。
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import AsyncIterator, Callable

HEARTBEAT = 10.0      # 没有新事件时多久发一次注释行（App 的读超时是 90 秒，留足余量）
KEEP_DONE = 300.0     # 跑完后再留 5 分钟，晚连上来的客户端还能把结果收全
POLL = 0.15           # 消费者多久看一次缓冲


class Turn:
    """一轮对话的缓冲区。生产者是后台线程，消费者是若干条 SSE 连接。"""

    def __init__(self, session_id: str, owner_id: int):
        self.id = uuid.uuid4().hex[:12]
        self.session_id = session_id
        self.owner_id = owner_id
        self.started = time.time()
        self.finished = 0.0
        self.ok = True
        self.error = ""
        self.cancelled = False
        self._chunks: list[str] = []
        self._done = False
        self._lock = threading.Lock()

    # ---- 生产者 ----

    def push(self, chunk: str) -> None:
        with self._lock:
            self._chunks.append(chunk)

    def cancel(self) -> None:
        """请求停下：后台循环处理完手头这个事件就跳出，跟以前拔网线一个效果。"""
        self.cancelled = True

    def finish(self, ok: bool = True, error: str = "") -> None:
        with self._lock:
            self.ok = ok
            self.error = error
            self._done = True
            self.finished = time.time()

    # ---- 消费者 ----

    def snapshot(self, since: int) -> tuple[list[str], bool]:
        with self._lock:
            return self._chunks[max(0, since):], self._done

    def state(self) -> dict:
        with self._lock:
            return {
                "running": not self._done,
                "ok": self.ok,
                "error": self.error,
                "chunks": len(self._chunks),
                "started": int(self.started),
                "finished": int(self.finished),
            }


class Hub:
    """按会话存放正在进行（以及刚跑完）的轮次。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._turns: dict[str, Turn] = {}

    def live(self, session_id: str) -> Turn | None:
        """这个会话最近的一轮；跑完超过 KEEP_DONE 就顺手清掉。"""
        with self._lock:
            turn = self._turns.get(session_id)
            if turn and turn._done and time.time() - turn.finished > KEEP_DONE:
                self._turns.pop(session_id, None)
                return None
            return turn

    def start(self, session_id: str, owner_id: int,
              work: Callable[[Turn], None]) -> Turn | None:
        """起一轮。同一会话已有在跑的就返回 None（调用方回 409，别交叉写历史）。"""
        with self._lock:
            current = self._turns.get(session_id)
            if current and not current._done:
                return None
            turn = Turn(session_id, owner_id)
            self._turns[session_id] = turn
        threading.Thread(target=self._run, args=(turn, work),
                         name=f"turn-{turn.id}", daemon=True).start()
        return turn

    def _run(self, turn: Turn, work: Callable[[Turn], None]) -> None:
        try:
            work(turn)
        except BaseException as exc:          # noqa: BLE001
            turn.finish(False, f"{type(exc).__name__}: {exc}")
            return
        turn.finish(turn.ok, turn.error)


hub = Hub()


async def stream_turn(turn: Turn, since: int, request) -> AsyncIterator[str]:
    """把缓冲里的内容按 SSE 吐出去，没内容就发心跳；客户端断开就退出。

    断开只是 return，后台线程完全不受影响——这正是「切后台继续跑」的关键。
    """
    index = max(0, since)
    beat = time.monotonic()
    while True:
        if await request.is_disconnected():
            return
        chunks, done = turn.snapshot(index)
        if chunks:
            index += len(chunks)
            for chunk in chunks:
                yield chunk
            beat = time.monotonic()
            continue
        if done:
            # 断线重连的客户端也要收到收尾标记，不然前端会一直等
            yield "data: [DONE]\n\n"
            return
        if time.monotonic() - beat >= HEARTBEAT:
            # 注释行：SSE 规范里就是心跳，网页端和 App 都会直接忽略
            yield ": keep-alive\n\n"
            beat = time.monotonic()
        await asyncio.sleep(POLL)
