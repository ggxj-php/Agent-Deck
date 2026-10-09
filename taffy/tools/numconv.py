"""进制 / 补码 / IEEE754 / 位运算换算，看寄存器值时常用。

op=base   进制互转（十进制 / 二进制 / 八进制 / 十六进制）
op=twos   补码：十进制 ↔ 指定位宽的补码位型
op=float  IEEE754 浮点：十进制 ↔ binary32 / binary64 的位型
op=bits   位运算：与或异或非、移位、置位 / 清位 / 取反某位 / 测某位

纯标准库实现，纯算术，不联网、不起子进程，跨平台。
"""
import math
import struct

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "number_convert",
            "description": (
                "进制 / 补码 / IEEE754 / 位运算换算，用 op 选算哪一样。\n"
                "op=base：进制互转。给 value（数写成 0x / 0b / 0o 前缀或者十进制），"
                "可选 from_base（不带前缀时按几进制读，可以是 2/8/10/16 或者 hex/bin/oct/dec），"
                "bits 默认 16，会按这个位宽给出补码位型。\n"
                "op=twos：补码换算。value 写十进制就是编码（给 bits 位宽，默认 16）；"
                "value 写成 0b/0x 或纯二进制串就当位型，解出它的有符号值。\n"
                "op=float：IEEE754。value 写十进制小数就编码成浮点位型，"
                "写 0x / 0b 就按位型解码成小数；precision 选 32（默认）或 64。\n"
                "op=bits：位运算。给 a，operation 选 and / or / xor / not / shl / shr / "
                "set / clear / toggle / test（也可以用 & | ^ ~ << >> 写），b 是第二个操作数，"
                "set/clear/toggle/test 时 b 是第几位（从 0 数起），shl/shr 时 b 是移几位。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["base", "twos", "float", "bits"],
                        "description": "算哪一样",
                    },
                    "value": {"type": "string", "description": "要换算的数，base / twos / float 用"},
                    "from_base": {"type": "string", "description": "value 不带前缀时按几进制读，默认 10"},
                    "bits": {"type": "integer", "description": "位宽，默认 16，twos / base 用"},
                    "precision": {"type": "integer", "description": "float 用，32 或 64，默认 32"},
                    "a": {"type": "string", "description": "位运算的第一个操作数，bits 用"},
                    "b": {"type": "string", "description": "位运算的第二个操作数（或位序号），bits 用"},
                    "operation": {"type": "string", "description": "位运算类型，bits 用"},
                },
                "required": ["op"],
            },
        },
    },
]

_BASE_NAMES = {
    "bin": 2, "binary": 2, "2": 2,
    "oct": 8, "octal": 8, "8": 8,
    "dec": 10, "decimal": 10, "10": 10,
    "hex": 16, "hexadecimal": 16, "16": 16,
}

_BIT_OPS = {
    "&": "and", "and": "and", "与": "and",
    "|": "or", "or": "or", "或": "or",
    "^": "xor", "xor": "xor", "异或": "xor",
    "~": "not", "not": "not", "非": "not",
    "<<": "shl", "shl": "shl", "左移": "shl",
    ">>": "shr", "shr": "shr", "右移": "shr",
    "set": "set", "置位": "set",
    "clear": "clear", "clr": "clear", "清位": "clear",
    "toggle": "toggle", "flip": "toggle", "取反": "toggle",
    "test": "test", "测位": "test",
}


def _int_value(text: str, default_base: int = 10) -> int:
    """按前缀（0x/0b/0o）或指定的进制把一个字符串读成整数。"""
    raw = str(text).strip().replace("_", "").replace(" ", "")
    if not raw:
        raise ValueError("没给数")
    sign = -1 if raw[:1] == "-" else 1
    if raw[:1] in "+-":
        raw = raw[1:]
    # 正负号只许有一个、而且必须写在最前面。「--5」这种去掉一个号以后剩下的还带符号，
    # 交给 int() 会读成 -5 再乘上外层的负号，符号正好反了（--5 变成 +5），所以这里直接拦掉。
    if any(char in "+-" for char in raw):
        raise ValueError(f"「{text}」不是合法的数字：正负号只能有一个，而且必须写在最前面")
    lowered = raw.lower()
    if lowered.startswith("0x"):
        base, digits = 16, raw[2:]
    elif lowered.startswith("0b"):
        base, digits = 2, raw[2:]
    elif lowered.startswith("0o"):
        base, digits = 8, raw[2:]
    else:
        base, digits = default_base, raw
    try:
        return sign * int(digits, base)
    except ValueError:
        raise ValueError(f"「{text}」不是合法的 {base} 进制数")


