@file:OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)

package com.agentdeck.app.ui.components
import androidx.compose.material3.TextButton
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.withStyle


import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontSynthesis
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.agentdeck.app.ui.theme.DeckPalette

/* ------------------------------------------------------------------
 * 原生 markdown 渲染：不再为每条消息开 WebView。
 * 覆盖模型实际会输出的子集：标题 / 段落 / 列表 / 代码块 / 行内代码 /
 * 引用 / 表格 / 分割线 / 链接 / 图片 / $$ 公式块 / 表情包标记。
 * 数学按网页同一套规则降级成 Unicode，读起来不费劲。
 * ------------------------------------------------------------------ */

sealed interface MdBlock {
    data class Heading(val level: Int, val text: String) : MdBlock
    data class Paragraph(val text: String) : MdBlock
    data class Bullet(val items: List<String>) : MdBlock
    data class Numbered(val items: List<Pair<String, String>>) : MdBlock
    data class Code(val lang: String, val code: String) : MdBlock
    data class Quote(val text: String) : MdBlock
    data class Table(val header: List<String>, val rows: List<List<String>>) : MdBlock
    data object Rule : MdBlock
    data class MathBlock(val latex: String) : MdBlock
    data class Picture(val url: String, val alt: String) : MdBlock

    /** agent 给的临时附件链接（/d/<token>/<name>），需要登录态才能下。 */
    data class Download(val label: String, val path: String) : MdBlock
}

private val STICKER = Regex("""\[sticker:([A-Za-z_]+)(?::([^\]\s]+))?]""")
private val FENCE = Regex("""^\s*(`{3,}|~{3,})\s*([A-Za-z0-9+#._-]*)\s*$""")
private val IMAGE_LINE = Regex("""^!\[([^\]]*)]\(([^)\s]+)\)$""")

/** 行内公式：$...$、$$...$$、\(...\) 都算。 */
private val INLINE_MATH = Regex("""\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$|\\\(([\s\S]+?)\\\)""")

internal data class InlineSegment(val text: String, val math: Boolean)

internal fun splitInlineMath(source: String): List<InlineSegment> {
    val out = mutableListOf<InlineSegment>()
    var last = 0
    for (match in INLINE_MATH.findAll(source)) {
        if (match.range.first > last) {
            out.add(InlineSegment(source.substring(last, match.range.first), false))
        }
        val latex = match.groupValues.drop(1).firstOrNull { it.isNotEmpty() } ?: ""
        out.add(InlineSegment(latex, true))
        last = match.range.last + 1
    }
    if (last < source.length) out.add(InlineSegment(source.substring(last), false))
    return out
}

private val DOWNLOAD_LINE = Regex("""\[([^\]]*)]\((/d/[^)\s]+)\)""")
private val HEADING = Regex("""^(#{1,6})\s+(.*)$""")
private val BULLET = Regex("""^\s*[-*+]\s+(.*)$""")
private val ORDERED = Regex("""^\s*(\d+)[.)]\s+(.*)$""")
private val RULE = Regex("""^\s*([-*_])\1{2,}\s*$""")
private val TABLE_DIVIDER = Regex("""^\s*\|?[\s:|-]+\|[\s:|-]*$""")

