"""能力探测：不炸、字段齐全、缺什么要给得出人话说明。"""
from deck import capabilities


def test_probe_结构():
    data = capabilities.probe()
    assert set(data["capabilities"]) >= {"traffic", "blacklist", "update"}
    for value in data["capabilities"].values():
        assert isinstance(value, bool)
    assert isinstance(data["notes"], dict)


def test_cached_会复用_and_能强制刷新(monkeypatch):
    capabilities._CACHE["data"] = None
    first = capabilities.cached()
    second = capabilities.cached()
    assert first is second                      # 走缓存，同一个对象
    monkeypatch.setattr(capabilities, "_CACHE", {"at": 0.0, "data": None})
    third = capabilities.cached()
    assert third is not first
