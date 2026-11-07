# Agent Deck

一个可自部署的迷你 Agent。**说话的人设由「身份卡」决定**——一张卡换一套名字、语气、欢迎语、头像、表情包和主题色，后台点一下就能切；默认带一张示例卡。

它不只是聊天：能联网搜资料、打开网页读原文、用真实浏览器登录和点页面，也能在沙箱里读写文件、编译运行代码，写完自己跑一遍验证，跑不通自己改。数学、算法、组成原理、嵌入式、物联网、数字取证这些方向配了专门的工具（汇编仿真、外设计算、逻辑仿真、高精度计算、文件雕复），配上本地知识库还能先翻教材原文再回答并标出处。

三种用法：**终端**直接对话、**网页版**（手机也能用）、**原生安卓客户端**（Kotlin + Compose，源码在 `feat/android-app` 分支的 `android/` 下）。

> **仓库里不带知识库文档**（避免版权问题）：要自己往 `knowledge/` 里放 PDF / DOCX / Markdown，详见 [知识库](docs/KNOWLEDGE-BASE.md)。不配也能正常聊，只是查不到资料。

## 快速开始

**要求**：Python 3.10+。

```bash
git clone <你的仓库> && cd Agent-Deck
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env：至少填 DEEPSEEK_API_KEY 和 TAFFY_ADMIN_PASSWORD

python -m deck.web        # 网页版：http://127.0.0.1:8000
python agent.py           # 或者终端版
```

手机连同一个 wifi，访问 `http://<电脑局域网IP>:8000` 也能用。想要跟线上完全一致的依赖版本（Python 3.12 验证过）就装 `requirements.lock.txt`。

**可选依赖**，不装也能跑：

| 装什么 | 有什么用 |
|---|---|
| `graphviz` + `fonts-noto-cjk` | `draw_diagram` 的排版引擎：树 / 流程 / ER / 数据流图交给 `dot` 自动布局，缺字体中文标签会算窄、框偏小。不装退回内置布局 |
| `pillow` | **抽 PDF 内嵌图必须装**（`pypdf` 的 `page.images` 依赖它，不装会静默返回「没有图」）；上传图片的解码 / 重编码也用它 |
| `bcrypt` | 密码哈希。不装退回标准库 scrypt，也能用，只是慢一点 |
| `fonttools` | `pypdf` 解析 PDF 里嵌入的 CFF 字体。不装可能抽出乱码并刷警告 |
| `g++` / `javac` / `node` | `run_code` 跑 C/C++ / Java / JS。不装其它语言照常 |

### 网页版有什么

- **要登录**：没登录访问任何页面都会跳 `/login`；注册要邀请码 + 图形验证码。
- **会话在服务器上**：会话和消息都写进 SQLite，关页面、换设备、重启服务，回来接着聊都在。
- **流式输出**：正文一个字一个字冒，思考过程和工具调用是可折叠卡片。
- **代码高亮 + 一键复制**，markdown 渲染前过一遍 DOMPurify 防 XSS。
- **每轮至少一张真·表情包**（gif 会动，按原图宽高比缩放，横竖都不变形）。
- **看得到上下文用量**：输入框上方一条百分比条，到 80% 变橙并自动压缩。
- **发图片**：一轮最多 6 张，服务端验魔术字节 + 重编码去 EXIF，20 分钟后自动清理；最近一两轮会自动放回多模态，所以追问「刚才那张图」它还记得。
- **手机窄屏也照顾了**：顶栏按钮收进「⋯」下拉，上下文用量一直在输入框正上方。
- **侧边栏还有两个入口**：「技能」看服务器上装了哪些技能包（只读）；「接管浏览器」实时看服务器上的 Chromium 画面并亲手操作。

改 `.py` 要重启服务；改 `static/` 下的前端文件刷新页面即可（页面和静态资源带 `Cache-Control: no-cache` + ETag，没变就 304）。

### Docker

