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


def test_两种模式都算激活(monkeypatch):
    monkeypatch.setattr(settings, "model_web_search", lambda: True)
    for mode in ("inline", "tools_api"):
        monkeypatch.setattr(settings, "web_search_probe",
                            lambda force=False, m=mode: {"supported": True, "mode": m})
        assert settings.web_search_active() is True
        assert settings.web_search_mode() == mode


def test_探测失败不激活(monkeypatch):
    monkeypatch.setattr(settings, "model_web_search", lambda: True)
    monkeypatch.setattr(settings, "web_search_probe",
                        lambda force=False: {"supported": False, "mode": "none"})
    assert settings.web_search_active() is False
    assert settings.web_search_mode() == "none"


def test_开关关掉就不激活(monkeypatch):
    monkeypatch.setattr(settings, "model_web_search", lambda: False)
    monkeypatch.setattr(settings, "web_search_probe",
                        lambda force=False: {"supported": True, "mode": "inline"})
    assert settings.web_search_active() is False
    assert settings.web_search_mode() == "none"


def test_inline_模式摘掉自家网页工具(monkeypatch):
    from deck import settings as s
    monkeypatch.setattr(s, "web_search_mode", lambda: "inline")
    names = {spec["function"]["name"] for spec in tools.active_specs()}
    assert "web_search" not in names
    assert "open_url" not in names


def test_tools_api_模式保留自家网页工具(monkeypatch):
    from deck import settings as s
    monkeypatch.setattr(s, "web_search_mode", lambda: "tools_api")
    names = {spec["function"]["name"] for spec in tools.active_specs()}
    # 官方接口模式：web_search 留着（它内部改调官方接口），open_url 也留
    assert "web_search" in names
    assert "open_url" in names


def test_没开时保留自家网页工具(monkeypatch):
    from deck import settings as s
    monkeypatch.setattr(s, "web_search_mode", lambda: "none")
    names = {spec["function"]["name"] for spec in tools.active_specs()}
    assert "web_search" in names
    assert "open_url" in names


def test_官方搜索接口解析(monkeypatch):
    """tools_api 模式：一次请求拿 results，正文直接拼进返回。"""
    from deck.tools import search

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"results": [
                {"title": "DeepSeek V4 发布", "url": "https://example.com/a",
                 "summary": "摘要 A", "content": "正文 A" * 20},
                {"title": "另一篇", "url": "https://example.com/b", "summary": "摘要 B"},
            ]}

    from deck import settings
    monkeypatch.setattr(settings, "web_search_mode", lambda: "tools_api")
    monkeypatch.setattr(settings, "web_search_official_api", lambda: "https://api.example.com/v1/tools/search")
    monkeypatch.setattr(settings, "chat_key", lambda: "test-key")
    monkeypatch.setattr(search._FETCH_SESSION, "post", lambda *a, **k: FakeResp())
    body = search._official_search("DeepSeek V4", 2)
    assert "DeepSeek V4 发布" in body and "https://example.com/a" in body
    assert "正文摘录" in body and "官方搜索接口" in body