fun parseMarkdown(source: String): List<MdBlock> {
    val blocks = mutableListOf<MdBlock>()
    // 统一换行：模型偶尔吐 CRLF 或单独的 CR，留着会多出空行和看不见的字符
    val lines = source.replace("\r\n", "\n").replace('\r', '\n').split("\n")
    var index = 0

    fun flushParagraph(buffer: MutableList<String>) {
        if (buffer.isEmpty()) return
        val text = buffer.joinToString("\n").trim()
        buffer.clear()
        if (text.isEmpty()) return
        // 整段就是一个公式（$...$ / $$...$$）：交给原生 LaTeX 渲染
        val singleDollar = Regex("""^\$([^$\n]+)\$$""").find(text)
        val doubleDollar = Regex("""^\$\$([\s\S]+)\$\$$""").find(text)
        when {
            doubleDollar != null -> blocks += MdBlock.MathBlock(doubleDollar.groupValues[1].trim())
            singleDollar != null -> blocks += MdBlock.MathBlock(singleDollar.groupValues[1].trim())
            else -> {
                val download = DOWNLOAD_LINE.find(text)
                val image = IMAGE_LINE.find(text)
                when {
                    // 链接可能带前缀（比如「👉 [手册.docx](/d/xxx)」），前后文字拆成段落，
                    // 中间那段一定要渲染成可下载的卡片，而不是普通文字链接。
                    download != null -> {
                        val before = text.substring(0, download.range.first).trim()
                        if (before.isNotEmpty()) blocks += MdBlock.Paragraph(demath(before))
                        blocks += MdBlock.Download(
                            download.groupValues[1].ifBlank { "下载附件" },
                            download.groupValues[2],
                        )
                        val after = text.substring(download.range.last + 1).trim()
                        if (after.isNotEmpty()) blocks += MdBlock.Paragraph(demath(after))
                    }

                    image != null -> blocks += MdBlock.Picture(image.groupValues[2], image.groupValues[1])
                    else -> blocks += MdBlock.Paragraph(text)
                }
            }
        }
    }

    val paragraph = mutableListOf<String>()
    while (index < lines.size) {
        val line = lines[index]
        val trimmed = line.trim()

        // 代码块：围栏必须「整行都是同一个符号」且结束围栏不低于开始长度。
        // 之前只判断 startsWith("~~~")，ASCII 画里的 ~~~~~ 会被当成结束围栏，
        // 后面的内容就被错误地拖进/拖出代码块（波浪线跑到表情包上面、结尾整段变黑）。
        val fence = FENCE.find(line)
        if (fence != null) {
            flushParagraph(paragraph)
            val marker = fence.groupValues[1]
            val lang = fence.groupValues[2].trim()
            val fenceChar = marker[0]
            val minLength = marker.length
            val closing = Regex("""^\s*""" + Regex.escape(fenceChar.toString()) + "{$minLength,}\\s*$")
            val code = StringBuilder()
            index++
            while (index < lines.size && !closing.matches(lines[index])) {
                code.append(lines[index]).append('\n')
                index++
            }
            if (index < lines.size) index++ // 跳过结束围栏
            blocks += MdBlock.Code(lang, code.toString().trimEnd('\n'))
            continue
        }

        // $$ 公式块（可能跨行）
        if (trimmed.startsWith("$$")) {
            flushParagraph(paragraph)
            var closed = trimmed.endsWith("$$") && trimmed.length > 3
            val body = StringBuilder(trimmed.removePrefix("$$"))
            if (!closed) {
                index++
                while (index < lines.size) {
                    val current = lines[index]
                    if (current.trim().endsWith("$$")) {
                        body.append('\n').append(current.trim().removeSuffix("$$"))
                        closed = true
                        break
                    }
                    body.append('\n').append(current)
                    index++
                }
            } else {
                body.setLength(body.length - 2)
            }
            index++
            if (closed) {
                blocks += MdBlock.MathBlock(body.toString().trim())
            } else {
                // 还没闭合（流式中途）：先按普通文本显示，等 $$ 收尾再变公式，
                // 否则会拿半截 LaTeX 去渲染，出现一堆原始命令
                blocks += MdBlock.Paragraph("$$" + body.toString().trimEnd())
            }
            continue
        }

        // 空行：段落结束
        if (trimmed.isEmpty()) {
            flushParagraph(paragraph)
            index++
            continue
        }

        // 分割线
        if (RULE.matches(trimmed)) {
            flushParagraph(paragraph)
            blocks += MdBlock.Rule
            index++
            continue
        }

        // 标题
        val heading = HEADING.find(trimmed)
        if (heading != null) {
            flushParagraph(paragraph)
            blocks += MdBlock.Heading(heading.groupValues[1].length, demath(heading.groupValues[2].trim()))
            index++
            continue
        }

        // 表格
        if (trimmed.startsWith("|") && index + 1 < lines.size && TABLE_DIVIDER.matches(lines[index + 1].trim())) {
            flushParagraph(paragraph)
            val header = splitRow(trimmed)
            val rows = mutableListOf<List<String>>()
            index += 2
            while (index < lines.size && lines[index].trim().startsWith("|")) {
                rows += splitRow(lines[index].trim())
                index++
            }
            blocks += MdBlock.Table(header, rows)
            continue
        }

        // 引用
        if (trimmed.startsWith(">")) {
            flushParagraph(paragraph)
            val quote = StringBuilder()
            while (index < lines.size && lines[index].trim().startsWith(">")) {
                if (quote.isNotEmpty()) quote.append('\n')
                quote.append(lines[index].trim().removePrefix(">").trim())
                index++
            }
            blocks += MdBlock.Quote(demath(quote.toString()))
            continue
        }

        // 无序列表
        if (BULLET.matches(line)) {
            flushParagraph(paragraph)
            val items = mutableListOf<String>()
            while (index < lines.size && BULLET.matches(lines[index])) {
                items += demath(BULLET.find(lines[index])!!.groupValues[1])
                index++
            }
            blocks += MdBlock.Bullet(items)
            continue
        }

        // 有序列表
        if (ORDERED.matches(line)) {
            flushParagraph(paragraph)
            val items = mutableListOf<Pair<String, String>>()
            while (index < lines.size && ORDERED.matches(lines[index])) {
                val match = ORDERED.find(lines[index])!!
                items += match.groupValues[1] to demath(match.groupValues[2])
                index++
            }
            blocks += MdBlock.Numbered(items)
            continue
        }

        paragraph += line
        index++
    }
    flushParagraph(paragraph)
    return blocks
}

