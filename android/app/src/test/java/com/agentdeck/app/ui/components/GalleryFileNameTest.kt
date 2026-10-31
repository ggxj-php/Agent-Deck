package com.agentdeck.app.ui.components

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 「保存图片」落盘时的命名规则：服务器给我的多是哈希名，得换成相册里能认的名字。
 * 这里只测纯函数，真正的 MediaStore 写入在真机上验。
 */
class GalleryFileNameTest {

    @Test
    fun keepsPngFromUrlWhenServerSaysNothing() {
        val (name, mime) = galleryFileName("https://www.example.com/api/persona/sticker/9f3a1c.png", null, 1_700_000_000_000)
        assertEquals("agentdeck-1700000000000.png", name)
        assertEquals("image/png", mime)
    }

    @Test
    fun serverMimeBeatsUrlSuffix() {
        // 有的后端会把图片转码，URL 后缀不可信，Content-Type 才描述真实字节
        val (name, mime) = galleryFileName("https://www.example.com/a.png", "image/jpeg", 42)
        assertEquals("agentdeck-42.jpeg", name)
        assertEquals("image/jpeg", mime)
    }

    @Test
    fun mimeWithCharsetAndPlusIsSplit() {
        val (name, mime) = galleryFileName("https://www.example.com/a", "image/svg+xml; charset=utf-8", 7)
        assertEquals("agentdeck-7.svg", name)
        assertEquals("image/svg+xml", mime)
    }

    @Test
    fun queryStringAndFragmentDoNotConfuseExtension() {
        val (name, mime) = galleryFileName("https://www.example.com/a.webp?v=2#top", null, 5)
        assertEquals("agentdeck-5.webp", name)
        assertEquals("image/webp", mime)
    }

    @Test
    fun uppercaseSuffixIsLowercased() {
        val (name, _) = galleryFileName("https://www.example.com/A.PNG", null, 9)
        assertEquals("agentdeck-9.png", name)
    }

    @Test
    fun fallsBackToPngForDataUriWithoutMime() {
        // data URI 的 source 传 null，只有 mime 可用
        val (name, mime) = galleryFileName(null, null, 11)
        assertEquals("agentdeck-11.png", name)
        assertEquals("image/png", mime)
    }

    @Test
    fun unknownSuffixFallsBackToPng() {
        val (name, mime) = galleryFileName("https://www.example.com/download", "application/octet-stream", 12)
        assertEquals("agentdeck-12.png", name)
        assertEquals("image/png", mime)
    }

    @Test
    fun noSlashOnlyFileName() {
        val (name, mime) = galleryFileName("sticker-1024.webp", "image/webp", 13)
        assertEquals("agentdeck-13.webp", name)
        assertEquals("image/webp", mime)
    }
}
