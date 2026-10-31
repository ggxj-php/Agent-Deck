package com.agentdeck.app.ui.components

import android.graphics.Bitmap
import android.graphics.Canvas
import android.util.LruCache
import androidx.compose.foundation.Image
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
import com.agentdeck.app.ui.theme.DeckPalette
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

    private fun key(latex: String, textSizePx: Int, color: Int) = "$textSizePx|$color|$latex"

    /** 只查缓存，不触发渲染：排版时用来拿准占位尺寸，避免文字跳一下。 */
    fun peek(latex: String, textSizePx: Int, color: Int): Bitmap? =
        synchronized(cache) { cache.get(key(latex, textSizePx, color)) }

    fun render(latex: String, textSizePx: Int, color: Int): Bitmap? {
        val cacheKey = key(latex, textSizePx, color)
        synchronized(cache) { cache.get(cacheKey) }?.let { return it }
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
            synchronized(cache) { cache.put(cacheKey, bitmap) }
            bitmap
        }.getOrNull()
    }
}

/* ------------------------------------------------------------------
 * LaTeX 清洗：JLaTeXMath 比 MathJax 挑剔，下面这些命令它认不了或者
 * 认了也没意义（编号、标签、排版微调），留着整条公式就会渲染失败、
 * 屏幕上露出一串 `\left` `\right`。它们不影响公式内容，删掉最安全。
 * ------------------------------------------------------------------ */

private val TAG_CMD = Regex("""\\tag\*?\s*\{[^{}]*}""")
private val LABEL_CMD = Regex("""\\label\s*\{[^{}]*}""")
private val BOOL_CMD = Regex("""\\(?:nonumber|notag|noindent|allowbreak|displaylimits)\b""")
private val SPACE_CMD = Regex("""\\(?:h|v)space\*?\s*\{[^{}]*}""")
private val ENV_OPEN = Regex("""\\begin\s*\{\s*(align|alignat|eqnarray|gather|equation|multline)\*?\s*}""")
private val ENV_CLOSE = Regex("""\\end\s*\{\s*(align|alignat|eqnarray|gather|equation|multline)\*?\s*}""")

/** 把一段 LaTeX 洗成 JLaTeXMath 能吃的样子；认不出来的命令一律保持原样，不吞内容。 */
internal fun sanitizeLatex(latex: String): String {
    var text = latex
    text = TAG_CMD.replace(text, "")
    text = LABEL_CMD.replace(text, "")
    text = BOOL_CMD.replace(text, "")
    text = SPACE_CMD.replace(text, " ")
    // align / gather 这类「带编号的显示公式环境」在数学模式里不被接受，
    // 换成内容等价、不编号的 aligned（cases、matrix 等本来就支持，不动）
    // 用 lambda 而不是字符串替换：替换串里的 "\b" 会被当成退格转义，反斜杠会丢掉
    text = ENV_OPEN.replace(text) { """\begin{aligned}""" }
    text = ENV_CLOSE.replace(text) { """\end{aligned}""" }
    // 只剩对齐符没法验算的奇数行：aligned 里 & 多了会报错，简单兜一下
    text = text.replace(Regex("""&{3,}"""), "&")
    return text.trim()
}

@Composable
fun LatexImage(
    latex: String,
    palette: DeckPalette,
    modifier: Modifier = Modifier,
    textSize: Int = 17,
) {
    val density = LocalDensity.current
    val sizePx = with(density) { textSize.sp.toPx() }.toInt()
    val color = palette.ink.toArgbColor()
    val source = remember(latex) { sanitizeLatex(latex) }

    var bitmap by remember(source, sizePx, color) { mutableStateOf(LatexRenderer.peek(source, sizePx, color)) }
    var failed by remember(source, sizePx, color) { mutableStateOf(false) }

    LaunchedEffect(source, sizePx, color) {
        if (bitmap != null) return@LaunchedEffect
        val result = withContext(Dispatchers.Default) {
            LatexRenderer.render(source, sizePx, color)
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

internal fun androidx.compose.ui.graphics.Color.toArgbColor(): Int = android.graphics.Color.argb(
    (alpha * 255).toInt(),
    (red * 255).toInt(),
    (green * 255).toInt(),
    (blue * 255).toInt(),
)

/**
 * 渲染失败时的兜底：把 LaTeX 命令换成可读的 Unicode（和网页端同一套规则）。
 *
 * 注意这里必须调 [demathExpr] 而不是 [demath]——传进来的是**裸 LaTeX**
 * （MathBlock 里没有 `$` 定界符），而 demath 只处理带 `$` 的片段，
 * 调它等于什么都没做，`\left(` `\right)` 会原样印在屏幕上。
 */
internal fun demathForFallback(latex: String): String = runCatching {
    demathExpr(sanitizeLatex(latex))
        .replace(Regex("""\s+"""), " ")
        .trim()
        .ifBlank { latex }
}.getOrElse { latex }
