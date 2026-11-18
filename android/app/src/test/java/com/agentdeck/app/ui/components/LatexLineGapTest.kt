package com.agentdeck.app.ui.components

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * `\\[4pt]` 这类「换行 + 竖直间距」必须只丢方括号参数、保留换行。
 *
 * 模型在 cases / aligned 里几乎每条都这么写，JLaTeXMath 不认这个可选参数，
 * 于是 `[4pt]` 被当成正文排进公式、印在屏幕上。
 */
class LatexLineGapTest {

    @Test
    fun dropsGapAfterLineBreak() {
        val src = """\begin{cases}0, & y<0\\[2pt] u, & 0\le y<8\\[4pt] 1, & y\ge 8\end{cases}"""
        val out = stripLatexLineGaps(src)
        assertEquals("""\begin{cases}0, & y<0\\ u, & 0\le y<8\\ 1, & y\ge 8\end{cases}""", out)
    }

    @Test
    fun keepsPlainLineBreak() {
        val src = """a\\b"""
        assertEquals(src, stripLatexLineGaps(src))
    }

    @Test
    fun handlesStarVariantAndExUnits() {
        assertEquals("""a\\*b""", stripLatexLineGaps("""a\\*[1.5ex]b"""))
        assertEquals("""a\\b""", stripLatexLineGaps("""a\\[6pt]b"""))
    }

    @Test
    fun leavesDisplayDelimitersAlone() {
        // 单个 \[ 是行间公式定界符，删了整条公式就废了
        val src = """\[x^2\]"""
        assertEquals(src, stripLatexLineGaps(src))
    }

    @Test
    fun leavesNonLengthBracketAlone() {
        val src = """a\\[x]b"""
        assertEquals(src, stripLatexLineGaps(src))
    }
}
