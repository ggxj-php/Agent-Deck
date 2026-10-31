package com.agentdeck.app.data

import android.app.Application
import android.content.Context
import android.content.Intent
import android.os.Build
import com.agentdeck.app.BuildConfig
import com.agentdeck.app.CrashActivity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * 崩溃兜底：不直接闪退，先把堆栈写成本地文件，再拉起 [CrashActivity] 给用户看，
 * 同时尝试上报到 `{BASE_URL}/api/app-crash`；上报失败的留在本地，下次启动继续补传。
 */
object CrashReporter {

    private const val DIR = "crashes"
    private const val ENDPOINT = "/api/app-crash"

    /**
     * 是否把崩溃日志发到服务器。
     *
     * 这个接口不在 FastAPI 里，而是 nginx 单独把 `POST /api/app-crash` 反代到一个
     * 只监听 127.0.0.1 的小服务（`crash_receiver.py`），日志追加进 `crashes/crashes.jsonl`。
     * 所以「后端 28 条路由里找不到它」不代表没有——部署方没配这条反代时会返回 404，
     * 那种情况下 [uploadPending] 会把文件改名成 `.failed` 归档，不会每次启动重传。
     */
    private const val REMOTE_UPLOAD = true

    private val client by lazy {
        OkHttpClient.Builder()
            .connectTimeout(6, TimeUnit.SECONDS)
            .writeTimeout(8, TimeUnit.SECONDS)
            .readTimeout(8, TimeUnit.SECONDS)
            .build()
    }

    fun install(app: Application) {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            runCatching { capture(app, thread, error) }
            // 原生行为（系统弹「应用已停止」）就先不走了，交给崩溃页面
            if (thread.name == "main") {
                runCatching { launchScreen(app, thread, error) }
                // 给 Activity 一点时间起来，再结束这个已经不可用的进程
                Thread {
                    runCatching { Thread.sleep(600) }
                    android.os.Process.killProcess(android.os.Process.myPid())
                    kotlin.system.exitProcess(10)
                }.start()
            } else {
                previous?.uncaughtException(thread, error)
            }
        }
    }

    /** 把堆栈写成本地文件，返回文件。 */
    fun capture(context: Context, thread: Thread, error: Throwable): File {
        val text = buildLog(thread, error)
        val stamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())
        val dir = File(context.filesDir, DIR).apply { mkdirs() }
        val file = File(dir, "crash-$stamp.txt")
        runCatching { file.writeText(text) }
        // 只留最近 20 份，别把用户存储塞满
        runCatching {
            dir.listFiles()?.sortedByDescending { it.lastModified() }?.drop(20)?.forEach { it.delete() }
        }
        return file
    }

    fun buildLog(thread: Thread, error: Throwable): String = buildString {
        appendLine("time: " + SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date()))
        appendLine("app: ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})")
        appendLine("device: ${Build.MANUFACTURER} ${Build.MODEL}")
        appendLine("android: ${Build.VERSION.RELEASE} (sdk ${Build.VERSION.SDK_INT})")
        appendLine("thread: ${thread.name}")
        appendLine("exception: ${error.javaClass.name}: ${error.message}")
        appendLine()
        appendLine(error.stackTraceToString())
    }

    private fun launchScreen(context: Context, thread: Thread, error: Throwable) {
        val intent = Intent(context, CrashActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
            putExtra(CrashActivity.EXTRA_MESSAGE, "${error.javaClass.name}: ${error.message}")
            putExtra(CrashActivity.EXTRA_LOG, buildLog(thread, error))
        }
        context.startActivity(intent)
    }

    /**
     * 启动时补传之前没发出去的崩溃日志。
     *
     * 传不上去的直接改名成 `.failed` 归档，不再每启动一次试一遍——
     * 以前服务端没有 /api/app-crash，这里会变成「永远在重传」的死循环。
     */
    fun uploadPending(context: Context) {
        if (!REMOTE_UPLOAD) return
        val dir = File(context.filesDir, DIR)
        val files = dir.listFiles()?.filter { it.name.endsWith(".txt") } ?: return
        if (files.isEmpty()) return
        Thread {
            files.forEach { file ->
                val text = runCatching { file.readText() }.getOrNull() ?: return@forEach
                val ok = runCatching { uploadBlocking(context, text) }.getOrDefault(false)
                if (ok) {
                    runCatching { file.delete() }
                } else {
                    runCatching { file.renameTo(File(file.parentFile, file.name + ".failed")) }
                }
            }
        }.start()
    }

    /** 远端上报是否打开（崩溃页用它决定文案）。 */
    fun remoteEnabled(): Boolean = REMOTE_UPLOAD

    /** 上报一条日志，成功返回 true。远端没开就直接返回 false，日志留在本地。 */
    suspend fun upload(context: Context, text: String): Boolean = withContext(Dispatchers.IO) {
        if (!REMOTE_UPLOAD) return@withContext false
        runCatching { uploadBlocking(context, text) }.getOrDefault(false)
    }

    private fun uploadBlocking(context: Context, text: String): Boolean {
        val base = AppSettings.baseUrl(context).trimEnd('/')
        if (base.isEmpty()) return false
        val body = JSONObject()
            .put("app", "agentdeck")
            .put("version", BuildConfig.VERSION_NAME)
            .put("versionCode", BuildConfig.VERSION_CODE)
            .put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
            .put("android", "${Build.VERSION.RELEASE} (sdk ${Build.VERSION.SDK_INT})")
            .put("log", text)
            .toString()
        val request = Request.Builder()
            .url(base + ENDPOINT)
            .post(body.toRequestBody("application/json; charset=utf-8".toMediaType()))
            .header("User-Agent", "AgentDeck-Android/${BuildConfig.VERSION_NAME}")
            .build()
        return client.newCall(request).execute().use { it.isSuccessful }
    }
}