```bash
# 只跑 Python（镜像约 200MB）
docker build -t agent-deck .
docker run --rm -p 8000:8000 --env-file .env \
    -v "$PWD/workspace:/app/workspace" \
    -v "$PWD/knowledge:/app/knowledge" \
    -v "$PWD/.cache:/app/.cache" \
    agent-deck

# 带上 C/C++ / Java / Node 工具链（多约 300MB）
docker build --build-arg WITH_TOOLCHAINS=1 -t agent-deck:full .

# 或者
docker compose up -d --build
```

镜像里**不会**打进 `.env` / `admin.json`（见 `.dockerignore`），运行时数据靠 volume 挂。

## 配置

大部分配置能在后台 `/admin` 里改（改完多半不用重启），`.env` 是「首次启动前的默认值」。常用的几个：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 无，**必填** | 聊天模型 key。没填程序启动时直接报错，不会静默失败 |
| `TAFFY_ADMIN_NAME` / `TAFFY_ADMIN_PASSWORD` | `admin` / 随机 | 默认管理员，第一次启动时库里没有管理员才创建。不配密码会随机生成、写进 `.cache/admin_initial_password.txt`（权限 600）并打进日志，**登录后马上改掉再删这个文件** |
| `VISION_API_KEY` / `VISION_BASE_URL` | 跟聊天 | 只有「聊天和看图分两家」时才要 |
| `SEARCH_TRANSLATE_MODEL` / `TRANSLATE_BASE_URL` / `TRANSLATE_API_KEY` | 跟聊天 | 知识库把中文问题翻成英文关键词用的那一路 |
| `EMBED_API_KEY` / `EMBED_BASE_URL` / `EMBED_MODEL` / `EMBED_DIM` | 不配 | 知识库向量检索，三样配齐才启用。不配也能跨语言检索 |
| `CXX` | PATH 里的 `g++` | `run_code` 编译 C/C++ 用，填绝对路径即可（`.c` 会自动找同目录的 `gcc`）。**不用加进系统 PATH**，配在项目里更省事 |
| `SEARCH_PROXY` | 直连 | `web_search` 走不走代理：`system` 跟随环境变量，或直接填 `http://127.0.0.1:7897`。国内引擎走代理反而容易被拒 |
| `TAFFY_SITE_WWW` / `TAFFY_SITE_API` | 占位域名 | 后台「访问流量」按这两个域名分站统计。**代码里不写死任何域名**，不配只是面板显示占位名 |
| `TAFFY_SERVICE_NAME` | `taffy-agent` | 后台「检查更新」「知识库上传」自动重启时用的 systemd 服务名 |
| `TAFFY_DB_PATH` | `.cache/taffy.db` | SQLite 位置 |
| `GENERATED_TTL_HOURS` | `12` | 生成物（pptx / pdf / docx / 项目目录）保留多少小时，过期自动删 |
| `CONTEXT_LIMIT` / `CONTEXT_COMPRESS_AT` | 1M / 0.8 | 上下文窗口大小、用到多少比例开始压缩，见下文 |
| `LLM_TPM_BUDGET` / `LLM_MAX_RETRIES` | 200000 / 5 | 每分钟 token 预算（超了先排队）和接口重试次数，见下文 |

单次回复最多输出多少 token 在 `deck/config.py` 的 `MAX_TOKENS`，默认 **32768**。带思考的模型思维链也占这个额度，卡太小会出现「思考到一半戛然而止、这一轮没有正文」——官方默认的 4096 在复杂问题上很容易被思考吃光，所以放得比较宽。想省钱可以往下调。默认模型是 `deepseek-flash`、地址 `https://api.deepseek.com/v1`，也在 `config.py` 开头，换别家改那里或直接在后台改。

账号是**邀请制**：每个用户注册时自动分到一个唯一邀请码，新用户必须填一个已有的邀请码、**再填对图形验证码**才能注册。登录只要用户名 + 密码。第一个账号就是这个默认管理员，它的邀请码在后台「用户管理」和聊天页侧边栏都能看到。

## 后台管理

服务起来后访问 **`/admin`**。鉴权走用户账号：必须先用普通账号登录，且那个账号 `is_admin = 1`。

