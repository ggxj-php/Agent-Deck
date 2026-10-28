package icu.taffy.deck.ui.components

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.BorderStroke
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
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import icu.taffy.deck.ui.theme.DeckPalette

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
    val fullUrl = if (path.startsWith("http")) path else baseUrl + path
    val fallbackName = path.trimEnd('/').substringAfterLast('/').ifBlank { "附件" }
    val tasks by DownloadCenter.tasks.collectAsState()
    val task = tasks.lastOrNull { it.url == fullUrl }

    Surface(
        color = palette.softFill,
        shape = RoundedCornerShape(10.dp),
        border = BorderStroke(1.dp, palette.line),
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
                    TextButton(onClick = { DownloadCenter.start(fullUrl, fallbackName) }) {
                        Text("App 内下载", fontSize = 12.5.sp, color = palette.accent)
                    }
                    TextButton(
                        onClick = {
                            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(fullUrl))
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            runCatching { context.startActivity(intent) }
                        },
                    ) { Text("用浏览器", fontSize = 12.5.sp, color = palette.inkSoft) }
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
        }
    }
}

private fun formatSpeed(bytesPerSecond: Long): String = when {
    bytesPerSecond < 1024 -> "$bytesPerSecond B/s"
    bytesPerSecond < 1024 * 1024 -> "${bytesPerSecond / 1024} KB/s"
    else -> String.format("%.1f MB/s", bytesPerSecond / 1024.0 / 1024.0)
}
