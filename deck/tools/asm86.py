"""8086 / 8088 汇编仿真：自带汇编器 + CPU 核心 + 最小 DOS / BIOS 中断。

8086 和 8088 的指令集完全一样，差别只在芯片外面：8086 是 16 位外部数据总线，
8088 是 8 位（IBM PC 用的就是 8088，所以同一条指令要多花几个总线周期）。
这里一份核心吃两个型号，cpu 参数只影响报告出来的总线周期估算。

跑的是实模式：1 MiB 地址空间，物理地址 = 段寄存器 × 16 + 偏移。段框架按 MASM 那套
认（SEGMENT / ENDS / ASSUME / PROC / ENDP / END、DATA 和 @DATA、.MODEL、.STACK），
也支持完全不带段的裸程序；.DATA / .CODE 这类简化伪指令会被忽略（数据照样按标签
归属寻址，所以两种写法都能跑）。

支持的指令：MOV / XCHG / LEA / PUSH / POP / PUSHF / POPF / XLAT；ADD / ADC / SUB /
SBB / CMP / INC / DEC / NEG；MUL / IMUL / DIV / IDIV / CBW / CWD；AND / OR / XOR /
NOT / TEST；SHL / SAL / SHR / SAR / ROL / ROR / RCL / RCR；JMP / 各种 Jcc / LOOP /
LOOPE / LOOPNE / CALL / RET / INT / IRET / HLT / NOP；CLC / STC / CMC / CLD / STD /
CLI / STI；MOVSB / MOVSW / STOSB / STOSW / LODSB / LODSW / CMPSB / CMPSW / SCASB /
SCASW（带 REP / REPZ / REPNZ；REP 跟在 CMPS / SCAS 后面就是 REPZ，看 ZF 决定停不停）。

中断直接内置处理，不走中断向量表：INT 21H 的 09H 打印字符串、02H 打印字符、
01H / 0AH 读键盘（从 stdin 参数取）、4CH / 00H 结束程序，INT 20H 结束，
INT 10H 的 0EH / 09H / 02H。别的一律明确回「没实现」，不会假装跑过。

纯标准库，不联网、不起子进程，也不生成机器码——指令地址是按常见编码长度估算的
偏移，用来显示跳转目标和 IP，跟真实机器码不保证逐字节一致。字符串按 UTF-8 存进
内存（真实 DOS 是 GBK），因为打印时也用同一套解码，所以中文打出来是对的。
"""
import re

from ..sandbox import safe_path

MEM_SIZE = 1 << 20          # 实模式 1 MiB 地址空间
MASK20 = MEM_SIZE - 1
MASK16 = 0xFFFF

DEFAULT_MAX_STEPS = 200000
MAX_STEPS_LIMIT = 5000000
MAX_TRACE = 200
MAX_OUTPUT_CHARS = 4000
MAX_STRING_SCAN = 4096          # INT 21H 09H 往后找 '$' 最多找这么多字节
MAX_SOURCE = 200000
MAX_MEM_ROWS = 12
MAX_DATA_ELEMENTS = 1 << 20     # 一段数据最多展开这么多元素，挡住嵌套 DUP 的指数爆炸

# 段值：代码段 1000H，各数据段依次 2000H / 3000H（每个段值差 1000H 正好隔 64 KiB）
CODE_SEG_VALUE = 0x1000
DATA_SEG_VALUE = 0x2000
SEG_STEP = 0x1000

# FLAGS 寄存器里各位的位置
F_CF, F_PF, F_AF, F_ZF, F_SF, F_TF, F_IF, F_DF, F_OF = (
    0x0001, 0x0004, 0x0010, 0x0040, 0x0080, 0x0100, 0x0200, 0x0400, 0x0800,
)
FLAG_ORDER = (
    ("CF", F_CF), ("PF", F_PF), ("AF", F_AF), ("ZF", F_ZF), ("SF", F_SF),
    ("OF", F_OF), ("IF", F_IF), ("DF", F_DF), ("TF", F_TF),
)

# 8086 / 8088 的 FLAGS 里这几位恒为 1、写不进去：bit1 是保留位，bit 12~15 也是。
# 这正是区分 8086 和 286 的经典判据（286 上 bit 12~15 是可写的），
# PUSHF / POPF / IRET 都要带上它，否则 `STC; PUSHF; POP AX` 会得到 0201H 而不是 F203H
F_FIXED_ONE = 0x0002 | 0xF000

_R16 = ("AX", "BX", "CX", "DX", "SI", "DI", "BP", "SP")
_R8 = {
    "AL": ("AX", 0), "AH": ("AX", 8),
    "BL": ("BX", 0), "BH": ("BX", 8),
    "CL": ("CX", 0), "CH": ("CX", 8),
    "DL": ("DX", 0), "DH": ("DX", 8),
}
_SREG = ("ES", "CS", "SS", "DS")
_REG_NAMES = set(_R16) | set(_R8) | set(_SREG)
_SIZE_TOKENS = {"BYTE": 8, "SBYTE": 8, "WORD": 16, "SWORD": 16}

# 条件跳转的后缀 -> 判定
_COND_SUFFIX = (
    "O", "NO", "B", "C", "NAE", "AE", "NB", "NC", "E", "Z", "NE", "NZ",
    "BE", "NA", "A", "NBE", "S", "NS", "P", "PE", "NP", "PO",
    "L", "NGE", "GE", "NL", "LE", "NG", "G", "NLE",
)
_JCC = {("J" + suffix): suffix for suffix in _COND_SUFFIX}

# 指令 -> 允许的操作数个数（最少, 最多）
_ARITY = {
    "NOP": (0, 0), "HLT": (0, 0), "WAIT": (0, 0), "CLC": (0, 0), "STC": (0, 0),
    "CMC": (0, 0), "CLD": (0, 0), "STD": (0, 0), "CLI": (0, 0), "STI": (0, 0),
    "CBW": (0, 0), "CWD": (0, 0), "PUSHF": (0, 0), "POPF": (0, 0),
    "MOVSB": (0, 0), "MOVSW": (0, 0), "STOSB": (0, 0), "STOSW": (0, 0),
    "LODSB": (0, 0), "LODSW": (0, 0), "CMPSB": (0, 0), "CMPSW": (0, 0),
    "SCASB": (0, 0), "SCASW": (0, 0), "XLAT": (0, 0), "IRET": (0, 0),
    "INC": (1, 1), "DEC": (1, 1), "NEG": (1, 1), "NOT": (1, 1),
    "MUL": (1, 1), "IMUL": (1, 1), "DIV": (1, 1), "IDIV": (1, 1),
    "PUSH": (1, 1), "POP": (1, 1), "CALL": (1, 1), "JMP": (1, 1),
    "INT": (1, 1), "RET": (0, 1),
    "MOV": (2, 2), "XCHG": (2, 2), "LEA": (2, 2), "ADD": (2, 2), "ADC": (2, 2),
    "SUB": (2, 2), "SBB": (2, 2), "CMP": (2, 2), "AND": (2, 2), "OR": (2, 2),
    "XOR": (2, 2), "TEST": (2, 2), "SHL": (2, 2), "SAL": (2, 2), "SHR": (2, 2),
    "SAR": (2, 2), "ROL": (2, 2), "ROR": (2, 2), "RCL": (2, 2), "RCR": (2, 2),
}
for _name in _JCC:
    _ARITY[_name] = (1, 1)
for _name in ("LOOP", "LOOPE", "LOOPZ", "LOOPNE", "LOOPNZ"):
    _ARITY[_name] = (1, 1)

# 按位宽执行的指令（要能从操作数推出是字节还是字）
_NEEDS_WIDTH = {
    "MOV", "XCHG", "LEA", "ADD", "ADC", "SUB", "SBB", "CMP", "INC", "DEC",
    "NEG", "NOT", "MUL", "IMUL", "DIV", "IDIV", "AND", "OR", "XOR", "TEST",
    "SHL", "SAL", "SHR", "SAR", "ROL", "ROR", "RCL", "RCR", "PUSH", "POP",
}
_SHIFTS = ("SHL", "SAL", "SHR", "SAR", "ROL", "ROR", "RCL", "RCR")
_STRING_OPS = ("MOVSB", "MOVSW", "STOSB", "STOSW", "LODSB", "LODSW",
               "CMPSB", "CMPSW", "SCASB", "SCASW")
_PREFIXES = ("REP", "REPE", "REPZ", "REPNE", "REPNZ")

# 会被直接忽略的伪指令（段框架、模式声明之类）
_IGNORED_WORDS = {
    "ASSUME", "PUBLIC", "EXTERN", "EXTRN", "INCLUDE", "TITLE", "PAGE",
    "NAME", "GROUP", "LOCAL", "MACRO", "ENDM", "ENDS", "LABEL",
}
_TEXT_MARKS = ("'", '"')

_BRANCH_LEN = {"JMP": 3, "CALL": 3}

_SEG_START_RE = re.compile(r"^([A-Za-z_@?][\w@?]*)\s+SEGMENT\b(.*)$", re.I)
_PROC_RE = re.compile(r"^([A-Za-z_@?][\w@?]*)\s+PROC\b", re.I)
_LABEL_RE = re.compile(r"^([A-Za-z_@?][\w@?]*)\s*:(.*)$", re.S)
_EQU_RE = re.compile(r"^([A-Za-z_@?][\w@?]*)\s+EQU\s+(.+)$", re.I)
_DATA_RE = re.compile(r"^(?:([A-Za-z_@?][\w@?]*)\s+)?(DB|DW|DD)\s+(.*)$", re.I)
_ORG_RE = re.compile(r"^ORG\s+(.+)$", re.I)
_END_RE = re.compile(r"^END\b\s*(.*)$", re.I)
_END_LABEL_RE = re.compile(r"^([A-Za-z_@?][\w@?]*)$")
_DUP_RE = re.compile(r"^(.*?)\s+DUP\s*\((.*)\)\s*$", re.I | re.S)
_IDENT_RE = re.compile(r"(?<![0-9A-Za-z_@?.])[A-Za-z_@?][\w@?]*")