| 分组 | 面板 |
|---|---|
| 概览 | 仪表盘（CPU / 内存 / 磁盘 / 负载）、访问流量（PV / 地区 / 风险 IP / 一键封禁）|
| 模型与接口 | 模型（聊天 / 图片两套 + 上下文上限）、接口与 Key（三套独立地址和 key）、向量模型、**工具开关**、配置总览、用量（最近 10 分钟 / 今天 / 累计 tokens 与请求数）|
| 人设 | 身份卡（名字 / 称呼 / 欢迎语 / 提示词 / 表情包）、系统提示词（共享约束 + 当前卡） |
| 技能 / 素材 / 文件 | 技能包（启用停用 / 从仓库安装 / 删除）、表情包库（含 zip 批量导入）、知识库文档、工作区文件 |
| 用户 / 系统 | 用户管理（建号 / 开权限 / 封号 / 重置密码）、用户反馈（App 里提的 issue，可看图 / 接受 / 拒绝 / 删除）、检查更新（Gitee / GitHub 一键拉取并重启） |

几条要点：

- **聊天和图片是两套独立配置**，模型 / Key / 地址各一套。可以聊天用 GLM、看图用 DeepSeek，互不影响。
- **Key 不回显、不显示头尾**，页面只告诉你它是后台设过的还是 `.env` 里的；输入框留空表示「不改」。
- 改动存在项目根目录的 `admin.json`（已 gitignore，里面有明文 key）。没配过的项自动回落 `.env` / `config.py`，删掉也不会坏。
- **生效时机**：模型、key、接口地址立刻生效；只有向量模型那张卡要重启（索引得重算）。
- 后台权限不轻：能管用户、能往 `workspace/` 传文件、能触发更新并重启服务。**给谁开管理员之前想清楚**。

面板逐项说明（表情包命名规则、上传限制、检查更新的快进策略、封号时长格式）见 **[docs/ADMIN.md](docs/ADMIN.md)**。

## 内置工具

49 个工具，分 12 组，后台「工具开关」按组开关——关掉的组模型连定义都看不到（省 token、少幻觉），硬调也会被拦。

| 组 | 工具 |
|---|---|
| 基础 | 时间、`list_files` / `read_file` / `write_file` / `delete_file`、`send_sticker`、`ask_user`（拿不准时反问用户）|
| 联网 | `web_search`（附最相关两条正文摘录）、`open_url`（跟跳转 + 抽正文） |
| 知识库 | `search_knowledge`（返回原文片段与页码） |
| 文档与图片 | `read_pdf` / `read_doc` / `read_img`（走视觉模型） |
| 跑代码 / 画图 | `run_code`、`render_html`、`draw_diagram`、`make_download_link` |
| 数字取证 | `hash_file` / `identify_file` / `extract_strings` / `carve_files` / `file_metadata` |
| 嵌入式 | `asm_sim`（16 位 CPU）、`asm86`（8086 实模式）、`mcu_calc`、`logic_sim` |
| 计算 | `sci_calc`（超高精度）、`number_convert`（进制 / 补码 / IEEE754） |
| 文本 | `regex_test` / `diff_text` / `json_tool` / `count_text` |
| 浏览器 | `browser_open` 等 12 个：真实浏览器登录、点击、填表、抓图 / 视频 |
| 技能包 | `list_skills` / `load_skill` / …：加载服务器上装好的 Agent Skills |
| 兜底 | `ultra_search` / `ultra_fetch`：常规搜索跑题或抓不到时的付费兜底 |

**`draw_diagram`** 值得一提：模型只给几十 token 的节点数据，服务端生成 SVG（树 / 数据结构图 / 流程 / 关系 / 数据流 / 架构 / 时序 / 图表 / 时间线 / ER），比让模型手写整段 HTML 快一个数量级。

工具详解（汇编指令集、外设计算项、正则与 JSON 的边界处理、怎么加自己的工具）见 **[docs/TOOLS.md](docs/TOOLS.md)**。

## 技能包（Agent Skills）

支持 Claude Code 风格的技能包：一个目录里放 `SKILL.md`（YAML frontmatter 写 name / description，正文写工作流）+ 脚本 + 模板，模型按需加载、按里面的步骤干活。做 PPT、文档、海报这类有固定流程的活，走技能比让它即兴发挥稳得多。