def _bit_width(value, default: int = 16) -> int:
    if value in (None, ""):
        return default
    try:
        width = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"bits 得是整数，收到的是 {value!r}")
    if not 1 <= width <= 64:
        raise ValueError(f"bits 要在 1~64 之间（收到 {width}）")
    return width


def _bit_operand(text: str) -> int:
    """位运算的操作数：带 0x/0b/0o 前缀按前缀读（允许前面的正负号）；只有 0-9 按十进制；含 a-f 就按十六进制。"""
    raw = str(text).strip().replace("_", "").replace(" ", "")
    if not raw:
        raise ValueError(f"「{text}」读不出数")
    sign = -1 if raw[:1] == "-" else 1
    body = raw[1:] if raw[:1] in "+-" else raw
    if not body:
        raise ValueError(f"「{text}」读不出数")
    lowered = body.lower()
    if lowered.startswith(("0x", "0b", "0o")):
        return sign * _int_value(body, 16)
    try:
        if any(char in "abcdefABCDEF" for char in body):
            return sign * int(body, 16)
        return sign * int(body, 10)
    except ValueError:
        raise ValueError(f"「{text}」读不出数")


def _group(value: int, width: int, marker: str) -> str:
    """按 4 位一组画位型，方便数位。"""
    digits = format(value & ((1 << width) - 1), f"0{width}b")
    grouped = []
    for start in range(0, len(digits), 4):
        grouped.append(digits[start:start + 4])
    return marker + "_".join(grouped)


def _base_op(args: dict) -> str:
    text = args.get("value")
    if text in (None, ""):
        return "base 要给 value"
    name = str(args.get("from_base", "10")).strip().lower()
    base = _BASE_NAMES.get(name)
    if base is None:
        return f"from_base 看不懂：{args.get('from_base')}，用 2/8/10/16 或者 bin/oct/dec/hex"
    try:
        value = _int_value(text, base)
    except ValueError as exc:
        return str(exc)

    width = _bit_width(args.get("bits"), 16)
    mask = (1 << width) - 1
    wrapped = value & mask
    signed = wrapped - (1 << width) if wrapped >> (width - 1) else wrapped

    lines = [
        f"输入：{str(text).strip()}",
        f"十进制：{value}",
        f"十六进制：0x{wrapped:X}",
        f"二进制：{_group(wrapped, width, '0b')}",
        f"八进制：0o{wrapped:o}",
        f"—— 按 {width} 位看 ——",
        f"无符号位型：0x{wrapped:0{max(1, width // 4)}X} = {_group(wrapped, width, '0b')}",
        f"按有符号（补码）解释：{signed}",
    ]
    if value > mask or value < -(1 << (width - 1)):
        lines.append(f"⚠ 原值 {value} 超出了 {width} 位能表示的范围，上面是截断后的位型")
    if value < 0:
        lines.append(f"（负数的位型就是它的 {width} 位补码）")
    return "\n".join(lines)


