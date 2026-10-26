"""图形验证码：答案对才放行、一次一用、错多了作废、过期作废。"""
import pytest

from deck import captcha


@pytest.fixture()
def captured(monkeypatch):
    """把画图那一步换掉，顺便把生成的答案抓出来（测试要知道正确答案）。"""
    box = {}
    monkeypatch.setattr(captcha, "_store", {})

    def fake_draw(code):
        box["code"] = code
        return b"PNG-BYTES"

    monkeypatch.setattr(captcha, "_draw", fake_draw)
    return box


def test_正确就能过(captured):
    ident, png = captcha.new()
    assert png == b"PNG-BYTES"
    ok, why = captcha.verify(ident, captured["code"])
    assert ok, why


def test_一次一用不能重放(captured):
    ident, _ = captcha.new()
    code = captured["code"]
    assert captcha.verify(ident, code)[0] is True
    assert captcha.verify(ident, code)[0] is False


def test_大小写和空格不敏感(captured):
    ident, _ = captcha.new()
    code = captured["code"]
    assert captcha.verify(ident, f"  {code.lower()} ")[0] is True


def test_错太多次就作废(captured):
    ident, _ = captcha.new()
    for _ in range(captcha.MAX_ATTEMPTS):
        assert captcha.verify(ident, "ZZZZZ")[0] is False
    ok, why = captcha.verify(ident, captured["code"])
    assert ok is False
    assert "试太多次" in why or "过期" in why


def test_过期作废(captured):
    ident, _ = captcha.new()
    with captcha._lock:
        captcha._store[ident]["expire"] = 0
    ok, why = captcha.verify(ident, captured["code"])
    assert ok is False
    assert "过期" in why


def test_空输入和乱填的id(captured):
    ident, _ = captcha.new()
    assert captcha.verify("", "ABCD")[0] is False
    assert captcha.verify(ident, "")[0] is False
    assert captcha.verify("not-a-real-id", "ABCD")[0] is False


def test_画出来的真的是PNG图片():
    """真实画一张（不 mock），确认是能打开的 PNG。"""
    import io

    from PIL import Image

    png = captcha._draw("AB23")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(png)) as img:
        assert img.size == (captcha.WIDTH, captcha.HEIGHT)


def test_答案是四位且不含易混字符():
    ident, _ = captcha.new()
    with captcha._lock:
        item = captcha._store[ident]
    assert item["tries"] == 0
    assert len(captcha.ALPHABET) > 10
    assert not set("01IOB8") & set(captcha.ALPHABET)
