"""工作区沙箱：把所有文件操作限制在 workspace 目录内。"""
import os
import re

from .config import PROJECT_ROOT

WORKSPACE = os.path.realpath(os.path.join(PROJECT_ROOT, "workspace"))
os.makedirs(WORKSPACE, exist_ok=True)

# Windows 保留设备名：CON / NUL / COM1 之类会绕到设备上去，别当普通文件名放行
_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _reserved(name: str) -> bool:
    base = os.path.splitext(name)[0].rstrip(" .").upper()
    return base in _RESERVED


def safe_path(path: str) -> str:
    """把相对路径解析成 workspace 内的绝对路径，越界就抛 ValueError。

    比较时两边都过一遍 normcase：Windows 文件系统不区分大小写，而 IDE 传进来的
    __file__ 可能是小写盘符 f:\\，realpath 返回的却是大写 F:\\，直接比字符串会误判越界。
    """
    if os.name == "nt":
        for seg in re.split(r"[\\/]+", str(path)):
            if seg and _reserved(seg):
                raise ValueError(f"路径里含 Windows 保留设备名，不能当普通文件用：{path}")
    full = os.path.realpath(os.path.join(WORKSPACE, path))
    root = os.path.normcase(WORKSPACE)
    target = os.path.normcase(full)
    if target != root and not target.startswith(root + os.sep):
        raise ValueError(f"路径超出工作区，只能操作 workspace 下的文件：{path}")
    return full
