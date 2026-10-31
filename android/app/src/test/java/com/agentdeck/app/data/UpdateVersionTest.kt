package com.agentdeck.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 版本号比较：不能用字符串比。
 * 字符串会认为 "1.9" > "1.11"，而真实版本语义是 1.11 更新。
 */
class UpdateVersionTest {

    @Test
    fun comparesEachSegmentAsNumber() {
        assertTrue(UpdateChecker.compareVersion("1.11", "1.2") > 0)
        assertTrue(UpdateChecker.compareVersion("1.2", "1.11") < 0)
        assertTrue(UpdateChecker.compareVersion("2.0", "1.99") > 0)
    }

    @Test
    fun missingSegmentsCountAsZero() {
        assertEquals(0, UpdateChecker.compareVersion("1.10", "1.10.0"))
        assertEquals(0, UpdateChecker.compareVersion("1.10.0", "1.10"))
        assertTrue(UpdateChecker.compareVersion("1.10.1", "1.10") > 0)
    }

    @Test
    fun ignoresNonNumericSuffix() {
        // 1.12-debug 这种带后缀的，按 1.12 比
        assertEquals(0, UpdateChecker.compareVersion("1.12-debug", "1.12"))
        assertTrue(UpdateChecker.compareVersion("1.13-beta", "1.12") > 0)
    }

    @Test
    fun blankIsNeverNewer() {
        assertFalse(UpdateChecker.compareVersion("", "1.11") > 0)
        assertEquals(0, UpdateChecker.compareVersion("", ""))
    }
}
