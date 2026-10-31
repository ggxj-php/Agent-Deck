package com.agentdeck.app.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 表情包插在哪里。
 *
 * 截图里的现象：表情包正好落在「（嘴上说着不啃，实际上还是在消耗…咕噜）。」中间，
 * 把一句带括号的话劈成两半。这两条规则就是防它的：插入前要求真的写到句末，
 * 万一还是落在句中，渲染时再往后挪到句末。
 */
class StickerPlacementTest {

    @Test
    fun ellipsisIsNotBoundary() {
        // 中文里「…」几乎都是句中断续，在这儿插等于劈开句子
        assertFalse(endsAtSentenceBoundary("实际上还是在消耗…"))
        assertFalse(endsAtSentenceBoundary("就这样吧；"))
    }

    @Test
    fun realEndMarksAreBoundary() {
        assertTrue(endsAtSentenceBoundary("本鱼知道了。"))
        assertTrue(endsAtSentenceBoundary("真的吗？"))
        assertTrue(endsAtSentenceBoundary("好耶！"))
        assertTrue(endsAtSentenceBoundary("记得先跟我说一声\n"))
        // 末尾多几个空格不影响判断
        assertTrue(endsAtSentenceBoundary("好。 "))
    }

    @Test
    fun emptyTextIsNotBoundary() {
        assertFalse(endsAtSentenceBoundary(""))
        assertFalse(endsAtSentenceBoundary("   "))
    }

    @Test
    fun stickerMovesOutOfParenthesis() {
        val source = "还是在消耗[sticker:sleepy:z.png]…咕噜）。"
        assertEquals(
            "还是在消耗…咕噜）。[sticker:sleepy:z.png]",
            HistoryParser.snapStickersToBoundaries(source),
        )
    }

    @Test
    fun stickerAtBoundaryStays() {
        val source = "本鱼知道了。[sticker:happy:z.png]下一句"
        assertEquals(source, HistoryParser.snapStickersToBoundaries(source))
    }

    @Test
    fun stickerWithoutBoundaryGoesLast() {
        val source = "一句没有标点的话[sticker:think:z.png]"
        assertEquals("一句没有标点的话[sticker:think:z.png]", HistoryParser.snapStickersToBoundaries(source))
    }

    @Test
    fun splitsAlternatingParts() {
        val parts = splitStickerMarks("前[sticker:happy:a.png]后")
        assertEquals(3, parts.size)
        assertEquals("前", (parts[0] as StickerPart.Text).value)
        assertEquals("happy", (parts[1] as StickerPart.Mark).mood)
        assertEquals("a.png", (parts[1] as StickerPart.Mark).name)
        assertEquals("后", (parts[2] as StickerPart.Text).value)
    }

    @Test
    fun parsesLegacyMoodOnly() {
        val parts = splitStickerMarks("[sticker:laugh]")
        assertEquals(1, parts.size)
        assertEquals("laugh", (parts[0] as StickerPart.Mark).mood)
        assertEquals("", (parts[0] as StickerPart.Mark).name)
    }
}
