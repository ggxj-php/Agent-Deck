package com.agentdeck.app.ui.components

import com.agentdeck.app.ui.theme.paletteFor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 用截图里的真实片段跑一遍整段解析，确认两处「原样露出来」都不再发生：
 * 1. `**第一步：…关于 x 的一元二次。**` 里的星号；
 * 2. `$$ … \tag{★}$$` 这种带编号的公式块。
 */
class MarkdownScreenshotTest {

    private val palette = paletteFor("#4d6bfe")

    private val sample = """
方法一：消元 + 换元 + 判别式（纯机械，推荐）
**第一步：把约束化成关于 ${'$'}x${'$'} 的一元二次。**
令 ${'$'}t = \frac{y}{x} > 0${'$'} ，即 ${'$'}y = tx${'$'} ，代入

${'$'}${'$'}(1+t)x^{2}+x-\left(2+\frac{8}{t}\right)=0 \tag{★}${'$'}${'$'}

**第二步：目标函数也用 ${'$'}t${'$'} 表示。**
    """.trimIndent()

    @Test
    fun boldLineStaysOneParagraph() {
        val blocks = parseMarkdown(sample)
        val first = blocks.filterIsInstance<MdBlock.Paragraph>().first { it.text.contains("第一步") }
        // 公式不该把一行拆成好几块：整句（含上一行的「方法一」）必须留在同一个段落里
        assertTrue(first.text, first.text.contains("一元二次"))
        assertTrue(first.text, first.text.contains("**第一步"))
        assertTrue(first.text, first.text.contains("方法一"))
    }

    @Test
    fun taggedFormulaBecomesMathBlock() {
        val blocks = parseMarkdown(sample)
        val math = blocks.filterIsInstance<MdBlock.MathBlock>()
        assertEquals(1, math.size)
        // 公式本体完整保留，清洗是渲染前那一层的事
        assertTrue(math[0].latex, math[0].latex.contains("\\tag{★}"))
        assertTrue(math[0].latex, math[0].latex.contains("\\frac{8}{t}"))
    }

    @Test
    fun boldAroundInlineFormulaYieldsNoStars() {
        val paragraph = parseMarkdown("**第三步：要证 ${'$'}x \\le M${'$'} 。**")
            .filterIsInstance<MdBlock.Paragraph>()
            .first()
        val chunks = inlineAndSplit(splitInlineMathPlaceholders(paragraph.text).text, palette)
        val text = chunks.filterIsInstance<InlineChunk.Text>().joinToString("") { it.annotated.text }
        assertFalse("正文里不该露出星号：$text", text.contains("**"))
        assertTrue(text, text.contains("第三步"))
        assertEquals(1, chunks.filterIsInstance<InlineChunk.Math>().size)
    }

    @Test
    fun taggedFormulaSurvivesSanitize() {
        val blocks = parseMarkdown(sample)
        val latex = blocks.filterIsInstance<MdBlock.MathBlock>().first().latex
        val clean = sanitizeLatex(latex)
        // 清洗之后才能交给 JLaTeXMath，否则整块渲染失败、屏幕上露出 \left \right
        assertFalse(clean, clean.contains("\\tag"))
        assertTrue(clean, clean.contains("\\frac{8}{t}"))
    }
}
