"""知识库索引：解析 -> 切块 -> 分词 / 向量化 -> 建索引，并把结果缓存到磁盘。

缓存按「单个文件」存，文件没动就直接复用上次的切块、分词和向量，只有新增 /
修改 / 删除的文件才重新处理。

检索走几路，再按名次融合（RRF）：
  · 原查询的词匹配：自建稀疏倒排索引 + numpy 向量化打分，查询词只碰到包含它的那些块，
    7 万块规模下 ~2ms。管精确词——STM32F103、I2C、IEEE754 这种。这一路永远都在，
    同语言的命中靠它。
  · 英文检索词：中文提问先用模型写成一句英文关键词再查一遍（见 translate.py），
    管跨语言——「内存取证」和 "memory forensics" 在词表上零交集，靠这一路才打得到
    英文教材。不用向量模型、不花那份钱。这一路只在英文内容里找（中文块有原查询那一路
    兜着，英文关键词落在它们身上纯是噪声）。
  · 向量：问题和原文都算成语义向量比远近（见 embed.py）。要配 key，配了才有这一路。

任何一路出问题（没配、接口挂了、返回的东西看不懂）都只是少一路，检索照常返回：
英文那路退回原查询，向量那路退回纯词匹配，不影响可用。

对外接口只有 KnowledgeBase.build() 和 KnowledgeBase.search() 两个方法。
"""
import hashlib
import math
import os
import pickle
import re
import time

import numpy as np

from ..config import (
    EMBED_DIM,
    KB_CACHE_PATH,
    KB_CHUNK_OVERLAP,
    KB_CHUNK_SIZE,
    KB_SOURCE_WEIGHTS,
    KB_SPARSE_WEIGHT,
    KB_TRANSLATE_WEIGHT,
    KB_VECTOR_WEIGHT,
    KNOWLEDGE_DIR,
)
from ..context import text_tokens
from . import embed, loader, translate
from .lexicon import expand

# 缓存结构变了就把版本号加一，旧缓存会自动失效重建
_CACHE_VERSION = 6

# 建索引时每过这么多文件就往盘上落一次缓存。全库向量化要发几千次请求、跑很久，
# 中途被杀掉的话，已经算好的也得留得住（见 build）。
_CHECKPOINT_FILES = 20

# 连着这么多个文件的向量都算不出来，就不再一个个去磕了：剩下的文件这次先只建词索引，
# 让建索引早点结束、能先用上；重启会接着补向量（见 build）。
_VECTOR_FAIL_LIMIT = 2

# BM25 参数：k1 控制词频饱和，b 控制长度归一化强度
_K1 = 1.5
_B = 0.75
# rank_bm25 的默认值，负 idf 会用 epsilon * 平均 idf 兜底
_EPSILON = 0.25

# 融合检索时，每一路各取这么多候选再排，最后才挑前 top_k 个
_CANDIDATES = 100
# RRF 的平滑常数。越大越不看重名次差异，60 是常用取值。
_RRF_K = 60
# 余弦低于这个值就当没匹配上，别把一堆弱相关的内容硬塞给模型
_VECTOR_MIN = 0.30
# 算向量分数时一次处理多少行。分块是为了别一次多出几百 MB 的临时数组。
_VECTOR_BLOCK = 8192

# 跨语言补进来的词打这个折扣（见 lexicon.py）。比原文分词低一档，是为了让
# 「同语言的命中」优先：中文提问先出中文资料，英文教材作为补充而不是反客为主。
_EXPAND_WEIGHT = 0.6

# 剔掉的功能词。中文部分是常见虚词 / 疑问词，英文部分是最常见的几十个停用词。
# 只收「几乎每块都有、本身不携带主题信息」的词，领域词一个都不要往里放。
_STOPWORDS = frozenset("""
的 地 得 了 着 过 是 在 和 与 及 或 等 就 都 而 也 还 又 很 太 更 最 不 没 无 会 能 要
把 被 给 让 向 从 到 对 为 以 之 其 这 那 哪 个 些 们 吧 呢 吗 啊 呀 哦 嗯 哈
我 你 他 她 它 我们 你们 他们 自己 大家 什么 怎么 怎样 如何 为什么 哪些 哪个 哪些
一个 一些 一下 一样 这种 那种 这里 那里 时候 的话 可以 能够 应该 需要 进行 通过 关于
因为 所以 但是 而且 如果 虽然 然后 就是 不是 没有 还是 只是 已经 一定 可能 或许 大概
上 下 里 中 外 前 后 时 用 做 说 看 想 知道 问题 问 答 请 帮 我 你
a an the of to in is are was were be been being am and or but if then than that this these those
it its as at by for from on with without into onto over under out up down off again further once
not no nor so such too very can could will would shall should may might must
do does did done doing have has had having i you he she we they them him his her their ours your my me us
which who whom whose what when where why how all any both each few more most other some only own same
about above after before during between through
""".split())


