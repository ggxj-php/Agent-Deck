package com.agentdeck.app.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Movie
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import com.agentdeck.app.DeckApp
import com.agentdeck.app.data.DeckException
import com.agentdeck.app.data.VideoJob
import com.agentdeck.app.ui.components.DownloadCenter
import com.agentdeck.app.ui.theme.DeckPalette
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import java.io.File

/**
 * 「我的视频」：看自己提交的视频任务排队到哪、渲染到几成，出片后能直接看/下载。
 *
 * 生成不在这个页面做——用户要片子在对话里说（`generate_video` 工具提交），
 * 这里只是「订单列表」。所以有任务在跑时每 10 秒刷一次（服务端带冷却，不会把
 * 渲染服务打爆），全部结束就停。
 *
 * 下载走 `/api/videos/{id}/file`，服务端代理转发并校验归属，令牌不出服务端；
 * 落到「下载/AgentDeck」，和附件下载同一套（见 DownloadCenter）。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun VideosScreen(palette: DeckPalette, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    var items by remember { mutableStateOf<List<VideoJob>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var refreshing by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var configured by remember { mutableStateOf(true) }
    var preparingId by remember { mutableStateOf<String?>(null) }
    var pendingDelete by remember { mutableStateOf<VideoJob?>(null) }
    var pendingCancel by remember { mutableStateOf<VideoJob?>(null) }

    androidx.activity.compose.BackHandler { onBack() }

    /** [first] = true 整页转圈；false 走下拉刷新 / 轮询的静默刷新。 */
    suspend fun reload(first: Boolean = false, silent: Boolean = false) {
        if (first) loading = true
        if (!silent && !first) refreshing = true
        runCatching { DeckApp.instance.api.videos() }
            .onSuccess {
                items = it.items
                configured = it.configured
                error = null
            }
            .onFailure {
                // 轮询失败不要把已有的列表清掉，页面上留着上一次的结果更友好
                if (items.isEmpty()) {
                    error = if (it is DeckException) it.message
                    else "拉取失败：${it.message ?: "网络波动"}"
                }
            }
        loading = false
        refreshing = false
    }

    LaunchedEffect(Unit) { reload(first = true) }

    // 有任务在排队/渲染时自己刷；全部结束这个协程就退出，不再空转
    val hasActive = items.any { it.active }
    LaunchedEffect(hasActive) {
        while (hasActive) {
            delay(10_000)
            reload(silent = true)
        }
    }

    fun toast(text: String) = Toast.makeText(context, text, Toast.LENGTH_SHORT).show()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(palette.bg)
            .statusBarsPadding()
            .navigationBarsPadding(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .background(
                    Brush.verticalGradient(
                        listOf(palette.pinkPale, palette.pinkMist.copy(alpha = 0.35f), palette.bg),
                    ),
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
                    Text("我的视频", fontSize = 17.sp, color = palette.ink)
                    Text(
                        text = when {
                            loading -> "正在加载…"
                            !configured -> "视频服务还没接上"
                            items.isEmpty() -> "生成任务会出现在这里"
                            hasActive -> "${items.count { it.active }} 个在排队/渲染 · 每 10 秒自动刷新"
                            else -> "共 ${items.size} 个 · 出片后可以播放和下载"
                        },
                        fontSize = 11.sp,
                        color = palette.inkSoft,
                    )
                }
                IconButton(onClick = { scope.launch { reload() } }) {
                    Icon(
                        Icons.Rounded.Refresh,
                        contentDescription = "刷新",
                        tint = palette.inkSoft,
                    )
                }
            }
        }

        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = { scope.launch { reload() } },
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
        ) {
            when {
                loading && items.isEmpty() -> Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator(color = palette.accent, strokeWidth = 2.dp)
                }

                !configured -> EmptyVideos(
                    palette = palette,
                    title = "视频服务还没接上",
                    hint = "管理员在后台「接口设置 → 视频生成」填好地址和令牌就能用了",
                )

                error != null && items.isEmpty() -> Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(28.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Icon(Icons.Rounded.ErrorOutline, contentDescription = null, tint = palette.inkSoft)
                    Spacer(Modifier.height(8.dp))
                    Text(error ?: "", fontSize = 13.sp, color = palette.inkSoft)
                    Spacer(Modifier.height(12.dp))
                    Surface(
                        onClick = { scope.launch { reload() } },
                        shape = RoundedCornerShape(999.dp),
                        color = palette.bubble,
                        border = BorderStroke(1.dp, palette.line),
                    ) {
                        Text(
                            "重试",
                            fontSize = 12.sp,
                            color = palette.ink,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                        )
                    }
                }

                items.isEmpty() -> EmptyVideos(
                    palette = palette,
                    title = "还没有视频任务",
                    hint = "回到对话里说一句「做个视频，讲讲为什么锂电池会起火」，\n任务就会出现在这里",
                )

                else -> LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 10.dp),
                    verticalArrangement = Arrangement.spacedBy(9.dp),
                ) {
                    items(items, key = { it.id }) { item ->
                        VideoCard(
                            item = item,
                            palette = palette,
                            preparing = preparingId == item.id,
                            onPlay = {
                                if (preparingId == null) {
                                    preparingId = item.id
                                    scope.launch {
                                        val ok = openVideo(context, item)
                                        preparingId = null
                                        if (!ok) toast("播放失败，可以先下载再看")
                                    }
                                }
                            },
                            onDownload = {
                                DownloadCenter.start(
                                    DeckApp.instance.api.videoFileUrl(item.id),
                                    safeFileName(item.displayName),
                                )
                                toast("开始下载，完成在「下载/AgentDeck」")
                            },
                            onCancel = { pendingCancel = item },
                            onDelete = { pendingDelete = item },
                        )
                    }
                    item {
                        Text(
                            text = "视频由另一台机器串行渲染，一条通常 5–10 分钟；" +
                                "排队靠前的先出片。片子只在服务器上留一段时间，看完了记得下载。",
                            fontSize = 11.sp,
                            lineHeight = 17.sp,
                            color = palette.inkSoft,
                            modifier = Modifier.padding(horizontal = 4.dp, vertical = 6.dp),
                        )
                    }
                }
            }
        }
    }

    pendingCancel?.let { job ->
        AlertDialog(
            onDismissRequest = { pendingCancel = null },
            containerColor = Color.White,
            title = { Text("取消这个任务？", fontSize = 16.sp, color = palette.ink) },
            text = {
                Text(
                    "「${job.displayName}」还在排队，取消后不会再渲染。",
                    fontSize = 13.sp,
                    color = palette.inkSoft,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    pendingCancel = null
                    scope.launch {
                        runCatching { DeckApp.instance.api.cancelVideo(job.id) }
                            .onSuccess {
                                toast("已取消")
                                reload(silent = true)
                            }
                            .onFailure { toast((it as? DeckException)?.message ?: "取消失败") }
                    }
                }) { Text("取消任务", color = palette.accent) }
            },
            dismissButton = {
                TextButton(onClick = { pendingCancel = null }) {
                    Text("再等等", color = palette.inkSoft)
                }
            },
        )
    }

    pendingDelete?.let { job ->
        AlertDialog(
            onDismissRequest = { pendingDelete = null },
            containerColor = Color.White,
            title = { Text("删除这条记录？", fontSize = 16.sp, color = palette.ink) },
            text = {
                Text(
                    "只从列表里删掉「${job.displayName}」，已经出好的片子还在服务器上。",
                    fontSize = 13.sp,
                    color = palette.inkSoft,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    pendingDelete = null
                    scope.launch {
                        runCatching { DeckApp.instance.api.deleteVideo(job.id) }
                            .onSuccess {
                                toast("已删除")
                                reload(silent = true)
                            }
                            .onFailure { toast((it as? DeckException)?.message ?: "删除失败") }
                    }
                }) { Text("删除", color = Color(0xFFD14949)) }
            },
            dismissButton = {
                TextButton(onClick = { pendingDelete = null }) {
                    Text("取消", color = palette.inkSoft)
                }
            },
        )
    }
}

