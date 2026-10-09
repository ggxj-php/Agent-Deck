"""数字逻辑仿真：描述一个门级网表，算输出或者直接列真值表。

给数字电路 / 计算机组成原理用。支持与门、或门、非门、与非、或非、异或、同或，
以及 D 触发器组成的时序电路（按周期跑，看时钟沿上怎么变）。
纯标准库实现，不联网、不起子进程，跨平台。

网表写法：一行一个门，`名字 = 门型 输入1 输入2 ...`，输入端口写成 `名字 = INPUT`。
`#` 或 `;` 后面是注释。例：

    A = INPUT
    B = INPUT
    n1 = AND A B
    Y = XOR n1 B
"""
import itertools
import re

MAX_TABLE_INPUTS = 6      # 真值表最多几个输入（6 个就是 64 行）
MAX_CYCLES = 64

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "logic_sim",
            "description": (
                "数字逻辑仿真：给一个门级网表，算输出，或者直接列真值表。讲数字电路、"
                "组合逻辑、时序逻辑、触发器的时候用。"
                "网表写法：一行一个门，「名字 = 门型 输入1 输入2 ...」；输入端口写「名字 = INPUT」；"
                "'#' 或 ';' 后面是注释。门型有 AND / OR / NOT / NAND / NOR / XOR / XNOR / BUF / DFF。"
                "AND/OR/NAND/NOR/XOR 可以给多个输入（两个以上都行），NOT/BUF/DFF 只收一个输入。"
                "DFF 就是 D 触发器，输出在时钟上升沿才更新。\n"
                "不给 inputs 时：把所有输入端口的所有组合跑一遍列成真值表（纯组合电路，最多 6 个输入）。"
                "给了 inputs 时：按给定值算一遍所有节点的值；如果网表里有 DFF，就按时钟跑 cycles 个周期"
                "（inputs 里可以给 '0101' 这样的序列来驱动，短的会保持最后一拍）。"
                "有时钟端口（名字叫 CLK）时在上升沿采样，没有就每个周期都采样。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "netlist": {
                        "type": "string",
                        "description": "网表，一行一个门，用 \\n 换行",
                    },
                    "inputs": {
                        "type": "object",
                        "description": "输入端口的值，例如 {\"A\": 1, \"B\": \"01\"}。不给就列真值表",
                    },
                    "cycles": {
                        "type": "integer",
                        "description": "有 DFF 时跑多少个时钟周期，默认 8，最多 64",
                    },
                },
                "required": ["netlist"],
            },
        },
    },
]

_INPUTS = {"INPUT", "IN", "PORT"}
_ONE_ARG = {"NOT", "BUF", "DFF"}
_MULTI_ARG = {"AND", "OR", "NAND", "NOR", "XOR", "XNOR"}
_GATES = _ONE_ARG | _MULTI_ARG
_CLOCK_NAMES = ("clk", "clock", "clk_i")


def _parse(netlist: str):
    """解析网表，返回 (inputs, nodes, dffs)，都按出现顺序。"""
    inputs, nodes, dffs = [], {}, {}

    for number, raw in enumerate((netlist or "").splitlines(), 1):
        line = raw.split("#")[0].split(";")[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"第 {number} 行看不懂：{raw.strip()!r}，要写成「名字 = 门型 输入...」")
        name, expression = line.split("=", 1)
        name = name.strip()
        parts = [part for part in re.split(r"[\s,]+", expression.strip()) if part]
        if not name or not parts:
            raise ValueError(f"第 {number} 行不完整：{raw.strip()!r}")
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise ValueError(f"第 {number} 行的名字不合法：{name}")
        if name in nodes or name in dffs or name in inputs:
            raise ValueError(f"第 {number} 行：{name} 定义了两次")

        gate = parts[0].upper()
        args = parts[1:]
        if gate in _INPUTS:
            if args:
                raise ValueError(f"第 {number} 行：输入端口后面不该跟东西")
            inputs.append(name)
            continue
        if gate not in _GATES:
            raise ValueError(f"第 {number} 行：不认识的门型 {parts[0]}（有 "
                             + "、".join(sorted(_GATES)) + "）")
        if gate in _ONE_ARG and len(args) != 1:
            raise ValueError(f"第 {number} 行：{gate} 只能有一个输入")
        if gate in _MULTI_ARG and len(args) < 2:
            raise ValueError(f"第 {number} 行：{gate} 至少要两个输入")
        if gate == "DFF":
            dffs[name] = args[0]
        else:
            nodes[name] = (gate, args)

    if not inputs:
        raise ValueError("网表里一个输入端口都没有（至少要有一行「名字 = INPUT」）")
    for name, (_, args) in nodes.items():
        for arg in args:
            if arg not in nodes and arg not in dffs and arg not in inputs:
                raise ValueError(f"{name} 用到了没定义的东西：{arg}")
    for name, arg in dffs.items():
        if arg not in nodes and arg not in dffs and arg not in inputs:
            raise ValueError(f"{name} 用到了没定义的东西：{arg}")
    return inputs, nodes, dffs


