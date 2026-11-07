# 内置工具详解

工具在后台「模型与接口 → 工具开关」里**按组开关**：关掉的组，模型连工具定义都看不到（省 token、少幻觉），硬调也会被拦。

工具本身都是项目自己的代码，不需要额外起服务；只有 `run_code` 要系统里有编译器、`read_pdf` 要 `pypdf` + `pillow`、`draw_diagram` 可选装 graphviz（见 README 的依赖表）。

## 分组一览

| 组 | 工具 | 干什么 |
|---|---|---|
| 基础 | `get_time` `list_files` `read_file` `write_file` `delete_file` `send_sticker` | 时间、workspace 读写删、发表情包 |
| 联网 | `web_search` `open_url` | 搜索、打开网页读正文 |
| 知识库 | `search_knowledge` | 检索 `knowledge/` 里的资料 |
| 文档与图片 | `read_pdf` `read_doc` `read_img` | 抽 PDF / DOCX 正文与配图、看图 |
| 跑代码 / 画图 | `run_code` `render_html` `draw_diagram` `make_download_link` | 编译运行、渲染卡片、结构化画图、做下载链接 |
| 数字取证 | `hash_file` `identify_file` `extract_strings` `carve_files` `file_metadata` | 哈希、类型识别、字符串、雕复、时间线 |
| 嵌入式 / 单片机 | `asm_sim` `asm86` `mcu_calc` `logic_sim` | CPU 仿真、外设计算、数字逻辑 |
| 计算 / 进制 | `sci_calc` `number_convert` | 高精度计算、进制与位运算 |
| 文本处理 | `regex_test` `diff_text` `json_tool` `count_text` | 正则、差异、JSON、字数统计 |
| 兜底检索 | `ultra_search` `ultra_fetch` | 付费兜底：常规搜索跑题 / 抓不到时用 |
| 浏览器 | `browser_open` 等 12 个 | 真实浏览器操作：登录、点击、填表、抓图 / 视频 |
| 技能包 | `list_skills` `load_skill` `list_skill_files` `read_skill_file` | 加载服务器上装好的 Agent Skills |

---

## 画图与渲染

**`draw_diagram`**——结构化画图。模型只给几十 token 的节点数据，服务端生成 SVG：树 / 数据结构图（二叉树、红黑树、堆）、流程图、关系图、数据流图、架构图、时序图、图表、时间线、ER 图。

树 / 流程 / 关系 / DFD / ER / 架构这几类交给 **Graphviz（dot）** 自动布局，其余内置渲染；没装 graphviz 会自动退回内置布局，功能不丢。比让模型手写整段 HTML 快一个数量级。

**`render_html`**——写一段 HTML 直接渲染成聊天气泡里的卡片（SVG / canvas / CSS 动画都行，四层 XSS 防护），用在创意海报、界面草图、动画这类自由排版。

**`make_download_link`**——把 workspace 里的文件做成临时下载链接：20 分钟有效、只有生成者本人登录能打开（防枚举 / 防越界 / 防执行）。用户说「下载 / 保存 / 导出」时用它。

---

## 工程计算与教学仿真

这六个工具（`asm_sim` / `asm86` / `mcu_calc` / `logic_sim` / `number_convert` / `sci_calc`）都是纯标准库、纯算术，不联网、不起子进程，跨平台。

**`asm_sim`**——自带的 16 位 CPU。寄存器 `R0`~`R7`，标志位 `Z/N/C`，内存 256 个字（指令和数据共用），数值按 16 位回绕。指令集：

```
LI Rd,imm      MOV Rd,Rs       LOAD Rd,addr    STORE Rs,addr
ADD/SUB/MUL/AND/OR/XOR Rd,Ra,Rb
ADDI/SUBI Rd,Ra,imm            NOT Rd,Ra
SHL/SHR Rd,Ra,imm              INC/DEC Rd      CMP Ra,Rb
JMP/JZ/JNZ/JC/JNC/JGT/JLT 标签  HALT            NOP
DW 1,2,3       DS 4
```