**装在哪**（都不进 git）：

```
<项目>/skills/<技能名>/            # 管理员手工装
<项目>/workspace/skills/<技能名>/  # 后台从仓库安装、或运行时装的
```

目录里必须有 `SKILL.md`：

```markdown
---
name: my-skill
description: 一句话说明干什么、什么时候用
---

# My Skill
（给模型看的详细工作流）
```

放好不用重启，`list_skills` 会扫到。

**模型怎么用**：`list_skills` 看有哪些（名字 + 描述）→ `load_skill(name)` 把 SKILL.md 读进上下文 → 按步骤用 `run_code` 或其它工具干活，需要细节就用 `read_skill_file` 读 → 成品放 workspace，用 `make_download_link` 发给用户。

**后台管理**（`/admin` → 技能 → 技能包）：看已装的（说明 / 大小 / 来源 / 启用状态）、启用停用（停用后模型看不到，文件不删）、从仓库安装（填 `https://github.com/owner/repo`，可指定子目录如 `skills/ppt-master`，后台用 `git clone --depth 1 --sparse` 拉）、删除（只删 `workspace/skills/` 里的）。安装只接受 github / gitee 地址，子目录做路径校验，拉取在临时目录里做，失败不会弄脏已有技能。

**沙盒的例外**：技能脚本用 `run_code` 跑，仍然只能读写 `workspace/`、不能联网；但 `workspace/skills/` 下的脚本按「管理员安装的受信任代码」处理，**允许起子进程**（PPT 转换、字体处理要靠它），模型现写的普通脚本仍然禁用 `subprocess`。依赖要提前 `pip install -r workspace/skills/<名字>/requirements.txt`。**只装你信任的技能**——技能脚本的系统能力比较强。

**ask_user**：技能流程里常有「选模板 / 选风格 / 选页数」这种决策点，模型可以调 `ask_user(question, options)`，网页端弹选择卡、点完自动继续。这是能力协商：只有声明了 `X-Client-Capabilities: interactive` 的客户端才拿得到这个工具，老版 App 拿不到，模型会改成在正文里直接问。

**接管浏览器**：侧边栏「接管浏览器」打开 `/takeover`，能实时看到服务器上 Chromium 的画面并用鼠标 / 触屏直接操作（过验证码、点 DOM 抓不到的按钮）。

**产物清理**：技能 / 代码生成出来的成品（`.pptx` `.ppt` `.pdf` `.docx` `.xlsx`）和 `workspace/projects/` 下的项目目录默认保留 12 小时（`GENERATED_TTL_HOURS`），后台每 10 分钟扫一次；上传的附件仍是 20 分钟 TTL，两套互不影响。清理不碰 `skills/`、`uploads/` 和代码文件。

## 安全

分七层，详细机制和「能做到什么、做不到什么」见 [docs/SECURITY.md](docs/SECURITY.md)：

| # | 层 | 一句话 |
|---|---|---|
| 1 | 文件工具 | 所有路径过 `safe_path()`，锁死在 `workspace/` 内 |
| 2 | `run_code` | 不经 shell；`.py` 装审计钩子、`.js` 用 Node 权限模型、C/C++/Java 跑前静态审查；子进程里的 `*KEY*` / `*TOKEN*` 环境变量一律抹掉 |
| 3 | `open_url` | 防 SSRF：内网 / 本机 / 链路本地直接拒，跳转自己一跳一跳跟、每跳重新校验 |
| 4 | 登录 | bcrypt + HttpOnly cookie（库存 sha256）+ 邀请码 + 图形验证码 + 限速 + 封号踢登录态 |
| 5 | 注入防线 | 外部内容统一包边界、人格约束、输出侧 `_LeakGuard` 拦系统提示词泄露 |
| 6 | 上传图片 | 只认魔术字节 + 解码重编码去 EXIF，20 分钟 TTL |
| 7 | 来源管控 | 境外 IP 拦截（只对 443）、IP 黑名单写 UFW、fail2ban |

