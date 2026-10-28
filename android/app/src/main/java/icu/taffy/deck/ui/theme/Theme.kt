package icu.taffy.deck.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 主题跟着「身份卡」走：一套 [DeckPalette] 同时喂给 Material3 和自定义组件，
 * 换人设 = 换顶栏渐变、气泡、按钮、背景，和网页端一样。
 */
@Composable
fun DeckTheme(accentHex: String = "#ffb7d5", content: @Composable () -> Unit) {
    val palette = remember(accentHex) { paletteFor(accentHex) }
    val scheme = lightColorScheme(
        primary = palette.accent,
        onPrimary = Color.White,
        primaryContainer = palette.pinkMist,
        onPrimaryContainer = palette.ink,
        secondary = palette.pinkDeep,
        background = palette.bg,
        onBackground = palette.ink,
        surface = palette.bubble,
        onSurface = palette.ink,
        onSurfaceVariant = palette.inkSoft,
        surfaceVariant = palette.pinkMist,
        outline = palette.line,
        outlineVariant = palette.line,
        error = Color(0xFFB3261E),
        errorContainer = Color(0xFFFDE7E5),
    )
    CompositionLocalProvider(LocalPalette provides palette) {
        MaterialTheme(
            colorScheme = scheme,
            typography = Typography(
                bodyLarge = Typography().bodyLarge.copy(fontSize = 15.sp, lineHeight = 22.sp),
                bodyMedium = Typography().bodyMedium.copy(fontSize = 14.sp, lineHeight = 20.sp),
                labelSmall = Typography().labelSmall.copy(fontSize = 11.sp),
            ),
            shapes = Shapes(
                extraSmall = RoundedCornerShape(8.dp),
                small = RoundedCornerShape(10.dp),
                medium = RoundedCornerShape(14.dp),
                large = RoundedCornerShape(18.dp),
                extraLarge = RoundedCornerShape(24.dp),
            ),
            content = content,
        )
    }
}
