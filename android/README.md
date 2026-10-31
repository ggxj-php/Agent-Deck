# Android 客户端

纯原生 Compose 实现的 Agent Deck 客户端：消息正文用自带的轻量 markdown 渲染器（不为每条消息开 WebView），
公式用 JLaTeXMath 原生排版，AI 画出来的图直接用 Coil 解码 SVG 内联显示、可全屏缩放。
主题跟着「身份卡」走：后端 `/api/persona` 给一个 `theme` 色值，客户端用和网页
`applyTheme()` 相同的混色公式派生整套配色，连桌面图标也会跟着换。

**这个 App 不绑定任何服务器**：仓库里只有一个占位地址 `https://example.com`，真实地址由构建时注入
（`local.properties` / 环境变量），或者用户在登录页填一次（存本机）。所以把这份代码发出去不会泄漏部署信息。

## 功能

对齐网页端（`deck/web/static/app.js`），逐项对应：

| 能力 | 网页端 | App |
|---|---|---|
| 登录 / 邀请码注册 / 登出 | ✅ | ✅ |
| 改密码 | ✅ 用户中心 | ✅ 用户中心 |
| 会话列表：新建 / 切换 / 删除 | ✅ | ✅ |
| 流式对话（SSE 全事件） | ✅ | ✅ |
| 思考过程折叠卡片 | ✅ | ✅ |
| 工具调用卡片 | ✅ | ✅ |
| 表情包（按句子边界插入） | ✅ | ✅ |
| AI 产物内联显示 | iframe | Coil 解 SVG |
| 上下文压缩 / 截断 / 报错提示 | ✅ | ✅ |
| 限速排队、工具熔断等系统提示（`notice`） | ✅ | ✅ |
| 出错 / 断流后的「继续」按钮 | ✅ | ✅ |
| 上下文占用条 | ✅ | ✅ |
| 图片全屏缩放 | ✅ | ✅ |
| 代码块语言标签 + 复制 | ✅ | ✅ |
| 附件上传（图 / 文档） | ✅ | ✅ |
| 附件下载 + 下载管理 | 浏览器 | App 内下载队列 |
| 邀请码显示 / 点击复制 | ✅ | ✅ |
| 用户中心（注册时间 / 会话数） | ✅ | ✅ |
| 管理员后台入口 | ✅ | ✅ 系统浏览器 |
| 服务器地址可改 | 同源，无需 | ✅ 登录页 / 用户中心 |
| 版本检查 | — | ✅ 两路来源 |
| 崩溃兜底页 | — | ✅ 含远端上报 |

其它细节：

- 发图：本地压到 1600px / JPEG 85 后以 data URL 上传
- 发文件：PDF / Office / 源码等（服务端限 20MB、单条最多 6 个），上传后带 id 随消息发出
- 附件下载：agent 给的 `/d/<token>` 链接渲染成卡片，可选「App 内下载」（带登录态，存到
  `下载/AgentDeck`）或「用浏览器」
- 返回键：抽屉开着先收抽屉，退出应用前会二次确认

## 配置服务器

后端地址**不在仓库里**。四种来源，优先级从高到低：

1. **用户在登录页填**（存 `SharedPreferences`，随时可在用户中心里改）
2. 环境变量 `DECK_BASE_URL`
3. `android/local.properties` 里的 `deck.baseUrl`（该文件已被 gitignore）
4. 都没有就用占位地址 `https://example.com` —— 此时登录页会强制要求先填地址

```bash
DECK_BASE_URL=https://your-server.example ./gradlew :app:assembleOssRelease
```

填地址时会自动补协议：纯域名补 `https://`，IP / `localhost` / 局域网段补 `http://`，末尾多余斜杠会去掉。
传输层默认仍只允许 HTTPS，明文例外只对局域网调试有意义，见 `res/xml/network_security_config.xml`。

## 版本检查

按顺序试两个来源，任何一个拿到更新就提示：

1. `{服务器}/static/app-version.json`（自建分发用，能写更新说明）

   ```json
   {
     "versionCode": 13,
     "versionName": "1.12",
     "notes": "更新说明",
     "url": "https://your-server.example/downloads"
   }
   ```

2. `{服务器}/api/app/latest`（后端自带，扫 `static/app/` 目录找版本号最大的 APK）

`versionCode` 有值就比它；没有（第 2 种只给版本名）就按 `1.11` vs `1.2` 逐段比较——
不会出现「1.9 比 1.11 新」这种字符串比较的错误。两路都拿不到就静默跳过，不影响使用。

## 崩溃日志

崩溃不闪退：主线程出错时拉起崩溃页，展示原因和堆栈，可以一键复制、重新打开。

日志写在 `filesDir/crashes/`，最多保留 20 份。远端上报走 `POST /api/app-crash`——这个接口不在
FastAPI 里，而是 nginx 单独反代到一个只监听 `127.0.0.1` 的小服务（`crash_receiver.py`），
日志追加进服务器的 `crashes/crashes.jsonl`。没配这条反代时请求会 404，客户端把文件改名 `.failed`
归档，不会每次启动重传一遍。

## 构建

需要 JDK 17+ 和 Android SDK（compileSdk 35）：

