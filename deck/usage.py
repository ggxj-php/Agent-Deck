"""模型 token 用量统计（照 DSH token-meter 的思路：每轮记真实 usage，后台可看）。

只放内存：进程重启就从零开始。要长期账本再落库，这里先把「每分钟用了多少、
今天一共多少」记下来，方便判断是不是撞了限流/配额。
"""
import threading
import time
from collections import deque

_lock = threading.Lock()
# 最近 N 分钟的逐次记录：(时间戳, prompt, completion)
_recent = deque(maxlen=2000)
_totals = {"prompt": 0, "completion": 0, "calls": 0, "errors": 0}
_day = {"date": "", "prompt": 0, "completion": 0, "calls": 0}


def _today():
    return time.strftime("%Y-%m-%d")


def record(prompt_tokens=0, completion_tokens=0, model="", ok=True):
    prompt_tokens = int(prompt_tokens or 0)
    completion_tokens = int(completion_tokens or 0)
    now = time.time()
    with _lock:
        _recent.append((now, prompt_tokens, completion_tokens))
        if ok:
            _totals["prompt"] += prompt_tokens
            _totals["completion"] += completion_tokens
            _totals["calls"] += 1
        else:
            _totals["errors"] += 1
        day = _today()
        if _day["date"] != day:
            _day.update({"date": day, "prompt": 0, "completion": 0, "calls": 0})
        if ok:
            _day["prompt"] += prompt_tokens
            _day["completion"] += completion_tokens
            _day["calls"] += 1
    if ok:
        print(f"[usage] model={model or '?'} prompt={prompt_tokens} "
              f"completion={completion_tokens}", flush=True)


def stats(window_minutes=10):
    now = time.time()
    cutoff = now - window_minutes * 60
    with _lock:
        recent = [(t, p, c) for t, p, c in _recent if t >= cutoff]
        totals = dict(_totals)
        day = dict(_day) if _day["date"] == _today() else {
            "date": _today(), "prompt": 0, "completion": 0, "calls": 0}
    return {
        "window_minutes": window_minutes,
        "window_prompt": sum(p for _t, p, _c in recent),
        "window_completion": sum(c for _t, _p, c in recent),
        "window_calls": len(recent),
        "total": totals,
        "today": day,
    }


def reset():
    with _lock:
        _recent.clear()
        for key in _totals:
            _totals[key] = 0
