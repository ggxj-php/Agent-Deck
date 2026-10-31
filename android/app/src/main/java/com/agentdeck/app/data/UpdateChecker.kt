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
 * 版本检查。两路来源，按顺序试：
 *
 * 1. `{baseUrl}/static/app-version.json` —— 自建分发用。放一个静态 JSON 就行
 *    （格式见 `version.sample.json`），能带更新说明。
 * 2. `{baseUrl}/api/app/latest` —— 后端自带接口，扫 `static/app 目录里的 APK` 找版本号最大的那个。
 *    不需要手写 JSON，但没有自定义说明。
 *
 * 以前只认第 1 种，而仓库里从来没有生成过这个文件，「检查更新」就永远是空的。
 */
object UpdateChecker {

    private val client = okhttp3.OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .build()

    /** 有新版返回 [UpdateInfo]；已是最新、或两个来源都拿不到时返回 null。 */
    suspend fun check(
        baseUrl: String,
        latestApk: (suspend () -> ApkInfo?)? = null,
    ): UpdateInfo? = withContext(Dispatchers.IO) {
        val root = baseUrl.trimEnd('/')
        if (root.isEmpty() || AppSettings.isPlaceholder(root)) return@withContext null

        fromVersionJson(root) ?: fromLatestApk(root, latestApk)
    }

    /** 来源一：静态 app-version.json（能带更新说明）。 */
    private fun fromVersionJson(root: String): UpdateInfo? {
        val json = fetch("$root/static/app-version.json") ?: return null
        val info = runCatching {
            val obj = JSONObject(json)
            UpdateInfo(
                versionCode = obj.optInt("versionCode", 0),
                versionName = obj.optString("versionName"),
                notes = obj.optString("notes"),
                url = obj.optString("url").ifBlank { root },
            )
        }.getOrNull() ?: return null
        return info.takeIf { isNewer(info.versionCode, info.versionName) }
    }

    /** 来源二：`/api/app/latest` 扫 static/app 目录里的 APK。 */
    private suspend fun fromLatestApk(
        root: String,
        latestApk: (suspend () -> ApkInfo?)?,
    ): UpdateInfo? {
        val apk = latestApk?.invoke() ?: fromLatestEndpoint(root) ?: return null
        if (apk.version.isBlank()) return null
        if (!isNewer(0, apk.version)) return null
        val size = if (apk.sizeMb > 0) "（${apk.sizeMb} MB）" else ""
        return UpdateInfo(
            versionCode = 0,
            versionName = apk.version,
            notes = "服务器上有新版本 $size，重新下载安装即可覆盖。",
            url = apk.url.ifBlank { root },
        )
    }

    /** 没有 DeckApi 时的兜底。 */
    private fun fromLatestEndpoint(root: String): ApkInfo? {
        val json = fetch("$root/api/app/latest") ?: return null
        return runCatching {
            val obj = JSONObject(json)
            ApkInfo(
                filename = obj.optString("filename"),
                version = obj.optString("version"),
                url = obj.optString("url").let { if (it.startsWith("http")) it else root + it },
                sizeMb = obj.optDouble("size_mb", 0.0),
            )
        }.getOrNull()
    }

    /**
     * 判断服务器上的版本是不是比当前新。
     *
     * versionCode 有值就以它为准；没有（`/api/app/latest` 只给版本名）就逐段比较版本号，
     * 别用字符串比——字符串会认为 "1.9" > "1.11"。
     */
    private fun isNewer(versionCode: Int, versionName: String): Boolean {
        if (versionCode > 0) return versionCode > BuildConfig.VERSION_CODE
        return compareVersion(versionName, BuildConfig.VERSION_NAME) > 0
    }

    /** 语义化比较：返回正数表示 [a] 更新。缺的段按 0 补。 */
    fun compareVersion(a: String, b: String): Int {
        val left = a.split('.', '-', '+').mapNotNull { it.toIntOrNull() }
        val right = b.split('.', '-', '+').mapNotNull { it.toIntOrNull() }
        val size = maxOf(left.size, right.size)
        for (i in 0 until size) {
            val x = left.getOrElse(i) { 0 }
            val y = right.getOrElse(i) { 0 }
            if (x != y) return x - y
        }
        return 0
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