@Composable
private fun VideoCard(
    item: VideoJob,
    palette: DeckPalette,
    preparing: Boolean,
    onPlay: () -> Unit,
    onDownload: () -> Unit,
    onCancel: () -> Unit,
    onDelete: () -> Unit,
) {
    val (badgeBg, badgeFg) = when (item.state) {
        "queued" -> Color(0xFFFFF4E4) to Color(0xFFB07A24)
        "running" -> Color(0xFFE8F0FF) to Color(0xFF3F6BB5)
        "done" -> Color(0xFFE7F7EC) to Color(0xFF2E9B52)
        "failed" -> Color(0xFFFDEAEA) to Color(0xFFD14949)
        else -> palette.trackFill to palette.inkSoft
    }

    Surface(
        color = palette.bubble,
        shape = RoundedCornerShape(14.dp),
        border = BorderStroke(1.dp, palette.line),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(horizontal = 13.dp, vertical = 11.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Text(
                    text = item.displayName,
                    fontSize = 14.5.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = palette.ink,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                Surface(shape = RoundedCornerShape(999.dp), color = badgeBg) {
                    Text(
                        text = item.stageText.ifBlank { item.state },
                        fontSize = 11.sp,
                        color = badgeFg,
                        modifier = Modifier.padding(horizontal = 9.dp, vertical = 3.dp),
                    )
                }
            }

            Spacer(Modifier.height(4.dp))
            Row {
                val bits = buildList {
                    if (item.duration > 0) add("目标 ${item.duration} 秒")
                    if (item.durationSeconds > 0) add("成片 ${item.durationSeconds.toInt()} 秒")
                    add(if (item.tts) "带配音" else "无配音")
                }
                Text(
                    text = bits.joinToString(" · "),
                    fontSize = 11.5.sp,
                    color = palette.inkSoft,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            when (item.state) {
                "running" -> {
                    Spacer(Modifier.height(9.dp))
                    LinearProgressIndicator(
                        progress = { (item.percent / 100f).coerceIn(0f, 1f) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(5.dp)
                            .clip(RoundedCornerShape(999.dp)),
                        color = palette.accent,
                        trackColor = palette.trackFill,
                    )
                    Spacer(Modifier.height(5.dp))
                    Text(
                        text = "${item.eta.ifBlank { "正在渲染" }}（${item.percent}%）",
                        fontSize = 11.5.sp,
                        color = palette.pinkDeep,
                    )
                }

                "queued" -> {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        text = buildString {
                            item.position?.let { append("排队第 $it 位 · ") }
                            append(item.eta.ifBlank { "等待开始" })
                        },
                        fontSize = 11.5.sp,
                        color = Color(0xFFB07A24),
                    )
                }
            }

            if (item.failed && item.error.isNotBlank()) {
                Spacer(Modifier.height(6.dp))
                Surface(
                    color = Color(0xFFFDF3F3),
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        text = item.error,
                        fontSize = 11.5.sp,
                        lineHeight = 16.sp,
                        color = Color(0xFFB4453F),
                        maxLines = 4,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp),
                    )
                }
            }

            Spacer(Modifier.height(6.dp))
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (item.canDownload) {
                    TextButton(onClick = onPlay, enabled = !preparing) {
                        if (preparing) {
                            CircularProgressIndicator(
                                strokeWidth = 2.dp,
                                color = palette.accent,
                                modifier = Modifier.size(13.dp),
                            )
                            Spacer(Modifier.width(5.dp))
                            Text("准备中…", fontSize = 12.5.sp, color = palette.accent)
                        } else {
                            Text("播放", fontSize = 12.5.sp, color = palette.accent)
                        }
                    }
                    TextButton(onClick = onDownload) {
                        Text("下载", fontSize = 12.5.sp, color = palette.accent)
                    }
                }
                if (item.canCancel) {
                    TextButton(onClick = onCancel) {
                        Text("取消", fontSize = 12.5.sp, color = palette.inkSoft)
                    }
                }
                Spacer(Modifier.weight(1f))
                TextButton(onClick = onDelete) {
                    Text("删除记录", fontSize = 12.sp, color = palette.inkSoft)
                }
            }
        }
    }
}

