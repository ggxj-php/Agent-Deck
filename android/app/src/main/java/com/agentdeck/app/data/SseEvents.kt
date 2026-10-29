package com.agentdeck.app.data

import org.json.JSONObject

/**
 * `await_user` 事件解析：question / options / allow_custom。
 * 抽成纯函数方便单测（服务端字段名是 allow_custom，默认 true）。
 */
internal fun parseAwaitUser(obj: JSONObject): ChatEvent.AwaitUser {
    val options = buildList {
        val array = obj.optJSONArray("options")
        if (array != null) {
            for (index in 0 until array.length()) {
                val text = array.optString(index).trim()
                if (text.isNotEmpty()) add(text)
            }
        }
    }
    return ChatEvent.AwaitUser(
        question = obj.optString("question").trim(),
        options = options,
        allowCustom = obj.optBoolean("allow_custom", true),
    )
}
