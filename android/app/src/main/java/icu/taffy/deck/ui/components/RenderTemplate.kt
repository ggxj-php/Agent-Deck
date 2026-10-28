package icu.taffy.deck.ui.components

import android.content.Context
import java.util.concurrent.atomic.AtomicReference

/**
 * 把 assets 里的渲染模板拼成一个完整 HTML（脚本内联，页面不依赖网络加载 JS）。
 *
 * 只拼一次然后缓存：每条消息的 WebView 都复用它，省得反复读盘、反复解析 170KB 的库。
 */
object RenderTemplate {

    private val cache = AtomicReference<String?>(null)

    fun html(context: Context): String {
        cache.get()?.let { return it }
        synchronized(this) {
            cache.get()?.let { return it }
            val shell = read(context, "render/shell.html")
            val built = shell
                .replace("/*__HIGHLIGHT_CSS__*/", read(context, "render/highlight.css"))
                .replace("/*__MARKED_JS__*/", inline(context, "render/marked.min.js"))
                .replace("/*__PURIFY_JS__*/", inline(context, "render/purify.min.js"))
                .replace("/*__HIGHLIGHT_JS__*/", inline(context, "render/highlight.min.js"))
                .replace("/*__RENDERER_JS__*/", inline(context, "render/renderer.js"))
            cache.set(built)
            return built
        }
    }

    private fun read(context: Context, path: String): String =
        context.assets.open(path).use { it.readBytes().toString(Charsets.UTF_8) }

    /** 内联脚本里的 `</script>` 必须转义，否则会把外层脚本标签提前截断。 */
    private fun inline(context: Context, path: String): String =
        read(context, path).replace("</script", "<\\/script")
}
