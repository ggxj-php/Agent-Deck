package com.agentdeck.app.ui

import com.agentdeck.app.data.HistoryItem
import com.agentdeck.app.ui.components.inlinePlainText
import com.agentdeck.app.ui.theme.paletteFor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 抠标记不能把行内格式劈成两半。
 *
 * 截图里的现象：表情包正好落在 `**加粗**` 中间，两段各剩半个 `**`，渲染器找不到配对，
 * 就把 `**` 原样印出来、加粗整段消失。这里验证的是通用规则：不管哪种行内格式、
 * 不管标记落在哪个位置，切完之后的每一段都必须自己配对完整。
 */
class InlineSpansTest {

    private val palette = paletteFor("#4d6bfe")

    private fun texts(markdown: String): List<String> =
        splitStickerMarksKeepingInline(markdown).filterIsInstance<StickerPart.Text>().map { it.value }

    @Test
    fun stickerInsideBoldKeepsBothHalvesBold() {
        val markdown = "**她回的是「没见过」。[sticker:happy:a.png] 然后自己画了一张发出来。**"
        assertEquals(
            listOf("**她回的是「没见过」。**", "** 然后自己画了一张发出来。**"),
            texts(markdown),
        )
    }

    @Test
    fun screenshotCaseRendersWithoutRawMarkers() {
        // 照着截图里的那段回复缩写的：加粗从「你那个」一直写到表情包后面那段的开头
        val markdown = "有个细节得说清楚：**你那个「你信不信」，她回的是「没见过」，" +
            "然后自己画了一个胖团子发出来。[sticker:shy:a.png]\n\n** 意思就是，她不是光看着你聊。"
        val parts = texts(markdown)
        parts.forEach { part ->
            val plain = inlinePlainText(part, palette)
            assertFalse("屏幕上露出了字面量标记：$plain", plain.contains("**"))
        }
        assertTrue("表情包前面那半句的加粗丢了：$parts", parts.first().endsWith("发出来。**"))
    }

    @Test
    fun noRawMarkersReachTheScreen() {
        val markdown = "**她回的是「没见过」。[sticker:happy:a.png] 然后自己画了一张发出来。**"
        texts(markdown).forEach { part ->
            val plain = inlinePlainText(part, palette)
            assertFalse("屏幕上露出了字面量标记：$plain", plain.contains("**"))
        }
    }

    @Test
    fun stickerInsideItalic() {
        assertEquals(
            listOf("*前半句*", "*后半句*"),
            texts("*前半句[sticker:calm:a.png]后半句*"),
        )
    }

    @Test
    fun stickerInsideStrike() {
        assertEquals(
            listOf("~~这句不要了~~", "~~这句也不要~~"),
            texts("~~这句不要了[sticker:calm:a.png]这句也不要~~"),
        )
    }

    @Test
    fun stickerInsideInlineCode() {
        assertEquals(
            listOf("`rm -rf`", "`/tmp`"),
            texts("`rm -rf[sticker:calm:a.png]/tmp`"),
        )
    }

    @Test
    fun stickerInsideLinkLabel() {
        // 链接拆开要补上完整的目标，否则后半段渲染不出链接
        assertEquals(
            listOf("[前半](https://example.com/a)", "[后半](https://example.com/a)"),
            texts("[前半[sticker:calm:a.png]后半](https://example.com/a)"),
        )
    }

    @Test
    fun markRightAfterClosingMarkStaysHarmless() {
        // 标记卡在内容和闭合标记之间：前半该闭合，后半不该再冒出字面量
        val parts = texts("**前半句。[sticker:calm:a.png]**后半句")
        parts.forEach { part ->
            val plain = inlinePlainText(part, palette)
            assertFalse("屏幕上露出了字面量标记：$plain", plain.contains("**"))
        }
        assertTrue("前半句的加粗丢了：$parts", parts.first().startsWith("**前半句。"))
    }

    @Test
    fun closingMarkAfterStickerIsSwallowed() {
        // 流式里很常见：句子刚写完就插了表情包，闭合的 `**` 隔了几行才到，
        // 这时候前半段该闭合，后半段保持普通文字，不能把 `**` 留在屏幕上
        assertEquals(
            listOf("**前半句。**", "\n\n 后半句"),
            texts("**前半句。[sticker:calm:a.png]\n\n** 后半句"),
        )
    }

    @Test
    fun unclosedBoldIsLeftAlone() {
        // 流式还没写完：两边都没配对，别自作聪明补标记
        assertEquals(listOf("**还没写完"), texts("**还没写完[sticker:calm:a.png]"))
    }

    @Test
    fun codeFenceIsNotTreatedAsFormatting() {
        val markdown = "```\n**这里的星号是代码\n```\n[sticker:calm:a.png]正文"
        assertEquals(
            listOf("```\n**这里的星号是代码\n```\n", "正文"),
            texts(markdown),
        )
    }

    @Test
    fun historyStickerInsideBoldKeepsBold() {
        val items = listOf(HistoryItem("assistant", "**第一句。[sticker:happy:a.png]第二句**"))
        val messages = HistoryParser.toMessages(items) { _, name -> "/api/persona/sticker/$name" }
        val blocks = messages.single().blocks
        assertEquals(
            listOf("**第一句。**", "**第二句**"),
            blocks.filterIsInstance<Block.Text>().map { it.markdown },
        )
        assertTrue("表情包没了：$blocks", blocks.any { it is Block.Sticker })
    }

    @Test
    fun historyArtifactInsideBoldKeepsBold() {
        val text = "**看这个 [[artifact:abcdef1234567890|产物图]] 的效果**"
        val items = listOf(HistoryItem("assistant", text))
        val messages = HistoryParser.toMessages(items) { _, name -> "/api/persona/sticker/$name" }
        val blocks = messages.single().blocks
        assertEquals(
            listOf("**看这个 **", "** 的效果**"),
            blocks.filterIsInstance<Block.Text>().map { it.markdown },
        )
        assertTrue("产物没了：$blocks", blocks.any { it is Block.Artifact })
    }

    @Test
    fun plainTextIsUntouched() {
        val markdown = "这句没有格式[sticker:happy:a.png]也没有标记"
        assertEquals(listOf("这句没有格式", "也没有标记"), texts(markdown))
    }
}
