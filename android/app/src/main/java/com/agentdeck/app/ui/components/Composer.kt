package com.agentdeck.app.ui.components
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.rememberScrollState

import androidx.compose.ui.graphics.asImageBitmap


import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Base64
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Description
import androidx.compose.material.icons.rounded.Image
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.agentdeck.app.DeckApp
import com.agentdeck.app.data.DeckException
import com.agentdeck.app.data.Persona
import com.agentdeck.app.data.UploadedFile
import com.agentdeck.app.ui.theme.DeckPalette
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import kotlin.math.max
import kotlin.math.min

private const val MAX_FILES = 6
private const val MAX_FILE_BYTES = 20L * 1024 * 1024

/**
 * 底部输入区：图片、文件附件、多行输入、发送/停止。
 *
 * 「＋」展开图片/文件两个入口；文件会先传到 /api/uploads/file，拿到 id 后随消息一起发。
 */
@Composable
fun Composer(
    busy: Boolean,
    persona: Persona,
    palette: DeckPalette,
    onSend: (String, List<String>, List<UploadedFile>) -> Unit,
    onStop: () -> Unit,
) {
    var text by rememberSaveable { mutableStateOf("") }
    // 和网页端一样最多 6 张（后端 uploads.MAX_COUNT）
    var images by remember { mutableStateOf<List<String>>(emptyList()) }
    var preparing by remember { mutableStateOf(false) }
    var files by remember { mutableStateOf<List<UploadedFile>>(emptyList()) }
    var uploading by remember { mutableStateOf(false) }
    var fileError by remember { mutableStateOf<String?>(null) }
    var menuOpen by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val imagePicker = rememberLauncherForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris ->
        if (uris.isNullOrEmpty()) return@rememberLauncherForActivityResult
        scope.launch {
            preparing = true
            val room = (IMAGE_MAX_COUNT - images.size).coerceAtLeast(0)
            if (uris.size > room) fileError = "一次最多 $IMAGE_MAX_COUNT 张，多的先不放进来"
            val added = withContext(Dispatchers.IO) {
                uris.take(room).mapNotNull { uri -> toDataUrl(context, uri) }
            }
            preparing = false
            if (added.isEmpty()) {
                fileError = "这张图读不出来，换一张试试"
            } else {
                images = images + added
            }
        }
    }

    val filePicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNullOrEmpty()) return@rememberLauncherForActivityResult
        val room = (MAX_FILES - files.size).coerceAtLeast(0)
        if (room == 0) {
            fileError = "一条消息最多带 $MAX_FILES 个文件"
            return@rememberLauncherForActivityResult
        }
        scope.launch {
            uploading = true
            fileError = if (uris.size > room) "一条消息最多带 $MAX_FILES 个文件，多的没放进来" else null
            var failed = 0
            for (uri in uris.take(room)) {
                val picked = withContext(Dispatchers.IO) { readAsDataUrl(context, uri) }
                if (picked == null) {
                    failed++
                    continue
                }
                try {
                    val uploaded = DeckApp.instance.api.uploadFile(picked.name, picked.dataUrl)
                    files = files + uploaded
                } catch (error: Exception) {
                    failed++
                    if (fileError == null) {
                        fileError = if (error is DeckException) error.message else "上传失败：${error.message}"
                    }
                }
            }
            if (failed > 0 && fileError == null) {
                fileError = "$failed 个文件没传上去（读不出来或超过 20MB）"
            }
            uploading = false
        }
    }

    val canSend = !busy && !preparing && !uploading && (text.isNotBlank() || images.isNotEmpty() || files.isNotEmpty())
    val placeholder = persona.placeholder.ifBlank { "和${persona.name}说点什么…" }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(palette.bubble),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(palette.line),
        )

        if (images.isNotEmpty() || preparing || files.isNotEmpty() || uploading) {
            Column(
                modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 9.dp),
            ) {
                if (images.isNotEmpty() || preparing) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            modifier = Modifier
                                .weight(1f, fill = false)
                                .horizontalScroll(rememberScrollState()),
                        ) {
                            images.forEachIndexed { index, dataUrl ->
                                Box {
                                    val localBitmap = remember(dataUrl) { decodeDataUri(dataUrl) }
                                    if (localBitmap != null) {
                                        androidx.compose.foundation.Image(
                                            bitmap = localBitmap.asImageBitmap(),
                                            contentDescription = "待发送的图片",
                                            contentScale = ContentScale.Crop,
                                            modifier = Modifier
                                                .size(58.dp)
                                                .clip(RoundedCornerShape(12.dp)),
                                        )
                                    } else {
                                        AsyncImage(
                                            model = dataUrl,
                                            contentDescription = "待发送的图片",
                                            contentScale = ContentScale.Crop,
                                            modifier = Modifier
                                                .size(58.dp)
                                                .clip(RoundedCornerShape(12.dp)),
                                        )
                                    }
                                    // 单张移除
                                    Box(
                                        contentAlignment = Alignment.Center,
                                        modifier = Modifier
                                            .align(Alignment.TopEnd)
                                            .padding(2.dp)
                                            .size(16.dp)
                                            .clip(CircleShape)
                                            .background(Color(0xCC000000))
                                            .clickable(enabled = !busy) {
                                                images = images.filterIndexed { i, _ -> i != index }
                                            },
                                    ) {
                                        Text("×", color = Color.White, fontSize = 10.sp)
                                    }
                                }
                            }
                            if (preparing) {
                                Box(
                                    contentAlignment = Alignment.Center,
                                    modifier = Modifier
                                        .size(58.dp)
                                        .clip(RoundedCornerShape(12.dp))
                                        .background(palette.softFill),
                                ) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(18.dp),
                                        strokeWidth = 2.dp,
                                    )
                                }
                            }
                        }
                        Spacer(Modifier.width(8.dp))
                        Text(
                            if (images.isEmpty()) "正在处理…" else "已选 ${images.size} 张",
                            fontSize = 12.sp,
                            color = palette.inkSoft,
                        )
                        Spacer(Modifier.weight(1f))
                        SmallCloseButton(enabled = !busy, palette = palette) { images = emptyList() }
                    }
                }

                files.forEach { file ->
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(top = 6.dp),
                    ) {
                        Surface(
                            color = palette.softFill,
                            shape = RoundedCornerShape(10.dp),
                            border = BorderStroke(1.dp, palette.line),
                            modifier = Modifier.weight(1f),
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
                            ) {
                                Icon(
                                    Icons.Rounded.Description,
                                    contentDescription = null,
                                    modifier = Modifier.size(16.dp),
                                    tint = palette.accent,
                                )
                                Spacer(Modifier.width(8.dp))
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(
                                        text = file.name,
                                        fontSize = 12.sp,
                                        color = palette.ink,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                    Text(
                                        text = formatSize(file.size),
                                        fontSize = 10.5.sp,
                                        color = palette.inkSoft,
                                    )
                                }
                            }
                        }
                        Spacer(Modifier.width(6.dp))
                        SmallCloseButton(enabled = !busy, palette = palette) { files = files - file }
                    }
                }

                if (uploading) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(top = 6.dp),
                    ) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(14.dp),
                            strokeWidth = 2.dp,
                            color = palette.accent,
                        )
                        Spacer(Modifier.width(8.dp))
                        Text("正在上传…", fontSize = 11.5.sp, color = palette.inkSoft)
                    }
                }

                fileError?.let { message ->
                    Text(
                        text = message,
                        fontSize = 11.5.sp,
                        color = androidx.compose.material3.MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
            }
        }

        Row(
            verticalAlignment = Alignment.Bottom,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 9.dp),
        ) {
            Box {
                Surface(
                    onClick = { menuOpen = true },
                    enabled = !busy,
                    shape = CircleShape,
                    color = palette.softFill,
                    border = BorderStroke(1.dp, palette.line),
                    modifier = Modifier.size(44.dp),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(
                            Icons.Rounded.Add,
                            contentDescription = "添加图片或文件",
                            modifier = Modifier.size(20.dp),
                            tint = palette.accent,
                        )
                    }
                }
                DropdownMenu(
                    expanded = menuOpen,
                    onDismissRequest = { menuOpen = false },
                    containerColor = palette.bubble,
                ) {
                    DropdownMenuItem(
                        text = { Text("发图片", fontSize = 13.5.sp, color = palette.ink) },
                        leadingIcon = {
                            Icon(
                                Icons.Rounded.Image,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp),
                                tint = palette.inkSoft,
                            )
                        },
                        onClick = {
                            menuOpen = false
                            imagePicker.launch("image/*")
                        },
                    )
                    DropdownMenuItem(
                        text = { Text("发文件", fontSize = 13.5.sp, color = palette.ink) },
                        leadingIcon = {
                            Icon(
                                Icons.Rounded.Description,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp),
                                tint = palette.inkSoft,
                            )
                        },
                        onClick = {
                            menuOpen = false
                            filePicker.launch(arrayOf("*/*"))
                        },
                    )
                }
            }

            Spacer(Modifier.width(8.dp))

            Surface(
                color = palette.softFill,
                shape = RoundedCornerShape(22.dp),
                border = BorderStroke(1.dp, palette.line),
                modifier = Modifier.weight(1f),
            ) {
                Box(modifier = Modifier.padding(horizontal = 13.dp, vertical = 9.dp)) {
                    if (text.isEmpty()) {
                        Text(
                            text = placeholder,
                            fontSize = 16.sp,
                            lineHeight = 24.sp,
                            color = palette.inkSoft.copy(alpha = 0.75f),
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    BasicTextField(
                        value = text,
                        onValueChange = { text = it.take(8000) },
                        enabled = !busy,
                        textStyle = TextStyle(
                            fontSize = 16.sp,
                            lineHeight = 24.sp,
                            color = palette.ink,
                        ),
                        cursorBrush = SolidColor(palette.accent),
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(min = 24.dp, max = 132.dp),
                    )
                }
            }

            Spacer(Modifier.width(8.dp))

            Surface(
                onClick = {
                    if (busy) {
                        onStop()
                    } else if (canSend) {
                        onSend(text.trim(), images, files)
                        text = ""
                        images = emptyList()
                        files = emptyList()
                        fileError = null
                    }
                },
                enabled = busy || canSend,
                shape = RoundedCornerShape(22.dp),
                color = Color.Transparent,
                modifier = Modifier
                    .shadow(3.dp, RoundedCornerShape(22.dp))
                    .height(44.dp)
                    .background(
                        Brush.linearGradient(listOf(palette.accent, palette.pinkDeep)),
                        RoundedCornerShape(22.dp),
                    ),
            ) {
                Box(
                    contentAlignment = Alignment.Center,
                    modifier = Modifier.padding(horizontal = 18.dp),
                ) {
                    Text(
                        text = if (busy) "停止" else "发送",
                        fontSize = 15.sp,
                        color = Color.White,
                    )
                }
            }
        }
    }
}

