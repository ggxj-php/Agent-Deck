package com.agentdeck.app.ui

/**
 * 表情包插入时机：和网页端 flushQueuedStickers 一样，
 * 只有正文刚好停在句末标点 / 换行时才允许把表情插进去，避免劈开一句话。
 */
internal fun endsAtSentenceBoundary(text: String): Boolean =
    Regex("[。！？!?…；;\\n]\\s*$").containsMatchIn(text)
