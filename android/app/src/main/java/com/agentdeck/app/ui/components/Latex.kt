package com.agentdeck.app.ui.components

import android.graphics.Bitmap
import android.graphics.Canvas
import android.util.Log
import android.util.LruCache
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.systemGestureExclusion
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.derivedStateOf
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
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
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
 * 宽公式的横滑状态，由 [LatexImage] 写、[WideScrollHint] 读。
 *
 * 为什么要绕这一道：提示文字早先画在公式卡片**里面**（滚动区下方的常驻行），
 * 结果粉色卡片为了塞这行小字被撑高一条空白，字还紧贴在右下圆角上，看着像公式
 * 自带的一行脚注——用户的原话是「这里不太适配」。现在卡片只包公式，提示由调用
 * 方画在卡片外面（[WideScrollHint]），所以状态得往外提一层。
 */
class WideScrollState {
    /** 这张公式是否真的要横滑（装得下就没有提示）。 */
    var active by mutableStateOf(false)

    /** 是否还停在最左边：停着才提示「可以往右滑」，滑过就收起。 */
    var atStart by mutableStateOf(true)
}

/**
 * 宽公式的滑动提示：一行 10.5sp 小字，画在公式卡片**下方**。
 *
 * 高度在 [WideScrollState.active] 时恒定为 15dp：滑到底收起文字时不会把下面的
 * 内容顶上去（拖动途中跳一下比留着一条空白更难接受）。公式不宽时整行不存在，
 * 普通短公式下面不会多出空隙。
 */
@Composable
internal fun WideScrollHint(state: WideScrollState, palette: DeckPalette) {
    if (!state.active) return
    // 滑过之后不是「啪」一下消失，淡出更顺眼；高度始终占着，不影响下面的位置
    val alpha by animateFloatAsState(
        targetValue = if (state.atStart) 1f else 0f,
        label = "wide-hint",
    )
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(15.dp),
        contentAlignment = Alignment.CenterEnd,
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.graphicsLayer { this.alpha = alpha },
        ) {
            // 不用「↔」字符：中文/符号字体的回落字形比正文字号小一圈、基线还偏低，
            // 摆在「可左右滑动」旁边像掉下去半个字。自己画一个，粗细和字号都对得上。
            ScrollArrow(color = palette.inkSoft.copy(alpha = 0.8f))
            Spacer(Modifier.width(4.dp))
            Text(
                text = "可左右滑动",
                fontSize = 10.5.sp,
                color = palette.inkSoft.copy(alpha = 0.8f),
            )
        }
    }
}

/** 双向箭头（← →合在一起），给「可左右滑动」提示用。 */
@Composable
private fun ScrollArrow(
    color: Color,
    width: Dp = 12.dp,
    height: Dp = 8.dp,
) {
    Canvas(modifier = Modifier.size(width, height)) {
        val stroke = 1.1.dp.toPx()
        val cy = size.height / 2f
        val left = 0.55.dp.toPx()
        val right = size.width - 0.55.dp.toPx()
        val head = 2.2.dp.toPx()
        val wing = head * 0.72f
        drawLine(color, Offset(left, cy), Offset(right, cy), strokeWidth = stroke, cap = StrokeCap.Round)
        drawLine(color, Offset(left + head, cy - wing), Offset(left, cy), strokeWidth = stroke, cap = StrokeCap.Round)
        drawLine(color, Offset(left + head, cy + wing), Offset(left, cy), strokeWidth = stroke, cap = StrokeCap.Round)
        drawLine(color, Offset(right - head, cy - wing), Offset(right, cy), strokeWidth = stroke, cap = StrokeCap.Round)
        drawLine(color, Offset(right - head, cy + wing), Offset(right, cy), strokeWidth = stroke, cap = StrokeCap.Round)
    }
}

