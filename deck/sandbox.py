"""工作区沙箱：每个用户一个独立根目录，互不可见。"""
import os
import re
import threading

from .config import PROJECT_ROOT

WORKSPACE = os.path.realpath(os.path.join(PROJECT_ROOT, "workspace"))
os.makedirs(WORKSPACE, exist_ok=True)

_OWNER = threading.local()


def set_owner(owner):
    """当前工具调用属于哪个用户；每个会话线程开始时设置。"""
    _OWNER.value = owner


def current_owner():
    return getattr(_OWNER, "value", None)


def user_workspace(owner=None):
    """用户的私有工作区。owner=None 时用当前线程 owner；都没有才回退到全局根（仅维护脚本）。"""
    uid = owner if owner is not None else current_owner()
    if uid is None:
        return WORKSPACE
    path = os.path.join(WORKSPACE, "users", str(uid))
    os.makedirs(path, exist_ok=True)
    return path

# Windows 保留设备名：CON / NUL / COM1 之类会绕到设备上去，别当普通文件名放行
_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _reserved(name: str) -> bool:
    base = os.path.splitext(name)[0].rstrip(" .").upper()
    return base in _RESERVED


def safe_path(path: str, owner=None) -> str:
    """把相对路径解析成 workspace 内的绝对路径，越界就抛 ValueError。

    比较时两边都过一遍 normcase：Windows 文件系统不区分大小写，而 IDE 传进来的
    __file__ 可能是小写盘符 f:\\，realpath 返回的却是大写 F:\\，直接比字符串会误判越界。
    """
    if os.name == "nt":
        for seg in re.split(r"[\\/]+", str(path)):
            if seg and _reserved(seg):
                raise ValueError(f"路径里含 Windows 保留设备名，不能当普通文件用：{path}")
    rel = os.path.normpath(str(path or "."))
    owner = current_owner() if owner is None else owner
    # 共享技能目录：所有用户只读共用（run_code 的技能脚本靠它）
    if rel == "skills" or rel.startswith("skills" + os.sep):
        base = WORKSPACE
    elif rel == "browser" or rel.startswith("browser" + os.sep):
        base = os.path.join(WORKSPACE, "browser")
        if owner is not None:
            parts = rel.split(os.sep)
            if len(parts) >= 2 and parts[1] not in (str(owner), ".."):
                raise ValueError("不能访问其他用户的浏览器文件")
    else:
        base = user_workspace(owner)
    full = os.path.realpath(os.path.join(base, rel))
    root = os.path.normcase(base)
    target = os.path.normcase(full)
    if target != root and not target.startswith(root + os.sep):
        raise ValueError(f"路径超出工作区，只能操作 workspace 下的文件：{path}")
    return full