# 英文后缀还原表，长的排前面（先试 "ations" 再试 "s"）。只保留最常见的那些，
# 具体用法和边界见 _stem()。
_STEM_RULES = sorted(
    (
        ("ations", ""), ("ically", "ic"), ("ation", ""), ("ities", ""),
        ("sses", "ss"), ("iness", "y"), ("ings", ""), ("edly", ""),
        ("ance", ""), ("ence", ""), ("ness", ""), ("ment", ""), ("less", ""),
        ("able", ""), ("ible", ""), ("ally", ""), ("ing", ""), ("ies", "y"),
        ("ous", ""), ("ive", ""), ("ful", ""), ("ion", ""), ("ity", ""),
        ("est", ""), ("ed", ""), ("ly", ""), ("er", ""),
    ),
    key=lambda rule: -len(rule[0]),
)


def _tokenizer_signature():
    """切词规则的指纹：停用词表 / 词干规则 / jieba 版本变了，缓存里的分词结果就作废。

    每块的 tokens 是存进缓存的，而这些规则一改，旧 tokens 就跟按新规则切出来的查询词
    对不上——检索时两边错开，改了停用词却一点效果都没有（静默漏召回）。所以并进缓存
    指纹，规则一改就让缓存里的 tokens 就地重切（见 _read_cache，原文和向量都还能用）。
    """
    import jieba

    blob = "\x00".join(sorted(_STOPWORDS))
    blob += "\x01" + repr(_STEM_RULES)
    blob += "\x02" + str(getattr(jieba, "__version__", ""))
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


def _stem_once(token):
    """砍一次后缀 / 复数，砍不动就原样返回。"""
    for suffix, replacement in _STEM_RULES:
        if token.endswith(suffix):
            stem = token[:len(token) - len(suffix)] + replacement
            if len(stem) >= 4:
                return stem
    # 复数：process 这种以 ss 结尾的不动，免得越砍越短
    if token.endswith("s") and not token.endswith(("ss", "us", "is")) and len(token) - 1 >= 4:
        return token[:-1]
    return token


def _stem(token):
    """英文词干还原：让同一个词的不同词形落到同一个词上。

    教材里同一个概念会以各种词形出现（forensic / forensics、encrypt / encryption /
    encrypted），不还原就是「明明是一个词、字面对不上」，白丢分。中文、数字和缩写
    （i2c、stm32f103）一律不碰——那些本来就没有词形变化。

    规则保守：只砍常见后缀，砍完不足 4 个字符就放弃。不追求语言学上正确——乱砍会把
    本来不相干的词并成一个，比不还原更糟。

    反复砍到砍不动为止，不然词形不同的两个词会停在不同的地方：registers 只砍掉复数
    停在 register，而 register 本身砍成 regist，同一个词反倒对不上（requirements
    这类「复数 + 后缀」的词很常见，所以必须收敛）。
    """
    if len(token) < 5 or not token.isascii() or not token.isalpha():
        return token
    for _ in range(4):
        shorter = _stem_once(token)
        if shorter == token:
            break
        token = shorter
    return token


def _segment(text):
    """切词：jieba 分词 -> 转小写 -> 丢功能词 -> 英文还原词干。jieba 首次调用要建词典
    缓存，所以延迟导入。

    英文必须转小写：教材原文大小写混着来（"The Internet of Things"），不统一的话
    查询里的 internet 和索引里的 Internet 是两个不同的词，跨语言检索全落空。
    """
    import jieba

    tokens = []
    for piece in jieba.lcut(text):
        token = piece.lower()
        if _useful(token):
            tokens.append(_stem(token))
    return tokens


def _query_terms(query):
    """把查询变成 [(词, 权重), ...]。

    原文分词权重 1.0；lexicon 按术语补进来的跨语言词打 _EXPAND_WEIGHT 折——中文
    提问会自动带上英文术语，英文教材才可能被命中（反之亦然）。补进来的词同样要过
    _stem，不然索引里存的是还原过的词，两边对不上。
    """
    terms = {token: 1.0 for token in _segment(query)}
    for token in expand(query):
        token = _stem(token.lower())
        if _useful(token):
            terms.setdefault(token, _EXPAND_WEIGHT)
    return list(terms.items())


