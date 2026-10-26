"""联网工具：搜资料，以及打开链接读正文。

搜索是抓结果页 HTML 再解析，没有用付费 API。抓 HTML 天生怕两件事：被风控挡下来、
对方换模板。所以这里挂了多家引擎（百度 / 必应 / 360 / DuckDuckGo），一家抠不到就换
下一家，并把每家的失败原因一起带回去，方便看出是哪一种问题。

百度排在最前头：中文查询里它最稳，必应碰上中文长句会把查询拆散（「数字取证 内存
取证 volatility」这种复合词，它会退化成只搜「数字」）。

代理默认直连（见 config.SEARCH_PROXY）；机器必须靠代理才能出网的话，在 .env 里设。
open_url 负责把网页正文抓回来，只认 http/https，本机和内网地址不给开。HTTP 302、
<meta refresh>、JS 里的 location 跳转都会一路跟下去（每跳都重新校验地址），
正文抓不到时还会试着从页面内嵌的 JSON 里捞。全是跨平台的纯 Python 实现。
"""
import base64
import html
import ipaddress
import json
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urljoin, urlsplit

import charset_normalizer
import lxml.etree
import lxml.html
import requests

from ..config import SEARCH_CACHE_TTL, SEARCH_COOLDOWN, SEARCH_MIN_INTERVAL, SEARCH_PROXY

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "联网搜索资料，返回标题、链接、摘要，并**自动附上最相关两条的正文摘录**。"
                "拿到这些一般就够回答了，不用再 open_url 打开同样的链接；"
                "一个提问最多搜 1~2 次，关键词不合适就换词，别反复搜同一串。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回条数，默认 5，最多 10",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": (
                "打开一个 http/https 链接，把网页正文抓回来读。"
                "只在「确实要看原文细节」时用：web_search 的返回里已经带了前两条正文摘录，"
                "别再用它打开同样的链接；一次提问最多开 1 个网址。"
                "返回的是网页正文纯文本；打不开、或者不是网页（图片、压缩包之类）会直接说明。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要打开的完整网址，要带 http:// 或 https://",
                    },
                },
                "required": ["url"],
            },
        },
    },
]

SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ---- 提速参数（都是实测调出来的）----
SEARCH_TIMEOUT = 5          # 单家搜索引擎的请求超时（原来 20s，太宽）
SEARCH_BUDGET = 4.5         # web_search 整体预算：到点就用已经拿到的结果
SEARCH_FAIL_COOLDOWN = 600  # 连不上的引擎歇 10 分钟再试
ENRICH_COUNT = 1            # 只抓最相关那 1 条的正文（2 条只多花时间）
ENRICH_TIMEOUT = 2          # 抓正文的总时限（连接+下载一起算）：抓不到就直接用摘要，不让用户等
ENRICH_CHARS = 1200         # 每条正文最多带回这么多字
ENRICH_BYTES = 64 * 1024    # 摘录只下 64KB，够抠正文了
FETCH_TIMEOUT = 6           # open_url 每一跳的超时
FETCH_DEADLINE = 12         # open_url 一次调用的总时限（含所有跳转）
MAX_BYTES = 200 * 1024      # open_url 最多下 200KB（正文本来只回 6000 字，下 2MB 纯浪费）
URL_CACHE_TTL = 600         # 同一个网址 10 分钟内不重复抓
_URL_CACHE = {}

# 全局共享会话：复用 TCP/TLS 连接，别每次请求都重新握手（每次能省 0.2~0.5s）
_FETCH_SESSION = requests.Session()
_FETCH_ADAPTER = requests.adapters.HTTPAdapter(
    pool_connections=8, pool_maxsize=8, max_retries=0)
_FETCH_SESSION.mount("http://", _FETCH_ADAPTER)
_FETCH_SESSION.mount("https://", _FETCH_ADAPTER)


def _url_cache_key(url, limit):
    return (url, int(limit))


def _url_cached(url, limit):
    with _STATE_LOCK:
        hit = _URL_CACHE.get(_url_cache_key(url, limit))
        if not hit:
            return None
        expires, text = hit
        if expires < time.monotonic():
            _URL_CACHE.pop(_url_cache_key(url, limit), None)
            return None
        return text


def _url_remember(url, limit, text):
    with _STATE_LOCK:
        if len(_URL_CACHE) > 100:
            _URL_CACHE.clear()
        _URL_CACHE[_url_cache_key(url, limit)] = (time.monotonic() + URL_CACHE_TTL, text)


def _quick_text(url, limit=ENRICH_CHARS, timeout=ENRICH_TIMEOUT):
    """轻量抓一页正文（搜索完自动补摘要用）：复用 open_url 的跳转/解包能力，
    但预算压到几秒，拿不到就返回空串（不拖慢整体）。"""
    try:
        text = open_url(url, max_chars=limit, budget=timeout,
                        hop_timeout=min(4.0, timeout))
    except Exception:
        return ""
    if not text or len(text) < 80:
        return ""
    # open_url 失败时会返回中文说明，别当正文带出去
    if text.startswith(("打不开", "这个链接", "没给网址", "读这个链接")):
        return ""
    return text


