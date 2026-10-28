package com.agentdeck.app.ui.components

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** 行内公式：$...$ 要拆成文字+公式，交给渲染层混排，而不是直接 demath 成文字。 */
class InlineMathTest {

    @Test
    fun splitsInlineDollar() {
        val segments = splitInlineMath("结果 \$1-a^{2}-b^{2}\$ 成立")
        assertEquals(3, segments.size)
        assertEquals(false, segments[0].math)
        assertTrue(segments[0].text.contains("结果"))
        assertEquals(true, segments[1].math)
        assertEquals("1-a^{2}-b^{2}", segments[1].text)
        assertEquals(false, segments[2].math)
        assertTrue(segments[2].text.contains("成立"))
    }

    @Test
    fun splitsDoubleDollarInline() {
        val segments = splitInlineMath("前 \$\$x^2\$\$ 后")
        val math = segments.filter { it.math }
        assertEquals(1, math.size)
        assertEquals("x^2", math[0].text)
    }

    @Test
    fun plainTextHasNoMath() {
        val segments = splitInlineMath("普通一句话，没有公式")
        assertEquals(1, segments.size)
        assertEquals(false, segments[0].math)
    }

    @Test
    fun parenDelimitersWork() {
        val segments = splitInlineMath("开头 \\(a+b\\) 结尾")
        val math = segments.filter { it.math }
        assertEquals(1, math.size)
        assertEquals("a+b", math[0].text)
    }
}
