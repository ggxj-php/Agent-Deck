"""工作区沙箱：路径越界必须一律拒绝。这是所有文件工具的地基。"""
import os

import pytest

from taffy import sandbox


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "WORKSPACE", str(tmp_path))
    return tmp_path


def test_相对路径能解析(workspace):
    full = sandbox.safe_path("a/b.txt")
    assert full.startswith(str(workspace))
    assert full.endswith(os.path.join("a", "b.txt"))


def test_根目录本身可以(workspace):
    assert sandbox.safe_path("") == os.path.realpath(str(workspace))


@pytest.mark.parametrize("bad", [
    "../etc/passwd",
    "a/../../x",
    "..",
    "/etc/passwd",
    "/",
])
def test_越界一律拒绝(bad):
    with pytest.raises(ValueError):
        sandbox.safe_path(bad)


def test_波浪号不展开只当普通目录名(workspace):
    # ~ 不会被展开成用户目录，就是一个普通目录名，落在工作区里是安全的
    assert sandbox.safe_path("~/secret").startswith(str(workspace))


def test_软链接指向外面被拒(workspace):
    outside = workspace.parent / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("这个平台不支持软链接")
    with pytest.raises(ValueError):
        sandbox.safe_path("link.txt")


def test_有子目录的路径不会漏(workspace):
    # a/../b 归一化之后仍在工作区里，必须放行；a/../../ 就越界了
    assert sandbox.safe_path("a/../b.txt").endswith("b.txt")
    with pytest.raises(ValueError):
        sandbox.safe_path("a/../../b.txt")
