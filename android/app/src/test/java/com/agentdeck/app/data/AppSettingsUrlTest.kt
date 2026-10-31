package com.agentdeck.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 服务器地址规范化。
 *
 * 用户在登录页可能只输 `deck.example.org`，也可能输 `192.168.1.5:8000`，
 * 两种都得补成能直接请求的完整 URL，否则 OkHttp 直接抛 unknown host。
 */
class AppSettingsUrlTest {

    @Test
    fun domainGetsHttps() {
        assertEquals("https://deck.example.org", AppSettings.normalize("deck.example.org"))
    }

    @Test
    fun privateAddressGetsHttp() {
        assertEquals("http://192.168.1.5:8000", AppSettings.normalize("192.168.1.5:8000"))
        assertEquals("http://10.0.0.2:8080", AppSettings.normalize("10.0.0.2:8080"))
        assertEquals("http://172.20.1.1", AppSettings.normalize("172.20.1.1"))
        assertEquals("http://localhost:8000", AppSettings.normalize("localhost:8000"))
    }

    @Test
    fun keepsExplicitScheme() {
        assertEquals("http://example.org", AppSettings.normalize("http://example.org"))
        assertEquals("https://example.org", AppSettings.normalize("https://example.org"))
    }

    @Test
    fun stripsTrailingSlashAndSpaces() {
        assertEquals("https://example.org", AppSettings.normalize("  https://example.org/  "))
    }

    @Test
    fun emptyStaysEmpty() {
        assertEquals("", AppSettings.normalize("   "))
    }

    @Test
    fun placeholderIsDetected() {
        assertTrue(AppSettings.isPlaceholder(""))
        assertTrue(AppSettings.isPlaceholder("https://example.com"))
        assertTrue(AppSettings.isPlaceholder("https://your-server.example"))
        assertFalse(AppSettings.isPlaceholder("https://deck.example.org"))
    }

    /** 本机调试用的地址是真地址，不能一直当「还没配」逼用户重填。 */
    @Test
    fun localhostIsNotPlaceholder() {
        assertFalse(AppSettings.isPlaceholder("http://localhost:8000"))
        assertFalse(AppSettings.isPlaceholder("http://127.0.0.1:8000"))
        assertFalse(AppSettings.isPlaceholder("http://192.168.1.5:8000"))
    }
}
