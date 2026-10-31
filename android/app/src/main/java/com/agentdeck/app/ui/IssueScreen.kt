package com.agentdeck.app.ui

import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
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
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import coil.compose.AsyncImage
import com.agentdeck.app.DeckApp
import com.agentdeck.app.data.DeckException
import com.agentdeck.app.data.IssueItem
import com.agentdeck.app.ui.components.ZoomableImageDialog
import com.agentdeck.app.ui.components.decodeDataUri
import com.agentdeck.app.ui.components.toDataUrl
import com.agentdeck.app.ui.theme.DeckPalette
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 一条反馈最多带几张图，和后端 issues.MAX_IMAGES 对齐。 */
private const val ISSUE_MAX_IMAGES = 4

/**
 * 意见反馈：提问题（标题 + 正文 + 图片），提交后状态是「审核中」；
 * 管理员在后台采纳或打回之后，这里能看到结果和管理员回的那句话。
 *
 * 处理和网页端 / 后台完全同一套接口（/api/issues、/api/issues/mine），
 * 图片走 /api/issues/image/<id>/<name>，带 cookie 才给看，所以直接用 Coil 的同一个 client。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun IssueScreen(palette: DeckPalette, onBack: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var issues by remember { mutableStateOf<List<IssueItem>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var refreshing by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var composing by remember { mutableStateOf(false) }
    var previewUrl by remember { mutableStateOf<String?>(null) }

    androidx.activity.compose.BackHandler { onBack() }

    /** [first] = true 是第一次进页面（整页转圈），false 是下拉刷新（只在顶部出指示器）。 */
    suspend fun reload(first: Boolean = false) {
        if (first) loading = true else refreshing = true
        error = null
        runCatching { DeckApp.instance.api.myIssues() }
            .onSuccess { issues = it }
            .onFailure {
                error = if (it is DeckException) it.message else "拉取失败：${it.message ?: "网络波动"}"
            }
        loading = false
        refreshing = false
    }

    LaunchedEffect(Unit) { reload(first = true) }

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
                    Text("意见反馈", fontSize = 17.sp, color = palette.ink)
                    Text(
                        text = when {
                            loading -> "正在加载…"
                            issues.isEmpty() -> "提问题、报 bug 都行"
                            else -> "共 ${issues.size} 条 · ${issues.count { it.pending }} 条在等审核"
                        },
                        fontSize = 11.sp,
                        color = palette.inkSoft,
                    )
                }
                // 右上角一个「+」：提反馈入口。样式和原来那个刷新按钮一致（就是个图标按钮）
                IconButton(onClick = { composing = true }) {
                    Icon(
                        Icons.Rounded.Add,
                        contentDescription = "提一个反馈",
                        tint = palette.inkSoft,
                    )
                }
            }
        }

        // 下拉刷新：跟网页端一样，列表往下一拉就重新问一遍服务器
        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = { scope.launch { reload() } },
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
        ) {
            when {
                loading && issues.isEmpty() -> Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator(color = palette.accent, strokeWidth = 2.dp)
                }

            error != null -> Column(
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
                    border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
                ) {
                    Text(
                        "重试",
                        fontSize = 12.sp,
                        color = palette.ink,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                    )
                }
            }

            issues.isEmpty() -> Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center,
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("还没提过反馈", fontSize = 14.sp, color = palette.ink)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "遇到问题、想要什么功能，写下来就行",
                        fontSize = 12.sp,
                        color = palette.inkSoft,
                    )
                }
            }

            else -> LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(9.dp),
            ) {
                items(issues, key = { it.id }) { issue ->
                    IssueCard(
                        issue = issue,
                        palette = palette,
                        onPreview = { previewUrl = it },
                    )
                }
            }
            }
        }
    }

    if (composing) {
        IssueComposer(
            palette = palette,
            onDismiss = { composing = false },
            onSubmitted = { issue ->
                composing = false
                issues = listOf(issue) + issues
                Toast.makeText(context, "已提交，等管理员审核", Toast.LENGTH_SHORT).show()
            },
        )
    }

    previewUrl?.let { url ->
        ZoomableImageDialog(model = url, description = null) { previewUrl = null }
    }
}

