package icu.taffy.deck.ui.components

import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.DeleteOutline
import androidx.compose.material.icons.rounded.Download
import androidx.compose.material.icons.rounded.OpenInNew
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import icu.taffy.deck.DeckApp
import icu.taffy.deck.ui.theme.DeckPalette
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import okhttp3.Call
import okhttp3.Request
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.util.concurrent.ConcurrentHashMap

/* ---------------- 数据 ---------------- */

enum class DownloadStatus { RUNNING, DONE, FAILED, CANCELED }

data class DownloadTask(
    val id: Long,
    val name: String,
    val url: String,
    val status: DownloadStatus = DownloadStatus.RUNNING,
    val bytes: Long = 0L,
    val total: Long = -1L,
    val speed: Long = 0L,
    val uri: Uri? = null,
    val error: String? = null,
    val createdAt: Long = System.currentTimeMillis(),
)

/**
 * 下载中心：应用级单例，任务列表用 StateFlow 暴露给界面。
 *
 * 下载在 IO 线程跑，边下边算速度（每 ~600ms 采样一次），支持取消；
 * 完成后落盘到「下载/AgentDeck」。
 */
object DownloadCenter {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _tasks = MutableStateFlow<List<DownloadTask>>(emptyList())
    val tasks: StateFlow<List<DownloadTask>> = _tasks.asStateFlow()

    private val calls = ConcurrentHashMap<Long, Call>()
    private var nextId = 1L

    fun start(url: String, fallbackName: String): Long {
        val id = synchronized(this) { nextId++ }
        val task = DownloadTask(id = id, name = fallbackName, url = url)
        update { it + task }

        scope.launch {
            val request = Request.Builder().url(url).header("Accept", "*/*").build()
            val call = DeckApp.instance.api.httpClient.newCall(request)
            calls[id] = call
            try {
                call.execute().use { response ->
                    if (!response.isSuccessful) {
                        fail(id, "服务返回 ${response.code}")
                        return@launch
                    }
                    val body = response.body ?: run { fail(id, "服务端没有返回内容"); return@launch }
                    val total = body.contentLength()
                    val mime = response.header("Content-Type")?.substringBefore(';')?.trim()
                        ?: "application/octet-stream"
                    val name = parseFilename(response.header("Content-Disposition")) ?: fallbackName
                    update { list ->
                        list.map { if (it.id == id) it.copy(name = name, total = total) else it }
                    }
                    val uri = DownloadSaver.save(
                        DeckApp.instance,
                        name,
                        mime,
                        body.byteStream(),
                        total,
                    ) { bytes, speed ->
                        update { list ->
                            list.map {
                                if (it.id == id) it.copy(bytes = bytes, speed = speed, total = if (total > 0) total else it.total)
                                else it
                            }
                        }
                    }
                    update { list ->
                        list.map {
                            if (it.id == id) it.copy(
                                status = DownloadStatus.DONE,
                                bytes = if (total > 0) total else it.bytes,
                                speed = 0,
                                uri = uri,
                                error = if (uri == null) "保存失败（没有写入权限？）" else null,
                            ) else it
                        }
                    }
                }
            } catch (error: Exception) {
                if (calls[id]?.isCanceled() == true) {
                    update { list -> list.map { if (it.id == id) it.copy(status = DownloadStatus.CANCELED, speed = 0) else it } }
                } else {
                    fail(id, error.message ?: "下载失败")
                }
            } finally {
                calls.remove(id)
            }
        }
        return id
    }

    fun cancel(id: Long) {
        calls[id]?.cancel()
        calls.remove(id)
        update { list ->
            list.map { if (it.id == id && it.status == DownloadStatus.RUNNING) it.copy(status = DownloadStatus.CANCELED, speed = 0) else it }
        }
    }

    fun remove(id: Long) {
        cancel(id)
        update { list -> list.filterNot { it.id == id } }
    }

    fun clearFinished() {
        update { list -> list.filter { it.status == DownloadStatus.RUNNING } }
    }

