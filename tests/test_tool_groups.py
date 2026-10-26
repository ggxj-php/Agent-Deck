"""工具分组开关：分组表完整、过滤生效、关掉的工具硬调会被拦。"""
import pytest

from taffy import settings, tools


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "admin.json"))
    monkeypatch.setattr(settings, "_cache", None)
    yield
    monkeypatch.setattr(settings, "_cache", None)


def test_每个工具都归了组且只归一组():
    seen = {}
    for group, info in tools.TOOL_GROUPS.items():
        for name in info["tools"]:
            assert name not in seen, f"{name} 同时属于 {seen[name]} 和 {group}"
            seen[name] = group
    registered = {spec["function"]["name"] for spec in tools.TOOLS}
    assert registered == set(seen), "有工具没分组，或者分了不存在的工具"


def test_默认全开():
    assert len(tools.active_specs()) == len(tools.TOOLS)


def test_关掉一组之后模型看不到也不让调():
    keep = [g for g in tools.TOOL_GROUPS if g != "forensics"]
    settings.set_tool_groups(keep)
    names = {spec["function"]["name"] for spec in tools.active_specs()}
    assert "hash_file" not in names
    assert "get_time" in names
    _, out = tools.execute("hash_file", '{"path": "x"}')
    assert "关掉" in out


def test_全关也能跑不炸():
    settings.set_tool_groups([])
    assert tools.active_specs() == []
    _, out = tools.execute("get_time", "{}")
    assert "关掉" in out


def test_groups_payload_结构():
    payload = tools.groups_payload()
    assert payload["total"] == len(tools.TOOLS)
    assert len(payload["groups"]) == len(tools.TOOL_GROUPS)
    for group in payload["groups"]:
        assert {"id", "label", "tools", "enabled", "count"} <= set(group)
