package com.agentdeck.app.ui.components

import android.graphics.Bitmap
import android.graphics.Canvas
import android.util.Log
import android.util.LruCache
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
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

private class LatexRule(val pattern: Regex, val replacement: String)

/**
 * 清洗规则表。
 *
 * 两个坑记在这里，别再踩一遍：
 * 1. 花括号一律写成 `[{]` / `[}]`。Android 走的是 ICU 正则，比 JVM 严——
 *    虽然接受 `\{`、`\}`，但**裸露的 `}` 会直接抛 PatternSyntaxException**；
 *    单元测试跑在 JVM 上发现不了，装到手机上就是 `ExceptionInInitializerError` 整页崩溃。
 * 2. 规则在这里就尝试编译，编不过只跳过这一条。宁可少清洗一条，也不能让整个 App 起不来。
 */
private val LATEX_RULES: List<LatexRule> = buildList {
    fun rule(pattern: String, replacement: String) {
        runCatching { Regex(pattern) }
            .getOrNull()
            ?.let { add(LatexRule(it, replacement)) }
            ?: Log.w("AgentDeck", "latex rule skipped (bad pattern): $pattern")
    }
    rule("""\\tag\*?\s*[{][^{}]*[}]""", "")
    rule("""\\label\s*[{][^{}]*[}]""", "")
    rule("""\\(?:nonumber|notag|noindent|allowbreak|displaylimits)\b""", "")
    rule("""\\(?:h|v)space\*?\s*[{][^{}]*[}]""", " ")
    // align / gather 这类「带编号的显示公式环境」在数学模式里不被接受，
    // 换成内容等价、不编号的 aligned（cases、matrix 等本来就支持，不动）
    rule(
        """\\begin\s*[{]\s*(align|alignat|eqnarray|gather|equation|multline)\*?\s*[}]""",
        """\begin{aligned}""",
    )
    rule(
        """\\end\s*[{]\s*(align|alignat|eqnarray|gather|equation|multline)\*?\s*[}]""",
        """\end{aligned}""",
    )
}

/**
 * 去掉 `\\[4pt]` 里那对方括号（保留 `\\` 换行本身）。
 *
 * `\\[4pt]` 是 LaTeX 的「换行 + 额外竖直间距」，模型在 cases / aligned 里特别爱写：
 *
 * ```
 * f(y)=\begin{cases}0, & y<0\\[2pt] u, & 0\le y<8\\[4pt] 1, & y\ge 8\end{cases}
 * ```
 *
 * JLaTeXMath 的 `\\` 不认这个可选参数，`[4pt]` 会被当成正文排进公式、原样印在屏幕上
 * （`1, & y\ge 8[4pt]`），看着像渲染坏了。这点间距纯粹是排版微调，丢掉不影响语义。
 *
 * 为什么手写扫描而不用正则：Android 走的是 ICU 正则，比 JVM 严，`\[` `\]` 这种写法
 * 容易在真机上栽跟头（本文件顶部那两个坑就是这么来的）。这里是纯字符串处理，
 * 不编译正则，也就不会在低版本机型上炸。
 */
internal fun stripLatexLineGaps(source: String): String {
    // 快速通道：连 `\\`（换行符本身）都没有的公式直接返回。
    // 注意判的是两个反斜杠——`\\*[4pt]` 这种中间还夹了个星号，`\[` 在这里出现不了。
    if (!source.contains("\\\\")) return source
    val out = StringBuilder(source.length)
    var i = 0
    while (i < source.length) {
        // 只处理「两个反斜杠」开头的换行符，单个 `\[` 是行间公式定界符，不能动
        if (source[i] == '\\' && i + 1 < source.length && source[i + 1] == '\\') {
            out.append("\\\\")
            i += 2
            if (i < source.length && source[i] == '*') { // `\\*[4pt]` 变体
                out.append('*')
                i++
            }
            if (i < source.length && source[i] == '[') {
                val close = source.indexOf(']', i)
                // [4pt] / [1.5ex] / [-2pt]：只认「有数字、只有数字和单位字母」的短括号
                if (close in (i + 2) until minOf(source.length, i + 12)) {
                    val body = source.substring(i + 1, close)
                    val looksLikeGap = body.any { it.isDigit() } &&
                        body.all { it.isDigit() || it.isLetter() || it == '.' || it == '-' || it == ' ' }
                    if (looksLikeGap) {
                        i = close + 1
                        continue
                    }
                }
            }
            continue
        }
        out.append(source[i])
        i++
    }
    return out.toString()
}