private fun splitRow(line: String): List<String> =
    line.trim().trim('|').split('|').map { demath(it.trim()) }

@Composable
fun MarkdownText(
    text: String,
    palette: DeckPalette,
    baseUrl: String,
    onPreview: (String) -> Unit = {},
) {
    val blocks = remember(text) { parseMarkdown(text) }
    Column {
        for (block in blocks) {
            MdBlockView(block, palette, baseUrl, onPreview)
        }
    }
}

@Composable
private fun MdBlockView(
    block: MdBlock,
    palette: DeckPalette,
    baseUrl: String,
    onPreview: (String) -> Unit,
) {
    // 打开字体合成，保证没有粗体字面的中文字体也能"加粗"
    val syncedStyle = TextStyle(fontSynthesis = FontSynthesis.All)
    when (block) {
        is MdBlock.Heading -> {
            val text = remember(block.text, palette.accent) { inline(block.text, palette) }
            Text(
                text = text,
                style = syncedStyle,
                fontSize = when (block.level) {
                    1 -> 19.sp
                    2 -> 17.sp
                    3 -> 16.sp
                    else -> 15.sp
                },
                lineHeight = 24.sp,
                fontWeight = FontWeight.W800,
                color = palette.ink,
                modifier = Modifier.padding(top = 10.dp, bottom = 4.dp),
            )
        }

        is MdBlock.Paragraph -> {
            val segments = remember(block.text) { splitInlineMath(block.text) }
            if (segments.none { it.math }) {
                val text = remember(block.text, palette.accent) { inline(block.text, palette) }
                Text(
                    text = text,
                    style = syncedStyle,
                    fontSize = 15.sp,
                    lineHeight = 22.sp,
                    color = palette.ink,
                    modifier = Modifier.padding(vertical = 3.dp),
                )
            } else {
                // 行内公式：文字和公式混排（网页端 KaTeX 的行内效果）
                androidx.compose.foundation.layout.FlowRow(
                    modifier = Modifier.padding(vertical = 3.dp),
                    horizontalArrangement = Arrangement.spacedBy(2.dp),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    segments.forEach { segment ->
                        if (segment.math) {
                            LatexImage(
                                latex = segment.text,
                                palette = palette,
                                textSize = 15,
                            )
                        } else if (segment.text.isNotBlank()) {
                            Text(
                                text = remember(segment.text, palette.accent) {
                                    inline(demath(segment.text), palette)
                                },
                                style = syncedStyle,
                                fontSize = 15.sp,
                                lineHeight = 22.sp,
                                color = palette.ink,
                            )
                        }
                    }
                }
            }
        }

        is MdBlock.Bullet -> Column(modifier = Modifier.padding(vertical = 2.dp)) {
            block.items.forEach { item ->
                Row(modifier = Modifier.padding(vertical = 1.dp)) {
                    Text("•", fontSize = 15.sp, color = palette.accent)
                    Spacer(Modifier.width(6.dp))
                    val text = remember(item, palette.accent) { inline(item, palette) }
                    Text(
                        text = text,
                        style = syncedStyle,
                        fontSize = 15.sp,
                        lineHeight = 22.sp,
                        color = palette.ink,
                    )
                }
            }
        }

        is MdBlock.Numbered -> Column(modifier = Modifier.padding(vertical = 2.dp)) {
            block.items.forEach { (number, item) ->
                Row(modifier = Modifier.padding(vertical = 1.dp)) {
                    Text("$number.", fontSize = 15.sp, color = palette.accent)
                    Spacer(Modifier.width(6.dp))
                    val text = remember(item, palette.accent) { inline(item, palette) }
                    Text(
                        text = text,
                        style = syncedStyle,
                        fontSize = 15.sp,
                        lineHeight = 22.sp,
                        color = palette.ink,
                    )
                }
            }
        }

        is MdBlock.Code -> CodeBlock(block.lang, block.code)

        is MdBlock.Quote -> {
            val text = remember(block.text, palette.accent) { inline(block.text, palette) }
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 4.dp),
            ) {
                Box(
                    modifier = Modifier
                        .width(3.dp)
                        .heightIn(min = 18.dp)
                        .background(palette.line, RoundedCornerShape(2.dp)),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = text,
                    style = syncedStyle,
                    fontSize = 14.sp,
                    lineHeight = 21.sp,
                    fontStyle = FontStyle.Italic,
                    color = palette.inkSoft,
                )
            }
        }

        is MdBlock.Table -> {
            val columns = maxOf(block.header.size, block.rows.maxOfOrNull { it.size } ?: 0)
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 5.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .border(1.dp, palette.line, RoundedCornerShape(8.dp)),
            ) {
                TableRow(block.header, columns, palette, header = true)
                block.rows.forEach { row -> TableRow(row, columns, palette, header = false) }
            }
        }

        MdBlock.Rule -> Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 8.dp)
                .height(1.dp)
                .background(palette.line),
        )

        is MdBlock.MathBlock -> Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 5.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(palette.softFill)
                .padding(horizontal = 10.dp, vertical = 8.dp),
            contentAlignment = Alignment.Center,
        ) {
            LatexImage(latex = block.latex, palette = palette)
        }

        is MdBlock.Download -> DownloadCard(
            label = block.label,
            path = block.path,
            baseUrl = baseUrl,
            palette = palette,
        )

        is MdBlock.Picture -> AsyncImage(
            model = resolveUrl(baseUrl, block.url).let { url ->
                if (isDataUri(url)) decodeDataUri(url) ?: url else url
            },
            contentDescription = block.alt,
            contentScale = ContentScale.FillWidth,
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 4.dp)
                .heightIn(max = 280.dp)
                .clip(RoundedCornerShape(10.dp))
                .clickable { onPreview(resolveUrl(baseUrl, block.url)) },
        )
    }
}

