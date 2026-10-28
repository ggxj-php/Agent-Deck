package com.agentdeck.app.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.agentdeck.app.BuildConfig
import com.agentdeck.app.DeckApp
import com.agentdeck.app.data.ChatEvent
import com.agentdeck.app.data.ContextUsage
import com.agentdeck.app.data.DeckApi
import com.agentdeck.app.data.DeckException
import com.agentdeck.app.data.Persona
import com.agentdeck.app.data.SessionSummary
import com.agentdeck.app.data.UpdateChecker
import com.agentdeck.app.data.UploadedFile
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * 聊天页的全部状态与动作。
 *
 * 事件流的归并规则跟网页端一致：思考块打头、正文按到达顺序追加、工具按「同名未收尾」配对、
 * 正文一来就把思考块标成已完成。
 */
class ChatViewModel(private val api: DeckApi) : ViewModel() {

    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    private var nextId = 1L
    private var stickerLib: Map<String, List<String>> = emptyMap()
    private var sendJob: Job? = null

    init {
        boot()
    }

    // ---------------- 启动 ----------------

    private fun boot() {
        viewModelScope.launch {
            // 人设和表情包都是公开接口，先拿下来，登录页也能显示名字和欢迎语
            val persona = runCatching { api.persona() }.getOrDefault(Persona())
            stickerLib = runCatching { api.stickers() }.getOrDefault(emptyMap())
            _state.update { it.copy(persona = persona, serverUrl = api.baseUrl) }

            try {
                val account = api.me()
                val sessions = runCatching { api.sessions() }.getOrDefault(emptyList())
                api.syncWebViewCookie()
                _state.update {
                    it.copy(
                        booting = false,
                        account = account,
                        sessions = sessions,
                        currentSessionId = sessions.firstOrNull()?.id,
                        serverUrl = api.baseUrl,
                    )
                }
                sessions.firstOrNull()?.let { openSession(it.id) }
                checkForUpdate()
            } catch (error: DeckException) {
                _state.update {
                    it.copy(
                        booting = false,
                        account = null,
                        loginError = if (error.code == 401) null else error.message,
                    )
                }
            } catch (error: Exception) {
                // 断网、DNS 失败、证书不对……都归到这里，别让首屏卡在转圈
                _state.update {
                    it.copy(
                        booting = false,
                        account = null,
                        loginError = "连不上服务器：${error.message ?: error.javaClass.simpleName}",
                    )
                }
            }
        }
    }

    // ---------------- 登录 / 登出 ----------------