@Composable
private fun SmallCloseButton(
    enabled: Boolean,
    palette: DeckPalette,
    onClick: () -> Unit,
) {
    Surface(
        onClick = onClick,
        enabled = enabled,
        shape = CircleShape,
        color = Color.White.copy(alpha = 0.9f),
        border = BorderStroke(1.dp, palette.line),
        modifier = Modifier.size(26.dp),
    ) {
        Box(contentAlignment = Alignment.Center) {
            Icon(
                Icons.Rounded.Close,
                contentDescription = "移除",
                modifier = Modifier.size(14.dp),
                tint = palette.inkSoft,
            )
        }
    }
}

private fun formatSize(bytes: Long): String = when {
    bytes <= 0 -> "未知大小"
    bytes < 1024 -> "${bytes} B"
    bytes < 1024 * 1024 -> "${bytes / 1024} KB"
    else -> String.format("%.1f MB", bytes / 1024.0 / 1024.0)
}

/** 一轮最多几张图，和后端 uploads.MAX_COUNT 对齐（网页端同值）。 */
private const val IMAGE_MAX_COUNT = 6

private data class PickedFile(val name: String, val dataUrl: String)

/** 读文件成 data URL，顺手把名字取出来（上限 20MB，和服务端一致）。 */
private fun readAsDataUrl(context: Context, uri: Uri): PickedFile? {
    return runCatching {
        val resolver = context.contentResolver
        val name = resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
            ?.use { cursor -> if (cursor.moveToFirst()) cursor.getString(0) else null }
            ?: "file.bin"
        val bytes = resolver.openInputStream(uri)?.use { it.readBytes() } ?: return null
        if (bytes.isEmpty() || bytes.size > MAX_FILE_BYTES) return null
        val mime = resolver.getType(uri) ?: "application/octet-stream"
        val encoded = Base64.encodeToString(bytes, Base64.NO_WRAP)
        PickedFile(name, "data:$mime;base64,$encoded")
    }.getOrNull()
}