@Composable
private fun CodeBlock(lang: String, code: String) {
    val clipboard = LocalClipboardManager.current
    var copied by remember { mutableStateOf(false) }
    LaunchedEffect(copied) {
        if (copied) {
            kotlinx.coroutines.delay(1500)
            copied = false
        }
    }
    val highlighted = remember(code, lang) { highlightCode(code, lang) }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 5.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(Color(0xFF23252F)),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 10.dp, end = 4.dp, top = 3.dp),
        ) {
            Text(
                text = lang.ifBlank { "text" },
                fontSize = 10.sp,
                color = Color(0xFF9AA0B5),
                modifier = Modifier.weight(1f),
            )
            TextButton(
                onClick = {
                    clipboard.setText(AnnotatedString(code))
                    copied = true
                },
                contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 8.dp, vertical = 0.dp),
            ) {
                Text(
                    text = if (copied) "已复制" else "复制",
                    fontSize = 11.sp,
                    color = if (copied) Color(0xFF7FD18B) else Color(0xFF9AA0B5),
                )
            }
        }
        Text(
            text = highlighted,
            fontFamily = FontFamily.Monospace,
            fontSize = 12.sp,
            lineHeight = 17.sp,
            color = Color(0xFFE6E6EE),
            modifier = Modifier
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 10.dp, vertical = 8.dp),
        )
    }
}

/* ---------------- 代码高亮（轻量、按语言给关键字） ---------------- */