def _enrich(results, count=ENRICH_COUNT):
    """并发抓前 count 条结果的正文摘录，塞进 r["text"]。抓不到就跳过。"""
    picked = [
        r for r in (results or [])[:count]
        if r.get("url") and not any(k in r["url"] for k in ("ai.so.com", "/search/so", "so.com/s?"))
    ]
    if not picked:
        return results
    pool = ThreadPoolExecutor(max_workers=len(picked))
    try:
        futures = {pool.submit(_quick_text, r["url"]): r for r in picked}
        for future in as_completed(futures, timeout=ENRICH_TIMEOUT + 0.5):
            item = futures[future]
            try:
                text = future.result()
            except Exception:
                text = ""
            if text:
                item["text"] = text
    except TimeoutError:
        pass                    # 慢的站点不等了，有多少用多少
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results


SCRIPT_BLOCK_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")


def _clean(text: str) -> str:
    """剥标签、还原实体、把换行和连续空白压成一个空格。

    标签要按 [^>]* 匹配：结果页里的开始标签经常换着行写（百度尤其），用 .*? 会漏掉
    \n 从而把半个标签留在正文里。script / style 得整段扔掉——页面内嵌的 JSON 就是
    一堆裸文本，光剥标签剥不掉。
    """
    text = SCRIPT_BLOCK_RE.sub(" ", text)
    text = html.unescape(re.sub(r"<[^>]*>", "", text))
    return re.sub(r"\s+", " ", text).strip()


# 只认确定是风控页的特征，宁可漏判也别误判：拿「captcha」这类通用词去扫正经文
# 网页（必应自己页面里就嵌了一堆第三方 JS 域名）会把好结果也判成被挡。
_ANTIBOT_RE = re.compile(
    r"百度安全验证|wappass\.baidu\.com|"
    r"location\.replace\(\s*location\.href\.replace",
    re.I,
)


def _looks_blocked(page: str) -> bool:
    """拿回来的是不是风控 / 验证页。

    百度被挡的时候特别阴：HTTP 200，页面才几百字节，正文就一句
    location.replace（把 https 换成 http），既不报错也抠不出结果。光看「没抠到
    结果」分不出这是被挡了还是对方改了模板，单独认一下报错能说清楚点。
    """
    return bool(_ANTIBOT_RE.search(page[:4096]))


def _proxies():
    """按 config.SEARCH_PROXY 决定搜索走不走代理。

    默认直连：360 这类国内引擎走代理反而容易被拒。proxies 传 None 是让 requests
    自己去读环境变量和系统代理设置，所以「跟随系统」这一档返回 None 就行。
    """
    value = SEARCH_PROXY.lower()
    if value == "system":
        return None
    if value and value != "direct":
        return {"http": SEARCH_PROXY, "https": SEARCH_PROXY}
    return {"http": None, "https": None}


_HREF_RE = re.compile(r'href="([^"]+)"')


def _href(open_tag: str) -> str:
    """从一个 <a ...> 的开始标签里抠 href。属性顺序不固定，所以单独找。"""
    m = _HREF_RE.search(open_tag)
    return html.unescape(m.group(1)) if m else ""


# ---------------- 百度 ----------------
# 每条结果是一个 <div class="result c-container ...">，真实地址就挂在它的 mu 属性上
# （<h3> 里的 href 是 www.baidu.com/link?url= 的跳转链，得再跟 302 才知道去哪，用它兜底）。
#
# 这里用 lxml 而不是正则：百度的页面里塞满了 <!--s-data:{...}--> 注释，注释里就是
# 一堆 JSON。用正则按 "取标题到下一个标题之间" 切窗口，窗口会从注释中间开头，闭合的
# --> 落在窗口外，剥不干净，JSON 就会漏进摘要里。lxml 天然不把注释当文本。
_BAIDU_JUNK_HOSTS = ("recommend_list.baidu.com",)
_BAIDU_JUNK_URLS = ("https://top.baidu.com/board",)
_BAIDU_SNIPPET = 300


