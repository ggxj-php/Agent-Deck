package com.agentdeck.app.ui.components

import com.agentdeck.app.ui.theme.paletteFor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 下载链接的解析：链接文字、相对路径、带前缀的写法都要能识别。 */
class MarkdownDownloadTest {

    @Test
    fun plainDownloadLinkBecomesCard() {
        val blocks = parseMarkdown("[报告.pdf](/d/abc123)")
        val download = blocks.filterIsInstance<MdBlock.Download>().single()
        assertEquals("报告.pdf", download.label)
        assertEquals("/d/abc123", download.path)
    }

    @Test
    fun linkWithEmojiPrefixStillBecomesCard() {
        // 真实回复里的写法：👉 [手册.docx](/d/3b7f...)
        val blocks = parseMarkdown("👉 [手册.docx](/d/3b7fb6ea9b77b5599f26cf847cabccc4)")
        val download = blocks.filterIsInstance<MdBlock.Download>().single()
        assertEquals("手册.docx", download.label)
        assertEquals("/d/3b7fb6ea9b77b5599f26cf847cabccc4", download.path)
    }

    @Test
    fun boldAroundDownloadLinkIsRebalanced() {
        // 链接被抠成卡片后，包着它的加粗要各段补齐，而不是把 `**` 删掉或原样印出来
        val blocks = parseMarkdown("📥 **报告 [报告.docx](/d/abc123) 已经生成**")
        val texts = blocks.filterIsInstance<MdBlock.Paragraph>().map { it.text }
        assertEquals(2, texts.size)
        assertTrue("前缀的加粗丢了：$texts", texts.first().contains("**报告"))
        texts.forEach { part ->
            val plain = inlinePlainText(part, paletteFor("#4d6bfe"))
            assertFalse("屏幕上露出了字面量标记：$plain", plain.contains("**"))
        }
    }

    @Test
    fun surroundingTextIsKept() {
        val blocks = parseMarkdown("先看这个 [手册.docx](/d/abc123) 有问题再找我")
        val download = blocks.filterIsInstance<MdBlock.Download>().single()
        assertEquals("/d/abc123", download.path)
        val texts = blocks.filterIsInstance<MdBlock.Paragraph>().map { it.text }
        assertTrue("前缀文字丢了：$texts", texts.any { it.contains("先看这个") })
        assertTrue("后缀文字丢了：$texts", texts.any { it.contains("有问题再找我") })
    }
}
