"""上下文压缩策略（照 DSH 公式）：触发点、保留量、强制压缩。"""
from deck import context, settings


def test_策略公式(monkeypatch):
    monkeypatch.setattr(settings, "context_limit", lambda: 1_000_000)
    policy = context.compaction_policy()
    # min(1M*0.8, 1M-32768-65536) = 800000
    assert policy["trigger"] == 800_000
    assert policy["retain_tokens"] == int((1_000_000 - 32768) * 0.16)


def test_小窗口时留头寸(monkeypatch):
    monkeypatch.setattr(settings, "context_limit", lambda: 100_000)
    policy = context.compaction_policy()
    # min(80000, 100000-32768-65536) = 1696
    assert policy["trigger"] == 1696


def test_用量快照给前端(monkeypatch):
    monkeypatch.setattr(settings, "context_limit", lambda: 200_000)
    info = context.usage([{"role": "user", "content": "你好"}], 200_000)
    assert info["limit"] == 200_000
    assert info["compress_at"] == 200_000 - 32768 - 65536      # min 里小的那个
    assert "compress_percent" in info