def _parse_baidu(page: str):
    try:
        doc = lxml.html.fromstring(page)
    except Exception:
        return []
    lxml.etree.strip_elements(doc, "script", "style", with_tail=False)

    results, seen = [], set()
    for div in doc.iterfind(".//div"):
        classes = (div.get("class") or "").split()
        if "result" not in classes or "c-container" not in classes:
            continue
        head = div.find(".//h3")
        if head is None:
            continue
        title = re.sub(r"\s+", " ", head.text_content()).strip()
        if not title:
            continue
        url = (div.get("mu") or "").strip()
        if not url:
            anchor = head.find(".//a")
            url = (anchor.get("href") if anchor is not None else "") or ""
        if not url:
            continue
        host = (urlsplit(url).hostname or "").lower()
        if host in _BAIDU_JUNK_HOSTS or url.startswith(_BAIDU_JUNK_URLS):
            continue
        if url in seen:      # 偶尔有嵌套的同一条，去个重
            continue
        seen.add(url)
        body = re.sub(r"\s+", " ", div.text_content()).strip()
        if body.startswith(title):
            body = body[len(title):].strip()
        results.append(
            {
                "title": title,
                "url": html.unescape(url),
                "snippet": body[:_BAIDU_SNIPPET],
            }
        )
    return results


# ---------------- 360 搜索 ----------------
_360_TITLE_RE = re.compile(
    r'<h3[^>]*class="[^"]*res-title[^"]*"[^>]*>\s*'
    r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.S,
)
_360_DESC_RE = re.compile(r'<p[^>]*class="[^"]*res-desc[^"]*"[^>]*>(.*?)</p>', re.S)


def _parse_360(page: str):
    results = []
    for m in _360_TITLE_RE.finditer(page):
        # 摘要跟在标题后面，取标题之后的一小段窗口来找，避免被嵌套标签截断
        snippet = _360_DESC_RE.search(page[m.end(): m.end() + 2000])
        results.append(
            {
                "title": _clean(m.group(2)),
                "url": html.unescape(m.group(1)),
                "snippet": _clean(snippet.group(1)) if snippet else "",
            }
        )
    return results


# ---------------- 必应 ----------------
_BING_ITEM_RE = re.compile(r'<li class="b_algo".*?</li>', re.S)
_BING_TITLE_RE = re.compile(r"<h2[^>]*>\s*<a([^>]*)>(.*?)</a>", re.S)
_BING_DESC_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)


def _parse_bing(page: str):
    results = []
    for item in _BING_ITEM_RE.findall(page):
        title = _BING_TITLE_RE.search(item)
        if not title:
            continue
        snippet = _BING_DESC_RE.search(item)
        results.append(
            {
                "title": _clean(title.group(2)),
                "url": _href(title.group(1)),
                "snippet": _clean(snippet.group(1)) if snippet else "",
            }
        )
    return results


# ---------------- DuckDuckGo（html 版，无 JS，好抓） ----------------
_DDG_TITLE_RE = re.compile(r'<a([^>]*class="[^"]*result__a[^"]*"[^>]*)>(.*?)</a>', re.S)
_DDG_DESC_RE = re.compile(
    r'<a([^>]*class="[^"]*result__snippet[^"]*"[^>]*)>(.*?)</a>', re.S
)


def _unwrap_redirect(url: str) -> str:
    """把常见的中转链接解开，少跟一跳（360 / 百度没法解，只能跟）。"""
    try:
        parts = urlsplit(url)
        host = (parts.netloc or "").lower()
        qs = parse_qs(parts.query)
        if "duckduckgo.com" in host and qs.get("uddg"):
            return qs["uddg"][0]
        if "bing.com" in host and qs.get("u"):
            raw = qs["u"][0]
            if raw.startswith("a1"):
                raw = raw[2:]
            padding = "=" * (-len(raw) % 4)
            target = base64.urlsafe_b64decode(raw + padding).decode("utf-8", "ignore")
            if target.startswith("http"):
                return target
    except Exception:
        pass
    return url


def _unwrap_ddg(url: str) -> str:
    """DDG 的链接是 //duckduckgo.com/l/?uddg=<真实地址> 这种包装，拆回真实地址。"""
    if "uddg=" not in url:
        return url
    if url.startswith("//"):
        url = "https:" + url
    real = parse_qs(urlsplit(url).query).get("uddg")
    return real[0] if real else url


def _parse_ddg(page: str):
    results = []
    for m in _DDG_TITLE_RE.finditer(page):
        snippet = _DDG_DESC_RE.search(page[m.end(): m.end() + 2000])
        results.append(
            {
                "title": _clean(m.group(2)),
                "url": _unwrap_ddg(_href(m.group(1))),
                "snippet": _clean(snippet.group(2)) if snippet else "",
            }
        )
    return results


