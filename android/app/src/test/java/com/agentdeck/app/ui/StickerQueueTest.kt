package com.agentdeck.app.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 表情包只能在句子边界插入，否则会把一句话从中间劈开（网页端同款判断）。 */
class StickerQueueTest {

    @Test
    fun chinesePunctuationIsBoundary() {
        assertTrue(endsAtSentenceBoundary("本鱼的鱼眼好使着呢。"))
        assertTrue(endsAtSentenceBoundary("说一声就重画！"))
        assertTrue(endsAtSentenceBoundary("行吧？"))
    }

    @Test
    fun midSentenceIsNotBoundary() {
        assertFalse(endsAtSentenceBoundary("本鱼的鱼眼好使着呢"))
        assertFalse(endsAtSentenceBoundary("，身上披着蓝羽大衣"))
    }

    @Test
    fun newlineIsBoundary() {
        assertTrue(endsAtSentenceBoundary("第一段\n"))
        assertTrue(endsAtSentenceBoundary("第二段\n\n"))
    }
}
