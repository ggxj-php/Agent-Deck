"""把 Agent Skills 暴露成工具：列技能、读 SKILL.md、看技能里的文件。"""
import json

from .. import skills


def list_skills() -> str:
    """列出已安装且启用的技能（名字 + 一句话说明）。"""
    items = skills.enabled_skills()
    hidden = len(skills.discover()) - len(items)
    payload = {"skills": [{"name": i["name"], "description": i["description"]} for i in items],
               "total": len(items)}
    if not items:
        payload["note"] = "服务器上还没有启用的技能（管理员可在后台安装/启用）"
    elif hidden:
        payload["note"] = f"另有 {hidden} 个技能被后台关掉了"
    return json.dumps(payload, ensure_ascii=False)


def _ensure_enabled(name):
    if str(name) in skills.disabled_names():
        raise ValueError(f"技能 {name} 被后台关掉了，先让管理员打开")


def load_skill(name: str) -> str:
    """把某个技能的 SKILL.md 全文读进上下文（按里面的步骤执行）。"""
    try:
        _ensure_enabled(name)
        _name, text, _path = skills.load(name)
    except ValueError as exc:
        return f"错误：{exc}"
    return text


def list_skill_files(name: str, subdir: str = "") -> str:
    """列出技能目录里的文件；subdir 传了就看子目录。"""
    try:
        _ensure_enabled(name)
        items = skills.files(name, subdir)
    except ValueError as exc:
        return f"错误：{exc}"
    return json.dumps({"skill": name, "subdir": subdir or ".", "files": items},
                      ensure_ascii=False)


def read_skill_file(name: str, path: str) -> str:
    """读技能目录里的某个文件（workflow 文档、脚本、模板说明等）。"""
    try:
        _ensure_enabled(name)
        return skills.read_file(name, path)
    except ValueError as exc:
        return f"错误：{exc}"


SPECS = [
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": (
                "列出服务器上装好的技能包（Agent Skills）。用户要做 PPT、做海报这类"
                "有专门技能包的任务时，先看一眼有没有对应技能。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": (
                "加载某个技能的 SKILL.md（工作流说明）。加载后严格按里面的步骤做，"
                "需要看细节就用 read_skill_file 读它引用的文档。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "技能名，比如 ppt-master"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skill_files",
            "description": "列出某个技能目录里的文件（相对路径），用来找脚本、模板、流程文档。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名"},
                    "subdir": {"type": "string", "description": "可选：只看某个子目录"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_skill_file",
            "description": "读技能目录里的某个文件（流程文档 / 脚本 / 模板说明）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名"},
                    "path": {"type": "string", "description": "相对技能目录的路径"},
                },
                "required": ["name", "path"],
            },
        },
    },
]
