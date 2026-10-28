package icu.taffy.deck.ui.components

import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Download
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import icu.taffy.deck.DeckApp
import icu.taffy.deck.ui.theme.DeckPalette
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import java.io.File

/**
 * 下载 agent 给的临时附件（`/d/<token>/<name>`）。
 *
 * 这个链接需要登录态，浏览器直接点会 401，所以默认走 App 自己的 OkHttp（带 cookie）下载，
 * 同时保留「用浏览器打开」的选项（浏览器里自己登录过就能用）。
 */
object FileDownloader {

    data class Result(val uri: Uri?, val name: String, val error: String?)

    suspend fun download(
        context: Context,
        url: String,
        fallbackName: String,
        onProgress: (Int) -> Unit,
    ): Result = withContext(Dispatchers.IO) {
        runCatching {
            val request = Request.Builder()
                .url(url)
                .header("Accept", "*/*")
                .build()
            DeckApp.instance.api.httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    return@withContext Result(null, fallbackName, "下载失败：HTTP ${response.code}")
                }
                val body = response.body ?: return@withContext Result(null, fallbackName, "服务端没有返回内容")
                val total = body.contentLength()
                val mime = response.header("Content-Type")?.substringBefore(';')?.trim()
                    ?: "application/octet-stream"
                val name = parseFilename(response.header("Content-Disposition")) ?: fallbackName

                val uri = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    saveViaMediaStore(context, name, mime, body.byteStream(), total, onProgress)
                } else {
                    saveLegacy(context, name, body.byteStream(), total, onProgress)
                }
                Result(uri, name, null)
            }
        }.getOrElse { error ->
            Result(null, fallbackName, "下载失败：${error.message ?: "网络异常"}")
        }
    }

    private fun parseFilename(header: String?): String? {
        if (header.isNullOrBlank()) return null
        val star = Regex("""filename\*=UTF-8''([^;]+)""").find(header)
        if (star != null) {
            return runCatching { java.net.URLDecoder.decode(star.groupValues[1], "UTF-8") }.getOrNull()
        }
        val plain = Regex("""filename="?([^";]+)"?""").find(header)
        return plain?.groupValues?.get(1)?.trim()
    }

    private fun saveViaMediaStore(
        context: Context,
        name: String,
        mime: String,
        input: java.io.InputStream,
        total: Long,
        onProgress: (Int) -> Unit,
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
                copyWithProgress(source, target, total, onProgress)
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
        input: java.io.InputStream,
        total: Long,
        onProgress: (Int) -> Unit,
    ): Uri? {
        val dir = File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
            "AgentDeck",
        )
        if (!dir.exists() && !dir.mkdirs()) return null
        val file = File(dir, name)
        input.use { source ->
            file.outputStream().use { target -> copyWithProgress(source, target, total, onProgress) }
        }
        return runCatching {
            FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
        }.getOrDefault(Uri.fromFile(file))
    }

    private fun copyWithProgress(
        input: java.io.InputStream,
        output: java.io.OutputStream,
        total: Long,
        onProgress: (Int) -> Unit,
    ) {
        val buffer = ByteArray(64 * 1024)
        var written = 0L
        var lastPercent = -1
        while (true) {
            val read = input.read(buffer)
            if (read <= 0) break
            output.write(buffer, 0, read)
            written += read
            if (total > 0) {
                val percent = (written * 100 / total).toInt()
                if (percent != lastPercent) {
                    lastPercent = percent
                    onProgress(percent)
                }
            }
        }
        output.flush()
    }

    fun open(context: Context, uri: Uri, mime: String = "*/*") {
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, mime)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        runCatching { context.startActivity(intent) }
    }
}

/** markdown 里的下载链接：卡片式，两个入口（App 内下载 / 浏览器打开）。 */
@Composable
fun DownloadCard(
    label: String,
    path: String,
    baseUrl: String,
    palette: DeckPalette,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val fullUrl = if (path.startsWith("http")) path else baseUrl + path
    val fallbackName = path.trimEnd('/').substringAfterLast('/').ifBlank { "附件" }

    var state by remember { mutableStateOf<DownloadState>(DownloadState.Idle) }
    var progress by remember { mutableIntStateOf(0) }
    var savedName by remember { mutableStateOf(fallbackName) }
    var savedUri by remember { mutableStateOf<Uri?>(null) }

    Surface(
        color = palette.softFill,
        shape = RoundedCornerShape(10.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
    ) {
        Column(modifier = Modifier.padding(horizontal = 11.dp, vertical = 9.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    Icons.Rounded.Download,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                    tint = palette.accent,
                )
                Spacer(Modifier.width(7.dp))
                Text(
                    text = label.ifBlank { savedName },
                    fontSize = 13.sp,
                    color = palette.ink,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            Spacer(Modifier.height(2.dp))
            when (val current = state) {
                is DownloadState.Idle -> Row(
                    horizontalArrangement = Arrangement.End,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    TextButton(
                        onClick = {
                            state = DownloadState.Running
                            progress = 0
                            scope.launch {
                                val result = FileDownloader.download(context, fullUrl, fallbackName) {
                                    progress = it
                                }
                                if (result.error != null) {
                                    state = DownloadState.Failed(result.error)
                                } else {
                                    savedName = result.name
                                    savedUri = result.uri
                                    state = DownloadState.Done(result.uri)
                                }
                            }
                        },
                    ) { Text("App 内下载", fontSize = 12.5.sp, color = palette.accent) }
                    TextButton(
                        onClick = {
                            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(fullUrl))
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            runCatching { context.startActivity(intent) }
                        },
                    ) { Text("用浏览器", fontSize = 12.5.sp, color = palette.inkSoft) }
                }

                is DownloadState.Running -> Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(14.dp),
                        strokeWidth = 2.dp,
                        color = palette.accent,
                    )
                    Spacer(Modifier.width(8.dp))
                    Text("正在下载 $progress%", fontSize = 12.sp, color = palette.inkSoft)
                }

                is DownloadState.Done -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("已保存到「下载/AgentDeck」", fontSize = 12.sp, color = palette.inkSoft)
                    Spacer(Modifier.weight(1f))
                    if (current.uri != null) {
                        TextButton(
                            onClick = { current.uri?.let { FileDownloader.open(context, it) } },
                        ) { Text("打开", fontSize = 12.5.sp, color = palette.accent) }
                    }
                }

                is DownloadState.Failed -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(current.message, fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { state = DownloadState.Idle }) {
                        Text("重试", fontSize = 12.5.sp, color = palette.accent)
                    }
                }
            }
        }
    }
}

private sealed interface DownloadState {
    data object Idle : DownloadState
    data object Running : DownloadState
    data class Done(val uri: Uri?) : DownloadState
    data class Failed(val message: String) : DownloadState
}
