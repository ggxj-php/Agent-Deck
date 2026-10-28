package com.agentdeck.app.data

/** 当前身份卡（人设）的公开信息。 */
data class Persona(
    val id: String = "",
    val name: String = "Agent",
    val selfName: String = "",
    val userName: String = "",
    val avatar: String = "",
    val welcome: String = "",
    val placeholder: String = "",
    val statusOnline: String = "",
    val statusThinking: String = "",
    val theme: String = "#ffb7d5",
) {
    /** 头像/图标是相对路径，拼成完整 URL 交给 Coil。 */
    fun avatarUrl(baseUrl: String): String =
        if (avatar.startsWith("http")) avatar else baseUrl + avatar
}

/** 登录用户。 */
data class Account(
    val id: Long = 0,
    val name: String = "",
    val inviteCode: String = "",
    val isAdmin: Boolean = false,
    val banned: Boolean = false,
)

/** 侧边栏的一条会话。 */
data class SessionSummary(
    val id: String,
    val title: String,
    val messages: Int = 0,
    val updatedAt: Double = 0.0,
)

/** 历史里的一条消息（后端已把工具调用过滤掉，只剩正文）。 */
data class HistoryItem(val role: String, val text: String)

/** 「上下文 xx%」。 */
data class ContextUsage(
    val known: Boolean = false,
    val tokens: Int = 0,
    val limit: Int = 0,
    val percent: Double = 0.0,
    val messages: Int = 0,
)

data class HistoryPage(
    val sessionId: String,
    val title: String,
    val history: List<HistoryItem>,
    val context: ContextUsage,
)

/** 已上传的附件（服务端只回 id/名字/大小，内容按 id 引用）。 */
data class UploadedFile(
    val id: String,
    val name: String,
    val size: Long = 0,
)

/** 图形验证码（注册要填），image 是 data URL，可以直接交给 Coil 显示。 */
data class Captcha(
    val id: String = "",
    val image: String = "",
    val ttl: Int = 300,
)

/** SSE 事件。字段名与后端 `ask_stream` 吐出来的 JSON 一一对应。 */
sealed interface ChatEvent {
    data class Thinking(val text: String) : ChatEvent
    data class Content(val text: String) : ChatEvent
    data class ToolPending(val name: String) : ChatEvent
    data class ToolStart(val name: String, val arguments: String) : ChatEvent
    data class ToolEnd(val name: String, val result: String) : ChatEvent
    data class Sticker(val mood: String, val name: String) : ChatEvent
    data class Artifact(val id: String, val title: String) : ChatEvent
    data class Cut(val reason: String) : ChatEvent
    data class Compressed(val dropped: Int, val beforeTokens: Int, val afterTokens: Int) : ChatEvent
    data class Done(val context: ContextUsage?) : ChatEvent
    data class Error(val message: String) : ChatEvent
    data class Unknown(val type: String) : ChatEvent
}
