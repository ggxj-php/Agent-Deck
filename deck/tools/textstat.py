"""字数统计：用户要求「写 800 字」这类需求时，模型可以用它精确核对，别靠感觉估。"""
import json
import os
import re

from ..sandbox import safe_path

MAX_CHARS = 200_000

_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002ffff]")
_LATIN = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"[0-9]")
_EN_WORD = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")
_DIGIT_RUN = re.compile(r"[0-9]+(?:[.,][0-9]+)*")
_CN_PUNCT = re.compile(r"[，。！？；：、（）【】《》「」“”‘’—…·～]")
_ASCII_PUNCT = re.compile(r"[!-/:-@\[-`{-~]")


def _stats(text):
    han = len(_HAN.findall(text))
    latin = len(_LATIN.findall(text))
    digits = len(_DIGIT.findall(text))
    spaces = text.count(" ") + text.count("\t")
    newlines = text.count("\n")
    cn_punct = len(_CN_PUNCT.findall(text))
    ascii_punct = len(_ASCII_PUNCT.findall(text))
    en_words = len(_EN_WORD.findall(text))
    digit_runs = len(_DIGIT_RUN.findall(text))
    other = max(0, len(text) - han - latin - digits - spaces - newlines - cn_punct - ascii_punct)
    paragraphs = len([p for p in re.split(r"\n\s*\n", text) if p.strip()]) if text.strip() else 0
    sentences = len([s for s in re.split(r"[。！？!?]+|\.(?=\s|$)", text) if s.strip()])
    return {
        "总字符数": len(text),
        "汉字": han,
        "英文字母": latin,
        "数字": digits,
        "空格/制表符": spaces,
        "换行": newlines,
        "中文标点": cn_punct,
        "英文标点/符号": ascii_punct,
        "其他字符": other,
        "英文单词": en_words,
        "数字串": digit_runs,
        "段落数": paragraphs,
        "句数": sentences,
        "字数（中文习惯：汉字+英文单词+数字串）": han + en_words + digit_runs,
        "字数（不含空白和标点）": han + latin + digits,
    }


def count_text(text: str = "", path: str = "") -> str:
    """统计一段文字或 workspace 里某个文件的字数。"""
    content = ""
    if str(path or "").strip():
        try:
            full = safe_path(str(path).strip())
        except Exception as exc:              # noqa: BLE001
            return json.dumps({"error": f"路径不合法：{exc}"}, ensure_ascii=False)
        if not os.path.isfile(full):
            return json.dumps({"error": f"文件不存在：{path}"}, ensure_ascii=False)
        if os.path.getsize(full) > 5 * 1024 * 1024:
            return json.dumps({"error": "文件超过 5MB，先截一段再统计"}, ensure_ascii=False)
        try:
            with open(full, encoding="utf-8", errors="replace") as fp:
                content = fp.read(MAX_CHARS)
        except OSError as exc:
            return json.dumps({"error": f"读文件失败：{exc}"}, ensure_ascii=False)
    else:
        content = str(text or "")[:MAX_CHARS]

    if not content:
        return json.dumps({"error": "没收到文字：要么传 text，要么传 workspace 里的 path"},
                          ensure_ascii=False)
    stats = _stats(content)
    stats["提示"] = ("「字数」按中文写作习惯算（汉字每个算 1 字、英文单词算 1 词、数字串算 1 个）；"
                   "不同平台口径可能不同，用户没特别说明就按这个口径报。")
    return json.dumps(stats, ensure_ascii=False, indent=2)


SPECS = [
    {
        "type": "function",
        "function": {
            "name": "count_text",
            "description": (
                "统计字数：总字符、汉字、英文字母、数字、标点符号、空格、段落、句数，"
                "以及按中文习惯算的「字数」（汉字+英文单词+数字串）。"
                "用户要求「写多少字」时，写完后调用它核对；也可以传 path 统计 workspace 里的文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要统计的文字（和 path 二选一）"},
                    "path": {"type": "string",
                             "description": "workspace 里的文件相对路径（和 text 二选一）"},
                },
            },
        },
    },
]
