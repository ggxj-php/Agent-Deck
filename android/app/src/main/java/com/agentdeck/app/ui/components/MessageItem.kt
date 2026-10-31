package com.agentdeck.app.ui.components
import androidx.compose.foundation.BorderStroke
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.TextButton

import androidx.compose.ui.graphics.asImageBitmap

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.ui.draw.alpha
import kotlinx.coroutines.delay

import androidx.compose.material.icons.rounded.ContentCopy
import androidx.compose.runtime.LaunchedEffect


import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Description
import androidx.compose.material.icons.rounded.ExpandLess
import androidx.compose.material.icons.rounded.ExpandMore
import androidx.compose.material.icons.rounded.Image
import androidx.compose.material.icons.rounded.Psychology
import androidx.compose.material.icons.rounded.ReportProblem
import androidx.compose.material.icons.rounded.Terminal
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.agentdeck.app.ui.Block
import com.agentdeck.app.ui.Role
import com.agentdeck.app.ui.UiMessage
import com.agentdeck.app.ui.HistoryParser
import com.agentdeck.app.ui.StickerPart
import com.agentdeck.app.ui.splitStickerMarks
import com.agentdeck.app.ui.theme.DeckPalette

fun resolveUrl(baseUrl: String, raw: String): String =
    // data: 是本地图片（刚发出去还没上传的那张），不能拼服务器地址，否则整条 URL 作废
    if (
        raw.startsWith("http://") || raw.startsWith("https://") ||
        raw.startsWith("data:") || raw.startsWith("content:") || raw.startsWith("file:")
    ) {
        raw
    } else {
        baseUrl + raw
    }

/** 一条消息：用户是右侧渐变气泡，助手是左侧头像 + 白底气泡，尺寸和网页端对齐。 */
@Composable
fun MessageItem(
    message: UiMessage,
    baseUrl: String,
    palette: DeckPalette,
    avatarUrl: String,
    onPreview: (String) -> Unit = {},
    onAskPick: (Long, Int, String) -> Unit = { _, _, _ -> },
    /** 提示条下面那个「继续」按钮：出错/截断后让模型接着讲。 */
    onContinue: () -> Unit = {},
) {
    when (message.role) {
        Role.USER -> UserMessage(message, baseUrl, palette, onPreview)
        Role.ASSISTANT -> AssistantMessage(
            message = message,
            baseUrl = baseUrl,
            palette = palette,
            avatarUrl = avatarUrl,
            onPreview = onPreview,
            onAskPick = onAskPick,
            onContinue = onContinue,
        )
    }
}

@Composable
private fun UserMessage(
    message: UiMessage,
    baseUrl: String,
    palette: DeckPalette,
    onPreview: (String) -> Unit,
) {
    val text = (message.blocks.filterIsInstance<Block.Text>().firstOrNull()?.markdown).orEmpty()
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 14.dp),
        horizontalArrangement = Arrangement.End,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 310.dp)
                .clip(RoundedCornerShape(topStart = 16.dp, topEnd = 5.dp, bottomEnd = 16.dp, bottomStart = 16.dp))
                .background(Brush.linearGradient(listOf(palette.accent, palette.userBubbleEnd)))
                .padding(horizontal = 13.dp, vertical = 10.dp),
        ) {
            message.images.forEach { raw ->
                val url = resolveUrl(baseUrl, raw)
                val context = LocalContext.current
                // 刚发出去的图片是 data URI，Coil 解不了，先自己解成 Bitmap
                val localBitmap = remember(url) {
                    if (isDataUri(url)) decodeDataUri(url) else null
                }
                val request = remember(url) {
                    ImageRequest.Builder(context).data(url).size(1200, 1200).build()
                }
                if (localBitmap != null) {
                    androidx.compose.foundation.Image(
                        bitmap = localBitmap.asImageBitmap(),
                        contentDescription = "发送的图片",
                        contentScale = ContentScale.FillWidth,
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(max = 240.dp)
                            .clip(RoundedCornerShape(12.dp))
                            .clickable { onPreview(url) },
                    )
                    return@forEach
                }
                AsyncImage(
                    model = request,
                    contentDescription = "发送的图片",
                    contentScale = ContentScale.FillWidth,
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(max = 240.dp)
                        .clip(RoundedCornerShape(12.dp))
                        .clickable { onPreview(url) },
                )
                Spacer(Modifier.height(6.dp))
            }
            message.files.forEach { file -> UserFileChip(file.name, file.size) }
            if (text.isNotBlank()) {
                Text(
                    text = text,
                    fontSize = 15.sp,
                    lineHeight = 22.sp,
                    color = Color.White,
                )
            }
        }
    }
}

