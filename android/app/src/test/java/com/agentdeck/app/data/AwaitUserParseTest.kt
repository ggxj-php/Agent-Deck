package com.agentdeck.app.data

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** 服务端 ask_user 的 SSE 事件解析（字段名和默认值都要对）。 */
class AwaitUserParseTest {

    @Test
    fun parsesQuestionOptionsAndCustom() {
        val event = parseAwaitUser(
            JSONObject(
                """{"type":"await_user","question":"要哪种风格？","options":["学术","活泼","商务"],"allow_custom":false}""",
            ),
        )
        assertEquals("要哪种风格？", event.question)
        assertEquals(listOf("学术", "活泼", "商务"), event.options)
        assertEquals(false, event.allowCustom)
    }

    @Test
    fun defaultsAllowCustomToTrue() {
        val event = parseAwaitUser(JSONObject("""{"type":"await_user","question":"继续吗","options":["好"]}"""))
        assertEquals(true, event.allowCustom)
    }

    @Test
    fun dropsBlankOptions() {
        val event = parseAwaitUser(
            JSONObject("""{"type":"await_user","question":"选","options":[" A ","  ","B"]}"""),
        )
        assertEquals(listOf("A", "B"), event.options)
    }

    @Test
    fun missingQuestionIsEmpty() {
        val event = parseAwaitUser(JSONObject("""{"type":"await_user","options":[]}"""))
        assertTrue(event.question.isEmpty())
        assertTrue(event.options.isEmpty())
    }
}
