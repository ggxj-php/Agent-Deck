"""服务器状态：CPU / 内存 / 磁盘 / 本进程占的内存。给后台仪表盘用。

只用标准库 + /proc，不引 psutil：多一个常驻依赖就多一分风险，而这台服务器只有 2G
内存，最想看的就是「还剩多少」和「谁在吃」——这些 /proc 里都有。

读不到的一律返回 None，让前端显示「读不到」——仪表盘自己绝不能把后台搞挂。
"""
import os
import shutil
import threading
import time

from ..config import PROJECT_ROOT

# 上次的 CPU 采样。累计值相减才有意义，单看一次的数字是开机以来的平均，没参考价值。
_last_cpu = None
# 采样状态是模块级共享的，几个请求同时来会互相覆盖，加把锁串一串。
_cpu_lock = threading.Lock()


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            return fp.read()
    except OSError:
        return ""


def _cpu_sample():
    """取一次 /proc/stat 里的累计值：(空闲时间, 总时间)，单位是内核的 jiffies。"""
    line = _read("/proc/stat").split("\n", 1)[0]
    if not line.startswith("cpu"):
        return None
    try:
        nums = [int(x) for x in line.split()[1:]]
    except ValueError:
        return None
    if len(nums) < 5:
        return None
    # idle + iowait 才算真闲；iowait 那部分其实也在等，但不像在算，按闲算更贴近体感
    idle = nums[3] + nums[4]
    return idle, sum(nums)


def cpu_percent():
    """距上次调用这段时间的 CPU 使用率（%）。

    第一次调用没有参照物，就自己采两次隔 0.12 秒（只在后台点开仪表盘时走到，慢一点无所谓）。
    """
    global _last_cpu
    first = _cpu_sample()
    if first is None:
        return None
    with _cpu_lock:
        if _last_cpu is None:
            time.sleep(0.12)
            second = _cpu_sample()
            if second is None:
                return None
            _last_cpu, current = first, second
        else:
            current = first
        idle, total = current
        last_idle, last_total = _last_cpu
        _last_cpu = current
    delta_total = total - last_total
    if delta_total <= 0:
        return None
    busy = 1 - (idle - last_idle) / delta_total
    return round(max(0.0, min(1.0, busy)) * 100, 1)


def memory():
    """内存 / swap（MB）。没有 swap 的话 swap 那几项就是 0——这台机器正是没开 swap。"""
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            try:
                info[key.strip()] = int(parts[0])   # kB
            except ValueError:
                pass
    total = info.get("MemTotal")
    if not total:
        return None
    available = info.get("MemAvailable")
    if available is None:
        available = info.get("MemFree", 0) + info.get("Cached", 0) + info.get("Buffers", 0)
    used = total - available
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    def to_mb(kb):
        return round(kb / 1024)
    return {
        "total_mb": to_mb(total),
        "used_mb": to_mb(used),
        "available_mb": to_mb(available),
        "percent": round(used / total * 100, 1),
        "swap_total_mb": to_mb(swap_total),
        "swap_used_mb": to_mb(swap_total - swap_free),
    }


def disk():
    """项目所在那个分区（MB）。"""
    try:
        usage = shutil.disk_usage(PROJECT_ROOT)
    except OSError:
        return None
    def to_mb(n):
        return round(n / 1024 / 1024)
    return {
        "total_mb": to_mb(usage.total),
        "used_mb": to_mb(usage.used),
        "free_mb": to_mb(usage.free),
        "percent": round(usage.used / usage.total * 100, 1),
    }


def process():
    """本进程占的内存（MB）和已经跑了多久。看在内存紧的机器上有没有越吃越多。"""
    rss = None
    for line in _read("/proc/self/status").splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    rss = round(int(parts[1]) / 1024)
                except ValueError:
                    pass
            break
    up = _read("/proc/uptime").split()
    uptime = None
    if up:
        try:
            uptime = round(float(up[0]))
        except ValueError:
            pass
    return {"rss_mb": rss, "uptime_s": uptime}


def snapshot():
    """一次性把仪表盘要的都取了。CPU 那项会阻塞一点点，其余都是读文件。"""
    cpu = {"percent": cpu_percent(), "cores": os.cpu_count()}
    try:
        cpu["load"] = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        cpu["load"] = None      # Windows 上没有 getloadavg，前端显示「—」
    return {
        "cpu": cpu,
        "mem": memory(),
        "disk": disk(),
        "process": process(),
    }
