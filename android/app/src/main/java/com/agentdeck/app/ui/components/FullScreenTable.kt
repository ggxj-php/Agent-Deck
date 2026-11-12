package com.agentdeck.app.ui.components

import androidx.activity.compose.BackHandler
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
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
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontSynthesis
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.agentdeck.app.ui.theme.LocalPalette

/**
 * 全屏看表格的状态槽，和 [ImagePreview] 一个套路：挂在 Activity 自己的窗口上，
 * 不另开 Dialog（Dialog 是独立窗口，insets 在部分 ROM 会算歪）。
 */
object TablePreview {
    var current by mutableStateOf<MdBlock.Table?>(null)
        private set

    fun show(table: MdBlock.Table) {
        if (table.header.isEmpty() && table.rows.isEmpty()) return
        current = table
    }

    fun dismiss() {
        current = null
    }
}

/** 挂在页面最外层，有表格要放大看时才铺满整屏。 */
@Composable
fun TablePreviewHost() {
    val table = TablePreview.current ?: return
    FullScreenTable(table = table, onDismiss = TablePreview::dismiss)
}

/** 字号档位：全屏页的核心就是「比消息里大一点」，顺便给两档可调。 */
private val FONT_STEPS = listOf(13f, 15f, 18f, 22f)

/**
 * 全屏表格：白底、双向滚动、字号可调。
 *
 * 为什么不做双指缩放：`transformable`/`detectTransformGestures` 在单指时也会上报 pan，
 * 和这里两个方向的滚动会互相抢手势——表现是「滑不动」或「一滑就跳」。用字号档位替代，
 * 既能放大看细节，又不破坏滚动。
 */
@Composable
private fun FullScreenTable(table: MdBlock.Table, onDismiss: () -> Unit) {
    val palette = LocalPalette.current
    val columns = maxOf(table.header.size, table.rows.maxOfOrNull { it.size } ?: 0)
    if (columns <= 0) {
        LaunchedEffect(Unit) { onDismiss() }
        return
    }

    val rows = remember(table) { listOf(table.header) + table.rows }
    val measurer = rememberTextMeasurer(cacheSize = 512)
    val density = LocalDensity.current
    val padPx = with(density) { (TABLE_PAD_H * 2).toPx() }
    // 全屏这一屏比消息里宽得多，列宽上限也跟着放，长单元格不至于折成一条
    val maxPx = with(density) { 340.dp.toPx() }
    // 下限给窄一点：全屏这一屏列多的时候（6 列判定表），每列硬撑 60dp 就会被判成
    // 「必须横滑」，明明放得下却要滑一下才看得全
    val minPx = with(density) { 40.dp.toPx() }

    var step by remember(table) { mutableIntStateOf(1) }
    val textSize = FONT_STEPS[step.coerceIn(0, FONT_STEPS.lastIndex)]
    val cellStyle = remember(textSize) {
        TextStyle(fontSize = textSize.sp, lineHeight = (textSize * 1.45f).sp, fontSynthesis = FontSynthesis.All)
    }
    val widths = remember(table, cellStyle) {
        measureTableColumns(measurer, rows, columns, cellStyle, padPx, maxPx)
            .map { it.coerceAtLeast(minPx) }
    }
    val merged = remember(table) { tableMergeMarks(rows, columns) }

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
                    text = "表格",
                    fontSize = 15.sp,
                    fontWeight = FontWeight.W600,
                    color = palette.ink,
                )
                Spacer(Modifier.weight(1f))
                // 字号：小 / 标准 / 大 / 特大，点一下重排一次列宽
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    FontStepChip("A-", enabled = step > 0, palette = palette) {
                        step = (step - 1).coerceAtLeast(0)
                    }
                    FontStepChip("A+", enabled = step < FONT_STEPS.lastIndex, palette = palette) {
                        step = (step + 1).coerceAtMost(FONT_STEPS.lastIndex)
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
                val totalPx = widths.sum() + with(density) { columns.dp.toPx() }
                // 左右各 12dp 是滚动容器外的边距，可用宽度要扣掉，不然刚好卡在边界时会被裁
                val viewportPx = with(density) { maxWidth.toPx() - 24.dp.toPx() }
                val wide = totalPx > viewportPx + 0.5f

                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        // 边距放在滚动外层：滑到尽头时表格也不会顶到屏幕边缘
                        .padding(start = 12.dp, end = 12.dp, top = 6.dp, bottom = 14.dp)
                        .verticalScroll(vScroll)
                        .then(
                            if (wide) {
                                Modifier
                                    .horizontalScroll(hScroll)
                                    // 宽表格从屏幕边缘起手横滑时，别让系统的返回手势把它吃掉
                                    .systemGestureExclusion()
                            } else {
                                Modifier
                            },
                        ),
                    // 窄表格居中，宽表格从左边开始（这时候左右都要靠滑）
                    contentAlignment = if (wide) Alignment.TopStart else Alignment.TopCenter,
                ) {
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(10.dp))
                            .border(1.dp, palette.line, RoundedCornerShape(10.dp)),
                    ) {
                        TableCanvas(
                            rows = rows,
                            columns = columns,
                            widths = widths,
                            merged = merged,
                            palette = palette,
                            fontSize = textSize.sp,
                            lineHeight = (textSize * 1.45f).sp,
                        )
                    }
                }
            }
        }

        // 底部一句操作提示，第一次进来知道能干什么
        Text(
            text = if (rows.size > 1) "左右 / 上下滑动查看，A+ 字号更大" else "左右滑动查看",
            fontSize = 11.sp,
            color = palette.inkSoft,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(bottom = safe.calculateBottomPadding() + 6.dp),
        )
    }
}

/** 顶栏那颗字号小胶囊。 */
@Composable
private fun FontStepChip(
    label: String,
    enabled: Boolean,
    palette: com.agentdeck.app.ui.theme.DeckPalette,
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