**边界，务必知道**：

- **静态审查能被绕过**（宏拼装、函数指针、动态拼路径）。C/C++/Java 没有真隔离，它挡的是「模型手滑」，不是「有人处心积虑」。**只在自己信任的机器上用，别对公网开放。**
- **提示词注入只能降低成功率，做不到根除**。真正的兜底是执行层沙箱。别把它当保险柜。
- **账号体系不等于安全审计**。邀请码泄露 = 谁都能注册；后台一个 admin 账号约等于半个服务器权限。

## 知识库

把文档丢进 `knowledge/`（子目录也认），重启服务（或在后台「知识库文档」里传）就会建索引。支持 `pdf` / `docx` / `html` / `md` / `txt` / `log`。

检索走**三路**，各取 100 个候选再融合排序（RRF，用名次而不是原始分）：

| 那一路 | 靠什么 | 管什么 |
|---|---|---|
| 原查询词 | 倒排索引 + BM25 | 精确词：`STM32F103`、`I2C`、题解里的函数名 |
| 英文检索词 | 中文问题先让模型写成一句英文关键词，再查一遍 | **跨语言**——中文提问也能搜到英文教材 |
| 向量（可选） | embedding 比余弦 | 语义：词面对不上但意思相近 |

原查询那一路永远都在，另外两路只是往结果里补——没配 key、接口挂了、模型不听话，都只是少一路，检索照常返回，不会整个失败。

「英文检索词」那一路不用向量模型也能解决中文问英文书的问题（词匹配对「内存取证」和 `memory forensics` 是零交集）。实测「内存取证里易失性数据该怎么采集」→ 8/8 都是讲 `volatile data` 的段落。

向量模型是可选的：任何 OpenAI 兼容的 `/embeddings` 都行，小块模型会自动探测单条输入上限、按它收口块长，避免 400 或静默截断。建库进度、限流重试、「断了怎么接着补」、缓存机制都在 **[docs/KNOWLEDGE-BASE.md](docs/KNOWLEDGE-BASE.md)**。

## 上下文与自动压缩

每轮开口前先估这段对话占多少 token。压缩触发点不是死比例，而是 `min(窗口 × 0.8, 窗口 − 输出预留 − 65536)`；压完保留「窗口 − 输出预留」的 16% 原文，其余压成摘要。参数都在 `deck/config.py`，也能用同名环境变量覆盖：

| 参数 | 默认 | 意思 |
|---|---|---|
| `CONTEXT_LIMIT` | `1000000` | 模型窗口多大。**默认按 1M 长上下文模型给**，用 64K / 128K 窗口的要往下调——**填大了比填小了危险**：会把压缩推迟到模型真正吃不下的时候，那一轮直接被拒。后台「模型」卡里改能立刻生效 |
| `CONTEXT_COMPRESS_AT` | `0.8` | 压缩触发的比例上限 |
| `CONTEXT_HEADROOM_TOKENS` | `65536` | 给输出和下一轮留的余量 |
| `CONTEXT_RETAIN_RATIO` | `0.16` | 压完保留多少原文（占可用窗口的比例）|

- **token 是估的**：中文一字一 token、英文四字符一 token，图片按 800 算。不追求精确，够决定「压不压」就行（前端那条百分比条也是这个值）。
- **怎么压**：把要丢掉的那段按顺序写成临时文本交给聊天模型摘要（`temperature=0`），留住结论、代码结果、知识库出处、用户偏好。压完历史变成 `[系统提示, 一条摘要消息, 最近若干条原文]`。
- **切点只落在用户消息上**，否则会把「模型要调工具」和「工具返回结果」拆散，下一轮直接报格式错。
- **摘要失败就当没压过**，历史原样不动；同一会话并发时拿锁再量一遍，不会重复压。
- 压缩时聊天里会冒一条系统提示，写清压掉了多少条、压前压后多少 token。**压缩是丢信息的**，细节只留在摘要里。

### 长任务还有几层保护

照 DeepSeek Harness 的 `dsh-compaction-*` / `dsh-llm-retry` 移植：

