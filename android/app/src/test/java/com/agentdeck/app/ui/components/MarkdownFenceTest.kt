package com.agentdeck.app.ui.components

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 用真实回复做回归：这段回复里有两个代码块，第二个是 ASCII 动画（里面有 `~ ~ ~` 波浪线），
 * 代码块之后还有正文和表情包。围栏误判的话，波浪线/结尾正文会被拖进代码块。
 */
class MarkdownFenceTest {

    private fun loadFixture(): String =
        javaClass.classLoader!!.getResourceAsStream("ascii_art.md")!!
            .bufferedReader(Charsets.UTF_8)
            .use { it.readText() }

    @Test
    fun asciiArtStaysInsideCodeBlock() {
        val blocks = parseMarkdown(loadFixture())

        val codes = blocks.filterIsInstance<MdBlock.Code>()
        assertEquals("应该正好两个代码块，实际：${codes.map { it.lang }}", 2, codes.size)
        assertEquals("python", codes[0].lang)
        assertEquals("", codes[1].lang)

        // 第二个代码块里必须有 ASCII 画的头和两条波浪线
        val art = codes[1].code
        val diagnostic = "langs=" + codes.map { it.lang } +
            " | artHead=" + art.take(40).replace("\n", "\\n") +
            " | artTail=" + art.takeLast(60).replace("\n", "\\n")
        assertTrue("ASCII 画被截断：$diagnostic", art.contains(".--."))
        assertTrue("波浪线没包进代码块：$diagnostic", art.contains("~  ~  ~"))
        assertTrue(
            "结尾波浪线丢了：$diagnostic",
            art.lines().last().trimStart().startsWith("~"),
        )

        // 代码块之后必须是正文，不能变成新的代码块
        val tail = blocks.dropWhile { it !== codes[1] }.drop(1)
        val tailText = tail.joinToString("\n") {
            when (it) {
                is MdBlock.Paragraph -> it.text
                is MdBlock.Heading -> it.text
                is MdBlock.Bullet -> it.items.joinToString("\n")
                else -> ""
            }
        }
        assertTrue("结尾正文没出现：$tailText", tailText.contains("要不要本鱼再加点花样"))
        assertTrue("结尾正文被吞进代码块", tail.none { it is MdBlock.Code })
    }
}