/** 把一段 LaTeX 洗成 JLaTeXMath 能吃的样子；认不出来的命令一律保持原样，不吞内容。 */
internal fun sanitizeLatex(latex: String): String {
    var text = stripLatexLineGaps(latex)
    for (rule in LATEX_RULES) {
        // 用 lambda 替换：替换串里的 "\b" 会被当成转义，反斜杠会丢
        text = rule.pattern.replace(text) { rule.replacement }
    }
    // 只剩对齐符没法验算的奇数行：aligned 里 & 多了会报错，简单兜一下
    text = text.replace(Regex("&{3,}"), "&")
    return text.trim()
}

/** 宽公式左右两端「还有内容」的渐隐宽度。 */
private val fadeWidth = 18.dp

/**
 * 宽公式的状态：要不要在卡片下方提示「点击放大查看」。
 *
 * 为什么不再做行内横滑：`horizontalScroll` 嵌在 `LazyColumn` 里，两套 scrollable
 * 靠 touchSlop 的方向判定争手势——手指起手带一点竖直分量、或者按在卡片留白处
 * （根本没落在滚动区里），事件就被纵向列表拿走了，表现就是「有时候根本划不动」，
 * 而且 `systemGestureExclusion` 在全面屏手势下也未必保得住边缘那几十 dp。
 * 现在改成点一下开全屏，全屏里只有公式自己，没有纵向列表抢手势。
 */
class WideFormulaState {
    /** 这张公式是否超宽（只有超宽时才值得点开放大）。 */
    var active by mutableStateOf(false)
}

/**
 * 宽公式的放大提示：一行 10.5sp 小字 + 自绘放大镜，画在公式卡片**下方**。
 *
 * 高度恒定为 20dp，公式不宽时整行不存在，普通短公式下面不会多出空隙。
 * 点区比字形大一圈（外面 3dp、右侧 6dp 的内边距都算在 clickable 里）：
 * 一行 11dp 高的灰字太难点中，用户会以为「点了没反应」，而实际上只是差两个像素。
 */
@Composable
internal fun WideFormulaHint(
    state: WideFormulaState,
    palette: DeckPalette,
    onClick: () -> Unit,
) {
    if (!state.active) return
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(20.dp),
        contentAlignment = Alignment.CenterEnd,
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier
                .clickable(onClick = onClick)
                .padding(horizontal = 6.dp, vertical = 3.dp),
        ) {
            ZoomIcon(color = palette.inkSoft.copy(alpha = 0.8f))
            Spacer(Modifier.width(4.dp))
            Text(
                text = "点击放大查看",
                fontSize = 10.5.sp,
                color = palette.inkSoft.copy(alpha = 0.8f),
            )
        }
    }
}

/** 放大镜图标：一个圆 + 斜柄 + 圆内一个「+」。 */
@Composable
private fun ZoomIcon(
    color: Color,
    size: Dp = 11.dp,
) {
    Canvas(modifier = Modifier.size(size)) {
        val sw = 1.1.dp.toPx()
        val r = this.size.width * 0.31f
        val c = Offset(this.size.width * 0.42f, this.size.height * 0.42f)
        drawCircle(color, radius = r, center = c, style = Stroke(width = sw))
        // 手柄：从圆的 45° 方向往外
        val d = r * 0.72f
        drawLine(
            color,
            Offset(c.x + d, c.y + d),
            Offset(this.size.width * 0.94f, this.size.height * 0.94f),
            strokeWidth = sw,
            cap = StrokeCap.Round,
        )
        // 圆里的加号
        val arm = r * 0.45f
        drawLine(color, Offset(c.x - arm, c.y), Offset(c.x + arm, c.y), strokeWidth = sw * 0.9f, cap = StrokeCap.Round)
        drawLine(color, Offset(c.x, c.y - arm), Offset(c.x, c.y + arm), strokeWidth = sw * 0.9f, cap = StrokeCap.Round)
    }
}