private val KEYWORDS_KOTLIN = setOf(
    "fun", "val", "var", "if", "else", "when", "for", "while", "return", "class",
    "object", "interface", "import", "package", "null", "true", "false", "try",
    "catch", "finally", "throw", "in", "is", "as", "override", "private", "public",
    "protected", "suspend", "data", "sealed", "companion", "init", "this", "super",
)
private val KEYWORDS_JAVA = setOf(
    "public", "private", "protected", "class", "interface", "void", "int", "long",
    "double", "float", "boolean", "char", "String", "if", "else", "for", "while",
    "return", "new", "static", "final", "try", "catch", "finally", "throw", "import",
    "package", "null", "true", "false", "extends", "implements", "this", "super",
)
private val KEYWORDS_PYTHON = setOf(
    "def", "class", "import", "from", "return", "if", "elif", "else", "for", "while",
    "in", "not", "and", "or", "None", "True", "False", "try", "except", "finally",
    "with", "as", "lambda", "yield", "global", "pass", "raise", "break", "continue",
)
private val KEYWORDS_JS = setOf(
    "function", "const", "let", "var", "if", "else", "for", "while", "return", "class",
    "new", "import", "export", "from", "async", "await", "try", "catch", "throw",
    "null", "undefined", "true", "false", "typeof", "instanceof", "this",
)
private val KEYWORDS_C = setOf(
    "int", "char", "float", "double", "void", "if", "else", "for", "while", "return",
    "struct", "class", "public", "private", "const", "static", "include", "define",
    "namespace", "using", "new", "delete", "true", "false", "nullptr", "unsigned",
)
private val KEYWORDS_SHELL = setOf(
    "if", "then", "else", "fi", "for", "while", "do", "done", "case", "esac",
    "function", "echo", "exit", "export", "local", "return", "sudo", "cd", "rm",
)
private val KEYWORDS_SQL = setOf(
    "select", "from", "where", "insert", "into", "values", "update", "set", "delete",
    "join", "left", "right", "inner", "on", "group", "by", "order", "having", "limit",
    "create", "table", "alter", "drop", "and", "or", "not", "null", "as", "distinct",
)
private val KEYWORDS_COMMON = KEYWORDS_KOTLIN + KEYWORDS_PYTHON + KEYWORDS_JS

private val HASH_COMMENT_LANGS = setOf(
    "py", "python", "sh", "bash", "shell", "zsh", "rb", "ruby", "yaml", "yml", "toml", "makefile",
)

private fun highlightCode(code: String, lang: String): AnnotatedString {
    val key = lang.lowercase()
    val keywords = when (key) {
        "kt", "kts", "kotlin" -> KEYWORDS_KOTLIN
        "java" -> KEYWORDS_JAVA
        "py", "python" -> KEYWORDS_PYTHON
        "js", "javascript", "ts", "typescript", "jsx", "tsx" -> KEYWORDS_JS
        "c", "h", "cpp", "c++", "cc", "hpp" -> KEYWORDS_C
        "sh", "bash", "shell", "zsh", "ps1", "powershell" -> KEYWORDS_SHELL
        "sql" -> KEYWORDS_SQL
        "json", "xml", "html", "css", "text", "" -> emptySet()
        else -> KEYWORDS_COMMON
    }
    val commentPart = if (key in HASH_COMMENT_LANGS) "#[^\\n]*" else "(?://[^\\n]*|#[^\\n]*|/\\*[\\s\\S]*?\\*/)"
    val keywordPart = if (keywords.isEmpty()) "" else "|\\b(?:" + keywords.joinToString("|") + ")\\b"
    val token = Regex(
        "(/\\*[\\s\\S]*?\\*/|$commentPart)" +
            "|(\"(?:\\\\.|[^\"\\\\])*\"|'(?:\\\\.|[^'\\\\])*')" +
            "|(\\b\\d+(?:\\.\\d+)?\\b)" +
            keywordPart,
    )

    return buildAnnotatedString {
        var last = 0
        for (match in token.findAll(code)) {
            if (match.range.first > last) append(code.substring(last, match.range.first))
            val color = when {
                match.groups[1] != null -> Color(0xFF7C8B99)
                match.groups[2] != null -> Color(0xFFA8D08D)
                match.groups[3] != null -> Color(0xFFE8B37A)
                else -> Color(0xFFC792EA)
            }
            withStyle(SpanStyle(color = color)) { append(match.value) }
            last = match.range.last + 1
        }
        if (last < code.length) append(code.substring(last))
    }
}

@Composable
private fun TableRow(    cells: List<String>,
    columns: Int,
    palette: DeckPalette,
    header: Boolean,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(if (header) palette.softFill else Color.Transparent)
            .padding(horizontal = 8.dp, vertical = 6.dp),
    ) {
        for (column in 0 until columns) {
            val raw = cells.getOrElse(column) { "" }
            val text = remember(raw, palette.accent) { inline(raw, palette) }
            Text(
                text = text,
                style = TextStyle(fontSynthesis = FontSynthesis.All),
                fontSize = 12.5.sp,
                lineHeight = 18.sp,
                fontWeight = if (header) FontWeight.SemiBold else FontWeight.Normal,
                color = palette.ink,
                modifier = Modifier.weight(1f),
            )
        }
    }
    if (header) {
        Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(palette.line))
    }
}

