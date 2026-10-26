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

from ..config import PROJECT_ROOT, SITE_API, SITE_WWW
from . import blacklist

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

# 站点口径。日志里两个站混在一起：WWW 站是塔菲，API 站是 one-api；
# 裸 IP 和随机 Host 头都落到默认 server（API 站）。
# 域名**不写死**：从 config 读（.env 里的 TAFFY_SITE_WWW / TAFFY_SITE_API），
# 默认是占位域名，换部署只改环境变量，不用动代码。
SITE_LABELS = {SITE_WWW: SITE_WWW, SITE_API: SITE_API}
SITE_BASE = SITE_WWW.split(".", 1)[1] if "." in SITE_WWW else SITE_WWW
SITE_ALIASES = {
    "www": SITE_WWW, "api": SITE_API, "all": "all", "": SITE_WWW,
    # 也认全名——别处传进来的可能已经是规范化之后的值
    SITE_WWW: SITE_WWW, SITE_API: SITE_API,
}


def norm_site(site):
    s = (site or "www").strip().lower()
    return SITE_ALIASES.get(s, SITE_WWW)


def site_of(rec):
    """这条请求属于哪个站。"""
    host = (rec["host"] or "").strip().lower()
    if host == SITE_WWW:
        return SITE_WWW
    if host == SITE_API:
        return SITE_API
    if not host:
        # 老日志没有 host 字段，按路径猜
        guess = _classify("", rec["url"].split("?")[0], rec["ref"] or "")
        return SITE_API if guess == SITE_API else SITE_WWW
    # 裸 IP / 各种随机 Host 头 → 默认 server 是 one-api
    return SITE_API

# 真访客才会碰的路径：前端资源 / 接口 / 头像。扫描器一般只抓 HTML 表面
# 扫描器也会抓 favicon / logo，所以只认「真前端」才有的：打包 js/css 和头像。
# **不能把 /api/ 算进来**：扫描器最爱挨个试 /api/user/login、/api/config 这类接口，
# 算进来它们就成了「加载过前端资源的真人」——112.14.75.8 就是这么逃掉的。
APP_HINTS = ("/static/", "/avatar", "/manifest")

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

# ---------------- 机房 / 云主机 / IDC 识别 ----------------
#
# 目的：只留真实访客，把机房 IP（云服务器、VPS、CDN、托管、扫描器老巢）全排掉。
# 判定三重保险，命中任意一个就算机房：
#   1. ip-api 的 hosting / proxy 标志（对国外机房很准）
#   2. ASN 黑名单（中国云商的 ISP 名字往往没有 cloud 字样，比如华数、火山引擎）
#   3. ISP / org / AS 字符串关键词（兜住没进名单的小机房）
IDC_ASNS = {
    # 阿里云
    "37963", "45102", "24429", "134963",
    # 腾讯云
    "45090", "132203", "132591", "133478",
    # 华为云 / 百度云 / 字节火山引擎 / UCloud / 金山云
    "55990", "136907", "38365", "137718", "135377", "137694",
    # 华数传媒（杭州，大量 IDC 段）
    "24139",
    # 网络空间搜索引擎 / 测绘：ONYPHE 自建 ASN
    "213412", "398324",
    # 国际云 / CDN / 托管
    "16509", "14618", "8987",          # AWS
    "15169", "396982", "19527",        # Google
    "8075", "8068", "12076",           # Azure
    "13335",                            # Cloudflare
    "20940", "16625", "21342",          # Akamai
    "14061",                            # DigitalOcean
    "20473",                            # Vultr / Choopa
    "63949",                            # Linode
    "16276",                            # OVH
    "24940", "213230",                  # Hetzner
    "9009",                             # M247
    "51167",                            # Contabo
    "12876",                            # Scaleway
    "31898",                            # Oracle Cloud
    "36351",                            # IBM SoftLayer
    "33070", "19994",                   # Rackspace
    "36352",                            # HostPapa / ColoCrossing
    "53667",                            # FranTech / BuyVM
    "21859",                            # Zenlayer
    "18779",                            # EGIHosting
    "47583",                            # Hostinger
    "22612",                            # Namecheap
    "54290",                            # Hostwinds
    "8100",                             # QuadraNet
    "40676",                            # Psychz
    "197769",                           # VPS Dedicated
    "199524",                           # Gcore
    "54113",                            # Fastly
    "60068",                            # CDN77
    "19551",                            # Imperva
    "30148",                            # Sucuri
}

