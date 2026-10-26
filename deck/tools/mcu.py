"""单片机 / 嵌入式常用计算：波特率、定时器、ADC、分压、PWM、I2C 地址、校验、RC。

这些都是调外设时天天要算的东西，手算容易错，交给工具算比较稳。纯标准库、纯算术，
不联网、不起子进程，跨平台。
"""
import math
import zlib

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "mcu_calc",
            "description": (
                "单片机 / 嵌入式外设参数计算器，用 op 选算哪一样（参数按 op 给）："
                "uart 串口波特率分频（f_cpu/baud，oversampling 默认 16）、"
                "timer 定时器周期与 ARR（f_cpu/prescaler，给 arr 或 period_us 反推）、"
                "adc 码值↔电压（bits 默认 12、vref 默认 3.3）、"
                "divider 电阻分压、pwm 占空比、i2c 7↔8 位地址、"
                "crc 校验和（crc8 / crc16-modbus / crc16-ccitt / crc16-xmodem 等）、rc RC 时间常数。"
                "返回中间值、实际值与误差（波特率误差超 2% 会提示）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["uart", "timer", "adc", "divider", "pwm", "i2c", "crc", "rc"],
                        "description": "算哪一样",
                    },
                    "f_cpu": {"type": "number", "description": "主频 Hz（uart/timer/pwm）"},
                    "baud": {"type": "number", "description": "波特率（uart）"},
                    "oversampling": {"type": "integer", "description": "16 或 8，默认 16（uart）"},
                    "prescaler": {"type": "number", "description": "预分频（timer/pwm）"},
                    "arr": {"type": "number", "description": "自动重装值（timer/pwm）"},
                    "ccr": {"type": "number", "description": "比较值（pwm）"},
                    "duty_pct": {"type": "number", "description": "占空比 %（pwm）"},
                    "period_us": {"type": "number", "description": "周期，微秒（timer）"},
                    "freq_hz": {"type": "number", "description": "频率 Hz（timer）"},
                    "bits": {"type": "integer", "description": "ADC 位数，默认 12"},
                    "vref": {"type": "number", "description": "ADC 参考电压，默认 3.3"},
                    "value": {"type": "number", "description": "原始码值（adc）"},
                    "voltage": {"type": "number", "description": "电压（adc）"},
                    "vin": {"type": "number", "description": "输入电压（divider）"},
                    "r1": {"type": "number", "description": "上臂电阻 Ω（divider）"},
                    "r2": {"type": "number", "description": "下臂电阻 Ω（divider）"},
                    "vout": {"type": "number", "description": "输出电压（divider）"},
                    "addr7": {"type": "string", "description": "7 位 I2C 地址，如 0x3C"},
                    "addr8": {"type": "string", "description": "8 位 I2C 地址，如 0x78"},
                    "data": {"type": "string", "description": "要算校验的数据（crc）"},
                    "data_format": {"type": "string", "enum": ["text", "hex"], "description": "默认 text"},
                    "algo": {"type": "string", "description": "算法名（crc）"},
                    "resistance": {"type": "number", "description": "电阻 Ω（rc）"},
                    "capacitance": {"type": "number", "description": "电容 F（rc）"},
                },
                "required": ["op"],
            },
        },
    },
]


def _num(args: dict, key: str, default=None, positive=False):
    """取一个数值参数，取不到就用默认值；positive=True 时要求大于 0。"""
    value = args.get(key, default)
    if value in (None, ""):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} 得是个数，收到的是 {args.get(key)!r}")
    if positive and value <= 0:
        raise ValueError(f"{key} 要大于 0")
    return value


