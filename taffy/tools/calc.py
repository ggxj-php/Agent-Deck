"""超高精度科学计算器：一次调用把表达式算到几十上百位有效数字。

复杂运算不用再 write_file + run_code 绕一圈，直接丢表达式进来就行。
用标准库的 decimal 做算术，三角函数/π 这些 decimal 没带的自己按级数算，
整数的加减乘幂走 Python 大整数，一位不差。纯算术，不联网、不起子进程，跨平台。
"""
import ast
import decimal
import math
import re
from decimal import Decimal, localcontext, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR

DEFAULT_PRECISION = 50
MAX_PRECISION = 1000
MAX_DIGITS = 1500          # 单个结果最多这么多位，再大就不算了
MAX_EXPRESSIONS = 20
MAX_EXPRESSION = 2000      # 表达式本身最长多少字符
OUTPUT_BUDGET = 6000       # 整段结果最多输出多少字符

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "sci_calc",
            "description": (
                "高精度科学计算器：丢一个数学表达式进去，按指定的有效数字位数算出来，"
                "几十上百位都行（默认 50 位）。复杂运算直接用这个，不用再写代码跑一遍。\n"
                "支持：+ - * / // % ** 和括号（整数之间的加减乘幂是精确大数，2**100 一位不差）；"
                "常量 pi / e / tau / phi；"
                "sqrt / cbrt、exp、ln / log(x[,底]) / lg / log2、"
                "sin cos tan asin acos atan atan2（按弧度）、sinh cosh tanh / asinh acosh atanh、"
                "floor / ceil / trunc / round(x[,位])、abs / sign / min / max / mod / hypot、"
                "degrees / radians、factorial / gcd / lcm / comb(n,k) / perm(n,k) / isqrt。\n"
                "一次可以给多个式子：换行或分号隔开，逐个算。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "数学表达式，如 sqrt(2)*1e6、sin(pi/6)、1/7；多个式子用换行或分号隔开",
                    },
                    "precision": {
                        "type": "integer",
                        "description": "有效数字位数，默认 50，最多 1000",
                    },
                },
                "required": ["expression"],
            },
        },
    },
]


# ---------------- π 和高精度三角函数 ----------------

_PI_CACHE = {}


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(value)


def _atan_small(x: Decimal, prec: int) -> Decimal:
    """atan 的泰勒级数，只喂 |x| ≤ 0.05 这种小量，收敛很快。"""
    with localcontext() as ctx:
        ctx.prec = prec
        x2 = x * x
        term = x
        total = x
        n = 1
        limit = Decimal(1).scaleb(-prec - 5)
        for _ in range(prec * 4 + 100):
            term = -term * x2
            n += 2
            piece = term / n
            total += piece
            if abs(piece) < limit:
                break
        return +total


