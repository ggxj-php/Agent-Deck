package icu.taffy.deck.data

import icu.taffy.deck.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** 后端明确返回的错误（带 HTTP 状态码），UI 直接把 message 摆出来就行。 */
class DeckException(val code: Int, message: String) : IOException(message)

/**
 * 与 Agent Deck 后端通信。
 *
 * - 登录态走 HttpOnly cookie，由 [PersistentCookieJar] 加密持久化；
 * - 聊天是 SSE：边读边回调，用 [cancelChat] 可以中止；
 * - 所有请求都只发往 [baseUrl]，跟着 302 跳别的域会直接失败。
 */
class DeckApi(context: android.content.Context) {

    val baseUrl: String = BuildConfig.BASE_URL.trimEnd('/')
    private val baseHttpUrl = baseUrl.toHttpUrlOrNull()
        ?: throw IllegalStateException("BASE_URL 不是合法地址：$baseUrl")

    val cookieJar = PersistentCookieJar(SecureStore(context.applicationContext))

    private val client: OkHttpClient = OkHttpClient.Builder()
        .cookieJar(cookieJar)
        .connectTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)   // 流式回复的静默上限，和网页端一致
        .followRedirects(true)
        .followSslRedirects(true)
        .retryOnConnectionFailure(true)
        .build()

    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    @Volatile
    private var chatCall: Call? = null

    // ---------------- 账号 ----------------

    suspend fun login(name: String, password: String): Account {
        val body = JSONObject().put("name", name).put("password", password).toString()
        val text = call("POST", "/api/auth/login", body)
        val user = JSONObject(text).optJSONObject("user")
        return parseAccount(user)
    }

    suspend fun me(): Account {
        val obj = JSONObject(call("GET", "/api/auth/me", null))
        return parseAccount(obj.optJSONObject("user"))
    }

    /** 注册用图形验证码：后端返回 {id, image(data URL), ttl}。 */
    suspend fun captcha(): Captcha {
        val obj = JSONObject(call("GET", "/api/auth/captcha", null))
        return Captcha(
            id = obj.optString("id"),
            image = obj.optString("image"),
            ttl = obj.optInt("ttl", 300),
        )
    }

    /** 邀请制注册：用户名 + 密码 + 邀请码 + 图形验证码，成功即登录（后端会下发 cookie）。 */
    suspend fun register(
        name: String,
        password: String,
        invite: String,
        captchaId: String,
        captchaAnswer: String,
    ): Account {
        val body = JSONObject()
            .put("name", name)
            .put("password", password)
            .put("invite", invite)
            .put("captcha_id", captchaId)
            .put("captcha", captchaAnswer)
            .toString()
        val text = call("POST", "/api/auth/register", body)
        return parseAccount(JSONObject(text).optJSONObject("user"))
    }

    suspend fun logout() {
        runCatching { call("POST", "/api/auth/logout", "{}") }
        cookieJar.clear()
    }

    /** 改密码：要先验旧密码，成功后会重新回到登录页。 */
    suspend fun changePassword(oldPassword: String, newPassword: String) {
        val body = JSONObject()
            .put("old_password", oldPassword)
            .put("new_password", newPassword)
            .toString()
        call("POST", "/api/auth/password", body)
    }

    // ---------------- 人设 / 会话 ----------------

    /** 上传附件：base64 data URL，服务端限 20MB / 最多 6 个 / 只收常见文档与源码扩展名。 */
    suspend fun uploadFile(name: String, dataUrl: String): UploadedFile {
        val body = JSONObject().put("name", name).put("data_url", dataUrl).toString()
        val text = call("POST", "/api/uploads/file", body)
        val file = JSONObject(text).optJSONObject("file")
            ?: throw DeckException(500, "上传失败：服务端没有返回文件信息")
        return UploadedFile(
            id = file.optString("id"),
            name = file.optString("name", name),
            size = file.optLong("size"),
        )
    }

    suspend fun persona(): Persona {
        val obj = JSONObject(call("GET", "/api/persona", null))
        return Persona(
            id = obj.optString("id"),
            name = obj.optString("name", "Agent"),
            selfName = obj.optString("self_name"),
            userName = obj.optString("user_name"),
            avatar = obj.optString("avatar"),
            welcome = obj.optString("welcome"),
            placeholder = obj.optString("placeholder"),
            statusOnline = obj.optString("status_online"),
            statusThinking = obj.optString("status_thinking"),
            theme = obj.optString("theme", "#ffb7d5"),
        )
    }

    suspend fun sessions(): List<SessionSummary> {
        val obj = JSONObject(call("GET", "/api/sessions", null))
        val array = obj.optJSONArray("sessions") ?: JSONArray()
        return (0 until array.length()).mapNotNull { index ->
            val item = array.optJSONObject(index) ?: return@mapNotNull null
            SessionSummary(
                id = item.optString("id"),
                title = item.optString("title", "新会话"),
                messages = item.optInt("messages"),
                updatedAt = item.optDouble("updated_at"),
            )
        }
    }

    suspend fun createSession(title: String = ""): SessionSummary {
        val body = JSONObject().put("title", title).toString()
        val obj = JSONObject(call("POST", "/api/sessions", body))
        val item = obj.optJSONObject("session") ?: throw DeckException(500, "服务端没返回新会话")
        return SessionSummary(
            id = item.optString("id"),
            title = item.optString("title", "新会话"),
            messages = item.optInt("messages"),
            updatedAt = item.optDouble("updated_at"),
        )
    }

    suspend fun deleteSession(id: String) {
        call("DELETE", "/api/sessions/$id", null)
    }

    suspend fun renameSession(id: String, title: String) {
        val body = JSONObject().put("title", title).toString()
        call("PATCH", "/api/sessions/$id", body)
    }

    suspend fun history(id: String): HistoryPage {
        val obj = JSONObject(call("GET", "/api/sessions/$id/messages", null))
        val session = obj.optJSONObject("session")
        val array = obj.optJSONArray("history") ?: JSONArray()
        val items = (0 until array.length()).mapNotNull { index ->
            val item = array.optJSONObject(index) ?: return@mapNotNull null
            val text = item.optString("text")
            if (text.isEmpty()) null else HistoryItem(item.optString("role"), text)
        }
        return HistoryPage(
            sessionId = session?.optString("id").orEmpty().ifEmpty { id },
            title = session?.optString("title").orEmpty(),
            history = items,
            context = parseContext(obj.optJSONObject("context")),
        )
    }

    suspend fun contextUsage(sessionId: String): ContextUsage {
        val obj = JSONObject(call("GET", "/api/context?session_id=$sessionId", null))
        return parseContext(obj)
    }

    /** 表情包清单：{心情: [文件名, ...]}。 */
    suspend fun stickers(): Map<String, List<String>> {
        val obj = JSONObject(call("GET", "/api/stickers", null))
        val out = LinkedHashMap<String, List<String>>()
        for (mood in obj.keys()) {
            val array = obj.optJSONArray(mood) ?: continue
            val names = (0 until array.length()).mapNotNull { index ->
                array.optString(index).takeIf { it.isNotBlank() }
            }
            if (names.isNotEmpty()) out[mood] = names
        }
        return out
    }

    // ---------------- 聊天（SSE） ----------------

    /**
     * 发一条消息，把事件一条条回调出来。函数返回即代表这条流结束（正常或异常）。
     * [onEvent] 在 IO 线程上被调用，切回主线程是 ViewModel 的事。
     */
    suspend fun streamChat(
        sessionId: String,
        message: String,
        images: List<String> = emptyList(),
        thinking: Boolean = true,
        files: List<String> = emptyList(),
        onEvent: (ChatEvent) -> Unit,
    ) = withContext(Dispatchers.IO) {
        val payload = JSONObject()
            .put("session_id", sessionId)
            .put("message", message)
            .put("thinking", thinking)
        if (images.isNotEmpty()) {
            val array = JSONArray()
            images.forEach { array.put(it) }
            payload.put("images", array)
        }
        if (files.isNotEmpty()) {
            val array = JSONArray()
            files.forEach { id -> array.put(JSONObject().put("id", id)) }
            payload.put("files", array)
        }

        val request = Request.Builder()
            .url("$baseUrl/api/chat")
            .header("Accept", "text/event-stream")
            .header("User-Agent", USER_AGENT)
            .post(payload.toString().toRequestBody(jsonMedia))
            .build()

        val call = client.newCall(request)
        chatCall = call
        try {
            call.execute().use { response ->
                if (!response.isSuccessful) throw errorFrom(response)
                val source = response.body?.source() ?: throw DeckException(500, "服务端没有返回内容")
                while (true) {
                    val line = source.readUtf8Line() ?: break
                    if (!line.startsWith("data:")) continue
                    val data = line.substring(5).trim()
                    if (data == "[DONE]") break
                    val event = parseEvent(data) ?: continue
                    onEvent(event)
                }
            }
        } finally {
            chatCall = null
        }
    }

    fun cancelChat() {
        chatCall?.cancel()
    }

    /** 把 SSO cookie 同步给 WebView，让正文里的 /api/uploads/xxx 图片也能加载。 */
    fun syncWebViewCookie() {
        val cookie = cookieJar.cookieHeader(baseHttpUrl) ?: return
        runCatching {
            android.webkit.CookieManager.getInstance().setCookie(baseUrl, cookie)
            android.webkit.CookieManager.getInstance().flush()
        }
    }

    // ---------------- 内部 ----------------

    private suspend fun call(method: String, path: String, body: String?): String =
        withContext(Dispatchers.IO) {
            val builder = Request.Builder()
                .url(baseUrl + path)
                .header("Accept", "application/json")
                .header("User-Agent", USER_AGENT)
            when (method) {
                "GET" -> builder.get()
                "DELETE" -> builder.delete()
                "PATCH" -> builder.patch((body ?: "{}").toRequestBody(jsonMedia))
                else -> builder.post((body ?: "{}").toRequestBody(jsonMedia))
            }
            client.newCall(builder.build()).execute().use { response ->
                if (!response.isSuccessful) throw errorFrom(response)
                response.body?.string().orEmpty()
            }
        }

    private fun errorFrom(response: Response): DeckException {
        val raw = runCatching { response.body?.string() }.getOrNull().orEmpty()
        var detail = ""
        var banned = false
        val obj = runCatching { JSONObject(raw) }.getOrNull()
        if (obj != null) {
            detail = obj.optString("detail")
            banned = obj.optBoolean("banned")
        }
        val message = when {
            detail.isNotEmpty() -> detail
            response.code == 401 -> "登录已失效，请重新登录"
            response.code == 403 && banned -> "账号被封禁，请联系管理员"
            response.code == 429 -> "操作太频繁，稍后再试"
            else -> "服务返回 ${response.code}"
        }
        return DeckException(response.code, message)
    }

    private fun parseAccount(user: JSONObject?): Account {
        if (user == null) return Account()
        return Account(
            id = user.optLong("id"),
            name = user.optString("name"),
            inviteCode = user.optString("invite_code"),
            isAdmin = user.optBoolean("is_admin"),
            banned = user.optBoolean("banned"),
        )
    }

    private fun parseContext(obj: JSONObject?): ContextUsage {
        if (obj == null) return ContextUsage()
        // /api/sessions/{id}/messages 里的 context 不带 known 字段，有 limit 就算已知
        val known = if (obj.has("known")) obj.optBoolean("known") else obj.optInt("limit") > 0
        return ContextUsage(
            known = known,
            tokens = obj.optInt("tokens"),
            limit = obj.optInt("limit"),
            percent = obj.optDouble("percent", 0.0),
            messages = obj.optInt("messages"),
        )
    }

    /** 解析一条 SSE 的 data 段；坏 JSON 直接丢掉（和网页端行为一致）。 */
    private fun parseEvent(data: String): ChatEvent? {
        val obj = try {
            JSONObject(data)
        } catch (error: JSONException) {
            return null
        }
        return when (obj.optString("type")) {
            "thinking" -> ChatEvent.Thinking(obj.optString("text"))
            "content" -> ChatEvent.Content(obj.optString("text"))
            "tool_pending" -> ChatEvent.ToolPending(obj.optString("name"))
            "tool_start" -> ChatEvent.ToolStart(
                name = obj.optString("name"),
                arguments = obj.optString("arguments"),
            )
            "tool_end" -> ChatEvent.ToolEnd(
                name = obj.optString("name"),
                result = obj.optString("result"),
            )
            "sticker" -> ChatEvent.Sticker(
                mood = obj.optString("mood"),
                name = obj.optString("name"),
            )
            "artifact" -> ChatEvent.Artifact(
                id = obj.optString("id"),
                title = obj.optString("title"),
            )
            "cut" -> ChatEvent.Cut(obj.optString("reason"))
            "compressed" -> ChatEvent.Compressed(
                dropped = obj.optInt("dropped"),
                beforeTokens = obj.optInt("before_tokens"),
                afterTokens = obj.optInt("after_tokens"),
            )
            "done" -> ChatEvent.Done(parseContext(obj.optJSONObject("context")))
            "error" -> ChatEvent.Error(obj.optString("message"))
            else -> ChatEvent.Unknown(obj.optString("type"))
        }
    }

    private companion object {
        const val USER_AGENT = "AgentDeck-Android/1.0"
    }
}