/**
 * 把一段 LaTeX 画成图片。
 *
 * [panWhenWide] 只给**独立成块的显示公式**（`$$...$$`）用：那种公式常常一路排到
 * 两三个屏宽，若按 `ContentScale.Fit` 缩进屏宽，一张 900px 高的式子会被压成
 * 一条几像素高的灰带，字全糊在一起（用户的原话是「挤在一条里，太小了看不清」）。
 * 打开后改为**按 1:1 原尺寸绘制，超宽时左右滑动**，和网页端 `overflow-x:auto` 一致。
 * 行内公式不能开：段落里一个公式跟着可左右滑，会和整页竖滑抢手感。
 *
 * [wideState] 传进来时会把「能不能滑 / 滑到哪」写进去，由调用方决定提示画在哪里。
 */
@Composable
fun LatexImage(
    latex: String,
    palette: DeckPalette,
    modifier: Modifier = Modifier,
    textSize: Int = 17,
    panWhenWide: Boolean = false,
    fadeColor: Color = Color.Transparent,
    wideState: WideScrollState? = null,
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
                    // 轻微超宽（缩不到 18%）就还按屏宽缩一缩：为此让用户滑一次不值得
                    val fitScale = if (wDp.value > 0f) maxWidth.value / wDp.value else 1f
                    val needsScroll = wDp > maxWidth && fitScale < 0.82f

                    val scroll = rememberScrollState()
                    val atStart by remember(scroll) {
                        derivedStateOf { scroll.value <= 0 }
                    }
                    val atEnd by remember(scroll) {
                        derivedStateOf { scroll.value >= scroll.maxValue }
                    }

                    // 把状态交给外面：提示画在卡片下方（见 WideScrollHint），不挤在卡片里。
                    // 注意这里必须先在本函数体里读一次 atStart——只在 SideEffect 的 lambda 里读
                    // 是不订阅的，滚动再多也不会重组，外面那行提示会一直挂着（真机上就是这个现象）。
                    val startNow = atStart
                    val scrollNow = needsScroll
                    SideEffect {
                        wideState?.active = scrollNow
                        wideState?.atStart = if (scrollNow) startNow else true
                    }

                    when {
                        // 装得下就原尺寸居中放，别给用户一个白滑的滚动条
                        !needsScroll && wDp <= maxWidth -> Image(
                            bitmap = image,
                            contentDescription = latex,
                            contentScale = ContentScale.Fit,
                        )

                        !needsScroll -> Image(
                            bitmap = image,
                            contentDescription = latex,
                            contentScale = ContentScale.Fit,
                            modifier = Modifier.size(maxWidth, (maxWidth.value * ratio).dp),
                        )

                        else -> Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                // 两侧渐隐直接在绘制阶段画。原来是用两个 fillMaxHeight() 的
                                // 兄弟 Box 叠上去——聊天列表是纵向无界的，父级 maxHeight 是
                                // Infinity，fillMaxHeight 拿不到高度，那两个渐变一直是 0 高，
                                // 屏幕上就是公式被硬切一刀（用户说的「像截断了」）。
                                .drawWithContent {
                                    drawContent()
                                    if (fadeColor.alpha > 0f) {
                                        val w = fadeWidth.toPx()
                                        // 末端必须画到完全不透明：只到 0.92 的话，裁切线那里还留着
                                        // 一层 8% 的墨迹，看着仍然是一刀切断的硬边。
                                        val fade = fadeColor.copy(alpha = 1f)
                                        if (!atEnd) {
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
                                        if (!atStart) {
                                            drawRect(
                                                brush = Brush.horizontalGradient(
                                                    listOf(fade, Color.Transparent),
                                                    startX = 0f,
                                                    endX = w,
                                                ),
                                                size = Size(w, size.height),
                                            )
                                        }
                                    }
                                },
                        ) {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .horizontalScroll(scroll)
                                    // 从屏幕边缘起手横滑时，别让系统的返回手势把它吃掉
                                    .systemGestureExclusion(),
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