# 按顺序试，谁先抠到「对得上」的结果就用谁。
#
# 必应挪到 360 后面：它对中文长句会把查询拆散，返回的其实是某个更短词的结果——
# 「数据结构的定义」给一堆讲「数据」的页面，「数字取证 内存取证 volatility」给讲
# 「数字」的页面。它排在前面的时候，只要百度一被风控，用户拿到的就是这种跑偏结果。
# 引擎列表（香港服务器实测结果，2026-10）：
#   360 搜索   0.7s  4 条结果  ✅ 主力
#   百度       10s+  只回 1.5KB 验证页，抠不到结果 ❌ 删
#   必应       ConnectionError（香港出口连不上）        ❌ 删
#   DuckDuckGo ConnectionError（香港出口连不上）        ❌ 删
# 以后要加引擎，直接往这个元组里塞；web_search 会并发跑、按命中率挑。
ENGINES = (
    {
        "name": "360 搜索",
        "url": "https://www.so.com/s",
        "param": "q",
        "home": "https://www.so.com/",
        "parse": _parse_360,
    },
)

# 中文没有空格，拿整串去比对太严（标题很少原样重复长短语），所以中文取相邻两字的
# 二元组当特征，英文按单词取。「数据结构的定义」会拆成 数据/据结/结构/构的/的定/定义。
_LATIN_TERM_RE = re.compile(r"[a-z][a-z0-9_.+-]*")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def _query_terms(keyword: str):
    """把查询拆成一组用来判断结果有没有跑偏的特征词。"""
    terms = _LATIN_TERM_RE.findall(keyword.lower())
    for run in _CJK_RUN_RE.findall(keyword):
        if len(run) == 1:
            terms.append(run)
        else:
            terms.extend(run[i:i + 2] for i in range(len(run) - 1))
    return list(dict.fromkeys(terms))


def _relevance(results, keyword: str) -> int:
    """这批结果和查询对上了几个特征词。

    光看「有没有结果」判断不出搜索是不是答非所问：必应退化查询之后照样返回满满
    一页结果，只是答的不是你问的。所以得回头比对特征词。
    """
    terms = _query_terms(keyword)
    if not terms:
        return 0
    return sum(
        1 for term in terms
        if any(term in (r["title"] + " " + r["snippet"]).lower() for r in results)
    )


def _render(results) -> str:
    """把结果列表拼成给模型看的正文（带正文摘录时一并给出）。"""
    parts = []
    for i, r in enumerate(results, 1):
        part = f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
        if r.get("text"):
            part += f"\n   正文摘录：{r['text']}"
        parts.append(part)
    return "\n\n".join(parts)


# ---------------- 别把引擎惹毛 ----------------
# 风控是按频率算的：同一 IP 短时间内请求多了就会被标记，之后一段时间一直返回验证页，
# 而且被标记了还接着撞会让封禁续期。所以除了「被挡了就换一家」，还得从源头上少发
# 请求、并且发得像个人。
_STATE_LOCK = threading.Lock()
_STATES = {}
_CACHE = {}


def _state(engine):
    """取这家引擎的运行状态：会话、是否预热过、上次请求时间、冷却截止、连续被挡次数。

    会话是为了攒 cookie：每次都裸连一个 /s?wd=...，等于每次都当新访客，是最像脚本的
    走法。状态放进程内存里，重启就重置——重启后网络出口通常也变了，不值得持久化。
    """
    with _STATE_LOCK:
        state = _STATES.get(engine["name"])
        if state is None:
            state = {
                "session": requests.Session(),
                "warmed": False,
                "last": 0.0,
                "blocked_until": 0.0,
                "strikes": 0,
            }
            _STATES[engine["name"]] = state
        return state


def _warm_up(engine, state):
    """第一次用这家引擎之前，先串一下它的首页把 cookie 拿到。

    直接 GET /s?wd=... 是「零 cookie 直入搜索」这种最像爬虫的走法；先访问一次首页
    拿到 BAIDUID 之类的字段，后面的搜索请求就带上了会话痕迹，明显不容易被挡。首页
    拿不到也无所谓，顶多少几个 cookie，不影响搜索本身。
    """
    if state["warmed"]:
        return
    state["warmed"] = True
    try:
        state["session"].get(
            engine["home"], headers=SEARCH_HEADERS, proxies=_proxies(), timeout=2
        )
    except Exception:
        pass


def _throttle(state):
    """和这家引擎的上一次请求拉开间隔。

    两秒内连着发好几个查询是最容易被标记的行为，所以宁可等一下。等待上限压到 5 秒：
    就算配置里写了个很大的值，也不该让工具调用卡在那儿。
    """
    wait = state["last"] + SEARCH_MIN_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(min(wait, 5.0))


def _block(engine, state):
    """记一次风控，返回这次要冷却多少秒。

    被挡之后最忌讳的就是下一轮马上再撞：拿不到结果不说，还会让封禁续期。所以连续
    被挡要按次数翻倍地歇着，这段时间里 web_search 会直接跳过它去用别家。

    次数封顶 4 次：风控一般几分钟就解，无限翻倍下去等于把这家引擎永久拉黑了。
    """
    state["strikes"] = min(state["strikes"] + 1, 4)
    backoff = SEARCH_COOLDOWN * state["strikes"]
    state["blocked_until"] = time.monotonic() + backoff
    return backoff


