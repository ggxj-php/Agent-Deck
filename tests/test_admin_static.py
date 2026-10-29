"""后台静态资源守卫：用户分析必须是 Markdown 渲染的容器，别退回纯文本。"""
import os


def _read(*parts):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, *parts), encoding="utf-8") as fp:
        return fp.read()


def test_分析结果容器是markdown渲染():
    html = _read("deck", "web", "static", "admin.html")
    assert 'class="analysis-md"' in html
    assert 'id="user-analysis-box"' in html
    js = _read("deck", "web", "static", "admin.js")
    assert "function renderMarkdown(" in js
    assert "box.innerHTML = renderMarkdown(" in js
    css = _read("deck", "web", "static", "style.css")
    assert ".analysis-md table" in css
