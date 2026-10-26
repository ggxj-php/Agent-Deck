"""表情包工具：让塔菲发自己的表情包。

真正把图发出去的是 core.py —— 它看到 send_sticker 就逐个 mood 发 sticker 事件，
这里只负责校验 mood、给模型一句回执。可选表情对应 web/static/stickers/ 下的文件名。

**一次调用可以发多张**：moods 是数组，比如 ["happy", "love"] 就发两张。
必须一次发完——core.py 把「只调了 send_sticker 的那一轮」当成回复的收尾，
第一次调用就结束了，拆成多次调用根本没机会发第二张。
"""

import os
import random

MOODS = ("happy", "think", "confused", "proud", "cry", "angry", "sleepy", "love")


def pick_sticker(mood):
    """从当前身份卡的表情包里随机挑一张该心情的图，返回文件名（没有就空串）。

    为什么要挑死一张：以前前端每次渲染都随机挑，刷新一次换一张。
    现在发图的时候就把文件名定下来、写进正文标记（[sticker:心情:文件名]），
    刷新之后照着标记渲染，永远是同一张。
    """
    mood = str(mood or "").strip().lower()
    if mood not in MOODS:
        return ""
    try:
        from .. import persona

        folder = persona.stickers_dir()
        names = sorted(
            name for name in os.listdir(folder)
            if name.lower().startswith(mood) and os.path.isfile(os.path.join(folder, name))
        )
        if not names:
            return ""
        return random.choice(names)
    except Exception:                    # noqa: BLE001
        return ""

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "send_sticker",
            "description": (
                "备用手段：只在整条回复没有正文、或者正文里一个 [sticker:心情] 标记都没写时才用。"
                "正常请在正文里直接写 [sticker:心情] 标记，那样表情包能夹在文字中间，"
                "比这个工具自然得多（用这个工具的话图只能挂在回复最末尾，而且调用完立刻收尾）。"
                "一次调用可以发 1~3 张：把 mood 放进 moods 数组，例如 [\"happy\", \"love\"]。"
                "只有这个工具发出来的是真表情包，emoji 和颜文字都不算，别拿那些凑数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "moods": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(MOODS)},
                        "minItems": 1,
                        "maxItems": 3,
                        "description": "要发的表情包，按显示顺序排列；通常 1 张，情绪变化大最多 2~3 张",
                    },
                },
                "required": ["moods"],
            },
        },
    },
]


def send_sticker(moods) -> str:
    """返回回执文本，图片由 core.py 逐个通过 sticker 事件发出去"""
    # 模型有时会直接把字符串塞进来，兼容一下
    if isinstance(moods, str):
        moods = [moods]
    elif not isinstance(moods, (list, tuple)):
        moods = []

    good, bad = [], []
    for mood in moods:
        if not isinstance(mood, str):
            continue
        (good if mood in MOODS else bad).append(mood)

    if bad:
        msg = f"没有 {'、'.join(bad)} 这些表情包，可选：{'、'.join(MOODS)}。"
        msg += f"已发送：{'、'.join(good) if good else '无'}"
        return msg
    if not good:
        return f"没拿到要发的表情包（moods 要是数组），可选：{'、'.join(MOODS)}"
    return f"已发送 {'、'.join(good)} 表情包"
