package com.agentdeck.app.ui.components

import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 回归：LaTeX 渲染失败时会走 demathForFallback，里面几条正则以前少了转义的 `}`，
 * 一编译就抛 PatternSyntaxException（AI 第一次输出公式时闪退）。
 */
class LatexFallbackTest {

    @Test
    fun fallbackHandlesCommonLatex() {
        val out = demathForFallback("""\text{速度} = \frac{d}{t}""")
        assertTrue("兜底文本为空：$out", out.isNotEmpty())
        assertTrue("分式没转换：$out", out.contains("d") && out.contains("t"))
    }

    @Test
    fun fallbackHandlesSqrtAndScripts() {
        val out = demathForFallback("""\sqrt{x^2} + a_{1} + b^{2}""")
        assertTrue("兜底文本为空：$out", out.isNotEmpty())
    }

    @Test
    fun fallbackNeverThrowsOnGarbage() {
        val garbage = listOf(
            """\frac{""",
            """\text{""",
            """$""",
            """\unknowncmd{1}{2}""",
            """\\""",
        )
        garbage.forEach { latex ->
            val out = demathForFallback(latex)
            assertTrue("输入 $latex 返回了空", out.isNotEmpty() || latex.isBlank())
        }
    }
}
