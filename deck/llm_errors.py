"""把各家接口的报错归类，决定「重试 / 放弃 / 压缩后重试」。

逻辑照 DSH 的 @deepseek-ai/dsh-llm 分类器搬的：
  AUTH / QUOTA / RATE_LIMIT / CONTEXT_OVERFLOW / INVALID_REQUEST / SERVER / TIMEOUT / TRANSPORT
只有 RATE_LIMIT/SERVER/TIMEOUT/TRANSPORT 值得重试；QUOTA 是终态（重试没用），
CONTEXT_OVERFLOW 交给上层「压缩后再试一次」。
"""
import re

AUTH = "AUTH"
QUOTA = "QUOTA"
RATE_LIMIT = "RATE_LIMIT"
CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
INVALID = "INVALID_REQUEST"
SERVER = "SERVER"
TIMEOUT = "TIMEOUT"
TRANSPORT = "TRANSPORT"
UNKNOWN = "UNKNOWN"

RETRYABLE = frozenset({RATE_LIMIT, SERVER, TIMEOUT, TRANSPORT})

# DSH 用的同一套关键词：额度耗尽（终态）
_QUOTA_RES = (
    re.compile(r"\binsufficient[\s_-]+(?:quota|balance|credits?)\b", re.I),
    re.compile(r"\b(?:quota|usage[\s_-]+limit)[\s_-]+(?:exceeded|exhausted|reached)\b", re.I),
    re.compile(r"\bexceed(?:ed|s)?[\s_-]+(?:(?:your|the)[\s_-]+)?(?:current[\s_-]+)?quota\b", re.I),
    re.compile(r"\b(?:balance|credits?)[\s_-]+(?:exhausted|depleted)\b", re.I),
    re.compile(r"\bout[\s_-]+of[\s_-]+(?:credits?|budget)\b", re.I),
)

# 上下文窗口超了（可以压缩后重试）
_CONTEXT_RES = (
    re.compile(r"maximum context length", re.I),
    re.compile(r"context[\s_-]+(?:length|window)[\s_-]+(?:exceeded|too long)", re.I),
    re.compile(r"\b(?:too many tokens|token limit exceeded)\b", re.I),
    re.compile(r"\b(?:input|prompt|request)[\s_-]+(?:is\s+)?too[\s_-]+(?:long|large)\b", re.I),
    re.compile(r"exceeds?[\s_-]+(?:the[\s_-]+)?(?:model|maximum)[\s_-]+context", re.I),
)


class LLMError(Exception):
    """带分类的模型接口错误。message 直接可以展示给用户。"""

    def __init__(self, code: str, message: str, detail: str = "",
                 retry_after: float = 0.0, status: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.retry_after = retry_after
        self.status = status

    def __str__(self):
        return self.message


class ContextOverflow(LLMError):
    """上下文撑爆了：上层压缩历史后可以重试一次。"""


class QuotaExceeded(LLMError):
    """额度/余额没了（分钟配额或账户额度），重试没用。"""


def _body_of(exc):
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return body
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            data = response.json()
            return data if isinstance(data, dict) else {}
        except Exception:                    # noqa: BLE001
            return {}
    return {}


def _detail_of(exc, body):
    parts = []
    if body:
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        for key in ("message", "code", "type", "detail"):
            value = error.get(key)
            if value:
                parts.append(str(value))
    parts.append(str(exc))
    return " ".join(parts)


def _retry_after(body, exc):
    """服务端让等多久（秒），没有就 0。"""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    for key in ("retry-after", "Retry-After"):
        value = headers.get(key) if hasattr(headers, "get") else None
        if value:
            try:
                return max(0.0, float(str(value).strip()))
            except ValueError:
                pass
    for source in (body, body.get("error") if isinstance(body, dict) else None):
        if not isinstance(source, dict):
            continue
        for key in ("retry_after", "retryAfter"):
            value = source.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
    return 0.0


def _looks_like_quota(detail):
    return any(rx.search(detail) for rx in _QUOTA_RES)


def _looks_like_context_overflow(detail):
    return any(rx.search(detail) for rx in _CONTEXT_RES)


def friendly(code, detail, retry_after=0.0):
    """给用户看的话。别再把限流/额度说成「网络抖了一下」。"""
    short = " ".join(str(detail or "").split())[:220]
    wait = f"（服务端建议等 {retry_after:g} 秒）" if retry_after else ""
    if code == QUOTA:
        return ("模型额度不够了：这个 key 的配额/余额已用尽或被限流"
                f"{wait}。可以去模型控制台看下配额，或者过几分钟再试。"
                f"【上游原话】{short}")
    if code == RATE_LIMIT:
        return (f"模型接口限流了{wait}，自动重试也没成功。等一两分钟再试，"
                f"或者把这一次的请求缩小一点。【上游原话】{short}")
    if code == CONTEXT_OVERFLOW:
        return f"这次请求的上下文超过模型窗口了，会自动压缩后重试一次。【上游原话】{short}"
    if code == AUTH:
        return ("模型接口鉴权失败（key 不对 / 没权限 / 地址不匹配）。"
                f"去后台检查一下 key 和接口地址。【上游原话】{short}")
    if code == INVALID:
        return f"模型接口说这个请求参数不合法。【上游原话】{short}"
    if code == TIMEOUT:
        return ("模型接口超时了（好久没收到新数据）。再发一次试试；"
                f"如果反复出现，多半是上游在抽风。【上游原话】{short}")
    if code == TRANSPORT:
        return f"连不上模型接口（网络/代理问题）。【上游原话】{short}"
    return f"模型接口返回了错误。【上游原话】{short}"


def classify(exc):
    """把一个异常归类成 LLMError（已带用户友好的 message）。"""
    if isinstance(exc, LLMError):
        return exc
    body = _body_of(exc)
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None) or 0
    type_ = str(error.get("type") or "") if isinstance(error, dict) else ""
    code_ = str(error.get("code") or "") if isinstance(error, dict) else ""
    detail = _detail_of(exc, body)
    retry_after = _retry_after(body, exc)

    if status in (401, 403) or type_ in ("authentication_error", "permission_error"):
        code = AUTH
    elif _looks_like_quota(detail) or status == 402:
        # 注意：DSH 也是先判额度再判限流。阿里的 429 insufficient_quota 属于这一档，
        # 重试多少次都没用，直接告诉用户去查配额。
        code = QUOTA
    elif status == 429 or type_ == "rate_limit_error" or code_ in ("rate_limit", "rate_limit_error"):
        code = RATE_LIMIT
    elif _looks_like_context_overflow(detail):
        code = CONTEXT_OVERFLOW
    elif status in (400, 413) or type_ == "invalid_request_error":
        code = INVALID
    elif (status and status >= 500) or type_ in ("api_error", "overloaded_error"):
        code = SERVER
    elif isinstance(exc, TimeoutError):
        code = TIMEOUT
    elif status == 0:
        # 没有 HTTP 状态：连接层的问题当成 TRANSPORT
        name = type(exc).__name__
        code = TIMEOUT if "Timeout" in name else TRANSPORT
    else:
        code = UNKNOWN

    message = friendly(code, detail, retry_after)
    if code == CONTEXT_OVERFLOW:
        return ContextOverflow(code, message, detail, retry_after, int(status or 0))
    if code == QUOTA:
        return QuotaExceeded(code, message, detail, retry_after, int(status or 0))
    return LLMError(code, message, detail, retry_after, int(status or 0))