    fun login(name: String, password: String) {
        if (name.isBlank() || password.isEmpty()) {
            _state.update { it.copy(loginError = "用户名和密码都得填") }
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(loggingIn = true, loginError = null) }
            try {
                api.login(name.trim(), password)
                afterAuth()
            } catch (error: DeckException) {
                _state.update { it.copy(loggingIn = false, loginError = error.message) }
            } catch (error: Exception) {
                _state.update {
                    it.copy(loggingIn = false, loginError = "登录失败：${error.message ?: "网络异常"}")
                }
            }
        }
    }

    /** 切换登录 / 注册页签；切到注册就顺手拿一张验证码。 */
    fun switchAuthMode(mode: AuthMode) {
        _state.update { it.copy(authMode = mode, loginError = null) }
        if (mode == AuthMode.REGISTER && _state.value.captcha == null) refreshCaptcha()
    }

    fun refreshCaptcha() {
        viewModelScope.launch {
            _state.update { it.copy(captchaLoading = true, loginError = null) }
            try {
                val captcha = api.captcha()
                _state.update { it.copy(captcha = captcha, captchaLoading = false) }
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        captchaLoading = false,
                        loginError = if (error is DeckException) error.message else "验证码加载失败",
                    )
                }
            }
        }
    }

    fun register(name: String, password: String, invite: String, answer: String) {
        val user = name.trim()
        if (user.length < 3 || user.length > 20) {
            _state.update { it.copy(loginError = "用户名要 3~20 个字") }
            return
        }
        if (password.length < 8) {
            _state.update { it.copy(loginError = "密码至少 8 位") }
            return
        }
        if (invite.isBlank()) {
            _state.update { it.copy(loginError = "邀请码得填") }
            return
        }
        if (answer.isBlank()) {
            _state.update { it.copy(loginError = "验证码得填") }
            return
        }
        val captchaId = _state.value.captcha?.id.orEmpty()
        if (captchaId.isEmpty()) {
            _state.update { it.copy(loginError = "验证码过期了，点图换一张") }
            refreshCaptcha()
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(loggingIn = true, loginError = null) }
            try {
                api.register(user, password, invite.trim(), captchaId, answer.trim())
                afterAuth()
            } catch (error: DeckException) {
                _state.update { it.copy(loggingIn = false, loginError = error.message) }
                refreshCaptcha()
            } catch (error: Exception) {
                _state.update {
                    it.copy(loggingIn = false, loginError = "注册失败：${error.message ?: "网络异常"}")
                }
                refreshCaptcha()
            }
        }
    }

    /** 登录或注册成功后的共同收尾：拉人设、会话、表情包，并把 cookie 同步给 WebView。 */
    private suspend fun afterAuth() {
        val account = api.me()
        val persona = runCatching { api.persona() }.getOrDefault(Persona())
        val sessions = runCatching { api.sessions() }.getOrDefault(emptyList())
        stickerLib = runCatching { api.stickers() }.getOrDefault(emptyMap())
        api.syncWebViewCookie()
        _state.update {
            it.copy(
                loggingIn = false,
                account = account,
                persona = persona,
                sessions = sessions,
                currentSessionId = null,
                messages = emptyList(),
                context = ContextUsage(),
                captcha = null,
                authMode = AuthMode.LOGIN,
                serverUrl = api.baseUrl,
            )
        }
        sessions.firstOrNull()?.let { openSession(it.id) }
        checkForUpdate()
    }

    fun logout() {
        viewModelScope.launch {
            api.logout()
            api.syncWebViewCookie()
            _state.update {
                ChatUiState(booting = false, persona = it.persona, serverUrl = api.baseUrl)
            }
        }
    }

    // ---------------- 版本检查 ----------------

    fun checkForUpdate(manual: Boolean = false) {
        viewModelScope.launch {
            val info = runCatching { UpdateChecker.check(api.baseUrl) }.getOrNull()
            if (info != null) {
                _state.update { it.copy(update = info) }
            } else if (manual) {
                _state.update { it.copy(toast = "已经是最新版本 v${BuildConfig.VERSION_NAME}") }
            }
        }
    }

    fun dismissUpdate() {
        _state.update { it.copy(update = null) }
    }

    fun changePassword(oldPassword: String, newPassword: String) {
        if (oldPassword.isEmpty()) {
            _state.update { it.copy(toast = "旧密码得填") }
            return
        }
        if (newPassword.length < 8) {
            _state.update { it.copy(toast = "新密码至少 8 位") }
            return
        }
        viewModelScope.launch {
            try {
                api.changePassword(oldPassword, newPassword)
                _state.update { it.copy(toast = "密码改好了，下次用新密码登录") }
            } catch (error: DeckException) {
                _state.update { it.copy(toast = error.message) }
            } catch (error: Exception) {
                _state.update { it.copy(toast = "改密码失败：${error.message ?: "网络异常"}") }
            }
        }
    }

    // ---------------- 会话 ----------------

    fun refreshSessions() {
        viewModelScope.launch {
            runCatching { api.sessions() }.onSuccess { list ->
                _state.update { it.copy(sessions = list) }
            }
        }
    }

    fun newSession() {
        if (_state.value.busy) return
        viewModelScope.launch {
            runCatching { api.createSession() }
                .onSuccess { session ->
                    _state.update {
                        it.copy(
                            sessions = listOf(session) + it.sessions,
                            currentSessionId = session.id,
                            messages = emptyList(),
                            context = ContextUsage(),
                        )
                    }
                }
                .onFailure { report(it) }
        }
    }

    fun openSession(id: String) {
        if (_state.value.busy) return
        viewModelScope.launch {
            try {
                _state.update { it.copy(currentSessionId = id) }
                val page = api.history(id)
                val loaded = HistoryParser.toMessages(page.history) { mood, name ->
                    stickerUrl(name, mood)
                }
                syncNextId(loaded)
                _state.update {
                    it.copy(
                        messages = loaded,
                        context = page.context,
                        currentSessionId = id,
                    )
                }
            } catch (error: Exception) {
                report(error)
            }
        }
    }

    fun deleteSession(id: String) {
        viewModelScope.launch {
            runCatching { api.deleteSession(id) }
                .onSuccess {
                    _state.update { current ->
                        val remaining = current.sessions.filterNot { it.id == id }
                        val nextId = if (current.currentSessionId == id) remaining.firstOrNull()?.id else current.currentSessionId
                        current.copy(
                            sessions = remaining,
                            currentSessionId = nextId,
                            messages = if (current.currentSessionId == id) emptyList() else current.messages,
                        )
                    }
                    _state.value.currentSessionId?.let { openSession(it) }
                }
                .onFailure { report(it) }
        }
    }

    /** 历史消息用的是 1..N，自增 id 必须跳过它们，否则 LazyColumn 的 key 会重复。 */
    private fun syncNextId(messages: List<UiMessage>) {
        val max = messages.maxOfOrNull { it.id } ?: 0L
        if (max >= nextId) nextId = max + 1
    }

    // ---------------- 发送 ----------------

    fun send(
        text: String,
        images: List<String> = emptyList(),
        files: List<UploadedFile> = emptyList(),
        thinking: Boolean = true,
    ) {
        if (_state.value.busy) return
        val message = text.trim()
        if (message.isEmpty() && images.isEmpty() && files.isEmpty()) return
        val useThinking = thinking && _state.value.thinking

        sendJob = viewModelScope.launch {
            val sessionId = ensureSession() ?: return@launch
            val userId = nextId++
            val assistantId = nextId++
            _state.update { current ->
                current.copy(
                    messages = current.messages + listOf(
                        UiMessage(
                            id = userId,
                            role = Role.USER,
                            blocks = listOf(Block.Text(message)),
                            images = images,
                            files = files,
                        ),
                        UiMessage(
                            id = assistantId,
                            role = Role.ASSISTANT,
                            streaming = true,
                            startedAt = System.currentTimeMillis(),
                            waiting = true,
                            waitingLabel = "正在思考…",
                            waitingSince = System.currentTimeMillis(),
                        ),
                    ),
                    busy = true,
                    toast = null,
                )
            }
            try {
                api.streamChat(
                    sessionId = sessionId,
                    message = message,
                    images = images,
                    thinking = useThinking,
                    files = files.map { it.id },
                ) { event -> handle(event) }
            } catch (error: Exception) {
                val reason: String = when (error) {
                    is DeckException -> error.message ?: "请求失败"
                    else -> "这条回复断在半路了：${error.message ?: "网络波动"}"
                }
                handle(ChatEvent.Error(reason))
            } finally {
                finishTurn(sessionId)
            }
        }
    }

    fun stop() {
        api.cancelChat()
    }

    fun toggleThinking() {
        _state.update { it.copy(thinking = !it.thinking) }
    }

    fun dismissToast() {
        _state.update { it.copy(toast = null) }
    }

    private suspend fun ensureSession(): String? {
        _state.value.currentSessionId?.let { return it }
        return runCatching { api.createSession() }
            .onSuccess { session ->
                _state.update {
                    it.copy(sessions = listOf(session) + it.sessions, currentSessionId = session.id)
                }
            }
            .onFailure { report(it) }
            .getOrNull()
            ?.id
    }

    private fun finishTurn(sessionId: String) {
        // 这一轮结束了，排队的表情包全部放出来
        flushStickers(force = true)
        updateLastAssistant { it.copy(streaming = false) }
        _state.update { it.copy(busy = false) }
        viewModelScope.launch {
            runCatching { api.contextUsage(sessionId) }.onSuccess { usage ->
                _state.update { it.copy(context = usage) }
            }
            runCatching { api.sessions() }.onSuccess { list ->
                _state.update { it.copy(sessions = list) }
            }
        }
        viewModelScope.launch { /* 让最后一条正文的 WebView 有机会完成渲染 */ }
    }

    /**
     * 排队中的表情包：和网页端的 queuedStickers 一样，先收着，
     * 等正文出现句末标点/换行再插，避免把一句话从中间劈开。
     */
    private val queuedStickers = ArrayDeque<String>()

    /** 最后一段正文里 $$ 的数量是奇数，说明公式还没收尾。 */
    private fun mathStillOpen(): Boolean {
        val message = _state.value.messages.lastOrNull { it.role == Role.ASSISTANT } ?: return false
        val text = message.blocks.lastOrNull { it is Block.Text } as? Block.Text ?: return false
        return Regex("\\$\\$").findAll(text.markdown).count() % 2 == 1
    }

    private fun lastAssistantText(): String? {
        val message = _state.value.messages.lastOrNull { it.role == Role.ASSISTANT } ?: return null
        return (message.blocks.lastOrNull { it is Block.Text } as? Block.Text)?.markdown
    }

    /**
     * 把排队的表情包放出来。force=false 时只有正文刚好停在句子边界才插，
     * 判断规则和网页端 flushQueuedStickers 一致（句末标点 / 换行 / 没在代码块里）。
     */
    private fun flushStickers(force: Boolean = false) {
        if (queuedStickers.isEmpty()) return
        if (!force) {
            val text = lastAssistantText()
            if (text.isNullOrEmpty()) {
                // 回复一开头就是表情包：立刻显示，别让气泡空着
            } else {
                if (mathStillOpen()) return
                val fences = Regex("```").findAll(text).count()
                if (fences % 2 == 1) return
                if (!endsAtSentenceBoundary(text)) return
            }
        }
        if (force) {
            while (queuedStickers.isNotEmpty()) {
                val url = queuedStickers.removeFirst()
                updateLastAssistant { msg ->
                    msg.copy(blocks = settleThinking(msg.blocks) + Block.Sticker(url))
                }
            }
        } else {
            val url = queuedStickers.removeFirst()
            updateLastAssistant { msg ->
                msg.copy(blocks = settleThinking(msg.blocks) + Block.Sticker(url))
            }
        }
    }

    /** 把最新一条助手消息的「等待首 token」占位状态更新一下。 */
    private fun setWaiting(label: String) {
        val now = System.currentTimeMillis()
        updateLastAssistant { it.copy(waiting = true, waitingLabel = label, waitingSince = now) }
    }

    private fun clearWaiting() {
        updateLastAssistant { it.copy(waiting = false) }
    }

    // ---------------- 事件归并 ----------------

    private fun handle(event: ChatEvent) {
        // 和网页端 handleEvent() 一样：除 sticker 外，任何事件都先撤掉「思考中」占位；
        // 表情包不算，因为模型常先甩一张图再去跑十几秒的工具。
        if (event !is ChatEvent.Sticker) clearWaiting()

        when (event) {
            is ChatEvent.Thinking -> updateLastAssistant { msg ->
                val blocks = msg.blocks.toMutableList()
                val last = blocks.lastOrNull()
                if (last is Block.Thinking && !last.done) {
                    blocks[blocks.size - 1] = last.copy(text = last.text + event.text)
                } else {
                    blocks.add(Block.Thinking(event.text))
                }
                msg.copy(blocks = blocks)
            }

            is ChatEvent.Content -> {
                updateLastAssistant { msg ->
                    val blocks = settleThinking(msg.blocks).toMutableList()
                    val last = blocks.lastOrNull()
                    if (last is Block.Text) {
                        blocks[blocks.size - 1] = last.copy(markdown = last.markdown + event.text)
                    } else {
                        blocks.add(Block.Text(event.text))
                    }
                    msg.copy(blocks = blocks)
                }
                flushStickers()
            }

            is ChatEvent.ToolPending -> setWaiting("正在准备 ${event.name}…")

            is ChatEvent.ToolStart -> {
                flushStickers(force = true)
                updateLastAssistant { msg ->
                val blocks = settleThinking(msg.blocks).toMutableList()
                    blocks.add(Block.Tool(event.name, event.arguments, null, false))
                    msg.copy(blocks = blocks)
                }
            }

            is ChatEvent.ToolEnd -> updateLastAssistant { msg ->
                val blocks = msg.blocks.toMutableList()
                val index = blocks.indexOfLast { it is Block.Tool && it.name == event.name && !it.done }
                if (index >= 0) {
                    val tool = blocks[index] as Block.Tool
                    blocks[index] = tool.copy(result = event.result, done = true)
                }
                // 工具跑完还要等模型下一轮，先挂个「正在思考…」，别让界面空着
                msg.copy(blocks = blocks, waiting = true, waitingLabel = "正在思考…", waitingSince = System.currentTimeMillis())
            }

            is ChatEvent.Sticker -> {
                val url = stickerUrl(event.name, event.mood)
                if (url.isNotEmpty()) {
                    queuedStickers.addLast(url)
                    // 正文还没开始（或刚好停在句子边界）就立刻显示
                    flushStickers()
                }
            }

            is ChatEvent.Artifact -> {
                flushStickers(force = true)
                updateLastAssistant { msg ->
                    msg.copy(blocks = settleThinking(msg.blocks) + Block.Artifact(event.id, event.title))
                }
            }

            is ChatEvent.Cut -> updateLastAssistant { msg ->
                msg.copy(
                    blocks = msg.blocks + Block.Notice("回复被中断：${event.reason.ifEmpty { "网络中断" }}")
                )
            }

            is ChatEvent.Compressed -> updateLastAssistant { msg ->
                val before = event.beforeTokens / 1000
                val after = event.afterTokens / 1000
                msg.copy(
                    blocks = msg.blocks + Block.Notice(
                        "聊得太长啦，前面 ${event.dropped} 条旧记录被压成摘要（上下文 ${before}k → ${after}k）"
                    )
                )
            }

            is ChatEvent.Done -> event.context?.let { usage ->
                _state.update { it.copy(context = usage) }
            }

            is ChatEvent.Error -> updateLastAssistant { msg ->
                msg.copy(blocks = msg.blocks + Block.Notice("喵呜…出错了：${event.message}", isError = true))
            }

            is ChatEvent.Unknown -> Unit
        }
    }

    private fun settleThinking(blocks: List<Block>): List<Block> =
        blocks.map { if (it is Block.Thinking && !it.done) it.copy(done = true) else it }

    private fun updateLastAssistant(transform: (UiMessage) -> UiMessage) {
        _state.update { current ->
            val list = current.messages.toMutableList()
            val index = list.indexOfLast { it.role == Role.ASSISTANT }
            if (index < 0) return@update current
            list[index] = transform(list[index])
            current.copy(messages = list)
        }
    }

    private fun stickerUrl(name: String?, mood: String): String {
        val chosen = name?.takeIf { it.isNotBlank() }
            ?: stickerLib[mood]?.randomOrNull()
            ?: return ""
        return "${api.baseUrl}/api/persona/sticker/$chosen"
    }

    private fun report(error: Throwable) {
        val text = if (error is DeckException) error.message else (error.message ?: "操作失败")
        _state.update { it.copy(toast = text) }
    }

    companion object {
        val Factory: ViewModelProvider.Factory = viewModelFactory {
            initializer { ChatViewModel(DeckApp.instance.api) }
        }
    }
}
