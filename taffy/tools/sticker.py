"""表情包工具：让塔菲发一张自己的表情包。

真正把图发出去的是 core.py —— 它看到 send_sticker 就单独发一个 sticker 事件，
这里只负责校验 mood、给模型一句回执。可选表情对应 web/static/stickers/ 下的文件名。
"""

MOODS = ("happy", "think", "confused", "proud", "cry", "angry", "sleepy", "love")

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "send_sticker",
            "description": (
                "给雏草姬发一张塔菲表情包（真实图片，会直接显示在聊天里）。"
                "每条回复都至少要发一张，在 happy / think / confused / proud / cry / "
                "angry / sleepy / love 里挑最贴合当下心情的；情绪变化大可以多发几张。"
                "只有这个工具发出来的是真表情包，emoji 和颜文字都不算，别拿那些凑数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mood": {
                        "type": "string",
                        "enum": list(MOODS),
                        "description": "选一张最贴合当下心情的表情包",
                    },
                },
                "required": ["mood"],
            },
        },
    },
]


def send_sticker(mood: str) -> str:
    """返回回执文本，图片由 core.py 通过 sticker 事件发出去"""
    if mood not in MOODS:
        return f"没有 {mood} 这张表情包，可选：{'、'.join(MOODS)}"
    return f"已发送 {mood} 表情包"