def _useful(token):
    """纯标点、空白和功能词都丢掉。

    功能词（的 / 是 / the / of 这类）几乎每块都出现，BM25 算出来的 idf 是负数，
    而负 idf 会被 epsilon * 平均 idf 兜底成**正数**——等于每个块都白拿一份分。
    结果是随便一个毫不相关的块也能排到前面，把真正相关的内容（尤其英文教材）
    挤下去。所以这里直接把它们剔掉，别让它们参与打分。
    """
    if not token.strip() or not any(ch.isalnum() for ch in token):
        return False
    return token not in _STOPWORDS


def _weight_of(rel_path):
    """按来源查加权系数，见 config.KB_SOURCE_WEIGHTS。"""
    key = rel_path.replace("\\", "/").lower()
    for prefix, weight in KB_SOURCE_WEIGHTS:
        if key.startswith(prefix):
            return weight
    return 1.0


# 判断一段是不是中文内容，见 _is_chinese()
_CJK = re.compile(r"[\u3400-\u9fff]")
_LETTER = re.compile(r"[A-Za-z\u3400-\u9fff]")


def _is_chinese(text):
    """这段内容是不是以中文写的（给英文检索那一路挡噪声用）。

    判「有没有中文」不行——英文教材里偶尔也夹着一两个汉字（目录、术语对照），一刀切会
    把整本书挡在英文检索之外。所以看比例：中文字符占「字母」的两成以上才算中文内容。
    """
    letters = len(_LETTER.findall(text))
    return letters >= 20 and len(_CJK.findall(text)) * 5 >= letters


def _split(text, size=KB_CHUNK_SIZE, overlap=KB_CHUNK_OVERLAP, max_tokens=0):
    """按段落打包成块。单段超长就硬切，相邻块留 overlap 个字符重叠。

    max_tokens > 0（= 向量模型单条输入的上限）时，打完包再按 token 收一道口：
    块是按字数切的，而字数跟 token 数不是一回事——中文 1 字差不多 1 个 token，
    英文 4 个字符才 1 个，同样的 500 字，中文块可能超、英文块差得远。所以只能
    拿每一块的实际内容去折算（见 _cap），不能写死一个字数。
    """
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n") if p.strip()]
    chunks = []
    buffer = ""

    for paragraph in paragraphs:
        # 超长段落：先把攒着的冲掉，再按固定长度硬切
        if len(paragraph) > size:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start:start + size])
                if start + size >= len(paragraph):
                    break
                # 步长至少 1：overlap >= size 时 size - overlap <= 0，不加下界就原地死循环
                start += max(1, size - overlap)
            continue

        if len(buffer) + len(paragraph) + 1 > size:
            chunks.append(buffer)
            buffer = buffer[-overlap:] + "\n" + paragraph if overlap else paragraph
        else:
            buffer = f"{buffer}\n{paragraph}" if buffer else paragraph

    if buffer:
        chunks.append(buffer)
    if max_tokens:
        chunks = [piece for chunk in chunks for piece in _cap(chunk, max_tokens, overlap)]
    return [c.strip() for c in chunks if c.strip()]


# 折算时留一成余量：估算本来就是估的，宁可块切小一点，也别卡着模型的边界
_FIT_MARGIN = 0.9


def _fit_chars(text, max_tokens):
    """这段文字里，多少个字差不多就是 max_tokens 个 token。

    按这段自己的「每字多少 token」折算：中文密（≈1 字 1 token）、英文疏（≈4 字符
    1 token），写死字数会错得很离谱。取样前 2000 字算密度，够代表这一段了。
    """
    sample = text[:2000] or text
    per_char = text_tokens(sample) / max(1, len(sample))
    if per_char <= 0:
        return len(text)
    return max(1, int(max_tokens * _FIT_MARGIN / per_char))


