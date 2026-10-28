package icu.taffy.deck.ui.components

import android.graphics.Bitmap
import android.graphics.Canvas
import android.util.LruCache
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.wrapContentSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.Image
import icu.taffy.deck.ui.theme.DeckPalette
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import ru.noties.jlatexmath.JLatexMathDrawable

/**
 * 原生 LaTeX 渲染：用 JLaTeXMath 把公式画成 Bitmap，再交给 Compose 的 Image。
 * 不依赖 WebView，也就能保持滚动流畅。
 *
 * 渲染失败（比如模型写了不合法 LaTeX）时退回「Unicode 化」的纯文本，不至于整块消失。
 */
object LatexRenderer {

    private val cache = object : LruCache<String, Bitmap>(48) {}

    fun render(latex: String, textSizePx: Int, color: Int): Bitmap? {
        val key = "$textSizePx|$color|$latex"
        synchronized(cache) { cache.get(key) }?.let { return it }
        return runCatching {
            val drawable = JLatexMathDrawable.builder(latex)
                .textSize(textSizePx.toFloat())
                .color(color)
                .build()
            val width = drawable.intrinsicWidth
            val height = drawable.intrinsicHeight
            if (width <= 0 || height <= 0) return null
            val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
            drawable.setBounds(0, 0, width, height)
            drawable.draw(Canvas(bitmap))
            synchronized(cache) { cache.put(key, bitmap) }
            bitmap
        }.getOrNull()
    }
}

@Composable
fun LatexImage(
    latex: String,
    palette: DeckPalette,
    modifier: Modifier = Modifier,
) {
    val density = LocalDensity.current
    val sizePx = with(density) { 17.sp.toPx() }.toInt()
    val color = palette.ink.toArgbColor()

    var bitmap by remember(latex, sizePx, color) { mutableStateOf<Bitmap?>(null) }
    var failed by remember(latex, sizePx, color) { mutableStateOf(false) }

    LaunchedEffect(latex, sizePx, color) {
        val result = withContext(Dispatchers.Default) {
            LatexRenderer.render(latex, sizePx, color)
        }
        if (result == null) failed = true else bitmap = result
    }

    when {
        bitmap != null -> Image(
            bitmap = bitmap!!.asImageBitmap(),
            contentDescription = latex,
            contentScale = ContentScale.Fit,
            modifier = modifier.wrapContentSize(),
        )

        failed -> Text(
            text = demathForFallback(latex),
            fontSize = 14.5.sp,
            color = palette.ink,
            modifier = modifier.padding(vertical = 2.dp),
        )

        else -> Box(modifier = modifier.height(22.dp))
    }
}

private fun androidx.compose.ui.graphics.Color.toArgbColor(): Int = android.graphics.Color.argb(
    (alpha * 255).toInt(),
    (red * 255).toInt(),
    (green * 255).toInt(),
    (blue * 255).toInt(),
)

/** 渲染失败时的兜底：把 LaTeX 命令换成可读的 Unicode（和网页端同一套规则）。 */
internal fun demathForFallback(latex: String): String {
    var text = latex
    text = text.replace(Regex("""\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)}"""), "$1")
    text = text.replace(Regex("""\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)}"""), "($1)/($2)")
    text = text.replace(Regex("""\\sqrt\s*\{([^{}]*)}"""), "√($1)")
    text = text.replace(Regex("""_\s*\{([^{}]*)}"""), "_($1)")
    text = text.replace(Regex("""\^\s*\{([^{}]*)}"""), "^($1)")
    return demath(text)
}
