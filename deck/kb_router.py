"""知识库预路由：先判断问题是否该查知识库，命中就直接检索并注入上下文。

目录和触发词都从 knowledge/ 实时扫描，新增文件不用改代码；模型只负责在
给定资料上回答，资料不够时再显式调用 search_knowledge。
"""
import os

from . import kb
from .config import KNOWLEDGE_DIR

# 强主题词：命中任一类就认为值得先查知识库。
DOMAIN_HINTS = {
    "算法/数据结构": (
        "算法", "数据结构", "复杂度", "排序", "二叉树", "链表", "图论",
        "动态规划", "贪心", "回溯", "并查集", "堆", "栈", "队列",
        "leetcode", "洛谷", "acm", "oi", "题解",
    ),
    "软件工程/UML": (
        "软件工程", "uml", "用例", "活动图", "需求分析", "设计模式",
        "类图", "时序图", "数据流图", "er图", "系统建模", "业务流程",
    ),
    "数字取证": (
        "取证", "哈希", "文件头", "内存取证", "手机取证", "电子数据",
        "数字取证", "carve", "extract_strings", "exif", "时间线",
    ),
    "嵌入式/计算机组成": (
        "嵌入式", "单片机", "stm32", "51单片机", "物联网", "计算机组成",
        "汇编", "寄存器", "中断", "adc", "pwm", "i2c", "uart", "串口",
    ),
    "情感心理学": ("心理学", "情感", "情绪", "情绪心理学"),
}

# 情感/心理类口语问题，通常不会出现“心理学”这个词；用于检索扩展。
EMOTION_HINTS = (
    "焦虑", "抑郁", "难过", "伤心", "失落", "孤独", "压力", "内耗",
    "喜欢", "爱上", "暗恋", "失恋", "分手", "感情", "爱情", "婚姻",
    "愤怒", "生气", "嫉妒", "害怕", "恐惧", "自卑", "讨好", "依恋",
    "情绪", "心情", "心理", "烦躁", "崩溃", "委屈",
)
PSYCH_QUERY = " psychology emotion feeling"

# 明确问“知识库/教材/书”时，不论主题都查。
EXPLICIT_HINTS = ("知识库", "教材", "课本", "书里", "第几章", "这本书", "资料库", "书库")

# 带这些问法时，即使主题词没命中，也先试着检索；新加的文件可能没写进关键词。
QUESTION_HINTS = (
    "是什么", "为什么", "怎么", "如何", "原理", "区别", "作用", "优缺点",
    "定义", "介绍", "解释", "哪个", "多少", "是否", "能否", "举例",
)

# 这些是“做东西”的任务，优先交给技能/画图工具，不自动灌知识库资料。
TASK_HINTS = (
    "ppt", "幻灯片", "海报", "落地页", "宣传页", "画个", "画一张",
    "画图", "渲染", "word", "excel", "xlsx", "docx", "pdf",
    "写代码", "写个脚本", "实现一个", "做个网站", "做个页面",
)

_STOP_TERMS = {
    "chapter", "readme", "index", "intro", "introduction", "preface",
    "appendix", "reference", "notes", "note", "data", "test", "main",
}


def _scan_knowledge():
    """实时扫 knowledge/：返回 (目录名列表, 文件名列表)。"""
    dirs = []
    files = []
    try:
        with os.scandir(KNOWLEDGE_DIR) as it:
            for entry in it:
                if entry.is_dir():
                    dirs.append(entry.name)
                    try:
                        with os.scandir(entry.path) as sub:
                            files.extend(os.path.splitext(e.name)[0] for e in sub
                                         if e.is_file())
                    except OSError:
                        pass
                elif entry.is_file():
                    files.append(os.path.splitext(entry.name)[0])
    except OSError:
        pass
    return sorted(dirs), sorted(files)


def _dynamic_terms(dirs, files):
    terms = set()
    for name in list(dirs) + list(files):
        low = str(name or "").casefold().strip()
        if len(low) < 2 or low in _STOP_TERMS:
            continue
        for part in low.replace("-", " ").replace("_", " ").replace(".", " ").split():
            if len(part) >= 3 and part.isascii() and part not in _STOP_TERMS:
                terms.add(part)
        # 中文文件名整段保留，避免把“情感心理学”拆碎
        if any("\u4e00" <= ch <= "\u9fff" for ch in low) and len(low) >= 2:
            terms.add(low)
    return terms


