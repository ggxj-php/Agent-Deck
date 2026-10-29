"""给 run_code 跑的 Python 脚本套一层审计钩子。

workspace 之外的读写、联网、起进程、动系统设置这些动作一发生就当场抛异常，
脚本自己删不掉钩子（sys.addaudithook 只能加不能摘），ctypes 这条原生后门也堵了。

由 runner.py 以子进程方式调用，用法：
    python _pyguard.py <workspace 绝对路径> <要跑的脚本> [脚本自己的参数...]
"""
import os
import runpy
import sys

# 这些事件直接禁止，不区分路径
FORBIDDEN = (
    "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty",
    "os.kill", "os.startfile", "subprocess.", "socket.", "urllib.", "ftplib.",
    "smtplib.", "http.client.", "ctypes.", "winreg.", "msvcrt.", "pty.spawn",
    "webbrowser.",
)

# 带路径参数的写/删类事件：路径参数必须全部落在 workspace 里
WRITE_EVENTS = frozenset((
    "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown",
    "os.truncate", "os.link", "os.symlink", "os.utime", "os.replace",
    "shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.move",
    "shutil.rmtree",
))

# 带路径参数的读类事件：路径要落在 workspace 或解释器自己的目录里
READ_EVENTS = frozenset(("os.listdir", "os.scandir", "os.chdir"))

# 不许导入的模块：这些能绕开路径检查直接碰系统
# 注意只禁 urllib.request / http.client，不要禁整个 urllib——
# urllib.parse 是纯字符串解析，pathlib（连带 zipfile / docx）都依赖它，一刀切会全炸。
# 只禁「真的能碰系统」的模块。注意 **不要** 禁 socket / urllib.request / http.client：
# 标准库自己会 import 它们（比如 xml.sax.saxutils → urllib.request、python-pptx 的依赖链），
# 一禁整个库都 import 不了。联网照样拦得住——下面 FORBIDDEN 里的审计事件
# （socket.* / urllib.* / http.client.*）是在真正发起连接时才触发的。
BLOCKED_MODULES = frozenset((
    "ctypes", "_ctypes", "_winapi", "msvcrt", "subprocess",
    "winreg", "multiprocessing", "ftplib", "smtplib", "telnetlib",
))


def _inside(path, root):
    return path == root or path.startswith(root + os.sep)


def _norm(path):
    """规范化成 realpath + normcase；顺带解掉指向 workspace 外的软链接"""
    try:
        return os.path.normcase(os.path.realpath(os.fspath(path)))
    except (TypeError, ValueError, OSError):
        return None


def _is_write(mode, flags):
    """从 open() 的 mode 或 os.open() 的 flags 判断是不是写，拿不准就当成写"""
    if isinstance(mode, str):
        return any(c in mode for c in "wax+")
    if isinstance(flags, int):
        if flags & (os.O_WRONLY | os.O_RDWR):
            return True
        return bool(flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    return True


def main():
    workspace = os.path.normcase(os.path.realpath(sys.argv[1]))
    # 技能脚本（workspace/skills/ 下）允许起子进程：PPT 生成那套要靠外部命令。
    # 这是管理员从仓库装的受信任代码，和模型现写的脚本区别对待。
    skill_mode = os.environ.get("DSH_SKILL_SCRIPT") == "1"
    script = sys.argv[2]
    sys.argv = sys.argv[2:]      # 让脚本看到自己的参数，跟直接 python 它一样

    # 只有 workspace 和解释器自身目录可以读，别的一律不行
    project_root = os.path.dirname(workspace)      # workspace = PROJECT_ROOT/workspace
    roots = {workspace}
    for base in (sys.prefix, sys.base_prefix, sys.exec_prefix,
                 os.path.dirname(sys.executable), os.path.dirname(os.__file__)):
        if not base:
            continue
        full = os.path.normcase(os.path.realpath(base))
        # 解释器目录若和项目根重合（venv 就装在项目里），等于把 .env / admin.json
        # 一起送出去，这种 root 直接不要
        if full == project_root or full.startswith(project_root + os.sep):
            continue
        roots.add(full)

    # 项目里的 venv 整个 root 不能加（会把 .env / admin.json 送出去），
    # 但第三方库都在 venv 的 site-packages 里：不加进来连 import docx 都会因为
    # 「读 workspace 以外的文件」被拦。site-packages 里只有库代码，单独放行。
    for getter in ("getsitepackages", "getusersitepackages"):
        try:
            import site

            values = getattr(site, getter)()
            values = values if isinstance(values, list) else [values]
            for path in values:
                if path and os.path.isdir(path):
                    roots.add(os.path.normcase(os.path.realpath(path)))
        except Exception:                    # noqa: BLE001
            pass
    try:
        import sysconfig

        for key in ("purelib", "platlib"):
            path = sysconfig.get_paths().get(key)
            if path and os.path.isdir(path):
                roots.add(os.path.normcase(os.path.realpath(path)))
    except Exception:                        # noqa: BLE001
        pass

    def deny(what):
        raise PermissionError(f"安全拦截：{what}。助手的代码只准在 workspace 里活动")

    def hook(event, args):
        if event == "import":
            name = args[0]
            blocked = (name in BLOCKED_MODULES
                       or any(name.startswith(m + ".") for m in BLOCKED_MODULES))
            if blocked and skill_mode and name.split(".")[0] in ("subprocess", "socket", "ctypes", "_ctypes"):
                return          # 技能脚本：允许 import，实际动作另有审计
                # 抛 ImportError 而不是 PermissionError：标准库里有些 import 是
                # try ... except ImportError 的（比如 ntpath 试 _winapi、pathlib 试 nt），
                # 抛别的异常会把正常库的 import 链整个炸掉。
                raise ImportError(f"安全拦截：导入 {name} 模块被禁止（可能绕开安全限制）")
            return

        if event == "open":
            path, mode, flags = args[0], args[1], args[2]
            if isinstance(path, int):      # 已经拿到的文件描述符，不是新开文件
                return
            full = _norm(path)
            if full is None or _inside(full, workspace):
                return
            if _is_write(mode, flags):
                deny(f"试图写 workspace 以外的文件：{path}")
            if skill_mode:
                return                     # 技能脚本：允许读系统文件（字体/mime 等），写仍然限制
            if not any(_inside(full, r) for r in roots):
                deny(f"试图读 workspace 以外的文件：{path}")
            return

        for prefix in FORBIDDEN:
            if not event.startswith(prefix):
                continue
            if skill_mode and (prefix.startswith("subprocess")
                               or prefix.startswith("os.system")
                               or prefix.startswith("os.exec")
                               or prefix.startswith("os.spawn")
                               or prefix.startswith("os.posix_spawn")
                               or prefix.startswith("ctypes")):
                # 技能脚本允许起子进程、允许 ctypes（numpy 导入就要用）
                continue
            deny(f"调用 {event} 被禁止")

        if event in WRITE_EVENTS:
            for arg in args:
                if isinstance(arg, (str, bytes, os.PathLike)):
                    full = _norm(arg)
                    if full is not None and not _inside(full, workspace):
                        deny(f"{event} 试图动 workspace 以外的路径：{arg}")
            return

        if event in READ_EVENTS:
            for arg in args:
                if isinstance(arg, (str, bytes, os.PathLike)):
                    full = _norm(arg)
                    if full is not None and not any(_inside(full, r) for r in roots):
                        deny(f"{event} 试图访问 workspace 以外的目录：{arg}")

    sys.addaudithook(hook)
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
