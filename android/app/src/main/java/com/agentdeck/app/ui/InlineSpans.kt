package com.agentdeck.app.ui

/*
 * 行内格式的「补平衡」。
 *
 * 表情包、产物卡片、下载卡片都是从正文里把一小段标记抠出来单独画的。抠的位置如果正好
 * 落在 `**加粗**`、`~~删除线~~`、`*斜体*`、`行内代码`、`[文字](链接)` 的中间，
 * 两边就各剩半个标记；inline() 找不到配对，会把标记当普通字符原样印在屏幕上，加粗也一起没了
 * （现象就是正文里冒出「**你那个「你信不信」」这种字面量）。
 *
 * 以前是每个调用点各修各的：`stripSplitMarks` 把落单的 `**` 直接删掉（加粗丢了），
 * 表情包切段则完全没管。这里改成统一的办法——切分前先看每个「抠点」落在哪些行内格式里，
 * 在抠点左侧补闭合标记、右侧补重开标记：
 *
 * ```
 * **你那个「你信不信」，她回的是「没见过」。[sticker:x:a.png] 意思就是……
 *   ↓
 * **你那个「你信不信」，她回的是「没见过」。** [sticker:x:a.png] ** 意思就是……
 * ```
 *
 * 这样每段单独交给渲染器都是配对完整的，格式保留、屏幕上也不会露出标记。
 * 扫描规则和 [inline] 保持一致（谁不认识的东西，这里也不当格式），避免两边判断不一致。
 */

/** 一段行内格式： [start, end) 覆盖开标记、内容、闭标记；[contentEnd] 是闭标记起点。 */
internal data class InlineSpan(
    val open: String,
    val close: String,
    val start: Int,
    val end: Int,
    val contentEnd: Int,
)

/**
 * 把 [ranges]（要被抠掉、单独渲染的区间）两侧的行内格式补平衡。
 *
 * 区间是半开区间 `start until endExclusive`，允许同时传多个（表情包、产物、下载链接）。
 * 没有任何格式跨越抠点时会原样返回，调用方可以无条件调。
 */
internal fun repairInlineSpans(text: String, ranges: List<IntRange>): String {
    if (text.isEmpty() || ranges.isEmpty()) return text
    val ordered = ranges
        .filter { it.first >= 0 && it.first < text.length }
        .sortedBy { it.first }
    if (ordered.isEmpty()) return text
    val spans = inlineSpans(text)
    if (spans.isEmpty()) return text

    val out = StringBuilder(text.length + ordered.size * 8)
    var cursor = 0
    // 已经被「吃掉」的闭合标记：格式在抠点处就结束了，留着它就是一对落单的字面量
    var consumed = 0
    for (range in ordered) {
        val from = maxOf(cursor, consumed)
        val start = range.first.coerceAtLeast(from)
        val end = (range.last + 1).coerceIn(start, text.length)
        out.append(text, from, start)

        // 抠点左边：先给还开着的格式收尾，内层先关（按开标记位置倒序）
        spans.filter { it.start < start && it.end > start }
            .sortedByDescending { it.start }
            .forEach { out.append(it.close) }

        out.append(text, start, end)

        // 抠点右边：跨越过去的格式要重新打开，外层先开（按开标记位置正序）。
        // 但标记后面只剩空白时，说明这段格式本来就在这儿结束——不重开，
        // 顺手把它自己的闭合标记吃掉，否则半截标记会被渲染成字面的 `**`。
        for (span in spans.filter { it.start < start && it.end > end }.sortedBy { it.start }) {
            // 抠点到闭合标记之间只有空白 ⇒ 格式本来就在这儿收尾：
            // 空白（换行/段落间隔）留着，只把那个闭合标记吃掉
            if (text.substring(end, span.contentEnd).isBlank()) {
                out.append(text, end, span.contentEnd)
                consumed = maxOf(consumed, span.end)
            } else {
                out.append(span.open)
            }
        }

        cursor = end
    }
    out.append(text, maxOf(cursor, consumed).coerceAtMost(text.length), text.length)
    return out.toString()
}