IDC_WORDS = re.compile(
    r"cloud|hosting|data ?cent(er|re)|datacenter|\bidc\b|colocation|\bcolo\b|"
    r"server|vps|dedicat|virtual private|"
    r"amazon|aws|google|microsoft|azure|digitalocean|vultr|linode|akamai|cloudflare|"
    r"ovh|hetzner|leaseweb|m247|choopa|contabo|scaleway|zenlayer|hostpapa|frantch|"
    r"egihosting|hosting|"
    r"alibaba|aliyun|tencent|huawei|volcano|bytedance|ucloud|kingsoft|baidu|"
    # 网络空间搜索引擎 / 安全测绘 / 学术扫描项目（它们自带 ASN，名字里也没有 cloud）
    r"onyphe|shodan|censys|leakix|binaryedge|zoomeye|fofa|greynoise|criminal ?ip|"
    r"netsystemsresearch|internetmeasurement|internet measurement|expanse|"
    r"security research|softsec|ruhr-uni-bochum|university of|"
    r"scanner|scanning|audit-scanner|"
    r"网络中心|数据中心|云计算|云服务|机房|网络科技|信息技术|传媒网络|"
    r"internet network information center",
    re.I,
)


def _asn_number(text):
    m = re.match(r"AS(\d+)", (text or "").strip(), re.I)
    return m.group(1) if m else ""


def _is_idc(info):
    """是不是机房 / 云主机 / 已经上过黑名单的托管 IP。"""
    if not info:
        return False
    if info.get("hosting") or info.get("proxy"):
        return True
    asn = _asn_number(info.get("as"))
    if asn and asn in IDC_ASNS:
        return True
    blob = " ".join(str(info.get(k) or "") for k in ("isp", "org", "as"))
    return bool(IDC_WORDS.search(blob))


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


GEO_FIELDS = ("status,country,regionName,city,isp,org,as,hosting,proxy,query")
GEO_BUDGET_SEC = 8.0   # 一次请求里最多花这么久补归属地


def _fill_geo(ips):
    """补齐没查过（或者缓存是旧版、没有 hosting/as 字段）的 IP。

    只在后台点开流量页时走到：限个数、限时间，查不到就留空，
    顶多是这个 IP 少一条判定依据，绝不让后台卡住。
    """
    cache = _load_geo()
    # 旧缓存只有 country/region/city/isp，缺 as / hosting → 也要补查，否则机房判定不全
    missing = [ip for ip in ips if ip not in cache or "hosting" not in (cache.get(ip) or {})]
    if not missing:
        return cache

    # 候选 IP 可能几百个，一次全查会拖慢后台：分块查，给个总时间预算。
    # 没轮上的下次点开继续补（缓存是累积的），所以最多头一两次看着慢一点。
    deadline = time.time() + GEO_BUDGET_SEC
    for start in range(0, len(missing), 100):
        if time.time() > deadline:
            break
        chunk = missing[start:start + 100]
        try:
            body = json.dumps(chunk).encode("utf-8")
            req = urllib.request.Request(
                "http://ip-api.com/batch?fields=" + GEO_FIELDS + "&lang=zh-CN",
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
                        "org": item.get("org") or "",
                        "as": item.get("as") or "",
                        "hosting": bool(item.get("hosting")),
                        "proxy": bool(item.get("proxy")),
                    }
                else:
                    cache[q] = {"country": "", "region": "", "city": "", "isp": "",
                                "org": "", "as": "", "hosting": False, "proxy": False}
        except Exception:
            break
        # 免费接口限 45 次/分钟，块之间稍微停一下
        time.sleep(0.3)
    _save_geo(cache)
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


def _host_of(value):
    """从 host / url / referer 里抠主机名（去掉协议、路径、端口）。"""
    text = (value or "").strip().lower()
    if not text:
        return ""
    if "//" in text:
        text = text.split("//", 1)[1]
    return text.split("/", 1)[0].split(":", 1)[0]


def _matches_site(host, site):
    """host 属不属于 site 这个站（含它自己的子域）。"""
    if not host:
        return False
    if host == site:
        return True
    return host == SITE_BASE or host.endswith("." + SITE_BASE)


def _classify(host, path, ref=""):
    """这条记录属于哪个站。认不出来就原样返回 host，不硬套。"""
    name = _host_of(host)
    if name:
        if name == SITE_API or name.startswith("api."):
            return SITE_API
        if _matches_site(name, SITE_WWW):
            return SITE_WWW
        return name
    name = _host_of(ref)
    if name:
        if name == SITE_API or name.startswith("api."):
            return SITE_API
        if _matches_site(name, SITE_WWW):
            return SITE_WWW
    if API_PATHS.match(path):
        return SITE_API
    if TAFFY_PATHS.match(path):
        return SITE_WWW
    return "未标注"


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


