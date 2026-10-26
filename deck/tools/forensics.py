"""数字取证工具：哈希校验、文件类型识别、字符串提取、文件雕复、时间戳与元数据。

只用标准库（PDF 元数据复用已有的 pypdf），不联网、不起子进程。输入输出都走
safe_path 锁在 workspace 内。除 carve_files 会把雕复出来的文件写进 workspace 外，
其余工具只读样本、以文字报告返回。
"""
import datetime
import hashlib
import math
import os
import re
import struct

from ..sandbox import safe_path

# 单个样本最多读这么多，防止一个巨型文件把内存吃光
_MAX_READ = 128 * 1024 * 1024
# 雕复时没有可靠结尾标记的格式，最多往后取这么大
_MAX_CARVE_SIZE = 8 * 1024 * 1024
# 熵值只在文件开头采这么多字节，全量统计太慢
_ENTROPY_SAMPLE = 256 * 1024
# 单条提取出来的字符串最多显示这么多字符（长到离谱的一条会把上下文灌满）
_MAX_STR = 300
# 字符串报告整体的字符上限
_MAX_STR_OUTPUT = 8000

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "hash_file",
            "description": "计算 workspace 里某个文件的 MD5 / SHA1 / SHA256（取证第一步的完整性校验）。可传 expected 跟已有哈希比对，判断文件有没有被动过。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 workspace 的文件路径，例如 samples/case.jpg"},
                    "expected": {"type": "string", "description": "可选。已知的哈希值，用来比对是否一致（自动忽略大小写和空格）"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "identify_file",
            "description": "读文件头魔数判断文件真实类型，并和扩展名比对（能看出改后缀伪装的文件）；同时给出大小、时间戳和熵值（熵接近 8 说明是加密 / 压缩 / 打包过的）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 workspace 的文件路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_strings",
            "description": "从二进制文件里提取可打印字符串（ASCII、UTF-16LE、中文都认），带偏移量。找 flag、URL、路径、命令、邮箱、密钥时用这个。可用 keyword 只筛含关键字的行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 workspace 的文件路径"},
                    "min_len": {"type": "integer", "description": "最短字符串长度，默认 4"},
                    "keyword": {"type": "string", "description": "可选。只返回包含该关键字的字符串（不区分大小写）"},
                    "limit": {"type": "integer", "description": "最多返回多少条，默认 200"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "carve_files",
            "description": "文件雕复：从二进制文件（镜像、被删过内容的文件）里按文件头尾签名把里面嵌的图片 / PDF / 压缩包等抠出来，写到 workspace 下的 out_dir。用于恢复被删除或藏在文件里的内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 workspace 的样本文件路径"},
                    "out_dir": {"type": "string", "description": "雕复结果写到 workspace 下的哪个目录，默认 carved"},
                    "max_files": {"type": "integer", "description": "最多雕复几个文件，默认 50"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_metadata",
            "description": "看文件的创建 / 修改 / 访问时间（MACB 时间线），以及文档元数据：PDF 的作者 / 制作软件 / 生成时间，JPEG 的 EXIF（相机型号、拍摄时间、GPS 经纬度）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 workspace 的文件路径"},
                },
                "required": ["path"],
            },
        },
    },
]

