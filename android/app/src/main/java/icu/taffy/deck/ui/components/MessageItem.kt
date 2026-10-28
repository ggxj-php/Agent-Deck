package icu.taffy.deck.ui.components

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
import icu.taffy.deck.ui.Block
import icu.taffy.deck.ui.Role
import icu.taffy.deck.ui.UiMessage
import icu.taffy.deck.ui.theme.DeckPalette

fun resolveUrl(baseUrl: String, raw: String): String =
    if (raw.startsWith("http://") || raw.startsWith("https://")) raw else baseUrl + raw

/** 一条消息：用户是右侧渐变气泡，助手是左侧头像 + 白底气泡，尺寸和网页端对齐。 */
@Composable
fun MessageItem(
    message: UiMessage,
    baseUrl: String,
    palette: DeckPalette,
    avatarUrl: String,
    onPreview: (String) -> Unit = {},
) {
    when (message.role) {
        Role.USER -> UserMessage(message, baseUrl, palette, onPreview)
        Role.ASSISTANT -> AssistantMessage(message, baseUrl, palette, avatarUrl, onPreview)
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
                AsyncImage(
                    model = url,
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
            for (block in message.blocks) {
                when (block) {
                    is Block.Text -> {
                        MarkdownText(
                            text = block.markdown,
                            palette = palette,
                            baseUrl = baseUrl,
                            onPreview = onPreview,
                        )
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

                    is Block.Notice -> NoticeRow(block.text, block.isError, palette)

                    is Block.Artifact -> ArtifactBlock(block, baseUrl, palette, onPreview)
                }
            }
        }
    }
}

@Composable
private fun StickerImage(url: String, onPreview: (String) -> Unit) {
    // 加载失败就整块收起来，别留一块空白占着版面
    var failed by remember(url) { mutableStateOf(false) }
    if (failed) return
    AsyncImage(
        model = url,
        contentDescription = "表情包",
        contentScale = ContentScale.Fit,
        onError = { failed = true },
        modifier = Modifier
            .heightIn(max = 132.dp)
            .widthIn(max = 160.dp)
            .clickable { onPreview(url) },
    )
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
private fun NoticeRow(text: String, isError: Boolean, palette: DeckPalette) {
    val color = if (isError) MaterialTheme.colorScheme.error else palette.inkSoft
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 3.dp),
    ) {
        Icon(
            imageVector = if (isError) Icons.Rounded.ReportProblem else Icons.Rounded.Psychology,
            contentDescription = null,
            modifier = Modifier.size(13.dp),
            tint = color,
        )
        Spacer(Modifier.width(5.dp))
        Text(text = text, fontSize = 12.sp, lineHeight = 17.sp, color = color)
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
