"""浏览器工具：注册、URL 安全校验、无 Chromium 时的错误处理。"""
from deck import browser
from deck.tools import TOOL_GROUPS, active_specs, execute


def test_浏览器工具已注册():
    names = {s["function"]["name"] for s in active_specs()}
    for name in ("browser_open", "browser_snapshot", "browser_click", "browser_type",
                 "browser_key", "browser_scroll", "browser_extract", "browser_images",
                 "browser_screenshot", "browser_close"):
        assert name in names
    assert "browser" in TOOL_GROUPS


def test_只允许http网址():
    for bad in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)",
                "http://127.0.0.1:8000/", "http://localhost/admin"):
        try:
            browser.BrowserManager._check_url(bad)
            raise AssertionError(f"应该拒绝：{bad}")
        except RuntimeError:
            pass
    assert browser.BrowserManager._check_url("https://example.com/a") == "https://example.com/a"


def test_非法网址不走浏览器直接报错():
    _, out = execute("browser_open", '{"url": "file:///etc/passwd"}')
    assert "错误" in out and "http" in out


def test_状态查询不启动浏览器():
    info = browser.status()
    assert {"running", "sessions", "max_contexts", "max_steps"} <= set(info)
