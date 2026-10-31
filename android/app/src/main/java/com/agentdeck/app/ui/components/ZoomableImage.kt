package com.agentdeck.app.ui.components

import android.content.Context
import android.graphics.Bitmap
import android.util.Base64
import android.util.Log
import android.widget.Toast
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.DownloadDone
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.agentdeck.app.DeckApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import java.io.File

private const val TAG = "AgentDeck"

/** 相册只认这几种；顺便把 mime 和扩展名绑在一起，避免名字和内容对不上。 */
internal val IMAGE_MIME_BY_EXT = mapOf(
    "png" to "image/png",
    "jpg" to "image/jpeg",
    "jpeg" to "image/jpeg",
    "webp" to "image/webp",
    "gif" to "image/gif",
    "bmp" to "image/bmp",
    "heic" to "image/heic",
    "heif" to "image/heif",
    "svg" to "image/svg+xml",
)

/**
 * 相册里的文件名。
 *
 * 服务器上的表情包多是哈希名（`9f3a1c.png`），原样存进去用户根本找不到，
 * 所以统一换成 `agentdeck-<时间戳>.<真实扩展名>`；扩展名优先信服务器给的 Content-Type
 * （它描述的是真实字节），拿不到再退回 URL 后缀，都没有才当 png。
 */
internal fun galleryFileName(source: String?, serverMime: String?, stamp: Long): Pair<String, String> {
    val fromMime = serverMime
        ?.substringBefore(';')
        ?.trim()
        ?.lowercase()
        ?.takeIf { it.startsWith("image/") }
        ?.substringAfter('/')
        ?.substringBefore('+')
        ?.takeIf { it in IMAGE_MIME_BY_EXT }
    val fromUrl = source
        ?.substringBefore('?')
        ?.substringBefore('#')
        ?.substringAfterLast('/')
        ?.substringAfterLast('.', "")
        ?.lowercase()
        ?.takeIf { it in IMAGE_MIME_BY_EXT }
    val ext = fromMime ?: fromUrl ?: "png"
    return "agentdeck-$stamp.$ext" to (IMAGE_MIME_BY_EXT[ext] ?: "image/png")
}

/** 保存结果，只用来决定按钮文案和提示语。 */
internal sealed interface SaveOutcome {
    object InGallery : SaveOutcome
    object InDownloads : SaveOutcome
    data class Failed(val reason: String) : SaveOutcome
}

/**
 * 全屏看图：白底、双指缩放、拖动、双击放大，底部可以把当前图保存到相册。
 *
 * 底部按钮的位置不依赖 dialog 窗口的 insets（实测在部分 ROM / Android 15+ 上恒为 0），
 * 而是「读到的 inset 和固定下限取大」，保证永远离开手势条，不会被系统手势吃掉点击。
 */
@Composable
fun ZoomableImageDialog(
    model: Any?,
    description: String?,
    onDismiss: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var saving by remember { mutableStateOf(false) }
    var outcome by remember { mutableStateOf<SaveOutcome?>(null) }

    // data URI 先解成 Bitmap，Coil 不认这种写法
    val localBitmap = remember(model) {
        (model as? String)?.takeIf { it.startsWith("data:") }?.let { decodeDataUri(it) }
    }
    // 预览按高分辨率栅格化：SVG 默认只按屏幕尺寸解一次（1200px 左右），放大就糊了
    val request = remember(model, localBitmap) {
        when {
            localBitmap != null -> localBitmap
            model is String -> ImageRequest.Builder(context)
                .data(model)
                .size(3072, 3072)
                .crossfade(false)
                .build()

            else -> model
        }
    }

    Dialog(
        onDismissRequest = onDismiss,
        // decorFitsSystemWindows = false：让 dialog 真正铺满并把 insets 派发给 Compose，
        // 否则 navigationBars 在 dialog 里读出来是 0，按钮就会压在手势条上
        properties = DialogProperties(
            usePlatformDefaultWidth = false,
            decorFitsSystemWindows = false,
        ),
    ) {
        var scale by remember { mutableFloatStateOf(1f) }
        var offset by remember { mutableStateOf(Offset.Zero) }

        val transformState = rememberTransformableState { zoomChange, panChange, _ ->
            scale = (scale * zoomChange).coerceIn(1f, 6f)
            offset = if (scale <= 1f) Offset.Zero else offset + panChange
        }

        val navBottom = WindowInsets.navigationBars.asPaddingValues().calculateBottomPadding()
        val statusTop = WindowInsets.statusBars.asPaddingValues().calculateTopPadding()
        // 双保险：读到 0 时用固定下限兜住
        val bottomGap = maxOf(navBottom, 22.dp) + 14.dp
        val topGap = maxOf(statusTop, 18.dp) + 6.dp

        Box(modifier = Modifier.fillMaxSize().background(Color.White)) {
            AsyncImage(
                model = request,
                contentDescription = description,
                contentScale = ContentScale.Fit,
                modifier = Modifier
                    .fillMaxSize()
                    .graphicsLayer(
                        scaleX = scale,
                        scaleY = scale,
                        translationX = offset.x,
                        translationY = offset.y,
                    )
                    .transformable(transformState)
                    .pointerInput(Unit) {
                        detectTapGestures(
                            onDoubleTap = {
                                if (scale > 1f) {
                                    scale = 1f
                                    offset = Offset.Zero
                                } else {
                                    scale = 2.5f
                                }
                            },
                        )
                    },
            )

            IconButton(
                onClick = onDismiss,
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(top = topGap, end = 10.dp)
                    .background(Color.White.copy(alpha = 0.85f), CircleShape),
            ) {
                Icon(Icons.Rounded.Close, contentDescription = "关闭", tint = Color(0xFF333333))
            }

            val failed = outcome as? SaveOutcome.Failed
            Surface(
                onClick = {
                    if (saving || outcome != null) return@Surface
                    saving = true
                    scope.launch {
                        val result = withContext(Dispatchers.IO) { saveToGallery(context, model) }
                        saving = false
                        outcome = result
                        val toast = when (result) {
                            is SaveOutcome.InGallery -> "已保存到系统相册"
                            is SaveOutcome.InDownloads -> "已保存到「下载/AgentDeck」"
                            is SaveOutcome.Failed -> "保存失败：${result.reason}"
                        }
                        Toast.makeText(context, toast, Toast.LENGTH_SHORT).show()
                    }
                },
                shape = RoundedCornerShape(999.dp),
                color = if (failed != null) Color(0xFFFFF3F3) else Color.White,
                border = BorderStroke(
                    width = 1.dp,
                    color = if (failed != null) Color(0x33E5484D) else Color(0x22000000),
                ),
                shadowElevation = 8.dp,
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = bottomGap)
                    .heightIn(min = 48.dp),
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(horizontal = 20.dp),
                ) {
                    Icon(
                        imageVector = if (failed != null) Icons.Rounded.ErrorOutline else Icons.Rounded.DownloadDone,
                        contentDescription = null,
                        modifier = Modifier.size(17.dp),
                        tint = if (failed != null) Color(0xFFE5484D) else Color(0xFF444444),
                    )
                    Text(
                        text = "  " + when {
                            saving -> "保存中…"
                            outcome is SaveOutcome.InGallery -> "已保存到相册"
                            outcome is SaveOutcome.InDownloads -> "已保存到「下载」"
                            failed != null -> "保存失败，点我重试"
                            else -> "保存图片"
                        },
                        fontSize = 14.sp,
                        color = if (failed != null) Color(0xFFC22F35) else Color(0xFF333333),
                    )
                }
            }
        }
    }
}

