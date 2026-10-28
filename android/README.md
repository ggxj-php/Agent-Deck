# Android 客户端

纯原生 Compose 实现的 Agent Deck 客户端：消息正文用自带的轻量 markdown 渲染器（不为每条消息开 WebView），
公式用 JLaTeXMath 原生排版，AI 画出来的图直接用 Coil 解码 SVG 内联显示、可全屏缩放。
主题跟着「身份卡」走：后端 `/api/persona` 给一个 `theme` 色值，客户端用和网页
`applyTheme()` 相同的混色公式派生整套配色，连桌面图标也会跟着换。

**这个 App 不绑定任何服务器**：地址要么由本机构建配置注入，要么在登录页里填，适合直接改成你自己的部署。

## 功能

- 登录 / 邀请码注册 / 登出（HttpOnly cookie，本地加密保存，30 天免登录）
- 改密码（校验旧密码，新密码至少 8 位）
- 会话列表：新建、切换、删除；历史里连续的多条 assistant 记录会合并成一个气泡
- 流式对话（SSE）：思考过程、工具调用卡片、表情包、产物、上下文压缩提示、报错
- 发图：本地压到 1600px / JPEG 85 后以 data URL 上传
- 发文件：PDF / Office / 源码等（服务端限 20MB、单条最多 6 个），上传后带 id 随消息发出
- 附件下载：agent 给的 `/d/<token>` 链接渲染成卡片，可选「App 内下载」（带登录态，存到
  `下载/AgentDeck`）或「用浏览器」
- 下载管理：任务列表、实时速度、进度、取消/重试/打开
- 上下文占用条（token 数 / 上限 / 百分比）
- 「回到底部」悬浮按钮：往上翻历史时出现，点一下回到最新
- 图片全屏查看：双指缩放、拖动、双击放大（用户图片 / 表情包 / AI 产物都支持）
- 数学公式：JLaTeXMath 原生排版（分式、上下标、根号），失败时退回 Unicode 文本
- 代码块：按语言高亮关键字/字符串/注释，右上角「复制」一键复制
- 版本检查：问当前服务器的 `/static/app-version.json`，有新版弹窗提示
- 主题：顶栏渐变、气泡、按钮、背景全部由身份卡的 `theme` 派生；换卡即换肤
- 消息复制：长按选择任意文字，或点气泡右下角「复制」整条拿走
- 图片查看：白底大图，双指缩放/拖动/双击放大，可一键保存到相册（Pictures/AgentDeck）
- 返回键：抽屉开着先收抽屉，退出应用前会二次确认

## 配置服务器

后端地址**不在代码里**，构建时注入，优先级：

1. 环境变量 `DECK_BASE_URL`
2. `android/local.properties` 里的 `deck.baseUrl`（这个文件已被 gitignore）
3. 都没配就用占位地址 `https://example.com`

```bash
DECK_BASE_URL=https://your-server.example ./gradlew :app:assembleRelease
```

App 内不提供改服务器的入口，避免把具体部署信息带进界面。

地址只支持 `https://`，配套的 `res/xml/network_security_config.xml` 也禁用了明文流量。

## 版本检查（可选）

App 会请求 `{你的服务器}/static/app-version.json`，格式见 `version.sample.json`：

```json
{
  "versionCode": 3,
  "versionName": "1.2",
  "notes": "更新说明",
  "url": "https://your-server.example/downloads"
}
```

`versionCode` 大于 App 自己的版本号才会提示；`url` 留空就打开服务器首页。
服务器上没放这个文件时，检查会静默失败，不影响使用。

## 构建

需要 JDK 17+ 和 Android SDK（compileSdk 35）：

```bash
cd android
./gradlew :app:assembleDebug          # 产物：app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest      # 行内/块解析的单元测试
```

## 结构

```
android/
├── version.sample.json      # 版本检查文件的模板
└── app/src/main/
    ├── java/com/agentdeck/app/
    │   ├── data/            # OkHttp、SSE、cookie 加密存储、服务器配置、版本检查
    │   └── ui/              # Compose 界面、ViewModel、markdown/LaTeX/SVG 渲染
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

## 渲染与性能

- 消息正文走 `ui/components/Markdown.kt` 的自研渲染器（标题/段落/列表/代码块/表格/引用/链接/图片），
  Compose 原生绘制，没有 WebView、没有 JS 桥
- 公式块（`$$...$$` 或独占一行的 `$...$`）交给 JLaTeXMath 画成位图并做 LRU 缓存
- AI 产物是 SVG，Coil + SvgDecoder 直接内联显示（`/api/artifact/{id}/svg`），并限制最大高度避免空白
- 流式输出时最多 ~70ms 重渲一次；自动吸底只在流式期间每 120ms 跟随一次，空闲时不碰滚动
- 历史里只有心情没有文件名的表情包标记（如 `[sticker:laugh]`）会按心情随机挑一张，挑不到就不占位

## 安全设计

| 面 | 做法 |
|---|---|
| 登录态 | HttpOnly cookie，AES-GCM（Android Keystore 里的密钥）加密后存 SharedPreferences |
| 传输 | `usesCleartextTraffic=false` + 只信任系统证书 |
| 本地数据 | `allowBackup=false`，不写日志、不接第三方统计，代码里没有密钥 |
| 外部链接 | 一律交给系统浏览器打开，App 内不加载任意网页 |

## 已知限制

- Markdown 只实现了模型常用的子集（没有脚注、嵌套列表缩进等）
- 产物目前按 SVG 内联；如果产物不是矢量图，会退化成「在浏览器打开」的卡片
- 启动器图标只能按色相分 8 档切换（Android 不允许任意改图标），同一时间只启用一个别名