/** 扫出文本里所有配对成功的行内格式。未闭合的（流式还没写完）不返回，和渲染器一致。 */
internal fun inlineSpans(text: String): List<InlineSpan> {
    val spans = ArrayList<InlineSpan>()
    val fences = fencedRanges(text)
    var fence = 0
    var i = 0
    while (i < text.length) {
        // 代码块里的 `**` 只是代码，跳过整段
        while (fence < fences.size && i > fences[fence].last) fence++
        if (fence < fences.size && i >= fences[fence].first) {
            i = fences[fence].last + 1
            continue
        }
        when {
            text.startsWith("**", i) -> {
                val end = text.indexOf("**", i + 2)
                if (end > i) {
                    spans.add(InlineSpan("**", "**", i, end + 2, end))
                    i = end + 2
                } else {
                    i += 2
                }
            }

            text.startsWith("~~", i) -> {
                val end = text.indexOf("~~", i + 2)
                if (end > i) {
                    spans.add(InlineSpan("~~", "~~", i, end + 2, end))
                    i = end + 2
                } else {
                    i += 2
                }
            }

            text[i] == '`' -> {
                val end = text.indexOf('`', i + 1)
                if (end > i) {
                    spans.add(InlineSpan("`", "`", i, end + 1, end))
                    i = end + 1
                } else {
                    i++
                }
            }

            text[i] == '*' || text[i] == '_' -> {
                // 和 inline 一样：`* 空格开头` 不算斜体，避免把项目符号吃进去
                val marker = text[i].toString()
                val next = text.getOrNull(i + 1)
                val end = if (next != null && !next.isWhitespace()) text.indexOf(marker, i + 1) else -1
                if (end > i + 1) {
                    spans.add(InlineSpan(marker, marker, i, end + 1, end))
                    i = end + 1
                } else {
                    i++
                }
            }

            text[i] == '[' -> {
                // 括号要配对找（`[前半[sticker:x:a.png]后半](url)` 里有嵌套），
                // 只看第一个 `]` 会把它当成表情包自己的括号，整段链接就漏掉了
                val close = matchingBracket(text, i)
                val paren = if (close > i && text.getOrNull(close + 1) == '(') text.indexOf(')', close + 2) else -1
                if (close > i && paren > close) {
                    // 链接拆开要带完整目标才能重开：`[后半段](url)`
                    val url = text.substring(close + 2, paren).trim()
                    spans.add(InlineSpan("[", "]($url)", i, paren + 1, close))
                    i = paren + 1
                } else {
                    i++
                }
            }

            else -> i++
        }
    }
    return spans
}

/** 从 [open] 处的 `[` 找配对的那个 `]`（支持嵌套）；没有配对返回 -1。 */
private fun matchingBracket(text: String, open: Int): Int {
    var depth = 0
    var i = open
    while (i < text.length) {
        when (text[i]) {
            '[' -> depth++
            ']' -> {
                depth--
                if (depth == 0) return i
            }
        }
        i++
    }
    return -1
}

/** ``` / ~~~ 围栏代码块的范围（含围栏本身），块里的标记不参与格式配对。 */
private fun fencedRanges(text: String): List<IntRange> {
    val ranges = ArrayList<IntRange>()
    var open = -1
    var offset = 0
    for (line in text.split("\n")) {
        val trimmed = line.trim()
        if (trimmed.startsWith("```") || trimmed.startsWith("~~~")) {
            if (open < 0) {
                open = offset
            } else {
                ranges.add(open..(offset + line.length - 1).coerceAtLeast(open))
                open = -1
            }
        }
        offset += line.length + 1
    }
    if (open >= 0) ranges.add(open..(text.length - 1).coerceAtLeast(open))
    return ranges
}
