package com.agentdeck.app.ui.components

import com.agentdeck.app.ui.theme.paletteFor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 行内公式与 markdown 的先后顺序。
 *
 * 截图里的现象：`**第一步：把约束化成关于 x 的一元二次。**` 原样露出星号，
 * 因为公式把加粗劈成两半、两半各自都配不上对。这里把「先占位、再解析」的顺序钉住。
 */
class MarkdownMathOrderTest {

    private val palette = paletteFor("#4d6bfe")

    @Test
    fun formulaBecomesPlaceholder() {
        val split = splitInlineMathPlaceholders("**第一步：把约束化成关于 \$x\$ 的一元二次。**")
        assertEquals(listOf("x"), split.formulas)
        // 加粗标记必须完整留在正文里，交给下一步解析
        assertTrue(split.text.startsWith("**第一步"))
        assertTrue(split.text.endsWith("一元二次。**"))
        assertFalse(split.text.contains("\$"))
    }

    @Test
    fun boldAcrossFormulaKeepsPaired() {
        val source = "**第一步：把约束化成关于 \$x\$ 的一元二次。**"
        val chunks = inlineAndSplit(splitInlineMathPlaceholders(source).text, palette)
        val text = chunks.filterIsInstance<InlineChunk.Text>().joinToString("") { it.annotated.text }
        assertFalse("加粗标记不应该漏到正文里：$text", text.contains("**"))
        assertEquals(1, chunks.filterIsInstance<InlineChunk.Math>().size)
    }

    @Test
    fun formulaCountMatchesChunks() {
        val source = "令 \$t = \\frac{y}{x} > 0\$ ，即 \$y = tx\$ ，代入后可得 \$x+y+1=0\$"
        val chunks = inlineAndSplit(splitInlineMathPlaceholders(source).text, palette)
        assertEquals(3, chunks.filterIsInstance<InlineChunk.Math>().size)
    }

    @Test
    fun dollarInsideCodeIsNotMath() {
        val split = splitInlineMathPlaceholders("价格是 `\$100` 哦")
        assertTrue(split.formulas.isEmpty())
    }
}

/** JLaTeXMath 比 MathJax 挑剔：编号/标签类的排版命令必须清掉，否则整块公式渲染失败。 */
class LatexSanitizeTest {

    @Test
    fun dropsTagAndLabel() {
        val raw = "(1+t)x^{2}+x-\\left(2+\\frac{8}{t}\\right)=0 \\tag{★}"
        val clean = sanitizeLatex(raw)
        assertFalse(clean.contains("\\tag"))
        assertFalse(clean.contains("★"))
        // 公式本体不能被误删
        assertTrue(clean.contains("x^{2}"))
        assertTrue(clean.contains("\\frac{8}{t}"))
    }

    @Test
    fun numberedEnvBecomesAligned() {
        assertEquals(
            "\\begin{aligned} a &= b \\end{aligned}",
            sanitizeLatex("\\begin{align} a &= b \\end{align}"),
        )
        assertEquals(
            "\\begin{aligned} a=b \\end{aligned}",
            sanitizeLatex("\\begin{equation} a=b \\end{equation}"),
        )
    }

    @Test
    fun dropsSpacingCommands() {
        val clean = sanitizeLatex("a \\nonumber \\hspace{1em} b")
        assertFalse(clean.contains("\\nonumber"))
        assertFalse(clean.contains("\\hspace"))
    }

    @Test
    fun fallbackHasNoLeftRight() {
        // 渲染失败时露出的应该是人能读的式子，不是 \left \right
        val text = demathForFallback("\\left(2+\\frac{8}{t}\\right)")
        assertFalse(text, text.contains("\\left"))
        assertFalse(text, text.contains("\\right"))
        assertTrue(text, text.contains("(2+(8)/(t))"))
    }
}
