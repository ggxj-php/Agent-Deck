package com.agentdeck.app.ui.components

import org.junit.Assert.assertEquals
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
    fun surroundingTextIsKept() {
        val blocks = parseMarkdown("先看这个 [手册.docx](/d/abc123) 有问题再找我")
        val download = blocks.filterIsInstance<MdBlock.Download>().single()
        assertEquals("/d/abc123", download.path)
        val texts = blocks.filterIsInstance<MdBlock.Paragraph>().map { it.text }
        assertTrue("前缀文字丢了：$texts", texts.any { it.contains("先看这个") })
        assertTrue("后缀文字丢了：$texts", texts.any { it.contains("有问题再找我") })
    }
}
