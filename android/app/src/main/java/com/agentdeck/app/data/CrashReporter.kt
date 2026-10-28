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

    /** 启动时补传之前没发出去的崩溃日志。 */
    fun uploadPending(context: Context) {
        val dir = File(context.filesDir, DIR)
        val files = dir.listFiles()?.filter { it.name.endsWith(".txt") } ?: return
        if (files.isEmpty()) return
        Thread {
            files.forEach { file ->
                val text = runCatching { file.readText() }.getOrNull() ?: return@forEach
                val ok = runCatching { uploadBlocking(context, text) }.getOrDefault(false)
                if (ok) runCatching { file.delete() }
            }
        }.start()
    }

    /** 上报一条日志，成功返回 true。 */
    suspend fun upload(context: Context, text: String): Boolean = withContext(Dispatchers.IO) {
        runCatching { uploadBlocking(context, text) }.getOrDefault(false)
    }

    private fun uploadBlocking(context: Context, text: String): Boolean {
        val base = ServerConfig.baseUrl(context)
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

/** 后端地址：构建时注入，开源版默认是占位地址。 */
object ServerConfig {
    fun baseUrl(context: Context): String = BuildConfig.BASE_URL.trimEnd('/')
}