/* ---------------- 行内格式 ---------------- */

/** 测试入口：只关心行内解析出来的纯文本，便于用单测验证内容没被吞。 */
internal fun inlinePlainText(text: String, palette: DeckPalette): String = inline(text, palette).text

private fun inline(text: String, palette: DeckPalette): AnnotatedString = buildAnnotatedString {
    // 中文字体大多没有独立的 Bold 字重，W900 + fontSynthesis 合成出来的加粗才看得出来
    val bold = SpanStyle(fontWeight = FontWeight.W900, color = Color(0xFF171219))
    val italic = SpanStyle(fontStyle = FontStyle.Italic)
    val strike = SpanStyle(textDecoration = TextDecoration.LineThrough)
    val code = SpanStyle(
        fontFamily = FontFamily.Monospace,
        fontSize = 13.sp,
        color = palette.pinkDeep,
        background = palette.softFill.copy(alpha = 0.9f),
    )

    var i = 0
    while (i < text.length) {
        when {
            text.startsWith("**", i) -> {
                val end = text.indexOf("**", i + 2)
                if (end > i) {
                    pushStyle(bold)
                    append(text.substring(i + 2, end))
                    pop()
                    i = end + 2
                } else {
                    append("**"); i += 2
                }
            }

            text.startsWith("~~", i) -> {
                val end = text.indexOf("~~", i + 2)
                if (end > i) {
                    pushStyle(strike)
                    append(text.substring(i + 2, end))
                    pop()
                    i = end + 2
                } else {
                    append("~~"); i += 2
                }
            }

            text[i] == '`' -> {
                val end = text.indexOf('`', i + 1)
                if (end > i) {
                    pushStyle(code)
                    append(text.substring(i + 1, end))
                    pop()
                    i = end + 1
                } else {
                    append('`'); i++
                }
            }

            text.startsWith("*", i) || text.startsWith("_", i) -> {
                val marker = text[i].toString()
                val end = text.indexOf(marker, i + 1)
                if (end > i + 1 && !text[i + 1].isWhitespace()) {
                    pushStyle(italic)
                    append(text.substring(i + 1, end))
                    pop()
                    i = end + 1
                } else {
                    append(marker); i++
                }
            }

            text.startsWith("[", i) -> {
                val close = text.indexOf(']', i + 1)
                val open = if (close > i && close + 1 < text.length && text[close + 1] == '(') {
                    text.indexOf(')', close + 2)
                } else -1
                if (close > i && open > close) {
                    val label = text.substring(i + 1, close)
                    val url = text.substring(close + 2, open).trim()
                    if (url.startsWith("http://") || url.startsWith("https://")) {
                        withLink(
                            LinkAnnotation.Url(
                                url,
                                TextLinkStyles(
                                    SpanStyle(
                                        color = palette.accent,
                                        textDecoration = TextDecoration.Underline,
                                    )
                                ),
                            )
                        ) { append(label.ifBlank { url }) }
                        i = open + 1
                    } else {
                        append(label)
                        i = open + 1
                    }
                } else {
                    append('['); i++
                }
            }

            else -> {
                append(text[i]); i++
            }
        }
    }
}

/* ---------------- LaTeX → Unicode ---------------- */

