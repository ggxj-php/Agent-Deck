"""小型 CPU 模拟器：一套精简的 16 位指令集，跑汇编看寄存器和内存怎么变。

给计算机组成原理 / 单片机教学用：寄存器 R0~R7，标志位 Z/N/C/V，内存 256 个字，
指令和数据共用这块内存，数值一律按 16 位回绕（超过 0xFFFF 就绕回来）。
纯标准库实现，不联网、不起子进程，跨平台。

格式约定：一行一条指令，`;` 或 `#` 后面是注释，`名字:` 定义标签。
数据用 DW（一个或多个字）和 DS（预留几个零字）声明；因为数据和指令共用内存，
数据一般写在 HALT 后面，免得被当成指令执行。
"""
import re

from ..sandbox import safe_path

MEM_SIZE = 256
REG_COUNT = 8
MASK = 0xFFFF
# 教学程序里几千次迭代的循环很常见（每轮好几条指令），上限太小会把正常程序误判成死循环
DEFAULT_MAX_STEPS = 200000
MAX_TRACE = 200
MAX_MEM_DUMP = 40

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "asm_sim",
            "description": (
                "在自带的 16 位 CPU 模拟器上跑一段汇编，返回执行步数、寄存器、标志位和改过的内存，"
                "trace=true 时给逐步执行过程。讲指令周期 / 寄存器 / 内存 / 标志位怎么动时用它。"
                "模型：R0~R7、标志 Z/N/C/V、内存 256 字、16 位回绕。"
                "指令：LI/MOV/LOAD/STORE/ADD/SUB/MUL/AND/OR/XOR/ADDI/SUBI/NOT/SHL/SHR/INC/DEC/CMP/"
                "JMP/JZ/JNZ/JC/JNC/JGT/JLT/HALT/NOP，数据用 DW/DS。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "汇编源码，一行一条指令。用 \\n 换行",
                    },
                    "path": {
                        "type": "string",
                        "description": "可选。workspace 里的 .asm 文件路径；传了它就忽略 source",
                    },
                    "max_steps": {
                        "type": "integer",
                        "description": "最多执行多少步，默认 200000，用来防死循环；"
                                       "正常程序被误判成死循环时可以调大，想更快停下来可以调小",
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

# 指令 -> 操作数个数，用来在汇编时校验
_ARITY = {
    "HALT": 0, "NOP": 0,
    "LI": 2, "MOV": 2, "LOAD": 2, "STORE": 2, "NOT": 2,
    "SHL": 3, "SHR": 3, "INC": 1, "DEC": 1, "CMP": 2,
    "ADD": 3, "SUB": 3, "MUL": 3, "AND": 3, "OR": 3, "XOR": 3,
    "ADDI": 3, "SUBI": 3,
    "JMP": 1, "JZ": 1, "JNZ": 1, "JC": 1, "JNC": 1, "JGT": 1, "JLT": 1,
}

# 三个操作数里最后一个当寄存器看 / 当立即数看的
_RRR = ("ADD", "SUB", "MUL", "AND", "OR", "XOR")
_RRI = ("ADDI", "SUBI")
_RSI = ("SHL", "SHR")
_RR = ("MOV", "NOT", "CMP")
_R = ("INC", "DEC")
_JUMP = ("JMP", "JZ", "JNZ", "JC", "JNC", "JGT", "JLT")

_LABEL_RE = re.compile(r"^([A-Za-z_]\w*)\s*:(.*)$", re.S)


def _strip_comment(line: str) -> str:
    """去掉 ; 和 # 后面的注释。"""
    cut = len(line)
    for mark in (";", "#"):
        pos = line.find(mark)
        if pos >= 0:
            cut = min(cut, pos)
    return line[:cut]


def _number(token: str) -> int:
    """解析十进制 / 0x / 0b / 0o 字面量，按 16 位回绕。"""
    text = token.strip().rstrip(",").strip()
    sign = -1 if text[:1] == "-" else 1
    if text[:1] in "+-":
        text = text[1:]
    lowered = text.lower()
    try:
        if lowered.startswith("0x"):
            value = int(text[2:], 16)
        elif lowered.startswith("0b"):
            value = int(text[2:], 2)
        elif lowered.startswith("0o"):
            value = int(text[2:], 8)
        else:
            value = int(text, 10)
    except ValueError:
        raise ValueError(f"看不懂的数：{token.strip()}")
    return (sign * value) & MASK


def _reg(token: str):
    """解析寄存器名 R0~R7。"""
    match = re.fullmatch(r"R([0-7])", token.strip().rstrip(",").strip().upper())
    if not match:
        raise ValueError(f"没有这个寄存器：{token.strip()}（只有 R0~R7）")
    return int(match.group(1))


def _operands(text: str) -> list:
    return [part for part in re.split(r"[\s,]+", text.strip()) if part]


def _size_of(text: str) -> int:
    """这条语句占几个字。"""
    parts = _operands(text)
    head = parts[0].upper()
    if head == "DW":
        return max(1, len(parts) - 1)
    if head == "DS":
        if len(parts) != 2:
            raise ValueError("DS 要写成「DS 个数」")
        count = _number(parts[1])
        if count < 1 or count > MEM_SIZE:
            raise ValueError(f"DS 的个数要在 1~{MEM_SIZE} 之间")
        return count
    return 1


def _encode(text: str, labels: dict) -> list:
    """把一条语句翻译成一个或多个内存字。"""
    parts = _operands(text)
    op = parts[0].upper()
    args = parts[1:]

    if op == "DW":
        if not args:
            raise ValueError("DW 后面要跟至少一个数")
        return [("DW", _number(arg)) for arg in args]
    if op == "DS":
        return [("DW", 0)] * _number(args[0])

    if op not in _ARITY:
        raise ValueError(f"没有这条指令：{op}")
    need = _ARITY[op]
    if len(args) != need:
        raise ValueError(f"{op} 需要 {need} 个操作数，这里给了 {len(args)} 个")

    def target(token):
        """跳转目标：标签名或直接给地址"""
        token = token.strip()
        if token in labels:
            return labels[token]
        return _number(token)

    if op in ("HALT", "NOP"):
        return [(op,)]
    if op == "LI":
        return [(op, _reg(args[0]), _number(args[1]))]
    if op == "MOV":
        return [(op, _reg(args[0]), _reg(args[1]))]
    if op == "LOAD":
        return [(op, _reg(args[0]), target(args[1]))]
    if op == "STORE":
        return [(op, _reg(args[0]), target(args[1]))]
    if op in _RRR:
        return [(op, _reg(args[0]), _reg(args[1]), _reg(args[2]))]
    if op in _RRI:
        return [(op, _reg(args[0]), _reg(args[1]), _number(args[2]))]
    if op == "NOT":
        return [(op, _reg(args[0]), _reg(args[1]))]
    if op in _RSI:
        return [(op, _reg(args[0]), _reg(args[1]), _number(args[2]))]
    if op in _R:
        return [(op, _reg(args[0]))]
    if op == "CMP":
        return [(op, _reg(args[0]), _reg(args[1]))]
    return [(op, target(args[0]))]


def _assemble(source: str):
    """汇编：返回 (code, data, labels, counts)。code 是 地址->指令，data 是 地址->数据字。"""
    label_addr = {}
    items = []
    address = 0

    for raw in source.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        while True:                      # 一行可以挂多个标签
            match = _LABEL_RE.match(line)
            if not match:
                break
            if match.group(1) in label_addr:
                raise ValueError(f"标签重名：{match.group(1)}")
            label_addr[match.group(1)] = address
            line = match.group(2).strip()
            if not line:
                break
        if not line:
            continue
        items.append((address, line))
        address += _size_of(line)
        if address > MEM_SIZE:
            raise ValueError(f"程序太大，超过了 {MEM_SIZE} 个字的内存")

    code, data = {}, {}
    instructions = 0
    for addr, text in items:
        for offset, cell in enumerate(_encode(text, label_addr)):
            if cell[0] == "DW":
                data[addr + offset] = cell[1]
            else:
                code[addr + offset] = cell
                instructions += 1

    return code, data, label_addr, instructions


def _disasm(cell, names=None) -> str:
    """把一条指令还原成汇编文本，给 trace 用。names 是 地址->标签名，跳转时显示标签。"""
    def place(addr):
        return names[addr][0] if names and addr in names else str(addr)

    op = cell[0]
    if op in ("HALT", "NOP"):
        return op
    if op in ("LI", "LOAD"):
        return f"{op} R{cell[1]}, {cell[2]}"
    if op == "STORE":
        return f"STORE R{cell[1]}, {cell[2]}"
    if op == "MOV":
        return f"MOV R{cell[1]}, R{cell[2]}"
    if op in _RRR:
        return f"{op} R{cell[1]}, R{cell[2]}, R{cell[3]}"
    if op in _RRI:
        return f"{op} R{cell[1]}, R{cell[2]}, {cell[3]}"
    if op == "NOT":
        return f"NOT R{cell[1]}, R{cell[2]}"
    if op in _RSI:
        return f"{op} R{cell[1]}, R{cell[2]}, {cell[3]}"
    if op in _R:
        return f"{op} R{cell[1]}"
    if op == "CMP":
        return f"CMP R{cell[1]}, R{cell[2]}"
    return f"{op} {place(cell[1])}"


def _step(cell, regs, flags, memory, pc):
    """执行一条指令，返回下一条指令的地址。"""
    op = cell[0]
    nxt = pc + 1

    if op == "NOP":
        return nxt
    if op == "LI":
        regs[cell[1]] = cell[2]
        return nxt
    if op == "MOV":
        regs[cell[1]] = regs[cell[2]]
        return nxt
    if op == "LOAD":
        if not 0 <= cell[2] < MEM_SIZE:
            # 不回绕：悄悄绕回低地址会改掉/读到别处的数据，教学时几乎看不出来
            raise ValueError(f"LOAD 的地址 {cell[2]} 超出内存范围（0~{MEM_SIZE - 1}）")
        regs[cell[1]] = memory[cell[2]]
        return nxt
    if op == "STORE":
        if not 0 <= cell[2] < MEM_SIZE:
            raise ValueError(f"STORE 的地址 {cell[2]} 超出内存范围（0~{MEM_SIZE - 1}）")
        memory[cell[2]] = regs[cell[1]]
        return nxt
    if op in _RRR:
        left, right = regs[cell[2]], regs[cell[3]]
        if op == "ADD":
            total = left + right
            result, flags["C"] = total & MASK, 1 if total > MASK else 0
        elif op == "SUB":
            diff = left - right
            result, flags["C"] = diff & MASK, 1 if diff < 0 else 0
        elif op == "MUL":
            product = left * right
            result, flags["C"] = product & MASK, 1 if product > MASK else 0
        elif op == "AND":
            result, flags["C"] = left & right, 0
        elif op == "OR":
            result, flags["C"] = left | right, 0
        else:
            result, flags["C"] = left ^ right, 0
        # V 是有符号溢出：加法看两操作数同号而结果变号，减法看两操作数异号而结果变号；
        # MUL 跟 C 一样，只看结果放不放得下 16 位（x86 的 MUL 也只定义 CF/OF）
        if op == "ADD":
            flags["V"] = 1 if (left ^ result) & (right ^ result) & 0x8000 else 0
        elif op == "SUB":
            flags["V"] = 1 if (left ^ right) & (left ^ result) & 0x8000 else 0
        elif op == "MUL":
            flags["V"] = flags["C"]
        else:
            flags["V"] = 0
        regs[cell[1]] = result
        flags["Z"] = 1 if result == 0 else 0
        flags["N"] = 1 if result & 0x8000 else 0
        return nxt
    if op in _RRI:
        left, imm = regs[cell[2]], cell[3]
        if op == "ADDI":
            total = left + imm
            result, flags["C"] = total & MASK, 1 if total > MASK else 0
        else:
            diff = left - imm
            result, flags["C"] = diff & MASK, 1 if diff < 0 else 0
        if op == "ADDI":
            flags["V"] = 1 if (left ^ result) & (imm ^ result) & 0x8000 else 0
        else:
            flags["V"] = 1 if (left ^ imm) & (left ^ result) & 0x8000 else 0
        regs[cell[1]] = result
        flags["Z"] = 1 if result == 0 else 0
        flags["N"] = 1 if result & 0x8000 else 0
        return nxt
    if op == "NOT":
        result = (~regs[cell[2]]) & MASK
        regs[cell[1]] = result
        flags["C"] = 0
        flags["V"] = 0
        flags["Z"] = 1 if result == 0 else 0
        flags["N"] = 1 if result & 0x8000 else 0
        return nxt
    if op in _RSI:
        value, count = regs[cell[2]], cell[3]
        count = min(count, 16)     # 只有 16 位，移超过字长的位跟移光一样，把 C 的判定位压回范围内
        if op == "SHL":
            shifted = value << count
            result = shifted & MASK
            # C 只记「最后一次」移出的那一位：左移时最后移出的是 bit(16-count)。
            # 原来判的是「有没有任何一位被移出去」（shifted > MASK），count≥2 时会多报 C=1。
            # count=0 一位都没移，标志位不动；count 超过字长时全部移光，最后移出的是最低位。
            if count:
                flags["C"] = 1 if (value >> (16 - count)) & 1 else 0
        else:
            flags["C"] = 1 if count and (value >> (count - 1)) & 1 else 0
            result = (value >> count) & MASK
        regs[cell[1]] = result
        flags["V"] = 0
        flags["Z"] = 1 if result == 0 else 0
        flags["N"] = 1 if result & 0x8000 else 0
        return nxt
    if op in _R:
        delta = 1 if op == "INC" else -1
        total = regs[cell[1]] + delta
        result = total & MASK
        regs[cell[1]] = result
        # INC/DEC 不碰 C（跟 x86 一致：进位链之外的加减法不报告进位）；Z/N/V 照旧
        # INC 从 0x7FFF 翻到 0x8000、DEC 从 0x8000 翻到 0x7FFF 就是有符号溢出
        flags["V"] = 1 if result == (0x8000 if op == "INC" else 0x7FFF) else 0
        flags["Z"] = 1 if result == 0 else 0
        flags["N"] = 1 if result & 0x8000 else 0
        return nxt
    if op == "CMP":
        left, right = regs[cell[1]], regs[cell[2]]
        diff = (left - right) & MASK
        flags["Z"] = 1 if diff == 0 else 0
        flags["N"] = 1 if diff & 0x8000 else 0
        flags["C"] = 1 if left < right else 0
        flags["V"] = 1 if (left ^ right) & (left ^ diff) & 0x8000 else 0
        return nxt

    # 跳转
    if op == "JMP":
        return cell[1]
    if op == "JZ":
        return cell[1] if flags["Z"] else nxt
    if op == "JNZ":
        return cell[1] if not flags["Z"] else nxt
    if op == "JC":
        return cell[1] if flags["C"] else nxt
    if op == "JNC":
        return cell[1] if not flags["C"] else nxt
    if op == "JGT":
        return cell[1] if (not flags["Z"] and flags["N"] == flags["V"]) else nxt
    if op == "JLT":
        return cell[1] if flags["N"] != flags["V"] else nxt
    raise ValueError(f"模拟器不认识这条指令：{op}")


def _reg_line(regs) -> str:
    """寄存器一行两个，十进制 + 十六进制都给出。"""
    cells = [f"R{i}={regs[i]} (0x{regs[i]:04X})" for i in range(REG_COUNT)]
    rows = ["  " + "    ".join(cells[i:i + 2]) for i in range(0, REG_COUNT, 2)]
    return "\n".join(rows)


def asm_sim(source: str = "", path: str = "", max_steps: int = DEFAULT_MAX_STEPS,
            trace: bool = False) -> str:
    """在小型 16 位 CPU 上跑汇编，回报寄存器和内存。"""
    if path:
        full = safe_path(path)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as handle:
                source = handle.read()
        except OSError as exc:
            return f"错误：读不了 {path}（{exc}）"
    if not isinstance(source, str):
        # 传进来非字符串时本来会 AttributeError 冒泡出去，这里给个能看懂的说法
        return f"错误：source 要是字符串，现在拿到的是 {type(source).__name__}；也可以改用 path 指一个 .asm 文件"
    if not source.strip():
        return "错误：没给汇编源码，往 source 里传（或者用 path 指一个 .asm 文件）"

    try:
        max_steps = max(1, min(int(max_steps), 100000))
    except (TypeError, ValueError):
        max_steps = DEFAULT_MAX_STEPS

    try:
        code, data, labels, instructions = _assemble(source)
    except ValueError as exc:
        return f"汇编失败：{exc}"

    if not code:
        return "汇编失败：里面没有可执行的指令"

    memory = [0] * MEM_SIZE
    for addr, value in data.items():
        memory[addr] = value
    initial = list(memory)

    label_of = {}
    for name, addr in labels.items():
        label_of.setdefault(addr, []).append(name)

    regs = [0] * REG_COUNT
    flags = {"Z": 0, "N": 0, "C": 0, "V": 0}
    pc = 0
    steps = 0
    trace_lines = []
    stopped = None

    while True:
        if steps >= max_steps:
            stopped = (f"达到步数上限 {max_steps} 就停了（要么是死循环，检查一下跳转条件；"
                       f"要么程序本来就长，把 max_steps 调大再跑）")
            break
        if not 0 <= pc < MEM_SIZE:
            stopped = f"PC 跑飞了（0x{pc & MASK:04X}），程序没写 HALT 结尾吧"
            break
        cell = code.get(pc)
        if cell is None:
            stopped = (f"PC=0x{pc:02X} 这里不是指令"
                       + ("（是数据区，数据要放在 HALT 后面）" if pc in data else "（程序跑过头了）"))
            break

        if cell[0] == "HALT":
            stopped = "执行到 HALT，正常停机"
            steps += 1
            if trace and len(trace_lines) < MAX_TRACE:
                trace_lines.append(f"  {steps:>4}  {pc:02X}  HALT")
            break

        before = list(regs) if trace else None
        here = pc
        try:
            pc = _step(cell, regs, flags, memory, pc)
        except ValueError as exc:
            # 地址越界之类的运行时错误：停下来把原因报出去，别让异常冒泡出工具
            stopped = f"运行出错：{exc}"
            break
        steps += 1

        if trace and len(trace_lines) < MAX_TRACE:
            changed = [f"R{i}={regs[i]}" for i in range(REG_COUNT) if regs[i] != before[i]]
            trace_lines.append(
                f"  {steps:>4}  {here:02X}  {_disasm(cell, label_of):<22}"
                + ("  ->  " + ", ".join(changed) if changed else "")
            )

    lines = [f"程序：{instructions} 条指令，{len(data)} 个字数据",
             f"停机：{stopped}（共执行 {steps} 步）",
             f"标志位：Z={flags['Z']} N={flags['N']} C={flags['C']} V={flags['V']}",
             "寄存器：", _reg_line(regs)]

    touched = [addr for addr in range(MEM_SIZE)
               if memory[addr] != initial[addr] or memory[addr] != 0]
    if touched:
        lines.append(f"内存（非零 / 有变化，共 {len(touched)} 个字）：")
        for addr in touched[:MAX_MEM_DUMP]:
            tag = f"  ← {', '.join(label_of[addr])}" if addr in label_of else ""
            lines.append(f"  0x{addr:02X} = {memory[addr]}{tag}")
        if len(touched) > MAX_MEM_DUMP:
            lines.append(f"  …… 还有 {len(touched) - MAX_MEM_DUMP} 个没显示")
    else:
        lines.append("内存：没有非零数据，也没有被写过")

    if trace:
        if trace_lines:
            lines.append(f"逐步执行（最多显示前 {MAX_TRACE} 步）：")
            lines.extend(trace_lines)
            if steps > len(trace_lines):
                lines.append(f"  …… 后面还有 {steps - len(trace_lines)} 步没显示")
        else:
            lines.append("逐步执行：（一步没跑）")

    return "\n".join(lines)