class _AsmError(Exception):
    """汇编阶段的错误，带行号。"""

    def __init__(self, message, line=0):
        super().__init__(message)
        self.line = line

    def __str__(self):
        return f"第 {self.line} 行：{self.args[0]}" if self.line else self.args[0]


class _Halt(Exception):
    """运行结束（正常退出或者出错停下），带一句说明。"""


# ---------------- 小工具 ----------------

def _strip_comment(line):
    """去掉 ; 后面的注释，引号里的分号不算。"""
    out = []
    quote = False
    for ch in line:
        if ch in _TEXT_MARKS:
            quote = not quote
        elif ch == ";" and not quote:
            break
        out.append(ch)
    return "".join(out)


def _split_operands(text):
    """按逗号在最外层拆开，方括号 / 圆括号 / 引号里的逗号不算。"""
    parts = []
    current = []
    depth = 0
    quote = False
    for ch in text:
        if quote:
            current.append(ch)
            if ch in _TEXT_MARKS:
                quote = False
            continue
        if ch in _TEXT_MARKS:
            quote = True
            current.append(ch)
            continue
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _identifiers(text):
    """挑出表达式里的标识符（前面挨着数字的不要，免得把 0FFH 里的 FFH 当名字）。"""
    return [match.group(0) for match in _IDENT_RE.finditer(text)]


def _literal(text):
    """认 123 / 0FFH / 1010B / 0x1F / 17O / 12D 这几种写法；不是数字就返回 None。"""
    if not text:
        return None
    lowered = text.lower()
    try:
        if lowered.startswith("0x"):
            return int(lowered[2:], 16)
        if lowered.startswith("0b") and lowered[2:] and set(lowered[2:]) <= {"0", "1"}:
            return int(lowered[2:], 2)
        if lowered.startswith("0o"):
            return int(lowered[2:], 8)
        if lowered.endswith("h"):
            return int(lowered[:-1], 16)
        if lowered.endswith("b") and lowered[:-1] and set(lowered[:-1]) <= {"0", "1"}:
            return int(lowered[:-1], 2)
        if lowered.endswith(("o", "q")) and lowered[:-1].isdigit():
            return int(lowered[:-1], 8)
        if lowered.endswith("d") and lowered[:-1].isdigit():
            return int(lowered[:-1], 10)
        return int(lowered, 10)
    except ValueError:
        return None


def _parity_even(value):
    """PF：结果低 8 位里 1 的个数是偶数就置 1。"""
    return bin(value & 0xFF).count("1") % 2 == 0


def _unsigned(value, bits):
    return value & ((1 << bits) - 1)