    fun open(context: Context, task: DownloadTask) {
        val uri = task.uri ?: return
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "*/*")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        runCatching { context.startActivity(intent) }
    }

    private fun fail(id: Long, message: String) {
        update { list -> list.map { if (it.id == id) it.copy(status = DownloadStatus.FAILED, speed = 0, error = message) else it } }
    }

    private fun update(transform: (List<DownloadTask>) -> List<DownloadTask>) {
        _tasks.value = transform(_tasks.value)
    }

    private fun parseFilename(header: String?): String? {
        if (header.isNullOrBlank()) return null
        val star = Regex("""filename\*=UTF-8''([^;]+)""").find(header)
        if (star != null) {
            return runCatching { java.net.URLDecoder.decode(star.groupValues[1], "UTF-8") }.getOrNull()
        }
        val plain = Regex("""filename="?([^";]+)"?""")
            .find(header)?.groupValues?.get(1)?.trim()
        return plain
    }
}

/* ---------------- 落盘 ---------------- */

private object DownloadSaver {

    suspend fun save(
        context: Context,
        name: String,
        mime: String,
        input: InputStream,
        total: Long,
        onProgress: (bytes: Long, speed: Long) -> Unit,
    ): Uri? = kotlinx.coroutines.withContext(Dispatchers.IO) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            saveViaMediaStore(context, name, mime, input, total, onProgress)
        } else {
            saveLegacy(context, name, input, total, onProgress)
        }
    }

    private fun saveViaMediaStore(
        context: Context,
        name: String,
        mime: String,
        input: InputStream,
        total: Long,
        onProgress: (Long, Long) -> Unit,
    ): Uri? {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/AgentDeck")
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val collection = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        val uri = resolver.insert(collection, values) ?: return null
        input.use { source ->
            resolver.openOutputStream(uri)?.use { target ->
                copy(source, target, total, onProgress)
            } ?: return null
        }
        values.clear()
        values.put(MediaStore.Downloads.IS_PENDING, 0)
        resolver.update(uri, values, null, null)
        return uri
    }

    @Suppress("DEPRECATION")
    private fun saveLegacy(
        context: Context,
        name: String,
        input: InputStream,
        total: Long,
        onProgress: (Long, Long) -> Unit,
    ): Uri? {
        val dir = File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
            "AgentDeck",
        )
        if (!dir.exists() && !dir.mkdirs()) return null
        val file = File(dir, name)
        input.use { source ->
            file.outputStream().use { target -> copy(source, target, total, onProgress) }
        }
        return runCatching {
            FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
        }.getOrDefault(Uri.fromFile(file))
    }

    private fun copy(
        input: InputStream,
        output: OutputStream,
        total: Long,
        onProgress: (Long, Long) -> Unit,
    ) {
        val buffer = ByteArray(64 * 1024)
        var written = 0L
        var sampledBytes = 0L
        var sampledAt = System.currentTimeMillis()
        while (true) {
            val read = input.read(buffer)
            if (read <= 0) break
            output.write(buffer, 0, read)
            written += read
            val now = System.currentTimeMillis()
            if (now - sampledAt >= 600) {
                val speed = ((written - sampledBytes) * 1000.0 / (now - sampledAt)).toLong()
                onProgress(written, speed)
                sampledBytes = written
                sampledAt = now
            }
        }
        output.flush()
        onProgress(written, 0)
    }
}

/* ---------------- 界面 ---------------- */

@Composable
fun DownloadCenterScreen(palette: DeckPalette, onBack: () -> Unit) {
    val tasks by DownloadCenter.tasks.collectAsState()
    val context = LocalContext.current

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(palette.bg)
            .statusBarsPadding(),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier
                .fillMaxWidth()
                .background(palette.bubble)
                .padding(horizontal = 4.dp, vertical = 6.dp),
        ) {
            IconButton(onClick = onBack) {
                Icon(
                    Icons.AutoMirrored.Rounded.ArrowBack,
                    contentDescription = "返回",
                    tint = palette.ink,
                )
            }
            Text(
                text = "下载管理",
                fontSize = 16.sp,
                color = palette.ink,
                modifier = Modifier.weight(1f),
            )
            if (tasks.any { it.status != DownloadStatus.RUNNING }) {
                TextButton(onClick = { DownloadCenter.clearFinished() }) {
                    Text("清除已完成", fontSize = 12.5.sp, color = palette.inkSoft)
                }
            }
        }
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(palette.line),
        )

        if (tasks.isEmpty()) {
            Column(
                modifier = Modifier.fillMaxSize(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Icon(
                    Icons.Rounded.Download,
                    contentDescription = null,
                    modifier = Modifier.size(40.dp),
                    tint = palette.inkSoft,
                )
                Spacer(Modifier.height(10.dp))
                Text("还没有下载任务", fontSize = 13.sp, color = palette.inkSoft)
            }
            return
        }

        LazyColumn(modifier = Modifier.fillMaxSize()) {
            items(tasks, key = { it.id }) { task ->
                DownloadRow(task, palette, context)
            }
        }
    }
}