/* ---------------- 保存 ---------------- */

private data class Fetched(val file: File, val mime: String?)

/**
 * 先把图落到缓存文件，再往相册写。
 *
 * 之前是「整包读成 Bitmap 再压 PNG」：SVG 解不出来直接返回 null、超大图容易 OOM，
 * 而且 webp/gif 会被重新编码得又慢又变样。改成原样搬字节，格式和体积都不动。
 */
private fun fetchToCache(context: Context, model: Any?, stamp: Long): Fetched? {
    return runCatching {
        val dir = File(context.cacheDir, "image-save").apply { mkdirs() }
        val file = File(dir, "src-$stamp.bin")
        when (model) {
            is Bitmap -> {
                file.outputStream().use { out -> model.compress(Bitmap.CompressFormat.PNG, 100, out) }
                Fetched(file, "image/png")
            }

            is String -> if (model.startsWith("data:")) {
                val payload = model.substringAfter("base64,", "")
                if (payload.isBlank()) return null
                val bytes = Base64.decode(payload, Base64.DEFAULT)
                file.writeBytes(bytes)
                val mime = model.removePrefix("data:")
                    .substringBefore(';')
                    .takeIf { it.startsWith("image/") }
                Fetched(file, mime)
            } else {
                val full = absoluteUrl(model) ?: return null
                val request = Request.Builder().url(full).header("Accept", "image/*,*/*").build()
                DeckApp.instance.api.httpClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        Log.w(TAG, "save image: HTTP ${response.code} -> $full")
                        return null
                    }
                    val body = response.body ?: return null
                    body.byteStream().use { input ->
                        file.outputStream().use { out -> input.copyTo(out, 64 * 1024) }
                    }
                    Fetched(file, response.header("Content-Type"))
                }
            }

            else -> null
        }
    }.getOrElse { error ->
        Log.w(TAG, "save image: fetch failed", error)
        null
    }
}

private fun absoluteUrl(url: String): String? = when {
    url.startsWith("http://") || url.startsWith("https://") -> url
    url.startsWith("/") -> runCatching {
        DeckApp.instance.api.baseUrl.trimEnd('/') + url
    }.getOrNull()

    else -> null
}

private suspend fun saveToGallery(context: Context, model: Any?): SaveOutcome {
    val stamp = System.currentTimeMillis()
    val fetched = fetchToCache(context, model, stamp)
        ?: return SaveOutcome.Failed("没拿到图片数据")
    return try {
        val source = (model as? String)?.takeIf { !it.startsWith("data:") }
        val (name, mime) = galleryFileName(source, fetched.mime, stamp)
        // 1) 先进系统相册
        DownloadSaver.saveImage(context, fetched.file, name, mime)?.let {
            return SaveOutcome.InGallery
        }
        Log.w(TAG, "save image: gallery refused, fallback to downloads")
        // 2) 相册被拒（权限 / ROM 不认 IS_PENDING）就退到「下载/AgentDeck」，和附件下载同一套落盘逻辑
        DownloadSaver.save(
            context,
            name,
            mime,
            fetched.file.inputStream(),
            fetched.file.length(),
        ) { _, _ -> }?.let {
            return SaveOutcome.InDownloads
        }
        SaveOutcome.Failed("系统不允许写入相册和下载目录")
    } catch (error: Exception) {
        Log.w(TAG, "save image: failed", error)
        SaveOutcome.Failed(error.message?.take(60) ?: error.javaClass.simpleName)
    } finally {
        runCatching { fetched.file.delete() }
    }
}
