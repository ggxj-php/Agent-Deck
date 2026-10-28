package com.agentdeck.app.ui.components

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** 流式输出时公式可能只写了一半，不能被当成完整公式渲染。 */
class MarkdownMathTest {

    @Test
    fun closedMathBlockIsMath() {
        val blocks = parseMarkdown("\$\$a^2 + b^2 = c^2\$\$")
        val math = blocks.filterIsInstance<MdBlock.MathBlock>().single()
        assertEquals("a^2 + b^2 = c^2", math.latex)
    }

    @Test
    fun unclosedMathBlockStaysText() {
        val blocks = parseMarkdown("\$\$\n\\begin{cases} a=1")
        assertTrue("未闭合的公式被当成公式渲染了", blocks.none { it is MdBlock.MathBlock })
        val text = blocks.filterIsInstance<MdBlock.Paragraph>().joinToString(" ") { it.text }
        assertTrue("原文丢了：$text", text.contains("\\begin{cases}"))
    }

    @Test
    fun closedMultilineMathStillWorks() {
        val blocks = parseMarkdown("\$\$\n\\frac{1}{2}\n\$\$")
        val math = blocks.filterIsInstance<MdBlock.MathBlock>().single()
        assertTrue(math.latex.contains("\\frac{1}{2}"))
    }
}
