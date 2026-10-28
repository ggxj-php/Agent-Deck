package com.agentdeck.app.ui.components
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color


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
import com.agentdeck.app.DeckApp
import com.agentdeck.app.ui.theme.DeckPalette
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

    // 按手机返回键应该回聊天页，而不是直接退出 App
    androidx.activity.compose.BackHandler { onBack() }

    val running = tasks.count { it.status == DownloadStatus.RUNNING }
    val done = tasks.count { it.status == DownloadStatus.DONE }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(palette.bg)
            .statusBarsPadding(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .background(
                    Brush.verticalGradient(listOf(palette.pinkPale, palette.pinkMist.copy(alpha = 0.35f), palette.bg)),
                )
                .padding(start = 6.dp, end = 10.dp, top = 6.dp, bottom = 14.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) {
                    Icon(
                        Icons.AutoMirrored.Rounded.ArrowBack,
                        contentDescription = "返回",
                        tint = palette.ink,
                    )
                }
                Column(modifier = Modifier.weight(1f)) {
                    Text("下载管理", fontSize = 17.sp, color = palette.ink)
                    Text(
                        text = if (tasks.isEmpty()) "还没有任务" else "$running 个进行中 · $done 个已完成",
                        fontSize = 11.sp,
                        color = palette.inkSoft,
                    )
                }
                if (tasks.any { it.status != DownloadStatus.RUNNING }) {
                    Surface(
                        onClick = { DownloadCenter.clearFinished() },
                        shape = RoundedCornerShape(999.dp),
                        color = palette.bubble.copy(alpha = 0.9f),
                        border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
                    ) {
                        Text(
                            "清除已完成",
                            fontSize = 11.5.sp,
                            color = palette.inkSoft,
                            modifier = Modifier.padding(horizontal = 11.dp, vertical = 6.dp),
                        )
                    }
                }
            }
        }

        if (tasks.isEmpty()) {
            EmptyDownloads(palette)
            return@Column
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(9.dp),
        ) {
            items(tasks, key = { it.id }) { task ->
                DownloadCard(task, palette, context)
            }
        }
    }
}

@Composable
private fun EmptyDownloads(palette: DeckPalette) {
    Column(
        modifier = Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Box(
            contentAlignment = Alignment.Center,
            modifier = Modifier
                .size(78.dp)
                .background(palette.activeFill, CircleShape),
        ) {
            Icon(
                Icons.Rounded.Download,
                contentDescription = null,
                modifier = Modifier.size(34.dp),
                tint = palette.accent,
            )
        }
        Spacer(Modifier.height(14.dp))
        Text("还没有下载任务", fontSize = 14.sp, color = palette.ink)
        Spacer(Modifier.height(5.dp))
        Text(
            "在对话里点附件卡片，就会出现在这里",
            fontSize = 11.5.sp,
            color = palette.inkSoft,
        )
    }
}

