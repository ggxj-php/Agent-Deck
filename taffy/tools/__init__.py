"""工具注册表：汇总各模块的 SPECS，对上给模型一份 schema，对下按名字调函数。

新增工具只需要写一个模块，在里面放 SPECS 列表和同名函数，然后加进 _MODULES。
"""
import json

from . import (asm86, asm_sim, basics, calc, files, forensics, kb, logic, mcu,
               numconv, render, runner, search, sticker, text)

_MODULES = (basics, kb, search, files, forensics, runner, sticker, render,
            asm_sim, asm86, mcu, logic, numconv, calc, text)

TOOLS = []
TOOL_MAP = {}
for _module in _MODULES:
    for _spec in _module.SPECS:
        _name = _spec["function"]["name"]
        TOOLS.append(_spec)
        # 约定：工具名和函数名一致，直接按名字取
        TOOL_MAP[_name] = getattr(_module, _name)


def execute(name: str, raw_arguments: str):
    """执行一次工具调用，返回 (参数, 结果文本)。异常都转成结果文本，不往外抛。"""
    try:
        args = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        # 别把解析失败的参数悄悄换成 {} 接着跑：那样会拿默认值算出「看似成功」的结果，
        # 模型还不知道自己发错了。直接把错误交回给模型，让它重发一次。
        return {}, (f"参数不是合法的 JSON，没法调用 {name}：{exc}。"
                    f"请把参数写成 JSON 对象再调用一次喵。")
    if not isinstance(args, dict):
        return {}, (f"参数要是一个 JSON 对象，收到的是 {type(args).__name__}，"
                    f"没法调用 {name}。")
    try:
        if name not in TOOL_MAP:
            return args, f"错误：没有名为 {name} 的工具"
        return args, str(TOOL_MAP[name](**args))
    except Exception as e:
        return args, f"工具 {name} 执行失败：{e}"
