package icu.taffy.deck.ui.components

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
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
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Image
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import icu.taffy.deck.data.Persona
import icu.taffy.deck.ui.theme.DeckPalette
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import kotlin.math.max
import kotlin.math.min

/**
 * 底部输入区，尺寸对齐网页端：44dp 圆形「+」、22dp 圆角输入框、44dp 渐变发送按钮。
 */
@Composable
fun Composer(
    busy: Boolean,
    persona: Persona,
    palette: DeckPalette,
    onSend: (String, List<String>) -> Unit,
    onStop: () -> Unit,
) {
    var text by rememberSaveable { mutableStateOf("") }
    var image by remember { mutableStateOf<String?>(null) }
    var preparing by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            preparing = true
            val dataUrl = withContext(Dispatchers.IO) { toDataUrl(context, uri) }
            preparing = false
            if (dataUrl != null) image = dataUrl
        }
    }

    val canSend = !busy && !preparing && (text.isNotBlank() || image != null)
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

        if (image != null || preparing) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 9.dp),
            ) {
                Surface(
                    color = palette.softFill,
                    shape = RoundedCornerShape(14.dp),
                    border = BorderStroke(1.dp, palette.line),
                    modifier = Modifier.size(72.dp),
                ) {
                    if (preparing) {
                        Box(contentAlignment = Alignment.Center) {
                            CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                        }
                    } else {
                        AsyncImage(
                            model = image,
                            contentDescription = "待发送的图片",
                            contentScale = ContentScale.Crop,
                            modifier = Modifier
                                .size(72.dp)
                                .clip(RoundedCornerShape(14.dp)),
                        )
                    }
                }
                Spacer(Modifier.width(10.dp))
                Text("发送这张图", fontSize = 12.sp, color = palette.inkSoft)
                Spacer(Modifier.weight(1f))
                Surface(
                    onClick = { image = null },
                    enabled = !busy,
                    shape = CircleShape,
                    color = Color.White.copy(alpha = 0.9f),
                    border = BorderStroke(1.dp, palette.line),
                    modifier = Modifier.size(26.dp),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(
                            Icons.Rounded.Close,
                            contentDescription = "移除图片",
                            modifier = Modifier.size(14.dp),
                            tint = palette.inkSoft,
                        )
                    }
                }
            }
        }

        Row(
            verticalAlignment = Alignment.Bottom,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 9.dp),
        ) {
            Surface(
                onClick = { picker.launch("image/*") },
                enabled = !busy,
                shape = CircleShape,
                color = palette.softFill,
                border = BorderStroke(1.dp, palette.line),
                modifier = Modifier.size(44.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(
                        Icons.Rounded.Image,
                        contentDescription = "发图片",
                        modifier = Modifier.size(20.dp),
                        tint = palette.inkSoft,
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
                        onSend(text.trim(), listOfNotNull(image))
                        text = ""
                        image = null
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

/** 最长边压到 1600px、JPEG 85，单张控制在 2MB 内再上传。 */
private fun toDataUrl(context: Context, uri: Uri): String? {
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
