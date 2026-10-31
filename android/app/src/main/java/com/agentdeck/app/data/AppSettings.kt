package com.agentdeck.app.data

import android.content.Context
import com.agentdeck.app.BuildConfig

/**
 * 本机设置。目前只有一项：后端地址。
 *
 * 仓库里**不放任何真实服务器地址**——构建时注入的 [BuildConfig.BASE_URL] 只是默认值
 * （开源版是 `https://example.com`，属于「未配置」状态），用户在登录页填一次就存在
 * SharedPreferences 里，换服务器不用重新打安装包。
 *
 * 这里刻意用普通 SharedPreferences 而不是 [SecureStore]：服务器地址不是机密，
 * 而且崩溃日志、图片加载都要在拿到登录态之前读它。
 */
object AppSettings {

    private const val FILE = "deck_settings"
    private const val KEY_BASE_URL = "base_url"

    /**
     * 这些字样出现在地址里就当成「还没配」，登录页会把输入框摆出来。
     *
     * 只认模板里的假域名：`localhost` / `127.0.0.1` 是开发者真会用的地址，
     * 把它们算成占位的话，本机连一个后端每次启动都会被要求重填一遍。
     */
    private val PLACEHOLDERS = listOf("example.com", "your-server")

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    /** 构建时注入的地址（没注入就是占位地址）。 */
    private val builtin: String
        get() = BuildConfig.BASE_URL.trim().trimEnd('/')

    /** 用户自己填的地址；没填过是空串。 */
    fun saved(context: Context): String =
        prefs(context).getString(KEY_BASE_URL, "").orEmpty().trim().trimEnd('/')

    /** 当前生效地址：用户填过优先，否则用构建注入的。 */
    fun baseUrl(context: Context): String =
        saved(context).ifEmpty { builtin }

    /** 还没配置过：地址为空、或是占位地址。 */
    fun needsServer(context: Context): Boolean = isPlaceholder(baseUrl(context))

    fun isPlaceholder(url: String): Boolean {
        val value = url.trim()
        if (value.isEmpty()) return true
        return PLACEHOLDERS.any { value.contains(it, ignoreCase = true) }
    }

    /**
     * 存下来。顺手做个规范化：只填 `1.2.3.4:8000` 这种就补 `http://`，
     * 填了域名就补 `https://`，末尾斜杠去掉。
     *
     * @return 规范化之后的地址
     */
    fun setBaseUrl(context: Context, raw: String): String {
        val normalized = normalize(raw)
        prefs(context).edit().putString(KEY_BASE_URL, normalized).apply()
        return normalized
    }

    fun clear(context: Context) {
        prefs(context).edit().remove(KEY_BASE_URL).apply()
    }

    /** 把用户随手输的地址补成完整 URL。 */
    fun normalize(raw: String): String {
        var url = raw.trim()
        if (url.isEmpty()) return ""
        if (!url.startsWith("http://", true) && !url.startsWith("https://", true)) {
            // 纯 IP / 带端口 / localhost 走 http，其余走 https
            val host = url.substringBefore('/')
            val plain = host.startsWith("localhost") || host.startsWith("127.") ||
                host.startsWith("10.") || host.startsWith("192.168.") ||
                Regex("^172\\.(1[6-9]|2\\d|3[01])\\.").containsMatchIn(host) ||
                Regex("^\\d{1,3}(\\.\\d{1,3}){3}(:\\d+)?$").containsMatchIn(host)
            url = (if (plain) "http://" else "https://") + url
        }
        return url.trimEnd('/')
    }
}