def _fmt(value: float, unit: str = "", digits: int = 4) -> str:
    """去掉多余的小数零，再带上单位。值太小、定点数位不够时会自动换成科学计数法。"""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    if value and text in ("", "0", "-0", "-"):   # 定点全是 0，说明位太少，换指数写法
        mantissa, _, exponent = f"{value:.{digits}e}".partition("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        text = f"{mantissa}e{int(exponent)}"
    return f"{text or '0'} {unit}".rstrip()


def _uart(args: dict) -> str:
    f_cpu = _num(args, "f_cpu", positive=True)
    baud = _num(args, "baud", positive=True)
    if f_cpu is None or baud is None:
        return "uart 要同时给 f_cpu 和 baud"
    over = _num(args, "oversampling", 16)
    if over not in (8, 16):
        return "oversampling 只能是 16 或 8"

    usartdiv = f_cpu / (over * baud)
    integer = int(usartdiv)
    fraction = usartdiv - integer
    # OVER16：BRR 的整数部分在 bit15:4、小数 4 位在 bit3:0（1 格 = 1/16）；
    # OVER8：小数只有 3 位、落在 BRR[2:0]（1 格 = 1/8，BRR[3] 要留 0），所以两种模式下
    # 小数都在 BRR 的低位，不用再移位。依据 STM32 HAL（stm32f4xx_hal_uart.h /
    # stm32f1xx_hal_uart.h）的宏：
    #   UART_DIVFRAQ_SAMPLING16 = ((余数 * 16) + 50) / 100
    #   UART_DIVFRAQ_SAMPLING8  = ((余数 *  8) + 50) / 100
    #   UART_BRR_SAMPLING8      = (DIVMANT << 4) + ((FRAQ & 0xF8) << 1) + (FRAQ & 0x07)
    # 也就是「小数按 1/8 取整、进位靠 (FRAQ & 0xF8) << 1 进到整数部分」；ST 的 LL 库
    # （__LL_USART_DIV_SAMPLING8）和 Linux 的 stm32-usart.c（f_cpu/baud 四舍五入后
    # 低 3 位当小数）也是这个口径。
    frac_bits = 4 if over == 16 else 3
    # HAL 用 ((x * 16) + 50) / 100 这种整数写法做四舍五入（半进位），数学上就是 floor(x + 0.5)；
    # 而 Python 的 round() 是银行家舍入，小数正好卡在 .5 时会往偶数靠（round(6.5) == 6），
    # 比 HAL 少一格，所以这里自己算 floor(x + 0.5)。
    frac_code = int(math.floor(fraction * (1 << frac_bits) + 0.5))
    if frac_code >= (1 << frac_bits):        # 小数进位到整数
        integer += 1
        frac_code = 0
    brr = (integer << 4) | frac_code

    # BRR 只有 16 位：OVER16 最大 0xFFF.F（4095.9375，BRR = 0xFFFF），OVER8 最大 0xFFF.8（4095.5）。
    # 波特率相对主频太低时 USARTDIV 会超出去，这时候必须报错——直接给一个 0x3A980 这样的
    # 「寄存器值」会让人以为写进去就行，其实低 16 位以外的位根本没地方放。
    if brr > 0xFFFF:
        max_div = 4095 + (15 / 16 if over == 16 else 1 / 2)
        return (f"USARTDIV = {usartdiv:.4f} 太大：算出来的 BRR = 0x{brr:X} 超过 16 位寄存器的上限"
                f"（OVER{int(over)} 最多到 {_fmt(max_div, '', 4)}，即 BRR = 0xFFFF）。"
                "这个波特率相对主频太低了，分频比装不进 BRR：提高 f_cpu，或者别用这么低的波特率")

    # 实际波特率要按 BRR 里那份「整数 + 小数」的分频算，不能把小数丢掉再用整数凑
    actual_div = integer + frac_code / (1 << frac_bits)
    if actual_div < 1:
        return (f"分频值算出来是 {_fmt(usartdiv)}，小于 1：主频太低或者波特率太高，"
                "这个波特率在这颗 MCU 上配不出来")
    actual = f_cpu / (over * actual_div)
    error = (actual - baud) / baud * 100

    ubrr = f_cpu / (16 * baud) - 1
    lines = [
        f"目标：f_cpu={_fmt(f_cpu / 1e6, 'MHz')}，波特率={_fmt(baud, 'bps')}，过采样={int(over)} 倍",
        f"USARTDIV（精确值）={usartdiv:.6f}",
        f"BRR = 0x{brr:04X}（整数部分 {integer}，小数部分 {frac_code}/{1 << frac_bits}）",
        f"按 BRR 反推：实际分频 {_fmt(actual_div, '', 6)} → 实际波特率 {_fmt(actual, 'bps', 1)}，"
        f"误差 {error:+.3f}%",
        f"AVR 风格 UBRR（16 倍）= {round(ubrr)}（精确值 {ubrr:.4f}）",
    ]
    if abs(error) > 2:
        lines.append("⚠ 误差超过 2%：这个主频配这个波特率容易丢字节，"
                     "换个波特率、或者把主频调成波特率的整数倍再试")
    else:
        lines.append("误差在 ±2% 以内，正常通信没问题")
    return "\n".join(lines)


def _timer(args: dict) -> str:
    f_cpu = _num(args, "f_cpu", positive=True)
    prescaler = _num(args, "prescaler", positive=True)
    if f_cpu is None or prescaler is None:
        return "timer 至少要给 f_cpu 和 prescaler"
    tick = prescaler / f_cpu                      # 一个计数走多久（秒）

    arr = _num(args, "arr")
    period_us = _num(args, "period_us")
    freq_hz = _num(args, "freq_hz")

    if arr is not None:
        period = (arr + 1) * tick
        lines = [
            f"输入：f_cpu={_fmt(f_cpu / 1e6, 'MHz')}，prescaler={_fmt(prescaler)}，ARR={_fmt(arr)}",
            f"一个计数 = {_fmt(tick * 1e6, 'us', 6)}（{(arr + 1)} 个计数溢出一次）",
            f"定时周期 = {_fmt(period * 1e6, 'us', 4)} = {_fmt(period, 's', 6)}",
            f"频率 = {_fmt(1 / period, 'Hz', 4)}",
        ]
        if arr > 0xFFFF:
            lines.append("⚠ ARR 超过 65535：16 位定时器装不下，得加大预分频")
        return "\n".join(lines)

    if period_us:
        wanted = period_us / 1e6
    elif freq_hz:
        wanted = 1 / freq_hz
    else:
        return "timer 要给 arr、period_us、freq_hz 三者之一"

    needed = wanted / tick
    if needed < 1:
        return (f"要的周期太短了（只合 {_fmt(needed, '个计数')}，一个计数都不到）："
                "把 prescaler 调小，或者换个能跑到这么快的定时器")
    arr_value = round(needed) - 1
    actual = (arr_value + 1) * tick
    lines = [
        f"输入：f_cpu={_fmt(f_cpu / 1e6, 'MHz')}，prescaler={_fmt(prescaler)}，"
        f"目标周期={_fmt(period_us if period_us else wanted * 1e6, 'us', 4)}",
        f"ARR = {arr_value}（0x{arr_value:04X}）",
        f"实际周期 = {_fmt(actual * 1e6, 'us', 4)}（目标 {_fmt(wanted * 1e6, 'us', 4)}，"
        f"误差 {(actual - wanted) / wanted * 100:+.3f}%）",
        f"实际频率 = {_fmt(1 / actual, 'Hz', 4)}",
    ]
    if arr_value > 0xFFFF:
        lines.append(f"⚠ ARR={arr_value} 超过 65535：16 位定时器装不下，prescaler 至少要到 "
                     f"{round(f_cpu * wanted / 65536) + 1}")
    return "\n".join(lines)


def _adc(args: dict) -> str:
    raw_bits = _num(args, "bits", 12)
    bits = 12 if raw_bits is None else int(raw_bits)
    vref = _num(args, "vref", 3.3)
    if vref is None or vref <= 0:
        return "vref 要大于 0"
    if not 2 <= bits <= 32:
        return "bits 要在 2~32 之间（常见 8 / 10 / 12 / 16）"
    levels = 1 << bits
    lsb = vref / levels

    value = _num(args, "value")
    voltage = _num(args, "voltage")
    lines = [f"分辨率：{bits} 位，Vref={_fmt(vref, 'V')}",
             f"满量程码值：0 ~ {levels - 1}（{levels} 个台阶）",
             f"1 LSB = {_fmt(lsb, 'V', 6)} = {_fmt(lsb * 1e6, 'uV', 2)}"]
    if value is not None:
        if value < 0:        # 原始码值是无符号的，负码值只会算出没意义的电压
            return (f"码值 {_fmt(value)} 是负数：ADC 的原始码值是无符号数，范围 0 ~ {levels - 1}，"
                    "负电压要先加偏置电路抬进量程再读")
        lines.append(f"码值 {_fmt(value)} → 电压 {_fmt(value * lsb, 'V', 6)}")
        if value >= levels:
            lines.append("⚠ 码值超出了量程上限，实际读到的是钳位值")
    elif voltage is not None:
        if voltage < 0:      # 量程是 0~Vref，负电压反推出来的码值也是负的，别输出「0x-26D」这种垃圾
            return (f"电压 {_fmt(voltage, 'V')} 是负数：ADC 只能量 0 ~ Vref（这里是 0 ~ {_fmt(vref, 'V')}）"
                    "之间的电压，负电压得先用偏置电路或差分输入抬到正区间")
        code = round(voltage / lsb)
        lines.append(f"电压 {_fmt(voltage, 'V')} → 码值 {code}（0x{code:0{max(2, bits // 4)}X}）")
        if code > levels - 1:
            lines.append("⚠ 这个电压超过了 Vref，ADC 只会读到满量程")
    else:
        lines.append("再给 value（码值）或 voltage（电压）其中之一就能换算")
    return "\n".join(lines)


def _divider(args: dict) -> str:
    vin = _num(args, "vin", positive=True)
    r1 = _num(args, "r1", positive=True)
    if vin is None or r1 is None:
        return "divider 至少要给 vin 和 r1"
    r2 = _num(args, "r2", positive=True)
    vout = _num(args, "vout")

    if r2 is not None:
        out = vin * r2 / (r1 + r2)
        return "\n".join([
            f"输入：Vin={_fmt(vin, 'V')}，R1={_fmt(r1, 'Ω')}（上臂），R2={_fmt(r2, 'Ω')}（下臂）",
            f"Vout = Vin × R2/(R1+R2) = {_fmt(out, 'V', 6)}",
            f"分压比 = {_fmt(r2 / (r1 + r2), '', 4)}，流过这两个电阻的电流 ≈ "
            f"{_fmt(vin / (r1 + r2) * 1000, 'mA', 4)}",
        ])

    if vout is None:
        return "divider 要给 r2（算 Vout）或 vout（反推 R2）"
    if vout < 0:        # 电阻分压只能把电压往下拉，负的 Vout 解出来是个负电阻，没意义
        return (f"Vout = {_fmt(vout, 'V')} 是负数：电阻分压只能输出 0 ~ Vin（这里 {_fmt(vin, 'V')}）"
                "之间的电压，要负压得用双电源或者反相放大")
    if vout >= vin:
        return f"Vout 要高到 {_fmt(vout, 'V')}，但 Vin 只有 {_fmt(vin, 'V')}：分压不可能升压，得换方案"
    solved = r1 * vout / (vin - vout)
    return "\n".join([
        f"目标：Vin={_fmt(vin, 'V')} → Vout={_fmt(vout, 'V')}，R1={_fmt(r1, 'Ω')}（上臂）",
        f"R2 = R1 × Vout/(Vin−Vout) = {_fmt(solved, 'Ω')}",
        f"取最近的标称值后实际 Vout 会略有偏差，验算一下："
        f"{_fmt(vin * solved / (r1 + solved), 'V', 6)}",
    ])


def _pwm(args: dict) -> str:
    f_cpu = _num(args, "f_cpu", positive=True)
    prescaler = _num(args, "prescaler", positive=True)
    arr = _num(args, "arr")
    if f_cpu is None or prescaler is None or arr is None:
        return "pwm 要给 f_cpu、prescaler、arr"
    top = arr + 1
    freq = f_cpu / (prescaler * top)

    ccr = _num(args, "ccr")
    duty_pct = _num(args, "duty_pct")
    lines = [f"PWM 频率 = f_cpu/(prescaler×(ARR+1)) = {_fmt(freq, 'Hz', 4)}"
             f"（周期 {_fmt(1 / freq * 1e6, 'us', 4)}）"]
    if ccr is not None:
        if ccr < 0:      # 比较值是从 0 往上的，负 CCR 只会算出负占空比和负的高电平时间
            return (f"CCR = {_fmt(ccr)} 是负数：比较值范围是 0 ~ ARR+1（这里 0 ~ {_fmt(top)}），"
                    "占空比只能是 0~100%")
        duty = ccr / top * 100
        lines.append(f"CCR={_fmt(ccr)} → 占空比 {duty:.3f}%")
        lines.append(f"高电平时间 {_fmt(ccr / top / freq * 1e6, 'us', 4)}，"
                     f"低电平 {_fmt((top - ccr) / top / freq * 1e6, 'us', 4)}")
        if ccr > arr:
            lines.append("⚠ CCR 比 ARR 还大，实际输出会一直是高电平")
    elif duty_pct is not None:
        if not 0 <= duty_pct <= 100:
            return "duty_pct 是百分比，要在 0~100 之间"
        code = round(duty_pct / 100 * top)
        lines.append(f"占空比 {duty_pct}% → CCR = {code}（0x{code:04X}）；"
                     f"实际占空比 {code / top * 100:.3f}%")
        if duty_pct == 100:
            lines.append("提示：CCR 要写成 ARR+1 才是常高，有些库里写 ARR 会差一点点")
    else:
        lines.append("再给 ccr（算占空比）或 duty_pct（反推 CCR）其中之一")
    return "\n".join(lines)


def _address(text: str) -> int:
    """把 0x 开头、带字母的、或者纯数字的地址文本转成整数。"""
    text = str(text).strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    if any(char in "abcdefABCDEF" for char in text):
        return int(text, 16)
    return int(text, 10)


def _i2c(args: dict) -> str:
    addr7 = args.get("addr7")
    addr8 = args.get("addr8")
    if addr7 in (None, "") and addr8 in (None, ""):
        return "i2c 要给 addr7 或 addr8"
    try:
        if addr7 not in (None, ""):
            seven = _address(addr7)
            if not 0 <= seven <= 0x7F:
                return f"{addr7} 不是合法的 7 位地址（范围 0x00~0x7F）"
        else:
            eight = _address(addr8)
            if not 0 <= eight <= 0xFF:
                return f"{addr8} 不是合法的 8 位地址（范围 0x00~0xFF）"
            if eight & 1:
                return (f"{addr8} 的最低位是 1，看着像「读」地址：8 位地址里最低位"
                        "是读写位，要先把它清掉才能还原 7 位地址")
            seven = eight >> 1
    except ValueError:
        return "地址看不懂，写成 0x3C 或者 60 这种"

    return "\n".join([
        f"7 位地址 = 0x{seven:02X}（{seven}）",
        f"写地址（8 位）= 0x{seven << 1:02X}（{(seven << 1)}）",
        f"读地址（8 位）= 0x{(seven << 1) | 1:02X}（{(seven << 1) | 1}）",
        "提示：STM32 HAL 里一般传 7 位地址再左移一位；AT24Cxx 这类手册常直接给 8 位地址，"
        "看到手册写 0xA0 就是 7 位 0x50",
    ])


def _bytes_from(data: str, data_format: str):
    """按 text 或 hex 把 data 转成字节串。"""
    if (data_format or "text").lower() == "hex":
        cleaned = data.strip()
        if cleaned.lower().startswith("0x"):
            cleaned = cleaned[2:]
        cleaned = cleaned.replace(" ", "").replace(",", "").replace("-", "").replace(":", "")
        if len(cleaned) % 2:
            raise ValueError("hex 形式的字节数得是偶数个十六进制字符")
        try:
            return bytes.fromhex(cleaned)
        except ValueError:
            raise ValueError(f"「{data}」里有不是十六进制字符的东西")
    return data.encode("utf-8", "replace")


def _crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _crc16_ccitt(data: bytes, init: int) -> int:
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _xor(data: bytes) -> int:
    result = 0
    for byte in data:
        result ^= byte
    return result


def _crc(args: dict) -> str:
    data = args.get("data")
    if data in (None, ""):
        return "crc 要给 data"
    algo = (args.get("algo") or "crc16-modbus").strip().lower()
    try:
        payload = _bytes_from(str(data), args.get("data_format") or "text")
    except ValueError as exc:
        return f"数据读不出来：{exc}"

    table = {
        "crc8": ("CRC-8（多项式 0x07，初值 0x00）", lambda: _crc8(payload), 2),
        "crc16-modbus": ("CRC-16/MODBUS（0xA001 反射，初值 0xFFFF）", lambda: _crc16_modbus(payload), 4),
        "crc16-ccitt": ("CRC-16/CCITT-FALSE（多项式 0x1021，初值 0xFFFF）",
                        lambda: _crc16_ccitt(payload, 0xFFFF), 4),
        "crc16-xmodem": ("CRC-16/XMODEM（多项式 0x1021，初值 0x0000）",
                         lambda: _crc16_ccitt(payload, 0x0000), 4),
        "crc32": ("CRC-32（zlib 标准）", lambda: zlib.crc32(payload) & 0xFFFFFFFF, 8),
        "sum8": ("累加和（8 位）", lambda: sum(payload) & 0xFF, 2),
        "xor8": ("异或和（8 位）", lambda: _xor(payload), 2),
        "bcc": ("BCC（异或和，8 位）", lambda: _xor(payload), 2),
        "lrc": ("LRC（累加和取反加一）", lambda: (-sum(payload)) & 0xFF, 2),
    }
    if algo not in table:
        return (f"不认识的 algo：{algo}。可选 "
                + "、".join(sorted(table)))

    name, compute, width = table[algo]
    result = compute()
    shown = " ".join(f"{b:02X}" for b in payload)
    lines = [f"算法：{name}",
             f"数据：{len(payload)} 字节" + (f"（{shown}）" if payload else "（空）"),
             f"结果：0x{result:0{width}X}（{result}）"]
    if width == 4:      # 16 位校验，顺手给出两种字节序的写法，填寄存器常用
        lines.append(f"小端写进帧里是 {result & 0xFF:02X} {result >> 8:02X}，"
                     f"大端是 {result >> 8:02X} {result & 0xFF:02X}")
    return "\n".join(lines)


def _rc(args: dict) -> str:
    resistance = _num(args, "resistance", positive=True)
    capacitance = _num(args, "capacitance", positive=True)
    if resistance is None or capacitance is None:
        return "rc 要给 resistance（欧姆）和 capacitance（法拉）"
    tau = resistance * capacitance
    return "\n".join([
        f"输入：R={_fmt(resistance, 'Ω')}，C={_fmt(capacitance, 'F', 9)}",
        f"时间常数 τ = R×C = {_fmt(tau, 's', 6)} = {_fmt(tau * 1000, 'ms', 4)}",
        f"充电到 63.2%（1τ）≈ {_fmt(tau * 1000, 'ms', 4)}",
        f"充电到 95%（3τ）≈ {_fmt(tau * 3000, 'ms', 4)}",
        f"充电到 99%（5τ）≈ {_fmt(tau * 5000, 'ms', 4)}",
        f"截止频率 fc = 1/(2πRC) = {_fmt(1 / (2 * math.pi * tau), 'Hz', 4)}",
    ])


_OPS = {
    "uart": _uart, "timer": _timer, "adc": _adc, "divider": _divider,
    "pwm": _pwm, "i2c": _i2c, "crc": _crc, "rc": _rc,
}


def mcu_calc(op: str = "", **kwargs) -> str:
    """按 op 分发到对应的计算函数。"""
    key = (op or "").strip().lower()
    if key not in _OPS:
        return f"不认识的 op：{op or '(空)'}。可选 " + "、".join(_OPS)
    try:
        return _OPS[key](kwargs)
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        return f"参数不对：{exc}"
