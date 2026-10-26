"""render_html：清洗层必须把危险标签干掉，内联脚本要留着（动画要用）。"""

from taffy.tools import render


def test_CSP_是沙箱策略():
    assert "sandbox" in render.ARTIFACT_CSP
    assert "default-src 'none'" in render.ARTIFACT_CSP
    assert "frame-ancestors 'self'" in render.ARTIFACT_CSP


def test_危险标签和协议被清掉():
    raw = """<html><body>
      <iframe src="https://evil.example/x"></iframe>
      <script src="https://evil.example/x.js"></script>
      <meta http-equiv="refresh" content="0;url=https://evil.example">
      <base href="https://evil.example/">
      <a href="javascript:alert(1)">点我</a>
      <img src="https://evil.example/pixel.png">
      <p>正常内容</p>
      <script>var animation = 1;</script>
    </body></html>"""
    clean, findings = render._sanitize(raw)
    low = clean.lower()
    assert "<iframe" not in low
    assert "evil.example/x.js" not in clean
    assert "javascript:" not in low
    assert 'http-equiv="refresh"' not in low
    assert "正常内容" in clean
    # 内联脚本要保留，否则 canvas / 动画全废
    assert "var animation = 1" in clean
    # 风险要记进 findings，方便审计
    assert findings


def test_奇怪输入也不会原样吐回去():
    # lxml 很宽容，通常不会解析失败；重点是：即使输入很奇怪，
    # 输出也一定是重新序列化过的 HTML，不会把原始串直接透传
    clean, _ = render._sanitize("<p>正常</p><object data='x'></object>")
    assert "<object" not in clean.lower()
    assert "正常" in clean


def test_产物能存能取():
    aid = render._store("<html><body>hi</body></html>", "测试标题")
    item = render.get_artifact(aid)
    assert item and item["title"] == "测试标题"
    # 非法 id 一律 None，防路径穿越
    assert render.get_artifact("../../etc/passwd") is None
    assert render.get_artifact("0" * 16) is None
    assert render.get_artifact("") is None


def test_过期就取不到(monkeypatch):
    monkeypatch.setattr(render, "RENDER_TTL", -1)
    aid = render._store("<html></html>", "过期")
    assert render.get_artifact(aid) is None


def test_超过上限会淘汰最早的(monkeypatch):
    monkeypatch.setattr(render, "RENDER_LIMIT", 2)
    first = render._store("<html>1</html>", "1")
    second = render._store("<html>2</html>", "2")
    third = render._store("<html>3</html>", "3")
    assert render.get_artifact(first) is None
    assert render.get_artifact(second) is not None
    assert render.get_artifact(third) is not None