def _signed(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


class _Expr:
    """很小的常量表达式求值器：+ - * / 取整、括号、符号名、$（当前位置）。"""

    def __init__(self, text, symbols, here):
        self.tokens = self._tokenize(text)
        self.at = 0
        self.symbols = symbols
        self.here = here

    @staticmethod
    def _tokenize(text):
        tokens = []
        index = 0
        while index < len(text):
            ch = text[index]
            if ch.isspace():
                index += 1
                continue
            if ch in _TEXT_MARKS:
                end = text.find(ch, index + 1)
                if end < 0:
                    raise _AsmError(f"字符常量的引号没闭合：{text}")
                body = text[index + 1:end]
                if len(body) != 1:
                    raise _AsmError(f"字符常量只能有一个字符：{text[index:end + 1]}")
                tokens.append(("num", ord(body)))
                index = end + 1
                continue
            if ch in "+-*/()$":
                tokens.append(("op", ch))
                index += 1
                continue
            match = re.match(r"[0-9A-Za-z_@?][0-9A-Za-z_@?.]*", text[index:])
            if not match:
                raise _AsmError(f"表达式里有看不懂的符号：{text[index:]}")
            tokens.append(("word", match.group(0)))
            index += len(match.group(0))
        return tokens

    def parse(self):
        value = self._sum()
        if self.at < len(self.tokens):
            rest = "".join(str(token[1]) for token in self.tokens[self.at:])
            raise _AsmError(f"表达式后面多出来东西：{rest}")
        return value

    def _peek(self):
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def _sum(self):
        value = self._product()
        while True:
            token = self._peek()
            if token == ("op", "+"):
                self.at += 1
                value += self._product()
            elif token == ("op", "-"):
                self.at += 1
                value -= self._product()
            else:
                return value

    def _product(self):
        value = self._unary()
        while True:
            token = self._peek()
            if token == ("op", "*"):
                self.at += 1
                value *= self._unary()
            elif token == ("op", "/"):
                self.at += 1
                divisor = self._unary()
                if divisor == 0:
                    raise _AsmError("表达式里除以 0 了")
                value = int(value / divisor)      # 汇编里的整除是向 0 取整
            else:
                return value

    def _unary(self):
        token = self._peek()
        if token == ("op", "+"):
            self.at += 1
            return self._unary()
        if token == ("op", "-"):
            self.at += 1
            return -self._unary()
        return self._atom()

    def _atom(self):
        token = self._peek()
        if token is None:
            raise _AsmError("表达式不完整")
        self.at += 1
        kind, text = token
        if kind == "num":
            return text
        if kind == "op" and text == "$":
            return self.here
        if kind == "op" and text == "(":
            value = self._sum()
            if self._peek() != ("op", ")"):
                raise _AsmError("表达式里的括号没配对")
            self.at += 1
            return value
        if kind == "word":
            found = self.symbols.get(text.lower())
            if found is not None:
                return found
            number = _literal(text)
            if number is not None:
                return number
            raise _AsmError(f"不认识的符号：{text}（标签没定义？还是拼错了？）")
        raise _AsmError(f"表达式里看不懂：{text}")


# ---------------- 汇编：第一遍定段算偏移，第二遍代符号 ----------------

def _reg_operand(name):
    if name in _R8:
        return ("r8", name)
    if name in _SREG:
        return ("sreg", name)
    return ("r16", name)


def _parse_memory(inner, seg, size, head=""):
    """解析 [BX+SI+4] 这种寻址：基址 / 变址 / 位移分开。head 是方括号前面那截
    （NUMS[BX+SI] 里的 NUMS），一起并进位移。"""
    pieces = []
    sign = 1
    current = ""
    for ch in inner:
        if ch in "+-":
            pieces.append((sign, current))
            current = ""
            sign = 1 if ch == "+" else -1
        else:
            current += ch
    pieces.append((sign, current))

    base = index = None
    terms = ["+" + head.strip()] if head.strip() else []
    for piece_sign, piece in pieces:
        token = piece.strip()
        if not token:
            continue
        upper = token.upper()
        if upper in _R16:
            if upper in ("BX", "BP"):
                if base is not None:
                    raise _AsmError("寻址里只能有一个基址寄存器（BX 或 BP）")
                base = upper
            elif upper in ("SI", "DI"):
                if index is not None:
                    raise _AsmError("寻址里只能有一个变址寄存器（SI 或 DI）")
                index = upper
            else:
                raise _AsmError(f"{upper} 不能放在方括号里，只有 BX / BP / SI / DI 可以")
            continue
        if upper in _R8 or upper in _SREG:
            raise _AsmError(f"{upper} 不能放在方括号里，只有 BX / BP / SI / DI 可以")
        terms.append(("-" if piece_sign < 0 else "+") + token)

    disp = "".join(terms)
    if disp.startswith("+"):
        disp = disp[1:]
    return ("mem", seg, base, index, disp, size)


def _parse_operand(text, branch=False):
    """把一个操作数文本解析成内部表示；符号值这时还不知道，先原样挂着。"""
    raw = text.strip()
    if not raw:
        raise _AsmError("操作数是空的")
    if branch:
        return ("imm", raw)

    upper = raw.upper()
    if upper.startswith("OFFSET"):
        rest = raw[len("OFFSET"):].strip()
        if not rest:
            raise _AsmError("OFFSET 后面要跟个名字")
        return ("imm", rest)

    size = None
    body = raw
    while True:
        match = re.match(r"^(BYTE|WORD|SBYTE|SWORD)\s+(PTR\s+)?", body, re.I)
        if not match:
            break
        size = _SIZE_TOKENS[match.group(1).upper()]
        body = body[match.end():].strip()
        if not body:
            raise _AsmError(f"{raw}：BYTE / WORD PTR 后面还缺内存操作数")

    seg = None
    match = re.match(r"^(CS|DS|SS|ES)\s*:\s*", body, re.I)
    if match:
        seg = match.group(1).upper()
        body = body[match.end():].strip()
        if not body:
            raise _AsmError(f"{raw}：段超越前缀后面还缺内容")
        if body.upper() in _REG_NAMES:
            return _reg_operand(body.upper())

    if body.endswith("]") and "[" in body:
        # 支持 [BX+SI] 和 NUMS[BX+SI] 两种写法，后者方括号前面那截当地址的一部分
        start = body.index("[")
        return _parse_memory(body[start + 1:-1], seg, size, body[:start])

    inner = body.upper()
    if seg is None and inner in _REG_NAMES:
        return _reg_operand(inner)

    if size is not None:
        # 写了 BYTE / WORD PTR 但没带方括号：MASM 里这就是取那个地址上的内容
        return ("mem", seg, None, None, body, size)

    return ("maybe_mem", body, size)


def _disp_size(disp):
    """位移占几个字节：没有是 0，能塞进有符号 8 位是 1，否则 2。

    只认纯整数字面量；表达式 / 符号这时值还不知道，按最宽的 2 估算（宁可估大）。
    """
    if not isinstance(disp, str) or not disp.strip():
        return 0
    try:
        value = int(disp.strip(), 0)
    except (TypeError, ValueError):
        return 2
    return 1 if -128 <= value <= 127 else 2


def _length(mnemonic, operands):
    """估算这条指令占几个字节，只用来推进偏移和算取指周期。"""
    if mnemonic in _BRANCH_LEN:
        return _BRANCH_LEN[mnemonic]
    if mnemonic in _JCC or mnemonic.startswith("LOOP"):
        return 2
    if mnemonic in ("MOVSB", "MOVSW", "STOSB", "STOSW", "LODSB", "LODSW",
                    "CMPSB", "CMPSW", "SCASB", "SCASW"):
        return 1

    regs = [op for op in operands if op[0] in ("r8", "r16", "sreg")]
    mems = [op for op in operands if op[0] in ("mem", "maybe_mem")]
    imms = [op for op in operands if op[0] == "imm"]

    if mnemonic in ("INC", "DEC", "PUSH", "POP") and regs and not mems:
        return 1
    if mnemonic == "INT":
        return 2

    total = 1
    if mems:
        total += 1                                     # ModR/M
        disp = mems[0][4] if mems[0][0] == "mem" else mems[0][1]
        total += _disp_size(disp)
    elif len(regs) >= 2:
        total += 1
    narrow = bool(regs) and all(op[0] == "r8" for op in regs)
    for _ in imms:
        total += 1 if narrow else 2
    return total


def _infer_width(operands, line, mnemonic=""):
    """从操作数推出这条指令按字节还是按字来。移位指令只看被移的那个操作数。"""
    counted = operands[:1] if mnemonic in _SHIFTS else operands
    widths = set()
    for op in counted:
        if op[0] == "r8":
            widths.add(8)
        elif op[0] in ("r16", "sreg"):
            widths.add(16)
        elif op[0] == "mem" and op[5]:
            widths.add(op[5])
    if len(widths) > 1:
        raise _AsmError("操作数位宽对不上，一个 8 位一个 16 位", line)
    if not widths:
        raise _AsmError(
            "说不清这条指令是按字节还是按字来——给内存操作数加 BYTE PTR 或 WORD PTR", line)
    return widths.pop()


def _label_segment(names, labels):
    for name in names:
        entry = labels.get(name.lower())
        if entry is not None:
            return entry[1]
    return None


def _resolve_operand(op, symbols, labels, here):
    """把第一遍挂着的符号值代进去。"""
    kind = op[0]
    if kind == "imm":
        return ("imm", _Expr(op[1], symbols, here).parse())
    if kind == "maybe_mem":
        text, size = op[1], op[2]
        names = _identifiers(text)
        if any(name.lower() in labels for name in names):
            return ("mem", _label_segment(names, labels), None, None,
                    _Expr(text, symbols, here).parse(), size)
        return ("imm", _Expr(text, symbols, here).parse())
    if kind == "mem":
        seg, base, index, text, size = op[1], op[2], op[3], op[4], op[5]
        disp = _Expr(text, symbols, here).parse() if text.strip() else 0
        if seg is None:
            seg = _label_segment(_identifiers(text), labels)
        return ("mem", seg, base, index, disp, size)
    return op


def _validate(mnemonic, operands, line):
    """把常见的写法错误在汇编阶段就挑出来，别等跑到一半炸。"""
    if mnemonic in ("MOV", "XCHG", "ADD", "ADC", "SUB", "SBB", "CMP", "AND",
                    "OR", "XOR", "TEST"):
        dst, src = operands
        if dst[0] in ("mem",) and src[0] in ("mem",):
            raise _AsmError(f"{mnemonic} 的两个操作数不能都是内存", line)
        if mnemonic != "CMP" and mnemonic != "TEST" and dst[0] == "imm":
            raise _AsmError(f"{mnemonic} 不能往立即数里写", line)
        if src[0] == "imm" and mnemonic == "XCHG":
            raise _AsmError("XCHG 的操作数不能是立即数", line)
    if mnemonic in ("INC", "DEC", "NEG", "NOT", "MUL", "IMUL", "DIV", "IDIV"):
        if operands[0][0] == "imm":
            raise _AsmError(f"{mnemonic} 的操作数不能是立即数", line)
    if mnemonic in _SHIFTS:
        count = operands[1]
        if count[0] not in ("imm", "r8"):
            raise _AsmError(f"{mnemonic} 的移位次数只能写 1 / 立即数 / CL", line)
        if count[0] == "r8" and count[1] != "CL":
            raise _AsmError(f"{mnemonic} 的移位次数寄存器只能是 CL", line)
    if mnemonic == "LEA":
        if operands[0][0] != "r16" or operands[1][0] != "mem":
            raise _AsmError("LEA 要写成 LEA 16 位寄存器, 内存操作数", line)
    if mnemonic == "MOV" and operands[0][0] == "sreg":
        if operands[0][1] == "CS":
            raise _AsmError("CS 不能当 MOV 的目标（8086 不允许改 CS）", line)
        if operands[1][0] == "imm":
            raise _AsmError(
                "8086 不允许 MOV 段寄存器, 立即数（这是 8086 的经典限制，"
                "先 MOV AX, 值 再 MOV DS, AX）", line)
    if mnemonic == "POP" and operands[0][0] == "sreg" and operands[0][1] == "CS":
        raise _AsmError("POP CS 是非法的", line)


def _expand_data(text, symbols, here, line, budget=None):
    """把 DB / DW 后面那串东西展开成元素表。

    budget 是个共享计数，拦住 65535 DUP(65535 DUP(0)) 这种嵌套：光限单层 count
    没用，展开出来的元素总数才能决定会不会把内存撑爆。
    """
    if budget is None:
        budget = [MAX_DATA_ELEMENTS]
    out = []
    for element in _split_operands(text):
        stripped = element.strip()
        match = _DUP_RE.match(stripped)
        if match:
            count = _Expr(match.group(1), symbols, here).parse()
            if count < 0 or count > 0xFFFF:
                raise _AsmError(f"DUP 的重复次数要在 0~65535 之间，这里是 {count}", line)
            inner = _expand_data(match.group(2), symbols, here, line, budget)
            need = len(inner) * count
            if need > budget[0]:
                raise _AsmError(
                    f"数据定义太大：展开后超过 {MAX_DATA_ELEMENTS} 个元素，不给了", line)
            budget[0] -= need
            out.extend(inner * count)
            continue
        if budget[0] <= 0:
            raise _AsmError(
                f"数据定义太大：展开后超过 {MAX_DATA_ELEMENTS} 个元素，不给了", line)
        budget[0] -= 1
        if stripped == "?":
            out.append(("zero", ""))
        elif len(stripped) >= 2 and stripped[0] in _TEXT_MARKS and stripped[-1] == stripped[0]:
            out.append(("str", stripped[1:-1]))
        else:
            if stripped.upper().startswith("OFFSET"):
                stripped = stripped[len("OFFSET"):].strip()
            out.append(("expr", stripped))
    return out


def _elements_size(elements, unit, line):
    """一段数据占多少字节。字符串是「一个元素多个字节」，不能按元素个数乘。"""
    total = 0
    for kind, payload in elements:
        if kind == "str":
            if unit != 1:
                raise _AsmError("只有 DB 里能放字符串，DW / DD 放不下", line)
            total += len(payload.encode("utf-8"))
        else:
            total += unit
    return total


def _data_bytes(elements, unit, symbols, here, line):
    raw = []
    for kind, payload in elements:
        if kind == "zero":
            raw.extend([0] * unit)
        elif kind == "str":
            if unit != 1:
                raise _AsmError("只有 DB 里能放字符串，DW / DD 放不下", line)
            raw.extend(payload.encode("utf-8"))
        else:
            value = _Expr(payload, symbols, here).parse()
            for i in range(unit):                       # 小端序，DB/DW/DD 通用
                raw.append((value >> (8 * i)) & 0xFF)
    return raw


def _assemble(source):
    """两遍扫描。返回一个描述整个程序的 dict。"""
    segments = []          # [{"name", "value", "used", "has_code"}]
    implicit = None        # 没有 SEGMENT 框架时用的隐式段
    current = None
    offsets = {}           # 段下标 -> 当前偏移
    raw_labels = {}        # 小写名字 -> (段下标, 段内偏移)
    label_names = {}       # 段下标 -> {偏移: 名字}，给内存标注用
    consts = {}            # 小写名字 -> 常量值
    seg_names = {}         # 小写段名 -> 段下标
    items = []             # 待落实的数据
    code = {}              # 段下标 -> {偏移: 指令记录}
    first_code = None      # (段下标, 偏移)
    entry_name = ""
    line_no = 0

    def ensure_segment():
        nonlocal implicit, current
        if current is None:
            if implicit is None:
                implicit = len(segments)
                segments.append({"name": "", "value": 0, "used": 0, "has_code": False})
                seg_names[""] = implicit
                code[implicit] = {}
                offsets[implicit] = 0
                label_names[implicit] = {}
            current = implicit
        return current

    def symbols_now():
        found = dict(consts)
        for name, entry in raw_labels.items():
            found[name] = entry[1]
        for name, index in seg_names.items():
            if name:
                found[name] = segments[index]["value"]
                found["@" + name] = segments[index]["value"]   # MASM 的 @DATA 写法
        return found

    for raw in source.splitlines():
        line_no += 1
        line = _strip_comment(raw).strip()
        if not line:
            continue

        # 段开始 / 结束
        match = _SEG_START_RE.match(line)
        if match:
            name = match.group(1).lower()
            if name in seg_names:
                raise _AsmError(f"段名重复：{match.group(1)}", line_no)
            index = len(segments)
            segments.append({"name": match.group(1), "value": 0, "used": 0, "has_code": False})
            seg_names[name] = index
            code[index] = {}
            offsets[index] = 0
            label_names[index] = {}
            current = index
            continue
        if re.match(r"^[A-Za-z_@?][\w@?]*\s+ENDS\b", line, re.I):
            current = None
            continue

        # PROC / ENDP：PROC 名字当标签，其余当没看见
        match = _PROC_RE.match(line)
        if match:
            index = ensure_segment()
            name = match.group(1).lower()
            if name in raw_labels:
                raise _AsmError(f"标签重名：{match.group(1)}", line_no)
            raw_labels[name] = (index, offsets[index])
            label_names[index][offsets[index]] = match.group(1)
            continue
        if re.match(r"^[A-Za-z_@?][\w@?]*\s+ENDP\b", line, re.I):
            continue

        # 纯伪指令：忽略（.MODEL / .STACK / .DATA / .CODE / .386 / ASSUME 这些）
        head = line.split(None, 1)[0]
        if head.startswith("."):
            continue
        if head.upper() in _IGNORED_WORDS and not re.match(r"^\w+\s*:", line):
            continue

        # END [入口标签]
        match = _END_RE.match(line)
        if match and match.group(1).strip():
            tail = match.group(1).strip()
            if _END_LABEL_RE.match(tail):
                entry_name = tail.lower()
            continue
        if re.match(r"^END$", line, re.I):
            continue

        # EQU 常量
        match = _EQU_RE.match(line)
        if match:
            index = ensure_segment()
            name = match.group(1).lower()
            consts[name] = _Expr(match.group(2), symbols_now(), offsets[index]).parse()
            continue

        # ORG
        match = _ORG_RE.match(line)
        if match:
            index = ensure_segment()
            offsets[index] = _Expr(match.group(1), symbols_now(), offsets[index]).parse()
            continue

        # 一行最多挂一个标签 + 后面的内容
        while True:
            match = _LABEL_RE.match(line)
            if not match:
                break
            index = ensure_segment()
            name = match.group(1).lower()
            if name in raw_labels:
                raise _AsmError(f"标签重名：{match.group(1)}", line_no)
            raw_labels[name] = (index, offsets[index])
            label_names[index][offsets[index]] = match.group(1)
            line = match.group(2).strip()
            if not line:
                break
        if not line:
            continue

        # 数据定义
        match = _DATA_RE.match(line)
        if match:
            index = ensure_segment()
            if match.group(1):
                name = match.group(1).lower()
                if name in raw_labels:
                    raise _AsmError(f"标签重名：{match.group(1)}", line_no)
                raw_labels[name] = (index, offsets[index])
                label_names[index][offsets[index]] = match.group(1)
            unit = {"DB": 1, "DW": 2, "DD": 4}[match.group(2).upper()]
            here = offsets[index]
            elements = _expand_data(match.group(3), symbols_now(), here, line_no)
            size = _elements_size(elements, unit, line_no)
            items.append((index, here, elements, unit, line_no))
            offsets[index] += size
            continue

        # 剩下的都当指令
        index = ensure_segment()
        parts = line.split(None, 1)
        prefix = ""
        mnemonic = parts[0].upper()
        rest = parts[1].strip() if len(parts) > 1 else ""
        if mnemonic in _PREFIXES:
            prefix = mnemonic
            parts = rest.split(None, 1)
            if not parts:
                raise _AsmError(f"{prefix} 后面要跟一条字符串指令", line_no)
            mnemonic = parts[0].upper()
            rest = parts[1].strip() if len(parts) > 1 else ""

        if mnemonic not in _ARITY:
            raise _AsmError(f"没有这条指令：{mnemonic}", line_no)
        low, high = _ARITY[mnemonic]
        operands_text = _split_operands(rest) if rest else []
        if not low <= len(operands_text) <= high:
            raise _AsmError(
                f"{mnemonic} 要 {low}~{high} 个操作数，这里给了 {len(operands_text)} 个", line_no)
        operands = [_parse_operand(text, branch=mnemonic in _JCC or mnemonic in
                                   ("JMP", "CALL", "LOOP", "LOOPE", "LOOPZ",
                                    "LOOPNE", "LOOPNZ"))
                    for text in operands_text]

        length = _length(mnemonic, operands)
        record = {
            "mnemonic": mnemonic,
            "operands": operands,
            "prefix": prefix,
            "text": line,
            "line": line_no,
            "length": length,
        }
        code[index][offsets[index]] = record
        segments[index]["has_code"] = True
        if first_code is None:
            first_code = (index, offsets[index])
        offsets[index] += length

    for index, segment in enumerate(segments):
        segment["used"] = offsets.get(index, 0)

    if first_code is None and not items:
        raise _AsmError("这里一条指令都没有，没东西可跑")

    # 定段值：带代码的那个当代码段，其余当数据段
    code_index = first_code[0] if first_code else 0
    if entry_name and entry_name in raw_labels:
        code_index = raw_labels[entry_name][0]
    data_value = DATA_SEG_VALUE
    for index, segment in enumerate(segments):
        if index == code_index:
            segment["value"] = CODE_SEG_VALUE
        else:
            segment["value"] = data_value
            data_value += SEG_STEP

    # 段值定下来了，标签表这才算得出最终值（同址的多个标签一个都不丢）
    labels = {}
    for name, (index, offset) in raw_labels.items():
        labels[name] = (offset, segments[index]["value"])

    symbols = dict(consts)
    for name, entry in labels.items():
        symbols[name] = entry[0]
    for name, index in seg_names.items():
        if name:
            symbols[name] = segments[index]["value"]
            symbols["@" + name] = segments[index]["value"]   # MASM 的 @DATA 写法

    # 第二遍：解析操作数 + 校验 + 存数据
    instructions = {}
    for index, block in code.items():
        for offset, record in block.items():
            try:
                operands = [_resolve_operand(op, symbols, labels, offset)
                            for op in record["operands"]]
                _validate(record["mnemonic"], operands, record["line"])
                width = None
                if record["mnemonic"] in _NEEDS_WIDTH:
                    width = _infer_width(operands, record["line"], record["mnemonic"])
            except _AsmError as exc:
                if not exc.line:
                    exc.line = record["line"]
                raise
            instructions[(index, offset)] = {
                "mnemonic": record["mnemonic"],
                "operands": operands,
                "prefix": record["prefix"],
                "text": record["text"],
                "length": record["length"],
                "width": width,
                "line": record["line"],
            }

    memory_items = []
    for index, offset, elements, unit, line in items:
        try:
            payload = _data_bytes(elements, unit, symbols, offset, line)
        except _AsmError as exc:
            if not exc.line:
                exc.line = line
            raise
        memory_items.append((index, offset, payload))

    code_instructions = {}
    for (index, offset), record in instructions.items():
        if index == code_index:
            code_instructions[offset] = record

    entry = first_code[1] if first_code and first_code[0] == code_index else 0
    if entry_name and entry_name in raw_labels and raw_labels[entry_name][0] == code_index:
        entry = raw_labels[entry_name][1]

    return {
        "segments": segments,
        "code_index": code_index,
        "code": code_instructions,
        "entry": entry,
        "memory_items": memory_items,
        "label_names": label_names,
        "count": len(code_instructions),
        "data_bytes": sum(len(payload) for _, _, payload in memory_items),
        "code_size": segments[code_index]["used"],
    }


# ---------------- CPU ----------------

class _Machine:
    """存着寄存器、内存和标志位，按指令一条条跑。"""

    def __init__(self, mem, model, stdin, max_steps):
        self.mem = mem
        self.model = model
        self.stdin = stdin
        self.stdin_at = 0
        self.max_steps = max_steps
        self.regs16 = {name: 0 for name in _R16}
        self.sregs = {"CS": 0, "DS": 0, "SS": 0, "ES": 0}
        self.ip = 0
        self.flags = F_IF
        self.out = bytearray()
        self.bus = 0
        self.steps = 0
        self.exit_code = None
        self.dirty = set()
        self.truncated = False

    # ---- 寄存器 ----

    def r16(self, name):
        return self.ip if name == "IP" else self.regs16[name]

    def set_r16(self, name, value):
        value &= MASK16
        if name == "IP":
            self.ip = value
        else:
            self.regs16[name] = value

    def r8(self, name):
        parent, shift = _R8[name]
        return (self.regs16[parent] >> shift) & 0xFF

    def set_r8(self, name, value):
        parent, shift = _R8[name]
        mask = 0xFF << shift
        self.regs16[parent] = ((self.regs16[parent] & ~mask) | ((value & 0xFF) << shift)) & MASK16

    def set_sreg(self, name, value):
        if name == "CS":
            raise _Halt("不能改 CS（8086 不允许直接 MOV / POP 到 CS）")
        self.sregs[name] = value & MASK16

    # ---- 标志位 ----

    def flag(self, bit):
        return 1 if self.flags & bit else 0

    def set_flag(self, bit, on):
        if on:
            self.flags |= bit
        else:
            self.flags &= ~bit

    def _flags_word(self):
        """PUSHF 压栈 / IRET 弹栈用的那个「完整 FLAGS」值，带上恒 1 的保留位。"""
        return (self.flags | F_FIXED_ONE) & MASK16

    def _load_flags_word(self, word):
        """POPF / IRET 把 16 位值装回 FLAGS：只认低 12 位的真实标志，保留位恒 1。"""
        self.flags = (word & 0x0FFF) | F_FIXED_ONE

    def set_logic_flags(self, result, bits):
        self.set_flag(F_CF, False)
        self.set_flag(F_OF, False)
        self.set_flag(F_ZF, result == 0)
        self.set_flag(F_SF, (result >> (bits - 1)) & 1)
        self.set_flag(F_PF, _parity_even(result))

    # ---- 内存 ----

    def read_mem(self, phys, bits):
        if bits == 8:
            self.bus += 1
            return self.mem[phys]
        aligned = phys % 2 == 0
        self.bus += 1 if (self.model == "8086" and aligned) else 2
        return self.mem[phys] | (self.mem[(phys + 1) & MASK20] << 8)

    def write_mem(self, phys, value, bits):
        if bits == 8:
            self.bus += 1
            self.mem[phys] = value & 0xFF
            self.dirty.add(phys)
            return
        aligned = phys % 2 == 0
        self.bus += 1 if (self.model == "8086" and aligned) else 2
        self.mem[phys] = value & 0xFF
        self.mem[(phys + 1) & MASK20] = (value >> 8) & 0xFF
        self.dirty.add(phys)
        self.dirty.add((phys + 1) & MASK20)

    def address(self, op):
        seg_hint, base, index, disp = op[1], op[2], op[3], op[4]
        offset = disp & MASK16
        if base:
            offset = (offset + self.regs16[base]) & MASK16
        if index:
            offset = (offset + self.regs16[index]) & MASK16
        if isinstance(seg_hint, str):
            segment = self.sregs[seg_hint]
        elif isinstance(seg_hint, int):
            segment = seg_hint
        else:
            segment = self.sregs["SS" if base == "BP" else "DS"]
        return ((segment << 4) + offset) & MASK20, offset

    # ---- 读 / 写操作数 ----

    def read(self, op, width=None):
        kind = op[0]
        if kind == "r16":
            return self.regs16[op[1]]
        if kind == "r8":
            return self.r8(op[1])
        if kind == "sreg":
            return self.sregs[op[1]]
        if kind == "imm":
            # 立即数按目标位宽截断：CMP AL,-1 里那个 -1 属于 8 位操作数，应为 0xFF；
            # 不截断就成了 0xFFFF，减法借位随之算错（CF 该 0 却给 1）
            return op[1] & (0xFF if width == 8 else MASK16)
        if kind == "mem":
            return self.read_mem(self.address(op)[0], op[5] or width or 16)
        raise _Halt(f"仿真器读不了这个操作数：{op[0]}")

    def write(self, op, value, width=None):
        kind = op[0]
        if kind == "r16":
            self.regs16[op[1]] = value & MASK16
        elif kind == "r8":
            self.set_r8(op[1], value)
        elif kind == "sreg":
            self.set_sreg(op[1], value)
        elif kind == "mem":
            self.write_mem(self.address(op)[0], value, op[5] or width or 16)
        else:
            raise _Halt("不能往立即数里写东西")

    def emit_bytes(self, data):
        """按字节往输出里塞，跟 DOS 一致（一个字节就是一个字节，不做字符转码）。"""
        if len(self.out) >= MAX_OUTPUT_CHARS:
            self.truncated = True
            return
        self.out.extend(data)
        if len(self.out) > MAX_OUTPUT_CHARS:
            del self.out[MAX_OUTPUT_CHARS:]
            self.truncated = True

    def emit(self, text):
        self.emit_bytes(text.encode("utf-8"))

    def read_key(self):
        if self.stdin_at >= len(self.stdin):
            raise _Halt(
                "程序要读键盘，但没给输入内容。要跑交互式的程序，把想敲的东西放在 stdin 参数里")
        ch = self.stdin[self.stdin_at]
        self.stdin_at += 1
        return ord(ch) & 0xFF

    # ---- 栈 ----

    def push(self, value):
        self.regs16["SP"] = (self.regs16["SP"] - 2) & MASK16
        self.write_mem(((self.sregs["SS"] << 4) + self.regs16["SP"]) & MASK20, value, 16)

    def pop(self):
        value = self.read_mem(((self.sregs["SS"] << 4) + self.regs16["SP"]) & MASK20, 16)
        self.regs16["SP"] = (self.regs16["SP"] + 2) & MASK16
        return value

    # ---- 算术 ----

    def do_add(self, kind, dst, src, bits):
        mask = (1 << bits) - 1
        left = self.read(dst, bits)
        right = self.read(src, bits)
        carry = 1 if kind == "ADC" and self.flag(F_CF) else 0
        total = left + right + carry
        result = total & mask
        self.set_flag(F_CF, total > mask)
        self.set_flag(F_OF, ((left ^ result) & (right ^ result) & (1 << (bits - 1))) != 0)
        self.set_flag(F_AF, ((left & 0xF) + (right & 0xF) + carry) > 0xF)
        self.set_flag(F_ZF, result == 0)
        self.set_flag(F_SF, (result >> (bits - 1)) & 1)
        self.set_flag(F_PF, _parity_even(result))
        return result

    def do_sub(self, kind, dst, src, bits):
        mask = (1 << bits) - 1
        left = self.read(dst, bits)
        right = self.read(src, bits)
        # SBB 只在 CF=1 时才多减那个借位（跟上面 do_add 里 ADC 的写法对齐）：
        # 无条件减 1 会让 `CLC; SBB` 变成 5-2-1=2，而真机是 5-2-0=3
        borrow = 1 if kind == "SBB" and self.flag(F_CF) else 0
        total = left - right - borrow
        result = total & mask
        self.set_flag(F_CF, total < 0)
        self.set_flag(F_OF, ((left ^ right) & (left ^ result) & (1 << (bits - 1))) != 0)
        self.set_flag(F_AF, ((left & 0xF) - (right & 0xF) - borrow) < 0)
        self.set_flag(F_ZF, result == 0)
        self.set_flag(F_SF, (result >> (bits - 1)) & 1)
        self.set_flag(F_PF, _parity_even(result))
        return result

    def do_mul(self, operand, bits, signed):
        source = self.read(operand, bits)
        if bits == 8:
            left, right = self.r8("AL"), source
            if signed:
                product = _signed(left, 8) * _signed(right, 8)
            else:
                product = left * right
            self.set_r16("AX", product & MASK16)
            # 有符号要按符号范围判溢出：0FFH * 1 = -1 在 8 位里放得下，CF/OF 应为 0；
            # 用无符号掩码判会把 -1 当成 0xFFFF，误报溢出
            if signed:
                overflow = not -128 <= product <= 127
            else:
                overflow = product != (product & 0xFF)
        else:
            left, right = self.regs16["AX"], source
            if signed:
                product = _signed(left, 16) * _signed(right, 16)
            else:
                product = left * right
            self.regs16["AX"] = product & MASK16
            self.regs16["DX"] = (product >> 16) & MASK16
            if signed:
                overflow = not -(1 << 15) <= product <= (1 << 15) - 1
            else:
                overflow = product != (product & MASK16)
        # 8086 上 MUL / IMUL 只保证 CF 和 OF 有意义，其余标志位是未定义的
        self.set_flag(F_CF, overflow)
        self.set_flag(F_OF, overflow)

    def do_div(self, operand, bits, signed):
        source = self.read(operand, bits)
        if source == 0:
            raise _Halt("除数是 0——8086 会触发 INT 0 除法错中断，程序在这里停住了")
        if bits == 8:
            dividend = self.r16("AX")
            if signed:
                dividend = _signed(dividend, 16)
                divisor = _signed(source, 8)
            else:
                divisor = source
            quotient, remainder = _trunc_div(dividend, divisor)
            # 商的合法范围随有无符号而变：IDIV 是 -128..127，DIV 是 0..255
            lo, hi = (-128, 127) if signed else (0, 255)
            if not lo <= quotient <= hi:
                raise _Halt("DIV / IDIV 的商放不进 AL（8086 会触发 INT 0），程序停住了")
            self.set_r8("AL", quotient & 0xFF)
            self.set_r8("AH", remainder & 0xFF)
        else:
            dividend = (self.regs16["DX"] << 16) | self.regs16["AX"]
            if signed:
                dividend = _signed(dividend, 32)
                divisor = _signed(source, 16)
            else:
                divisor = source
            quotient, remainder = _trunc_div(dividend, divisor)
            lo, hi = (-(1 << 15), (1 << 15) - 1) if signed else (0, MASK16)
            if not lo <= quotient <= hi:
                raise _Halt("DIV / IDIV 的商放不进 AX（8086 会触发 INT 0），程序停住了")
            self.regs16["AX"] = quotient & MASK16
            self.regs16["DX"] = remainder & MASK16
        # 8086 上 DIV / IDIV 的标志位同样没有定义，这里不去猜

    def do_shift(self, mnemonic, dst, count, bits):
        if count == 0:
            return
        mask = (1 << bits) - 1
        sign_bit = 1 << (bits - 1)
        value = self.read(dst, bits)
        result = value
        carry = self.flag(F_CF)
        for _ in range(count):
            if mnemonic in ("SHL", "SAL"):
                carry = 1 if result & sign_bit else 0
                result = (result << 1) & mask
            elif mnemonic == "SHR":
                carry = result & 1
                result = (result >> 1) & mask
            elif mnemonic == "SAR":
                carry = result & 1
                result = ((result >> 1) | (result & sign_bit)) & mask
            elif mnemonic == "ROL":
                carry = 1 if result & sign_bit else 0
                result = ((result << 1) | carry) & mask
            elif mnemonic == "ROR":
                carry = result & 1
                result = ((result >> 1) | (carry << (bits - 1))) & mask
            elif mnemonic == "RCL":
                carry_out = 1 if result & sign_bit else 0
                result = ((result << 1) & mask) | carry
                carry = carry_out
            else:                                   # RCR
                carry_out = result & 1
                result = (result >> 1) | (carry << (bits - 1))
                carry = carry_out
        self.write(dst, result, bits)
        self.set_flag(F_CF, carry)
        if count == 1:
            if mnemonic in ("SHL", "SAL", "RCL"):
                self.set_flag(F_OF, self.flag(F_CF) != ((result >> (bits - 1)) & 1))
            elif mnemonic == "SHR":
                self.set_flag(F_OF, bool(value & sign_bit))
            elif mnemonic == "SAR":
                self.set_flag(F_OF, False)
            elif mnemonic in ("ROR", "RCR"):
                # 右旋的 OF 看结果最高两位是否不同（Intel SDM 正文：For right rotates,
                # the OF flag is set to the exclusive OR of the two most-significant bits
                # of the result）。RCR 跟 ROR 同族，所以不能套 ROL 那条 MSB(结果)^CF。
                # SDM 里 RCR 的操作伪码写的是 OF := MSB(DEST) XOR CF，而且排在旋转循环
                # 之前，跟同一页正文自相矛盾（RCL 那段的 OF 就规规矩矩排在循环之后）；
                # 这里按正文统一，别照伪码改回去
                self.set_flag(F_OF,
                              ((result >> (bits - 1)) & 1) != ((result >> (bits - 2)) & 1))
            else:                                   # ROL
                self.set_flag(F_OF, ((result >> (bits - 1)) & 1) != self.flag(F_CF))
        if mnemonic in ("SHL", "SAL", "SHR", "SAR"):
            self.set_flag(F_ZF, result == 0)
            self.set_flag(F_SF, (result >> (bits - 1)) & 1)
            self.set_flag(F_PF, _parity_even(result))

    # ---- 中断 ----

    def interrupt(self, number):
        """DOS / BIOS 中断直接内置处理，不走中断向量表。"""
        if number == 0x20:
            self.exit_code = 0
            raise _Halt("用 INT 20H 结束程序（返回码 0）")
        if number == 0x21:
            function = self.r8("AH")
            if function == 0x09:
                segment, offset = self.sregs["DS"], self.r16("DX")
                chars = bytearray()
                for _ in range(MAX_STRING_SCAN):
                    byte = self.mem[((segment << 4) + offset) & MASK20]
                    if byte == 0x24:            # '$'
                        break
                    chars.append(byte)
                    offset = (offset + 1) & MASK16
                    self.bus += 1
                else:
                    raise _Halt(
                        f"INT 21H 的 09H 要打印的那串字符没找到结束的 '$'"
                        f"（往后读了 {MAX_STRING_SCAN} 个字节还没碰上），"
                        "检查一下字符串结尾是不是漏了 $")
                self.emit_bytes(bytes(chars))
                return
            if function == 0x02:
                self.emit_bytes(bytes([self.r8("DL")]))
                return
            if function == 0x01:
                char = self.read_key()
                self.emit_bytes(bytes([char]))
                self.set_r8("AL", char)
                return
            if function == 0x0A:
                segment, offset = self.sregs["DS"], self.r16("DX")
                limit = self.mem[((segment << 4) + offset) & MASK20]
                chars = []
                while len(chars) < max(0, limit - 1):
                    char = self.read_key()
                    if char in (0x0D, 0x0A):
                        break
                    chars.append(char)
                base = ((segment << 4) + offset) & MASK20
                self.mem[(base + 1) & MASK20] = len(chars)
                for index, char in enumerate(chars):
                    self.mem[(base + 2 + index) & MASK20] = char
                self.mem[(base + 2 + len(chars)) & MASK20] = 0x0D
                self.emit("".join(chr(char) for char in chars) + "\n")
                return
            if function == 0x06:
                if self.r8("DL") == 0xFF:
                    self.set_flag(F_ZF, True)
                    self.set_r8("AL", 0)
                else:
                    self.emit_bytes(bytes([self.r8("DL")]))
                return
            if function == 0x00:
                self.exit_code = 0
                raise _Halt("用 INT 21H 的 00H 结束程序（返回码 0）")
            if function == 0x4C:
                self.exit_code = self.r8("AL")
                raise _Halt(f"用 INT 21H 的 4CH 正常退出（返回码 {self.exit_code}）")
            raise _Halt(
                f"INT 21H 的 {function:02X}H 号功能没实现。这里只做了 "
                "01H 读字符 / 02H 打印字符 / 06H 直接控制台 / 09H 打印字符串 / "
                "0AH 读一行 / 00H 和 4CH 结束程序")
        if number == 0x10:
            function = self.r8("AH")
            if function == 0x0E:
                self.emit_bytes(bytes([self.r8("AL")]))
                return
            if function == 0x09:
                count = min(self.r16("CX") or 1, MAX_OUTPUT_CHARS)
                self.emit_bytes(bytes([self.r8("AL")]) * count)
                return
            if function in (0x02, 0x06, 0x07):
                return                              # 光标 / 滚屏这类，仿真里当没发生
            raise _Halt(f"INT 10H 的 {function:02X}H 号功能没实现（这里只做了 02H / 09H / 0EH）")
        if number in (0x11, 0x12, 0x1A):
            return                                  # 设备 / 内存大小 / 时钟：给个空回应
        raise _Halt(f"INT {number:02X}H 没实现。这里只认 INT 20H / 21H / 10H 这几个")

    # ---- 一条指令 ----

    def execute(self, record):
        mnemonic = record["mnemonic"]
        operands = record["operands"]
        width = record["width"]
        # 取指：8086 一次搬 16 位（两个字节能一个周期搬完），8088 只能一次一个字节
        self.bus += (record["length"] if self.model == "8088"
                     else max(1, (record["length"] + 1) // 2))

        if mnemonic == "NOP" or mnemonic == "WAIT":
            return
        if mnemonic == "HLT":
            raise _Halt("执行到 HLT，停机")
        if mnemonic == "MOV":
            dst, src = operands
            if dst[0] == "sreg":
                self.set_sreg(dst[1], self.read(src, 16))
            else:
                self.write(dst, self.read(src, width), width)
            return
        if mnemonic == "XCHG":
            dst, src = operands
            left = self.read(dst, width)
            right = self.read(src, width)
            self.write(dst, right, width)
            self.write(src, left, width)
            return
        if mnemonic == "LEA":
            self.write(operands[0], self.address(operands[1])[1], 16)
            return
        if mnemonic == "XLAT":
            offset = (self.regs16["BX"] + self.r8("AL")) & MASK16
            self.set_r8("AL", self.read_mem(((self.sregs["DS"] << 4) + offset) & MASK20, 8))
            return
        if mnemonic in ("ADD", "ADC", "SUB", "SBB", "CMP"):
            dst, src = operands
            if mnemonic in ("ADD", "ADC"):
                result = self.do_add(mnemonic, dst, src, width)
            else:
                result = self.do_sub(mnemonic, dst, src, width)
            if mnemonic != "CMP":
                self.write(dst, result, width)
            return
        if mnemonic == "INC" or mnemonic == "DEC":
            dst = operands[0]
            value = self.read(dst, width)
            mask = (1 << width) - 1
            if mnemonic == "INC":
                result = (value + 1) & mask
                self.set_flag(F_OF, result == (1 << (width - 1)))
                self.set_flag(F_AF, (value & 0xF) == 0xF)
                # INC / DEC 不动 CF，这是 8086 上很容易踩的坑
            else:
                result = (value - 1) & mask
                self.set_flag(F_OF, result == ((1 << (width - 1)) - 1))
                self.set_flag(F_AF, (value & 0xF) == 0)
            self.set_flag(F_ZF, result == 0)
            self.set_flag(F_SF, (result >> (width - 1)) & 1)
            self.set_flag(F_PF, _parity_even(result))
            self.write(dst, result, width)
            return
        if mnemonic == "NEG":
            dst = operands[0]
            value = self.read(dst, width)
            mask = (1 << width) - 1
            result = (-value) & mask
            self.set_flag(F_CF, value != 0)
            self.set_flag(F_OF, value == (1 << (width - 1)))
            self.set_flag(F_AF, (value & 0xF) != 0)
            self.set_flag(F_ZF, result == 0)
            self.set_flag(F_SF, (result >> (width - 1)) & 1)
            self.set_flag(F_PF, _parity_even(result))
            self.write(dst, result, width)
            return
        if mnemonic in ("MUL", "IMUL"):
            self.do_mul(operands[0], width, mnemonic == "IMUL")
            return
        if mnemonic in ("DIV", "IDIV"):
            self.do_div(operands[0], width, mnemonic == "IDIV")
            return
        if mnemonic == "CBW":
            self.set_r16("AX", _signed(self.r8("AL"), 8) & MASK16)
            return
        if mnemonic == "CWD":
            self.regs16["DX"] = 0xFFFF if self.regs16["AX"] & 0x8000 else 0
            return
        if mnemonic in ("AND", "OR", "XOR", "TEST"):
            dst, src = operands
            left = self.read(dst, width)
            right = self.read(src, width)
            if mnemonic == "AND" or mnemonic == "TEST":
                result = left & right
            elif mnemonic == "OR":
                result = left | right
            else:
                result = left ^ right
            self.set_logic_flags(result, width)
            if mnemonic != "TEST":
                self.write(dst, result, width)
            return
        if mnemonic == "NOT":
            dst = operands[0]
            self.write(dst, (~self.read(dst, width)) & ((1 << width) - 1), width)
            return
        if mnemonic in _SHIFTS:
            count = operands[1]
            if count[0] == "imm":
                amount = count[1] & 0xFF
            elif count[0] == "r8":
                amount = self.r8("CL")
            else:
                amount = 1
            self.do_shift(mnemonic, operands[0], amount, width)
            return
        if mnemonic == "PUSH":
            # 8086 的 `PUSH SP` 压的是自减之后的 SP（286 之后才改成压自减前的旧值）。
            # 之所以按 8086 来：这里的操作数是先读出来再压栈的，直接 read 会拿到旧 SP，
            # 只有 SP 这一个寄存器需要在读的时候先按 -2 算
            value = self.read(operands[0], 16)
            if operands[0] == ("r16", "SP"):
                value = (value - 2) & MASK16
            self.push(value)
            return
        if mnemonic == "POP":
            self.write(operands[0], self.pop(), 16)
            return
        if mnemonic == "PUSHF":
            self.push(self._flags_word())
            return
        if mnemonic == "POPF":
            self._load_flags_word(self.pop())
            return
        if mnemonic == "CLC":
            self.set_flag(F_CF, False)
            return
        if mnemonic == "STC":
            self.set_flag(F_CF, True)
            return
        if mnemonic == "CMC":
            self.set_flag(F_CF, not self.flag(F_CF))
            return
        if mnemonic == "CLD":
            self.set_flag(F_DF, False)
            return
        if mnemonic == "STD":
            self.set_flag(F_DF, True)
            return
        if mnemonic == "CLI":
            self.set_flag(F_IF, False)
            return
        if mnemonic == "STI":
            self.set_flag(F_IF, True)
            return
        if mnemonic == "INT":
            self.interrupt(self.read(operands[0], 8))
            return
        if mnemonic == "IRET":
            self.ip = self.pop()
            self.sregs["CS"] = self.pop()
            self._load_flags_word(self.pop())
            return
        if mnemonic == "CALL":
            self.push(self.ip)
            self.ip = operands[0][1] & MASK16
            return
        if mnemonic == "RET":
            value = self.pop()
            if operands:
                self.regs16["SP"] = (self.regs16["SP"] + (operands[0][1] & MASK16)) & MASK16
            self.ip = value
            return
        if mnemonic == "JMP":
            self.ip = operands[0][1] & MASK16
            return
        if mnemonic in _JCC:
            if _condition(_JCC[mnemonic], self):
                self.ip = operands[0][1] & MASK16
            return
        if mnemonic.startswith("LOOP"):
            self.regs16["CX"] = (self.regs16["CX"] - 1) & MASK16
            jump = True
            if mnemonic in ("LOOPE", "LOOPZ"):
                jump = self.flag(F_ZF) == 1
            elif mnemonic in ("LOOPNE", "LOOPNZ"):
                jump = self.flag(F_ZF) == 0
            if self.regs16["CX"] != 0 and jump:
                self.ip = operands[0][1] & MASK16
            return
        if mnemonic in _STRING_OPS:
            self.do_string(record)
            return
        raise _Halt(f"仿真器还不认识这条指令：{mnemonic}")

    def do_string(self, record):
        mnemonic = record["mnemonic"]
        prefix = record["prefix"]
        bits = 8 if mnemonic.endswith("B") else 16
        step = 1 if bits == 8 else 2
        repeat = prefix in _PREFIXES
        # 真机上 F3 只有一个前缀：跟在 MOVS / STOS / LODS 后面是无条件重复的 REP，
        # 跟在 CMPS / SCAS 后面就是 REPE / REPZ（相等才继续，看 ZF）。
        # 汇编器这边按名字分了 REP 和 REPE / REPZ，所以这里把裸 REP 补成 REPE，
        # 免得 `REP CMPSB` 被当成无条件扫到底、把「不等就停」的终止条件丢掉
        if prefix == "REP" and mnemonic.startswith(("CMPS", "SCAS")):
            prefix = "REPE"
        condition = prefix in ("REPE", "REPZ", "REPNE", "REPNZ")
        want_zero = prefix in ("REPE", "REPZ")

        first = True
        while True:
            if repeat and not first:
                self.steps += 1
                if self.steps >= self.max_steps:
                    raise _Halt(f"达到步数上限 {self.max_steps} 就停了（多半是死循环）")
            first = False
            if repeat and self.regs16["CX"] == 0:
                return
            delta = -step if self.flag(F_DF) else step
            if mnemonic.startswith("MOVS"):
                src = ((self.sregs["DS"] << 4) + self.regs16["SI"]) & MASK20
                dst = ((self.sregs["ES"] << 4) + self.regs16["DI"]) & MASK20
                self.write_mem(dst, self.read_mem(src, bits), bits)
                self.regs16["SI"] = (self.regs16["SI"] + delta) & MASK16
                self.regs16["DI"] = (self.regs16["DI"] + delta) & MASK16
            elif mnemonic.startswith("STOS"):
                dst = ((self.sregs["ES"] << 4) + self.regs16["DI"]) & MASK20
                self.write_mem(dst, self.r8("AL") if bits == 8 else self.regs16["AX"], bits)
                self.regs16["DI"] = (self.regs16["DI"] + delta) & MASK16
            elif mnemonic.startswith("LODS"):
                src = ((self.sregs["DS"] << 4) + self.regs16["SI"]) & MASK20
                value = self.read_mem(src, bits)
                if bits == 8:
                    self.set_r8("AL", value)
                else:
                    self.regs16["AX"] = value
                self.regs16["SI"] = (self.regs16["SI"] + delta) & MASK16
            elif mnemonic.startswith("CMPS"):
                left = self.read_mem(((self.sregs["DS"] << 4) + self.regs16["SI"]) & MASK20, bits)
                right = self.read_mem(((self.sregs["ES"] << 4) + self.regs16["DI"]) & MASK20, bits)
                self._set_cmp_flags(left, right, bits)
                self.regs16["SI"] = (self.regs16["SI"] + delta) & MASK16
                self.regs16["DI"] = (self.regs16["DI"] + delta) & MASK16
            else:                                   # SCAS
                left = self.r8("AL") if bits == 8 else self.regs16["AX"]
                right = self.read_mem(((self.sregs["ES"] << 4) + self.regs16["DI"]) & MASK20, bits)
                self._set_cmp_flags(left, right, bits)
                self.regs16["DI"] = (self.regs16["DI"] + delta) & MASK16
            if not repeat:
                return
            self.regs16["CX"] = (self.regs16["CX"] - 1) & MASK16
            if condition and mnemonic.startswith(("CMPS", "SCAS")):
                if want_zero and self.flag(F_ZF) == 0:
                    return
                if not want_zero and self.flag(F_ZF) == 1:
                    return

    def _set_cmp_flags(self, left, right, bits):
        mask = (1 << bits) - 1
        total = left - right
        result = total & mask
        self.set_flag(F_CF, total < 0)
        self.set_flag(F_OF, ((left ^ right) & (left ^ result) & (1 << (bits - 1))) != 0)
        self.set_flag(F_AF, ((left & 0xF) - (right & 0xF)) < 0)
        self.set_flag(F_ZF, result == 0)
        self.set_flag(F_SF, (result >> (bits - 1)) & 1)
        self.set_flag(F_PF, _parity_even(result))


def _trunc_div(dividend, divisor):
    """向 0 取整的除法，同时给出余数（汇编里的除法就是向 0 截断）。"""
    quotient = abs(dividend) // abs(divisor)
    if (dividend < 0) != (divisor < 0):
        quotient = -quotient
    return quotient, dividend - quotient * divisor


def _condition(suffix, machine):
    cf = machine.flag(F_CF)
    zf = machine.flag(F_ZF)
    sf = machine.flag(F_SF)
    of = machine.flag(F_OF)
    pf = machine.flag(F_PF)
    table = {
        "O": of == 1, "NO": of == 0,
        "B": cf == 1, "C": cf == 1, "NAE": cf == 1,
        "AE": cf == 0, "NB": cf == 0, "NC": cf == 0,
        "E": zf == 1, "Z": zf == 1, "NE": zf == 0, "NZ": zf == 0,
        "BE": cf == 1 or zf == 1, "NA": cf == 1 or zf == 1,
        "A": cf == 0 and zf == 0, "NBE": cf == 0 and zf == 0,
        "S": sf == 1, "NS": sf == 0,
        "P": pf == 1, "PE": pf == 1, "NP": pf == 0, "PO": pf == 0,
        "L": sf != of, "NGE": sf != of,
        "GE": sf == of, "NL": sf == of,
        "LE": zf == 1 or sf != of, "NG": zf == 1 or sf != of,
        "G": zf == 0 and sf == of, "NLE": zf == 0 and sf == of,
    }
    return table[suffix]


# ---------------- 跑起来并组织输出 ----------------

def _flags_text(flags):
    return " ".join(f"{name}={1 if flags & bit else 0}" for name, bit in FLAG_ORDER)


def _reg_lines(machine):
    groups = ("AX", "BX", "CX", "DX"), ("SI", "DI", "BP", "SP")
    lines = []
    for group in groups:
        cells = [f"{name}={machine.regs16[name]:04X}" for name in group]
        lines.append("  " + "  ".join(cells))
    sregs = "  ".join(f"{name}={machine.sregs[name]:04X}" for name in ("CS", "DS", "SS", "ES"))
    lines.append(f"  {sregs}  IP={machine.ip:04X}")
    return lines


def _memory_rows(memory, program, dirty, initial):
    """把有内容的字节按 16 字节一行摆出来，数据段和运行中改过的地方都算。"""
    addresses = set()
    for index, offset, payload in program["memory_items"]:
        base = (program["segments"][index]["value"] << 4) + offset
        for position, value in enumerate(payload):
            if value:
                addresses.add(base + position)
    for phys in dirty:
        if memory[phys] != initial[phys]:
            addresses.add(phys)
    if not addresses:
        return []
    label_at = {}
    for index, block in program["label_names"].items():
        for offset, name in block.items():
            label_at[(program["segments"][index]["value"] << 4) + offset] = name

    rows = []
    bases = sorted({address & ~0xF for address in addresses})
    for base in bases[:MAX_MEM_ROWS]:
        chunk = memory[base:base + 16]
        text = " ".join(f"{byte:02X}" for byte in chunk)
        tag = f"   ← {label_at[base]}" if base in label_at else ""
        rows.append(f"  {base >> 4:04X}:{base & 0xF:04X}  {text}{tag}")
    if len(bases) > MAX_MEM_ROWS:
        rows.append(f"  …… 还有 {len(bases) - MAX_MEM_ROWS} 行没显示")
    return rows


def asm86(source: str = "", path: str = "", cpu: str = "8086", stdin: str = "",
          max_steps: int = DEFAULT_MAX_STEPS, trace: bool = False) -> str:
    """跑一段 8086 / 8088 实模式汇编，回报输出、寄存器、标志位和内存变化。"""
    if path:
        try:
            full = safe_path(path)
        except ValueError as exc:
            return f"错误：{exc}"
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as handle:
                source = handle.read()
        except OSError as exc:
            return f"错误：读不了 {path}（{exc}）"
    if not (source or "").strip():
        return "错误：没给汇编源码，往 source 里传（或者用 path 指一个 .asm 文件）"
    if len(source) > MAX_SOURCE:
        return f"错误：源码太长了（{len(source)} 字符，上限 {MAX_SOURCE}）"

    model = (cpu or "8086").strip().upper()
    if model not in ("8086", "8088"):
        return f"错误：cpu 只能选 8086 或者 8088，这里给的是 {cpu!r}"
    if model == "8088":
        model = "8088"
    else:
        model = "8086"

    try:
        max_steps = max(1, min(int(max_steps), MAX_STEPS_LIMIT))
    except (TypeError, ValueError):
        max_steps = DEFAULT_MAX_STEPS

    try:
        program = _assemble(source)
    except _AsmError as exc:
        return f"汇编失败：{exc}"
    except RecursionError:
        return "汇编失败：表达式嵌套得太深了"
    except MemoryError:
        return "汇编失败：数据定义太大，内存不够展开"

    memory = bytearray(MEM_SIZE)
    for index, offset, payload in program["memory_items"]:
        base = ((program["segments"][index]["value"] << 4) + offset) & MASK20
        memory[base:base + len(payload)] = bytes(payload)
    initial = bytes(memory)

    machine = _Machine(memory, model, stdin or "", max_steps)
    code_index = program["code_index"]
    machine.sregs["CS"] = program["segments"][code_index]["value"]
    machine.sregs["SS"] = machine.sregs["CS"]
    data_value = 0
    for index, segment in enumerate(program["segments"]):
        if index != code_index:
            data_value = segment["value"]
            break
    else:
        data_value = machine.sregs["CS"]
    machine.sregs["DS"] = data_value
    machine.sregs["ES"] = data_value
    machine.regs16["SP"] = 0xFFFE
    machine.ip = program["entry"]

    code = program["code"]
    trace_lines = []
    stopped = ""
    while True:
        if machine.exit_code is not None:
            stopped = f"用 INT 21H 的 4CH 正常退出（返回码 {machine.exit_code}）"
            break
        if machine.steps >= max_steps:
            stopped = f"达到步数上限 {max_steps} 就停了（多半是死循环，检查一下跳转条件）"
            break
        record = code.get(machine.ip)
        if record is None:
            stopped = (f"CS:IP = {machine.sregs['CS']:04X}:{machine.ip:04X} 这里不是指令"
                       "（程序跑过头了，或者没写结束的出口）")
            break

        before_flags = machine.flags
        before_regs = dict(machine.regs16)
        before_sregs = dict(machine.sregs)
        here = machine.ip
        machine.ip = (machine.ip + record["length"]) & MASK16
        try:
            machine.execute(record)
        except _Halt as exc:
            stopped = str(exc)
            machine.steps += 1
            if trace and len(trace_lines) < MAX_TRACE:
                trace_lines.append(
                    f"  {machine.steps:>5}  {machine.sregs['CS']:04X}:{here:04X}  {record['text']}")
            break
        except (IndexError, KeyError, ValueError, ZeroDivisionError) as exc:
            stopped = f"跑到 {record['text']} 这条时仿真器出错了（{exc}），八成是指令用错了操作数"
            machine.steps += 1
            break
        machine.steps += 1

        if trace and len(trace_lines) < MAX_TRACE:
            changes = []
            for name in _R16:
                if machine.regs16[name] != before_regs[name]:
                    changes.append(f"{name}={machine.regs16[name]:04X}")
            for name in ("CS", "DS", "SS", "ES"):
                if machine.sregs[name] != before_sregs[name]:
                    changes.append(f"{name}={machine.sregs[name]:04X}")
            if machine.ip != here + record["length"]:
                changes.append(f"IP={machine.ip:04X}")
            for name, bit in FLAG_ORDER:
                if (machine.flags & bit) != (before_flags & bit):
                    changes.append(f"{name}={1 if machine.flags & bit else 0}")
            trace_lines.append(
                f"  {machine.steps:>5}  {machine.sregs['CS']:04X}:{here:04X}  "
                f"{record['text']:<28}" + ("-> " + " ".join(changes) if changes else ""))

    segments = program["segments"]
    layout = f"代码段 {segments[code_index]['value']:04X}H"
    others = [f"{segment['name'] or '数据'}={segment['value']:04X}H"
              for index, segment in enumerate(segments) if index != code_index]
    if others:
        layout += "，" + "，".join(others)

    lines = [
        f"程序：{model} 实模式，{program['count']} 条指令，数据 {program['data_bytes']} 字节"
        f"（{layout}）",
        f"停机：{stopped}（共执行 {machine.steps} 步）",
        f"估算总线周期：{machine.bus} 个"
        f"（{model} 的 {'16' if model == '8086' else '8'} 位数据总线，"
        "取指 + 数据访问，不含等待周期）",
    ]

    text = bytes(machine.out).decode("utf-8", "replace")
    if text:
        lines.append("输出：")
        lines.extend("  " + row for row in text.splitlines() or [text])
        if machine.truncated:
            lines.append(f"  ……（输出太长，只留了前 {MAX_OUTPUT_CHARS} 个字符）")
    elif machine.out:
        lines.append("输出：（有内容，但不是能显示的文本）")

    lines.append(f"标志位：{_flags_text(machine.flags)}")
    lines.append("寄存器：")
    lines.extend(_reg_lines(machine))

    rows = _memory_rows(memory, program, machine.dirty, initial)
    if rows:
        lines.append("内存（有内容的字节）：")
        lines.extend(rows)
    else:
        lines.append("内存：没有非零数据，也没有被写过")

    if trace:
        if trace_lines:
            lines.append(f"逐步执行（最多显示前 {MAX_TRACE} 步）：")
            lines.extend(trace_lines)
            if machine.steps > len(trace_lines):
                lines.append(f"  …… 后面还有 {machine.steps - len(trace_lines)} 步没显示")
        else:
            lines.append("逐步执行：（一条没跑）")

    return "\n".join(lines)


SPECS = [
    {
        "type": "function",
        "function": {
            "name": "asm86",
            "description": (
                "跑一段 8086 / 8088 实模式汇编：自带汇编器 + CPU 仿真 + 最小 DOS 中断，"
                "返回程序输出、寄存器、标志位、内存变化；trace=true 时给逐步执行过程。"
                "认 MASM 常见写法（SEGMENT/ENDS、ASSUME、PROC/ENDP、DB/DW/DUP、OFFSET 等），"
                "含常用指令、条件跳转、CALL/RET、字符串操作和 INT 21H / INT 10H 常用功能。"
                "讲指令、寻址方式、标志位怎么变，或要看这段汇编跑出什么结果时用它；程序要读键盘就把字符放 stdin。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "8086 汇编源码，用 \\n 换行",
                    },
                    "path": {
                        "type": "string",
                        "description": "可选。workspace 里的 .asm 文件路径；传了它就忽略 source",
                    },
                    "cpu": {
                        "type": "string",
                        "description": "8086 还是 8088，默认 8086。两者指令集相同，8088 是 8 位数据总线，总线周期估算会更多",
                    },
                    "stdin": {
                        "type": "string",
                        "description": "可选。程序用 INT 21H 的 01H / 0AH 读键盘时，从这里按顺序取字符",
                    },
                    "max_steps": {
                        "type": "integer",
                        "description": "最多执行多少条指令，默认 200000，用来防死循环",
                    },
                    "trace": {
                        "type": "boolean",
                        "description": "是否附上逐步执行过程，默认 false",
                    },
                },
                "required": [],
            },
        },
    },
]
