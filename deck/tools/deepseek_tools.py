"""究极兜底：DeepSeek 官方付费 Search / Fetch API（只在常规检索不可靠时用）。"""

import requests

from .. import settings

TIMEOUT = 30


def _endpoint(path):
    base = settings.premium_base().rstrip("/")
    return base + path


def _post(path, payload):
    key = settings.premium_key()
    if not key:
        raise RuntimeError("没配 DeepSeek 兜底 API key（后台可改）")
    resp = requests.post(_endpoint(path), json=payload, timeout=TIMEOUT,
                         headers={"Authorization": "Bearer " + key,
                                  "Content-Type": "application/json"})
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
    return resp.json()


def ultra_search(query: str, count: int = 8, ori_question: str = "", need_content: bool = False) -> str:
    """DeepSeek 官方搜索（付费兜底）：结果比自建搜索更权威，带摘要和权威度。"""
    query = str(query or "").strip()[:128]
    if not query:
        return "错误：query 不能为空"
    try:
        data = _post("/v1/tools/search", {
            "query": query,
            "count": max(1, min(int(count or 8), 20)),
            "need_content": bool(need_content),
        })
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    results = data.get("results") or []
    lines = [f"共 {len(results)} 条（按相关度/权威度排序）："]
    for i, item in enumerate(results, 1):
        lines.append(f"[{i}] {item.get('title','')}")
        lines.append(f"    URL: {item.get('url','')}")
        if item.get("summary"):
            lines.append(f"    摘要: {item['summary'][:400]}")
        if item.get("snippet"):
            lines.append(f"    片段: {item['snippet'][:200]}")
        extra = []
        if item.get("authority") is not None:
            extra.append(f"权威度 {item['authority']}")
        if item.get("rerank_score") is not None:
            extra.append(f"相关度 {item['rerank_score']:.2f}")
        if item.get("date"):
            extra.append(str(item["date"])[:10])
        if extra:
            lines.append("    " + " | ".join(extra))
        if need_content and item.get("content"):
            lines.append(f"    正文: {item['content'][:1500]}")
    return "\n".join(lines)


def ultra_fetch(url: str) -> str:
    """DeepSeek 官方抓取（付费兜底）：把网页抓成 Markdown。"""
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "错误：只支持 http/https 网址"
    try:
        data = _post("/v1/tools/fetch", {"url": url[:8000]})
    except Exception as exc:                 # noqa: BLE001
        return f"错误：{exc}"
    content = str(data.get("content") or "")
    note = "（内容过长已截断）" if len(content) > 9000 else ""
    return f"标题: {data.get('title','')}\nURL: {url}{note}\n\n{content[:9000]}"


SPECS = [
    {"type": "function", "function": {
        "name": "ultra_search",
        "description": "究极兜底搜索（付费）：常规 web_search 结果跑题/被墙/明显不可靠时再用。返回权威度更高的结果和长摘要。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索词（1~128 字，支持 site: / -site: / 引号）"},
            "count": {"type": "integer", "description": "返回条数，默认 8，最多 20"},
            "need_content": {"type": "boolean", "description": "是否顺带返回正文（更贵，默认 false）"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "ultra_fetch",
        "description": "究极兜底抓取（付费）：open_url 抓不到/被拦时，用 DeepSeek 官方抓取把网页转 Markdown。",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "http/https 网址"}}, "required": ["url"]}}},
]
