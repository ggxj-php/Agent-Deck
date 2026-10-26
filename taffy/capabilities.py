"""服务器能力探测：这个部署能提供哪些「运维类」功能。

有些后台面板绑定了特定环境：
  - 流量面板要能读 `/var/log/nginx/access.log`
  - IP 黑名单要 `ufw` + root
  - 检查更新要仓库是 git clone 的、并且有 systemd 单元
  - 境外 IP 拦截要有 nginx geo 配置

换台机器这些可能压根没有。与其让接口 500、页面满屏红字，不如启动时探测出来，
后台按能力显示/隐藏面板，用户一看就知道「这台机器没有这个功能」。

探测结果缓存 60 秒（只读几个文件 / 跑一次 which，开销很小，但别每个请求都跑）。
"""
import os
import shutil
import subprocess
import threading
import time

from .config import PROJECT_ROOT

NGINX_LOG_DIR = "/var/log/nginx"
_CACHE = {"at": 0.0, "data": None}
_LOCK = threading.Lock()
_TTL = 60.0


def _nginx_logs():
    """能不能读 nginx 的访问日志。"""
    if not os.path.isdir(NGINX_LOG_DIR):
        return False
    try:
        names = os.listdir(NGINX_LOG_DIR)
    except OSError:
        return False
    return any(n.startswith("access.log") for n in names)


def _ufw():
    """能不能调 ufw（需要 root + 装了 ufw）。"""
    if not shutil.which("ufw"):
        return False
    try:
        # 读一下状态，顺便确认有权限（非 root 会失败）
        subprocess.run(["ufw", "status"], capture_output=True, timeout=5, check=False)
        return True
    except Exception:                     # noqa: BLE001
        return False


def _systemd():
    return bool(shutil.which("systemctl")) and os.path.isdir("/run/systemd/system")


def _service_name():
    """当前进程是不是 systemd 管起来的；返回单元名（探测不到返回空）。"""
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ""
    for line in text.splitlines():
        # cgroup v2: 0::/system.slice/taffy-agent.service
        if ".service" in line:
            part = line.rsplit("/", 1)[-1].strip()
            if part.endswith(".service"):
                return part
    return ""


def _git_repo():
    return os.path.isdir(os.path.join(PROJECT_ROOT, ".git"))


def _geo():
    """有没有 nginx 的 CN 网段 geo 配置（境外拦截用）。"""
    return os.path.isfile("/etc/nginx/geo/cn.conf")


def probe():
    """返回 {能力名: bool}，外加一句人话说明缺什么。"""
    traffic = _nginx_logs()
    ufw = _ufw()
    systemd = _systemd()
    service = _service_name()
    git = _git_repo()
    geo = _geo()

    caps = {
        "traffic": traffic,
        "blacklist": ufw,
        "update": git and systemd and bool(service),
        "geo_block": geo,
        "systemd": systemd,
    }
    notes = {}
    if not traffic:
        caps["traffic"] = False
        notes["traffic"] = f"读不到 {NGINX_LOG_DIR}/access.log（不是 nginx 或者没权限）"
    if not ufw:
        notes["blacklist"] = "没有 ufw 或者不是 root，IP 黑名单用不了"
    if not caps["update"]:
        notes["update"] = "这份部署不是 git clone 的，或者不是 systemd 管起来的，一键更新用不了"
    if not geo:
        notes["geo_block"] = "没有 /etc/nginx/geo/cn.conf，境外拦截没配"
    return {"capabilities": caps, "notes": notes,
            "service": service, "root": PROJECT_ROOT}


def cached():
    now = time.time()
    with _LOCK:
        if _CACHE["data"] is not None and now - _CACHE["at"] < _TTL:
            return _CACHE["data"]
    data = probe()
    with _LOCK:
        _CACHE["at"] = time.time()
        _CACHE["data"] = data
    return data
