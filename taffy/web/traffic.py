"""访问日志统计：给后台「流量」页用。

数据源是 nginx 的 access.log（含轮转和 .gz 归档），口径和 /root/taffy-traffic.py 一致：
按「真实访客」算——排掉本机、扫描器、腾讯网段，默认再排掉海外 IP。

每次算都要扫一遍日志（几百毫秒），所以结果缓存 60 秒；同一时间只让一个线程扫，
别的请求直接拿上一次的结果，别把后台点卡。

读不到日志 / 读不到归属地都不抛异常，返回 ok=False 让前端显示提示——
后台面板自己绝不能把服务搞挂。
"""
import glob
import gzip
import json
import os
import re
import threading
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from ..config import PROJECT_ROOT

LOG_DIR = "/var/log/nginx"
ACCESS_LOG = os.path.join(LOG_DIR, "access.log")

# 和命令行版 /root/taffy-traffic.py 共用两份缓存，省得各查各的
GEO_CACHE = "/root/.cache/ip-geo.json"
GEO_CACHE_FALLBACK = os.path.join(PROJECT_ROOT, ".cache", "ip-geo.json")
TENCENT_CACHE = "/root/.cache/tencent-cidrs.txt"

LOCAL_TZ = timezone(timedelta(hours=8))
TIME_FMT = "%d/%b/%Y:%H:%M:%S %z"

# nginx 日志行：
#   combined            ip - user [time] "req" status bytes "ref" "ua"
#   新格式（末尾多两个）  ... "ua" "host" request_time
LINE_RE = re.compile(
    r'^(?P<ip>\S+) \S+ (?P<user>\S+) \[(?P<time>[^\]]+)\] '
    r'"(?P<req>[^"]*)" (?P<status>\d{3}|-) (?P<bytes>\d+|-) '
    r'"(?P<ref>[^"]*)" "(?P<ua>[^"]*)"'
    r'(?: "(?P<host>[^"]*)")?(?: (?P<rt>[0-9.]+))?\s*$'
)
URL_RE = re.compile(r"^\S+\s+(\S+)")

# 疑似扫描器 / 爬虫 / 自动化工具
BOT_UA = re.compile(
    r"zgrab|masscan|nmap|nikto|nuclei|sqlmap|dirbuster|gobuster|wpscan|"
    r"python-requests|python-urllib|aiohttp|httpx|go-http-client|curl/|wget/|"
    r"libwww-perl|scrapy|semrush|ahrefs|censys|shodan|internetmeasurement|"
    r"paloalto|expanse|netsystemsresearch|zmap|mongodb|httpclient|l9scan|l9explore",
    re.I,
)
BOT_PATH = re.compile(
    r"/\.env|/\.git|/owa/|/ecp/|/autodiscover|/druid/|/actuator|/cgi-bin|"
    r"/phpmyadmin|/wp-login|/wp-admin|/xmlrpc\.php|/telescope|"
    r"/developmentserver|/solr/|/console/|/manager/html|/\.aws/|/config\.json|"
    r"/docker-compose|/\.ssh/|/jenkins|/hudson|^/vendor/",
    re.I,
)
LOOPBACK = ("127.0.0.1", "::1", "localhost")

TAFFY_PATHS = re.compile(r"^/(admin|api/(chat|context|session|ping|stickers|admin)|static/)")
API_PATHS = re.compile(
    r"^/(v1/|dashboard|login|logout|register|setup|logo\.png|favicon\.ico|"
    r"static/(js|css)/|api/(status|user|token|channel|log|redemption|group|option|"
    r"model|ratio|about|uptime|verification|oauth|pricing|topup|notice|"
    r"home_page_content|ratio_config|channels|logs|tokens))"
)

# 归属地统一口径：中国的拆到省（中国·吉林），国外的用国家名
REGION_SUFFIX = ("维吾尔自治区", "壮族自治区", "回族自治区", "特别行政区", "自治区", "省", "市")
COUNTRY_ALIAS = {
    "德意志联邦共和国": "德国", "德意志": "德国", "美利坚合众国": "美国",
    "俄罗斯联邦": "俄罗斯", "大韩民国": "韩国", "日本国": "日本",
    "意大利共和国": "意大利", "澳大利亞": "澳大利亚", "中國": "中国",
    "香港特別行政區": "香港",
}

_CACHE_TTL = 60           # 秒；同一口径 60 秒内直接返回上次结果
_cached = {}              # (days, overseas) -> (ts, payload)
_lock = threading.Lock()  # 同一时间只让一个线程扫日志


# ---------------- 日志读取 ----------------

def _log_files(days):
    files = [ACCESS_LOG]
    for pat in ("access.log.[0-9]*", "access.log.[0-9]*.gz"):
        files += glob.glob(os.path.join(LOG_DIR, pat))
    out = []
    cutoff = datetime.now().timestamp() - (days + 1) * 86400 if days else None
    for f in sorted(set(files)):
        if not os.path.exists(f):
            continue
        try:
            if cutoff and os.path.getmtime(f) < cutoff:
                continue
        except OSError:
            continue
        out.append(f)
    return out