一行一条，`;` / `#` 后面是注释，`loop:` 定义标签，数支持十进制和 `0x` / `0b` / `0o`。数据写在 `HALT` 后面，免得被当指令执行。`trace=true` 会给逐步执行过程（每步的寄存器变化）。

**`asm86`**——8086 / 8088 实模式汇编仿真器，认的是真·x86 汇编。喂一段 MASM 风格源码，两遍汇编后直接跑，返回寄存器（`AX`~`DI`、`CS`/`DS`/`SS`/`ES`/`IP`/`SP`）、九个标志位（`CF`/`PF`/`AF`/`ZF`/`SF`/`OF`/`IF`/`DF`/`TF`）、有内容的和改过的内存、程序输出，以及**估算的总线周期**（估算值，不是精确机器码长度）。

- 认 MASM 常见写法：`SEGMENT`/`ENDS`、`ASSUME`、`PROC`/`ENDP`、`END 入口`、`DB`/`DW`/`DD`、`DUP`、`EQU`、`ORG`，段名能用 `@DATA` 这么引；也支持完全不带段的裸程序，`.MODEL` / `.STACK` / `.DATA` / `.CODE` 会被忽略。
- 数字字面量认 `123` / `0FFH` / `1010B` / `0x1F`；指令覆盖 `MOV`、算术、逻辑、移位、乘除、`CMP`/`TEST` 和各种条件跳转、`LOOP`、`CALL`/`RET`、`PUSH`/`POP`、`CBW`/`CWD`/`XLAT`，以及 `MOVSB`/`CMPSB`/`SCASB` 这类字符串指令（带 `REP`/`REPE`/`REPNE`）。
- 寻址支持 `[BX+SI+4]`、`NUMS[BX+SI]`、`BYTE PTR`/`WORD PTR`、段超越前缀 `DS:`/`ES:`。
- 中断内置实现：`INT 21H`（`09H` 打印 `$` 结尾串、`02H` 打字符、`01H` 读键、`0AH` 读行、`4CH` 退出）和 `INT 10H` 的几个功能；没实现的中断会明确说没实现，不会瞎猜。
- `cpu` 选 `"8088"` 按 8 位外部数据总线折算取指周期（`8086` 是 16 位），指令集两者一样。`stdin` 可以喂给 `01H` / `0AH`，`trace=true` 出逐步执行过程。

```asm
; 8086 Hello World（MASM 全框架写法）
.MODEL SMALL
.STACK 100H
DATA SEGMENT
    MSG DB 'Hello, Deck!$'
DATA ENDS
CODE SEGMENT
    ASSUME CS:CODE, DS:@DATA
START:
    MOV AX, @DATA
    MOV DS, AX
    MOV AH, 09H
    LEA DX, MSG
    INT 21H
    MOV AX, 4C00H
    INT 21H
CODE ENDS
END START
```

**`mcu_calc`**——单片机外设计算，用 `op` 选算哪一样：串口波特率分频、定时器重载值、ADC 换算、电阻分压、PWM 占空比、I2C 地址、CRC、RC 时间常数等，附带校验（比如波特率实际误差百分比）。

**`logic_sim`**——门级网表，一行一个门：

```
A = INPUT
B = INPUT
n1 = AND A B
Y = XOR n1 B
```

门型 `AND` / `OR` / `NOT` / `NAND` / `NOR` / `XOR` / `XNOR` / `BUF` / `DFF`。不给 `inputs` 就列真值表（纯组合电路，最多 6 个输入）；给了 `inputs` 就按值算一遍。网表里有 `DFF` 就变成时序仿真，按时钟跑 `cycles` 个周期——`inputs` 可以给 `"0101"` 这样的序列来驱动，时钟端口叫 `CLK` 时在上升沿采样。

**`number_convert`**——进制互转、补码、IEEE754 位型、位运算置位 / 清位 / 测位。

