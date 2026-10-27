"""_trim_history 回归：当前这轮搜到的结果不能被裁掉（否则模型会自相矛盾）。"""
from deck.core import _trim_history


def _round(i):
    return [
        {"role": "assistant", "content": f"第{i}轮", "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "search_knowledge", "arguments": '{"keyword":"x"}'}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "知识库原题内容"},
    ]


def test_当前轮的工具结果全部保留():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "做题"}]
    for i in range(6):          # 同一轮里调了 6 次工具
        msgs += _round(i)
    out = _trim_history(msgs)
    kept = [m for m in out if m.get("role") == "tool"]
    assert len(kept) == 6, "当前这轮的工具结果被裁掉了"
