"""技能预路由：在进主模型前，按强触发词自动加载已启用技能。

只做高置信度、强关键词匹配，不猜模糊任务；活动图/用例图当前没有对应技能，
所以这里不会触发，模型仍按普通流程处理。
"""
from . import skills

# 只放强触发词，避免“表格”“图”这种泛词误加载。
SKILL_TRIGGERS = {
    "ppt-master": ("ppt", "幻灯片", "演示文稿", "课件", "路演", "汇报材料"),
    "frontend-design": ("海报", "落地页", "宣传页", "界面设计", "网页设计", "前端设计"),
    "docx": ("word文档", "word 文档", "docx", "文档排版"),
    "xlsx": ("excel", "xlsx", "电子表格"),
    "pdf": ("pdf", "合并pdf", "拆分pdf"),
    "archify": (
        "架构图", "流程图", "时序图", "部署图", "状态图", "状态机",
        "er图", "erd", "类图", "数据流图", "dfd", "技术架构", "系统架构",
    ),
    "humanizer-zh": ("去ai味", "去 ai 味", "润色", "改写", "人味儿", "太ai"),
    "renwei-writing": ("人味儿写作", "更像人写"),
    "humanizer": ("humanize", "de-ai"),
}

MAX_SKILL_CHARS = 14000


def _enabled_names():
    return {item["name"] for item in skills.enabled_skills()}


def match(text):
    """返回 (技能名, 命中词, 命中数)；没有匹配返回 None。"""
    text = str(text or "").casefold()
    if not text:
        return None
    enabled = _enabled_names()
    best = None
    for name, words in SKILL_TRIGGERS.items():
        if name not in enabled:
            continue
        hits = [w for w in words if w.casefold() in text]
        if not hits:
            continue
        score = len(hits) * 10 + max(len(w) for w in hits)
        if best is None or score > best[0]:
            best = (score, name, hits)
    if best is None:
        return None
    return best[1], best[2], len(best[2])


def route(user_input):
    """给 core 用：命中就加载技能正文，返回 {name, text, hits}；否则 None。"""
    if "别用技能" in str(user_input or ""):
        return None
    matched = match(user_input)
    if not matched:
        return None
    name, hits, _count = matched
    try:
        _name, text, _path = skills.load(name)
    except Exception as exc:  # noqa: BLE001
        print(f"[skill-router] 加载 {name} 失败：{exc}", flush=True)
        return None
    text = text or ""
    clipped = len(text) > MAX_SKILL_CHARS
    if clipped:
        text = text[:MAX_SKILL_CHARS]
    print(f"[skill-router] matched={name} hits={hits} clipped={clipped}", flush=True)
    return {"name": name, "text": text, "hits": hits, "clipped": clipped}