@Composable
private fun EmptyVideos(palette: DeckPalette, title: String, hint: String) {
    Column(
        modifier = Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Box(
            contentAlignment = Alignment.Center,
            modifier = Modifier
                .size(78.dp)
                .background(palette.activeFill, RoundedCornerShape(999.dp)),
        ) {
            Icon(
                Icons.Rounded.Movie,
                contentDescription = null,
                modifier = Modifier.size(34.dp),
                tint = palette.accent,
            )
        }
        Spacer(Modifier.height(14.dp))
        Text(title, fontSize = 14.sp, color = palette.ink)
        Spacer(Modifier.height(5.dp))
        Text(
            text = hint,
            fontSize = 11.5.sp,
            lineHeight = 17.sp,
            color = palette.inkSoft,
            modifier = Modifier.padding(horizontal = 28.dp),
        )
    }
}

/** 下载用的文件名：去掉路径分隔符之类的字符，太长的截一下。 */
private fun safeFileName(name: String): String {
    val cleaned = name.replace(Regex("""[\\/:*?"<>|\n\r\t]"""), "").trim().take(40)
    val base = cleaned.ifBlank { "视频" }
    return if (base.endsWith(".mp4", ignoreCase = true)) base else "$base.mp4"
}

/**
 * 播放：先把成片下到 App 缓存（带登录 cookie），再用系统播放器打开。
 *
 * 不直接把 URL 丢给外部播放器——那边没有 cookie，打开就是个 401 空白页。
 * 缓存文件几分钟就能下完，看完可以让系统自己清。
 */
private suspend fun openVideo(context: Context, item: VideoJob): Boolean {
    val url = DeckApp.instance.api.videoFileUrl(item.id)
    val dir = File(context.cacheDir, "videos").apply { mkdirs() }
    val target = File(dir, safeFileName(item.displayName))
    val ok = withContext(Dispatchers.IO) {
        runCatching {
            if (!target.exists() || target.length() <= 0) {
                val request = Request.Builder().url(url).header("Accept", "*/*").build()
                DeckApp.instance.api.httpClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) error("服务返回 ${response.code}")
                    val body = response.body ?: error("服务端没有返回内容")
                    body.byteStream().use { input ->
                        target.outputStream().use { output -> input.copyTo(output, 64 * 1024) }
                    }
                }
            }
            true
        }.getOrDefault(false)
    }
    if (!ok) return false
    return withContext(Dispatchers.Main) {
        runCatching {
            val uri: Uri = FileProvider.getUriForFile(
                context,
                "${context.packageName}.fileprovider",
                target,
            )
            val intent = Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, "video/mp4")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            true
        }.getOrDefault(false)
    }
}
