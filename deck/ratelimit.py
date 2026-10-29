"""客户端限速：按「每分钟 token 预算」排队，别把上游的 TPM 配额一下打爆。

背景：阿里百炼给这个 key 的每分钟 token 配额实测只有 20 万出头，
技能流程一轮接一轮（每轮 2~4 万 tokens），十几秒内就能超；
超了上游返回 429 `insufficient_quota`，重试也没用。
大厂做法是网关层令牌桶排队（DSH 的 api-gateway 也这么干），这里做单机版：
**发请求前先看最近 60 秒用了多少，超预算就等一会儿再发**，把突发摊平。
"""
import collections
import threading
import time

_lock = threading.Lock()
# (时间戳, tokens)，只留最近几分钟的
_events = collections.deque(maxlen=5000)

WINDOW = 60.0
# 默认预算：实测上限约 24 万/分钟，留 20% 余量给别的请求/重试
DEFAULT_BUDGET = 200_000
MAX_WAIT = 90.0


def _prune(now, window=WINDOW):
    while _events and _events[0][0] <= now - window:
        _events.popleft()


def used(window=WINDOW, now=None):
    now = time.time() if now is None else now
    with _lock:
        _prune(now, window)
        return sum(tok for _ts, tok in _events)


def record(tokens, now=None):
    now = time.time() if now is None else now
    tokens = max(0, int(tokens or 0))
    if tokens:
        with _lock:
            _events.append((now, tokens))


def plan(tokens, budget=None, window=WINDOW):
    """预估这次要等多久（秒），不睡。core 用它提前发个提示，别让前端干等。"""
    budget = DEFAULT_BUDGET if budget is None else int(budget or 0)
    tokens = max(1, int(tokens or 0))
    if budget <= 0 or tokens >= budget:
        return 0.0
    now = time.time()
    with _lock:
        _prune(now, window)
        snapshot = list(_events)
    total = sum(tok for _ts, tok in snapshot)
    if total + tokens <= budget:
        return 0.0
    need = total + tokens - budget
    acc = 0
    for ts, tok in snapshot:
        acc += tok
        if acc >= need:
            return max(0.0, ts + window - now)
    return float(window)


def acquire(tokens, budget=None, max_wait=MAX_WAIT, window=WINDOW):
    """要发一个预计 tokens 的请求：超预算就先睡，直到窗口里腾出位置。

    返回实际等了多久（秒）。budget<=0 表示不限速。
    """
    budget = DEFAULT_BUDGET if budget is None else int(budget or 0)
    tokens = max(1, int(tokens or 0))
    if budget <= 0 or tokens >= budget:
        return 0.0
    waited = 0.0
    while True:
        now = time.time()
        with _lock:
            _prune(now, window)
            snapshot = list(_events)
        total = sum(tok for _ts, tok in snapshot)
        if total + tokens <= budget:
            return waited
        need = total + tokens - budget
        free_at = now + window
        acc = 0
        for ts, tok in snapshot:
            acc += tok
            free_at = ts + window
            if acc >= need:
                break
        wait = max(0.05, free_at - now)
        if waited + wait > max_wait:
            return waited
        print(f"[ratelimit] 最近 {int(window)}s 已用 {total:,} tokens，"
              f"再发要等 {wait:.1f}s", flush=True)
        time.sleep(wait)
        waited += wait


def reset():
    with _lock:
        _events.clear()
