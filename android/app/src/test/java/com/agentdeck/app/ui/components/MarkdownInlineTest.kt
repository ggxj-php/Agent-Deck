package com.agentdeck.app.ui.components

import com.agentdeck.app.ui.theme.paletteFor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** 行内解析必须保证「一点都不丢」：文字、代码、加粗都要原样保留内容。 */
class MarkdownInlineTest {

    private val palette = paletteFor("#4d6bfe")

    @Test
    fun keepsInlineCode() {
        val out = inlinePlainText("先把 `mmssms.db` 提取出来并计算哈希", palette)
        assertEquals("先把 mmssms.db 提取出来并计算哈希", out)
    }

    @Test
    fun keepsBoldContent() {
        val out = inlinePlainText("**数据流图**：用户扫码开锁", palette)
        assertEquals("数据流图：用户扫码开锁", out)
    }

    @Test
    fun keepsUnderscoreCode() {
        val out = inlinePlainText("关键发现是 `_enforce_limit` 导致的", palette)
        assertEquals("关键发现是 _enforce_limit 导致的", out)
    }

    @Test
    fun keepsLinkLabel() {
        val out = inlinePlainText("看这个 [文档](https://example.com/a)", palette)
        assertEquals("看这个 文档", out)
    }

    @Test
    fun inlineMathStaysRawForRenderer() {
        // 解析阶段保留原始 $...$，由渲染层拆成文字+公式混排（以前在这里 demath 成文字，公式就没了）
        val blocks = parseMarkdown("面积 \$S_{\\text{总}} = 3600\$")
        val text = blocks.filterIsInstance<MdBlock.Paragraph>().first().text
        assertTrue("实际输出：$text", text.contains("3600"))
        assertTrue("公式被提前转成文字了：$text", text.contains("$"))
    }

    @Test
    fun demathStillConvertsStandaloneLatex() {
        val out = demath("面积 \$S_{\\text{总}} = 3600\$")
        assertTrue("实际输出：$out", out.contains("3600"))
        assertTrue("兜底文本没转换：$out", !out.contains("\\text"))
    }

    @Test
    fun bulletKeepsInlineCodeThroughParser() {
        val markdown = "- 先把 `mmssms.db` 提取出来并计算哈希，然后将原件设为只读；"
        val blocks = parseMarkdown(markdown)
        val bullet = blocks.filterIsInstance<MdBlock.Bullet>().firstOrNull()
        assertTrue("没解析成列表：$blocks", bullet != null)
        assertEquals(
            "先把 `mmssms.db` 提取出来并计算哈希，然后将原件设为只读；",
            bullet!!.items.first(),
        )
    }

    @Test
    fun parsesBlocksWithoutDroppingText() {
        val markdown = """
            ## 第三步：算面积
            正方形的总面积：

            ${'$'}${'$'}S_{\text{总}} = 60 \times 60 = 3600${'$'}${'$'}

            - 先把 `mmssms.db` 提取出来
        """.trimIndent()
        val blocks = parseMarkdown(markdown)
        assertTrue(blocks.any { it is MdBlock.Heading })
        assertTrue(blocks.any { it is MdBlock.MathBlock })
        assertTrue(blocks.any { it is MdBlock.Bullet })
        assertTrue(blocks.none { it is MdBlock.Paragraph && it.text.contains("\\text") })
    }
}
