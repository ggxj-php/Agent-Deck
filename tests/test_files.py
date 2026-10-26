"""文件工具：只能在 workspace 里读写删。"""
import pytest

from deck import sandbox
from deck.tools import files


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(files, "WORKSPACE", str(tmp_path))
    return tmp_path


def test_写读删一条龙():
    assert "已写入" in files.write_file("notes/todo.txt", "hello")
    assert files.read_file("notes/todo.txt") == "hello"
    assert "已删除" in files.delete_file("notes/todo.txt")
    assert "不存在" in files.read_file("notes/todo.txt")


def test_越界读写被拒():
    # 越界直接抛 ValueError，由 tools.execute() 兜住转成给模型看的错误文本
    import pytest
    with pytest.raises(ValueError):
        files.read_file("../outside.txt")
    with pytest.raises(ValueError):
        files.write_file("../outside.txt", "x")


def test_不能删工作区根目录():
    assert "错误" in files.delete_file("")
    assert "错误" in files.delete_file(".", recursive=True)


def test_列目录():
    files.write_file("a.txt", "1")
    listed = files.list_files(".")
    assert "a.txt" in listed
