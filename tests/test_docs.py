"""文档工具里的纯逻辑：页码解析。"""
from deck.tools import docs


def test_页码范围():
    assert docs._parse_pages("1-3", 10) == [1, 2, 3]
    assert docs._parse_pages("2,5", 10) == [2, 5]
    assert docs._parse_pages("2-4,9", 10) == [2, 3, 4, 9]


def test_默认只读前几页():
    assert docs._parse_pages("", 100) == list(range(1, docs.DEFAULT_PAGE_CAP + 1))
    assert docs._parse_pages("", 3) == [1, 2, 3]


def test_非法输入不炸():
    assert docs._parse_pages("abc", 10) == []
    assert docs._parse_pages("99", 10) == []
    assert docs._parse_pages("1-", 10) == []
    assert docs._parse_pages("", 0) == []
