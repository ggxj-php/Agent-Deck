package icu.taffy.deck.ui

import icu.taffy.deck.data.HistoryItem
import icu.taffy.deck.data.UploadedFile

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
                    var cursor = 0
                    for (match in ARTIFACT.findAll(item.text)) {
                        appendAssistantSegment(blocks, item.text.substring(cursor, match.range.first), stickerUrl)
                        blocks.add(Block.Artifact(match.groupValues[1], match.groupValues[2]))
                        cursor = match.range.last + 1
                    }
                    appendAssistantSegment(blocks, item.text.substring(cursor), stickerUrl)
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
    ) {
        if (segment.isEmpty()) return
        var cursor = 0
        var plain = StringBuilder()
        for (match in STICKER.findAll(segment)) {
            plain.append(segment, cursor, match.range.first)
            cursor = match.range.last + 1
            val text = plain.toString()
            if (text.isNotBlank()) blocks.add(Block.Text(text))
            plain = StringBuilder()
            // 历史里偶尔只有心情没有文件名（[sticker:laugh]），按心情从表情库随机挑一张；
            // 挑不到就干脆不显示，绝不能插一个空块占地方。
            val url = stickerUrl(match.groupValues[1], match.groupValues[2])
            if (url.isNotBlank()) blocks.add(Block.Sticker(url))
        }
        plain.append(segment.substring(cursor))
        if (plain.isNotBlank()) blocks.add(Block.Text(plain.toString()))
    }
}