# ---------------- 主逻辑 ----------------

def _compute(days, overseas, scanners=True, only_site=SITE_WWW):
    files = _log_files(days)
    if not files:
        return {"ok": False, "error": "找不到 nginx 访问日志（要 root 才能读）"}

    since = None
    if days:
        since = (datetime.now(LOCAL_TZ) - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0)

    in_tencent = _tencent_matcher()
    banned_ips = blacklist.banned_ips()

    # 第一遍：收集候选 IP（排掉本机 / 扫描器 / 腾讯网段），查归属地
    #
    # 扫描器按 IP 整体拉黑：只要这个 IP 探过 /.env、/.git 这类敏感路径，
    # 或者 UA 一眼是工具，那它后面装的「正常请求」也不再算访客——
    # 扫描器经常先探一下敏感路径、再老老实实 get 首页，逐条判断会漏。
    candidate = set()
    scanner_ips = set()
    app_ips = set()   # 真正加载过前端资源（JS/CSS/接口）的 IP，才算真访客
    for r in _iter_records(files, since):
        if only_site != "all" and site_of(r) != only_site:
            continue
        ip = r["ip"]
        if ip in LOOPBACK:
            continue
        base = r["url"].split("?")[0]
        if scanners and _is_bot(r["ua"], base):
            scanner_ips.add(ip)
            continue
        if base.startswith(APP_HINTS):
            app_ips.add(ip)
        if in_tencent(ip):
            continue
        candidate.add(ip)

    # 行为判定：一个真访客打开页面，浏览器必然会去拉 /static/* 的 js、css 和 /api/* 的接口。
    # 只抓了首页、/logo.png、/robots.txt 这种「表面路径」的，就是扫描器 / 测绘器
    # （Onyphe、Shodan 这类就是这么整站摸一遍的）。
    if scanners:
        for ip in candidate:
            if ip not in app_ips:
                scanner_ips.add(ip)

    geo = _fill_geo(candidate)

    # 只留真实访客：机房 / 云主机 / 代理一律排掉（不管算不算海外）
    keep = {ip for ip in candidate if not _is_idc(geo.get(ip))}
    if not overseas:
        keep = {ip for ip in keep
                if (geo.get(ip) or {}).get("country") in ("中国", "China", "")}

    # 第二遍：出统计
    n_loop = n_bots = n_tc = n_os = n_idc = n_ban = 0
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
        if only_site != "all" and site_of(r) != only_site:
            continue
        ip = r["ip"]
        base = r["url"].split("?")[0]

        if ip in LOOPBACK:
            n_loop += 1
            continue
        if ip in banned_ips:
            n_ban += 1
            continue
        if ip in scanner_ips:
            n_bots += 1
            continue
        if in_tencent(ip):
            n_tc += 1
            continue
        if ip not in keep:
            if _is_idc(geo.get(ip)):
                n_idc += 1
            else:
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
        "scanners": bool(scanners),
        "site": only_site,
        "site_label": SITE_LABELS.get(only_site, "全部站点"),
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
            "idc": n_idc,
            "bot_ips": len(scanner_ips),
            "blacklist": n_ban,
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
        # 全量给，前端自己分页（真实访客本来就不多，多的时候也不至于截断）
        "top_ips": [
            {"ip": ip, "count": c, "place": _geo_place(geo.get(ip))}
            for ip, c in ip_count.most_common()
        ],
        "log_files": len(files),
    }


def snapshot(days=7, overseas=False, scanners=True, site="www"):
    """给后台接口用：带 60 秒缓存 + 单线程扫描。"""
    days = max(0, min(int(days or 0), 90))
    site = norm_site(site)
    key = (days, bool(overseas), bool(scanners), site)
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
        payload = _compute(days, overseas, scanners, site)
        _cached[key] = (time.time(), payload)
        return payload
    finally:
        _lock.release()


# ---------------- 单个 IP 的明细（给后台「AI 分析」用） ----------------