| 机制 | 做法 |
|---|---|
| **工具结果修剪** | 超过 8192 字符只留头 4096 + 尾 1024，中间标「省略了多少」；完整结果前端卡片还能看到，模型需要可重读 |
| **错误分类** | AUTH / QUOTA / RATE_LIMIT / CONTEXT_OVERFLOW / INVALID / SERVER / TIMEOUT / TRANSPORT。**额度不够（QUOTA）不重试**，直接说明白；限流 / 5xx / 超时才重试 |
| **重试** | 指数退避 500ms→10s、10% 抖动、尊重 `Retry-After`，最多 5 次；**已经开始吐字的不重试**（避免半截话重复）|
| **上下文溢出** | 抛 `CONTEXT_OVERFLOW` → 强制压缩历史 → 重试 1 次 |
| **TPM 排队** | 发请求前按「最近 60 秒已用 + 本次预估」排队，默认每分钟 20 万 token（`LLM_TPM_BUDGET`），超了先等（最多 45 秒，前端提示「等 N 秒再继续」）。不会被 429 打断，代价是长任务变慢 |
| **工具熔断** | 单轮工具调用超 150 次、试错 20 轮、或同一工具连续返回几乎一样的结果 → 立刻熔断，逼模型停下来向用户说明卡点（防止读图失败时无限重写脚本重试）|
| **用量观测** | 每轮记 `[usage] prompt=… completion=…` 日志；后台 `GET /api/admin/usage` 看最近 10 分钟 / 今天 / 累计的 tokens 和请求数 |

## 测试与 CI

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                                          # 192 个用例
python -m pytest -q --cov=deck --cov-report=term-missing     # 看覆盖率
```

测试重点压在**安全相关**的地方：沙箱路径越界、`run_code` 运行时护栏（真起子进程验证被拦）、非 Python 代码的静态审查、上传图片的魔术字节与 EXIF 剥离、`render_html` 清洗、IP 黑名单护栏、登录 / 封号 / 越权、会话按用户隔离、验证码一次性。

CI（`.github/workflows/ci.yml`）在 push `main` 或开 PR 时跑：Python 3.11 / 3.12、`compileall`、`pytest`、覆盖率、`ruff`（不阻塞）、`docker build`。仓库同时镜像在 Gitee，但 CI 只在 GitHub Actions 上跑。

## 目录结构

```
Agent-Deck/
├── agent.py                 # 终端入口
├── requirements*.txt        # 运行依赖 / 锁定版本 / 开发依赖
├── Dockerfile               # WITH_TOOLCHAINS=1 带上 g++/JDK/Node
├── docs/                    # 定制 / 后台 / 工具 / 知识库 / 安全 详解
├── .github/workflows/ci.yml # CI：3.11/3.12 跑测试 + 语法检查 + docker build
├── personas/                # 身份卡（人设包，已 gitignore）
├── tests/                   # pytest
├── knowledge/               # 知识库原始文档（自己放，已 gitignore）
├── .cache/                  # 索引缓存与 SQLite，可删
├── workspace/               # 沙箱根目录，Agent 只能在这里动文件
└── deck/
    ├── config.py            # 模型、运行参数、共享约束的默认值
    ├── settings.py          # 后台可改的配置（覆盖 .env）
    ├── core.py              # DeckAgent：对话历史 + 工具循环
    ├── sandbox.py           # 路径越界检查
    ├── fuse.py              # 工具熔断：反复试错没有进展就停
    ├── context.py           # 上下文估算 + 压缩策略
    ├── llm.py / llm_errors.py  # 模型接入层 + 错误分类（决定重不重试）
    ├── ratelimit.py         # TPM 令牌桶排队
    ├── db.py / store.py     # SQLite：用户 / 登录态 / 会话 / 消息
    ├── auth.py              # bcrypt、邀请码、token、默认管理员
    ├── persona.py / skills.py  # 身份卡、技能包
    ├── browser.py           # Playwright + 每用户 BrowserContext
    ├── cleanup.py           # 生成物过期清理
    ├── kb/                  # 知识库：解析 / 中英术语 / 翻译 / 向量 / 索引
    ├── tools/               # 每个文件一组工具，__init__.py 注册派发
    └── web/
        ├── app.py           # FastAPI：鉴权 + SSE + 会话 CRUD
        ├── admin.py         # 后台接口（配置 / 用户 / 流量 / 仪表盘 / 更新 / 技能）
        ├── content.py       # 表情包 / 知识库 / 工作区文件管理
        ├── live.py          # 生成与连接解耦：后台线程跑，连接只订阅
        ├── issues.py        # App 用户反馈
        ├── update.py        # 检查更新 + ff-only 拉取重启
        └── static/          # index.html / app.js / style.css / admin.* / vendor