# 文件头魔数 -> (类型描述, 常见扩展名)。同一种类型按特征从长到短排，先匹配长的。
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "PNG 图片", ".png"),
    (b"SQLite format 3\x00", "SQLite 数据库", ".sqlite/.db"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "旧版 Office 文档 / OLE2 复合文档（doc/xls/ppt/msg）", ".doc/.xls/.ppt"),
    (b"\x7fELF", "Linux 可执行文件（ELF）", ""),
    (b"Rar!\x1a\x07", "RAR 压缩包", ".rar"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip 压缩包", ".7z"),
    (b"PK\x03\x04", "ZIP 容器（docx / xlsx / pptx / jar / apk 这些本质都是 ZIP 包装）",
     ".zip/.docx/.xlsx/.pptx/.jar/.apk/.odt/.ods/.odp/.epub/.whl"),
    (b"PK\x05\x06", "ZIP 空包（只有目录）", ".zip"),
    (b"\xff\xd8\xff", "JPEG 图片", ".jpg"),
    (b"GIF87a", "GIF 图片", ".gif"),
    (b"GIF89a", "GIF 图片", ".gif"),
    (b"%PDF", "PDF 文档", ".pdf"),
    (b"\x1f\x8b", "GZIP 压缩流", ".gz"),
    (b"BZh", "BZIP2 压缩流", ".bz2"),
    (b"\xca\xfe\xba\xbe", "Java class / Mach-O 通用二进制", ".class"),
    (b"OggS", "Ogg 媒体", ".ogg"),
    (b"ID3", "MP3 音频（带 ID3 标签）", ".mp3"),
    (b"RIFF", "RIFF 容器（WAV / AVI / WebP）", ".wav/.avi/.webp"),
    (b"MZ", "Windows 可执行文件（EXE / DLL）", ".exe/.dll"),
    (b"BM", "BMP 位图", ".bmp"),
)

# 纯文本认不出更细的类型，拿扩展名兜底：这些都是「文本」，内容确实是文本时不该报改后缀伪装。
# （以前这里只有 .txt，于是 .py/.md/.json/.log/.csv/... 全被误报成伪装。）
_TEXT_EXTS = (
    "txt", "text", "md", "markdown", "rst", "json", "jsonl", "ndjson", "log", "csv", "tsv",
    "html", "htm", "xhtml", "xml", "xsl", "svg", "yaml", "yml", "ini", "cfg", "conf", "config",
    "toml", "properties", "env", "c", "h", "cpp", "cxx", "cc", "hpp", "hh", "cs", "java",
    "js", "mjs", "cjs", "jsx", "ts", "tsx", "py", "pyw", "pyi", "sh", "bash", "zsh", "bat",
    "cmd", "ps1", "psm1", "sql", "css", "scss", "less", "tex", "go", "rs", "rb", "php", "pl",
    "lua", "swift", "kt", "kts", "gradle", "dart", "r", "m", "vb", "vbs", "asm", "s", "diff",
    "patch", "rtf", "srt", "vtt", "pem", "crt", "key", "gitignore", "editorconfig",
)

# 这些扩展名本身就是 ZIP 容器（OOXML / OpenDocument / JAR 之类），
# 内容识别成 ZIP 是正常的，不该报「改后缀伪装」，给一句说明就行。
_ZIP_BASED = frozenset(("docx", "xlsx", "pptx", "jar", "apk", "odt", "ods", "odp", "epub", "whl"))

