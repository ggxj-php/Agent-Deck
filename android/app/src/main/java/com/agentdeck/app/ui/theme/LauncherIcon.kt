package com.agentdeck.app.ui.theme

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Color
import kotlin.math.abs

/**
 * 让桌面的启动器图标跟着身份卡的色相走。
 *
 * Android 不允许运行时换图标，标准做法是：manifest 里挂若干个 activity-alias，
 * 每个 alias 用一个配色的图标，运行时只启用与当前主题色最接近的那一个。
 * 切换用 DONT_KILL_APP，避免把正在用的 App 杀掉。
 */
object LauncherIcon {

    private const val CLASS_PREFIX = "com.agentdeck.app."

    private data class Variant(val alias: String, val hue: Float)

    private val VARIANTS = listOf(
        Variant("LauncherPink", 330f),
        Variant("LauncherRed", 0f),
        Variant("LauncherOrange", 30f),
        Variant("LauncherYellow", 52f),
        Variant("LauncherGreen", 145f),
        Variant("LauncherCyan", 190f),
        Variant("LauncherBlue", 232f),
        Variant("LauncherPurple", 275f),
    )

    fun sync(context: Context, themeHex: String) {
        val hue = hueOf(themeHex) ?: return
        val target = VARIANTS.minByOrNull { angularDistance(it.hue, hue) } ?: return
        val packageManager = context.packageManager
        val packageName = context.packageName

        // 先把目标打开，再关掉其它的：中途不会出现「一个都没开」导致图标消失
        enable(packageManager, packageName, target, PackageManager.COMPONENT_ENABLED_STATE_ENABLED)
        VARIANTS.filter { it != target }.forEach { variant ->
            enable(packageManager, packageName, variant, PackageManager.COMPONENT_ENABLED_STATE_DISABLED)
        }
    }

    private fun enable(
        packageManager: PackageManager,
        packageName: String,
        variant: Variant,
        state: Int,
    ) {
        val component = ComponentName(packageName, CLASS_PREFIX + variant.alias)
        if (packageManager.getComponentEnabledSetting(component) == state) return
        runCatching {
            packageManager.setComponentEnabledSetting(
                component,
                state,
                PackageManager.DONT_KILL_APP,
            )
            android.util.Log.i(
                "LauncherIcon",
                "setComponentEnabledSetting ${component.flattenToShortString()} state=$state now=" +
                    packageManager.getComponentEnabledSetting(component),
            )
        }.onFailure {
            android.util.Log.w("LauncherIcon", "切换失败 ${component.flattenToShortString()}: $it")
        }
    }

    private fun hueOf(hex: String): Float? {
        val value = hex.trim().removePrefix("#")
        if (value.length != 6) return null
        val parsed = value.toLongOrNull(16) ?: return null
        val hsv = FloatArray(3)
        Color.colorToHSV(0xFF000000.toInt() or parsed.toInt(), hsv)
        return hsv[0]
    }

    /** 色相是环形的：350° 和 10° 只差 20°。 */
    private fun angularDistance(a: Float, b: Float): Float {
        val diff = abs(a - b) % 360f
        return if (diff > 180f) 360f - diff else diff
    }
}