def ip_detail(ip, days=7, sample=40):
    """把某个 IP 这段时间的访问记录整理成一份「给模型看」的精简材料。

    只取前 sample 条请求，UA / 路径 / 状态码各取 top，避免把整本日志喂给模型
    （既贵又没用）。
    """
    ip = (ip or "").strip()
    if not ip or not re.fullmatch(r"[0-9a-fA-F:.]{3,45}", ip):
        return {"ok": False, "error": "IP 格式不对"}

    files = _log_files(days)
    if not files:
        return {"ok": False, "error": "找不到 nginx 访问日志"}

    since = None
    if days:
        since = (datetime.now(LOCAL_TZ) - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0)

    recs = [r for r in _iter_records(files, since) if r["ip"] == ip]
    if not recs:
        return {"ok": False, "error": "这段时间没有这个 IP 的记录"}

    geo = (_load_geo().get(ip) or {})
    status = Counter(r["status"] for r in recs)
    paths = Counter(r["url"].split("?")[0] for r in recs)
    uas = Counter((r["ua"] or "-") for r in recs)
    hosts = Counter((r["host"] or "-") for r in recs)

    # 敏感路径 / 登录接口：带各自的状态码，方便判断「有没有得手」
    def _hits(pred):
        out = []
        for path, cnt in paths.most_common():
            if not pred(path):
                continue
            st = Counter(r["status"] for r in recs if r["url"].split("?")[0] == path)
            # 响应体大小：判断「200 是真的给了东西，还是失败也返回 200」的关键
            sz = Counter(
                f'{r["status"]}:{r["bytes"]}'
                for r in recs if r["url"].split("?")[0] == path
            )
            out.append({
                "path": path,
                "count": cnt,
                "statuses": st.most_common(4),
                "sizes": sz.most_common(4),
            })
        return out[:8]

    sensitive = _hits(is_sensitive_path)
    login = _hits(lambda p: any(p.lower().startswith(h) for h in LOGIN_HINTS))
    raw_host = sum(1 for r in recs
                   if (r["host"] or "")[:1].isdigit() and "taffy" not in (r["host"] or ""))
    app_hits = sum(1 for r in recs if r["url"].split("?")[0].startswith(APP_HINTS))
    span = max((recs[-1]["ts"] - recs[0]["ts"]).total_seconds(), 1)
    rate = round(len(recs) / (span / 60.0), 2)

    return {
        "ok": True,
        "ip": ip,
        "days": days,
        "sensitive": sensitive,
        "login": login,
        "raw_host": raw_host,
        "app_hits": app_hits,
        "rate_per_min": rate,
        "geo": {
            "country": geo.get("country") or "",
            "region": geo.get("region") or "",
            "city": geo.get("city") or "",
            "isp": geo.get("isp") or "",
            "as": geo.get("as") or "",
            "idc": _is_idc(geo),
        },
        "count": len(recs),
        "first": recs[0]["ts"].strftime("%m-%d %H:%M:%S"),
        "last": recs[-1]["ts"].strftime("%m-%d %H:%M:%S"),
        "statuses": status.most_common(6),
        "paths": paths.most_common(12),
        "uas": [(ua[:120], n) for ua, n in uas.most_common(4)],
        "hosts": hosts.most_common(4),
        "samples": [
            {
                "t": r["ts"].strftime("%m-%d %H:%M:%S"),
                "status": r["status"],
                "path": r["url"][:120],
                "ua": (r["ua"] or "-")[:80],
            }
            for r in recs[:sample]
        ],
    }


# ---------------- 高风险 IP 候选（给「AI 风险分析」粗筛用） ----------------

# 扫描器 / 攻击者最爱探的路径
SENSITIVE_HINTS = re.compile(
    r"/\.(?:env|git|svn|hg|aws|ssh|DS_Store|auth\.json|azure-pipelines|gitlab-ci|htaccess|htpasswd)|"
    r"wp-(?:login|admin|content|includes|json)|xmlrpc\.php|phpmyadmin|cgi-bin|actuator|"
    r"telescope|druid|solr|manager/html|config\.json|docker-compose|^/vendor/|^/backup|\.sql|"
    r"Dockerfile|eval-stdin|composer\.json|\.yml$|\.yaml$|\.bak$|\.old$|\.zip$|\.tar|\.tar\.gz|"
    r"/1\.sql|/\.well-known/\.\.|phpinfo|info\.php|\.php$|\.asp$|\.jsp$|server-status",
    re.I,
)

def is_sensitive_path(path):
    """是不是扫描器才会探的敏感路径。

    注意：/static/ 下的前端资源（js/css/图片）一律不算——
    之前 vendor/ 没锚定根路径，/static/vendor/marked.min.js 这种正常文件
    也被当成敏感路径，把真访客的分数刷上去了。
    """
    if not path or path.startswith("/static/"):
        return False
    return bool(SENSITIVE_HINTS.search(path))