```bash
cd android
./gradlew :app:assembleOssDebug        # 产物：app/build/outputs/apk/oss/debug/app-oss-debug.apk
./gradlew :app:assembleOssRelease      # 开源包（R8 + 资源压缩）
./gradlew :app:assembleLocalRelease    # 私有包：用 app/src/local 里的名字和图标
./gradlew :app:testLocalDebugUnitTest  # 单元测试
```

## 结构

```
android/
├── version.sample.json      # 版本检查文件的模板
└── app/src/main/
    ├── java/com/agentdeck/app/
    │   ├── data/            # OkHttp、SSE、cookie 加密、本机设置、版本检查、崩溃兜底
    │   │   ├── AppSettings.kt     # 服务器地址（不入库）
    │   │   ├── DeckApi.kt         # 全部 HTTP 接口 + SSE 解析
    │   │   ├── Models.kt          # 数据模型与 ChatEvent
    │   │   ├── UpdateChecker.kt   # 两路版本检查
    │   │   └── CrashReporter.kt   # 崩溃落盘与上报
    │   └── ui/              # Compose 界面、ViewModel、markdown/LaTeX/SVG 渲染
    │       ├── ChatScreen.kt      # 顶栏 / 抽屉 / 用户中心
    │       ├── ChatViewModel.kt   # 状态与事件归并
    │       └── components/        # MessageItem / Markdown / Composer / DownloadCenter
    └── res/xml/             # 网络安全配置（禁明文）
```

## 名字与图标

默认（开源）包名 `com.agentdeck.app`、显示名 **Agent Deck**、图标是仓库内置的气泡图标。

想换成自己的名字/头像，不需要改仓库里的任何资源：在 `android/app/src/local/res/` 放覆盖资源，
这个目录已被 gitignore，不会被提交：

```
app/src/local/res/values/strings.xml          # <string name="app_name">你的名字</string>
app/src/local/res/mipmap-*/ic_launcher.png    # 启动器图标（各密度）
app/src/local/res/mipmap-anydpi-v26/ic_launcher.xml
```

然后按 flavor 构建：

```bash
./gradlew :app:assembleOssRelease    # 开源默认：Agent Deck + 气泡图标
./gradlew :app:assembleLocalRelease  # 私有：用 app/src/local 里的名字和图标
```

## 交互式选择卡（ask_user）

服务端在客户端声明支持交互时，才允许模型用 `ask_user` 弹选择框（技能包、PPT 风格这类需要拍板的地方）。

- `/api/chat` 请求头带 `X-Client-Capabilities: interactive`
- SSE 里识别 `await_user`（`question` / `options` / `allow_custom`），渲染成气泡里的选择卡
- 用户点选项（或自己输入）后，App 照常发一条 `我选择：<选项>` 的消息，服务端接着跑下一轮
- 不带这个头的旧客户端不会收到 `ask_user`，模型会在正文里直接问，功能不丢

⚠️ 这套能力要求服务端是**带 `ask_user` 的版本**（`deck/tools/ask.py` + `core.py` 里的
`await_user` 分支）。用旧后端时 App 不会报错，只是永远收不到选择卡。

技能包（Agent Skills）本身在服务端安装/启停，App 侧不需要额外接口：模型调用
`list_skills` / `load_skill` 时，App 按普通工具卡片展示。

## 渲染与性能

- 消息正文走 `ui/components/Markdown.kt` 的自研渲染器（标题/段落/列表/代码块/表格/引用/链接/图片），
  Compose 原生绘制，没有 WebView、没有 JS 桥
- 代码围栏严格匹配：结束围栏长度不得低于开始围栏，ASCII 画里的 `~ ~ ~` 不会把代码块提前截断
- 公式块（`$$...$$` 或独占一行的 `$...$`）交给 JLaTeXMath 画成位图并做 LRU 缓存
- AI 产物是 SVG，Coil + SvgDecoder 直接内联显示（`/api/artifact/{id}/svg`），并限制最大高度避免空白
- 流式输出时最多 ~60ms 重渲一次；自动吸底是「开关模型」——手指一碰就停，空闲时不碰滚动
- 历史里只有心情没有文件名的表情包标记（如 `[sticker:laugh]`）会按心情随机挑一张，挑不到就不占位

## 安全设计

| 面 | 做法 |
|---|---|
| 登录态 | HttpOnly cookie，AES-GCM（Android Keystore 里的密钥）加密后存 SharedPreferences |
| 传输 | `usesCleartextTraffic=false` + 只信任系统证书 |
| 本地数据 | `allowBackup=false`，不写日志、不接第三方统计，代码里没有密钥、没有服务器地址 |
| 外部链接 | 一律交给系统浏览器打开，App 内不加载任意网页 |
| 崩溃日志 | 只存本机 `filesDir/crashes`，上报失败即归档，不循环重传 |

## 已知限制

- Markdown 只实现了模型常用的子集（没有脚注、嵌套列表缩进等）
- 产物目前按 SVG 内联；如果产物不是矢量图，会退化成「在浏览器打开」的卡片
- 启动器图标只能按色相分 8 档切换（Android 不允许任意改图标），同一时间只启用一个别名
- 服务端没有 PATCH 会话接口，所以 App 不支持「重命名会话」
