"""身份卡（persona pack）：建 / 列 / 切 / 删、提示词优先级、表情包目录、迁移。"""
import json

import pytest

from deck import persona, settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """把 personas 目录和 admin.json 都指到临时位置。"""
    monkeypatch.setattr(persona, "PERSONAS_DIR", str(tmp_path / "personas"))
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "admin.json"))
    monkeypatch.setattr(settings, "_cache", None)
    persona._cache["key"] = None
    (tmp_path / "personas").mkdir(parents=True, exist_ok=True)
    yield tmp_path
    monkeypatch.setattr(settings, "_cache", None)
    persona._cache["key"] = None


def test_第一次启动会建默认人设():
    assert persona.ensure_default() is True
    assert persona.exists(persona.DEFAULT_ID)
    assert persona.current_id() == persona.DEFAULT_ID
    # 再跑一次不重复建
    assert persona.ensure_default() is False


def test_默认人设的提示词来自老配置():
    (path := persona._dir(persona.DEFAULT_ID))
    import os
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "persona.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": "x"}, fh)
    # 假设老 admin.json 里存过 prompt
    settings.set_prompt("老提示词")
    persona._cache["key"] = None
    # 删掉重新迁移
    import shutil
    shutil.rmtree(path)
    persona.ensure_default()
    assert "老提示词" in persona.system_prompt()


def test_建卡_列出_切换_删除():
    persona.ensure_default()
    meta, err = persona.save("neko", {"name": "猫猫", "user_name": "铲屎官",
                                      "prompt": "你是一只猫。"})
    assert not err, err
    assert meta["id"] == "neko"
    ids = [item["id"] for item in persona.list_personas()]
    assert "neko" in ids and persona.DEFAULT_ID in ids

    persona.set_current("neko")
    assert persona.current_id() == "neko"
    assert persona.system_prompt() == "你是一只猫。"
    pub = persona.public()
    assert pub["name"] == "猫猫" and pub["user_name"] == "铲屎官"

    # 正在用的不能删
    ok, why = persona.delete("neko")
    assert ok is False and "正在用" in why
    persona.set_current(persona.DEFAULT_ID)
    ok, _ = persona.delete("neko")
    assert ok is True


def test_id_合法性():
    assert persona.safe_id("neko") == "neko"
    assert persona.safe_id("Neko") == "neko"          # 大小写归一
    for bad in ("", "1abc", "a", "带中文", "../etc", "a" * 30):
        assert persona.safe_id(bad) == ""


def test_提示词写在_md_里时优先():
    persona.ensure_default()
    persona.save("neko", {"name": "猫猫", "prompt": "md 里的提示词"})
    # 直接改 persona.json 里的 prompt 字段，应该被 prompt.md 盖掉
    path = persona._dir("neko")
    import os
    data = json.load(open(os.path.join(path, "persona.json"), encoding="utf-8"))
    data["prompt"] = "json 里的提示词"
    json.dump(data, open(os.path.join(path, "persona.json"), "w", encoding="utf-8"))
    persona._cache["key"] = None
    # 直接看这张卡的提示词（system_prompt 取的是「当前」人设的）
    assert persona._meta("neko")["prompt"] == "md 里的提示词"


def test_表情包目录跟着人设走():
    persona.ensure_default()
    # 默认人设指回 static/stickers
    assert persona.stickers_dir() == persona.STICKERS_DIR
    persona.save("neko", {"name": "猫猫", "stickers": "stickers"})
    persona.set_current("neko")
    path = persona.stickers_dir()
    assert path.endswith("neko/stickers")
    assert "static" not in path


def test_删最后一个会拒绝():
    persona.ensure_default()
    # 只有一张卡，而且是当前用的 → 两种拒绝都要命中
    ok, why = persona.delete(persona.DEFAULT_ID)
    assert ok is False
