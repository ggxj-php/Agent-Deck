"""限速预估：plan() 只算不睡，用来提前提示用户。"""
from deck import ratelimit


def test_plan_不超预算为0():
    ratelimit.reset()
    assert ratelimit.plan(10_000, budget=100_000) == 0


def test_plan_超预算大于0():
    ratelimit.reset()
    ratelimit.record(95_000)
    assert ratelimit.plan(10_000, budget=100_000) > 0


def test_plan_不限速为0():
    ratelimit.reset()
    ratelimit.record(10_000_000)
    assert ratelimit.plan(10_000, budget=0) == 0
