"""IP 归属地查询：后台用户列表用，结果缓存 1 天，失败不影响页面。"""
import json
import threading
import time
import urllib.parse
import urllib.request

_TTL = 24 * 3600
_cache = {}
_lock = threading.Lock()

_PRIVATE = ("127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
            "172.2", "172.30.", "172.31.", "::1", "localhost", "100.64.", "169.254.")


def _is_private(ip):
    return not ip or ip.startswith(_PRIVATE)


_ISP_MAP = (
    ("unicom", "联通"), ("cnc", "联通"), ("china169", "联通"), ("china unicom", "联通"),
    ("telecom", "电信"), ("chinanet", "电信"), ("ctc", "电信"),
    ("mobile", "移动"), ("cmnet", "移动"), ("cmcc", "移动"),
    ("cernet", "教育网"), ("alibaba", "阿里云"), ("tencent", "腾讯云"),
    ("google", "谷歌"), ("cloudflare", "Cloudflare"),
)


def _short_isp(isp):
    """把运营商英文名缩成中文（CNC Group CHINA169 → 联通）。"""
    low = isp.lower()
    for key, name in _ISP_MAP:
        if key in low:
            return name
    return isp if len(isp) <= 12 else ""


def lookup(ip, timeout=3.0):
    """返回「国家 省 市 运营商」这种中文描述；查不到返回「—」。"""
    ip = str(ip or "").strip()
    if not ip:
        return "—"
    if _is_private(ip):
        return "内网"
    now = time.time()
    hit = _cache.get(ip)
    if hit and now - hit[1] < _TTL:
        return hit[0]
    value = "—"
    try:
        query = urllib.parse.urlencode({
            "lang": "zh-CN",
            "fields": "status,country,regionName,city,isp",
        })
        url = f"http://ip-api.com/json/{ip}?{query}"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        if data.get("status") == "success":
            parts = [data.get("country"), data.get("regionName"), data.get("city")]
            place = " ".join(p for p in parts if p) or "—"
            isp = _short_isp((data.get("isp") or "").strip())
            value = f"{place} {isp}".strip() if isp else place
    except Exception:                        # noqa: BLE001
        value = "—"
    with _lock:
        _cache[ip] = (value, now)
    return value
