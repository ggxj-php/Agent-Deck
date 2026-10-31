package com.agentdeck.app.ui

import androidx.compose.runtime.Immutable

import com.agentdeck.app.data.Account
import com.agentdeck.app.data.Captcha
import com.agentdeck.app.data.ContextUsage
import com.agentdeck.app.data.Persona
import com.agentdeck.app.data.SessionSummary
import com.agentdeck.app.data.UpdateInfo
import com.agentdeck.app.data.UploadedFile

/** 一条助手消息里的内容块，顺序就是流式输出的顺序。 */
@Immutable
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

    /**
     * 系统提示（压缩上下文、网络中断、报错、限速排队……）。
     *
     * [canContinue] 对应网页端 `noticeBubble(turn, text, canContinue)` 的第三个参数：
     * 断流/截断/报错这类「接着再讲一次就行」的场景，下面会挂一个「继续」按钮。
     */
    data class Notice(
        val text: String,
        val isError: Boolean = false,
        val canContinue: Boolean = false,
    ) : Block

    /** 助手画出来的图/卡片，点开用浏览器看。 */
    data class Artifact(val id: String, val title: String) : Block

    /**
     * ask_user 选择卡：模型要用户拍板时弹出来。
     * 点选项后照常发一条「我选择：X」，服务端接着跑下一轮。
     */
    data class Ask(
        val question: String,
        val options: List<String>,
        val allowCustom: Boolean = true,
        val chosen: String? = null,
    ) : Block
}

enum class Role { USER, ASSISTANT }

/** 登录页的两个页签。 */
enum class AuthMode { LOGIN, REGISTER }

@Immutable
data class UiMessage(
    val id: Long,
    val role: Role,
    val blocks: List<Block> = emptyList(),
    val images: List<String> = emptyList(),
    val files: List<UploadedFile> = emptyList(),
    val streaming: Boolean = false,
    /** 流式回复的开始时间。 */
    val startedAt: Long = 0L,
    /** 正在等首 token（或等工具/下一轮），要显示「思考中」占位。 */
    val waiting: Boolean = false,
    val waitingLabel: String = "正在思考…",
    val waitingSince: Long = 0L,
)

data class ChatUiState(
    val booting: Boolean = true,
    val account: Account? = null,
    val persona: Persona = Persona(),
    /** 当前生效的后端地址（用户填的优先，没填就用构建时注入的）。 */
    val serverUrl: String = "",
    /** 还没配服务器（占位地址）——登录页要把地址输入框摆出来。 */
    val needsServer: Boolean = true,
    /** 用户中心要显示的会话总数（`/api/auth/me` 的 sessions 字段）。 */
    val accountSessions: Int = 0,
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