**`sci_calc`**——超高精度科学计算器。丢一个表达式进去，一次算到指定有效数字（默认 50 位，最多 1000 位），不用再为了让模型算个数而去 `write_file` + `run_code` 绕一圈。`decimal` 底子 + 自己实现的 π（Machin 公式）和三角函数级数，整数的加减乘幂走 Python 大整数，**一位不差**：

```
(1+2)**100 / sqrt(3)          2**100            factorial(50)
1/7                           pi                1e6*sqrt(2)
sin(pi/6)  cos(1.234)  tan(pi/4)  atan2(1,1)
ln(2)  log(1000,10)  lg(1000)  log2(1024)  exp(1)
comb(52,5)  perm(10,3)  gcd(1071,462)  isqrt(2**100)
```

`0.1 + 0.2` 在这里就是 `0.3`（数字字面量按原文读，不走二进制浮点）；一次可以给多个式子，换行或分号隔开。结果太大（超过 1500 位）会直接拒绝，免得又慢又占地方。

展开看个例子：

```
你: 帮我算一下 72MHz 主频、115200 波特率的串口分频对不对，再跑个 1+2+...+10 的汇编，顺便 π 算到 50 位
[工具] mcu_calc({'op': 'uart', 'f_cpu': 72000000, 'baud': 115200})
     -> USARTDIV=39.0625，BRR=0x0271，实际 115384.6 bps，误差 +0.160%
[工具] asm_sim({'source': 'LI R1,0 ...', 'trace': True})
     -> R1=45，执行到 HALT，共 41 步
[工具] sci_calc({'expression': 'pi', 'precision': 50})
     -> 3.1415926535897932384626433832795028841971693993751
Agent: 波特率误差 +0.16%，能放心用～汇编那边跑出来是 45，对的
```

---

## 文本与数据

**`regex_test`**——正则调试器。给正则和文本，列出每一处匹配的位置（偏移）、内容和各个分组（命名分组会标出组名）；再给 `replace` 就顺带做替换，支持 `\1` 和 `\g<name>` 反向引用。`flags` 可以传 `i` / `m` / `s` / `x` 的组合。

`re` 模块没法给匹配设超时，所以它先扫一遍正则，把 `(a+)+`、`(.*)*` 这种**嵌套量词**（灾难性回溯的典型形状）拦下来报错，免得一个写错的正则把整个服务卡死。正常的 `(a|b)+`、`(\d{4}-)+`、`(a+)?` 不会误伤。

**`diff_text`**——两段文本 / 代码的 unified diff，带「X 行 → Y 行；新增 N 行，删除 M 行」的统计，`context` 控制差异上下显示几行（默认 3）。

**`json_tool`**——`op=format` 缩进美化（默认，中文不转义成 `\uXXXX`）、`op=minify` 压成一行、`op=get` 按路径取值、`op=validate` 只校验。语法错会指到第几行第几列并画出那一行：

```
JSON 不合法：Expecting ':' delimiter（第 3 行第 7 列）
>   3 |   "b" 2
     |       ^
```

取值路径支持 `data.items[0].name`、`items[-1]`、`["名字"]` 这几种写法，走错了会分清是「键不存在（附上现有键名）」「下标越界（附上长度）」还是「这一层不是对象 / 数组」。

**`count_text`**——统计字数：总字符、汉字、英文字母、数字、标点、空格、段落、句数，以及按中文习惯算的「字数」（汉字 + 英文单词 + 数字串）。用户要求「写够多少字」时用它核对。

---

## 浏览器

`browser_*` 这组是**真实浏览器**（能跑 JS、能登录），不是抓 HTML：