@Composable
private fun UserFileChip(name: String, size: Long) {
    Surface(
        color = Color.White.copy(alpha = 0.20f),
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 6.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 9.dp, vertical = 7.dp),
        ) {
            Icon(
                Icons.Rounded.Description,
                contentDescription = null,
                modifier = Modifier.size(15.dp),
                tint = Color.White,
            )
            Spacer(Modifier.width(7.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = name,
                    fontSize = 12.sp,
                    color = Color.White,
                    maxLines = 1,
                    overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                )
                Text(
                    text = when {
                        size <= 0 -> "附件"
                        size < 1024 * 1024 -> "${size / 1024} KB"
                        else -> String.format("%.1f MB", size / 1024.0 / 1024.0)
                    },
                    fontSize = 10.sp,
                    color = Color.White.copy(alpha = 0.85f),
                )
            }
        }
    }
}

@Composable
private fun AssistantMessage(
    message: UiMessage,
    baseUrl: String,
    palette: DeckPalette,
    avatarUrl: String,
    onPreview: (String) -> Unit,
    onAskPick: (Long, Int, String) -> Unit,
    onContinue: () -> Unit,
) {
    val bubbleShape = RoundedCornerShape(
        topStart = 5.dp,
        topEnd = 16.dp,
        bottomEnd = 16.dp,
        bottomStart = 16.dp,
    )
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 14.dp),
        verticalAlignment = Alignment.Top,
    ) {
        AsyncImage(
            model = avatarUrl,
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier
                .size(32.dp)
                .shadow(4.dp, CircleShape)
                .clip(CircleShape)
                .border(1.5.dp, Color.White, CircleShape),
        )
        Spacer(Modifier.width(8.dp))
        Column(
            modifier = Modifier
                .widthIn(max = 320.dp)
                .clip(bubbleShape)
                .background(palette.bubble)
                .border(1.dp, palette.line, bubbleShape)
                .padding(horizontal = 13.dp, vertical = 10.dp),
        ) {
            message.blocks.forEachIndexed { index, block ->
                when (block) {
                    is Block.Text -> {
                        androidx.compose.foundation.text.selection.SelectionContainer {
                            MarkdownWithStickers(
                                markdown = block.markdown,
                                palette = palette,
                                baseUrl = baseUrl,
                                onPreview = onPreview,
                            )
                        }
                    }

                    is Block.Thinking -> ThinkingCard(block, palette)

                    is Block.Tool -> ToolCard(block, palette)

                    is Block.Sticker -> {
                        if (block.url.isNotBlank()) {
                            Spacer(Modifier.height(4.dp))
                            StickerImage(block.url, onPreview)
                            Spacer(Modifier.height(4.dp))
                        }
                    }

                    is Block.Notice -> NoticeRow(
                        text = block.text,
                        isError = block.isError,
                        canContinue = block.canContinue,
                        palette = palette,
                        onContinue = onContinue,
                    )

                    is Block.Artifact -> ArtifactBlock(block, baseUrl, palette, onPreview)

                    is Block.Ask -> AskCard(
                        block = block,
                        palette = palette,
                        onPick = { option -> onAskPick(message.id, index, option) },
                    )
                }
            }
            if (message.waiting) {
                LoadingBubble(
                    palette = palette,
                    since = message.waitingSince,
                    label = message.waitingLabel,
                )
            }
            CopyWholeRow(message, palette)
        }
    }
}

@Composable
private fun StickerImage(url: String, onPreview: (String) -> Unit) {
    // 加载失败就整块收起来，别留一块空白占着版面
    var failed by remember(url) { mutableStateOf(false) }
    if (failed) return
    val context = LocalContext.current
    val request = remember(url) {
        ImageRequest.Builder(context).data(url).size(360, 360).build()
    }
    AsyncImage(
        model = request,
        contentDescription = "表情包",
        contentScale = ContentScale.Fit,
        onError = { failed = true },
        modifier = Modifier
            .heightIn(max = 132.dp)
            .widthIn(max = 160.dp)
            .clickable { onPreview(url) },
    )
}

/**
 * 带表情包的正文。
 *
 * 流式时表情包是以 `[sticker:心情:文件名]` 的形式留在正文里的（不是独立的一块），
 * 所以这里能像网页端 `hydrateStickers` 那样：先把标记挪到句子边界，再按标记切段渲染。
 * 之前表情包是直接往消息末尾追加一个块，位置一定下来就再也没法纠正，
 * 落在括号中间只能认了——这就是「表情包插在奇怪地方」的由来。
 */
