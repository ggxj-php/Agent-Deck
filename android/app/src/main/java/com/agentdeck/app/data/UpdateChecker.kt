package com.agentdeck.app.data

import com.agentdeck.app.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** 一条更新信息。 */
data class UpdateInfo(
    val versionCode: Int,
    val versionName: String,
    val notes: String,
    val url: String,
)

/**
 * 版本检查：直接问当前后端要 `{baseUrl}/static/app-version.json`。
 *
 * 部署方只需要在服务器放一个静态 JSON（格式见仓库根目录的 `version.sample.json`），
 * 里面写最新版本号和下载地址；换服务器时这个地址跟着变，不需要改代码。
 */
object UpdateChecker {

    private val client = okhttp3.OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .build()

    /** 有新版返回 [UpdateInfo]；已是最新、或服务器没放这个文件时返回 null。 */
    suspend fun check(baseUrl: String): UpdateInfo? = withContext(Dispatchers.IO) {
        val root = baseUrl.trimEnd('/')
        val json = fetch("$root/static/app-version.json") ?: return@withContext null
        val info = runCatching {
            val obj = JSONObject(json)
            UpdateInfo(
                versionCode = obj.optInt("versionCode", 0),
                versionName = obj.optString("versionName"),
                notes = obj.optString("notes"),
                url = obj.optString("url").ifBlank { root },
            )
        }.getOrNull() ?: return@withContext null

        if (info.versionCode > BuildConfig.VERSION_CODE) info else null
    }

    private fun fetch(url: String): String? = runCatching {
        val request = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .header("User-Agent", "AgentDeck-Android/${BuildConfig.VERSION_NAME}")
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) return null
            response.body?.string()?.takeIf { it.trimStart().startsWith("{") }
        }
    }.getOrNull()
}
