"""非 Python 代码的跑前静态审查：危险头文件 / 包 / 越界路径字符串要被拦。"""
from deck.tools import _codescan


def check(tmp_path, name, text, ext):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return _codescan.check(str(path), ext)


def test_安全代码放行(tmp_path):
    code = '#include <stdio.h>\nint main(){printf("hi");return 0;}\n'
    assert check(tmp_path, "ok.c", code, ".c") == ""


def test_联网头文件被拦(tmp_path):
    code = "#include <sys/socket.h>\nint main(){return 0;}\n"
    assert "安全拦截" in check(tmp_path, "net.c", code, ".c")


def test_越界路径字符串被拦(tmp_path):
    code = 'void f(){ FILE*fp=fopen("../../etc/passwd","r"); }\n'
    assert "安全拦截" in check(tmp_path, "path.c", code, ".c")


def test_js_起子进程被拦(tmp_path):
    code = 'const cp = require("child_process");\n'
    assert "安全拦截" in check(tmp_path, "x.js", code, ".js")


def test_注释里写危险词不算(tmp_path):
    code = "// 这里只是注释提到 socket，不会真的调\nint main(){return 0;}\n"
    assert check(tmp_path, "comment.c", code, ".c") == ""