def _apply(gate: str, operands: list) -> int:
    if gate == "BUF":
        return operands[0]
    if gate == "NOT":
        return 0 if operands[0] else 1
    if gate == "AND":
        return 1 if all(operands) else 0
    if gate == "NAND":
        return 0 if all(operands) else 1
    if gate == "OR":
        return 1 if any(operands) else 0
    if gate == "NOR":
        return 0 if any(operands) else 1
    if gate == "XOR":
        return 1 if sum(operands) % 2 else 0
    return 0 if sum(operands) % 2 else 1     # XNOR


def _eval(name, nodes, env, cache, visiting):
    """递归求一个节点的值，env 里是已经定下来的输入和触发器输出。"""
    if name in cache:
        return cache[name]
    if name in env:
        return env[name]
    if name not in nodes:
        raise ValueError(f"用到了没定义的东西：{name}")
    if name in visiting:
        raise ValueError(f"{name} 这里绕成了一个组合环：没有触发器的话电路会一直震荡，算不出来")
    visiting.add(name)
    gate, args = nodes[name]
    operands = [_eval(arg, nodes, env, cache, visiting) for arg in args]
    visiting.discard(name)
    result = _apply(gate, operands)
    cache[name] = result
    return result


def _values_for(nodes, env) -> dict:
    cache = {}
    return {name: _eval(name, nodes, env, cache, set()) for name in nodes}


def _sequence(value, cycles: int, name: str) -> list:
    """把一个输入值铺成 cycles 长度的 0/1 序列，短的保持最后一拍。"""
    if isinstance(value, bool):
        return [int(value)] * cycles
    if isinstance(value, int):
        if value not in (0, 1):
            raise ValueError(f"输入 {name} 的值只能是 0 或 1")
        return [value] * cycles
    if isinstance(value, list):
        digits = []
        for item in value:
            if int(item) not in (0, 1):
                raise ValueError(f"输入 {name} 的序列里只能有 0 和 1")
            digits.append(int(item))
        if not digits:
            raise ValueError(f"输入 {name} 的序列是空的")
        return [digits[min(i, len(digits) - 1)] for i in range(cycles)]
    if isinstance(value, str):
        digits = []
        for char in value.strip():
            # 跟列表风格保持一致：非 0/1 的字符要报错，别悄悄丢掉（"12" 当成 1 会看不出错）
            if char not in "01":
                raise ValueError(f"输入 {name} 的序列里只能有 0 和 1，这里出现了 {char!r}")
            digits.append(int(char))
        if not digits:
            raise ValueError(f"输入 {name} 给的是 {value!r}，里面没有 0/1")
        return [digits[min(i, len(digits) - 1)] for i in range(cycles)]
    raise ValueError(f"输入 {name} 的值看不懂：{value!r}")


def _truth_table(inputs, nodes, dffs) -> str:
    if dffs:
        return (f"这个网表里有 D 触发器（{', '.join(dffs)}），时序电路没有「真值表」这一说，"
                "得给 inputs 上时钟序列跑周期（inputs 里放 CLK 的 0101 序列）")
    if len(inputs) > MAX_TABLE_INPUTS:
        return (f"输入端口有 {len(inputs)} 个，真值表会有 {2 ** len(inputs)} 行，太多了。"
                f"最多支持 {MAX_TABLE_INPUTS} 个输入；输入少一点，或者直接给一组 inputs 算单次结果")

    order = list(nodes)
    head = " ".join(inputs) + " | " + " ".join(order)
    lines = [f"真值表（{len(inputs)} 个输入，{2 ** len(inputs)} 行）",
             head,
             "-" * len(head)]
    for combo in itertools.product((0, 1), repeat=len(inputs)):
        env = dict(zip(inputs, combo))
        try:
            values = _values_for(nodes, env)
        except ValueError as exc:
            return f"算不出来：{exc}"
        lines.append(" ".join(str(bit) for bit in combo) + " | "
                     + " ".join(str(values[name]) for name in order))
    if len(inputs) > 4:
        lines.append(f"（{2 ** len(inputs)} 行，觉得太长就少给几个输入，或者直接给一组 inputs）")
    return "\n".join(lines)


