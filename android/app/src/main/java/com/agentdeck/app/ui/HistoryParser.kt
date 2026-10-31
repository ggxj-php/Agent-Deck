package com.agentdeck.app.ui

import com.agentdeck.app.data.HistoryItem
import com.agentdeck.app.data.UploadedFile

/**
 * 把后端存的历史正文还原成内容块。
 *
 * 后端给的是「模型视角的纯文本」：助手正文里混着 `[[artifact:id|标题]]` 和
 * `[sticker:mood:name]`，用户消息里带着 `[图片 #id uploads/xxx.jpg：摘要]` 标记。
 * 这些标记不能直接丢给 markdown，得先拆出来，否则界面上会露出生硬的占位串。
 */
object HistoryParser {

    private val ARTIFACT = Regex("""\[\[artifact:([0-9a-fA-F]{8,32})\|([^\]\n]*)\]\]""")
    private val STICKER = Regex("""\[sticker:([A-Za-z_]+)(?::([^\]\s]+))?]""")
    private val IMAGE_MARK = Regex("""\[图片 #([0-9a-fA-F]{6,32})[^\]]*]""")

    // [附件 #id uploads/xxx.pdf（报告.pdf，812KB）：用户上传的文件…]
    private val FILE_MARK = Regex("""\[附件 #([0-9a-fA-F]+) uploads/[^\s（]+（([^，]+)，(\d+)KB）：[^\]]*]""")

    fun toMessages(items: List<HistoryItem>, stickerUrl: (String, String) -> String): List<UiMessage> {
        val out = ArrayList<UiMessage>()
        var seed = 0L
        for (item in items) {
            seed += 1
            when (item.role) {
                "user" -> {
                    val imageIds = IMAGE_MARK.findAll(item.text).map { it.groupValues[1] }.toList()
                    val files = FILE_MARK.findAll(item.text).map { match ->
                        UploadedFile(
                            id = match.groupValues[1],
                            name = match.groupValues[2],
                            size = match.groupValues[3].toLongOrNull()?.times(1024) ?: 0L,
                        )
                    }.toList()
                    var body = IMAGE_MARK.replace(item.text, "")
                    body = FILE_MARK.replace(body, "")
                    body = body.replace(Regex("""\n{3,}"""), "\n\n").trim()
                    if (body.isEmpty() && imageIds.isEmpty() && files.isEmpty()) continue
                    out.add(
                        UiMessage(
                            id = seed,
                            role = Role.USER,
                            blocks = listOf(Block.Text(body.ifEmpty { "（这条消息只有附件）" })),
                            images = imageIds.map { "/api/uploads/$it" },
                            files = files,
                        )
                    )
                }

                "assistant" -> {
                    val blocks = ArrayList<Block>()
                    // 代码块范围先算出来：代码里的 [sticker:...] / [[artifact:...]] 只是示例文本，
                    // 不能当成真标记拆出去（否则会把代码块劈成两半，正文也会被带进代码块）
                    val codeRanges = codeRanges(item.text)
                    // 历史里的表情包标记经常卡在句子中间，先挪到最近的句末/换行，
                    // 和网页端 snapStickersToBoundaries 一个规则（代码块里的标记不动）
                    val rawText = if (codeRanges.isEmpty()) snapStickersToBoundaries(item.text) else item.text
                    var cursor = 0
                    for (match in ARTIFACT.findAll(rawText)) {
                        if (codeRanges.any { match.range.first in it }) continue
                        appendAssistantSegment(
                            blocks,
                            rawText.substring(cursor, match.range.first),
                            stickerUrl,
                            codeRanges,
                            cursor,
                        )
                        blocks.add(Block.Artifact(match.groupValues[1], match.groupValues[2]))
                        cursor = match.range.last + 1
                    }
                    appendAssistantSegment(
                        blocks,
                        rawText.substring(cursor),
                        stickerUrl,
                        codeRanges,
                        cursor,
                    )
                    // 空回复（比如只调了工具）就不占位置
                    if (blocks.isEmpty()) continue

                    // 一次回复在库里可能是好几条连续的 assistant（工具调用会拆条），
                    // 网页端是合并成一个气泡的，这里照做，否则一条回复会裂成好几个。
                    val previous = out.lastOrNull()
                    if (previous != null && previous.role == Role.ASSISTANT) {
                        out[out.lastIndex] = previous.copy(blocks = previous.blocks + blocks)
                    } else {
                        out.add(UiMessage(id = seed, role = Role.ASSISTANT, blocks = blocks))
                    }
                }
            }
        }
        return out
    }

