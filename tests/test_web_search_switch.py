"""模型内置联网搜索开关：默认开、探测结果决定是否真启用、开了就摘掉自家 web 工具。"""
from deck import settings, tools


def test_默认是开的():
    assert settings.model_web_search() is True


def test_开关能存能读():
    try:
        settings.set_model_web_search(False)
        assert settings.model_web_search() is False
    finally:
        settings.set_model_web_search(True)
    assert settings.model_web_search() is True


def test_端点不支持时不算激活(monkeypatch):
    monkeypatch.setattr(settings, "model_web_search", lambda: True)
    monkeypatch.setattr(settings, "web_search_probe",
                        lambda force=False: {"supported": False, "reason": "假支持"})
    assert settings.web_search_active() is False


def test_激活时摘掉自家网页工具(monkeypatch):
    from deck import settings as s
    monkeypatch.setattr(s, "web_search_active", lambda: True)
    names = {spec["function"]["name"] for spec in tools.active_specs()}
    assert "web_search" not in names
    assert "open_url" not in names


def test_没激活时保留自家网页工具(monkeypatch):
    from deck import settings as s
    monkeypatch.setattr(s, "web_search_active", lambda: False)
    names = {spec["function"]["name"] for spec in tools.active_specs()}
    assert "web_search" in names
    assert "open_url" in names