def _twos_op(args: dict) -> str:
    text = args.get("value")
    if text in (None, ""):
        return "twos 要给 value"
    width = _bit_width(args.get("bits"), 16)
    mask = (1 << width) - 1
    raw = str(text).strip().replace("_", "").replace(" ", "")
    looks_like_bits = raw.lower().startswith(("0x", "0b")) or (len(raw) == width and set(raw) <= {"0", "1"})

    if looks_like_bits:
        value = _int_value(raw, 2)
        wrapped = value & mask
        signed = wrapped - (1 << width) if wrapped >> (width - 1) else wrapped
        return "\n".join([
            f"输入位型：{_group(wrapped, width, '0b')}（0x{wrapped:X}）",
            f"按 {width} 位补码解释：{signed}",
            f"最高位（符号位）是 {wrapped >> (width - 1)}，"
            + ("所以是负数" if wrapped >> (width - 1) else "所以是正数"),
            f"无符号值：{wrapped}",
        ])

    try:
        value = _int_value(text, 10)
    except ValueError:
        return f"「{text}」既不像位型也不像十进制数"
    low, high = -(1 << (width - 1)), (1 << (width - 1)) - 1
    wrapped = value & mask
    lines = [
        f"输入：{value}（{width} 位）",
        f"补码位型：{_group(wrapped, width, '0b')}",
        f"十六进制：0x{wrapped:0{max(1, width // 4)}X}",
        f"无符号解释：{wrapped}",
    ]
    if not low <= value <= high:
        lines.append(f"⚠ {value} 超出了 {width} 位有符号数的范围（{low} ~ {high}），位型是按截断给的")
    else:
        lines.append(f"{width} 位有符号范围是 {low} ~ {high}，这里放得下")
    # raw 是原输入（已去掉下划线/空格）。纯 0/1 串和十进制在字面上分不清：同一串
    # 「11111111」在 bits=16 时走这条十进制路径，在 bits=8 时又被当成 8 位位型，语义跟着位宽漂。
    # 这里不改判定结果，只把这件事说明白，并给出「按位型读」的写法。
    if len(raw) > 1 and set(raw) <= {"0", "1"}:
        lines.append(f"提示：「{raw}」只由 0/1 组成，这里按十进制读成了 {value}、再按 {width} 位给位型"
                     f"（同一串换个 bits 可能就被当成位型，结果完全不同）。"
                     f"如果本意是二进制位型，请写成 0b{raw}，或者把 bits 设成 {len(raw)}")
    return "\n".join(lines)


def _float_fields(bits: int, precision: int):
    """拆出符号 / 阶码 / 尾数。"""
    if precision == 32:
        sign, exponent, fraction = bits >> 31 & 1, bits >> 23 & 0xFF, bits & 0x7FFFFF
        return sign, exponent, fraction, 8, 23, 127
    sign, exponent, fraction = bits >> 63 & 1, bits >> 52 & 0x7FF, bits & ((1 << 52) - 1)
    return sign, exponent, fraction, 11, 52, 1023


