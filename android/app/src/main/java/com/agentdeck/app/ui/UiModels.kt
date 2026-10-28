package com.agentdeck.app.ui

import com.agentdeck.app.data.Account
import com.agentdeck.app.data.Captcha
import com.agentdeck.app.data.ContextUsage
import com.agentdeck.app.data.Persona
import com.agentdeck.app.data.SessionSummary
import com.agentdeck.app.data.UpdateInfo
import com.agentdeck.app.data.UploadedFile

/** 一条助手消息里的内容块，顺序就是流式输出的顺序。 */
sealed interface Block {
    data class Text(val markdown: String) : Block

    data class Tool(
        val name: String,
        val args: String,
        val result: String? = null,
        val done: Boolean = false,
    ) : Block

    data class Thinking(val text: String, val done: Boolean = false) : Block

    data class Sticker(val url: String) : Block

    /** 系统提示（压缩上下文、网络中断、报错……）。 */
    data class Notice(val text: String, val isError: Boolean = false) : Block

    /** 助手画出来的图/卡片，点开用浏览器看。 */
    data class Artifact(val id: String, val title: String) : Block
}

enum class Role { USER, ASSISTANT }

/** 登录页的两个页签。 */
enum class AuthMode { LOGIN, REGISTER }

data class UiMessage(
    val id: Long,
    val role: Role,
    val blocks: List<Block> = emptyList(),
    val images: List<String> = emptyList(),
    val files: List<UploadedFile> = emptyList(),
    val streaming: Boolean = false,
)

data class ChatUiState(
    val booting: Boolean = true,
    val account: Account? = null,
    val persona: Persona = Persona(),
    /** 当前生效的后端地址（构建时注入或用户在登录页改的）。 */
    val serverUrl: String = "",
    val sessions: List<SessionSummary> = emptyList(),
    val currentSessionId: String? = null,
    val messages: List<UiMessage> = emptyList(),
    val context: ContextUsage = ContextUsage(),
    val busy: Boolean = false,
    val thinking: Boolean = true,
    val authMode: AuthMode = AuthMode.LOGIN,
    val captcha: Captcha? = null,
    val captchaLoading: Boolean = false,
    val update: UpdateInfo? = null,
    val loggingIn: Boolean = false,
    val loginError: String? = null,
    val toast: String? = null,
)
