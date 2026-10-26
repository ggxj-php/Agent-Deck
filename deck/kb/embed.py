"""向量化：调 OpenAI 兼容的 /embeddings 接口，把文本变成向量。

知识库检索的第二路。词匹配那套（kb/index.py 的 BM25）按字面词查，中文问题跟英文
教材在词表上零交集；向量把不同语言的同义内容映到相近位置，中文提问才搜得到英文书。

模型 / 接口地址 / key 都从 settings 里取，网页后台能改，后台还有个总开关。开关关了、
或者三样没配齐，这一路就不启用，检索自动退回纯词匹配，不会报错。

两处「自动适配」：
  · 一次发几条——接口嫌行多就砍半重发（见 embed_texts）；
  · 一条能有多长——建索引前探一次模型的单条输入上限，反推块长（见 max_input_tokens）。
    第二件事是为了小窗口的向量模型（512 / 256 / 128），块切大了会被静默截断或者 400。
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .. import settings
from ..config import EMBED_DIM

# 一次请求带多少条。各家的「单次最大行数」不一样：百炼 qwen3.7 系列收 20 行，
# text-embedding-v4 / v3 只收 10 行，v1 / v2 是 25 行。先按 _BATCH_MAX 发，接口嫌多
# 就自动砍半重发（见 embed_texts），砍出来的安全值记在 _batch 里，一个进程只碰一次壁。
# 全库 8.5 万块按 64 行一批是 1328 次请求，按 20 行一批是 4248 次——建库慢不慢，一大半
# 卡在「接口每分钟能收几次请求」上，所以默认给大一点。嫌大就用 EMBED_BATCH 调小。
_BATCH_MAX = max(1, int(os.environ.get("EMBED_BATCH", "64") or 64))
_batch = _BATCH_MAX
# 同时开几个请求。全库 8.5 万块要发上千次请求，串行发太慢。8 路既快又不容易被限流
# （按 40 秒一次请求算，8 路约 12 次/分钟，多数免费额度还吃得下）；被限流就调小，
# 接口够宽就调大，用 EMBED_WORKERS 配。
_WORKERS = max(1, int(os.environ.get("EMBED_WORKERS", "8") or 8))
# 单次请求等多久。接口健康时一次几秒，卡满这个数说明对面基本不会回了，别干等。
_TIMEOUT = max(5.0, float(os.environ.get("EMBED_TIMEOUT", "60") or 60))
_RETRIES = 3          # 普通失败（5xx、网络抖动）重试几次
_RATE_RETRIES = 6     # 限流（429）单独多给几次：免费额度动不动就 429，等一下多半就好了
_RETRY_CAP = 60.0     # 单次重试最多等这么久。等太久不如报错，让下次重启接着算

# 建索引的实时计数，后台拿它显示进度（见 kb/index.py 的 progress_snapshot）
_progress = {"vectors": 0, "requests": 0, "retries": 0, "failed": 0}

# ---------- 单条输入上限（自动探测）----------
# 上面那个砍半只管「一次发几条」，不管「一条有多长」。而分块是按字数切的
# （config.KB_CHUNK_SIZE = 500 字），跟模型能吃多长原本毫无关系。碰上小窗口的模型
# （bge-small-zh 512、一些本地小模型 256 / 128），中文块会超：接口会 400（整个向量
# 那一路被迫关掉），或者更糟——静默截断，块的尾巴永远搜不到而且一声不响。
#
# 所以第一次要发向量之前先探一次，探测结果记在进程里（一个进程只探一遍）：
#   · 先拿一段约 _PROBE_TOP 个 token 的文本单发一条；
#   · 接口报 4xx = 吃不下，按阶梯往下找；
#   · 没报错，就再发一条三倍长的，比它自己报的 usage.prompt_tokens：
#     涨了 = 没截断；不涨（或者三倍那条直接报太长）= 它把输入掐住了，掐的那个数就是上限；
#   · 探不出来（网络不通、接口不报用量、返回看不懂）= 按「不限」，也就是回到老行为。
#
# 为什么要发两条来比：各家 tokenizer 的密度差得远（同样是中文，有的 1 字 1 token，
# 有的 1.5 字 1 token），拿我们自己的估算去跟它报的用量比一定会误判；拿它自己的两个
# 数互相比才是可靠的。
_PROBE_TOP = 900          # 第一档。现有块长（500 字，最坏全中文）估出来约 580 token，
                          # 这一档能完整过，就说明怎么切都不会超，直接收工
_PROBE_LADDER = (900, 512, 384, 256, 192, 128)
_PROBE_FLOOR = 64         # 探出来比这还小就没法用了（块会碎成渣），按不限处理
_PROBE_MARGIN = 0.9       # 比它报的用量再收一成，别卡着边界
_PROBE_STEPS = 3          # 上限夹在两档中间时，再二分几次（一次一条小请求）
_PROBE_TIMEOUT = 20       # 探测是阻塞建索引的，别用一个 60 秒的超时
# 只有这几个状态码才当「输入太长」。别把 4xx 一锅端：401 / 403 是 key 不对、
# 404 是地址写错、429 是限流，当成「太长」会让块长一路缩到最小还一声不响。
_TOO_LONG_CODES = (400, 413, 422)
_probed = {}              # {"模型@维度@地址": (上限, 说明, 探没探准)}，进程内只探一遍


class EmbedError(RuntimeError):
    """向量化失败。调用方接住它、把向量那一路关掉就行，别让整个索引建不起来。"""


class EmbedConfigError(EmbedError):
    """向量配置有问题（典型：接口返回的维度和 EMBED_DIM 对不上）。

    这类错重启、重试都不会好，必须去改配置。跟「限流 / 网络」那种等一等还能续跑的
    失败分开报，免得给用户指错动作（见 kb/index.py 的 build）。
    """


class _TooManyItems(EmbedError):
    """一次塞的行数超过服务商上限。内部信号，embed_texts 会砍半重试，不往上报。"""


def enabled():
    """这一路到底用不用：后台那个开关关了就关；开着/没设过，还得模型、地址、key 配齐。"""
    return settings.embed_on()


def signature():
    """缓存用的指纹：模型 / 维度 / 接口地址 变了才需要重算向量。

    base_url 也得算进去：同一个模型名挂在不同地址（不同服务商 / 自建）上未必是同一套
    权重，向量不在一个空间里，混着用就是静默漏召回。key 不算——换个 key 不该让几万个
    块的向量全部作废重花钱。
    """
    return f"{settings.embed_model()}@{EMBED_DIM}@{settings.embed_base_url()}"


def describe():
    return f"{settings.embed_model()} @ {settings.embed_base_url()}"


def progress():
    """建索引的实时计数，给后台显示进度用。"""
    return dict(_progress)


def reset_progress():
    """每次重建索引从零开始数。"""
    _progress.update(vectors=0, requests=0, retries=0, failed=0)


def _headers():
    return {
        "Authorization": f"Bearer {settings.embed_key()}",
        "Content-Type": "application/json",
    }


def _probe_text(tokens):
    """造一段「我们自己估着约 tokens 个 token」的中文文本。

    全用中日韩字符，估算里 1 字就是 1 token，好算。对方 tokenizer 多密都不影响
    结论——探测比的是「它自己报的两个用量」，不是我们的估算。
    """
    unit = "内存取证与数据分析技术要点说明文档内容示例"
    tokens = max(1, int(tokens))
    return (unit * (tokens // len(unit) + 1))[:tokens]


def _ask(text):
    """单发一条（不是一批，这样 usage 才只属于这一条），返回 (状态, 它报的 token 数)。

    状态：ok（正常回来了）/ too_long（吃不下）/ no_usage（回来了但没报用量）/
    unknown（别的失败——不猜）。
    """
    url = settings.embed_base_url().rstrip("/") + "/embeddings"
    payload = {"model": settings.embed_model(), "input": [text]}
    try:
        resp = requests.post(url, headers=_headers(), json=payload, timeout=_PROBE_TIMEOUT)
    except Exception:
        return "unknown", 0
    if resp.status_code != 200:
        # 只有这几种才当「吃不下」。401 / 403 是 key 不对、404 是地址不对、429 是限流——
        # 那些跟长度无关，要是也当成「太长」，就会把块长一路缩到最小，还一路沉默地缩下去。
        return ("too_long", 0) if resp.status_code in _TOO_LONG_CODES else ("unknown", 0)
    try:
        body = resp.json() or {}
    except ValueError:
        return "unknown", 0
    used = (body.get("usage") or {}).get("prompt_tokens")
    if not isinstance(used, int) or used <= 0:
        return "no_usage", 0
    return "ok", used


def _probe_once(candidate):
    """探一档，返回 (状态, 上限)。状态：ok / too_long / truncated / unknown。"""
    status, first = _ask(_probe_text(candidate))
    if status == "too_long":
        return "too_long", 0
    if status == "unknown":
        return "unknown", 0
    if status == "no_usage":
        # 没报用量就只能确定「这一档它没报错」，比不了长短
        return "ok", 0
    status, second = _ask(_probe_text(candidate * 3))
    if status == "ok" and second > first * 1.5:
        return "ok", first          # 三倍长的用量跟着涨 = 没掐我们的输入
    if status == "too_long":
        # 三倍那条被挡了，说明上限夹在这一档和三倍之间。光拿 first 当上限太保守了
        # （实测有个模型真实上限 512，这么算会算成 352，块白切小三分之一），
        # 所以在两者之间再二分几次，找最大的「它收下的」那个数。
        best = first
        low, high = candidate, candidate * 3
        for _ in range(_PROBE_STEPS):
            if high - low <= max(24, low // 8):
                break
            middle = (low + high) // 2
            state, used = _ask(_probe_text(middle))
            if state == "ok":
                low, best = middle, max(best, used or 0)
            elif state == "too_long":
                high = middle
            else:
                break               # 限流 / 网络抖动就别继续猜了
        return "truncated", max(1, int(best * _PROBE_MARGIN))
    # 静默截断：它把输入掐到了 first 这个数
    return "truncated", max(1, int(first * _PROBE_MARGIN))


def _probe():
    """从大到小试，返回 (上限 token, 给人看的说明, 探没探准)。上限 0 = 不限。

    第三项是给缓存指纹用的（见 kb/index.py 的 _chunk_params）：探测失败时那个 0 不是
    真的「不限」，只是没探出来，不能拿去算指纹——否则这次记 0、下次探成功记 512，
    缓存被判过期、整库向量白重算一遍。

    能一次探完就别多花请求：顶档过得去就直接收工——那是绝大多数情况（8K 窗口的模型
    到处都是，而我们的块最长才 580 token 左右）。
    """
    for candidate in _PROBE_LADDER:
        status, limit = _probe_once(candidate)
        if status == "unknown":
            return 0, "探不出来（接口没回应 / 返回看不懂），按不限处理", False
        if status == "ok":
            if candidate == _PROBE_TOP:
                return 0, f"模型吃得下 {_PROBE_TOP} token 以上，现有块长不用收口", True
            return candidate, f"模型上限约 {candidate} token，块长按它收口", True
        if status == "truncated":
            if limit >= _PROBE_FLOOR:
                return limit, f"模型上限约 {limit} token（按它自己报的用量算的），块长按它收口", True
            return 0, f"模型上限只有 {limit} token，小到没法切块了，按不限处理", True
        # too_long：这一档吃不下，往下试
    # 连最小那一档都发不出去，多半不是长度问题（key / 模型名 / 地址不对），
    # 那就别乱缩块长，按不限处理；这种也没探准，不进指纹
    return 0, f"连 {_PROBE_LADDER[-1]} token 都发不出去，看着不像长度问题（key / 模型名 / 地址？），按不限处理", False


def _probe_key():
    """一个进程只探一次：按「模型 / 维度 / 地址」区分（signature 已含这三样）。"""
    return signature()


def _ensure_probed():
    """没探过就探一次，结果记在进程里；探测本身绝不能把建索引搞挂。"""
    key = _probe_key()
    if key not in _probed:
        try:
            _probed[key] = _probe()
        except Exception as exc:
            _probed[key] = (0, f"探测出错（{type(exc).__name__}），按不限处理", False)
    return _probed[key]


def max_input_tokens():
    """单条输入最多多少 token。0 = 不限（后台填了就用填的，没填自动探一次）。

    探测要走网络，所以结果记在进程里，一个进程只探一遍；索引重建不会重复探。
    """
    configured = settings.embed_max_tokens()
    if configured > 0:
        return configured
    return _ensure_probed()[0]


def max_input_tokens_known():
    """这次的上限可不可信。

    后台填了、或者真探出来了 = 可信；探测没通 / 返回看不懂 = 不可信（那一刻的 0 只是
    「不知道」，不能拿去算缓存指纹，见 kb/index.py 的 _chunk_params）。
    """
    if settings.embed_max_tokens() > 0:
        return True
    return bool(_ensure_probed()[2])


def probe_note():
    """上一次探测的结论，给后台显示用。还没探过就是空串。"""
    if settings.embed_max_tokens() > 0:
        return f"按后台填的 {settings.embed_max_tokens()} token 收口（没走自动探测）"
    entry = _probed.get(_probe_key())
    return entry[1] if entry else ""


def _retry_delay(resp, attempt):
    """重试前等多久。限流优先听它 Retry-After 的，没给就指数退避（1.5 / 3 / 6 …秒）。"""
    if resp is not None:
        after = (getattr(resp, "headers", None) or {}).get("Retry-After")
        if after:
            try:
                return min(_RETRY_CAP, max(0.5, float(after)))
            except (TypeError, ValueError):
                pass                       # 有的是给日期而不是秒数，认不出来就退避
    return min(_RETRY_CAP, 1.5 * (2 ** max(0, attempt - 1)))


def _post(texts):
    """发一批，失败重试几次。返回 list[list[float]]，顺序跟 texts 一致。

    限流（429）单独多给几次、并且优先听它 Retry-After 的话：免费额度的向量接口很
    容易 429，而建全库要几千次请求，没点耐心根本建不完。真建不完也不怕——算好的文件
    都在缓存里，重启会接着没算完的往下走（见 index.py 的 build）。
    """
    url = settings.embed_base_url().rstrip("/") + "/embeddings"
    headers = _headers()
    # 不传 dimensions：有的服务商不认这个参数，传了反而 400。默认维度拿回来校验就行。
    payload = {"model": settings.embed_model(), "input": texts}
    want = EMBED_DIM
    last = ""
    attempt = 0
    limited = False

    while True:
        _progress["requests"] += 1
        resp = None
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"  # 连不上 / 超时，退避重试还有救
        except Exception as exc:
            # 地址写错、key 里混进非 ASCII 字符这类，重试多少次都一样，直接报出去
            raise EmbedError(
                f"向量化请求发不出去（{describe()}）：{type(exc).__name__}: {exc}"
            ) from exc
        else:
            if resp.status_code == 200:
                try:
                    rows = (resp.json() or {}).get("data") or []
                except ValueError as exc:
                    raise EmbedError(f"返回的不是 JSON（{describe()}）：{exc}") from exc
                if len(rows) != len(texts):
                    raise EmbedError(
                        f"返回条数对不上：要 {len(texts)} 条，回来 {len(rows)} 条（{describe()}）"
                    )
                rows.sort(key=lambda item: item.get("index", 0))  # 别信它给的顺序
                vectors = [item["embedding"] for item in rows]
                got = len(vectors[0])
                if got != want:
                    # 维度对不上是配置错（EMBED_DIM 没跟着模型改）：重试、重启都不会好，
                    # 用专门的异常往外报，别混进「限流、重启会接着算」那一类里误导用户
                    raise EmbedConfigError(
                        f"{settings.embed_model()} 返回的是 {got} 维，但 EMBED_DIM 配的是 {want} 维。"
                        f"把 EMBED_DIM 改成 {got}，或者删掉 .cache/ 重建索引"
                    )
                _progress["vectors"] += len(vectors)
                return vectors
            if resp.status_code == 400 and len(texts) > 1:
                # 多于一行的批次碰到 400，先当成「塞太多行了」——各家的上限不一样，
                # 报错措辞也五花八门，让上层砍半重发比猜文案稳。砍到只剩一行还 400，
                # 那就是这批内容本身有问题（比如单行 token 超长），如实报出去。
                raise _TooManyItems(f"HTTP 400: {resp.text[:200]}")
            if resp.status_code == 429:
                limited = True
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"

        attempt += 1
        if attempt >= (_RATE_RETRIES if limited else _RETRIES):
            break
        _progress["retries"] += 1
        time.sleep(_retry_delay(resp, attempt))

    _progress["failed"] += 1
    if limited:
        raise EmbedError(
            f"向量化一直被限流（{describe()}）：{last}。这是接口的速率限制，不是配置问题——"
            f"等几分钟重启服务，会接着没算完的文件往下算，已经算好的不会重来"
        )
    raise EmbedError(f"向量化失败（{describe()}）：{last}")


def embed_texts(texts):
    """把一批文本变成向量，顺序跟输入一致。

    接口嫌一次给的行太多就自动砍半重发，并把砍出来的安全值记下来给后面用。
    """
    global _batch
    texts = list(texts)
    if not texts:
        return []
    start = _batch          # 进来时的批大小，砍错了要还原
    while True:
        batches = [texts[i:i + _batch] for i in range(0, len(texts), _batch)]
        try:
            if len(batches) == 1:
                return _post(batches[0])
            # map 按输入顺序返回结果，正好保证跟 batches 对齐
            with ThreadPoolExecutor(max_workers=min(_WORKERS, len(batches))) as pool:
                return [vector for chunk in pool.map(_post, batches) for vector in chunk]
        except _TooManyItems:
            # _post 只在多行的批次上抛这个，正常砍下去一定会收敛；留个兜底免得死循环
            if _batch <= 1:
                _batch = start
                raise
            _batch = max(1, _batch // 2)
        except EmbedError:
            # 400 未必都是「行太多」——也可能是内容 / 模型名 / key。砍小了还报同样的错，
            # 就说明跟行数无关：把被误砍的批大小还原，别让一次内容性的 400 把整轮的
            # 批大小永久砍到 1（一进程只砍不还原，后面每批都只发 1 条，慢得离谱）。
            if _batch < start:
                _batch = start
            raise


def embed_query(text):
    """检索时把问题也转向量。"""
    return embed_texts([text])[0]
