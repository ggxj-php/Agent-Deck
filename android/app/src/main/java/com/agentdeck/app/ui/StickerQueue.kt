package com.agentdeck.app.ui

/**
 * 表情包插入时机：和网页端 flushQueuedStickers 一样，
 * 只有正文刚好停在句末标点 / 换行时才允许把表情插进去，避免劈开一句话。
 *
 * 和网页端有一处有意的差别：网页端把「…；;」也算边界，但中文里省略号和分号
 * 几乎都是句中断续（「……还是在消耗…咕噜）。」），在那儿插正好把括号劈成两半。
 * 这里只认真正的句末，代价最多是晚一两秒出现，换来的是不再出现半截句子。
 */
internal fun endsAtSentenceBoundary(text: String): Boolean {
    if (text.isEmpty()) return false
    // 只去掉行内空白；换行本身是有效边界，不能被 trimEnd 吃掉
    val trimmed = text.trimEnd(' ', '\t')
    if (trimmed.isEmpty()) return false
    val last = trimmed.last()
    return last == '\n' || last in "。！？!?"
}

/** 正文里可能夹着的表情包标记：`[sticker:心情]` 或 `[sticker:心情:文件名]`。 */
internal val STICKER_MARK = Regex("""\[sticker:([A-Za-z_]+)(?::([^\]\s]+))?]""")

/** 一段正文按表情包标记切开后的一节。 */
internal sealed interface StickerPart {
    data class Text(val value: String) : StickerPart
    data class Mark(val mood: String, val name: String) : StickerPart
}

internal fun splitStickerMarks(text: String): List<StickerPart> {
    val out = ArrayList<StickerPart>()
    var cursor = 0
    for (match in STICKER_MARK.findAll(text)) {
        if (match.range.first > cursor) {
            out.add(StickerPart.Text(text.substring(cursor, match.range.first)))
        }
        out.add(StickerPart.Mark(match.groupValues[1], match.groupValues[2]))
        cursor = match.range.last + 1
    }
    if (cursor < text.length) out.add(StickerPart.Text(text.substring(cursor)))
    return out
}
