"""工具熔断：试错组合连轮、重复结果、调用预算。"""
from deck.fuse import TurnFuse


def test_试错组合连轮触发熔断():
    fuse = TurnFuse(trial_rounds=3, tool_budget=100, same_result_limit=10)
    for i in range(2):
        fuse.observe_round(["write_file", "run_code"])
        assert not fuse.active
    fuse.observe_round(["write_file", "run_code"])
    assert fuse.active
    assert "写脚本" in fuse.reason or "试错" in fuse.reason
    assert "别再调用工具" in fuse.blocked_text()


def test_中间夹别的工具会重置连轮():
    fuse = TurnFuse(trial_rounds=3)
    fuse.observe_round(["write_file", "run_code"])
    fuse.observe_round(["write_file", "run_code"])
    fuse.observe_round(["read_img"])
    fuse.observe_round(["write_file", "run_code"])
    assert not fuse.active


def test_重复结果提醒然后熔断():
    fuse = TurnFuse(trial_rounds=99, tool_budget=99, same_result_limit=3)
    assert fuse.observe_result("run_code", "退出码 0\nok") is None
    assert "第 2 次" in fuse.observe_result("run_code", "退出码 0\nok")
    fuse.observe_result("run_code", "退出码 0\nok")
    assert fuse.active
    assert "几乎一样的结果" in fuse.reason


def test_不同结果不触发():
    fuse = TurnFuse(same_result_limit=3)
    for i in range(10):
        fuse.observe_result("run_code", f"退出码 0\n结果 {i}")
    assert not fuse.active


def test_工具预算():
    fuse = TurnFuse(tool_budget=2, trial_rounds=99, same_result_limit=99)
    for _ in range(2):
        fuse.observe_call()
    assert not fuse.active
    fuse.observe_call()
    assert fuse.active
    assert "超过" in fuse.reason


def test_熔断只触发一次原因不覆盖():
    fuse = TurnFuse(trial_rounds=1)
    fuse.observe_round(["write_file", "run_code"])
    first = fuse.reason
    fuse.observe_call()
    assert fuse.reason == first
