package com.agentdeck.app.ui.theme

import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * 和网页端 `applyTheme()` 完全一致的一套派生色。
 *
 * 后端只给一个 `theme`（#rrggbb），网页把往白/黑方向混出来的档位灌进 CSS 变量；
 * 这里用同样的公式算一遍，保证「换身份卡 = 换整套配色」两边长得一样。
 */
data class DeckPalette(
    val accent: Color,          // --pink-dark 原色
    val pink: Color,            // --pink
    val pinkDeep: Color,        // --pink-deep
    val pinkPale: Color,        // --pink-pale（顶栏渐变起点）
    val pinkMist: Color,        // --pink-mist（顶栏渐变终点）
    val bg: Color,              // --bg
    val line: Color,            // --line
    val ink: Color = Color(0xFF4A3B43),
    val inkSoft: Color = Color(0xFF90788A),
    val bubble: Color = Color.White,
    val userBubbleEnd: Color = Color(0xFFFF86B6),
    val softFill: Color = Color(0xFFFFF8FC),
    val activeFill: Color = Color(0xFFFFF0F7),
    val trackFill: Color = Color(0xFFFFE3F0),
    val activeText: Color = Color(0xFFD63F86),
    val shadow: Color = Color(0x4DE0729F),
    val softShadow: Color = Color(0x2EFF9EC7),
)

private fun mix(base: Color, target: Color, ratio: Float): Color = Color(
    red = base.red + (target.red - base.red) * ratio,
    green = base.green + (target.green - base.green) * ratio,
    blue = base.blue + (target.blue - base.blue) * ratio,
    alpha = 1f,
)

/** 对应网页端：mixColor(theme, 'white'|'black', ratio)。 */
fun paletteFor(themeHex: String): DeckPalette {
    val accent = parseHex(themeHex) ?: Color(0xFFFFB7D5)
    val white = Color.White
    val black = Color.Black
    return DeckPalette(
        accent = accent,
        pink = mix(accent, white, 0.25f),
        pinkDeep = mix(accent, black, 0.30f),
        pinkPale = mix(accent, white, 0.45f),
        pinkMist = mix(accent, white, 0.78f),
        bg = mix(accent, white, 0.93f),
        line = mix(accent, white, 0.70f),
    )
}

/** 支持 #rgb / #rrggbb，认不出来就返回 null 交给兜底色。 */
private fun parseHex(raw: String): Color? {
    val value = raw.trim().removePrefix("#")
    return when (value.length) {
        6 -> value.toLongOrNull(16)?.let { Color(0xFF000000 or it) }
        3 -> value.toLongOrNull(16)?.let {
            val r = (it shr 8) and 0xF
            val g = (it shr 4) and 0xF
            val b = it and 0xF
            Color(0xFF000000 or (r * 17 shl 16) or (g * 17 shl 8) or (b * 17))
        }
        else -> null
    }
}

val LocalPalette = staticCompositionLocalOf { paletteFor("#ffb7d5") }