# 雕复签名：(扩展名, 头, 尾)。尾为 None 的格式没有可靠结束标记，按 _MAX_CARVE_SIZE 截。
_CARVE_SIGNATURES = (
    ("jpg", b"\xff\xd8\xff", b"\xff\xd9"),
    ("png", b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82"),
    ("gif", b"GIF89a", b"\x3b"),                 # 尾标记是单字节 0x3B，但数据里到处都是，取法见 _gif_trailer
    ("pdf", b"%PDF", b"%%EOF"),
    ("zip", b"PK\x03\x04", b"PK\x05\x06"),       # 用中央目录结束记录当结尾，别整包 8MB
)

_EXIF_IFD0 = {
    0x010F: "相机厂商",
    0x0110: "相机型号",
    0x0112: "方向",
    0x0131: "处理软件",
    0x0132: "拍摄时间",
    0x013B: "作者",
    0x8298: "版权",
}
_GPS_NAMES = {1: "GPS 纬度", 2: "GPS 纬度", 3: "GPS 经度", 4: "GPS 经度", 6: "GPS 海拔"}


def _read(path):
    """读样本文件，超过上限就只读前半部分。"""
    full = safe_path(path)
    if not os.path.isfile(full):
        raise ValueError(f"文件不存在 {path}")
    if os.path.getsize(full) <= _MAX_READ:
        with open(full, "rb") as f:
            return f.read(), False
    with open(full, "rb") as f:
        return f.read(_MAX_READ), True


def hash_file(path: str, expected: str = "") -> str:
    """算 MD5 / SHA1 / SHA256，可选与 expected 比对。"""
    full = safe_path(path)
    if not os.path.isfile(full):
        return f"错误：文件不存在 {path}"

    digests = {name: hashlib.new(name) for name in ("md5", "sha1", "sha256")}
    size = 0
    with open(full, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            size += len(block)
            for hasher in digests.values():
                hasher.update(block)

    lines = [f"文件：{path}", f"大小：{size} 字节"]
    for name, hasher in digests.items():
        lines.append(f"{name.upper()}：{hasher.hexdigest()}")

    if expected:
        want = expected.strip().lower().replace(" ", "")
        matched = [name.upper() for name, hasher in digests.items() if hasher.hexdigest() == want]
        if matched:
            lines.append(f"比对结果：与 expected 一致（命中 {matched[0]}）")
        else:
            lines.append("比对结果：不一致！文件可能被修改过，或者 expected 传错了")
    return "\n".join(lines)


def _entropy(data):
    """香农熵，采样前 _ENTROPY_SAMPLE 字节。"""
    sample = data[:_ENTROPY_SAMPLE]
    if not sample:
        return 0.0
    counts = {}
    for byte in sample:
        counts[byte] = counts.get(byte, 0) + 1
    total = len(sample)
    # 单符号（比如全 0 的文件）时每项都是 1.0 * log2(1.0) = 0.0，整体取负号会得到 IEEE 负零，
    # 再按 "%.2f" 格式化就成了很难看的「-0.00」。加 0.0 按 IEEE 规则把 -0.0 规范成 +0.0。
    return -sum((c / total) * math.log2(c / total) for c in counts.values()) + 0.0


def _looks_like_bmp(data):
    """给只有两个字节的 "BM" 补个校验，免得 "BMI report 2024" 这种文本被当成位图。

    BMP 文件头固定 14 字节：偏移 2..5 是小端文件大小，6..9 是保留位（规范要求 0），
    10..13 是像素数据起始偏移。大小字段明显和文件长度对不上，就说明不是 BMP。
    """
    if len(data) < 14:
        return False
    size = int.from_bytes(data[2:6], "little")
    if int.from_bytes(data[6:10], "little") != 0:       # 保留位必须为 0
        return False
    if int.from_bytes(data[10:14], "little") < 14:      # 像素数据不可能落在文件头里面
        return False
    # 读到的是文件开头那一段：超过 _MAX_READ 的大文件会被截断，此时 size 比 len(data) 大也正常，
    # 所以只在「没被截断却对不上」时才否决。size 为 0 是有些写入方干脆不填，放行。
    return size == 0 or size <= len(data) or len(data) >= _MAX_READ


def _identify(data):
    """按魔数猜类型，认不出来再看是不是纯文本。"""
    for magic, desc, ext in _SIGNATURES:
        if not data.startswith(magic):
            continue
        if magic == b"BM" and not _looks_like_bmp(data):
            continue        # 只是碰巧以 "BM" 开头，交给后面的文本判定
        return desc, ext
    sample = data[:4096]
    if sample and b"\x00" not in sample:
        for encoding in ("utf-8", "gbk"):
            try:
                sample.decode(encoding)
                return f"纯文本（{encoding}）", "/".join("." + e for e in _TEXT_EXTS)
            except UnicodeDecodeError:
                continue
    return "未知格式", ""


def identify_file(path: str) -> str:
    """魔数识别 + 扩展名比对 + 熵值。"""
    data, truncated = _read(path)
    size = os.path.getsize(safe_path(path))
    desc, ext = _identify(data)
    entropy = _entropy(data)

    lines = [
        f"文件：{path}",
        f"大小：{size} 字节",
        f"实际类型：{desc}",
    ]
    if ext:
        # 纯文本那串扩展名有几十个，全铺一行太吵：只列前几个，再把总数报出来
        names = ext.split("/")
        shown = "/".join(names) if len(names) <= 10 else "/".join(names[:9]) + f"/…（共 {len(names)} 种）"
        lines.append(f"这类文件常用扩展名：{shown}")

    real_ext = os.path.splitext(path)[1].lower().lstrip(".")
    if real_ext and ext:
        allowed = [e.lstrip(".").lower() for e in ext.split("/")]
        if real_ext not in allowed:
            lines.append(f"⚠ 扩展名是 .{real_ext}，但内容其实是 {desc}——疑似改后缀伪装")
        elif real_ext in _ZIP_BASED:
            # docx / xlsx / jar 这些本来就是 ZIP 容器，识别成 ZIP 属于正常，说明一句就行
            lines.append(f"说明：.{real_ext} 本质就是 ZIP 容器（里面是 XML / 资源部件），扩展名和内容相符，不是伪装")

    note = "加密 / 压缩 / 打包过" if entropy > 7.5 else ("基本是明文" if entropy < 5.5 else "普通二进制数据")
    lines.append(f"熵值：{entropy:.2f} / 8（采样前 {_ENTROPY_SAMPLE // 1024}KB，{note}）")
    if truncated:
        lines.append(f"注意：文件超过 {_MAX_READ // 1024 // 1024}MB，只读了前一部分")
    return "\n".join(lines)


def extract_strings(path: str, min_len: int = 4, keyword: str = "", limit: int = 200) -> str:
    """抽 ASCII / UTF-16LE / 中文可打印字符串，带偏移。"""
    min_len = max(1, int(min_len))
    limit = max(1, int(limit))
    data, truncated = _read(path)

    found = []
    for match in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
        found.append((match.start(), match.group().decode("ascii", "replace")))
    for match in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len, data):
        found.append((match.start(), match.group().decode("utf-16-le", "replace")))
    text = data.decode("utf-8", errors="ignore")
    for match in re.finditer(r"[\u4e00-\u9fff]{2,}", text):
        found.append((match.start(), match.group()))

    if keyword:
        needle = keyword.lower()
        found = [item for item in found if needle in item[1].lower()]

    found.sort()
    lines = [f"文件：{path}"]
    if keyword:
        lines.append(f"含关键字「{keyword}」的字符串：{len(found)} 条")
    else:
        lines.append(f"共提取到 {len(found)} 条可打印字符串（最短 {min_len} 字符）")
    shown = 0
    for offset, value in found[:limit]:
        if len(value) > _MAX_STR:      # 一条长到离谱的字符串本身就够把上下文灌满了
            value = value[:_MAX_STR] + "…"
        line = f"[0x{offset:08x}] {value}"
        if shown + len(line) + 1 > _MAX_STR_OUTPUT:
            lines.append("…（报告已经够长了，剩下的用 keyword 过滤更省事）")
            break
        lines.append(line)
        shown += len(line) + 1
    if len(found) > limit:
        lines.append(f"…… 还有 {len(found) - limit} 条没显示，可以加 keyword 过滤或调大 limit")
    if truncated:
        lines.append(f"注意：文件超过 {_MAX_READ // 1024 // 1024}MB，只搜了前一部分")
    return "\n".join(lines)


def _gif_trailer(data, begin):
    """按 GIF 的块结构往后走，返回真正的 trailer（0x3B）的下标；找不到返回 -1。

    GIF 的 LZW 压缩数据、注释 / 应用扩展块里 0x3B 都很常见，直接 find 取第一个会把图切短
    （实测 60 字节的 GIF 只写出 29 字节）。老老实实一块一块跳过去，跳到的那个 0x3B 才算数；
    结构走不通（截断 / 损坏）时退回「取最后一个 0x3B」，也比取第一个强。
    """
    pos = begin + 6                     # 跳过 GIF87a / GIF89a
    if pos + 7 > len(data):
        return -1
    packed = data[pos + 4]
    pos += 7                            # Logical Screen Descriptor
    if packed & 0x80:                   # 有全局调色板，整块跳过去
        pos += 3 * (2 << (packed & 0x07))
    while pos < len(data):
        marker = data[pos]
        if marker == 0x3B:              # Trailer：就是它
            return pos
        if marker == 0x21:              # 扩展块：0x21 + label，后面跟子块链
            pos += 2
        elif marker == 0x2C:            # 图像描述符：固定 10 字节
            if pos + 10 > len(data):
                break
            local = data[pos + 9]
            pos += 10
            if local & 0x80:            # 局部调色板
                pos += 3 * (2 << (local & 0x07))
            pos += 1                    # LZW 最小码长
        else:
            break
        while pos < len(data):                   # 子块链：[长度][数据]，长度 0 那个块就是链尾
            length = data[pos]
            pos += 1 + length
            if not length:
                break
    return data.rfind(b"\x3b", begin + 6)


def carve_files(path: str, out_dir: str = "carved", max_files: int = 50) -> str:
    """按文件头尾签名雕复出内嵌文件，写到 workspace 下的 out_dir。"""
    max_files = max(1, int(max_files))
    data, truncated = _read(path)

    target = safe_path(out_dir)
    os.makedirs(target, exist_ok=True)

    hits = []
    for ext, head, tail in _CARVE_SIGNATURES:
        start = 0
        while True:
            begin = data.find(head, start)
            if begin < 0:
                break
            start = begin + 1
            if tail:
                # GIF 的尾标记是单字节 0x3B 且数据里遍地都是，得按结构找真正的 trailer；
                # 其余格式（PNG 的 IEND、JPEG 的 FFD9）取第一个就是对的，别动。
                stop = _gif_trailer(data, begin) if ext == "gif" else data.find(tail, begin + len(head))
                if stop < 0:
                    continue
                stop += len(tail)
                if tail == b"PK\x05\x06":
                    # ZIP 的 EOCD 记录后面还有 18 字节 + 可变长注释，取完整
                    if stop + 18 <= len(data):
                        comment = int.from_bytes(data[stop + 16:stop + 18], "little")
                        stop = min(stop + 18 + comment, len(data))
                    else:
                        stop = len(data)
            else:
                stop = min(begin + _MAX_CARVE_SIZE, len(data))
            hits.append((begin, stop, ext))

    hits.sort()
    # 同一个容器里会有一串嵌套的签名（比如 ZIP 里每个条目都是 PK\x03\x04），
    # 按区间去重，不然同一段内容会被反复抠出来写盘。
    kept = []
    ext_stop = {}
    for begin, stop, ext in hits:
        if begin < ext_stop.get(ext, -1):
            continue
        kept.append((begin, stop, ext))
        ext_stop[ext] = stop
    hits = kept

    lines = [f"样本：{path}", f"命中签名：{len(hits)} 处，写入 workspace/{out_dir}/"]
    written = 0
    for begin, stop, ext in hits:
        if written >= max_files:
            lines.append(f"已达上限 {max_files} 个，剩下的没再写")
            break
        name = f"carved_{written:02d}_{begin:08x}.{ext}"
        with open(os.path.join(target, name), "wb") as f:
            f.write(data[begin:stop])
        lines.append(f"  {name}  偏移 0x{begin:08x}  大小 {stop - begin} 字节")
        written += 1
    if not written:
        lines.append("  没抠出东西：这个样本里没有认识的签名，或者内容被加密 / 压缩过")
    if truncated:
        lines.append(f"注意：文件超过 {_MAX_READ // 1024 // 1024}MB，只扫了前一部分")
    return "\n".join(lines)


def _read_ifd(blob, offset, endian):
    """读一个 EXIF IFD，返回 {tag: (类型, 个数, 原始字节)}。"""
    if offset <= 0 or offset + 2 > len(blob):
        return {}
    total = struct.unpack(endian + "H", blob[offset:offset + 2])[0]
    unit = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
    entries = {}
    for index in range(total):
        pos = offset + 2 + index * 12
        if pos + 12 > len(blob):
            break
        tag, ftype, count = struct.unpack(endian + "HHI", blob[pos:pos + 8])
        size = unit.get(ftype, 1) * count
        if size <= 4:
            raw = blob[pos + 8:pos + 8 + size]
        else:
            pointer = struct.unpack(endian + "I", blob[pos + 8:pos + 12])[0]
            raw = blob[pointer:pointer + size]
        entries[tag] = (ftype, count, raw)
    return entries


def _exif_value(entry, endian):
    """把 EXIF 条目转成能读的值。"""
    ftype, count, raw = entry
    try:
        if ftype == 2:
            return raw.split(b"\x00")[0].decode("ascii", "replace").strip()
        if ftype == 3 and len(raw) >= 2:
            return struct.unpack(endian + "H", raw[:2])[0]
        if ftype == 4 and len(raw) >= 4:
            return struct.unpack(endian + "I", raw[:4])[0]
        if ftype in (5, 10) and len(raw) >= 8 * count:
            values = []
            for i in range(count):
                num, den = struct.unpack(endian + "II", raw[i * 8:i * 8 + 8])
                values.append(num / den if den else 0.0)
            return values
    except struct.error:
        return None
    return None


def _jpeg_exif(data):
    """从 JPEG 的 APP1 段里取出 EXIF（TIFF 头开始的那一段）。"""
    if not data.startswith(b"\xff\xd8"):
        return None
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xDA:
            return None
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        segment = data[pos + 4:pos + 2 + length]
        if marker == 0xE1 and segment.startswith(b"Exif\x00\x00"):
            return segment[6:]
        pos += 2 + length
    return None


def _exif_report(blob):
    """解析 TIFF 头 + IFD0 + GPS IFD。"""
    if len(blob) < 8:
        return []
    if blob[:2] == b"II":
        endian = "<"
    elif blob[:2] == b"MM":
        endian = ">"
    else:
        return []
    ifd0_offset = struct.unpack(endian + "I", blob[4:8])[0]
    ifd0 = _read_ifd(blob, ifd0_offset, endian)

    lines = []
    for tag, name in _EXIF_IFD0.items():
        if tag in ifd0:
            value = _exif_value(ifd0[tag], endian)
            if value not in (None, ""):
                lines.append(f"EXIF {name}：{value}")

    gps_pointer = ifd0.get(0x8825)
    if gps_pointer:
        gps_offset = struct.unpack(endian + "I", gps_pointer[2][:4])[0] if len(gps_pointer[2]) >= 4 else 0
        gps = _read_ifd(blob, gps_offset, endian)
        values = {}
        for tag in (1, 2, 3, 4, 6):
            if tag in gps:
                values[tag] = _exif_value(gps[tag], endian)
        for ref_tag, deg_tag, label in ((1, 2, "纬度"), (3, 4, "经度")):
            degrees = values.get(deg_tag)
            if isinstance(degrees, list) and len(degrees) >= 3:
                decimal = degrees[0] + degrees[1] / 60 + degrees[2] / 3600
                ref = values.get(ref_tag)
                if ref in ("S", "W"):
                    decimal = -decimal
                lines.append(f"EXIF GPS {label}：{decimal:.6f}（ref={ref}）")
        if isinstance(values.get(6), list) and values[6]:
            lines.append(f"EXIF GPS 海拔：{values[6][0]:.1f} 米")
    return lines


def _time(value):
    return datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def file_metadata(path: str) -> str:
    """MACB 时间戳 + PDF / JPEG 文档元数据。"""
    full = safe_path(path)
    if not os.path.isfile(full):
        return f"错误：文件不存在 {path}"

    stat = os.stat(full)
    lines = [
        f"文件：{path}",
        f"大小：{stat.st_size} 字节",
        f"创建时间：{_time(stat.st_ctime)}",
        f"修改时间：{_time(stat.st_mtime)}",
        f"访问时间：{_time(stat.st_atime)}",
    ]

    head = ""
    with open(full, "rb") as f:
        head = f.read(4096)

    if head.startswith(b"%PDF"):
        try:
            from pypdf import PdfReader

            meta = PdfReader(full).metadata or {}
            for key in ("/Title", "/Author", "/Subject", "/Creator", "/Producer",
                        "/CreationDate", "/ModDate"):
                value = meta.get(key)
                if value:
                    lines.append(f"PDF {key.lstrip('/')}：{value}")
        except Exception as exc:
            lines.append(f"PDF 元数据读取失败：{exc}")
    elif head.startswith(b"\xff\xd8\xff"):
        data, _ = _read(path)
        blob = _jpeg_exif(data)
        lines.extend(_exif_report(blob) if blob else ["没找到 EXIF 段，可能被处理软件抹掉了"])
    else:
        lines.append("这种格式暂无元数据解析（目前支持 PDF 和 JPEG）")

    return "\n".join(lines)
