"""接口报错分类（照 DSH 的思路）：额度是终态、限流可重试、上下文溢出要压缩。"""
import pytest

from deck import llm_errors


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}

    def json(self):
        return self._body


class FakeAPIError(Exception):
    def __init__(self, status=500, body=None, headers=None, text="boom"):
        super().__init__(text)
        self.status_code = status
        self.body = body or {}
        self.response = FakeResponse(status, self.body, headers)


def test_阿里额度错误判成QUOTA():
    # 用户实际遇到的那条（429 但语义是额度没了，不能当限流重试）
    exc = FakeAPIError(429, {"error": {
        "message": "Allocated quota exceeded, please increase your quota limit. "
                   "For details, see: https://www.alibabacloud.com/help/en/model-studio/error-code#token-limit",
        "type": "insufficient_quota", "code": "insufficient_quota"}})
    err = llm_errors.classify(exc)
    assert err.code == llm_errors.QUOTA
    assert isinstance(err, llm_errors.QuotaExceeded)
    assert err.code not in llm_errors.RETRYABLE
    assert "额度" in err.message


def test_纯限流判成RATE_LIMIT且可重试():
    exc = FakeAPIError(429, {"error": {"message": "rate limit exceeded",
                                       "type": "rate_limit_error"}})
    err = llm_errors.classify(exc)
    assert err.code == llm_errors.RATE_LIMIT
    assert err.code in llm_errors.RETRYABLE


def test_上下文溢出可识别():
    exc = FakeAPIError(400, {"error": {"message":
        "This model's maximum context length is 65536 tokens. However you requested 70000 tokens."},
        "type": "invalid_request_error"})
    err = llm_errors.classify(exc)
    assert err.code == llm_errors.CONTEXT_OVERFLOW
    assert isinstance(err, llm_errors.ContextOverflow)


def test_鉴权和服务端错误():
    assert llm_errors.classify(FakeAPIError(401, {"error": {"message": "bad key"}})).code == llm_errors.AUTH
    assert llm_errors.classify(FakeAPIError(503, {"error": {"message": "overloaded"}})).code == llm_errors.SERVER


def test_retry_after_取自响应头():
    exc = FakeAPIError(429, {"error": {"message": "rate limit", "type": "rate_limit_error"}},
                       headers={"retry-after": "7"})
    assert llm_errors.classify(exc).retry_after == pytest.approx(7.0)


def test_没有状态码当成传输问题():
    class ConnError(Exception):
        pass
    err = llm_errors.classify(ConnError("connection reset"))
    assert err.code in (llm_errors.TRANSPORT, llm_errors.TIMEOUT)
