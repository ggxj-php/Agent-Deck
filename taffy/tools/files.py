"""文件工具：只允许操作 workspace 沙箱内的文件。"""
import os
import shutil

from ..sandbox import WORKSPACE, safe_path

MAX_READ_BYTES = 512 * 1024   # 一次最多读这么多，别让一个超大文件把内存 / 上下文撑爆

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出工作区 workspace 中某个目录下的文件，默认列出根目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对 workspace 的目录路径，默认 .",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作区 workspace 内的文本文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对 workspace 的文件路径，例如 notes/todo.txt",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "把内容写入工作区 workspace 内的文件，会覆盖原内容，父目录自动创建",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对 workspace 的文件路径，例如 notes/todo.txt",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整文本内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "删除工作区 workspace 内的文件或目录。删目录必须显式传 recursive=true。只能删 workspace 里的东西，根目录删不掉。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对 workspace 的路径，例如 temp_test.py",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "要删的是目录时必须传 true，删文件不用",
                    },
                },
                "required": ["path"],
            },
        },
    },
]


def list_files(path: str = ".") -> str:
    """列出工作区某个目录下的内容"""
    full = safe_path(path)
    if not os.path.isdir(full):
        return f"错误：{path} 不是目录"
    names = []
    for name in sorted(os.listdir(full)):
        names.append(name + os.sep if os.path.isdir(os.path.join(full, name)) else name)
    return "（空目录）" if not names else "\n".join(names)


def read_file(path: str) -> str:
    """读取工作区内的文本文件（有大小上限，超了只给前半截）"""
    full = safe_path(path)
    if not os.path.isfile(full):
        return f"错误：文件不存在 {path}"
    size = os.path.getsize(full)
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        text = f.read(MAX_READ_BYTES)
    if size > MAX_READ_BYTES:
        text += (f"\n…（文件 {size} 字节，超过 {MAX_READ_BYTES} 字节上限，只给了前半截；"
                 f"要没被截的整份内容请想办法分段读）")
    return text


def write_file(path: str, content: str) -> str:
    """写入（覆盖）工作区内的文本文件，父目录会自动创建"""
    full = safe_path(path)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {path}（{len(content)} 个字符）"


def delete_file(path: str, recursive: bool = False) -> str:
    """删除工作区内的文件或目录；删目录要显式 recursive=True"""
    full = safe_path(path)
    if os.path.normcase(full) == os.path.normcase(WORKSPACE):
        return "错误：不能删除 workspace 根目录"
    if os.path.isfile(full):
        os.remove(full)
        return f"已删除文件 {path}"
    if os.path.isdir(full):
        if not recursive:
            return f"错误：{path} 是目录，要删目录请传 recursive=True"
        shutil.rmtree(full)
        return f"已删除目录 {path}"
    return f"错误：不存在 {path}"
