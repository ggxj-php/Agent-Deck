"""IP 黑名单：后台点一下就真的封进 UFW（内核层，所有端口都拦）。

- 名单存在 `.cache/ip_blacklist.json`，进程重启不丢
- 实际拦截靠 `ufw insert 1 deny from <ip> to any`：插到最前面，不然会被
  22/80/443 那几条 allow 规则先放行
- UFW 自己会把规则写进 /etc/ufw/user.rules，服务器重启也还在
- 只允许封「公网单 IP」：本机、内网段、保留地址、以及**当前操作者自己的 IP**
  一律拒绝——免得在网页上点一下把自己锁在门外

失败不抛异常，统一返回 {"ok": False, "error": ...}，后台面板自己不能挂。
"""
import ipaddress
import json
import os
import subprocess
import threading

from ..config import PROJECT_ROOT

BANS_PATH = os.path.join(PROJECT_ROOT, ".cache", "ip_blacklist.json")
UFW = "/usr/sbin/ufw"

_lock = threading.Lock()
_ufw_lock = threading.Lock()   # 同一时间只让一个 ufw 命令跑


def _load():
    try:
        with open(BANS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _save(data):
    try:
        os.makedirs(os.path.dirname(BANS_PATH), exist_ok=True)
        tmp = BANS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, BANS_PATH)
        return True
    except OSError:
        return False


def _ufw(args, timeout=15):
    """跑一条 ufw 命令，返回 (成功?, 输出)。"""
    with _ufw_lock:
        try:
            proc = subprocess.run(
                [UFW, *args], capture_output=True, text=True, timeout=timeout
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode == 0, out


def _check_ip(ip, actor=""):
    ip = (ip or "").strip()
    if not ip:
        return "IP 不能为空"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "IP 格式不对"
    if addr.version != 4:
        return "暂时只支持 IPv4（UFW 的 IPv6 条目要单独写，先不冒险）"
    if addr.is_loopback or addr.is_private or addr.is_link_local \
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
        return "本机 / 内网 / 保留地址不能封"
    if actor and actor.split(":")[0] == ip:
        return "这是你自己现在的 IP，封了就进不来后台了"
    return ""


def banned_ips():
    """返回已封的 IP 集合（给统计那边过滤用）。"""
    return set(_load().keys())


def list_bans():
    data = _load()
    return [
        {"ip": ip, "ts": item.get("ts") or "", "reason": item.get("reason") or ""}
        for ip, item in sorted(data.items(), key=lambda kv: kv[1].get("ts") or "", reverse=True)
    ]


def ban(ip, reason="", actor=""):
    ip = (ip or "").strip()
    err = _check_ip(ip, actor)
    if err:
        return {"ok": False, "error": err}

    with _lock:
        data = _load()
        if ip in data:
            return {"ok": True, "already": True, "ip": ip}

        # 插到第 1 条：UFW 是按顺序匹配的，放到 allow 后面就永远轮不到
        good, out = _ufw(["insert", "1", "deny", "from", ip, "to", "any"])
        if not good or "ERROR" in out.upper():
            return {"ok": False, "error": f"ufw 执行失败：{out[:120]}"}

        data[ip] = {
            "ts": _now(),
            "reason": (reason or "").strip()[:120],
        }
        _save(data)
    return {"ok": True, "ip": ip, "created": True}


def unban(ip):
    ip = (ip or "").strip()
    if not ip:
        return {"ok": False, "error": "IP 不能为空"}
    with _lock:
        data = _load()
        if ip not in data:
            return {"ok": False, "error": "这个 IP 不在黑名单里"}
        # 有没有这条规则都删，删不到不算错
        _ufw(["delete", "deny", "from", ip, "to", "any"])
        data.pop(ip, None)
        _save(data)
    return {"ok": True, "ip": ip, "removed": True}


def _now():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _sync():
    """启动时对一遍：名单里有、但 UFW 里已经没有的，补回去。

    （正常不会发生；万一有人 ufw reset 过，黑名单不至于悄悄失效。）
    """
    data = _load()
    if not data:
        return
    good, out = _ufw(["status"])
    if not good:
        return
    missing = [ip for ip in data if ip not in out]
    if not missing:
        return
    for ip in missing:
        _ufw(["insert", "1", "deny", "from", ip, "to", "any"])


def start_sync():
    """后台线程里对账，不拖慢服务启动。"""
    threading.Thread(target=_sync, daemon=True).start()
