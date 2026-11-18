package com.agentdeck.app.ui.components

import androidx.compose.ui.geometry.Size
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 图片自适应高度那一行的回归测试。
 *
 * 1.18.4 起真机上偶发 `IllegalStateException: Size is unspecified`，原因就是这里
 * 直接读了 `Size.Unspecified.width`——它的 getter 是抛异常，不是返回 NaN。
 */
class SafeAspectRatioTest {

    @Test
    fun unspecifiedDoesNotThrow() {
        // 关键用例：以前在这里抛「Size is unspecified」
        assertNull(safeAspectRatio(Size.Unspecified))
    }

    @Test
    fun zeroAndNegativeAreRejected() {
        assertNull(safeAspectRatio(Size(0f, 100f)))
        assertNull(safeAspectRatio(Size(100f, 0f)))
        assertNull(safeAspectRatio(Size(-1f, 100f)))
        assertNull(safeAspectRatio(Size.Zero))
    }

    @Test
    fun normalSizeGivesRatio() {
        assertEquals(2f, safeAspectRatio(Size(200f, 100f))!!, 0.0001f)
        // 长图：1:6
        assertEquals(1f / 6f, safeAspectRatio(Size(100f, 600f))!!, 0.0001f)
    }
}
