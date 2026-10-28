package com.agentdeck.app.data

import android.content.Context
import com.agentdeck.app.BuildConfig

/**
 * 后端地址：默认取构建时注入的 `deck.baseUrl`（local.properties，不进仓库），
 * 也允许用户在登录页临时改成自己的服务器。
 *
 * 只接受 https，避免明文流量。
 */
object ServerStore {

    private const val PREFS = "deck_server"
    private const val KEY = "base_url"

    fun current(context: Context): String {
        val custom = prefs(context).getString(KEY, null)?.trim()?.trimEnd('/')
        return if (!custom.isNullOrEmpty() && custom.startsWith("https://")) custom else BuildConfig.BASE_URL
    }

    fun isOverridden(context: Context): Boolean =
        prefs(context).getString(KEY, null)?.startsWith("https://") == true

    fun override(context: Context, url: String?) {
        val normalized = url?.trim()?.trimEnd('/').orEmpty()
        if (normalized.isEmpty()) {
            prefs(context).edit().remove(KEY).apply()
        } else if (normalized.startsWith("https://")) {
            prefs(context).edit().putString(KEY, normalized).apply()
        }
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
