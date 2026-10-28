package icu.taffy.deck.data

import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import org.json.JSONArray
import org.json.JSONObject

/**
 * 把登录 cookie 存在加密的 SecureStore 里，进程重启后还能免登录。
 *
 * 只存这一份（cookie 本身是 HttpOnly 的随机 token，不是密码），并且只发给它自己的域。
 */
class PersistentCookieJar(private val store: SecureStore) : CookieJar {

    private val bucket = LinkedHashMap<String, Cookie>()

    init {
        load()
    }

    @Synchronized
    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        var changed = false
        for (cookie in cookies) {
            bucket[keyFor(cookie)] = cookie
            changed = true
        }
        if (changed) persist()
    }

    @Synchronized
    override fun loadForRequest(url: HttpUrl): List<Cookie> {
        val now = System.currentTimeMillis()
        var dirty = false
        val usable = ArrayList<Cookie>()
        val iterator = bucket.entries.iterator()
        while (iterator.hasNext()) {
            val entry = iterator.next()
            val cookie = entry.value
            if (cookie.expiresAt <= now) {          // 过期的顺手清掉
                iterator.remove()
                dirty = true
                continue
            }
            if (cookie.matches(url)) usable.add(cookie)
        }
        if (dirty) persist()
        return usable
    }

    @Synchronized
    fun clear() {
        bucket.clear()
        store.remove(KEY)
    }

    /** 给 WebView 的 CookieManager 同步用：`name=value; name2=value2`。 */
    @Synchronized
    fun cookieHeader(url: HttpUrl): String? {
        val now = System.currentTimeMillis()
        val parts = bucket.values
            .filter { it.expiresAt > now && it.matches(url) }
            .map { "${it.name}=${it.value}" }
        return if (parts.isEmpty()) null else parts.joinToString("; ")
    }

    private fun keyFor(cookie: Cookie) = "${cookie.domain}|${cookie.path}|${cookie.name}"

    private fun persist() {
        val array = JSONArray()
        for (cookie in bucket.values) {
            array.put(
                JSONObject()
                    .put("domain", cookie.domain)
                    .put("path", cookie.path)
                    .put("name", cookie.name)
                    .put("value", cookie.value)
                    .put("expires", cookie.expiresAt)
                    .put("secure", cookie.secure)
                    .put("httpOnly", cookie.httpOnly)
            )
        }
        store.putString(KEY, array.toString())
    }

    private fun load() {
        val raw = store.getString(KEY) ?: return
        val array = runCatching { JSONArray(raw) }.getOrNull() ?: return
        for (index in 0 until array.length()) {
            val item = array.optJSONObject(index) ?: continue
            val cookie = runCatching {
                Cookie.Builder()
                    .domain(item.getString("domain"))
                    .path(item.getString("path"))
                    .name(item.getString("name"))
                    .value(item.getString("value"))
                    .expiresAt(item.getLong("expires"))
                    .apply {
                        if (item.optBoolean("secure")) secure()
                        if (item.optBoolean("httpOnly")) httpOnly()
                    }
                    .build()
            }.getOrNull() ?: continue
            bucket[keyFor(cookie)] = cookie
        }
    }

    private companion object {
        const val KEY = "cookie_jar"
    }
}
