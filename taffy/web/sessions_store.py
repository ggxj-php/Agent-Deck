"""会话存档：把一段聊天的上下文存成 txt，放在项目根目录的 sessions/ 下。

**存文件，不存内存**——服务器只有 2G，历史全堆在进程里迟早 OOM；存成 txt 还能直接
拿编辑器打开看。

一个存档其实是两份文件，同名不同后缀：
· `<uuid>.txt`  —— 给人看的（后台能读、能下）。
· `<uuid>.json` —— 给程序看的：原始 messages，导入时靠它把历史**原样接回来**
  （连当时用了多少上下文一起带回，见 load()）。
删存档两份一起删。

文件名就是主人自己起的 uuid，所以：
· uuid 有格式要求（字母数字加 . _ - ，3~64 位，不能以点开头）；
· 保存前先查重，重名直接拒绝，绝不覆盖已经存过的那份（重了就换个名字）。
"""
import json
import os
import re
import time

from ..config import SESSIONS_DIR

# 只收这种 uuid：字母开头、只含字母数字和 . _ -，3~64 位。别的一律拒绝——
# 它会长成磁盘上的文件名，宽一点就是路径穿越的口子。
_UUID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")

MAX_READ_CHARS = 400 * 1024   # 单个存档最多读这么多字符给后台看，别拿大文件把内存顶了

os.makedirs(SESSIONS_DIR, exist_ok=True)


def check_uuid(value):
    """校验 uuid，返回规整后的值；不合法抛 ValueError。"""
    value = (value or "").strip()
    if not value:
        raise ValueError("会话 uuid 不能空着喵")
    if ".." in value or not _UUID_OK.match(value):
        raise ValueError("uuid 只能用字母、数字和 . _ -（3~64 位，字母或数字开头）喵")
    return value


def path_of(value):
    return os.path.join(SESSIONS_DIR, check_uuid(value) + ".txt")


def machine_path(value):
    """机器可读的那份（原始 messages，导入用）。"""
    return os.path.join(SESSIONS_DIR, check_uuid(value) + ".json")


def exists(value):
    try:
        return os.path.isfile(path_of(value))
    except ValueError:
        return False


def _content_of(message):
    """把一条消息的 content 拍成纯文本。带图那轮 content 是个列表，这里只留一句说明。"""
    content = message.get("content")
    if isinstance(content, list):
        texts = [part.get("text", "") for part in content
                 if isinstance(part, dict) and part.get("type") == "text"]
        has_image = any(isinstance(part, dict) and part.get("type") == "image_url"
                        for part in content)
        text = "\n".join(t for t in texts if t)
        return (text + "\n[图片]" if has_image else text).strip()
    return (content or "").strip()


def render(messages, uuid):
    """把对话历史拍成给人看的 txt。系统提示词不写进去（那是人设，不是聊天内容）。"""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    lines = [
        "# 永雏塔菲 · 会话存档",
        f"# 会话 uuid：{uuid}",
        f"# 保存时间：{stamp}（服务器本地时间）",
        f"# 共 {len(messages)} 条记录（含工具调用与结果）",
        "",
    ]
    index = 0
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue          # 人设提示词不进去，存档只留对话本身
        index += 1
        if role == "user":
            who = "你"
        elif role == "assistant":
            who = "塔菲"
        else:
            who = "工具结果"
        body = _content_of(message)
        calls = message.get("tool_calls") or []
        if calls:
            names = [c.get("function", {}).get("name", "?") for c in calls]
            note = "（调用了工具：" + "、".join(names) + "）"
            body = (body + "\n" + note).strip() if body else note
        if not body:
            body = "（这一条没有正文）"
        lines += ["-" * 60, f"[{index}] {who}", "", body, ""]
    return "\n".join(lines)


def _write(path, text):
    """先写临时文件再改名：中途出错也不会留下半截存档。"""
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fp:
        fp.write(text)
    os.replace(tmp, path)


def _plain(message):
    """把一条消息收拾成能安全塞进 json 的样子。

    图片是 data URL（base64），又大又没意义（下一轮本来就会被抹掉），这里换成一句
    「[图片]」占位；其余字段原样留着（tool_calls / tool_call_id 的配对不能动，
    动了导入回去下一轮就会因为「工具调用对不上」直接报错）。
    """
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if role not in ("user", "assistant", "tool"):
        return None
    out = {"role": role}
    content = message.get("content")
    if isinstance(content, list):
        texts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and part.get("text"):
                texts.append(part["text"])
            elif part.get("type") == "image_url":
                texts.append("[图片]")
        out["content"] = "\n".join(texts)
    else:
        out["content"] = content or ""
    for key in ("tool_calls", "tool_call_id", "name"):
        if message.get(key):
            out[key] = message[key]
    return out


