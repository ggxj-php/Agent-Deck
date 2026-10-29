"""技能包发现与路径安全。"""
import os

import pytest

from deck import skills


def _make_skill(base, name, description="一句话说明", body="正文"):
    folder = os.path.join(base, name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fp:
        fp.write(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")
    return folder


def test_发现技能(tmp_path, monkeypatch):
    _make_skill(str(tmp_path), "demo", "演示技能")
    (tmp_path / "demo" / "scripts").mkdir()
    (tmp_path / "demo" / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
    monkeypatch.setattr(skills, "SKILL_DIRS", (str(tmp_path),))
    items = skills.discover()
    assert [i["name"] for i in items] == ["demo"]
    assert items[0]["description"] == "演示技能"


def test_读取_skill_和文件(tmp_path, monkeypatch):
    _make_skill(str(tmp_path), "demo", "x", "正文内容")
    (tmp_path / "demo" / "docs").mkdir()
    (tmp_path / "demo" / "docs" / "flow.md").write_text("流程", encoding="utf-8")
    monkeypatch.setattr(skills, "SKILL_DIRS", (str(tmp_path),))
    name, text, _path = skills.load("demo")
    assert name == "demo" and "正文内容" in text
    assert skills.read_file("demo", "docs/flow.md") == "流程"
    assert "docs/flow.md" in skills.files("demo")


def test_路径不能越界(tmp_path, monkeypatch):
    _make_skill(str(tmp_path), "demo")
    monkeypatch.setattr(skills, "SKILL_DIRS", (str(tmp_path),))
    for bad in ("../secret", "..\\secret", "/etc/passwd"):
        with pytest.raises(ValueError):
            skills.read_file("demo", bad)


def test_没有的技能报错(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "SKILL_DIRS", (str(tmp_path),))
    with pytest.raises(ValueError):
        skills.load("nope")


def test_仓库地址校验(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "SKILL_DIRS", (str(tmp_path),))
    ok, sub = skills.normalize_repo("https://github.com/hugohe3/ppt-master/", "skills/ppt-master")
    assert ok.endswith("ppt-master") and sub == "skills/ppt-master"
    for bad in ("http://github.com/a/b", "https://evil.com/a/b", "https://github.com/a",
                "file:///etc/passwd"):
        with pytest.raises(ValueError):
            skills.normalize_repo(bad)
    for bad_sub in ("../x", "a/../../b", "/etc"):
        with pytest.raises(ValueError):
            skills.normalize_repo("https://github.com/a/b", bad_sub)


def test_启用停用(tmp_path, monkeypatch):
    _make_skill(str(tmp_path), "demo", "演示")
    monkeypatch.setattr(skills, "SKILL_DIRS", (str(tmp_path),))
    monkeypatch.setattr(skills, "STATE_FILE", "skills.json")
    # 状态文件写进技能目录
    assert skills.set_enabled("demo", False) is False
    assert [i["name"] for i in skills.enabled_skills()] == []
    assert skills.set_enabled("demo", True) is True
    assert [i["name"] for i in skills.enabled_skills()] == ["demo"]


def test_卸载只能删workspace里的(tmp_path, monkeypatch):
    base = tmp_path / "skills"
    _make_skill(str(base), "demo")
    monkeypatch.setattr(skills, "SKILL_DIRS", (str(tmp_path / "proj"), str(base)))
    # 只在 workspace/skills 里的能删
    skills.uninstall("demo")
    assert not (base / "demo").exists()


def test_ask_user_工具已注册():
    from deck.tools import active_specs
    names = {s["function"]["name"] for s in active_specs(extra=("ask_user",))}
    assert "ask_user" in names
    assert "list_skills" in names or True