    private fun appendAssistantSegment(
        blocks: MutableList<Block>,
        segment: String,
        stickerUrl: (String, String) -> String,
        codeRanges: List<IntRange>,
        segmentStart: Int,
    ) {
        if (segment.isEmpty()) return
        var cursor = 0
        var plain = StringBuilder()
        for (match in STICKER.findAll(segment)) {
            // 代码块里的标记只是文本，跳过
            if (codeRanges.any { (segmentStart + match.range.first) in it }) continue
            plain.append(segment, cursor, match.range.first)
            cursor = match.range.last + 1
            // 贴纸标记前后常带着换行，直接塞进 Text 会渲染成空段落（看起来一大片空白）
            val text = plain.toString().trim('\n', ' ', '\t')
            if (text.isNotEmpty()) blocks.add(Block.Text(text))
            plain = StringBuilder()
            // 历史里偶尔只有心情没有文件名（[sticker:laugh]），按心情从表情库随机挑一张；
            // 挑不到就干脆不显示，绝不能插一个空块占地方。
            val url = stickerUrl(match.groupValues[1], match.groupValues[2])
            if (url.isNotBlank()) blocks.add(Block.Sticker(url))
        }
        plain.append(segment.substring(cursor))
        val tail = plain.toString().trim('\n', ' ', '\t')
        if (tail.isNotEmpty()) blocks.add(Block.Text(tail))
    }


/**
 * 把表情包标记挪到最近的句末标点/换行之后：块级表情包插在句子中间会把一句话劈开。
 * 规则和网页端 snapStickersToBoundaries 一致，找不到边界的挪到这段最后。
 */
internal fun snapStickersToBoundaries(text: String): String {
    val marks = STICKER.findAll(text).toList()
    if (marks.isEmpty()) return text
    val out = StringBuilder()
    val trailing = StringBuilder()
    var cursor = 0
    for (mark in marks) {
        if (mark.range.first < cursor) continue
        out.append(text, cursor, mark.range.first)
        // 已经站在句末（标记前面紧跟句号/换行）就原地不动，
        // 否则会被一路推到下一个句子之后，越挪越远
        val before = text.getOrNull(mark.range.first - 1)
        if (before != null && (before == '\n' || "。！？!?".contains(before))) {
            out.append(mark.value)
            cursor = mark.range.last + 1
            continue
        }
        var cut = -1
        var i = mark.range.last + 1
        while (i < text.length) {
            val ch = text[i]
            // 只认真正的句末：省略号/分号在中文里常是句中断续（「消耗…咕噜」），
            // 挪到那儿等于没挪，括号还是被劈成两半
            if (ch == '\n' || "。！？!?".contains(ch)) {
                cut = i + 1
                break
            }
            i++
        }
        if (cut == -1) {
            trailing.append(mark.value)
            cursor = mark.range.last + 1
        } else {
            out.append(text, mark.range.last + 1, cut).append(mark.value)
            cursor = cut
        }
    }
    out.append(text.substring(cursor)).append(trailing)
    return out.toString()
}

    /** 找出 markdown 代码块（``` / ~~~）在整段文本里的字符范围。 */
    private fun codeRanges(text: String): List<IntRange> {
        val ranges = mutableListOf<IntRange>()
        var open: Int? = null
        var offset = 0
        for (line in text.split("\n")) {
            val trimmed = line.trim()
            val isFence = trimmed.startsWith("```") || trimmed.startsWith("~~~")
            if (isFence) {
                if (open == null) open = offset else {
                    ranges.add(open!!..(offset + line.length))
                    open = null
                }
            }
            offset += line.length + 1
        }
        if (open != null) ranges.add(open!!..text.length)
        return ranges
    }
}