def _cached(key):
    """查结果缓存：同一个查询在 TTL 内不重复发请求。"""
    with _STATE_LOCK:
        hit = _CACHE.get(key)
        if not hit:
            return None
        expires, body = hit
        if expires < time.monotonic():
            _CACHE.pop(key, None)
            return None
        return body


def _remember(key, body):
    """存结果缓存。带个简单的条数封顶，免得长跑的后台把内存吃满。"""
    if SEARCH_CACHE_TTL <= 0:
        return
    with _STATE_LOCK:
        if len(_CACHE) > 200:
            _CACHE.clear()
        _CACHE[key] = (time.monotonic() + SEARCH_CACHE_TTL, body)


def _try_engine(engine, keyword: str, limit: int):
    """跑一家引擎，返回 (结果列表, 说明, 命中数)。

    拿到东西就把解析结果和它跟查询对上的特征词数一起返回；冷却中 / 连不上 / 被挡 /
    没抠到就返回空列表和原因，命中数记 0。
    """
    state = _state(engine)
    now = time.monotonic()
    if now < state.get("fail_until", 0.0):
        return [], f'{engine["name"]}连不上（稍后自动重试）', 0
    if now < state["blocked_until"]:
        left = int(state["blocked_until"] - now)
        return [], f'{engine["name"]}还在冷却（{left} 秒后再试）', 0

    _warm_up(engine, state)
    _throttle(state)
    state["last"] = time.monotonic()

    headers = dict(SEARCH_HEADERS)
    headers["Referer"] = engine["home"]     # 从首页点进搜索，才是浏览器该有的样子
    try:
        resp = state["session"].get(
            engine["url"],
            params={engine["param"]: keyword},
            headers=headers,
            proxies=_proxies(),
            timeout=SEARCH_TIMEOUT,
        )
    except Exception as e:
        state["fail_until"] = time.monotonic() + SEARCH_FAIL_COOLDOWN
        return [], f'{engine["name"]}连不上（{type(e).__name__}）', 0
    if resp.status_code != 200:
        return [], f'{engine["name"]}返回 HTTP {resp.status_code}', 0
    if _looks_blocked(resp.text):
        backoff = _block(engine, state)
        return (
            [],
            f'{engine["name"]}被风控挡了（{len(resp.content)} 字节的验证页，歇 {int(backoff)} 秒）',
            0,
        )

    results = engine["parse"](resp.text)[:limit]
    if not results:
        # 抠不到一般是两种情况：被风控挡了（拿回来的是几 KB 的验证页），
        # 或者对方换模板了（页面照样几十万字节）。带上大小就能区分。
        return [], f'{engine["name"]}没抠到结果（页面 {len(resp.content)} 字节）', 0
    state["strikes"] = 0        # 这一家搜通了，连续被挡的次数清零
    return results, "", _relevance(results, keyword)


def _finish(keyword, total, notes, candidates, key):
    """从候选里挑一份：命中率高的优先（同分取先到的），全跑偏就用最接近的。"""
    good = [c for c in candidates if c[0] > 0]
    if good:
        good.sort(key=lambda c: (-c[0], c[1]))
        hits, _, engine, results = good[0]
        _enrich(results)
        trail = [f"结果来自{engine['name']}"] + notes
        if any(r.get("text") for r in results):
            trail.append("已附最相关一条的正文摘录，够用就别再 open_url")
        else:
            trail.append("这条只拿到引擎摘要（正文没抓到，可能被限流），够用就直接回答，别硬等")
        body = _render(results) + "\n\n（" + "；".join(trail) + "）"
        _remember(key, body)
        return body

    off = [c for c in candidates if c[3]]
    if off:
        off.sort(key=lambda c: c[1])
        _, _, engine, results = off[0]
        _enrich(results)
        return (
            _render(results)
            + f"\n\n（没找到和「{keyword}」对得上的结果。上面这些是{engine['name']}返回的，"
              "答的很可能不是你要问的——换个说法再搜一次试试）"
        )

    detail = "；".join(notes)
    if notes and all("连不上" in note for note in notes):
        detail += (
            "。各家都连不上，多半是这台机器的网络出不去，"
            "要是必须走代理才能上外网，就在 .env 里设 SEARCH_PROXY=system，或者直接填代理地址"
        )
    return f"没搜到「{keyword}」的结果（{detail}）"


