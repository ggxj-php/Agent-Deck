"""强制压缩（上下文溢出兜底）：压完返回统计，历史被替换成摘要+最近原文。"""

from deck import context, settings


def test_强制压缩把旧历史换成摘要(monkeypatch):
    monkeypatch.setattr(settings, "context_limit", lambda: 100_000)
    monkeypatch.setattr(context, "_summarize", lambda text: "这是摘要")
    lines = [{"role": "user", "content": "第一句"}]
    lines.append({"role": "assistant", "content": "好的"})
    for i in range(40):
        lines.append({"role": "user", "content": f"问题{i}" + "啊" * 600})
        lines.append({"role": "assistant", "content": f"回答{i}" + "喵" * 600})
    messages = [{"role": "system", "content": "人设"}] + lines
    before = context.estimate(messages)
    info = context.compress_if_needed(messages, force=True)
    assert info and info["ok"]
    assert info["forced"] is True
    assert info["before_tokens"] == before
    assert context.estimate(messages) < before
    assert messages[0]["role"] == "system"
    assert "这是摘要" in messages[1]["content"]


def test_没到阈值不压(monkeypatch):
    monkeypatch.setattr(settings, "context_limit", lambda: 100_000)
    messages = [{"role": "system", "content": "人设"},
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好呀"}]
    assert context.compress_if_needed(messages) is None