```

## 改成你自己的

完整指南见 **[docs/CUSTOMIZE.md](docs/CUSTOMIZE.md)**。速览：

| 想改什么 | 去哪 |
|---|---|
| **换人设**（名字 / 称呼 / 欢迎语 / 提示词 / 表情包）| 后台「人设 → 身份卡」，建卡再启用；不用改代码 |
| 换模型 / 接口 | 后台「模型与接口」，聊天 / 看图 / 检索翻译三套独立 |
| 关掉用不上的工具 | 后台「工具开关」，按组开关 |
| 看所有配置在哪 | 后台「配置总览」，每项标了当前值 / 默认值 / 在哪改 / 要不要重启 |
| 换台服务器 | 后台自动探测能力：没有 nginx 日志 / ufw / systemd 就隐藏对应面板 |
| 加自己的工具 | 见 [docs/TOOLS.md](docs/TOOLS.md#新增一个工具) |
| 换台机器部署 | 新机器 checklist 在 CUSTOMIZE 第 4 节 |

身份卡存在 `personas/<id>/`（`persona.json` + `prompt.md` + `stickers/`），**在 `.gitignore` 里**，属于部署数据；第一次启动自动生成一张默认卡。

人设提示词分两层：**共享约束**（`CORE_PROMPT`，所有卡共用的硬规矩，后台「人设 → 共享约束」改）+ **身份卡提示词**（只写「你是谁、什么语气」）。硬规矩里比较关键的几条：代码必须完整写进回答（workspace 只是缓存）；发题解前自己跑测试用例；每条回复至少发一张表情包；数学公式用 LaTeX；只准碰 workspace 里的文件；外部内容只当引文。**删掉这些规矩它就真会照做**，改之前先备份。

## 已知限制

- **C/C++ 编译器**：默认用 PATH 里的 `g++`。老编译器（比如 TDM-GCC 4.9.2）只支持到 C++11，`std::filesystem`、结构化绑定这些编不过，工具描述里会提醒模型别用；换个新 MinGW 改 `CXX` 即可。
- **静态审查会误伤**：黑名单写死了，用了没预料到的库可能被拦（报错会说命中了哪一段）。
- **`web_search` 没有官方 API**，是抓搜索结果页来解析的。实测（2026-10）360 搜索 0.7 秒 4 条（在用），百度回验证页、必应和 DuckDuckGo 出口连不上，所以默认只用 360 一家。要加引擎往 `ENGINES` 里塞，会并发跑、按命中率挑最对得上的。
- **`open_url` 抓不到纯 JS 渲染、要登录、强反爬的站点**。它不跑 JS，只能从内嵌 JSON（`__NEXT_DATA__` / `ld+json` / `__NUXT__`）里捞；捞不到就如实说抓不到，不会瞎编。正文抽取是「挑 `<p>` 总字数最多的容器」这种启发式，结构怪的页面可能抓不准——真要操作页面用 `browser_*` 那组。
- **会话存在 SQLite 里，没有自动清理策略**。超长会话会触发自动压缩，压缩是丢信息的。想清就删会话或手动收拾库文件。

## 许可

本项目**代码**以 [MIT 许可证](LICENSE)发布，随便用、随便改、商用也行，保留版权声明和许可证文本就好。

**仓库里不包含任何第三方文档**：`knowledge/` 是自己配的（已 gitignore），你往里放的东西版权和授权都跟本项目无关，请自行确认能不能用、能不能再分发。