/** 最长边压到 1600px、JPEG 85，单张控制在 2MB 内再上传。反馈页也复用这一份。 */
internal fun toDataUrl(context: Context, uri: Uri): String? {
    return runCatching {
        val raw = context.contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: return null
        if (raw.size > 24 * 1024 * 1024) return null

        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(raw, 0, raw.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

        var sample = 1
        val longest = max(bounds.outWidth, bounds.outHeight)
        while (longest / sample > 1600) sample *= 2

        val decodeOptions = BitmapFactory.Options().apply { inSampleSize = sample }
        val decoded = BitmapFactory.decodeByteArray(raw, 0, raw.size, decodeOptions) ?: return null

        val scale = min(1f, 1600f / max(decoded.width, decoded.height))
        val target = if (scale < 0.999f) {
            Bitmap.createScaledBitmap(
                decoded,
                max(1, (decoded.width * scale).toInt()),
                max(1, (decoded.height * scale).toInt()),
                true,
            )
        } else {
            decoded
        }

        val output = ByteArrayOutputStream()
        target.compress(Bitmap.CompressFormat.JPEG, 85, output)
        if (target !== decoded) target.recycle()
        decoded.recycle()

        val encoded = Base64.encodeToString(output.toByteArray(), Base64.NO_WRAP)
        "data:image/jpeg;base64,$encoded"
    }.getOrNull()
}