def _cap(chunk, max_tokens, overlap):
    """把一块压进 token 上限，返回若干段。都在上限以内就原样返回那一块。

    切完复核一遍：同一块里中英夹杂时密度并不均匀，折算出来的字数可能还是偏大，
    那就再收一次口（最多两轮，第二轮之后基本没有偏的了）。
    """
    pieces = [chunk]
    for _ in range(2):
        if max(text_tokens(piece) for piece in pieces) <= max_tokens:
            break
        out = []
        for piece in pieces:
            if text_tokens(piece) <= max_tokens:
                out.append(piece)
                continue
            budget = _fit_chars(piece, max_tokens)
            if budget >= len(piece):
                out.append(piece)      # 已经没法再切（密度估不准），留着别切碎
                continue
            step = max(1, budget - min(overlap, budget // 4))
            out.extend(piece[start:start + budget] for start in range(0, len(piece), step))
        pieces = out
    return pieces


def _scan():
    """递归找出 knowledge/ 下所有受支持的文档，返回相对路径（排序保证稳定）。"""
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    found = []
    for folder, _, names in os.walk(KNOWLEDGE_DIR):
        for name in names:
            full = os.path.join(folder, name)
            if loader.supported(full):
                found.append(os.path.relpath(full, KNOWLEDGE_DIR))
    return sorted(found)


def _parse(rel_path, full_path, signature, want_vectors, max_tokens=0):
    """把一个文件解析成块，每块带出处、页码、原文和分词结果。

    want_vectors 为真时顺手把整批块向量化（一个文件一次发完，别按块发）。
    max_tokens 是向量模型的单条输入上限，切块时按它收口（0 = 不限制，见 _split）。
    """
    chunks = []
    for text, page in loader.load(full_path):
        for piece in _split(text, KB_CHUNK_SIZE, KB_CHUNK_OVERLAP, max_tokens):
            chunks.append({
                "source": rel_path,
                "page": page,
                "text": piece,
                "tokens": _segment(piece),
            })
    entry = {"signature": list(signature), "chunks": chunks}
    if want_vectors:
        # 顺手归一化并转成 numpy，缓存里存的就是这个二进制数组，不再来回转
        entry["vectors"] = _normalize(embed.embed_texts([c["text"] for c in chunks]))
    return entry


def _chunk_params(max_tokens, known=True):
    """切块参数。它是缓存的一部分：改了就说明块得重切，光看文件的 mtime + size 发现不了。

    max_tokens 光看「值变没变」不够——它是由「模型」推出来的，而模型已经在 embed 指纹
    里了；这里只记最终用的上限，换了模型或者换了上限都会让缓存整体作废重切（重切必须
    重算向量，钱也只能花这一遍）。

    known=False（自动探测没探出来）时记 None 而不是 0：0 是个真实取值（不限），拿它当
    「不知道」会把「探测失败」和「真的不限」混成一件事，下次探成功值一变就白白作废整库。
    """
    return {
        "size": KB_CHUNK_SIZE,
        "overlap": KB_CHUNK_OVERLAP,
        "max_tokens": None if not known else int(max_tokens or 0),
    }


def _same_chunk_params(stored, params):
    """比对切块参数。max_tokens 若有一边是 None（探测没探出来），那一项就不比——
    否则「这次探测失败、下次探成功」的值不一样，整个缓存被判过期、向量全白重算。"""
    if stored.get("size") != params.get("size") or stored.get("overlap") != params.get("overlap"):
        return False
    before, now = stored.get("max_tokens"), params.get("max_tokens")
    if before is None or now is None:
        return True
    return before == now


# 缓存白名单：只允许反序列化这些类型。缓存在 .cache/（可写），裸 pickle.load 意味着
# 谁写进这个文件谁就能在服务进程里执行任意代码。把 find_class 收窄到缓存里真正会用到
# 的那些（基础容器 + numpy 数组 / dtype）；出现白名单外的类型就当缓存坏了丢掉重建——
# 宁可重算一遍，也不执行未知代码。
_PICKLE_ALLOWED = {
    ("builtins", "dict"), ("builtins", "list"), ("builtins", "tuple"),
    ("builtins", "set"), ("builtins", "frozenset"), ("builtins", "str"),
    ("builtins", "int"), ("builtins", "float"), ("builtins", "bool"),
    ("builtins", "bytes"), ("builtins", "bytearray"), ("builtins", "NoneType"),
    ("numpy", "dtype"), ("numpy", "ndarray"),
    ("numpy._core.numeric", "_frombuffer"), ("numpy.core.numeric", "_frombuffer"),
    ("numpy._core.multiarray", "_reconstruct"), ("numpy.core.multiarray", "_reconstruct"),
}


class _SafeUnpickler(pickle.Unpickler):
    """只认缓存里会用到的类型，挡住 pickle 的任意代码执行（见 _PICKLE_ALLOWED）。"""

    def find_class(self, module, name):
        if (module, name) in _PICKLE_ALLOWED:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"缓存里出现不允许的类型：{module}.{name}")


def _load_cache_file():
    """读缓存文件，白名单之外的一律当坏缓存（抛异常，由调用方兜底重建）。"""
    with open(KB_CACHE_PATH, "rb") as f:
        return _SafeUnpickler(f).load()


def _read_cache(want_vectors, embed_sig, chunk_params):
    """读缓存。

    换了向量模型（或维度）就把存着的向量全丢掉——旧向量跟新模型不在同一个空间里，
    留着算出来的分数是错的。分块和分词还能继续用，不用重新解析一遍 PDF。

    切块参数对不上就整份丢掉重切（老缓存里没这个字段，按当年的默认参数算，所以
    参数没变的话老缓存照样能用，不会白重算一遍向量）。

    切词规则（停用词 / 词干 / jieba 版本）变了，就把每块的 tokens 就地重切一遍：
    原文和向量都还能用，只是分词结果得按新规则重来，不用重发向量（省钱）。
    """
    try:
        cache = _load_cache_file()
    except Exception:
        return {}
    if cache.get("version") != _CACHE_VERSION:
        return {}
    stored = cache.get("chunk") or {"size": KB_CHUNK_SIZE,
                                    "overlap": KB_CHUNK_OVERLAP,
                                    "max_tokens": 0}
    if not _same_chunk_params(stored, chunk_params):
        return {}
    files = cache.get("files", {})
    if want_vectors and cache.get("embed") != embed_sig:
        for entry in files.values():
            entry.pop("vectors", None)
    if cache.get("tokenizer") != _tokenizer_signature():
        for entry in files.values():
            for chunk in entry.get("chunks", []):
                chunk["tokens"] = _segment(chunk["text"])
    return files


def _write_cache(files, embed_sig, chunk_params):
    os.makedirs(os.path.dirname(KB_CACHE_PATH), exist_ok=True)
    with open(KB_CACHE_PATH, "wb") as f:
        pickle.dump({"version": _CACHE_VERSION, "embed": embed_sig,
                     "tokenizer": _tokenizer_signature(),
                     "chunk": chunk_params, "files": files}, f)


def _normalize(rows):
    """按行归一化成单位向量，之后点积就是余弦相似度。零向量保持零，不参与匹配。

    存成 float16：8.5 万块 1024 维就是 174MB，float32 要翻倍。缓存在盘上也一样，
    存 Python 浮点列表的话光这一项就要 800MB，所以一定得是 numpy 数组。
    """
    if not len(rows):
        return np.zeros((0, EMBED_DIM), dtype=np.float16)
    matrix = np.asarray(rows, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.divide(matrix, norms, out=matrix, where=norms > 0)
    return matrix.astype(np.float16)


class _SparseIndex:
    """倒排索引 + numpy 向量化 BM25 打分。

    postings[词] = (块下标 int32 数组, 词频 float32 数组)。查询时只累加命中的
    那些块，不再像 BM25Okapi 那样对全部块逐个算一遍。
    """

    def __init__(self, token_lists):
        size = len(token_lists)
        self.size = size
        self.doc_len = np.zeros(size, dtype=np.float32)

        buckets = {}
        for i, tokens in enumerate(token_lists):
            self.doc_len[i] = len(tokens)
            if not tokens:
                continue
            counts = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            for term, freq in counts.items():
                buckets.setdefault(term, []).append((i, freq))

        self.avgdl = float(self.doc_len.mean()) if size else 0.0
        self.postings = {}
        doc_freq = {}
        for term, pairs in buckets.items():
            pairs.sort()  # 按下标有序，方便按需取交集 / 局部打分
            arr = np.array(pairs, dtype=np.float32)
            self.postings[term] = (arr[:, 0].astype(np.int32), arr[:, 1])
            doc_freq[term] = len(pairs)

        self.idf = {}
        if size:
            idf_sum = 0.0
            negative = []
            for term, freq in doc_freq.items():
                value = math.log(size - freq + 0.5) - math.log(freq + 0.5)
                self.idf[term] = value
                idf_sum += value
                if value < 0:
                    negative.append(term)
            fallback = _EPSILON * idf_sum / len(self.idf)
            for term in negative:
                self.idf[term] = fallback

    def scores(self, terms, weights):
        """只累加命中查询词的块，返回 float32 分数向量（长度 size）。

        terms 是 [(词, 权重), ...]，权重用来压低跨语言补进来的那些词。
        """
        out = np.zeros(self.size, dtype=np.float32)
        if not self.size:
            return out

        norm = 1.0 - _B + _B * self.doc_len / self.avgdl
        k1 = _K1
        for term, term_weight in terms:
            entry = self.postings.get(term)
            if entry is None:
                continue
            idf = self.idf.get(term, 0.0)
            if idf == 0.0:
                continue
            idx, freq = entry
            out[idx] += term_weight * idf * (freq * (k1 + 1.0)) / (freq + k1 * norm[idx])

        if weights is not None:
            out *= weights
        return out


class _VectorIndex:
    """向量索引：所有块的向量拼成一个大矩阵，检索时跟问题的向量算余弦。

    向量建库时就归一化过了，所以点积就是余弦相似度。矩阵存 float16 省一半内存
    （8.5 万块 × 1024 维约 174MB），算的时候分块升到 float32，避免一次多出
    几百 MB 的临时数组。
    """

    def __init__(self, matrix):
        self.matrix = matrix               # (块数, 维度) float16，已归一化
        self.size = int(matrix.shape[0])

    def scores(self, vector):
        """返回每个块的余弦相似度（float32 向量）。"""
        out = np.zeros(self.size, dtype=np.float32)
        query = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(query))
        if not self.size or norm == 0.0:
            return out
        query /= norm
        for start in range(0, self.size, _VECTOR_BLOCK):
            stop = min(start + _VECTOR_BLOCK, self.size)
            out[start:stop] = self.matrix[start:stop].astype(np.float32) @ query
        return out


class KnowledgeBase:
    """多路检索：词匹配（稀疏倒排）+ 英文检索词 + 向量。build() 组装，search() 融合。"""

    def __init__(self):
        self.chunks = []
        self._index = None
        self._weights = None
        self._chinese = None
        self._vector = None
        self.vector_error = ""   # 向量那路出问题就把原因记这儿，检索自动退回词匹配
        self.skipped = []        # 解析失败的文件名，便于排查
        self.max_tokens = 0      # 这次建索引用的「单条输入上限」，0 = 不限
        self.embed_note = ""     # 那个上限是怎么来的（后台显示用）
        self.vector_missing = 0  # 没算上向量的块数（多半是限流，下次重启会接着补）
        # 建索引的进度，后台拿 progress_snapshot() 显示（见 kb/__init__.py 的 stats）
        self.progress = {
            "stage": "还没开始",
            "files_done": 0,
            "files_total": 0,
            "chunks": 0,
            "current": "",
            "started": 0.0,       # 开始建的时刻，用来算已用时间和速度
        }

    def vector_count(self):
        """真正算好的向量块数。0 表示这一路没启用、或者一块都没算上。"""
        if self._vector is None:
            return 0
        return self._vector.size - self.vector_missing

    def progress_snapshot(self):
        """给后台看的进度：建到哪儿了 + 向量请求的情况 + 已经跑了多久、多快。

        建的过程中「算了多少块向量」取自实时计数（每一批回来就加），建完则以矩阵里
        真正对齐的块数为准。速度按「算好的向量块数 / 已用秒数」算——建库慢不慢基本
        就卡在向量那一段上，看这个数就知道是接口慢还是自己在磨蹭。
        """
        snapshot = dict(self.progress)
        snapshot.update(embed.progress())
        if snapshot.get("stage") != "正在建":
            snapshot["vectors"] = self.vector_count()
        started = self.progress.get("started") or 0.0
        elapsed = max(0.0, time.time() - started) if started else 0.0
        snapshot["elapsed"] = round(elapsed, 1)
        snapshot["speed"] = round(snapshot["vectors"] / elapsed, 2) if elapsed > 2 and snapshot["vectors"] else 0.0
        snapshot["missing"] = self.vector_missing
        return snapshot

    def build(self):
        """扫描 knowledge/ 建索引，返回块数。没变的部分走缓存，不重复解析、不重复花钱。"""
        want_vectors = embed.enabled()
        embed_sig = embed.signature() if want_vectors else ""
        # 向量模型单条输入的上限：后台填了就用填的，没填自动探一次（见 kb/embed.py）。
        # 块长按它收口——小窗口的模型（512 / 256 / 128）配 500 字的块会被静默截断或者 400。
        probed = want_vectors
        max_tokens = embed.max_input_tokens() if want_vectors else 0
        # 探测没探出来时这个值不可信（分不清是「真不限」还是「没探到」），别写进指纹，
        # 免得下次探成功值一变就把整个缓存判过期、向量全白重算（见 _chunk_params）。
        max_tokens_known = embed.max_input_tokens_known() if want_vectors else True
        chunk_params = _chunk_params(max_tokens, max_tokens_known)
        cached = _read_cache(want_vectors, embed_sig, chunk_params)
        embed.reset_progress()
        names = _scan()
        known = set(names)
        self.progress.update(stage="正在建", files_total=len(names), files_done=0,
                             chunks=0, current="", started=time.time())
        # 从缓存里已有的条目开始（只留文件还在的），这样中途落盘时不会把还没轮到、
        # 但上一轮已经算好的文件从缓存里挤掉
        files = {path: entry for path, entry in cached.items() if path in known}
        corpus = []
        weights = []
        vector_failed = []          # 向量没算上的文件（多半是限流），原因也带上
        vector_stopped = False      # 连着失败就不再一个个去磕了，剩下的先只建词索引
        vector_config_error = ""    # 配置错（维度对不上之类），重启也不会好，单独报

        for rel_path in names:
            self.progress["current"] = rel_path
            full_path = os.path.join(KNOWLEDGE_DIR, rel_path)
            stat = os.stat(full_path)
            signature = (stat.st_mtime, stat.st_size)
            entry = cached.get(rel_path)

            # 文件变了要重来；向量这一路开着但缓存里没有向量，也得重来
            stale = not entry or entry.get("signature") != list(signature)
            if not stale and want_vectors and "vectors" not in entry:
                stale = True

            if stale:
                entry = None
                if want_vectors and not vector_stopped:
                    try:
                        entry = _parse(rel_path, full_path, signature, True, max_tokens)
                    except embed.EmbedConfigError as exc:
                        # 配置错（典型：EMBED_DIM 跟接口实际返回的维度对不上）。这重启
                        # 多少次都不会好，必须去改配置——明确报出来、指到 .env，并且别
                        # 再往下磕其它文件（每个都会撞同一堵墙）。这次先只建词索引。
                        vector_config_error = str(exc)
                        vector_stopped = True
                    except Exception as exc:
                        # 一个文件的向量算不出来（多半是限流掐了）不该让整库白建：这个
                        # 文件先不带向量存着，接着算后面的。算好的都在缓存里，下次重启
                        # 会接着没算完的往下走，不会从头重来、也不白花第二遍钱。
                        vector_failed.append(f"{rel_path}（{type(exc).__name__}: {exc}）")
                        if len(vector_failed) >= _VECTOR_FAIL_LIMIT:
                            # 连着几个文件都算不出来，说明不是偶发：再一个个去磕（每个
                            # 文件都要退避重试两分钟）只是白等。剩下的文件这次先只建词
                            # 索引，让整个建索引早点结束、能用上；重启再接着补向量。
                            vector_stopped = True
                if entry is None:
                    try:
                        entry = _parse(rel_path, full_path, signature, False, max_tokens)
                    except Exception:
                        self.skipped.append(rel_path)
                        self.progress["files_done"] += 1
                        continue

            files[rel_path] = entry
            corpus.extend(entry["chunks"])
            # 权重按来源算，不写进缓存，改了 KB_SOURCE_WEIGHTS 立刻生效
            weights.extend([_weight_of(rel_path)] * len(entry["chunks"]))
            self.progress["files_done"] += 1
            self.progress["chunks"] = len(corpus)
            # 中途落一次盘：全库向量化要跑很久（几千次请求），万一中途服务被重启 /
            # 被杀掉，算好的这部分也留得住，下次接着没算完的往下走。整份写一次是
            # O(缓存大小)，所以隔一批写一次，别每个文件都写。
            if self.progress["files_done"] % _CHECKPOINT_FILES == 0:
                _write_cache(files, embed_sig, chunk_params)

        self.progress["current"] = ""
        if vector_config_error:
            # 配置错要跟「限流，重启会接着算」分开：这种重启多少次都不会好，必须改配置，
            # 报「重启服务会接着算」等于把用户指到错误动作上。
            self.vector_error = (
                f"向量配置有误，重启也不会好：{vector_config_error} "
                f"请按上面的提示改 .env（或后台配置）后重启服务；这次先只用词索引。"
            )
        elif vector_failed:
            shown = "；".join(vector_failed[:3])
            if len(vector_failed) > 3:
                shown += f"…（还有 {len(vector_failed) - 3} 个）"
            tail = "，剩下的文件这次先只建词索引" if vector_stopped else ""
            self.vector_error = (
                f"{len(vector_failed)} 个文件的向量没算上{tail}，"
                f"重启服务会接着算（算好的不会重来）：{shown}"
            )

        self.chunks = corpus
        self._index = _SparseIndex([c["tokens"] for c in corpus]) if corpus else None
        self._weights = np.array(weights, dtype=np.float32) if corpus else None
        self._chinese = np.array([_is_chinese(c["text"]) for c in corpus], dtype=bool)
        self.max_tokens = int(max_tokens or 0)
        self.embed_note = embed.probe_note() if probed else ""
        # 先把带向量的缓存写到盘上，再从 files 里摘掉拼成矩阵，省得内存里存两份
        _write_cache(files, embed_sig, chunk_params)
        self._vector = self._load_vectors(files) if want_vectors else None
        self.progress["stage"] = self._done_stage(want_vectors, corpus)
        return len(corpus)

    def _done_stage(self, want_vectors, corpus):
        """建完之后一句话说清「向量到底建成了没」，后台直接显示它。"""
        if not corpus:
            return "知识库是空的"
        if not want_vectors:
            return "没走向量这一路"
        if self.vector_count() >= len(corpus):
            return "向量建好了"
        return "部分完成" if self.vector_count() else "没有向量"

    def _load_vectors(self, files):
        """把各文件的向量按块序拼成一个矩阵。

        有文件没算上向量（多半是限流被掐了）时，那些块填**零向量**：零向量跟谁都
        不相似，检索时自然被跳过，但其余已经算好的块照样能走向量那一路——比整库退回
        纯词匹配好得多。缺了哪些块记在 vector_missing 里，后台会说出来。
         """
        pieces = []
        total = 0
        missing = 0
        for rel_path in _scan():
            entry = files.get(rel_path)
            if entry is None:
                return None
            count = len(entry["chunks"])
            vectors = entry.pop("vectors", None)
            if vectors is None or len(vectors) != count:
                missing += count
                vectors = np.zeros((count, EMBED_DIM), dtype=np.float16)
            pieces.append(vectors)
            total += count
        if not total or total != len(self.chunks):
            return None

        # 拷进一个大矩阵，拷完立刻把小块放掉，峰值只有「大矩阵 + 一个小块」
        matrix = np.empty((total, EMBED_DIM), dtype=np.float16)
        at = 0
        for index, piece in enumerate(pieces):
            matrix[at:at + len(piece)] = piece
            at += len(piece)
            pieces[index] = None
        self.vector_missing = missing
        return _VectorIndex(matrix)

    def search(self, query, top_k):
        """返回 [(分数, 块), ...]，按相关度从高到低，不相关的直接丢掉。

        原查询那一路永远都在：英文检索和向量都只是往结果里补，不会把同语言的命中
        挤掉。反过来，后两路翻不出来 / 没配 key 的时候就只剩原查询一路，行为跟以前
        一样。
        """
        if self._index is None:
            return []

        routes = [(self._rank_sparse(query), KB_SPARSE_WEIGHT)]
        english = translate.to_english(query)
        if english:
            routes.append((self._rank_english(english), KB_TRANSLATE_WEIGHT))
        if self._vector is not None:
            routes.append((self._rank_vector(query), KB_VECTOR_WEIGHT))

        if len(routes) == 1:
            ranked = [(score, self.chunks[i]) for i, score in routes[0][0]]
            return self._dedupe(ranked)[:top_k]
        return self._fuse(routes, top_k)

    def _top(self, scores, floor):
        """取分数最高的一批，返回 [(块下标, 分数), ...]，从高到低。"""
        keep = min(_CANDIDATES, len(scores))
        if keep <= 0:
            return []
        top = np.argpartition(-scores, keep - 1)[:keep]
        top = top[np.argsort(-scores[top])]
        return [(int(i), float(scores[i])) for i in top if scores[i] > floor]

    def _rank_sparse(self, query):
        """词匹配那路（原查询走这条，按来源加权）。"""
        return self._top(self._index.scores(_query_terms(query), self._weights), 0.0)

    def _rank_english(self, query):
        """英文检索那一路：只在英文内容里找。

        中文块有原查询那一路兜着，英文关键词对它们没有意义——中文资料里混着不少英文
        术语（data、structure、memory 这种），拿英文检索词去打分它们也排得挺靠前，
        同一段内容于是两路各得一次分，把真正的英文教材挤下去。所以这里按 _is_chinese
        把中文块直接清零。
        """
        scores = self._index.scores(_query_terms(query), None)
        scores[self._chinese] = 0.0
        return self._top(scores, 0.0)

    def _rank_vector(self, query):
        """向量那路。

        向量服务挂了、key 填错了、返回的东西看不懂——一律返回空、把原因记下来，
        让检索退回词匹配。知识库检索不该因为这一路出问题就整个用不了。
        """
        try:
            vector = embed.embed_query(query)
        except Exception as exc:
            self.vector_error = f"{type(exc).__name__}: {exc}"
            return []
        return self._top(self._vector.scores(vector), _VECTOR_MIN - 1e-6)

    def _fuse(self, routes, top_k):
        """RRF：每一路各按名次给分再相加。

        用名次而不是原始分，是因为各路的分数量纲根本没法比（词匹配十几到几十分，
        余弦 0~1），直接相加就得反复调系数；换成名次天然可比，也不怕某一路恰好
        分数特别大把其它路整个盖住。

        routes 是 [(名次列表, 权重), ...]；只有一路时名次顺序原样保留。
        """
        fused = {}
        for ranked, weight in routes:
            for rank, (i, _) in enumerate(ranked):
                fused[i] = fused.get(i, 0.0) + weight / (_RRF_K + rank)
        best = sorted(fused.items(), key=lambda kv: -kv[1])
        return self._dedupe([(score, self.chunks[i]) for i, score in best])[:top_k]

    def _dedupe(self, ranked):
        """同内容异名的两个文件会让同一段在结果里出现两次（RRF 里各拿一次分），
        这里按正文去重，只留名次靠前的那份。"""
        out = []
        seen = set()
        for score, chunk in ranked:
            text = chunk["text"]
            if text in seen:
                continue
            seen.add(text)
            out.append((score, chunk))
        return out