def _read_lines(files):
    for f in files:
        opener = gzip.open if f.endswith(".gz") else open
        try:
            with opener(f, "rt", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    yield line
        except OSError:
            continue


def _iter_records(files, since):
    for f in files:
        for line in _read_lines([f]):
            m = LINE_RE.match(line)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group("time"), TIME_FMT).astimezone(LOCAL_TZ)
            except ValueError:
                continue
            if since and ts < since:
                continue
            um = URL_RE.match(m.group("req") or "")
            yield {
                "ts": ts,
                "ip": m.group("ip"),
                "ua": m.group("ua") or "",
                "host": m.group("host") or "",
                "url": um.group(1) if um else "-",
                "status": m.group("status") or "-",
                "bytes": m.group("bytes"),
                "rt": m.group("rt"),
                "ref": m.group("ref") or "",
            }


# ---------------- 腾讯网段 ----------------

def _ip_to_int(ip):
    try:
        parts = [int(p) for p in ip.split(".")]
    except (ValueError, AttributeError):
        return None
    if len(parts) != 4 or any(p < 0 or p > 255 for p in parts):
        return None
    return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]


def _tencent_matcher():
    """读腾讯网段缓存（命令行版维护），读不到就不过滤。"""
    ranges = []
    try:
        with open(TENCENT_CACHE, encoding="utf-8") as fh:
            for line in fh:
                cidr = line.strip()
                if "/" not in cidr:
                    continue
                base, _, bits = cidr.partition("/")
                start = _ip_to_int(base)
                if start is None:
                    continue
                try:
                    bits = int(bits)
                except ValueError:
                    continue
                size = 1 << (32 - bits)
                ranges.append((start, start + size - 1))
    except OSError:
        return lambda _ip: False
    ranges.sort()
    starts = [r[0] for r in ranges]

    def hit(ip):
        n = _ip_to_int(ip)
        if n is None or not ranges:
            return False
        import bisect
        i = bisect.bisect_right(starts, n) - 1
        return i >= 0 and ranges[i][0] <= n <= ranges[i][1]

    return hit


# ---------------- 归属地 ----------------

def _load_geo():
    for path in (GEO_CACHE, GEO_CACHE_FALLBACK):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            continue
    return {}


def _save_geo(cache):
    for path in (GEO_CACHE, GEO_CACHE_FALLBACK):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, ensure_ascii=False)
            return
        except OSError:
            continue


