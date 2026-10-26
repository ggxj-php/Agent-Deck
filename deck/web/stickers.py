"""表情包库：列出 / 上传单张 / 批量导入 zip / 删除。

**文件命名规则**（后台页面和 README 里都写着同一份）：
    心情 + 序号 + 后缀，例如 happy1.png、love_02.gif、think-3.webp
去掉后缀以后，开头那串字母就是心情，后面的数字随便写（只影响显示顺序）。
心情只能是这八个之一：happy / think / confused / proud / cry / angry / sleepy / love。
认不出心情的文件会被列在「没归类的」里，不会硬塞进某个心情。

图片就放在 deck/web/static/stickers/，跟网页静态目录在一起，直接由 /static 提供，
所以网页那边只要换个文件名就能显示——png / jpg / gif / webp 都行，gif 会自己动。

内存：上传是流式落盘的（见 admin.py），压缩包也是一个个读、一个个写，不会把整个包
读进内存。这台服务器只有 2G，这些地方都得抠着来。
"""
import os
import re
import zipfile

from .. import persona
from ..tools.sticker import MOODS

# 允许的图片后缀。gif 放进来是为了动图——前端用 <img> 直接显示，浏览器自己会播。
EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

MAX_FILE_BYTES = 8 * 1024 * 1024          # 单张上限
MAX_ZIP_BYTES = 80 * 1024 * 1024          # 压缩包上限
MAX_IMPORT_FILES = 500                    # 一次最多导入几张
MAX_IMPORT_BYTES = 200 * 1024 * 1024      # 解压后总量上限（挡 zip 炸弹）

# 去掉后缀后开头的字母串就是心情：happy1 / happy_02 / happy-x 都算 happy
_LEADING = re.compile(r"^([a-z]+)")

def _dir():
    """当前人设的表情包目录（每次现算，后台切人设立刻生效）。"""
    path = persona.stickers_dir()
    os.makedirs(path, exist_ok=True)
    return path


def mood_of(name):
    """从文件名里读出心情，读不出来返回空字符串。"""
    stem = os.path.splitext(os.path.basename(name))[0].lower()
    match = _LEADING.match(stem)
    return match.group(1) if match and match.group(1) in MOODS else ""


def check_name(name):
    """校验一个要存进来的文件名，返回 (心情, 后缀)。不合法就抛 ValueError。"""
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    if not base or base.startswith("."):
        raise ValueError("文件名不能空着，也不能是隐藏文件喵")
    ext = os.path.splitext(base)[1].lower()
    if ext not in EXTS:
        raise ValueError(f"只收 {'、'.join(EXTS)} 这几种图片喵")
    mood = mood_of(base)
    if not mood:
        raise ValueError(
            "文件名开头得是心情喵，八个之一："
            + "、".join(MOODS) + f"。比如 happy1.gif、love_02.png（现在是 {base}）"
        )
    return mood, ext


def free_path(name):
    """给一个不冲突的目标路径：重名就加 -2、-3……（不会覆盖已有的表情包）。"""
    stem, ext = os.path.splitext(name)
    target = os.path.join(_dir(), name)
    index = 2
    while os.path.exists(target):
        target = os.path.join(_dir(), f"{stem}-{index}{ext}")
        index += 1
    return target


def library():
    """按心情列出现有的表情包：{"happy": [{"name","size"}...], ...}。

    认不出心情的文件放在 "other" 里，让主人能看见并改名——悄悄丢掉更糟。
    """
    moods = {mood: [] for mood in MOODS}
    other = []
    try:
        names = os.listdir(_dir())
    except OSError:
        names = []
    for name in names:
        full = os.path.join(_dir(), name)
        if not os.path.isfile(full) or name.startswith("."):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in EXTS:
            continue                      # 别的文件（比如 .gitkeep）不当作表情包
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        item = {"name": name, "size": size}
        mood = mood_of(name)
        if mood:
            moods[mood].append(item)
        else:
            other.append(item)
    for items in moods.values():
        items.sort(key=lambda it: it["name"])
    other.sort(key=lambda it: it["name"])
    return {"moods": moods, "other": other, "total": sum(len(v) for v in moods.values())}


def _safe_existing(name):
    """把名字解析成库里的一个真实文件，越界/不存在就抛 ValueError。"""
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    if not base or base != (name or "").strip():
        raise ValueError("文件名不对喵")
    full = os.path.join(_dir(), base)
    if not os.path.isfile(full):
        raise ValueError(f"没有这张表情包喵：{base}")
    return full


def remove(name):
    """删掉一张。返回被删的文件名。"""
    full = _safe_existing(name)
    os.remove(full)
    return os.path.basename(full)


def _discard(path):
    """回滚时删文件，删不掉就算了——不能让清理本身再抛异常。"""
    try:
        os.remove(path)
    except OSError:
        pass


def import_zip(path):
    """把压缩包里的图片收进库里。

    返回 {"added": [...], "skipped": [(文件名, 原因), ...]}。认不出来的不报错，
    一条条列给主人看——批量导入时最怕的就是「不知道哪几张没进去」。

    整包中途坏掉（坏 zip、读到一半 CRC 错、写盘失败）时，把本次已经写进去的连同
    那张半截文件一起删掉，干干净净地失败——不然调用方只会翻成一句「解不开」，
    库里却已经悄悄多了几张废图。
    """
    added, skipped = [], []
    written = []                      # 本次已成功落盘的文件全路径，出错时用来回滚
    total_bytes = 0
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                raw = info.filename.replace("\\", "/")
                base = os.path.basename(raw)
                if info.is_dir() or not base or base.startswith("."):
                    continue
                if base.startswith("__MACOSX") or raw.startswith("__MACOSX"):
                    continue
                if len(added) >= MAX_IMPORT_FILES:
                    skipped.append((base, f"一次最多导 {MAX_IMPORT_FILES} 张"))
                    break
                if info.file_size > MAX_FILE_BYTES:
                    skipped.append((base, f"超过 {MAX_FILE_BYTES // 1024 // 1024}MB"))
                    continue
                total_bytes += info.file_size
                if total_bytes > MAX_IMPORT_BYTES:
                    skipped.append((base, "解压后总量太大，剩下的没导"))
                    break
                try:
                    check_name(base)
                except ValueError as exc:
                    skipped.append((base, str(exc)))
                    continue
                target = free_path(base)
                # 一张一张读、一张一张写：单张已经限过 8MB，不会把整个包读进内存
                try:
                    with zf.open(info) as src, open(target, "wb") as dst:
                        while True:
                            chunk = src.read(64 * 1024)
                            if not chunk:
                                break
                            dst.write(chunk)
                except BaseException:
                    _discard(target)      # 写到一半 / 读坏了：这半截别留着
                    raise
                written.append(target)
                added.append(os.path.basename(target))
    except BaseException:
        # 中途失败：把本次已经写进去的都删掉，当作什么都没发生（调用方会翻成 400）
        for full in written:
            _discard(full)
        raise
    return {"added": added, "skipped": skipped}