def save(uuid, messages, context=None):
    """存一份存档。重名抛 FileExistsError，让调用方给主人一句人话。

    context 是存的时候这段历史占了多少（taffy/context.py 估算的），一并记进 json，
    导入时能告诉主人「接回来的这份大概占几成」。
    """
    uuid = check_uuid(uuid)
    target = path_of(uuid)
    if os.path.exists(target) or os.path.exists(machine_path(uuid)):
        raise FileExistsError(uuid)
    text = render(messages, uuid)
    _write(target, text)

    # 机器可读的那份：导入时按它还原文史。system prompt 不存（导入时用当前那份人设）
    data = {
        "uuid": uuid,
        "saved_at": int(time.time()),
        "messages": [item for item in (_plain(m) for m in messages) if item],
        "context": context or {},
    }
    _write(machine_path(uuid), json.dumps(data, ensure_ascii=False))
    return {
        "uuid": uuid,
        "file": os.path.basename(target),
        "messages": len(messages),
        "bytes": len(text.encode("utf-8")),
    }


def load(uuid):
    """读一份存档的原始历史，返回 (messages, 存档时记下的 context)。

    没有 .json（老版本存的、或者只有 txt）就报错，让主人重新存一份。
    """
    path = machine_path(uuid)
    if not os.path.isfile(path):
        raise FileNotFoundError(uuid)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except ValueError as exc:
        raise ValueError(f"这份存档的 json 读不出来（{exc}），可能存坏了") from exc
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list) or not messages:
        raise ValueError("这份存档里没有能导回来的对话内容喵")
    messages = [m for m in messages if isinstance(m, dict) and m.get("role")]
    # 从第一条用户消息开始：前面可能是半截的工具结果，接回去会让模型当场报格式错
    for index, message in enumerate(messages):
        if message.get("role") == "user":
            messages = messages[index:]
            break
    else:
        raise ValueError("这份存档里没有用户说的话，导回来也没用喵")
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    return messages, context


def display(messages):
    """给网页聊天区看的精简版：只留用户说的和塔菲的正文，工具那些不摆出来。"""
    items = []
    for message in messages:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _content_of(message)
        if text:
            items.append({"role": role, "text": text})
    return items


def _preview(text):
    """预览：第一句「你」说的话，截一小段，列表里能认出是哪个会话就行。"""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("[") and line.endswith("] 你"):
            for follow in lines[i + 1:]:
                follow = follow.strip()
                if follow and not follow.startswith("-"):
                    return follow[:60]
            break
    return "（没有用户发言）"


def list_all():
    """列出所有存档（新存的在前）。只读每个文件开头一点，不整个读进来。"""
    items = []
    try:
        names = os.listdir(SESSIONS_DIR)
    except OSError:
        return items
    for name in names:
        if not name.endswith(".txt"):
            continue
        full = os.path.join(SESSIONS_DIR, name)
        if not os.path.isfile(full):
            continue
        try:
            stat = os.stat(full)
            with open(full, "r", encoding="utf-8", errors="replace") as fp:
                head = fp.read(4096)
        except OSError:
            continue
        items.append({
            "uuid": name[:-4],
            "file": name,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "preview": _preview(head),
            # 有 .json 才能导回聊天页；老版本存的只有 txt，只能看不能导
            "importable": os.path.isfile(os.path.join(SESSIONS_DIR, name[:-4] + ".json")),
        })
    items.sort(key=lambda it: it["mtime"], reverse=True)
    return items


def read(uuid, limit=MAX_READ_CHARS):
    """读一份存档的正文。超大文件截断，返回 (文本, 是否被截断)。"""
    full = path_of(uuid)
    if not os.path.isfile(full):
        raise FileNotFoundError(uuid)
    with open(full, "r", encoding="utf-8", errors="replace") as fp:
        text = fp.read(limit + 1)
    if len(text) > limit:
        return text[:limit], True
    return text, False


def remove(uuid):
    """删存档：.txt 和 .json 两份一起删。"""
    full = path_of(uuid)
    if not os.path.isfile(full):
        raise FileNotFoundError(uuid)
    os.remove(full)
    try:
        os.remove(machine_path(uuid))
    except OSError:
        pass
    return os.path.basename(full)