def _atan(x: Decimal, prec: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = prec + 10
        if x < 0:
            return -_atan(-x, prec)
        if not x:
            return Decimal(0)
        if x > 1:
            # atan(x) = π/2 − atan(1/x)，把参数压到 1 以内
            return +(_pi(prec + 10) / 2 - _atan(1 / x, prec))
        half = 0
        while x > Decimal("0.05"):
            # atan(x) = 2·atan(x / (1 + √(1+x²)))，反复折半把参数压小
            x = x / (1 + (1 + x * x).sqrt())
            half += 1
        return +(_atan_small(x, prec + 10) * (1 << half))


def _pi(prec: int) -> Decimal:
    """Machin 公式 16·atan(1/5) − 4·atan(1/239)，想要多少位算多少位。"""
    if prec not in _PI_CACHE:
        with localcontext() as ctx:
            ctx.prec = prec + 15
            _PI_CACHE[prec] = (16 * _atan_small(Decimal(1) / Decimal(5), ctx.prec)
                               - 4 * _atan_small(Decimal(1) / Decimal(239), ctx.prec))
    return _PI_CACHE[prec]


def _sin_cos(x: Decimal, prec: int):
    """一起把 sin 和 cos 算出来，归约到 [−π, π] 之后套泰勒级数。"""
    with localcontext() as ctx:
        ctx.prec = prec + 10
        pi = _pi(prec + 10)
        two_pi = 2 * pi
        # 先按 2π 取余，级数才不会因为参数太大而不收敛
        x = x - (x / two_pi).to_integral_value(rounding=ROUND_FLOOR) * two_pi
        if x > pi:
            x -= two_pi
        x2 = x * x
        limit = Decimal(1).scaleb(-prec - 5)
        term_s = sin_total = x
        term_c = cos_total = Decimal(1)
        n = 0
        for _ in range(prec * 2 + 50):
            # sin 的下一项还要多乘一次 x²，所以分母比 cos 的大两阶
            term_s = -term_s * x2 / ((n + 2) * (n + 3))
            term_c = -term_c * x2 / ((n + 1) * (n + 2))
            sin_total += term_s
            cos_total += term_c
            n += 2
            if abs(term_s) < limit and abs(term_c) < limit:
                break
        return +sin_total, +cos_total


def _asin(x: Decimal, prec: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = prec + 10
        if abs(x) > 1:
            raise ValueError("asin / acos 的参数要在 −1 到 1 之间")
        if abs(x) == 1:
            half_pi = _pi(prec + 10) / 2
            return +half_pi if x > 0 else -half_pi
        return +_atan(x / (1 - x * x).sqrt(), prec)


def _sinh_cosh(x: Decimal, prec: int):
    with localcontext() as ctx:
        ctx.prec = prec + 10
        ex = x.exp()
        inv = 1 / ex
        return +((ex - inv) / 2), +((ex + inv) / 2)


# ---------------- 单次求值的小工具 ----------------

def _arity(name, values, lo, hi=None):
    hi = lo if hi is None else hi
    if not lo <= len(values) <= hi:
        want = f"{lo}" if lo == hi else f"{lo}~{hi}"
        raise ValueError(f"{name} 要 {want} 个参数，给了 {len(values)} 个")


def _digits_of(n: int) -> int:
    return len(str(n))


def _guard_digits(estimated: float, what: str = "结果"):
    # estimated 是「位数上界」：整数幂那套 log10 估法本身多加 1（个别调用方不加），
    # 再留 1e-6 位余量吸收 log10 的浮点毛刺——log10(10**1500) 会给出 1500.0000000000002，
    # 不减掉这个偏移就会把正好卡在上限的式子误拒（10**751、10**1500 都栽在这儿）
    if estimated - 1 > MAX_DIGITS + 1e-6:
        raise ValueError(f"{what}大约有 {int(estimated)} 位，超过 {MAX_DIGITS} 位就不算了"
                         "（太长也没人看）；要算就先缩小范围")


def _power(base, exp, prec: int):
    if isinstance(base, int) and isinstance(exp, int) and exp >= 0:
        if base in (0, 1, -1):          # 这几个无论多少次方都很快
            return base ** exp
        # 位数要按对数估：底数 ≥ 10 时它的位数会「进位」，直接乘底数位数会把
        # 10**751 估成 1502 位这种两倍偏差，把本来算得出来的式子误拒掉
        _guard_digits(exp * math.log10(abs(base)) + 1)
        return base ** exp
    b, e = _dec(base), _dec(exp)
    if e.is_zero():
        return Decimal(1)               # 任何数的 0 次方都是 1（0**0 也算 1）
    if b.is_zero():
        if e < 0:
            raise ValueError("0 不能算负次方（相当于除以 0）")
        return Decimal(0)               # 0 的正次方还是 0
    if b < 0 and e != e.to_integral_value():
        raise ValueError("负数的非整数次方在实数里没有结果")
    try:
        estimated = float(e) * float(abs(b).log10())
    except (OverflowError, ValueError):
        estimated = 0
    _guard_digits(estimated)
    with localcontext() as ctx:
        ctx.prec = prec + 10
        return +(b ** e)


def _to_integral(value, rounding, what: str):
    if isinstance(value, int):
        return value
    if not value.is_finite():
        raise ValueError(f"{what} 的要求是有限数")
    return int(value.to_integral_value(rounding=rounding))


def _need_int(name, value):
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    raise ValueError(f"{name} 只吃整数，收到的是 {value}")


# ---------------- 函数表 ----------------

def _fn_sqrt(values, prec):
    _arity("sqrt", values, 1)
    x = _dec(values[0])
    if x < 0:
        raise ValueError("sqrt 的参数不能是负数")
    return x.sqrt()


def _fn_cbrt(values, prec):
    _arity("cbrt", values, 1)
    x = _dec(values[0])
    if x.is_zero():
        return Decimal(0)
    target = abs(x)
    with localcontext() as ctx:
        ctx.prec = prec + 10
        root = target ** (Decimal(1) / 3)     # 粗略初值（1/3 本身有误差）
        for _ in range(4):                    # 牛顿迭代把精度补满
            root = (2 * root + target / (root * root)) / 3
        root = +root
    # 完全立方数就直接给整数，免得 8 开出来是 2.000…0
    nearest = root.to_integral_value(rounding=decimal.ROUND_HALF_EVEN)
    if nearest != 0 and nearest * nearest * nearest == target:
        return -nearest if x < 0 else +nearest
    return -root if x < 0 else root


def _fn_exp(values, prec):
    _arity("exp", values, 1)
    x = _dec(values[0])
    if x > 4605:                        # e^4605 就已经一千多位了
        _guard_digits(float(x) / math.log(10))
    return x.exp()


def _fn_ln(values, prec):
    _arity("ln", values, 1)
    x = _dec(values[0])
    if x <= 0:
        raise ValueError("ln 的参数要大于 0")
    return x.ln()


def _fn_log(values, prec):
    _arity("log", values, 1, 2)
    x = _dec(values[0])
    if x <= 0:
        raise ValueError("log 的参数要大于 0")
    if len(values) == 1:
        return x.ln()
    base = _dec(values[1])
    if base <= 0 or base == 1:
        raise ValueError("log 的底数要大于 0 且不等于 1")
    return x.ln() / base.ln()


def _fn_lg(values, prec):
    _arity("lg", values, 1)
    x = _dec(values[0])
    if x <= 0:
        raise ValueError("lg 的参数要大于 0")
    return x.log10()


def _fn_log2(values, prec):
    _arity("log2", values, 1)
    x = _dec(values[0])
    if x <= 0:
        raise ValueError("log2 的参数要大于 0")
    return x.ln() / Decimal(2).ln()


def _fn_sin(values, prec):
    _arity("sin", values, 1)
    return _sin_cos(_dec(values[0]), prec)[0]


def _fn_cos(values, prec):
    _arity("cos", values, 1)
    return _sin_cos(_dec(values[0]), prec)[1]


def _fn_tan(values, prec):
    _arity("tan", values, 1)
    sin_v, cos_v = _sin_cos(_dec(values[0]), prec)
    if cos_v.is_zero():
        raise ValueError("tan 在这个点上没有定义（cos 正好是 0）")
    return +(sin_v / cos_v)


def _fn_asin(values, prec):
    _arity("asin", values, 1)
    return _asin(_dec(values[0]), prec)


def _fn_acos(values, prec):
    _arity("acos", values, 1)
    x = _dec(values[0])
    with localcontext() as ctx:
        ctx.prec = prec + 10
        return +(_pi(prec + 10) / 2 - _asin(x, prec))


def _fn_atan(values, prec):
    _arity("atan", values, 1)
    return _atan(_dec(values[0]), prec)


def _fn_atan2(values, prec):
    _arity("atan2", values, 2)
    y, x = _dec(values[0]), _dec(values[1])
    with localcontext() as ctx:
        ctx.prec = prec + 10
        pi = _pi(prec + 10)
        if x > 0:
            return +_atan(y / x, prec)
        if x < 0:
            return +(_atan(y / x, prec) + (pi if y >= 0 else -pi))
        if y > 0:
            return +pi / 2
        if y < 0:
            return -pi / 2
        raise ValueError("atan2(0, 0) 没有定义")


def _fn_sinh(values, prec):
    _arity("sinh", values, 1)
    return _sinh_cosh(_dec(values[0]), prec)[0]


def _fn_cosh(values, prec):
    _arity("cosh", values, 1)
    return _sinh_cosh(_dec(values[0]), prec)[1]


def _fn_tanh(values, prec):
    _arity("tanh", values, 1)
    s, c = _sinh_cosh(_dec(values[0]), prec)
    return +(s / c)


def _fn_asinh(values, prec):
    _arity("asinh", values, 1)
    x = _dec(values[0])
    with localcontext() as ctx:
        ctx.prec = prec + 10
        return +((x + (x * x + 1).sqrt()).ln())


def _fn_acosh(values, prec):
    _arity("acosh", values, 1)
    x = _dec(values[0])
    if x < 1:
        raise ValueError("acosh 的参数要不小于 1")
    with localcontext() as ctx:
        ctx.prec = prec + 10
        return +((x + (x * x - 1).sqrt()).ln())


def _fn_atanh(values, prec):
    _arity("atanh", values, 1)
    x = _dec(values[0])
    if abs(x) >= 1:
        raise ValueError("atanh 的参数要在 −1 到 1 之间（开区间）")
    with localcontext() as ctx:
        ctx.prec = prec + 10
        return +(((1 + x) / (1 - x)).ln() / 2)


def _fn_abs(values, prec):
    _arity("abs", values, 1)
    return abs(values[0])


def _fn_sign(values, prec):
    _arity("sign", values, 1)
    x = _dec(values[0])
    return 0 if x == 0 else (1 if x > 0 else -1)


def _fn_floor(values, prec):
    _arity("floor", values, 1)
    return _to_integral(values[0], ROUND_FLOOR, "floor")


def _fn_ceil(values, prec):
    _arity("ceil", values, 1)
    return _to_integral(values[0], ROUND_CEILING, "ceil")


def _fn_trunc(values, prec):
    _arity("trunc", values, 1)
    return _to_integral(values[0], ROUND_DOWN, "trunc")


def _fn_round(values, prec):
    _arity("round", values, 1, 2)
    x = values[0]
    digits = _need_int("round 的第二个参数（保留几位小数）", values[1]) if len(values) == 2 else 0
    if not -prec * 2 <= digits <= prec * 2:
        raise ValueError("round 要保留的位数太夸张了")
    if isinstance(x, int):
        if digits >= 0:
            return x
        factor = 10 ** (-digits)
        quotient, remainder = divmod(abs(x), factor)
        if 2 * remainder >= factor:          # 四舍五入（不是 Python 默认的半偶舍入）
            quotient += 1
        return (quotient if x >= 0 else -quotient) * factor
    with localcontext() as ctx:
        # quantize 要求结果的系数位宽不超过上下文精度，而全局上下文只有 prec+10 位，
        # 大数取整就会抛 InvalidOperation（再被 _decimal_hint 误说成「实数里没有结果」）；
        # 按「整数部分位数 + 保留位数」临时抬高本次精度，不动全局
        ctx.prec = max(ctx.prec, x.adjusted() + 1 + max(digits, 0))
        rounded = x.quantize(Decimal(1).scaleb(-digits), rounding=decimal.ROUND_HALF_UP)
    return int(rounded) if digits <= 0 else +rounded


def _fn_min(values, prec):
    _arity("min", values, 1, 64)
    return min(values)


def _fn_max(values, prec):
    _arity("max", values, 1, 64)
    return max(values)


def _fn_mod(values, prec):
    _arity("mod", values, 2)
    a, b = values
    if _dec(b) == 0:
        raise ValueError("mod 的除数不能是 0")
    if isinstance(a, int) and isinstance(b, int):
        return a % b
    return _dec(a) - (_dec(a) / _dec(b)).to_integral_value(rounding=ROUND_FLOOR) * _dec(b)


def _fn_hypot(values, prec):
    _arity("hypot", values, 1, 64)
    total = Decimal(0)
    for item in values:
        item = _dec(item)
        total += item * item
    return total.sqrt()


def _fn_degrees(values, prec):
    _arity("degrees", values, 1)
    with localcontext() as ctx:
        ctx.prec = prec + 10
        return +(_dec(values[0]) * 180 / _pi(prec + 10))


def _fn_radians(values, prec):
    _arity("radians", values, 1)
    with localcontext() as ctx:
        ctx.prec = prec + 10
        return +(_dec(values[0]) * _pi(prec + 10) / 180)


def _fn_factorial(values, prec):
    _arity("factorial", values, 1)
    n = _need_int("factorial", values[0])
    if n < 0:
        raise ValueError("factorial 的参数不能是负数")
    if n > 2:
        _guard_digits(n * math.log10(n / math.e) + 0.5 * math.log10(2 * math.pi * n) + 1)
    return math.factorial(n)


def _fn_gcd(values, prec):
    _arity("gcd", values, 2, 64)
    return math.gcd(*[_need_int("gcd", v) for v in values])


def _fn_lcm(values, prec):
    _arity("lcm", values, 2, 64)
    return math.lcm(*[_need_int("lcm", v) for v in values])


def _fn_comb(values, prec):
    _arity("comb", values, 2)
    n, k = _need_int("comb", values[0]), _need_int("comb", values[1])
    if n < 0 or k < 0:
        raise ValueError("comb 的两个参数都不能是负数")
    if k <= n:
        # 直接估 C(n,k) 的位数，别拿 n! 的位数去估（那个会大出好几个数量级）
        digits = (math.lgamma(n + 1) - math.lgamma(k + 1)
                  - math.lgamma(n - k + 1)) / math.log(10)
        _guard_digits(digits)
    return math.comb(n, k)


def _fn_perm(values, prec):
    _arity("perm", values, 2)
    n, k = _need_int("perm", values[0]), _need_int("perm", values[1])
    if n < 0 or k < 0:
        raise ValueError("perm 的两个参数都不能是负数")
    if k <= n:
        digits = (math.lgamma(n + 1) - math.lgamma(n - k + 1)) / math.log(10)
        _guard_digits(digits)
    return math.perm(n, k)


def _fn_isqrt(values, prec):
    _arity("isqrt", values, 1)
    n = _need_int("isqrt", values[0])
    if n < 0:
        raise ValueError("isqrt 的参数不能是负数")
    return math.isqrt(n)


def _fn_pow(values, prec):
    _arity("pow", values, 2)
    return _power(values[0], values[1], prec)


_FUNCS = {
    "sqrt": _fn_sqrt, "cbrt": _fn_cbrt,
    "exp": _fn_exp, "ln": _fn_ln, "log": _fn_log, "lg": _fn_lg, "log10": _fn_lg,
    "log2": _fn_log2,
    "sin": _fn_sin, "cos": _fn_cos, "tan": _fn_tan,
    "asin": _fn_asin, "acos": _fn_acos, "atan": _fn_atan, "atan2": _fn_atan2,
    "sinh": _fn_sinh, "cosh": _fn_cosh, "tanh": _fn_tanh,
    "asinh": _fn_asinh, "acosh": _fn_acosh, "atanh": _fn_atanh,
    "abs": _fn_abs, "sign": _fn_sign, "sgn": _fn_sign,
    "floor": _fn_floor, "ceil": _fn_ceil, "trunc": _fn_trunc, "round": _fn_round,
    "min": _fn_min, "max": _fn_max, "mod": _fn_mod, "hypot": _fn_hypot,
    "degrees": _fn_degrees, "radians": _fn_radians,
    "factorial": _fn_factorial, "gcd": _fn_gcd, "lcm": _fn_lcm,
    "comb": _fn_comb, "perm": _fn_perm, "nCr": _fn_comb, "nPr": _fn_perm,
    "isqrt": _fn_isqrt, "pow": _fn_pow,
}

_CONSTS = {
    "pi": lambda prec: _pi(prec + 10),
    "tau": lambda prec: 2 * _pi(prec + 10),
    "e": lambda prec: Decimal(1).exp(),
    "phi": lambda prec: (1 + Decimal(5).sqrt()) / 2,
}

_FUNC_NAMES = " / ".join(sorted(set(_FUNCS)))


# ---------------- 表达式求值 ----------------

def _literal(node, source):
    """数字字面量按原文读，别先过一遍二进制浮点（0.1 会变成 0.1000000000000000055…）。"""
    text = ast.get_source_segment(source, node)
    text = (text if text is not None else repr(node.value)).strip().replace("_", "")
    if isinstance(node.value, bool):
        raise ValueError("表达式里不能有 True / False")
    # 显式挡掉 0x / 0b / 0o：不挡的话 int() 会抛出英文的 Python 原生异常文本，
    # 而且「看得懂就顺手支持」会让同一个式子写法不同结果不同，不如直接说清楚
    if re.fullmatch(r"[+-]?0[xXbBoO][0-9a-fA-F]+", text):
        raise ValueError("不支持 0x / 0b / 0o 进制字面量，请写成十进制喵")
    if isinstance(node.value, int):
        return int(text)
    if isinstance(node.value, float):
        return Decimal(text)
    raise ValueError(f"看不懂这个写法：{text}")


def _binop(op, left, right, prec):
    if isinstance(op, ast.Add):
        return left + right
    if isinstance(op, ast.Sub):
        return left - right
    if isinstance(op, ast.Mult):
        return left * right
    if isinstance(op, ast.Div):
        # 两个整数除得尽就还它一个整数，除不尽才落到高精度小数
        if isinstance(left, int) and isinstance(right, int) and right and left % right == 0:
            return left // right
        return _dec(left) / _dec(right)
    if isinstance(op, ast.FloorDiv):
        if isinstance(left, int) and isinstance(right, int):
            return left // right
        return _to_integral(_dec(left) / _dec(right), ROUND_FLOOR, "整除")
    if isinstance(op, ast.Mod):
        if isinstance(left, int) and isinstance(right, int):
            return left % right
        return _fn_mod([left, right], prec)
    if isinstance(op, ast.Pow):
        return _power(left, right, prec)
    raise ValueError("不支持这个运算符")


def _eval(node, source, prec):
    if isinstance(node, ast.Expression):
        return _eval(node.body, source, prec)
    if isinstance(node, ast.Constant):
        return _literal(node, source)
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, source, prec)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return value
        raise ValueError("不支持这个一元运算")
    if isinstance(node, ast.BinOp):
        return _binop(node.op,
                      _eval(node.left, source, prec),
                      _eval(node.right, source, prec),
                      prec)
    if isinstance(node, ast.Name):
        if node.id not in _CONSTS:
            raise ValueError(f"不认识 {node.id}，常量只有 pi / e / tau / phi")
        return _CONSTS[node.id](prec)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("只能调用工具里自带的函数")
        if node.keywords:
            raise ValueError("函数不支持写关键字参数，参数按位置给就行")
        if any(isinstance(arg, ast.Starred) for arg in node.args):
            raise ValueError("函数参数不能写成 *args 展开")
        name = node.func.id
        if name not in _FUNCS:
            raise ValueError(f"没有 {name} 这个函数；能用的有 {_FUNC_NAMES}")
        values = [_eval(arg, source, prec) for arg in node.args]
        return _FUNCS[name](values, prec)
    raise ValueError("表达式里只能用数字、pi / e / tau / phi、+ - * / // % **、括号和自带的函数"
                     "（属性访问、下标、推导式、赋值这些都不支持）")


def _round_to(value, prec):
    if isinstance(value, int):
        return value
    with localcontext() as ctx:
        ctx.prec = prec
        return +value


def _render(value, prec):
    if isinstance(value, int):
        return str(value)
    if value.is_nan():
        return "不是个数（NaN）"
    if value.is_infinite():
        return "∞（超出范围了）" if value > 0 else "−∞（超出范围了）"
    if value.is_zero():
        return "0"
    exponent = value.adjusted()
    if -7 <= exponent <= 30:
        return format(value, "f")
    mantissa, _, exp_text = format(value, "E").partition("E")
    return f"{mantissa}×10^{int(exp_text)}"


def _decimal_hint(exc):
    if isinstance(exc, decimal.DivisionByZero):
        return "除数不能是 0"
    if isinstance(exc, decimal.InvalidOperation):
        return "这个运算在实数里没有结果"
    if isinstance(exc, decimal.Overflow):
        return "结果太大了，超出能算的范围"
    return str(exc)


# ---------------- 入口 ----------------

def sci_calc(expression: str = "", precision: int = DEFAULT_PRECISION) -> str:
    """把表达式算到指定精度，返回可读的结果文本。"""
    text = (expression or "").strip()
    if not text:
        return "给我个表达式呀，比如 (1+2)**100 / sqrt(3)、sin(pi/6)、1/7 喵"
    if len(text) > MAX_EXPRESSION:
        return f"表达式太长了（{len(text)} 字符，最多 {MAX_EXPRESSION}）"
    try:
        prec = int(precision)
    except (TypeError, ValueError):
        return "precision 得是整数（有效数字位数）"
    if not 1 <= prec <= MAX_PRECISION:
        return f"precision 要在 1 到 {MAX_PRECISION} 之间"

    pieces = [p.strip() for p in re.split(r"[\n;]", text) if p.strip()]
    if len(pieces) > MAX_EXPRESSIONS:
        pieces = pieces[:MAX_EXPRESSIONS]

    lines = []
    has_decimal = False
    for piece in pieces:
        try:
            tree = ast.parse(piece, mode="eval")
            with localcontext() as ctx:
                ctx.prec = prec + 10
                value = _eval(tree, piece, prec)
            value = _round_to(value, prec)
        except SyntaxError as e:
            lines.append(f"{piece}   ← 读不懂：{e.msg}")
            continue
        except ValueError as e:
            lines.append(f"{piece}   ← 算不了：{e}")
            continue
        except decimal.DecimalException as e:
            lines.append(f"{piece}   ← 算不了：{_decimal_hint(e)}")
            continue
        except ZeroDivisionError:
            lines.append(f"{piece}   ← 算不了：除数不能是 0")
            continue
        except (OverflowError, MemoryError):
            lines.append(f"{piece}   ← 算不了：数太大撑爆了")
            continue
        except RecursionError:
            lines.append(f"{piece}   ← 算不了：表达式套得太深了")
            continue
        if isinstance(value, Decimal):
            has_decimal = True
        lines.append(f"{piece} = {_render(value, prec)}")

    if has_decimal:
        lines.append(f"（按 {prec} 位有效数字算的；三角函数按弧度）")

    output = "\n".join(lines)
    if len(output) > OUTPUT_BUDGET:
        output = output[:OUTPUT_BUDGET] + "\n…（结果太长，先给到这儿）"
    return output
