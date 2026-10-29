"""客户端 TPM 限速：超预算要等，不超不等。"""
import time

from deck import ratelimit


def test_不超预算不等待(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("不该等")))
    ratelimit.acquire(30_000, budget=100_000)
    ratelimit.acquire(30_000, budget=100_000)


def test_超预算会等待(monkeypatch):
    ratelimit.reset()
    clock = {"now": 1000.0}
    slept = []
    monkeypatch.setattr(ratelimit.time, "time", lambda: clock["now"])

    def fake_sleep(seconds):
        slept.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(ratelimit.time, "sleep", fake_sleep)
    ratelimit.record(90_000, now=1000.0)
    waited = ratelimit.acquire(30_000, budget=100_000, max_wait=120)
    assert slept and waited > 0


def test_超长等待会被截断(monkeypatch):
    ratelimit.reset()
    clock = {"now": 1000.0}
    monkeypatch.setattr(ratelimit.time, "time", lambda: clock["now"])
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: None)
    ratelimit.record(200_000, now=1000.0)
    waited = ratelimit.acquire(10_000, budget=100_000, max_wait=2)
    assert waited <= 2


def test_预算为0表示不限速(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("不该等")))
    ratelimit.record(10_000_000)
    assert ratelimit.acquire(10_000, budget=0) == 0


def test_记录和用量统计():
    ratelimit.reset()
    now = time.time()
    ratelimit.record(1000, now=now)
    ratelimit.record(2000, now=now)
    assert ratelimit.used(now=now) == 3000
