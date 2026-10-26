"""检查更新 / 一键更新：从 Gitee 或 GitHub 拉最新版，拉完自动重启。

只对「git clone 部署出来的目录」有用——后台就跑在这个仓库里，直接拿它自己的 git
仓库当更新源。默认 Gitee：国内服务器连 GitHub 经常直接超时（这台就是）。

更新完得重启才生效，所以拉完隔两秒去调 systemctl 重启自己——先让 HTTP 响应发出去，
再让 systemd 用新代码把进程拉起来。不是 systemd 管的话就老实告诉主人「你自己重启」。

任何一步失败都只报错不折腾：绝不 stash、绝不 reset、绝不用 --force。
"""
import os
import subprocess
import threading

from ..config import GIT_MIRRORS, PROJECT_ROOT, SERVICE_NAME

TIMEOUT = 120
# 要密码就直接失败，别把后台请求挂在那儿等输入（服务器上的仓库是公开的，用不着密码）
_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")
# 把所有 git 操作串起来，见 _git。
_git_lock = threading.Lock()


def _run(args, timeout=TIMEOUT):
    """跑一条命令，返回 (是否成功, 输出)。"""
    try:
        proc = subprocess.run(
            args, cwd=PROJECT_ROOT, env=_ENV,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, f"这台机器上找不到 {args[0]}"
    except subprocess.TimeoutExpired:
        return False, f"{' '.join(args)} 超时了（{timeout} 秒），大概是连不上那个远端"
    except OSError as exc:
        return False, f"命令跑不起来：{exc}"
    return proc.returncode == 0, proc.stdout.decode("utf-8", "replace").strip()


def _git(*args, **kwargs):
    # 串行化所有 git 操作：后台「检查更新」和「一键更新」都可能被连点，两条 git 同时
    # 动 .git 会互踩（撞 index.lock），排队跑最省事。
    with _git_lock:
        return _run(["git", *args], **kwargs)


def remotes():
    """仓库里配了哪些远端：{名字: 地址}。"""
    ok, out = _git("remote", "-v")
    if not ok:
        return {}
    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            found.setdefault(parts[0], parts[1])
    return found


def pick_remote(provider):
    """按厂家挑远端：先找地址里带 gitee.com / github.com 的那个。

    仓库里没有对应厂家的 remote 时，退回 config.GIT_MIRRORS 里的镜像地址——
    服务器上常常只 clone 了其中一个，切到另一边就会「没有远端地址」拉不了。
    返回 (名字, 地址)：名字为空表示「仓库里没这个 remote，直接用地址拉」。
    """
    host = "gitee.com" if provider == "gitee" else "github.com"
    for name, url in remotes().items():
        if host in url:
            return name, url
    mirror = GIT_MIRRORS.get(provider, "")
    if mirror and host in mirror:
        return "", mirror
    return "", ""


def _branch():
    ok, out = _git("rev-parse", "--abbrev-ref", "HEAD")
    return out if ok else ""


def service_active():
    """服务是不是 systemd 在管（是的话更新完能自动重启）。"""
    if not SERVICE_NAME:
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.stdout.decode("utf-8", "replace").strip() == "active"


def check(provider):
    """看看远端有没有新东西。全程只读，不碰工作区。"""
    info = {
        "provider": provider,
        "remote": "",
        "url": "",
        "branch": _branch(),
        "local": "",
        "remote_head": "",
        "behind": 0,
        "commits": [],
        "up_to_date": False,
        "error": "",
        "can_auto_restart": service_active(),
        "service": SERVICE_NAME,
    }
    name, url = pick_remote(provider)
    # 镜像兜底时没有 remote 名字，前端那行「远端：xxx」就显示出厂家，别空着
    info["remote"], info["url"] = (name or f"{provider}（镜像）"), url
    if not url:
        info["error"] = (f"这个仓库里没有 {provider} 的远端地址，config.py 里也没配它那份镜像地址。"
                         "换个厂家试试，或者把 GIT_MIRRORS 里的地址改成你自己的仓库")
        return info
    if not info["branch"] or info["branch"] == "HEAD":
        info["error"] = "当前不在任何分支上（detached HEAD），没法比较"
        return info

    ok, out = _git("rev-parse", "HEAD")
    info["local"] = out[:8] if ok else ""

    if name:
        ok, out = _git("fetch", name, "--prune", "--quiet")
        ref = f"{name}/{info['branch']}"
    else:
        # 仓库里没这个 remote：直接按镜像地址拉，只更新 FETCH_HEAD（不改本地 remote 配置）
        ok, out = _git("fetch", url, info["branch"], "--quiet")
        ref = "FETCH_HEAD"
    if not ok:
        info["error"] = f"拉取远端仓库信息失败：{out[-300:]}"
        return info

    ok, out = _git("rev-parse", ref)
    if not ok:
        info["error"] = f"远端没有 {ref} 这个分支"
        return info
    info["remote_head"] = out[:8]

    ok, out = _git("rev-list", "--count", f"HEAD..{ref}")
    if ok and out.isdigit():
        info["behind"] = int(out)
    ok, out = _git("log", "--oneline", "--no-decorate", "-n", "20", f"HEAD..{ref}")
    if ok and out:
        info["commits"] = out.splitlines()
    info["up_to_date"] = info["behind"] == 0
    return info


def restart_later(delay=2.0):
    """过一会儿重启服务：先让响应发回去，再让 systemd 把新代码拉起来。

    别的模块也要用（知识库改完得重启才会重新扫 knowledge/），所以是公开的。
    """
    timer = threading.Timer(delay, restart_now)
    timer.daemon = True
    timer.start()
    return timer


def restart_now():
    # --no-block：任务交给 systemd 就返回，免得自己被杀在半路。
    # start_new_session：别跟本进程一个会话，不然一起被带走。
    for cmd in (["systemctl", "--no-block", "restart", SERVICE_NAME],
                ["systemctl", "restart", SERVICE_NAME]):
        try:
            subprocess.Popen(cmd, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue


def apply(provider):
    """拉最新版。成功的话顺手安排一次重启。"""
    name, url = pick_remote(provider)
    if not url:
        return {"ok": False,
                "message": f"这个仓库里没有 {provider} 的远端地址，config.py 里也没配它那份镜像地址，拉不了",
                "restart": False}
    branch = _branch()
    if not branch or branch == "HEAD":
        return {"ok": False, "message": "当前不在任何分支上，拉不了", "restart": False}

    before, _ = _git("rev-parse", "--short", "HEAD")
    # 有对应 remote 就按名字拉；没有就按镜像地址拉（效果一样，只走 --ff-only 快进）
    ok, out = _git("pull", "--ff-only", name or url, branch)
    if not ok:
        # 本地改过的东西（比如手动删过几张自带的表情包、或者 knowledge/ 里动过仓库里
        # 已有的文件）会让快进被拒。这里如实说清楚，并提醒「后传的文件不受影响」——
        # git 只认已跟踪的文件，没跟踪的一律不碰、也不会被删。
        low = out.lower()
        extra = ""
        if ("local changes" in low or "would be overwritten" in low
                or "untracked working tree files would be overwritten" in low):
            extra = ("（多半是本地改动过仓库里已跟踪的文件：比如删/改过自带的那批表情包，"
                     "或者 knowledge/ 里动过仓库里已有的文档。你自己后传的表情包和文档都是"
                     "没跟踪的新文件，更新不会碰它们。要么把改动还原，要么先 git stash 再更新）")
        return {
            "ok": False,
            "restart": False,
            "message": "拉取失败（本地改过文件、或者历史分叉了）。这条命令只在能快进时才动，"
                       "本地改动一律不覆盖，需要的话上服务器手动处理：" + extra + out[-300:],
        }
    after, _ = _git("rev-parse", "--short", "HEAD")
    if before == after:
        return {"ok": True, "restart": False, "from": before, "to": after,
                "message": f"已经是最新的了（{after}），没有东西要更新"}

    if service_active():
        restart_later()
        return {
            "ok": True, "restart": True, "from": before, "to": after,
            "message": f"更新好了：{before} → {after}。两秒后自动重启生效，"
                       f"页面会短暂断一下，刷新就好（systemctl 服务名 {SERVICE_NAME}）。",
        }
    return {
        "ok": True, "restart": False, "from": before, "to": after,
        "message": f"代码更新好了：{before} → {after}。但没检测到 systemd 在管 {SERVICE_NAME} 这个服务，"
                   "没法自动重启，自己重启一下才生效喵。",
    }
