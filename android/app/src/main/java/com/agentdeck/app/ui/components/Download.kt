package com.agentdeck.app.ui.components

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
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
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.agentdeck.app.data.AppSettings
import com.agentdeck.app.ui.theme.DeckPalette

/**
 * markdown 里的下载链接：卡片式，两个入口（App 内下载 / 浏览器打开）。
 * 下载进度直接读 [DownloadCenter] 的任务状态，和下载管理页是同一份数据。
 */
@Composable
fun DownloadCard(
    label: String,
    path: String,
    baseUrl: String,
    palette: DeckPalette,
) {
    val context = LocalContext.current
    // serverUrl 偶发为空（消息刚回来、ViewModel 还在 boot，或者历史数据没带地址），
    // 这时拼出来的是 `/d/xxx`：OkHttp 直接抛异常、浏览器也打不开，用户看到的就是
    // 「两个按钮都没反应」。进程里那份设置永远读得到，用它兜底。
    val root = baseUrl.ifBlank { AppSettings.baseUrl(context) }.trimEnd('/')
    val fullUrl = if (path.startsWith("http")) path else root + path
    val absolute = fullUrl.startsWith("http://") || fullUrl.startsWith("https://")

    // 点击必须留下看得见的痕迹：真正开始下载之前（或者根本没法下载）先把原因写在
    // 卡片里。之前只有 Toast，一闪而过，用户就记住「点了没反应」。
    var hint by remember(fullUrl) { mutableStateOf<String?>(null) }
    var hintError by remember(fullUrl) { mutableStateOf(false) }
    // /d/<token> 的末段是 token，直接用会把文件名存成哈希；链接文字有扩展名时优先用它
    val fallbackName = label.takeIf { it.contains('.') && it.length <= 80 }
        ?: path.trimEnd('/').substringAfterLast('/').ifBlank { "附件" }

    fun showHint(text: String, isError: Boolean) {
        hint = text
        hintError = isError
    }

    fun startDownload() {
        if (!absolute) {
            showHint("下载地址不完整（服务器地址是空的），下拉刷新会话再试", true)
            return
        }
        // 先画出「点击已收到」：任务一建起来就会被进度条取代。如果这行一直停着，
        // 说明请求根本没走出去——比什么都不变好排查得多。
        showHint("正在准备下载…", false)
        DownloadCenter.start(fullUrl, fallbackName)
    }

    fun openInBrowser() {
        if (!absolute) {
            showHint("下载地址不完整（服务器地址是空的），下拉刷新会话再试", true)
            return
        }
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(fullUrl))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(intent) }.isFailure) {
            showHint("这台机器上没有能打开网页的浏览器", true)
        }
    }

    val tasks by DownloadCenter.tasks.collectAsState()
    val task = tasks.lastOrNull { it.url == fullUrl }
    // 任务一旦建起来，卡片自己就会显示进度，不用再留着提示
    LaunchedEffect(task?.status) {
        if (task != null) hint = null
    }

    Surface(
        color = palette.softFill,
        shape = RoundedCornerShape(10.dp),
        border = BorderStroke(1.dp, palette.line),
        // 整张卡片都能点：手指落在右边空白处也不至于「没反应」。
        // 只有还没开始下载（两个入口都摆着）的时候才接管，进度/完成态仍用行内按钮。
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .then(if (task == null) Modifier.clickable { startDownload() } else Modifier),
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
                    text = task?.name?.takeIf { it.isNotBlank() } ?: label.ifBlank { fallbackName },
                    fontSize = 13.sp,
                    color = palette.ink,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.height(2.dp))

            when (task?.status) {
                null -> Row(
                    horizontalArrangement = Arrangement.End,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    TextButton(onClick = { startDownload() }) {
                        Text("App 内下载", fontSize = 12.5.sp, color = palette.accent)
                    }
                    TextButton(onClick = { openInBrowser() }) {
                        Text("用浏览器", fontSize = 12.5.sp, color = palette.inkSoft)
                    }
                }

                DownloadStatus.RUNNING -> {
                    val ratio = if (task.total > 0) (task.bytes.toFloat() / task.total) else 0f
                    LinearProgressIndicator(
                        progress = { ratio.coerceIn(0f, 1f) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(4.dp)
                            .clip(RoundedCornerShape(999.dp)),
                        color = palette.accent,
                        trackColor = palette.trackFill,
                    )
                    Spacer(Modifier.height(4.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = if (task.total > 0) "${(ratio * 100).toInt()}%" else "下载中…",
                            fontSize = 11.5.sp,
                            color = palette.inkSoft,
                        )
                        Spacer(Modifier.weight(1f))
                        Text(
                            text = if (task.speed > 0) "${formatSpeed(task.speed)}" else "连接中…",
                            fontSize = 11.5.sp,
                            color = palette.accent,
                        )
                        TextButton(onClick = { DownloadCenter.cancel(task.id) }) {
                            Text("取消", fontSize = 12.sp, color = palette.inkSoft)
                        }
                    }
                }

                DownloadStatus.DONE -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("已保存到「下载/AgentDeck」", fontSize = 12.sp, color = palette.inkSoft)
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { DownloadCenter.open(context, task) }) {
                        Text("打开", fontSize = 12.5.sp, color = palette.accent)
                    }
                }

                DownloadStatus.FAILED, DownloadStatus.CANCELED -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = if (task.status == DownloadStatus.CANCELED) "已取消" else (task.error ?: "下载失败"),
                        fontSize = 12.sp,
                        color = if (task.status == DownloadStatus.FAILED) MaterialTheme.colorScheme.error else palette.inkSoft,
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(onClick = { DownloadCenter.start(fullUrl, fallbackName) }) {
                        Text("重试", fontSize = 12.5.sp, color = palette.accent)
                    }
                }
            }

            // 点击没成的时候，原因留在卡片里，别只闪一下 Toast
            hint?.let { message ->
                Spacer(Modifier.height(2.dp))
                Text(
                    text = message,
                    fontSize = 11.5.sp,
                    color = if (hintError) MaterialTheme.colorScheme.error else palette.inkSoft,
                )
            }
        }
    }
}

private fun formatSpeed(bytesPerSecond: Long): String = when {
    bytesPerSecond < 1024 -> "$bytesPerSecond B/s"
    bytesPerSecond < 1024 * 1024 -> "${bytesPerSecond / 1024} KB/s"
    else -> String.format("%.1f MB/s", bytesPerSecond / 1024.0 / 1024.0)
}
