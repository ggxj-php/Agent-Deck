"""基础工具：时间。"""
import datetime

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "获取现在时间",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


def get_time() -> str:
    """返回当前时间"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
