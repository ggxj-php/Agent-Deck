package com.agentdeck.app.ui.components

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.RectF
import android.util.Base64
import android.util.Log
import android.widget.Toast
import androidx.activity.compose.BackHandler
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
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
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.agentdeck.app.DeckApp
import com.caverock.androidsvg.SVG
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import java.io.ByteArrayInputStream
import java.io.File
import kotlin.math.roundToInt

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
 * 全屏看图的状态槽。
 *
 * **为什么不用 Dialog 了**：Dialog 是另一个 window，`decorFitsSystemWindows = false` 之后
 * 窗口高度在部分 ROM（Android 15/16 的机型上实测）会算多一截，底部按钮被顶到屏幕外面，
 * 只剩 7/8 露出来。改成挂在 Activity 自己的窗口上（跟聊天界面同一个 window，insets 一定
 * 准），位置就再也不会算歪；顺带返回键也能正常关闭。
 */
object ImagePreview {
    var current by mutableStateOf<String?>(null)
        private set

    fun show(url: String?) {
        if (!url.isNullOrBlank()) current = url
    }

    fun dismiss() {
        current = null
    }
}

/** 挂在页面最外层（MainActivity 的 Surface 里），有图要看时才铺满整屏。 */
@Composable
fun ImagePreviewHost() {
    val url = ImagePreview.current ?: return
    ImagePreviewOverlay(url = url, onDismiss = ImagePreview::dismiss)
}

/**
 * 全屏看图：白底、双指缩放、拖动、双击放大，底部可以把当前图保存到相册。
 *
 * 底部按钮用 `safeDrawing` 的内边距——它同时包含手势条、导航栏和刘海，比自己猜
 * 一个固定值靠谱；图片本身仍然铺满整屏，只有按钮和关闭键让开系统区域。
 */
@Composable
private fun ImagePreviewOverlay(
    url: String,
    onDismiss: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var saving by remember(url) { mutableStateOf(false) }
    var outcome by remember(url) { mutableStateOf<SaveOutcome?>(null) }
    var scale by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }

    BackHandler(enabled = true) { onDismiss() }

    // data URI 先解成 Bitmap，Coil 不认这种写法
    val localBitmap = remember(url) {
        url.takeIf { it.startsWith("data:") }?.let { decodeDataUri(it) }
    }
    // 预览按高分辨率栅格化：SVG 默认只按屏幕尺寸解一次（1200px 左右），放大就糊了
    val request = remember(url, localBitmap) {
        if (localBitmap != null) {
            localBitmap
        } else {
            ImageRequest.Builder(context)
                .data(url)
                .size(3072, 3072)
                .crossfade(false)
                .build()
        }
    }

    val transformState = rememberTransformableState { zoomChange, panChange, _ ->
        scale = (scale * zoomChange).coerceIn(1f, 6f)
        offset = if (scale <= 1f) Offset.Zero else offset + panChange
    }

    val safe = WindowInsets.safeDrawing.asPaddingValues()
    val bottomGap = safe.calculateBottomPadding() + 14.dp
    val topGap = safe.calculateTopPadding() + 6.dp

    Box(modifier = Modifier.fillMaxSize().background(Color.White)) {
        AsyncImage(
            model = request,
            contentDescription = null,
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
                .pointerInput(url) {
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
                    val result = withContext(Dispatchers.IO) { saveToGallery(context, url) }
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

/** 产物图是 SVG 时，字节里有 `<svg`（可能是 `<?xml` 开头），按 SVG 处理。 */
private fun looksLikeSvg(file: File): Boolean = runCatching {
    val head = ByteArray(1024)
    val read = file.inputStream().use { it.read(head) }
    if (read <= 0) return false
    val text = String(head, 0, read, Charsets.UTF_8).trimStart()
    text.startsWith("<svg") || (text.startsWith("<?xml") && text.contains("<svg"))
}.getOrDefault(false)

/** viewBox 里的宽高（`width="100%"` 这种拿不到绝对尺寸时靠它）。 */
private val VIEWBOX = Regex("""viewBox\s*=\s*"\s*-?[\d.]+[\s,]+-?[\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)""")

/**
 * SVG 栅格化成 PNG，直接覆盖回同一个文件。
 *
 * 相册/相册类 App 都不认 SVG，直接存字节进去就是一张白图——所以保存前先转成位图。
 * 用 androidsvg（Coil 用的就是它）渲染，尺寸按 2200px 宽出图，够清晰也不至于 OOM。
 */
private fun rasterizeSvg(file: File): Boolean = runCatching {
    val bytes = file.readBytes()
    val svg = SVG.getFromInputStream(ByteArrayInputStream(bytes))
    var width = svg.documentWidth
    var height = svg.documentHeight
    if (width <= 1f || height <= 1f) {
        val match = VIEWBOX.find(String(bytes, Charsets.UTF_8)) ?: return false
        width = match.groupValues[1].toFloatOrNull() ?: return false
        height = match.groupValues[2].toFloatOrNull() ?: return false
    }
    if (width <= 0f || height <= 0f) return false

    val scale = (2200f / width).coerceAtLeast(1f)
    val outW = (width * scale).roundToInt().coerceIn(64, 4096)
    val outH = (height * scale).roundToInt().coerceIn(64, 8192)
    val bitmap = Bitmap.createBitmap(outW, outH, Bitmap.Config.ARGB_8888)
    try {
        val canvas = Canvas(bitmap)
        canvas.drawColor(android.graphics.Color.WHITE)      // 透明底在图库里会变成黑
        svg.renderToCanvas(canvas, RectF(0f, 0f, outW.toFloat(), outH.toFloat()))
        file.outputStream().use { out ->
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)
        }
    } finally {
        bitmap.recycle()
    }
    true
}.getOrElse { error ->
    Log.w(TAG, "save image: rasterize svg failed", error)
    false
}

private suspend fun saveToGallery(context: Context, model: Any?): SaveOutcome {
    val stamp = System.currentTimeMillis()
    var fetched = fetchToCache(context, model, stamp)
        ?: return SaveOutcome.Failed("没拿到图片数据")
    // 矢量图存进相册会是空白，先转成 PNG（产物图正常走的是服务端的 /png，这里是兜底）
    if (looksLikeSvg(fetched.file) && rasterizeSvg(fetched.file)) {
        fetched = Fetched(fetched.file, "image/png")
    }
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