def web_search(keyword: str, max_results: int = 5) -> str:
    """跑搜索引擎拿结果，并自动补上最相关几条的正文摘录。

    - 只有一家引擎（现在就是 360）时直接跑，耗时 ≈ 一次请求 + 抓两条摘录；
    - 以后加了多家引擎会自动并发，总耗时≈最快一家（有预算兜底，不等慢的）；
    - 抓到结果后并发补前两条的正文摘录，模型一般就不用再 open_url 了
      （open_url 实测 12~21 秒，那一步才是真慢）。
    """
    limit = max(1, min(max_results, 10))
    key = (keyword, limit)
    cached = _cached(key)
    if cached is not None:
        return cached

    total = len(_query_terms(keyword)) or 1
    notes = []
    candidates = []          # [(hits, 到达顺序, engine, results)]

    if len(ENGINES) == 1:
        engine = ENGINES[0]
        results, note, hits = _try_engine(engine, keyword, limit)
        if results:
            candidates.append((hits if hits * 2 >= total else 0, 1, engine, results))
        elif note:
            notes.append(note)
        return _finish(keyword, total, notes, candidates, key)

    order = 0
    pool = ThreadPoolExecutor(max_workers=max(1, len(ENGINES)))
    try:
        futures = {pool.submit(_try_engine, engine, keyword, limit): engine for engine in ENGINES}
        deadline = time.monotonic() + SEARCH_BUDGET
        for future in as_completed(futures, timeout=SEARCH_BUDGET):
            engine = futures[future]
            order += 1
            try:
                results, note, hits = future.result()
            except Exception as exc:            # noqa: BLE001
                results, note, hits = [], f'{engine["name"]}出错（{type(exc).__name__}）', 0
            if not results:
                if note:
                    notes.append(note)
                continue
            if hits * 2 >= total:
                candidates.append((hits, order, engine, results))
                if time.monotonic() >= deadline - 0.2:
                    break
            else:
                notes.append(f'{engine["name"]}把查询理解偏了（只有 {hits}/{total} 个关键词对得上）')
                candidates.append((0, order, engine, results))
    except TimeoutError:
        notes.append(f"有引擎 {SEARCH_BUDGET:g} 秒没返回，先用已有的结果")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return _finish(keyword, total, notes, candidates, key)


# ---------------- 打开链接 ----------------

FETCH_HEADERS = {
    "User-Agent": SEARCH_HEADERS["User-Agent"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MAX_CHARS = 6000              # 正文最多回这么多字，再长就截断
MAX_REDIRECTS = 4             # 最多跟 4 跳（原来 8 跳，实测 360 跳转链能拖到 20s）
BOUNCE_MAX_BYTES = 32 * 1024  # 中转页都很小，超过这个大小就不当成中转页
BOUNCE_MAX_TEXT = 500         # 中转页的可见文字很少，多了就说明是正经网页
MIN_TEXT = 200                # 正文少于这么多字，就怀疑是 JS 动态渲染出来的
DROP_TAGS = ("script", "style", "noscript", "svg", "iframe", "form",
             "nav", "footer", "header", "aside", "template", "button")
BLOCK_END = re.compile(
    r"</(?:p|div|li|tr|h[1-6]|section|article|blockquote|pre|table|ul|ol)\s*>|<br\s*/?>",
    re.I,
)
TAG_RE = re.compile(r"<[^>]*>")
BLANK_RE = re.compile(r"\n{3,}")
SPACE_RE = re.compile(r"[ \t\u00a0]+")
CHARSET_RE = re.compile(rb"""charset\s*=\s*["']?([\w-]+)""", re.I)

# 页面自己发起的两种跳转：<meta http-equiv="refresh">，以及 JS 里的 location 赋值
META_REFRESH_RE = re.compile(
    r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]*content=["']?\s*[\d.]+\s*;\s*url=([^"'>\s]+)""",
    re.I,
)
JS_REDIRECT_RE = re.compile(
    r"""(?:window\.)?location(?:\.href)?\s*(?:=|\.replace\(|\.assign\()\s*["']([^"']+)["']""",
    re.I,
)


# 真正碰不得的网段：本机、内网、链路本地、运营商级 NAT。
#
# 这里没用 ip.is_global 一票否决：手机上挂透明代理（Clash 之类）时 DNS 会被劫持成
# 假 IP——Clash 默认就拿 198.18.0.0/15 当 fake-ip 池，所有域名都解析到这个段里。
# 而 198.18.0.0/15 在 Python 里既不算 global 也不算 reserved，用 is_global 会把
# 所有正常外链都判成内网，整条 open_url 就废了。所以改成只拦下面这些段。
_LOCAL_NETS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",        # 本机
        "10.0.0.0/8",       # 私网
        "100.64.0.0/10",    # 运营商级 NAT
        "127.0.0.0/8",      # 回环
        "169.254.0.0/16",   # 链路本地，云元数据 169.254.169.254 就在这一段
        "172.16.0.0/12",    # 私网
        "192.168.0.0/16",   # 私网
        "::/128",           # 未指定
        "::1/128",          # 回环
        "fc00::/7",         # 唯一本地地址
        "fe80::/10",        # 链路本地
    )
)


