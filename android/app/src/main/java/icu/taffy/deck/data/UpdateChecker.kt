package icu.taffy.deck.data

import icu.taffy.deck.BuildConfig
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
 * 版本检查：默认读 Gitee（仓库公开，匿名可取），失败再退回 GitHub。
 *
 * 版本清单是仓库里的 `android/version.json`，发版时改它即可，不需要动服务器。
 */
object UpdateChecker {

    /** Gitee 的 raw 地址（默认源）。 */
    private const val GITEE_JSON =
        "https://gitee.com/your-org/Agent-Deck/raw/feat/android-app/android/version.json"

    /** GitHub 兜底。 */
    private const val GITHUB_JSON =
        "https://raw.githubusercontent.com/your-org/Agent-Deck/feat/android-app/android/version.json"

    const val GITEE_RELEASES = "https://gitee.com/your-org/Agent-Deck/releases"
    const val GITHUB_RELEASES = "https://github.com/your-org/Agent-Deck/releases"

    private val client = okhttp3.OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .build()

    /** 有新版返回 [UpdateInfo]，已是最新/检查失败返回 null。 */
    suspend fun check(): UpdateInfo? = withContext(Dispatchers.IO) {
        val json = fetch(GITEE_JSON) ?: fetch(GITHUB_JSON) ?: return@withContext null
        val info = runCatching {
            val obj = JSONObject(json)
            UpdateInfo(
                versionCode = obj.optInt("versionCode", 0),
                versionName = obj.optString("versionName"),
                notes = obj.optString("notes"),
                url = obj.optString("url").ifBlank { GITEE_RELEASES },
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