@Composable
private fun IssueCard(
    issue: IssueItem,
    palette: DeckPalette,
    onPreview: (String) -> Unit,
) {
    val tone = when (issue.status) {
        "accepted" -> Color(0xFF17734A) to Color(0xFFE6F7EC)
        "rejected" -> Color(0xFFB3261E) to Color(0xFFFFECEC)
        else -> Color(0xFFA86500) to Color(0xFFFFF4E0)
    }
    Surface(
        color = palette.bubble,
        shape = RoundedCornerShape(14.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(horizontal = 13.dp, vertical = 11.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Surface(color = tone.second, shape = RoundedCornerShape(999.dp)) {
                    Text(
                        issue.statusLabel,
                        fontSize = 11.sp,
                        color = tone.first,
                        modifier = Modifier.padding(horizontal = 9.dp, vertical = 2.dp),
                    )
                }
                Spacer(Modifier.width(8.dp))
                Text(
                    text = issue.title,
                    fontSize = 14.5.sp,
                    color = palette.ink,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
            }
            Spacer(Modifier.height(6.dp))
            Text(
                text = issue.body,
                fontSize = 13.sp,
                lineHeight = 19.sp,
                color = palette.inkSoft,
                maxLines = 6,
                overflow = TextOverflow.Ellipsis,
            )
            if (issue.images.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    issue.images.take(4).forEach { name ->
                        val url = DeckApp.instance.api.issueImageUrl(issue.id, name)
                        AsyncImage(
                            model = url,
                            contentDescription = "反馈图片",
                            contentScale = ContentScale.Crop,
                            modifier = Modifier
                                .size(64.dp)
                                .background(palette.softFill, RoundedCornerShape(9.dp))
                                .clip(RoundedCornerShape(9.dp))
                                .clickable { onPreview(url) },
                        )
                    }
                }
            }
            if (issue.adminNote.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Surface(color = palette.pinkMist, shape = RoundedCornerShape(10.dp)) {
                    Column(modifier = Modifier.padding(horizontal = 10.dp, vertical = 7.dp)) {
                        Text("管理员回复", fontSize = 10.5.sp, color = palette.inkSoft)
                        Text(issue.adminNote, fontSize = 12.5.sp, color = palette.ink)
                    }
                }
            }
            Spacer(Modifier.height(6.dp))
            Text(
                text = timeText(issue.createdAt) + if (issue.pending) " · 审核中" else "",
                fontSize = 10.5.sp,
                color = palette.inkSoft.copy(alpha = 0.8f),
            )
        }
    }
}

