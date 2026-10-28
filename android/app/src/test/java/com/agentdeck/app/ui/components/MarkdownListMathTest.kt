package com.agentdeck.app.ui.components

import org.junit.Assert.assertTrue
import org.junit.Test

/** 列表项里的行内公式也要保留 $...$，渲染层才能排版（网页端是整段统一 mathify）。 */
class MarkdownListMathTest {

    @Test
    fun bulletKeepsInlineMath() {
        val blocks = parseMarkdown("- 组合：浙大版大量写 \$C_n^m\$，这个基本没跑")
        val bullet = blocks.filterIsInstance<MdBlock.Bullet>().single()
        assertTrue("列表项里的公式被吃掉了：${bullet.items}", bullet.items.first().contains("\$C_n^m\$"))
    }

    @Test
    fun numberedKeepsInlineMath() {
        val blocks = parseMarkdown("1. 排列用 \$P_n^m\$ 表示")
        val numbered = blocks.filterIsInstance<MdBlock.Numbered>().single()
        assertTrue("有序列表里的公式被吃掉了：${numbered.items}", numbered.items.first().second.contains("\$P_n^m\$"))
    }
}
