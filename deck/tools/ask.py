"""ask_user：遇到需要用户拍板的地方，弹一个选择框给用户，而不是替用户瞎猜。"""
import json


def ask_user(question: str, options=None, allow_custom: bool = True) -> str:
    """把问题发给用户，等用户在界面上选。真正的弹框由 core 发 await_user 事件。"""
    opts = [str(o).strip() for o in (options or []) if str(o).strip()]
    if not str(question or "").strip():
        return "错误：question 不能为空"
    return json.dumps({
        "ok": True,
        "queued": True,
        "question": question,
        "options": opts,
        "note": "问题已经弹给用户了，这一轮先停在这里，等用户选完再继续。",
    }, ensure_ascii=False)


SPECS = [
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "让用户做选择/确认时用：弹出一个带选项的选择框，用户点完自动继续。"
                "用于风格、页数、模板、是否覆盖文件这类需要用户拍板的地方；"
                "能从上下文合理推断时不要滥用它。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "要问用户的问题（一两句话）"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "选项列表（2~6 个），用户点一下就继续",
                    },
                    "allow_custom": {
                        "type": "boolean",
                        "description": "是否允许用户自己输入（默认 true）",
                    },
                },
                "required": ["question"],
            },
        },
    },
]