def _is_local(ip) -> bool:
    """这个地址是不是本机 / 内网。v4-mapped 的 v6 地址先还原成 v4 再判。"""
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_multicast:
        return True
    return any(ip in net for net in _LOCAL_NETS)


def _host_blocked(host: str) -> str:
    """本机、内网、链路本地一律不给开，免得拿助手当跳板去戳内部服务。

    返回拒绝原因；没问题的地址返回空串。解析不了的域名按「拦下」处理：如果这里
    放行，攻击者只要让校验那次解析失败、真正连接那次解析到内网，就是典型的
    DNS rebinding 绕过。
    """
    if not host:
        return "地址里没有主机名"
    host = host.lower().strip("[]")
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return "指向本机"
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return "这个域名解析不了"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_local(ip):
            return "解析到了本机或者内网地址"
    return ""


def _check_url(url: str):
    """只认 http/https，且不能是本机/内网。合法返回 None，否则返回拒绝说明。"""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https"):
        return f"只认 http / https 的链接，{parsed.scheme or '这种'}开头的不给开"
    reason = _host_blocked(parsed.hostname)
    if reason:
        return f"这个地址助手不给开（{reason}）"
    return None


def _bounce_target(url: str, raw: bytes, ctype: str):
    """页面本身只是个中转页就返回它要去的地址，不是中转页返回 None。

    认 <meta http-equiv="refresh"> 和 JS 里的 location 赋值这两种。中转页都是又小
    又空，所以只在小页面、且可见文字很少的时候才认，免得把正经网页里出现的同名
    字符串误当成跳转。
    """
    if len(raw) > BOUNCE_MAX_BYTES:
        return None
    text = raw.decode("utf-8", "ignore")
    if "html" not in ctype and "<html" not in text[:2000].lower():
        return None
    visible = TAG_RE.sub(" ", SCRIPT_BLOCK_RE.sub(" ", text))
    if len(visible.strip()) > BOUNCE_MAX_TEXT:
        return None
    m = META_REFRESH_RE.search(text) or JS_REDIRECT_RE.search(text)
    if not m:
        return None

    target = urljoin(url, html.unescape(m.group(1)))
    base, moved = urlsplit(url), urlsplit(target)
    if moved.scheme not in ("http", "https"):
        return None     # javascript: / mailto: 之类，跟不了
    if (moved.scheme, moved.netloc, moved.path, moved.query) == \
            (base.scheme, base.netloc, base.path, base.query):
        return None     # 只换了个 #锚点，等于没跳
    return target


_EMBEDDED_RE = (
    re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S),
    re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S),
    re.compile(r"window\.__NUXT__\s*=\s*(\{.*?\})\s*</script>", re.S),
    re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;", re.S),
)


