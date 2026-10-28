"""count_text：各类字符统计对不对。"""
import json

from deck.tools.textstat import count_text


def test_中英混排统计():
    out = json.loads(count_text(text="你好，World! 2024 年"))
    assert out["汉字"] == 3          # 你好 + 年 = 3
    assert out["英文字母"] == 5
    assert out["数字"] == 4
    assert out["中文标点"] == 1
    assert out["英文标点/符号"] == 1
    assert out["字数（中文习惯：汉字+英文单词+数字串）"] == out["汉字"] + 1 + 1


def test_纯中文句子():
    out = json.loads(count_text(text="今天天气很好。"))
    assert out["汉字"] == 6
    assert out["句数"] == 1
    assert out["段落数"] == 1


def test_空输入报错():
    assert "error" in json.loads(count_text(text=""))
