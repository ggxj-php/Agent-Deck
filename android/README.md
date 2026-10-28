# Android 客户端

Compose 做壳（会话列表 / 输入框 / 工具卡片），单条消息的正文交给 WebView 渲染——
复用网页端同一套 `marked + DOMPurify + highlight.js`，所以 Markdown、代码高亮、表情包
在两边的表现一致。主题跟着「身份卡」走：后端 `/api/persona` 给一个 `theme` 色值，
客户端用和网页 `applyTheme()` 完全相同的混色公式派生整套配色。

<table>
  <tr>
    <td><img src="docs/screenshot-login.png" width="240" alt="登录页"></td>
    <td><img src="docs/screenshot-chat.png" width="240" alt="聊天页"></td>
  </tr>
</table>

## 功能

- 登录 / 邀请码注册 / 登出（HttpOnly cookie，本地加密保存，30 天免登录）
- 改密码（校验旧密码，新密码至少 8 位）
- 会话列表：新建、切换、删除；历史消息按角色还原成气泡
- 流式对话（SSE）：思考过程、工具调用卡片、表情包、产物卡片、上下文压缩提示、报错
- 发图：本地压到 1600px / JPEG 85 后以 data URL 上传
- 发文件：PDF / Office / 源码等（服务端限 20MB、单条最多 6 个），上传后带 id 随消息发出
- 上下文占用条（token 数 / 上限 / 百分比）
- 「回到底部」悬浮按钮：往上翻历史时出现，点一下回到最新
- 主题：顶栏渐变、气泡、按钮、背景全部由身份卡的 `theme` 派生；换卡即换肤
- **桌面图标也跟着身份卡变**：manifest 里挂了 8 个按色相区分的 activity-alias，
  运行时只启用与主题色最接近的那个（`ui/theme/LauncherIcon.kt`）

## 性能

- 流式渲染用 `conflate + 顺序 collect` 做节流（最多 ~70ms 重渲一次，且总是渲染最新全文），
  不会因为 token 来得太密而一帧都不更新
- 自动吸底改成 120ms 轮询一次，只在「用户没往上翻」时跟随，避免每个 token 都触发滚动
- WebView 滑出屏幕即 `destroy()`，长对话不会攒下几十个渲染进程

## 构建

需要 JDK 17+ 和 Android SDK（compileSdk 35）：

```bash
cd android
./gradlew :app:assembleDebug          # 产物：app/build/outputs/apk/debug/app-debug.apk
```

后端地址写在 `app/build.gradle.kts` 的 `buildConfigField("String", "BASE_URL", ...)`，
只接受 `https://`，配套的 `res/xml/network_security_config.xml` 也禁用了明文流量。

## 结构

```
android/
├── app/src/main/
│   ├── java/icu/taffy/deck/
│   │   ├── data/            # OkHttp、SSE、cookie 加密存储、数据模型
│   │   └── ui/              # Compose 界面、ViewModel、消息块渲染
│   ├── assets/render/       # WebView 用的模板 + marked/DOMPurify/highlight
│   └── res/xml/             # 网络安全配置（禁明文）
└── docs/                    # 截图
```

## 安全设计

| 面 | 做法 |
|---|---|
| 登录态 | HttpOnly cookie，AES-GCM（Android Keystore 里的密钥）加密后存 SharedPreferences |
| WebView | 只放行本站 HTTPS 与 `data:`，其余请求 403；点链接交给系统浏览器，WebView 内永不导航 |
| WebView 能力 | 禁文件访问、禁 `content://`、禁 DOM storage、禁多窗口、禁混合内容 |
| 传输 | `usesCleartextTraffic=false` + 只信任系统证书 |
| 本地数据 | `allowBackup=false`，不写日志、不接第三方统计，代码里没有密钥 |

## 已知限制

- 数学公式按网页端的「降级成可读文本」处理，没有引入 MathJax/KaTeX
- 产物（画出来的图）点开用浏览器查看，没有在 App 内做 iframe 内联
- 启动器图标只能按色相分 8 档切换（Android 不允许任意改图标），同一时间只启用一个别名