def _simulate(inputs, nodes, dffs, given: dict, cycles: int) -> str:
    unknown = [name for name in given if name not in inputs]
    if unknown:
        return (f"inputs 里的 {', '.join(unknown)} 不是网表里的输入端口。"
                f"网表的输入端口是：{', '.join(inputs)}")

    clock = next((name for name in inputs if name.lower() in _CLOCK_NAMES), None)
    notes = []
    if dffs and not clock:
        notes.append("网表里没有 CLK 端口，这里每个周期都采一次样")

    drives = {}
    defaulted = []
    for name in inputs:
        if name in given:
            try:
                drives[name] = _sequence(given[name], cycles, name)
            except ValueError as exc:
                return f"输入有问题：{exc}"
        elif dffs:
            drives[name] = [0] * cycles
            defaulted.append(name)
        else:
            return f"输入端口 {name} 没给值。要么都补上，要么干脆别给 inputs（那就列真值表）"
    if defaulted:
        notes.append(f"{'、'.join(defaulted)} 没给值，按一直保持 0 处理")

    state = {name: 0 for name in dffs}
    rows = []
    previous_clock = 0        # 假定时钟初始为低电平，第一拍 CLK=1 才算上升沿
    for tick in range(cycles):
        pins = {name: drives[name][tick] for name in inputs}
        env = dict(pins)
        env.update(state)
        try:
            values = _values_for(nodes, env)
        except ValueError as exc:
            return f"算不出来：{exc}"
        clock_now = env.get(clock) if clock else 1
        edge = (clock is None) or (clock_now == 1 and previous_clock == 0)
        if edge:
            # 先把所有 D 端按「沿之前的状态」采样好，再一起更新，
            # 否则先更新的那个会影响到后面那个（移位寄存器就错了）
            captured = {name: _eval(arg, nodes, env, {}, set()) for name, arg in dffs.items()}
            state.update(captured)
            after = dict(pins)
            after.update(state)
            values = _values_for(nodes, after)
        rows.append((pins, dict(state), values))
        previous_clock = clock_now

    columns = list(inputs) + [f"{name}*" for name in dffs] + list(nodes)
    width = max([len(col) for col in columns] + [4])
    headline = f"时序仿真：{cycles} 个周期"
    if notes:
        headline += "（" + "；".join(notes) + "）"
    lines = [headline,
             "  ".join(col.rjust(width) for col in columns),
             "-" * (len(columns) * (width + 2) - 2)]
    for pins, snapshot, values in rows:
        cells = [str(pins[name]) for name in inputs]
        cells += [str(snapshot[name]) for name in dffs]
        cells += [str(values[name]) for name in nodes]
        lines.append("  ".join(cell.rjust(width) for cell in cells))
    lines.append("（带 * 的是 D 触发器输出，在时钟上升沿之后取值；"
                 "前面几列是各端口这一拍的输入）")
    return "\n".join(lines)


def _single(inputs, nodes, dffs, given: dict) -> str:
    env = {}
    for name in inputs:
        if name not in given:
            return f"输入端口 {name} 没给值。补齐所有端口，或者干脆不给 inputs（那就列真值表）"
        try:
            env[name] = _sequence(given[name], 1, name)[0]
        except ValueError as exc:
            return f"输入有问题：{exc}"
    env.update({name: 0 for name in dffs})
    try:
        values = _values_for(nodes, env)
    except ValueError as exc:
        return f"算不出来：{exc}"

    lines = ["输入：" + "  ".join(f"{name}={env[name]}" for name in inputs)]
    for name in nodes:
        gate = nodes[name][0]
        lines.append(f"  {name} = {gate}({', '.join(nodes[name][1])}) → {values[name]}")
    return "\n".join(lines)


def logic_sim(netlist: str = "", inputs: dict = None, cycles: int = 8) -> str:
    """门级网表仿真：给输入算结果，或者列真值表。"""
    if not isinstance(netlist, str):
        # 非字符串本来会在 splitlines 上炸出 AttributeError，这里给个能看懂的说法
        return f"网表有问题：netlist 要是字符串，现在拿到的是 {type(netlist).__name__}"
    try:
        ports, nodes, dffs = _parse(netlist)
    except ValueError as exc:
        return f"网表有问题：{exc}"

    given = inputs or {}
    if not isinstance(given, dict):
        return "inputs 要是个对象，例如 {\"A\": 1, \"B\": 0}"

    try:
        cycles = max(1, min(int(cycles), MAX_CYCLES))
    except (TypeError, ValueError):
        cycles = 8

    try:
        if dffs:
            if not given:
                return _truth_table(ports, nodes, dffs)
            return _simulate(ports, nodes, dffs, given, cycles)
        if not given:
            return _truth_table(ports, nodes, dffs)
        return _single(ports, nodes, dffs, given)
    except RecursionError:
        # 求值是递归的，链太深（几千级）时 Python 会抛 RecursionError；
        # 这不是电路本身的错，转成看得懂的说法，别让异常冒泡出工具
        return ("网表有问题：电路太深了，递归求值的时候超过了 Python 的递归层数"
                "（常见于上千级的门链，或者定义顺序是「用到在前、定义在后」的深链）。"
                "建议拆成几段中间节点，或者把定义顺序改成从输入往输出写")
