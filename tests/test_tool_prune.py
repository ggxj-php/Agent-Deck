"""工具结果修剪：超长只留头+尾，短的别动。"""
from deck import config
from deck.core import _prune_tool_result


def test_短结果原样返回():
    text = "很短的结果"
    assert _prune_tool_result(text) == text


def test_长结果保留头尾并标出省略量():
    text = "头" * config.TOOL_RESULT_PRUNE_HEAD + "中间内容" * 2000 + "尾" * config.TOOL_RESULT_PRUNE_TAIL
    out = _prune_tool_result(text)
    assert len(out) < len(text)
    assert out.startswith("头" * 100)
    assert out.endswith("尾" * 100)
    assert "省略" in out
    assert "重新读" in out