LOGIN_HINTS = (
    "/api/user/login", "/admin/auth/login", "/api/admin/login", "/login",
    "/user/login", "/api/login", "/admin/login", "/signin",
)

# 算「高危」的最低分（低于这个不入候选，免得把正常访客塞给模型）
RISK_MIN_SCORE = 20


def risk_candidates(days=7, limit=30, site="all"):
    """从日志里按启发式挑出最可疑的一批 IP，交给模型做终审。

    为什么要粗筛：全量日志喂给模型又贵又没必要。这里先用规则打分，
    只把真正可疑的（探敏感路径 / 撞登录 / 量特别大 / 裸连 / 扫描器特征）
    挑出来，通常几十个里取二十个。
    """
    files = _log_files(days)
    if not files:
        return {"ok": False, "error": "找不到 nginx 访问日志"}

    since = None
    if days:
        since = (datetime.now(LOCAL_TZ) - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0)

    per = defaultdict(lambda: {
        "n": 0, "sens": 0, "login": 0, "deny": 0, "rawhost": 0, "garbage": 0,
        "paths": Counter(), "status": Counter(), "uas": Counter(),
        "hosts": Counter(), "sites": Counter(),
    })

    site = norm_site(site)
    for r in _iter_records(files, since):
        if site != "all" and site_of(r) != site:
            continue
        ip = r["ip"]
        if ip in LOOPBACK:
            continue
        base = r["url"].split("?")[0]
        d = per[ip]
        d["n"] += 1
        d["paths"][base] += 1
        d["status"][r["status"]] += 1
        d["uas"][(r["ua"] or "-")[:90]] += 1
        d["sites"][SITE_LABELS.get(site_of(r), "其他")] += 1
        d["hosts"][r["host"] or "-"] += 1
        if is_sensitive_path(base):
            d["sens"] += 1
        if any(base.lower().startswith(h) for h in LOGIN_HINTS):
            d["login"] += 1
        if r["status"] in ("401", "403", "444"):
            d["deny"] += 1
        host = r["host"] or ""
        if host and host[0].isdigit() and "taffy" not in host:
            d["rawhost"] += 1
        # 非 ASCII 路径 = 畸形/二进制请求（扫描器特征）
        if any(ord(ch) < 32 or ord(ch) > 126 for ch in base):
            d["garbage"] += 1

    geo = _load_geo()
    banned = blacklist.banned_ips()
    cands = []

    for ip, d in per.items():
        info = geo.get(ip) or {}
        ua = (d["uas"].most_common(1) or [("-", 1)])[0][0]
        static_hits = sum(c for p, c in d["paths"].items() if p.startswith("/static/"))

        score = 0
        score += min(d["sens"], 12) * 12           # 探敏感路径，权重最高
        score += min(d["login"], 10) * 25          # 撞登录：主动攻击，权重给高
        score += 80 if d["login"] >= 10 else 0     # 十次以上 = 持续性爆破
        score += min(d["rawhost"], 6) * 6          # 裸连服务器 IP
        score += min(d["garbage"], 5) * 8          # 畸形请求
        score += 20 if d["deny"] >= 5 else 0       # 401/403 风暴
        score += 25 if _is_bot(ua, "") else 0      # UA 就是扫描器
        score += 25 if static_hits == 0 and d["n"] >= 3 else 0   # 从没加载前端
        score += 15 if _is_idc(info) else 0        # 机房 / 云主机
        score += min(d["n"] // 20, 10) * 2         # 量大
        if score < RISK_MIN_SCORE:
            continue

        cands.append({
            "ip": ip,
            "score": score,
            "requests": d["n"],
            "place": _geo_place(info),
            "isp": info.get("isp") or "",
            "as": info.get("as") or "",
            "idc": _is_idc(info),
            "banned": ip in banned,
            "paths": d["paths"].most_common(6),
            "statuses": d["status"].most_common(5),
            "ua": ua,
            "rawhost": d["rawhost"],
            "sensitive": d["sens"],
            "login": d["login"],
            "garbage": d["garbage"],
            "static_hits": static_hits,
            "sites": "/".join(s for s, _ in d["sites"].most_common()),
        })

    cands.sort(key=lambda x: -x["score"])
    return {"ok": True, "total_scanned": len(per), "candidates": cands[:limit]}
