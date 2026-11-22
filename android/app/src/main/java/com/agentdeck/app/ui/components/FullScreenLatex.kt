package com.agentdeck.app.ui.components

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.systemGestureExclusion
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.agentdeck.app.ui.theme.DeckPalette
import com.agentdeck.app.ui.theme.LocalPalette
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * 全屏看公式的状态槽，和 [TablePreview]、[ImagePreview] 同一个套路：
 * 挂在 Activity 自己的窗口上，不另开 Dialog（Dialog 是独立窗口，insets 在部分 ROM 会算歪）。
 */
object LatexPreview {
    var current by mutableStateOf<String?>(null)
        private set

    fun show(latex: String) {
        if (latex.isBlank()) return
        current = latex
    }

    fun dismiss() {
        current = null
    }
}

/** 挂在页面最外层，有公式要看时铺满整屏。 */
@Composable
fun LatexPreviewHost() {
    val latex = LatexPreview.current ?: return
    FullScreenLatex(latex = latex, onDismiss = LatexPreview::dismiss)
}

/** 字号档位（sp）。默认第 0 档＝消息里那个大小，也就是 1:1 原样，先保证清晰。 */
private val LATEX_FONT_STEPS = listOf(17, 22, 28, 36)

/**
 * 全屏公式：白底、双向滚动、字号可调。
 *
 * 为什么不在这里做双指缩放：`detectTransformGestures` 单指时也会上报 pan，
 * 和外层两个方向的滚动抢手势，表现是「滑不动」或者「一滑就跳」——表格全屏当初
 * 也是这个坑，最后用字号档位替代。公式这边同理，A+ 档会**按更大的字号重新渲染
 * 位图**（不是把旧图拉大），所以放大后线条依旧锐利。
 */
@Composable
private fun FullScreenLatex(latex: String, onDismiss: () -> Unit) {
    val palette = LocalPalette.current
    val density = LocalDensity.current

    var step by remember(latex) { mutableIntStateOf(0) }
    val textSize = LATEX_FONT_STEPS[step.coerceIn(0, LATEX_FONT_STEPS.lastIndex)]
    val sizePx = with(density) { textSize.sp.toPx() }.toInt()
    val color = palette.ink.toArgbColor()
    val source = remember(latex) { sanitizeLatex(latex) }

    // 命中缓存就直接用（消息里那张就是按 17sp 渲染的，进全屏不该再等一次）
    var bitmap by remember(source, sizePx, color) {
        mutableStateOf(LatexRenderer.peek(source, sizePx, color))
    }
    var failed by remember(source, sizePx, color) { mutableStateOf(false) }
    LaunchedEffect(source, sizePx, color) {
        if (bitmap != null) return@LaunchedEffect
        val result = withContext(Dispatchers.Default) { LatexRenderer.render(source, sizePx, color) }
        if (result == null) failed = true else bitmap = result
    }

    val safe = WindowInsets.safeDrawing.asPaddingValues()
    val vScroll = rememberScrollState()
    val hScroll = rememberScrollState()

    BackHandler(enabled = true) { onDismiss() }

    Box(modifier = Modifier.fillMaxSize().background(palette.bg)) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(top = safe.calculateTopPadding()),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 16.dp, end = 4.dp, top = 2.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "公式",
                    fontSize = 15.sp,
                    fontWeight = FontWeight.W600,
                    color = palette.ink,
                )
                Spacer(Modifier.weight(1f))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    LatexFontChip("A-", enabled = step > 0, palette = palette) {
                        step = (step - 1).coerceAtLeast(0)
                    }
                    LatexFontChip("A+", enabled = step < LATEX_FONT_STEPS.lastIndex, palette = palette) {
                        step = (step + 1).coerceAtMost(LATEX_FONT_STEPS.lastIndex)
                    }
                }
                IconButton(onClick = onDismiss) {
                    Icon(Icons.Rounded.Close, contentDescription = "关闭", tint = palette.ink)
                }
            }

            BoxWithConstraints(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(bottom = safe.calculateBottomPadding()),
            ) {
                val bmp = bitmap
                if (bmp == null) {
                    if (failed) {
                        // 渲染不出来就退回清洗后的文本，至少不是一片空白
                        Text(
                            text = demathForFallback(latex),
                            fontSize = 15.sp,
                            color = palette.ink,
                            modifier = Modifier
                                .align(Alignment.TopCenter)
                                .padding(20.dp),
                        )
                    }
                    return@BoxWithConstraints
                }

                val wDp = with(density) { bmp.width.toDp() }
                val hDp = with(density) { bmp.height.toDp() }
                // 左右各 12dp 是滚动容器外的边距，扣掉再判，不然刚好卡边界时会被裁一点
                val viewport = maxWidth - 24.dp
                val wide = wDp > viewport

                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(start = 12.dp, end = 12.dp, top = 12.dp, bottom = 14.dp)
                        .verticalScroll(vScroll)
                        .then(
                            if (wide) {
                                Modifier
                                    .horizontalScroll(hScroll)
                                    // 从屏幕边缘起手横滑时，别让系统的返回手势把它吃掉
                                    .systemGestureExclusion()
                            } else {
                                Modifier
                            },
                        ),
                    // 水平方向：宽了就顶着左边（两头都靠滑），窄了居中；
                    // 垂直方向始终居中——公式通常比屏幕矮很多，贴着顶栏看很别扭
                    contentAlignment = if (wide) Alignment.CenterStart else Alignment.Center,
                ) {
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(10.dp))
                            .border(1.dp, palette.line, RoundedCornerShape(10.dp))
                            .background(palette.softFill)
                            .padding(14.dp),
                    ) {
                        Image(
                            bitmap = bmp.asImageBitmap(),
                            contentDescription = latex,
                            contentScale = ContentScale.Fit,
                            modifier = Modifier.size(wDp, hDp),
                        )
                    }
                }
            }
        }

        // 底部一句话说清能干什么；只出现一次，不挡内容
        Text(
            text = if (failed) "" else "A+ 字号更大，放不下时可以左右 / 上下滑动",
            fontSize = 11.sp,
            color = palette.inkSoft,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(bottom = safe.calculateBottomPadding() + 6.dp),
        )
    }
}

/** 顶栏那颗字号小胶囊（和表格全屏同款）。 */
@Composable
private fun LatexFontChip(
    label: String,
    enabled: Boolean,
    palette: DeckPalette,
    onClick: () -> Unit,
) {
    Surface(
        onClick = onClick,
        enabled = enabled,
        shape = CircleShape,
        color = if (enabled) palette.softFill else palette.bg,
        border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
        modifier = Modifier.width(40.dp),
    ) {
        Box(contentAlignment = Alignment.Center, modifier = Modifier.padding(vertical = 5.dp)) {
            Text(
                text = label,
                fontSize = 12.sp,
                color = if (enabled) palette.ink else palette.inkSoft.copy(alpha = 0.5f),
            )
        }
    }
}
