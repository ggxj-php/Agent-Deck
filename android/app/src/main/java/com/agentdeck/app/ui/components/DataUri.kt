package com.agentdeck.app.ui.components

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64

/**
 * `data:image/jpeg;base64,...` 这种本地图片：Coil 不认 data URI，
 * 刚发出去、还没换回服务器路径的那张图就会空着。这里自己解成 Bitmap。
 */
fun decodeDataUri(data: String?): Bitmap? = runCatching {
    if (data.isNullOrBlank() || !data.startsWith("data:")) return null
    val payload = if (data.contains("base64,")) data.substringAfter("base64,") else return null
    val bytes = Base64.decode(payload, Base64.DEFAULT)
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
}.getOrNull()

/** 只在确实是 data URI 时才解码，避免每次重组都算一遍。 */
fun isDataUri(url: String): Boolean = url.startsWith("data:")