def _fill_geo(ips):
    """补齐没查过的 IP。只在后台点开流量页时走到，限个数限时间，查不到就算了。"""
    cache = _load_geo()
    missing = [ip for ip in ips if ip not in cache][:60]
    if not missing:
        return cache
    try:
        body = json.dumps(missing).encode("utf-8")
        req = urllib.request.Request(
            "http://ip-api.com/batch?fields=status,country,regionName,city,isp,query&lang=zh-CN",
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.load(resp)
        for item in data:
            q = (item or {}).get("query")
            if not q:
                continue
            if item.get("status") == "success":
                cache[q] = {
                    "country": item.get("country") or "",
                    "region": item.get("regionName") or "",
                    "city": item.get("city") or "",
                    "isp": item.get("isp") or "",
                }
            else:
                cache[q] = {"country": "", "region": "", "city": "", "isp": ""}
        _save_geo(cache)
    except Exception:
        pass
    return cache


def _shorten_region(name):
    for suf in REGION_SUFFIX:
        if name.endswith(suf) and len(name) > len(suf):
            return name[:-len(suf)]
    return name


def _geo_key(info):
    if not info or not info.get("country"):
        return "未知"
    country = COUNTRY_ALIAS.get(info["country"], info["country"])
    region = info.get("region") or ""
    if country in ("中国", "China") and region:
        return "中国·" + _shorten_region(region)
    return country


def _geo_place(info):
    if not info:
        return ""
    bits = [info.get("country") or "", info.get("region") or "", info.get("city") or ""]
    return " ".join(b for b in bits if b)


# ---------------- 辅助 ----------------

def _is_bot(ua, path):
    return bool(BOT_UA.search(ua or "") or BOT_PATH.search(path or ""))


def _classify(host, path, ref=""):
    if host:
        if host.startswith("api."):
            return "api.taffy.icu"
        if "taffy.icu" in host:
            return "www.taffy.icu"
        return host
    if ref and "taffy.icu" in ref:
        return "api.taffy.icu" if "//api.taffy.icu" in ref else "www.taffy.icu"
    if API_PATHS.match(path):
        return "api.taffy.icu"
    if TAFFY_PATHS.match(path):
        return "www.taffy.icu"
    return "未标注"


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


# ---------------- 主逻辑 ----------------

def _compute(days, overseas):
    files = _log_files(days)
    if not files:
        return {"ok": False, "error": "找不到 nginx 访问日志（要 root 才能读）"}

    since = None
    if days:
        since = (datetime.now(LOCAL_TZ) - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0)

    in_tencent = _tencent_matcher()

    # 第一遍：收集候选 IP（排掉本机 / 扫描器 / 腾讯网段），查归属地
    candidate = set()
    for r in _iter_records(files, since):
        if r["ip"] in LOOPBACK:
            continue
        if _is_bot(r["ua"], r["url"].split("?")[0]):
            continue
        if in_tencent(r["ip"]):
            continue
        candidate.add(r["ip"])

    geo = _fill_geo(candidate)

    keep = None
    if not overseas:
        keep = {ip for ip in candidate
                if (geo.get(ip) or {}).get("country") in ("中国", "China", "")}

    # 第二遍：出统计
    n_loop = n_bots = n_tc = n_os = 0
    requests = pv = chat = 0
    bytes_out = 0
    rt_sum = 0.0
    rt_n = 0
    ips = set()
    per_day = defaultdict(lambda: {"requests": 0, "pv": 0, "ips": set(), "bytes": 0, "chat": 0})
    per_site = defaultdict(lambda: {"pv": 0, "ips": set()})
    paths = Counter()
    status = Counter()
    ip_count = Counter()
    first_ts = last_ts = None

    for r in _iter_records(files, since):
        ip = r["ip"]
        base = r["url"].split("?")[0]

        if ip in LOOPBACK:
            n_loop += 1
            continue
        if _is_bot(r["ua"], base):
            n_bots += 1
            continue
        if in_tencent(ip):
            n_tc += 1
            continue
        if keep is not None and ip not in keep:
            n_os += 1
            continue

        ts = r["ts"]
        requests += 1
        first_ts = ts if first_ts is None or ts < first_ts else first_ts
        last_ts = ts if last_ts is None or ts > last_ts else last_ts

        nbytes = int(r["bytes"] or 0)
        bytes_out += nbytes
        if r["rt"]:
            try:
                rt_sum += float(r["rt"])
                rt_n += 1
            except ValueError:
                pass

        site = _classify(r["host"], base, r["ref"])
        ips.add(ip)
        ip_count[ip] += 1
        paths[base] += 1
        status[r["status"]] += 1
        is_page = base in ("/", "/admin")
        if is_page:
            pv += 1
        if base == "/api/chat":
            chat += 1

        d = ts.strftime("%Y-%m-%d")
        day = per_day[d]
        day["requests"] += 1
        if is_page:
            day["pv"] += 1
        day["ips"].add(ip)
        day["bytes"] += nbytes
        if base == "/api/chat":
            day["chat"] += 1
        per_site[site]["pv"] += 1
        per_site[site]["ips"].add(ip)

    # 地区分布（按独立 IP 数）
    region = Counter(_geo_key(geo.get(ip)) for ip in ips)
    total_ips = max(len(ips), 1)

    return {
        "ok": True,
        "generated_at": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
        "overseas": bool(overseas),
        "range": {
            "from": first_ts.strftime("%Y-%m-%d") if first_ts else "",
            "to": last_ts.strftime("%Y-%m-%d") if last_ts else "",
        },
        "totals": {
            "requests": requests,
            "pv": pv,
            "ips": len(ips),
            "bytes": bytes_out,
            "bytes_human": _human(bytes_out),
            "chat": chat,
            "avg_rt": round(rt_sum / rt_n, 3) if rt_n else None,
        },
        "excluded": {
            "loopback": n_loop,
            "bots": n_bots,
            "tencent": n_tc,
            "overseas": n_os,
        },
        "per_day": [
            {"date": d, "requests": v["requests"], "pv": v["pv"],
             "ips": len(v["ips"]), "bytes": v["bytes"], "chat": v["chat"]}
            for d, v in sorted(per_day.items())
        ],
        "per_site": [
            {"site": s, "pv": v["pv"], "ips": len(v["ips"])}
            for s, v in sorted(per_site.items(), key=lambda kv: -kv[1]["pv"])
        ],
        "paths": [{"path": p, "count": c} for p, c in paths.most_common(8)],
        "status": [{"code": c, "count": n} for c, n in status.most_common(8)],
        "regions": [
            {"name": name, "count": c, "percent": round(c * 100.0 / total_ips, 1)}
            for name, c in region.most_common()
        ],
        "top_ips": [
            {"ip": ip, "count": c, "place": _geo_place(geo.get(ip))}
            for ip, c in ip_count.most_common(10)
        ],
        "log_files": len(files),
    }


def snapshot(days=7, overseas=False):
    """给后台接口用：带 60 秒缓存 + 单线程扫描。"""
    days = max(0, min(int(days or 0), 90))
    key = (days, bool(overseas))
    now = time.time()
    cached = _cached.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    if not _lock.acquire(blocking=False):
        # 已经有人在算了，把上次的结果先给出去（哪怕过期）
        if cached:
            return cached[1]
        _lock.acquire()
    try:
        again = _cached.get(key)
        if again and time.time() - again[0] < _CACHE_TTL:
            return again[1]
        payload = _compute(days, overseas)
        _cached[key] = (time.time(), payload)
        return payload
    finally:
        _lock.release()