@Composable
private fun DownloadCard(task: DownloadTask, palette: DeckPalette, context: Context) {
    Surface(
        color = palette.bubble,
        shape = RoundedCornerShape(16.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, palette.line.copy(alpha = 0.55f)),
        shadowElevation = 2.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(modifier = Modifier.padding(12.dp)) {
            StatusBadge(task.status, palette)
            Spacer(Modifier.width(10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = task.name,
                    fontSize = 13.5.sp,
                    color = palette.ink,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )

                Spacer(Modifier.height(5.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = when (task.status) {
                            DownloadStatus.RUNNING -> {
                                if (task.total > 0) {
                                    "${formatSize(task.bytes)} / ${formatSize(task.total)}"
                                } else {
                                    formatSize(task.bytes)
                                }
                            }

                            DownloadStatus.DONE -> "已完成 · ${formatSize(task.bytes)}"
                            DownloadStatus.FAILED -> "下载失败"
                            DownloadStatus.CANCELED -> "已取消"
                        },
                        fontSize = 11.sp,
                        color = palette.inkSoft,
                    )
                    Spacer(Modifier.weight(1f))
                    if (task.status == DownloadStatus.RUNNING) {
                        SpeedChip(task.speed, palette)
                    } else if (task.status == DownloadStatus.DONE) {
                        Text(
                            text = "100%",
                            fontSize = 11.sp,
                            color = palette.accent,
                        )
                    }
                }

                if (task.status == DownloadStatus.RUNNING) {
                    val ratio = if (task.total > 0) (task.bytes.toFloat() / task.total) else 0f
                    Spacer(Modifier.height(7.dp))
                    LinearProgressIndicator(
                        progress = { ratio.coerceIn(0f, 1f) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(6.dp)
                            .clip(RoundedCornerShape(999.dp)),
                        color = palette.accent,
                        trackColor = palette.trackFill,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text = if (task.total > 0) "${(ratio * 100).toInt()}%" else "准备中…",
                        fontSize = 10.5.sp,
                        color = palette.inkSoft,
                    )
                }

                Spacer(Modifier.height(3.dp))
                ActionsRow(task, palette, context)
            }
        }
    }
}

@Composable
private fun StatusBadge(status: DownloadStatus, palette: DeckPalette) {
    val (bg, fg) = when (status) {
        DownloadStatus.RUNNING -> palette.activeFill to palette.accent
        DownloadStatus.DONE -> Color(0xFFE7F7EC) to Color(0xFF2E9B52)
        DownloadStatus.FAILED -> Color(0xFFFDEAEA) to Color(0xFFD14949)
        DownloadStatus.CANCELED -> palette.trackFill to palette.inkSoft
    }
    Box(
        contentAlignment = Alignment.Center,
        modifier = Modifier
            .size(38.dp)
            .background(bg, CircleShape),
    ) {
        when (status) {
            DownloadStatus.RUNNING -> CircularProgressIndicator(
                strokeWidth = 2.dp,
                color = fg,
                modifier = Modifier.size(17.dp),
            )

            DownloadStatus.DONE -> Icon(
                Icons.Rounded.Check,
                contentDescription = null,
                modifier = Modifier.size(19.dp),
                tint = fg,
            )

            DownloadStatus.FAILED -> Icon(
                Icons.Rounded.ErrorOutline,
                contentDescription = null,
                modifier = Modifier.size(19.dp),
                tint = fg,
            )

            DownloadStatus.CANCELED -> Icon(
                Icons.Rounded.Close,
                contentDescription = null,
                modifier = Modifier.size(19.dp),
                tint = fg,
            )
        }
    }
}

@Composable
private fun SpeedChip(speed: Long, palette: DeckPalette) {
    Surface(
        shape = RoundedCornerShape(999.dp),
        color = palette.activeFill,
    ) {
        Text(
            text = if (speed > 0) "${formatSize(speed)}/s" else "连接中…",
            fontSize = 10.5.sp,
            color = palette.accent,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
        )
    }
}

@Composable
private fun ActionsRow(task: DownloadTask, palette: DeckPalette, context: Context) {
    Row(
        horizontalArrangement = Arrangement.End,
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.fillMaxWidth(),
    ) {
        when (task.status) {
            DownloadStatus.RUNNING -> SmallAction("取消", Icons.Rounded.Close, palette.inkSoft) {
                DownloadCenter.cancel(task.id)
            }

            DownloadStatus.DONE -> {
                SmallAction("打开", Icons.Rounded.OpenInNew, palette.accent) {
                    DownloadCenter.open(context, task)
                }
                SmallAction("移除", Icons.Rounded.DeleteOutline, palette.inkSoft) {
                    DownloadCenter.remove(task.id)
                }
            }

            DownloadStatus.FAILED -> {
                SmallAction("重试", Icons.Rounded.Refresh, palette.accent) {
                    DownloadCenter.remove(task.id)
                    DownloadCenter.start(task.url, task.name)
                }
                SmallAction("移除", Icons.Rounded.DeleteOutline, palette.inkSoft) {
                    DownloadCenter.remove(task.id)
                }
            }

            DownloadStatus.CANCELED -> SmallAction("移除", Icons.Rounded.DeleteOutline, palette.inkSoft) {
                DownloadCenter.remove(task.id)
            }
        }
    }
}

@Composable
private fun SmallAction(
    text: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    tint: Color,
    onClick: () -> Unit,
) {
    TextButton(onClick = onClick, contentPadding = PaddingValues(horizontal = 7.dp)) {
        Icon(icon, contentDescription = null, modifier = Modifier.size(13.dp), tint = tint)
        Spacer(Modifier.width(3.dp))
        Text(text, fontSize = 11.5.sp, color = tint)
    }
}

private fun formatSize(bytes: Long): String = when {
    bytes <= 0 -> "0 B"
    bytes < 1024 -> "$bytes B"
    bytes < 1024 * 1024 -> "${bytes / 1024} KB"
    bytes < 1024L * 1024 * 1024 -> String.format("%.1f MB", bytes / 1024.0 / 1024.0)
    else -> String.format("%.2f GB", bytes / 1024.0 / 1024.0 / 1024.0)
}