def _flatten(value, out, depth=0):
    """把嵌套 JSON 里的字符串取出来拼成文本。"""
    if depth > 8 or len(out) > 4000:
        return
    if isinstance(value, str):
        text = value.strip()
        if len(text) > 1:
            out.append(text)
    elif isinstance(value, dict):
        for item in value.values():
            _flatten(item, out, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _flatten(item, out, depth + 1)


def _embedded_text(markup: str) -> str:
    """从页面内嵌的 JSON（Next / Nuxt / ld+json 之类）里捞正文。

    「动态加载」的页面很多其实是服务端渲染 + 前端注水，内容就躺在这些 JSON 里，
    不真的跑 JS 也能拿到一部分。
    """
    parts = []
    for pattern in _EMBEDDED_RE:
        for block in pattern.findall(markup):
            try:
                data = json.loads(html.unescape(block))
            except Exception:
                continue
            _flatten(data, parts)
    return "\n".join(dict.fromkeys(p for p in parts if p))[:MAX_CHARS]


def _decode(raw: bytes, encoding) -> str:
    """猜编码。requests 对没写 charset 的网页会给 iso-8859-1，那个基本是错的，不认。"""
    enc = encoding
    if enc and enc.lower() in ("iso-8859-1", "ascii"):
        enc = None
    if not enc:
        m = CHARSET_RE.search(raw[:4096])
        enc = m.group(1).decode("ascii", "ignore") if m else None
    if not enc:
        best = charset_normalizer.from_bytes(raw).best()
        enc = str(best.encoding) if best else "utf-8"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _pick_main(doc):
    """挑正文。正文块里的 <p> 总字数一般最多，取它；都不像正文就退回整个文档。"""
    best, best_len = None, 0
    for node in doc.iter("article", "main", "div", "section"):
        length = sum(len(p.text_content()) for p in node.iter("p"))
        if length > best_len:
            best, best_len = node, length
    return best if best_len >= 200 else doc


def _html_to_text(markup: str) -> str:
    try:
        doc = lxml.html.fromstring(markup)
    except Exception:
        return ""
    lxml.etree.strip_elements(doc, *DROP_TAGS, with_tail=False)
    node = _pick_main(doc)
    out = lxml.etree.tostring(node, encoding="unicode", method="html")
    out = BLOCK_END.sub("\n", out)
    out = html.unescape(TAG_RE.sub("", out))
    out = SPACE_RE.sub(" ", out)
    out = "\n".join(line.strip() for line in out.splitlines())
    return BLANK_RE.sub("\n\n", out).strip()


def open_url(url: str, max_chars: int = MAX_CHARS, budget: float = FETCH_DEADLINE,
             hop_timeout: float = FETCH_TIMEOUT) -> str:
    """打开一个网址，把网页正文抓回来。

    会一路跟着跳转走：HTTP 3xx、页面里的 <meta refresh>、JS 的 location 赋值都认。
    每一跳都重新校验一遍地址（防 SSRF），跳回走过的地址就停下来说清楚，免得死循环。
    """
    url = (url or "").strip()
    if not url:
        return "没给网址"

    cached = _url_cached(url, max_chars)
    if cached is not None:
        return cached

    original_url = url
    url = _unwrap_redirect(url)
    session = _FETCH_SESSION        # 复用连接，省 DNS/TLS 握手
    visited = []
    deadline = time.monotonic() + budget

    for _ in range(MAX_REDIRECTS + 1):
        left = deadline - time.monotonic()
        if left <= 0.5:
            return (f"这个链接太慢了：{budget:g} 秒内还没走到正文"
                    "（跳转太多或者对方站很慢），先别看了，换个来源试试")
        bad = _check_url(url)
        if bad:
            if not visited:
                return bad
            return f"链接跳转到了不给开的地方（{url}），助手就不跟了。{bad}"

        headers = dict(FETCH_HEADERS)
        if visited:
            headers["Referer"] = visited[-1]
        try:
            resp = session.get(url, headers=headers,
                               timeout=max(1.0, min(hop_timeout, left)),
                               stream=True, allow_redirects=False)
        except Exception as e:
            return f"打不开这个链接：{e}"

        with resp:
            status = resp.status_code
            ctype = (resp.headers.get("Content-Type") or "").lower()
            location = resp.headers.get("Location")
            encoding = resp.encoding
            chunks, total = [], 0
            try:
                for chunk in resp.iter_content(8192):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= MAX_BYTES or time.monotonic() > deadline:
                        break
            except Exception as e:
                return f"读这个链接的时候断了：{e}"

        raw = b"".join(chunks)
        cut = total >= MAX_BYTES

        if status in (301, 302, 303, 307, 308) and location:
            target = urljoin(url, location)              # HTTP 跳转
        else:
            if status >= 400:
                return f"打不开这个链接：HTTP {status}"
            if "pdf" in ctype:
                return "这个链接是 PDF，助手没法直接读正文。想读的话下下来放进 knowledge/ 目录，助手就能索引了"
            if ctype and not any(k in ctype for k in ("html", "text", "json", "xml")):
                return f"这个链接不是网页（{ctype.split(';')[0]}），助手打开也读不了"
            target = _bounce_target(url, raw, ctype)     # 页面自己发的跳转

        if target:
            if target in visited:
                # 只把绕圈的那一段列出来（从 target 上次出现的地方开始）
                path = visited + [url]
                chain = " → ".join(path[path.index(target):] + [target])
                return (f"这个链接在几个地址之间来回跳（{chain}），助手不跟了。"
                        "这种一般是要登录、或者先让你同意 Cookie 的中间页")
            visited.append(url)
            url = target
            continue

        markup = _decode(raw, encoding)
        is_html = "html" in ctype or "<html" in markup[:2000].lower()
        text = _html_to_text(markup) if is_html else markup

        # 正文太少就先怀疑是 JS 动态渲染，试着从内嵌 JSON 里捞一份
        fallback = ""
        if is_html and len(text.strip()) < MIN_TEXT:
            embedded = _embedded_text(markup)
            if len(embedded) > len(text.strip()):
                text = embedded
                fallback = "\n\n（这段是从页面内嵌的数据里抠出来的：正文靠 JS 动态渲染，直接抓 HTML 拿不到）"

        if not text.strip():
            return ("网页打开了，但里面没抓到正文喵——要么是纯图片页 / 要登录，"
                    "要么正文是 JS 动态渲染出来的（这种纯抓取拿不到）。"
                    "要是页面在浏览器里能正常看，把内容截图发助手也行")

        text = text.strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n……（正文太长，助手先看到这儿）"
        elif cut:
            text += f"\n\n……（网页太大，只下了前 {MAX_BYTES // 1024}KB）"
        result = text + fallback
        _url_remember(original_url, max_chars, result)
        _url_remember(url, max_chars, result)
        return result

    return f"这个链接跳了 {MAX_REDIRECTS} 次还没走到正经页面，助手不跟了"