def _float_op(args: dict) -> str:
    text = args.get("value")
    if text in (None, ""):
        return "float 要给 value"
    try:
        precision = int(args.get("precision") or 32)
    except (TypeError, ValueError):
        return "precision 只能是 32 或 64"
    if precision not in (32, 64):
        return "precision 只能是 32 或 64"
    total = 32 if precision == 32 else 64
    pack_code = ">f" if precision == 32 else ">d"

    raw = str(text).strip()
    is_bits = raw.lower().startswith(("0x", "0b"))

    if is_bits:
        try:
            bits = _int_value(raw, 2) & ((1 << total) - 1)
        except ValueError as exc:
            return str(exc)
        value = struct.unpack(pack_code, bits.to_bytes(total // 8, "big"))[0]
        layout = format(bits, f"0{total}b")
        sign, exponent, fraction, exp_bits, frac_bits, bias = _float_fields(bits, precision)
        lines = [
            f"位型：0x{bits:0{total // 4}X}",
            f"二进制：{_group(bits, total, '0b')}",
            f"  符号位 {layout[0]}｜阶码 {layout[1:1 + exp_bits]}｜尾数 {layout[1 + exp_bits:]}",
            f"解析成小数：{value!r}",
        ]
        if exponent == 0:
            lines.append("阶码全 0：这是零或者非规格化数")
        elif exponent == (1 << exp_bits) - 1:
            lines.append("阶码全 1：这是无穷大（尾数为 0）或者 NaN（尾数不为 0）")
        else:
            lines.append(f"实际阶码 = {exponent} − {bias} = {exponent - bias}，"
                         f"尾数隐含一个前导 1 → 有效数字 ≈ {1 + fraction / (1 << frac_bits):.10f}")
        return "\n".join(lines)

    try:
        value = float(raw)
    except ValueError:
        return f"「{text}」既不是十进制小数，也不是 0x / 0b 开头的位型"
    seconds = 3.4e38 if precision == 32 else 1.7e308
    try:
        packed = struct.pack(pack_code, value)
    except OverflowError:
        return (f"{raw} 超出了 {precision} 位浮点的量程（最大约 {seconds:.1e}），"
                "存进去会变成 inf；要精确点就用 64 位")
    bits = int.from_bytes(packed, "big")
    back = struct.unpack(pack_code, packed)[0]
    layout = format(bits, f"0{total}b")
    sign, exponent, fraction, exp_bits, frac_bits, bias = _float_fields(bits, precision)
    if exponent == 0:
        exp_line = "阶码 = 0（零或非规格化数，没有隐含的前导 1）"
    elif exponent == (1 << exp_bits) - 1:
        exp_line = f"阶码 = {exponent}（全 1：无穷大或 NaN，没有常规指数）"
    else:
        exp_line = f"阶码 = {exponent}（实际指数 {exponent - bias}）"
    lines = [
        f"输入：{raw}（{precision} 位浮点）",
        f"位型：0x{bits:0{total // 4}X}",
        f"二进制：{_group(bits, total, '0b')}",
        f"  符号位 {layout[0]}｜阶码 {layout[1:1 + exp_bits]}｜尾数 {layout[1 + exp_bits:]}",
        exp_line,
        f"还原成小数：{back!r}",
    ]
    if back != value and math.isfinite(value) and math.isfinite(back):
        lines.append(f"注意：浮点存不进精确值，还原后差了 {back - value:.6e}（比较浮点别用 ==）")
    return "\n".join(lines)


def _bits_op(args: dict) -> str:
    operand = args.get("a")
    if operand in (None, ""):
        return "bits 要给 a"
    key = str(args.get("operation", "and")).strip().lower()
    op = _BIT_OPS.get(key)
    if op is None:
        return (f"不认识的 operation：{args.get('operation')}。可选 "
                + "、".join(sorted(set(_BIT_OPS.values()))))
    try:
        left = _bit_operand(operand)
    except ValueError as exc:
        return f"a 读不出来：{exc}"

    second = args.get("b")
    try:
        right = None if second in (None, "") else _bit_operand(second)
    except ValueError as exc:
        return f"b 读不出来：{exc}"

    if op in ("and", "or", "xor") and right is None:
        return f"{op} 需要 b"
    if op in ("shl", "shr") and right is None:
        return f"{op} 需要 b（移几位）"
    if op in ("set", "clear", "toggle", "test") and right is None:
        return f"{op} 需要 b（第几位，从 0 数起）"

    width = max(16, left.bit_length(), (right.bit_length() if right is not None and right >= 0 else 0))

    if op == "and":
        result, formula = left & right, f"{left:#x} & {right:#x}"
    elif op == "or":
        result, formula = left | right, f"{left:#x} | {right:#x}"
    elif op == "xor":
        result, formula = left ^ right, f"{left:#x} ^ {right:#x}"
    elif op == "not":
        result, formula = ~left, f"~{left:#x}"
    elif op == "shl":
        result, formula = left << right, f"{left:#x} << {right}"
    elif op == "shr":
        result, formula = left >> right, f"{left:#x} >> {right}"
    elif op == "set":
        result, formula = left | (1 << right), f"{left:#x} | (1 << {right})"
    elif op == "clear":
        result, formula = left & ~(1 << right), f"{left:#x} & ~(1 << {right})"
    elif op == "toggle":
        result, formula = left ^ (1 << right), f"{left:#x} ^ (1 << {right})"
    else:
        bit = (left >> right) & 1
        return "\n".join([
            f"a = 0x{left & ((1 << width) - 1):X}（{_group(left, width, '0b')}）",
            f"第 {right} 位 = {bit}（{'是 1，置位了' if bit else '是 0，没置位'}）",
        ])

    width = max(width, result.bit_length() + (1 if result < 0 else 0))
    lines = [
        f"计算：{formula}",
        f"结果：{result}（0x{result & ((1 << width) - 1):X}）",
        f"二进制：{_group(result, width, '0b')}（按 {width} 位看）",
    ]
    if result < 0:
        lines.append(f"结果按 64 位补码看是 0x{result & ((1 << 64) - 1):X}（~ 和负数右移都会这样）")
    return "\n".join(lines)


_OPS = {"base": _base_op, "twos": _twos_op, "float": _float_op, "bits": _bits_op}


def number_convert(op: str = "", **kwargs) -> str:
    """按 op 分发到对应的换算函数。"""
    key = (op or "").strip().lower()
    if key not in _OPS:
        return f"不认识的 op：{op or '(空)'}。可选 " + "、".join(_OPS)
    try:
        return _OPS[key](kwargs)
    except ValueError as exc:
        return f"参数不对：{exc}"