private val MATH_WORDS = mapOf(
    "times" to "×", "cdot" to "·", "div" to "÷", "pm" to "±", "mp" to "∓",
    "le" to "≤", "leq" to "≤", "ge" to "≥", "geq" to "≥", "ne" to "≠", "neq" to "≠",
    "approx" to "≈", "equiv" to "≡", "sim" to "∼", "propto" to "∝",
    "infty" to "∞", "sum" to "∑", "prod" to "∏", "int" to "∫", "oint" to "∮",
    "partial" to "∂", "nabla" to "∇", "forall" to "∀", "exists" to "∃",
    "in" to "∈", "notin" to "∉", "subset" to "⊂", "subseteq" to "⊆",
    "supset" to "⊃", "supseteq" to "⊇", "cup" to "∪", "cap" to "∩",
    "emptyset" to "∅", "therefore" to "∴", "because" to "∵",
    "rightarrow" to "→", "to" to "→", "Rightarrow" to "⇒", "implies" to "⇒",
    "leftarrow" to "←", "Leftarrow" to "⇐", "leftrightarrow" to "↔",
    "Leftrightarrow" to "⇔", "iff" to "⇔", "mapsto" to "↦",
    "ldots" to "…", "cdots" to "…", "dots" to "…", "vdots" to "⋮", "ddots" to "⋱",
    "angle" to "∠", "perp" to "⊥", "parallel" to "∥", "triangle" to "△", "circ" to "∘",
    "degree" to "°", "quad" to "  ", "qquad" to "    ",
    "alpha" to "α", "beta" to "β", "gamma" to "γ", "delta" to "δ", "epsilon" to "ε",
    "varepsilon" to "ε", "zeta" to "ζ", "eta" to "η", "theta" to "θ", "iota" to "ι",
    "kappa" to "κ", "lambda" to "λ", "mu" to "μ", "nu" to "ν", "xi" to "ξ", "pi" to "π",
    "rho" to "ρ", "sigma" to "σ", "tau" to "τ", "upsilon" to "υ", "phi" to "φ",
    "varphi" to "φ", "chi" to "χ", "psi" to "ψ", "omega" to "ω",
    "Gamma" to "Γ", "Delta" to "Δ", "Theta" to "Θ", "Lambda" to "Λ", "Xi" to "Ξ",
    "Pi" to "Π", "Sigma" to "Σ", "Upsilon" to "Υ", "Phi" to "Φ", "Psi" to "Ψ",
    "Omega" to "Ω",
)

private val FRAC = Regex("""\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}""")
private val SQRT_N = Regex("""\\sqrt\s*\[([^\]]*)]\s*\{([^{}]*)\}""")
private val SQRT = Regex("""\\sqrt\s*\{([^{}]*)\}""")
private val SUP = Regex("""\^\s*\{([^{}]*)\}""")
private val SUB = Regex("""_\s*\{([^{}]*)\}""")
private val TEXT_CMD = Regex("""\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}""")
private val LEFT_RIGHT = Regex("""\\(?:left|right)\s*""")
private val SPACING = Regex("""\\[,;!]""")
private val COMMAND = Regex("""\\([A-Za-z]+)""")

private fun renderMathBody(source: String): String =
    demathExpr(source).replace(Regex("""\s+"""), " ").trim()

private fun demathExpr(input: String): String {
    var text = input
    // 先把 \text{...} / \mathrm{...} 这层壳剥掉：否则 S_{\text{总}} 这种嵌套花括号
    // 会让后面的下标/分式正则一个都匹配不上（之前数学没渲染出来就是这个原因）。
    repeat(3) {
        text = text.replace(TEXT_CMD, "$1")
    }
    text = text.replace(SUP, "^($1)")
    text = text.replace(SUB, "_($1)")
    text = text.replace(FRAC, "($1)/($2)")
    text = text.replace(SQRT_N, "$1√($2)")
    text = text.replace(SQRT, "√($1)")
    text = text.replace(LEFT_RIGHT, "")
    text = text.replace(SPACING, " ")
    text = text.replace(COMMAND) { match ->
        MATH_WORDS[match.groupValues[1]] ?: match.value
    }
    return text
}

/** 把 $...$ / $$...$$ / \(...\) / \[...\] 都翻成可读文本；代码片段原样保留。 */
fun demath(source: String): String {
    val codePattern = Regex("""(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)""")
    val out = StringBuilder()
    var last = 0
    // 不能用 split(Regex)：Kotlin 会连分隔符（代码片段）一起丢掉，导致行内代码凭空消失
    for (match in codePattern.findAll(source)) {
        out.append(demathText(source.substring(last, match.range.first)))
        out.append(match.value)
        last = match.range.last + 1
    }
    out.append(demathText(source.substring(last)))
    return out.toString()
}

private fun demathText(segment: String): String = segment
    .replace(Regex("""\$\$([\s\S]+?)\$\$""")) { demathExpr(it.groupValues[1]) }
    .replace(Regex("""\$([^$\n]+?)\$""")) { demathExpr(it.groupValues[1]) }
    .replace(Regex("""\\\[([\s\S]+?)\\\]""")) { demathExpr(it.groupValues[1]) }
    .replace(Regex("""\\\(([\s\S]+?)\\\)""")) { demathExpr(it.groupValues[1]) }