@Composable
private fun MarkdownWithStickers(
    markdown: String,
    palette: DeckPalette,
    baseUrl: String,
    onPreview: (String) -> Unit,
) {
    val parts = remember(markdown) {
        splitStickerMarks(HistoryParser.snapStickersToBoundaries(markdown))
    }
    if (parts.none { it is StickerPart.Mark }) {
        MarkdownText(text = markdown, palette = palette, baseUrl = baseUrl, onPreview = onPreview)
        return
    }
    Column {
        parts.forEach { part ->
            when (part) {
                is StickerPart.Text -> {
                    val body = part.value.trim('\n')
                    if (body.isNotBlank()) {
                        MarkdownText(
                            text = body,
                            palette = palette,
                            baseUrl = baseUrl,
                            onPreview = onPreview,
                        )
                    }
                }

                is StickerPart.Mark -> {
                    val name = part.name
                    if (name.isNotBlank()) {
                        Spacer(Modifier.height(4.dp))
                        StickerImage("$baseUrl/api/persona/sticker/$name", onPreview)
                        Spacer(Modifier.height(4.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun ThinkingCard(block: Block.Thinking, palette: DeckPalette) {
    var expanded by remember { mutableStateOf(false) }
    Surface(
        onClick = { expanded = !expanded },
        color = palette.softFill,
        shape = RoundedCornerShape(10.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
    ) {
        Column(modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    Icons.Rounded.Psychology,
                    contentDescription = null,
                    modifier = Modifier.size(14.dp),
                    tint = palette.inkSoft,
                )
                Spacer(Modifier.width(5.dp))
                Text(
                    text = if (block.done) "已深度思考" else "思考中…",
                    fontSize = 12.sp,
                    color = palette.inkSoft,
                    modifier = Modifier.weight(1f),
                )
                Icon(
                    imageVector = if (expanded) Icons.Rounded.ExpandLess else Icons.Rounded.ExpandMore,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                    tint = palette.inkSoft,
                )
            }
            if (expanded && block.text.isNotBlank()) {
                Spacer(Modifier.height(6.dp))
                SelectionContainer {
                    Text(
                        text = block.text,
                        fontSize = 12.sp,
                        lineHeight = 18.sp,
                        color = palette.inkSoft,
                    )
                }
            }
        }
    }
}

@Composable
private fun ToolCard(tool: Block.Tool, palette: DeckPalette) {
    var expanded by remember { mutableStateOf(false) }
    Surface(
        onClick = { expanded = !expanded },
        color = Color.White,
        shape = RoundedCornerShape(10.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
    ) {
        Column(modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    Icons.Rounded.Terminal,
                    contentDescription = null,
                    modifier = Modifier.size(14.dp),
                    tint = palette.accent,
                )
                Spacer(Modifier.width(5.dp))
                Text(
                    text = tool.name,
                    fontSize = 12.5.sp,
                    fontWeight = FontWeight.Medium,
                    color = palette.ink,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text = if (tool.done) "完成" else "运行中…",
                    fontSize = 11.5.sp,
                    color = if (tool.done) palette.pinkDeep else palette.inkSoft,
                )
                Spacer(Modifier.width(3.dp))
                Icon(
                    imageVector = if (expanded) Icons.Rounded.ExpandLess else Icons.Rounded.ExpandMore,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                    tint = palette.inkSoft,
                )
            }
            if (expanded) {
                if (tool.args.isNotBlank()) {
                    Spacer(Modifier.height(6.dp))
                    FieldLabel("参数", palette)
                    MonoBlock(tool.args, palette)
                }
                if (tool.result != null) {
                    Spacer(Modifier.height(6.dp))
                    FieldLabel("结果", palette)
                    MonoBlock(tool.result, palette)
                }
            }
        }
    }
}

@Composable
private fun FieldLabel(text: String, palette: DeckPalette) {
    Text(text = text, fontSize = 10.5.sp, color = palette.inkSoft)
    Spacer(Modifier.height(3.dp))
}

@Composable
private fun MonoBlock(text: String, palette: DeckPalette) {
    Surface(
        color = Color(0xFF23252F),
        shape = RoundedCornerShape(8.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        SelectionContainer {
            Text(
                text = text.take(20_000),
                fontFamily = FontFamily.Monospace,
                fontSize = 11.sp,
                lineHeight = 16.sp,
                color = Color(0xFFE6E6EE),
                modifier = Modifier
                    .padding(8.dp)
                    .heightIn(max = 240.dp),
            )
        }
    }
}

@Composable
private fun NoticeRow(
    text: String,
    isError: Boolean,
    canContinue: Boolean,
    palette: DeckPalette,
    onContinue: () -> Unit,
) {
    val color = if (isError) MaterialTheme.colorScheme.error else palette.inkSoft
    Column(modifier = Modifier
        .fillMaxWidth()
        .padding(vertical = 3.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                imageVector = if (isError) Icons.Rounded.ReportProblem else Icons.Rounded.Psychology,
                contentDescription = null,
                modifier = Modifier.size(13.dp),
                tint = color,
            )
            Spacer(Modifier.width(5.dp))
            Text(text = text, fontSize = 12.sp, lineHeight = 17.sp, color = color)
        }
        // 网页端 noticeBubble(turn, text, canContinue) 的第三个参数：
        // 断流 / 截断 / 报错之后给个按钮，点一下自动发「继续」，不用用户自己打字
        if (canContinue) {
            var sent by remember(text) { mutableStateOf(false) }
            Surface(
                onClick = {
                    if (sent) return@Surface
                    sent = true
                    onContinue()
                },
                color = Color.Transparent,
                shape = RoundedCornerShape(7.dp),
                border = androidx.compose.foundation.BorderStroke(1.dp, palette.accent.copy(alpha = 0.5f)),
                modifier = Modifier.padding(top = 5.dp),
            ) {
                Text(
                    text = if (sent) "已继续" else "接着讲",
                    fontSize = 11.5.sp,
                    color = if (sent) palette.inkSoft else palette.accent,
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun ArtifactBlock(
    block: Block.Artifact,
    baseUrl: String,
    palette: DeckPalette,
    onPreview: (String) -> Unit,
) {
    var failed by remember(block.id) { mutableStateOf(false) }
    if (failed) {
        ArtifactCard(block, baseUrl, palette)
        return
    }
    val url = "$baseUrl/api/artifact/${block.id}/svg"
    val context = LocalContext.current
    // 内联按 1600px 栅格化，缩放/小屏看文字都够清楚
    val request = remember(url) {
        ImageRequest.Builder(context).data(url).size(1600, 1600).build()
    }
    Column(modifier = Modifier.padding(vertical = 4.dp)) {
        if (block.title.isNotBlank()) {
            Text(
                text = block.title,
                fontSize = 12.5.sp,
                color = palette.inkSoft,
                modifier = Modifier.padding(bottom = 4.dp),
            )
        }
        // 产物本来就是 SVG，用 Coil 解码后直接显示；点一下能全屏放大
        AsyncImage(
            model = request,
            contentDescription = block.title.ifBlank { "生成的图" },
            contentScale = ContentScale.Fit,
            onError = { failed = true },
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 60.dp, max = 460.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(Color.White)
                .clickable { onPreview(url) },
        )
    }
}

@Composable
private fun ArtifactCard(block: Block.Artifact, baseUrl: String, palette: DeckPalette) {
    val context = LocalContext.current
    Surface(
        onClick = {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse("$baseUrl/api/artifact/${block.id}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            runCatching { context.startActivity(intent) }
        },
        color = palette.pinkMist,
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 9.dp),
        ) {
            Icon(
                Icons.Rounded.Image,
                contentDescription = null,
                modifier = Modifier.size(16.dp),
                tint = palette.accent,
            )
            Spacer(Modifier.width(7.dp))
            Column {
                Text(
                    text = block.title.ifBlank { "查看生成的图" },
                    fontSize = 12.5.sp,
                    fontWeight = FontWeight.Medium,
                    color = palette.ink,
                )
                Text(
                    text = "在浏览器里打开",
                    fontSize = 10.5.sp,
                    color = palette.inkSoft,
                )
            }
        }
    }
}

/** 每條回复顶部：一键复制整条正文（贴纸/工具卡片不参与）。 */
@Composable
private fun CopyWholeRow(message: UiMessage, palette: DeckPalette) {
    val clipboard = androidx.compose.ui.platform.LocalClipboardManager.current
    var copied by remember(message.id) { mutableStateOf(false) }
    // 不能只按 id 记忆：流式消息第一次组合时 blocks 是空的，
    // 记住空串之后就永远不显示「复制」了（刚生成完的消息没复制键就是这个原因）。
    val plain = remember(message.blocks) {
        message.blocks.filterIsInstance<Block.Text>()
            .joinToString("\n\n") { it.markdown }
            .trim()
    }
    if (plain.isEmpty()) return
    Row(
        horizontalArrangement = Arrangement.Start,
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 2.dp),
    ) {
        Surface(
            onClick = {
                clipboard.setText(androidx.compose.ui.text.AnnotatedString(plain))
                copied = true
            },
            color = Color.Transparent,
            shape = RoundedCornerShape(6.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(horizontal = 4.dp, vertical = 2.dp),
            ) {
                Icon(
                    Icons.Rounded.ContentCopy,
                    contentDescription = null,
                    modifier = Modifier.size(12.dp),
                    tint = if (copied) palette.accent else palette.inkSoft,
                )
                Spacer(Modifier.width(4.dp))
                Text(
                    text = if (copied) "已复制" else "复制",
                    fontSize = 10.5.sp,
                    color = if (copied) palette.accent else palette.inkSoft,
                )
            }
        }
        LaunchedEffect(copied) {
            if (copied) {
                kotlinx.coroutines.delay(1500)
                copied = false
            }
        }
    }
}

/**
 * 发出消息后、第一个事件回来之前：三个跳动的点 + 「正在思考… N 秒」。
 * 和网页端 `showLoading()` 一致——想很久的时候也能看出它没卡死。
 */
@Composable
private fun LoadingBubble(palette: DeckPalette, since: Long, label: String) {
    val start = remember(since) {
        if (since > 0) since else System.currentTimeMillis()
    }
    var seconds by remember { mutableStateOf(0) }
    LaunchedEffect(start) {
        while (true) {
            delay(1000)
            seconds = ((System.currentTimeMillis() - start) / 1000).toInt()
        }
    }

    Row(verticalAlignment = Alignment.CenterVertically) {
        repeat(3) { index ->
            val alpha by animateFloatAsState(
                targetValue = if (seconds % 3 == index) 1f else 0.35f,
                label = "dot$index",
            )
            Box(
                modifier = Modifier
                    .padding(end = 4.dp)
                    .size(6.dp)
                    .alpha(alpha)
                    .background(palette.accent, CircleShape),
            )
        }
        Spacer(Modifier.width(4.dp))
        Text(
            text = "$label ${seconds}s",
            fontSize = 12.5.sp,
            color = palette.inkSoft,
        )
    }
}


/**
 * ask_user 选择卡：模型要用户拍板时用。
 * 和网页端 showAskCard 一致——点选项/自己输入后，发一条「我选择：X」继续。
 */
@Composable
private fun AskCard(
    block: Block.Ask,
    palette: DeckPalette,
    onPick: (String) -> Unit,
) {
    var custom by remember(block.question) { mutableStateOf("") }
    val answered = block.chosen != null

    Surface(
        color = palette.softFill,
        shape = RoundedCornerShape(12.dp),
        border = BorderStroke(1.dp, palette.line),
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 6.dp),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text = block.question,
                fontSize = 13.5.sp,
                color = palette.ink,
                fontWeight = FontWeight.SemiBold,
                lineHeight = 20.sp,
            )
            Spacer(Modifier.height(8.dp))

            block.options.forEach { option ->
                val picked = answered && block.chosen == option
                Surface(
                    onClick = { if (!answered) onPick(option) },
                    enabled = !answered,
                    shape = RoundedCornerShape(10.dp),
                    color = if (picked) palette.activeFill else palette.bubble,
                    border = BorderStroke(1.dp, if (picked) palette.accent else palette.line),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 3.dp),
                ) {
                    Text(
                        text = option,
                        fontSize = 13.sp,
                        color = palette.accent,
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 9.dp),
                    )
                }
            }

            if (block.allowCustom && !answered) {
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(
                        value = custom,
                        onValueChange = { custom = it },
                        singleLine = true,
                        placeholder = { Text("也可以自己输入…", fontSize = 12.sp, color = palette.inkSoft) },
                        textStyle = androidx.compose.ui.text.TextStyle(fontSize = 13.sp),
                        shape = RoundedCornerShape(10.dp),
                        modifier = Modifier.weight(1f),
                    )
                    Spacer(Modifier.width(8.dp))
                    TextButton(
                        enabled = custom.isNotBlank(),
                        onClick = { onPick(custom.trim()) },
                    ) {
                        Text("确定", color = palette.accent, fontSize = 13.sp)
                    }
                }
            }

            if (answered) {
                Text(
                    text = "已选择：${block.chosen}",
                    fontSize = 11.5.sp,
                    color = palette.accent,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
        }
    }
}
