"""run_code 的运行时护栏：联网 / 起进程 / 越界读写都必须被当场拦下。

这里不 mock，直接拿子进程跑真正的 _pyguard.py —— 审计钩子的行为只有真跑才算数。
"""
import subprocess
import sys

import pytest

from deck.tools import _pyguard

GUARD = _pyguard.__file__


def run_guarded(workspace, code):
    script = workspace / "case.py"
    script.write_text(code, encoding="utf-8")
    # 和 runner.py 保持一致：cwd 固定 workspace，相对路径才会落在工作区里
    return subprocess.run(
        [sys.executable, GUARD, str(workspace), str(script)],
        capture_output=True, text=True, timeout=60, cwd=str(workspace),
    )


def test_正常计算和写文件能跑(tmp_path):
    result = run_guarded(tmp_path, "open('ok.txt','w').write('hi')\nprint('done')")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "ok.txt").read_text(encoding="utf-8") == "hi"
    assert "done" in result.stdout


@pytest.mark.parametrize("code", [
    "import socket\nsocket.socket()",
    "import socket\ns = socket.socket()\ns.connect(('1.1.1.1', 80))",
    "import subprocess\nsubprocess.run(['echo', 'x'])",
    "import os\nos.system('echo x')",
    "import ctypes",
    "import urllib.request",
    "import http.client",
])
def test_联网和起进程被拦(tmp_path, code):
    result = run_guarded(tmp_path, code)
    assert result.returncode != 0, f"这段居然跑通了：{code!r}"
    assert "安全拦截" in result.stderr


def test_越界写入被拦(tmp_path):
    target = tmp_path.parent / "outside_should_not_exist.txt"
    target.unlink(missing_ok=True)
    result = run_guarded(tmp_path, f"open({str(target)!r}, 'w').write('x')")
    assert result.returncode != 0
    assert "安全拦截" in result.stderr
    assert not target.exists()


def test_越界读取被拦(tmp_path):
    result = run_guarded(tmp_path, "open('/etc/passwd').read()")
    assert result.returncode != 0
    assert "安全拦截" in result.stderr


def test_删除系统文件被拦(tmp_path):
    result = run_guarded(tmp_path, "import os\nos.remove('/etc/hostname')")
    assert result.returncode != 0
    assert "安全拦截" in result.stderr