def _catalog(dirs, files):
    known = {
        "hello-algo": "算法/数据结构",
        "leetcode": "算法题解",
        "luogu": "洛谷题解",
        "oi-wiki": "OI/算法竞赛",
        "books": "教材/书籍",
    }
    lines = ["知识库可用范围（自动扫描）："]
    grouped = []
    for d in dirs:
        if d in known:
            grouped.append(f"{known[d]}（{d}）")
    if grouped:
        lines.append("- " + "、".join(grouped))
    book_dir = "books"
    if os.path.isdir(os.path.join(KNOWLEDGE_DIR, book_dir)):
        try:
            with os.scandir(os.path.join(KNOWLEDGE_DIR, book_dir)) as it:
                titles = [os.path.splitext(e.name)[0] for e in it if e.is_file()]
        except OSError:
            titles = []
        if titles:
            shown = "、".join(titles[:10])
            more = f" 等 {len(titles)} 本" if len(titles) > 10 else ""
            lines.append(f"- 书籍：{shown}{more}")
    extra = [d for d in dirs if d not in known]
    if extra:
        lines.append("- 其他目录：" + "、".join(extra))
    lines.append("涉及这些主题时优先使用知识库原文，不要凭记忆编造。")
    return "\n".join(lines)


def catalog_text():
    """紧凑目录，让模型知道知识库里有什么；随文件实时变化。"""
    if not os.path.isdir(KNOWLEDGE_DIR):
        return ""
    dirs, files = _scan_knowledge()
    return _catalog(dirs, files)


def should_retrieve(text):
    text = str(text or "").strip()
    if len(text) < 2:
        return False
    low = text.casefold()
    if any(word.casefold() in low for word in EXPLICIT_HINTS):
        return True
    if any(word.casefold() in low for words in DOMAIN_HINTS.values() for word in words):
        return True
    dirs, files = _scan_knowledge()
    if any(term in low for term in _dynamic_terms(dirs, files)):
        return True
    return any(word in low for word in QUESTION_HINTS) and len(low) >= 6


def _search(text, top_k=4):
    try:
        return str(kb.search(text, top_k=top_k) or "").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[kb-router] search failed: {type(exc).__name__}: {exc}", flush=True)
        return ""


def is_substantive(text):
    """是否值得先查一次知识库：排除短句/寒暄/纯任务。"""
    text = str(text or "").strip()
    low = text.casefold()
    if len(low) < 4:
        return False
    if low in {"你好", "您好", "谢谢", "好的", "在吗", "继续", "嗯", "哦", "哈", "ok"}:
        return False
    if should_retrieve(text):
        return True
    if any(word in low for word in TASK_HINTS):
        return False
    return len(low) >= 6


def prefetch_context(text):
    """返回要注入的知识库上下文；不相关返回目录或空。"""
    text = str(text or "").strip()
    catalog = catalog_text()
    if not text:
        return catalog
    if not is_substantive(text):
        return catalog
    search_text = text
    low = text.casefold()
    if any(word in low for word in EMOTION_HINTS):
        search_text = text + PSYCH_QUERY
    result = _search(search_text, top_k=4)
    if not result or result.startswith("知识库索引正在") or result.startswith("知识库里没找到"):
        return catalog
    print(f"[kb-router] auto retrieved {len(result)} chars", flush=True)
    return (
        f"{catalog}\n\n【服务端知识库预检索】已针对本轮问题自动检索知识库。"
        "以下是检索到的片段，可能不完全相关：先判断是否真能回答本轮问题；"
        "相关就用并注明出处，不相关就忽略，不要硬套。资料不够再补 search_knowledge。\n\n" + result
    )


def _is_high_confidence(text):
    low = text.casefold()
    if any(word.casefold() in low for word in EXPLICIT_HINTS):
        return True
    if any(word.casefold() in low for words in DOMAIN_HINTS.values() for word in words):
        return True
    dirs, files = _scan_knowledge()
    if any(term in low for term in _dynamic_terms(dirs, files)):
        return True
    return any(word in low for word in EMOTION_HINTS)


def route(text):
    """混合路由结果：命中时返回 level/hint；不命中返回 None。"""
    text = str(text or "").strip()
    if not is_substantive(text):
        return None
    high = _is_high_confidence(text)
    catalog = catalog_text()
    if high:
        hint = (f"{catalog}\n\n【知识库路由】检测到本轮问题很可能命中知识库。"
                "请先调用 search_knowledge 检索；关键词不合适就换词，不要直接凭记忆回答。"
                "资料不相关可以忽略，但必须先查。")
    else:
        hint = (f"{catalog}\n\n【知识库路由】本轮问题可能需要知识库资料。"
                "如果问题涉及知识库里的主题，请先调用 search_knowledge；"
                "不需要就正常回答。")
    print(f"[kb-router] route level={'high' if high else 'medium'}", flush=True)
    return {"level": "high" if high else "medium", "hint": hint}