@Composable
private fun DownloadRow(task: DownloadTask, palette: DeckPalette, context: Context) {
    Surface(
        color = palette.bubble,
        shape = RoundedCornerShape(12.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 5.dp),
    ) {
        Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp)) {
            Text(
                text = task.name,
                fontSize = 13.5.sp,
                color = palette.ink,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(6.dp))

            when (task.status) {
                DownloadStatus.RUNNING -> {
                    val ratio = if (task.total > 0) (task.bytes.toFloat() / task.total) else 0f
                    LinearProgressIndicator(
                        progress = { ratio.coerceIn(0f, 1f) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(5.dp)
                            .clip(RoundedCornerShape(999.dp)),
                        color = palette.accent,
                        trackColor = palette.trackFill,
                    )
                    Spacer(Modifier.height(6.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = buildString {
                                if (task.total > 0) {
                                    append("${formatSize(task.bytes)} / ${formatSize(task.total)} · ")
                                    append("${(ratio * 100).toInt()}%")
                                } else {
                                    append(formatSize(task.bytes))
                                }
                            },
                            fontSize = 11.5.sp,
                            color = palette.inkSoft,
                        )
                        Spacer(Modifier.weight(1f))
                        Text(
                            text = if (task.speed > 0) "${formatSize(task.speed)}/s" else "连接中…",
                            fontSize = 11.5.sp,
                            color = palette.accent,
                        )
                        TextButton(onClick = { DownloadCenter.cancel(task.id) }) {
                            Text("取消", fontSize = 12.sp, color = palette.inkSoft)
                        }
                    }
                }

                DownloadStatus.DONE -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Rounded.Download,
                        contentDescription = null,
                        modifier = Modifier.size(14.dp),
                        tint = palette.accent,
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = "已完成 · ${formatSize(task.bytes)}",
                        fontSize = 11.5.sp,
                        color = palette.inkSoft,
                    )
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { DownloadCenter.open(context, task) }) {
                        Icon(Icons.Rounded.OpenInNew, contentDescription = null, modifier = Modifier.size(14.dp), tint = palette.accent)
                        Spacer(Modifier.width(4.dp))
                        Text("打开", fontSize = 12.sp, color = palette.accent)
                    }
                    TextButton(onClick = { DownloadCenter.remove(task.id) }) {
                        Icon(Icons.Rounded.DeleteOutline, contentDescription = null, modifier = Modifier.size(14.dp), tint = palette.inkSoft)
                    }
                }

                DownloadStatus.FAILED -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = task.error ?: "下载失败",
                        fontSize = 11.5.sp,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(onClick = { DownloadCenter.remove(task.id) }) {
                        Text("移除", fontSize = 12.sp, color = palette.inkSoft)
                    }
                }

                DownloadStatus.CANCELED -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("已取消", fontSize = 11.5.sp, color = palette.inkSoft, modifier = Modifier.weight(1f))
                    TextButton(onClick = { DownloadCenter.remove(task.id) }) {
                        Text("移除", fontSize = 12.sp, color = palette.inkSoft)
                    }
                }
            }
        }
    }
}

private fun formatSize(bytes: Long): String = when {
    bytes <= 0 -> "0 B"
    bytes < 1024 -> "$bytes B"
    bytes < 1024 * 1024 -> "${bytes / 1024} KB"
    bytes < 1024L * 1024 * 1024 -> String.format("%.1f MB", bytes / 1024.0 / 1024.0)
    else -> String.format("%.2f GB", bytes / 1024.0 / 1024.0 / 1024.0)
}
