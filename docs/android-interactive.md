# Android 端接入「交互式选择卡」（ask_user）

服务端只在客户端声明支持交互时才下发 `ask_user` 工具。App 端要做三件事：

1. 请求头带上 `X-Client-Capabilities: interactive`
2. SSE 事件里识别 `await_user`，渲染成选择卡
3. 用户点选后，**照常走发送消息的接口**发一条 `我选择：<选项>`（服务端会接着跑）

不需要新增接口、不需要保存半截状态——服务端是「弹完就结束这一轮」，用户选择后是一次新的普通对话。

## 1. 请求头

```kotlin
val request = Request.Builder()
    .url("$baseUrl/api/chat")
    .addHeader("Content-Type", "application/json")
    .addHeader("X-Client-Capabilities", "interactive")
    .post(body.toRequestBody("application/json".toMediaType()))
    .build()
```

> 老版本 App 不带这个头 → 服务端不会给模型 `ask_user`，模型会在正文里直接问，
> 用户打字回复即可，功能不丢。

## 2. 事件模型

服务端 SSE 事件是 `data: {json}`，其中：

```json
{"type":"await_user","question":"要哪种风格？","options":["学术简约","活泼插画","商务深色"],"allow_custom":true}
```

紧接着还会来一个 `{"type":"done", ...}`，表示这一轮结束（`awaiting: true`）。

数据类可以这样加：

```kotlin
@Serializable
data class AwaitUserEvent(
    val question: String = "",
    val options: List<String> = emptyList(),
    val allowCustom: Boolean = true,
)
```

（字段名是 `allow_custom`，用 `@SerialName("allow_custom")` 映射。）

## 3. 解析事件

在现有 SSE 解析的位置加一个分支：

```kotlin
when (event.type) {
    "thinking" -> upsertThinking(event.text)
    "content" -> appendContent(event.text)
    "tool_pending" -> showToolPending(event.name)
    "tool_start" -> startTool(event.name, event.arguments)
    "tool_end" -> finishTool(event.name, event.result)
    "artifact" -> addArtifact(event)
    "sticker" -> addSticker(event)
    // ↓↓↓ 新增
    "await_user" -> pendingAsk = AwaitUserEvent(
        question = event.question,
        options = event.options,
        allowCustom = event.allowCustom,
    )
    "done" -> {
        isStreaming = false
        pendingAsk?.let { pendingAsk = null /* 卡片已经在 UI 里了 */ }
    }
}
```

## 4. 选择卡 Composable

```kotlin
@Composable
fun AskUserCard(
    ask: AwaitUserEvent,
    onPick: (String) -> Unit,
) {
    var custom by remember { mutableStateOf("") }
    var sent by remember { mutableStateOf(false) }

    Column(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 6.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(MaterialTheme.colorScheme.surface)
            .padding(14.dp)
    ) {
        Text(ask.question, style = MaterialTheme.typography.titleSmall)
        Spacer(Modifier.height(10.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ask.options.forEach { option ->
                OutlinedButton(
                    onClick = { if (!sent) { sent = true; onPick(option) } },
                    enabled = !sent,
                ) { Text(option) }
            }
        }
        if (ask.allowCustom) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = custom,
                    onValueChange = { custom = it },
                    singleLine = true,
                    enabled = !sent,
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("也可以自己输入…") },
                )
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = { if (!sent && custom.isNotBlank()) { sent = true; onPick(custom.trim()) } },
                    enabled = !sent && custom.isNotBlank(),
                ) { Text("确定") }
            }
        }
        if (sent) {
            Text("已选择", color = MaterialTheme.colorScheme.primary, fontSize = 12.sp)
        }
    }
}
```

`FlowRow` 需要 `androidx.compose.foundation.layout.FlowRow`（`@OptIn(ExperimentalLayoutApi::class)`），
不想用就直接用 `Column` 竖着摆。

## 5. 点选后继续

把选择当成一条普通用户消息发出去即可（走现有的发送逻辑）：

```kotlin
fun onPick(option: String) {
    sendMessage("我选择：$option")   // 就是平时发消息那个函数
}
```

服务端收到后模型会接着原来的任务往下做（历史里已经有那次工具调用和回执），
新一轮的 `thinking/content/tool_*` 事件照常渲染。

## 6. 兼容性

- 不带 `X-Client-Capabilities: interactive` 的客户端：模型不会调用 `ask_user`，
  会在正文里问「要哪种风格？」→ 用户打字回答，下一轮继续
- 服务端侧 `ask_user` 属于 `base` 工具组，后台关掉「基础」组时会一起消失
- 一次选择卡最多 6 个选项；`allow_custom` 为 true 时给输入框