| 工具 | 干什么 |
|---|---|
| `browser_open` | 打开网址，返回带 ref 编号的可交互元素快照 |
| `browser_snapshot` | 重新取快照（ref 会变，点之前建议先看） |
| `browser_click` | 点元素（真实鼠标事件，支持 iframe / Shadow DOM / 动态按钮 / 开新页） |
| `browser_type` | 往 ref 输入框填字（填完可能要 `browser_key` 按回车） |
| `browser_key` | 按键：Enter / Tab / Escape / 方向键 |
| `browser_scroll` | 滚动（懒加载、加载更多） |
| `browser_extract` | 取当前页面正文纯文本（最多 6000 字） |
| `browser_images` | 抓页面所有图片（含懒加载、背景图、视频封面），打包 zip 给下载链接 |
| `browser_screenshot` | 截可视区域，返回图片下载链接 |
| `browser_video` | 抓视频直链（mp4 / m3u8 / webm），能下的 mp4 顺带存好 |
| `browser_click_at` | 按坐标点（canvas / 游戏 / 纯图片按钮兜底） |
| `browser_close` | 关掉释放内存（任务做完或长时间不用） |

纯阅读能用 `open_url` 就别开浏览器——浏览器慢、吃内存。它主要用在要**登录、要点、要填表、要抓图**的场景。

**实现要点**：一个 Chromium 实例 + **每用户一个 BrowserContext**（登录态隔离、省内存），Playwright 跑在单独线程；默认拦掉图片 / 字体 / 媒体渲染省内存（抓图走 request 另取）；每用户 60 步预算、空闲 10 分钟自动关、最多 2 个 Context。依赖：`pip install playwright patchright`，`patchright install chromium`，系统库 `patchright install-deps chromium`。

网页端侧边栏的「接管浏览器」能实时看到这台 Chromium 的画面并手动操作（过验证码、点 DOM 拿不到的按钮），适合模型卡住时人手接一把。

---

## 技能包与兜底检索

**技能包**（`list_skills` / `load_skill` / `list_skill_files` / `read_skill_file`）——服务器上装好的 Agent Skills（一套 SKILL.md 工作流 + 脚本 + 模板）。做 PPT、做海报这类有专门技能的任务，模型会先看一眼有没有对应的，加载后按里面写的步骤做。

装在哪（都不进 git）：`<项目>/skills/<名字>/`（管理员手工装）、`<项目>/workspace/skills/<名字>/`（后台从仓库安装的）。目录里必须有 `SKILL.md`。放好不用重启，`list_skills` 会扫到。

**沙盒对技能开了一个口子**：`workspace/skills/` 下的脚本按「管理员安装的受信任代码」处理，允许起子进程（PPT 转换、字体处理要靠它）；模型现写的普通脚本仍然禁用 `subprocess`、不能联网。所以**只装你信任的技能**——它的系统能力比普通脚本强。

后台 `技能 → 技能包` 可以启用 / 停用 / 从 GitHub 或 Gitee 装（只接受这两家的地址，子目录有路径校验，拉取在临时目录里做）/ 删除（只删 `workspace/skills/` 里的）。

**兜底检索**（`ultra_search` / `ultra_fetch`，付费）——常规 `web_search` 跑题 / 被墙 / 结果不可靠时，用权威度更高的搜索；`open_url` 抓不到或被拦时，把网页转成 Markdown。都是「最后再试」的手段，一次任务最多 3 次，不默认用。base / key 在后台「接口与 Key」里改。

---

## 新增一个工具

1. 在 `deck/tools/` 下新建模块，写一个 `SPECS` 列表（OpenAI function calling 的 schema）和同名函数：

```python
SPECS = [
    {
        "type": "function",
        "function": {
            "name": "say_hi",
            "description": "打个招呼",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "对方名字"}
                },
                "required": ["name"],
            },
        },
    },
]


def say_hi(name: str) -> str:
    return f"你好 {name} 喵"
```

2. 加进 `deck/tools/__init__.py` 的 `_MODULES`；
3. 在 `TOOL_GROUPS` 里给它归个组（不归就默认落到「基础」）。

注册表按约定工作：**schema 里的 `name` 要和函数名一致**，它会自动把函数挂到 `TOOL_MAP` 上。

返回值会自动过两层保护：会把外部内容带进上下文的工具，返回值统一包一层「外部内容」边界；`run_code` 有审计钩子兜底（见 [SECURITY.md](SECURITY.md)）。