/** 提交弹窗：标题 + 正文 + 最多 4 张图。 */
@Composable
private fun IssueComposer(
    palette: DeckPalette,
    onDismiss: () -> Unit,
    onSubmitted: (IssueItem) -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var title by remember { mutableStateOf("") }
    var body by remember { mutableStateOf("") }
    var images by remember { mutableStateOf<List<String>>(emptyList()) }
    var submitting by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris ->
        if (uris.isNullOrEmpty()) return@rememberLauncherForActivityResult
        scope.launch {
            val room = (ISSUE_MAX_IMAGES - images.size).coerceAtLeast(0)
            if (room == 0) {
                error = "最多 $ISSUE_MAX_IMAGES 张图"
                return@launch
            }
            if (uris.size > room) error = "最多 $ISSUE_MAX_IMAGES 张图，多的没放进来"
            val added = withContext(Dispatchers.IO) {
                uris.take(room).mapNotNull { toDataUrl(context, it) }
            }
            if (added.isEmpty()) error = "这张图读不出来，换一张试试" else images = images + added
        }
    }

    Dialog(
        onDismissRequest = { if (!submitting) onDismiss() },
        properties = DialogProperties(usePlatformDefaultWidth = true),
    ) {
        Surface(
            color = palette.bubble,
            shape = RoundedCornerShape(18.dp),
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(max = 620.dp)
                .imePadding(),
        ) {
            Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("提个反馈", fontSize = 16.sp, color = palette.ink, modifier = Modifier.weight(1f))
                    IconButton(onClick = { if (!submitting) onDismiss() }) {
                        Icon(Icons.Rounded.Close, contentDescription = "关闭", tint = palette.inkSoft)
                    }
                }
                Column(modifier = Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState())) {
                    TextField(
                        value = title,
                        onValueChange = { if (it.length <= 120) title = it },
                        placeholder = { Text("一句话说清问题", fontSize = 13.5.sp, color = palette.inkSoft) },
                        singleLine = true,
                        colors = fieldColors(palette),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 4.dp),
                    )
                    Spacer(Modifier.height(8.dp))
                    TextField(
                        value = body,
                        onValueChange = { if (it.length <= 8000) body = it },
                        placeholder = {
                            Text(
                                "详细描述一下：怎么复现、当时在做什么、期望是什么",
                                fontSize = 13.5.sp,
                                color = palette.inkSoft,
                            )
                        },
                        colors = fieldColors(palette),
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(min = 130.dp),
                    )
                    Spacer(Modifier.height(10.dp))
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        images.forEachIndexed { index, dataUrl ->
                            Box {
                                // data URI Coil 解不了（聊天里的图也是自己解 Bitmap），得先解出来
                                val bitmap = remember(dataUrl) { decodeDataUri(dataUrl) }
                                if (bitmap != null) {
                                    androidx.compose.foundation.Image(
                                        bitmap = bitmap.asImageBitmap(),
                                        contentDescription = "待提交图片",
                                        contentScale = ContentScale.Crop,
                                        modifier = Modifier
                                            .size(58.dp)
                                            .clip(RoundedCornerShape(9.dp)),
                                    )
                                } else {
                                    AsyncImage(
                                        model = dataUrl,
                                        contentDescription = "待提交图片",
                                        contentScale = ContentScale.Crop,
                                        modifier = Modifier
                                            .size(58.dp)
                                            .clip(RoundedCornerShape(9.dp)),
                                    )
                                }
                                Box(
                                    modifier = Modifier
                                        .align(Alignment.TopEnd)
                                        .padding(2.dp)
                                        .size(16.dp)
                                        .background(Color(0x99000000), CircleShape)
                                        .clickable { images = images.filterIndexed { i, _ -> i != index } },
                                    contentAlignment = Alignment.Center,
                                ) {
                                    Icon(
                                        Icons.Rounded.Close,
                                        contentDescription = "移除",
                                        tint = Color.White,
                                        modifier = Modifier.size(11.dp),
                                    )
                                }
                            }
                        }
                        if (images.size < ISSUE_MAX_IMAGES) {
                            Surface(
                                onClick = { picker.launch("image/*") },
                                shape = RoundedCornerShape(9.dp),
                                color = palette.softFill,
                                border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
                                modifier = Modifier.size(58.dp),
                            ) {
                                Box(contentAlignment = Alignment.Center) {
                                    Icon(
                                        Icons.Rounded.Add,
                                        contentDescription = "加图片",
                                        tint = palette.inkSoft,
                                        modifier = Modifier.size(20.dp),
                                    )
                                }
                            }
                        }
                    }
                    if (error != null) {
                        Spacer(Modifier.height(8.dp))
                        Text(error ?: "", fontSize = 12.sp, color = Color(0xFFB3261E))
                    }
                }
                Spacer(Modifier.height(12.dp))
                Surface(
                    onClick = {
                        if (submitting) return@Surface
                        val t = title.trim()
                        val b = body.trim()
                        when {
                            t.isEmpty() -> error = "标题别空着"
                            b.isEmpty() -> error = "正文别空着"
                            else -> {
                                submitting = true
                                error = null
                                scope.launch {
                                    runCatching {
                                        DeckApp.instance.api.submitIssue(t, b, images)
                                    }.onSuccess {
                                        submitting = false
                                        onSubmitted(it)
                                    }.onFailure {
                                        submitting = false
                                        error = if (it is DeckException) it.message
                                        else "提交失败：${it.message ?: "网络波动"}"
                                    }
                                }
                            }
                        }
                    },
                    shape = RoundedCornerShape(12.dp),
                    color = palette.accent,
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 44.dp),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Text(
                            text = if (submitting) "提交中…" else "提交",
                            fontSize = 14.5.sp,
                            color = Color.White,
                        )
                    }
                }
                Text(
                    text = "提交后是「审核中」，管理员处理完这里会变成已采纳 / 已打回",
                    fontSize = 10.5.sp,
                    color = palette.inkSoft.copy(alpha = 0.85f),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 8.dp),
                )
            }
        }
    }
}

@Composable
private fun fieldColors(palette: DeckPalette) = TextFieldDefaults.colors(
    focusedContainerColor = palette.softFill,
    unfocusedContainerColor = palette.softFill,
    disabledContainerColor = palette.softFill,
    focusedIndicatorColor = palette.accent,
    unfocusedIndicatorColor = palette.line,
    cursorColor = palette.accent,
    focusedTextColor = palette.ink,
    unfocusedTextColor = palette.ink,
)

private fun timeText(seconds: Double): String {
    if (seconds <= 0.0) return ""
    val date = Date((seconds * 1000).toLong())
    return SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.CHINA).format(date)
}