/**
 * 把一段 LaTeX 画成图片。
 *
 * [panWhenWide] 只给**独立成块的显示公式**（`$$...$$`）用：那种公式常常一路排到
 * 两三个屏宽，若按 `ContentScale.Fit` 缩进屏宽，一张 900px 高的式子会被压成
 * 一条几像素高的灰带，字全糊在一起（用户的原话是「挤在一条里，太小了看不清」）。
 * 打开后**按 1:1 原尺寸绘制**，超出屏宽的部分裁掉、右端渐隐，并提示「点击放大查看」。
 *
 * 早先是就地横滑（`horizontalScroll`），但在聊天列表里经常划不动：横向滚动和
 * `LazyColumn` 的纵向滚动要靠 touchSlop 的方向判定争手势，起手稍微斜一点、或者
 * 按在卡片留白处（没落进滚动区），事件就被列表拿走了。现在改成点开全屏看，
 * 全屏里只有公式自己，手势不再打架。
 *
 * [wideState] 传进来时会把「是不是超宽」写进去，由调用方决定提示画在哪里、能不能点。
 */
@Composable
fun LatexImage(
    latex: String,
    palette: DeckPalette,
    modifier: Modifier = Modifier,
    textSize: Int = 17,
    panWhenWide: Boolean = false,
    fadeColor: Color = Color.Transparent,
    wideState: WideFormulaState? = null,
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
        bitmap != null -> {
            val image = bitmap!!.asImageBitmap()
            if (!panWhenWide) {
                Image(
                    bitmap = image,
                    contentDescription = latex,
                    contentScale = ContentScale.Fit,
                    // 不再用 wrapContentSize：那个会让位图突破父级约束（宽公式顶出表格单元格）。
                    // 交给 Image 按 intrinsic 尺寸测量、超宽时等比缩小，表格/段落都不会被撑破。
                    modifier = modifier,
                )
            } else {
                BoxWithConstraints(
                    modifier = modifier.fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    // 位图是按 textSize(sp)×density 画的，这里原样换成 dp，就是「1:1 不缩放」
                    val wDp = with(density) { bitmap!!.width.toDp() }
                    val hDp = with(density) { bitmap!!.height.toDp() }
                    val ratio = if (wDp.value > 0f) hDp.value / wDp.value else 1f
                    // 按屏宽能缩到几成
                    val fitScale = if (wDp.value > 0f) maxWidth.value / wDp.value else 1f
                    // 渲染路径用「缩不下 18%」这个偏保守的门槛：只超一点的式子按屏宽
                    // 缩一缩还看得清，为它截掉右边反而浪费空间。
                    val needsExpand = wDp > maxWidth && fitScale < 0.82f
                    // 但「要不要提示能放大」不能跟它用同一个门槛。早先两处共用一个值，
                    // 于是比卡片宽一点点（1.0~1.2 倍）的式子既不提示、也点不动——
                    // 用户看着和别的卡片一模一样，点下去没反应，就是「有的好使有的不好使」。
                    // 现在只要超出可用宽度就提示，窄的虽不提示但也能点。
                    val overflow = wDp > maxWidth

                    SideEffect { wideState?.active = overflow }

                    when {
                        // 装得下就原尺寸居中放
                        !needsExpand && wDp <= maxWidth -> Image(
                            bitmap = image,
                            contentDescription = latex,
                            contentScale = ContentScale.Fit,
                        )

                        !needsExpand -> Image(
                            bitmap = image,
                            contentDescription = latex,
                            contentScale = ContentScale.Fit,
                            modifier = Modifier.size(maxWidth, (maxWidth.value * ratio).dp),
                        )

                        // 超宽：按 1:1 画、右边裁掉一截（右侧渐隐），点一下开全屏看。
                        // 这里再也不放 horizontalScroll——列表里那一层纵向滚动会把它抢掉。
                        else -> Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(hDp)
                                .clipToBounds()
                                .drawWithContent {
                                    drawContent()
                                    if (fadeColor.alpha > 0f) {
                                        val w = fadeWidth.toPx()
                                        // 末端画到完全不透明：只到 0.92 的话裁切线那里还留着
                                        // 一层 8% 的墨迹，看着仍然像被硬切了一刀
                                        val fade = fadeColor.copy(alpha = 1f)
                                        drawRect(
                                            brush = Brush.horizontalGradient(
                                                listOf(Color.Transparent, fade),
                                                startX = size.width - w,
                                                endX = size.width,
                                            ),
                                            topLeft = Offset(size.width - w, 0f),
                                            size = Size(w, size.height),
                                        )
                                    }
                                },
                        ) {
                            Image(
                                bitmap = image,
                                contentDescription = latex,
                                contentScale = ContentScale.Fit,
                                modifier = Modifier.size(wDp, hDp),
                            )
                        }
                    }
                }
            }
        }

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
