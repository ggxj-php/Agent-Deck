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
            // 还没配服务器（开源版默认是占位地址）：先把登录页放出来让用户填，
            // 不然所有请求都打到 example.com，只能看到一句莫名其妙的网络错误。
            if (api.needsServer) {
                _state.update {
                    it.copy(
                        booting = false,
                        account = null,
                        needsServer = true,
                        serverUrl = api.baseUrl,
                    )
                }
                return@launch
            }

            // 人设和表情包都是公开接口，先拿下来，登录页也能显示名字和欢迎语
            val persona = runCatching { api.persona() }.getOrDefault(Persona())
            stickerLib = runCatching { api.stickers() }.getOrDefault(emptyMap())
            _state.update { it.copy(persona = persona, serverUrl = api.baseUrl, needsServer = false) }

            try {
                val info = api.me()
                val sessions = runCatching { api.sessions() }.getOrDefault(emptyList())
                api.syncWebViewCookie()
                _state.update {
                    it.copy(
                        booting = false,
                        account = info.account,
                        accountSessions = info.sessions,
                        sessions = sessions,
                        currentSessionId = sessions.firstOrNull()?.id,
                        serverUrl = api.baseUrl,
                        needsServer = false,
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

    /**
     * 换服务器：存下新地址、清掉旧的登录态，然后重新走一遍启动流程。
     * 登录页那个「服务器地址」输入框和用户中心的「切换服务器」都走这里。
     */
    fun setServerUrl(raw: String) {
        val saved = api.saveServerUrl(raw)
        if (saved.isEmpty()) {
            _state.update { it.copy(loginError = "服务器地址不能为空") }
            return
        }
        viewModelScope.launch {
            runCatching { api.logout() }
            api.syncWebViewCookie()
            _state.value = ChatUiState(
                booting = true,
                persona = _state.value.persona,
                serverUrl = saved,
                needsServer = false,
                thinking = _state.value.thinking,
            )
            boot()
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
        val info = api.me()
        val persona = runCatching { api.persona() }.getOrDefault(Persona())
        val sessions = runCatching { api.sessions() }.getOrDefault(emptyList())
        stickerLib = runCatching { api.stickers() }.getOrDefault(emptyMap())
        api.syncWebViewCookie()
        _state.update {
            it.copy(
                loggingIn = false,
                account = info.account,
                accountSessions = info.sessions,
                persona = persona,
                sessions = sessions,
                currentSessionId = null,
                messages = emptyList(),
                context = ContextUsage(),
                captcha = null,
                authMode = AuthMode.LOGIN,
                serverUrl = api.baseUrl,
                needsServer = false,
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
                ChatUiState(
                    booting = false,
                    persona = it.persona,
                    serverUrl = api.baseUrl,
                    // 已经配过的服务器不该再让用户填一遍，退出登录 ≠ 忘掉地址
                    needsServer = api.needsServer,
                    thinking = it.thinking,
                )
            }
        }
    }

    /** 重新拉一次 `/api/auth/me`：用户中心打开时刷一下会话数和注册时间。 */
    fun refreshAccount() {
        viewModelScope.launch {
            runCatching { api.me() }.onSuccess { info ->
                _state.update { it.copy(account = info.account, accountSessions = info.sessions) }
            }
        }
    }

    // ---------------- 版本检查 ----------------

    fun checkForUpdate(manual: Boolean = false) {
        viewModelScope.launch {
            val info = runCatching { UpdateChecker.check(api.baseUrl) { api.latestApk() } }.getOrNull()
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

    /**
     * 重发一条用户消息。
     *
     * 图片只有还在内存里那种（data URI）才带得上——历史记录里的图是服务器路径，
     * 后端只认 data URL，硬塞会整条被拒，所以这里直接跳过。
     */
    fun resend(message: UiMessage) {
        if (_state.value.busy) {
            _state.update { it.copy(toast = "上一条还在回，等它说完再重发") }
            return
        }
        val text = message.blocks
            .filterIsInstance<Block.Text>()
            .joinToString("\n\n") { it.markdown }
            .trim()
        val images = message.images.filter { it.startsWith("data:") }
        if (text.isEmpty() && images.isEmpty() && message.files.isEmpty()) {
            _state.update { it.copy(toast = "这条消息没有能重发的内容") }
            return
        }
        send(text = text, images = images, files = message.files)
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
        // 这一轮结束了：先把攒着的正文落盘，再放掉排队的表情包
        flushContent()
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

    /** 流式正文先攒 60ms 再合并成一次更新，别让每个 token 都触发一次整段重排。 */
    private val pendingContent = StringBuilder()
    private var contentJob: kotlinx.coroutines.Job? = null

    private fun appendContentChunk(text: String) {
        pendingContent.append(text)
        if (contentJob?.isActive == true) return
        contentJob = viewModelScope.launch {
            kotlinx.coroutines.delay(60)
            flushContent()
        }
    }

    private fun flushContent() {
        if (pendingContent.isEmpty()) return
        val text = pendingContent.toString()
        pendingContent.setLength(0)
        updateLastAssistant { msg ->
            val blocks = settleThinking(msg.blocks).toMutableList()
            val last = blocks.lastOrNull()
            if (last is Block.Text) {
                blocks[blocks.size - 1] = last.copy(markdown = last.markdown + text)
            } else {
                blocks.add(Block.Text(text))
            }
            msg.copy(blocks = blocks)
        }
        flushStickers()
    }

    /**
     * 排队中的表情包标记（`[sticker:心情:文件名]`），和网页端的 queuedStickers 一样：
     * 先收着，等正文出现句末标点/换行再插，避免把一句话从中间劈开。
     *
     * 存标记而不是图片地址，是为了让它**留在正文里**——这样渲染时还能统一往后挪
     * 到句子边界。之前它是作为独立块追加到消息末尾的，位置一旦定下就再也动不了，
     * 落在括号中间也只能认了。
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
            while (queuedStickers.isNotEmpty()) appendStickerMark(queuedStickers.removeFirst())
        } else {
            appendStickerMark(queuedStickers.removeFirst())
        }
    }

    /**
     * 把表情包标记接在正文末尾（和网页端的 `contentChunk('@@sticker:0@@')` 一个意思）。
     *
     * 接在正文里而不是新起一个块：渲染时会统一做一次 snapStickersToBoundaries，
     * 万一标记落在句子/括号中间，还能被挪到真正的句末去。
     */
    private fun appendStickerMark(mark: String) {
        if (mark.isBlank()) return
        updateLastAssistant { msg ->
            val blocks = settleThinking(msg.blocks).toMutableList()
            val last = blocks.lastOrNull()
            if (last is Block.Text) {
                blocks[blocks.size - 1] = last.copy(markdown = last.markdown + mark)
            } else {
                blocks.add(Block.Text(mark))
            }
            msg.copy(blocks = blocks)
        }
    }

    /**
     * 用户点了选择卡：把这张卡标记成已选，然后照常发一条「我选择：X」。
     * 服务端会接着原来的任务跑下一轮（网页端也是这么做的）。
     */
    fun pickAsk(messageId: Long, blockIndex: Int, option: String) {
        val text = option.trim()
        if (text.isEmpty()) return
        _state.update { current ->
            current.copy(
                messages = current.messages.map { message ->
                    if (message.id != messageId) {
                        message
                    } else {
                        message.copy(
                            blocks = message.blocks.mapIndexed { index, block ->
                                if (index == blockIndex && block is Block.Ask && block.chosen == null) {
                                    block.copy(chosen = text)
                                } else {
                                    block
                                }
                            },
                        )
                    }
                },
            )
        }
        send("我选择：$text")
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
        // 正文是攒着批量刷的（和网页端 RENDER_MIN_MS 一个意思），
        // 其它事件到来前先落盘，避免工具卡片/表情包插到文字前面
        if (event !is ChatEvent.Content) flushContent()

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
                appendContentChunk(event.text)
            }

            is ChatEvent.ToolPending -> setWaiting("正在准备 ${event.name}…")

            is ChatEvent.ToolStart -> {
                // send_sticker 不用画工具卡片：紧接着的 sticker 事件就是它的样子。
                // 网页端 handleEvent 里也有这一句，不然每条表情包会多一张空卡片。
                if (event.name == "send_sticker") return
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
                // 文件名在这一刻就定死（随机挑的那张），刷新后还是同一张
                val file = stickerFilename(event.name, event.mood)
                if (file.isNotEmpty()) {
                    queuedStickers.addLast("[sticker:" + event.mood + ":" + file + "]")
                    // 正文还没开始（或刚好停在句子边界）就立刻显示
                    flushStickers()
                }
            }

            is ChatEvent.AwaitUser -> updateLastAssistant { msg ->
                msg.copy(
                    blocks = settleThinking(msg.blocks) + Block.Ask(
                        question = event.question.ifBlank { "要我怎么做？" },
                        options = event.options,
                        allowCustom = event.allowCustom,
                    ),
                )
            }

            is ChatEvent.Artifact -> {
                flushStickers(force = true)
                updateLastAssistant { msg ->
                    msg.copy(blocks = settleThinking(msg.blocks) + Block.Artifact(event.id, event.title))
                }
            }

            is ChatEvent.Cut -> updateLastAssistant { msg ->
                msg.copy(
                    blocks = msg.blocks + Block.Notice(
                        text = "回复被中断：${event.reason.ifEmpty { "网络中断" }}",
                        // 截断/断流都能靠「继续」接着讲，和网页端 noticeBubble(..., true) 一致
                        canContinue = true,
                    )
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

            // 限速排队 / 工具熔断 / 工具组被关掉：网页端会画一条灰色提示，App 以前直接丢了
            is ChatEvent.Notice -> updateLastAssistant { msg ->
                msg.copy(blocks = msg.blocks + Block.Notice(event.text))
            }

            is ChatEvent.Done -> event.context?.let { usage ->
                _state.update { it.copy(context = usage) }
            }

            is ChatEvent.Error -> updateLastAssistant { msg ->
                msg.copy(
                    blocks = msg.blocks + Block.Notice(
                        text = "喵呜…出错了：${event.message}",
                        isError = true,
                        // 报错后大多能靠「继续」重试一次，网页端 errorBubble(turn, msg, true)
                        canContinue = true,
                    )
                )
            }

            is ChatEvent.Unknown -> Unit
        }
    }

    /**
     * 从后台切回前台：探一次活。
     *
     * 手机切走几分钟，nginx / 服务重启 / 换网都会让原来的连接失效；
     * 回来先 ping 一下，失败就明确告诉用户，而不是等他发消息才看到报错
     * （网页端 `resume()` 做的是同一件事）。
     */
    fun onAppResume() {
        if (_state.value.account == null || _state.value.busy) return
        viewModelScope.launch {
            val ok = api.ping()
            if (!ok) {
                _state.update { it.copy(toast = "连不上服务器，检查一下网络或服务器地址") }
            }
        }
    }

    /** 点了提示条下面的「继续」：照常发一条「继续」，让模型接着上一轮说下去。 */
    fun continueGeneration() {
        if (_state.value.busy) return
        send("继续")
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
        val chosen = stickerFilename(name, mood)
        return if (chosen.isEmpty()) "" else "${api.baseUrl}/api/persona/sticker/$chosen"
    }

    /** 挑一张表情包：优先用后端定好的文件名，否则按心情随机（刷新前后保持一致）。 */
    private fun stickerFilename(name: String?, mood: String): String =
        name?.takeIf { it.isNotBlank() } ?: stickerLib[mood]?.randomOrNull().orEmpty()

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
