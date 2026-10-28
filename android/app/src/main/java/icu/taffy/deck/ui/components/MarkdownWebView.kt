package icu.taffy.deck.ui.components

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.util.Base64
import android.view.View
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.delay
import java.io.ByteArrayInputStream

/**
 * 高度回报桥。整个 App 只暴露这一个方法给网页，参数是一个整数高度。
 * 页面内容是我们自己的模板 + 经过 DOMPurify 消毒的正文，不含脚本。
 */
class MarkdownBridge {
    @Volatile
    var heightCallback: ((Int) -> Unit)? = null

    @JavascriptInterface
    fun onHeight(px: Int) {
        heightCallback?.invoke(px)
    }
}

/**
 * 单条消息的正文渲染：WebView 里跑 marked + DOMPurify + highlight，和网页版同一套逻辑，
 * 高度由页面自己量好回报给 Compose。
 *
 * 安全边界：
 * - 只允许 https、且只允许本站域名；其它请求一律 403（防止正文里的图片/脚本把请求带到外域）；
 * - 所有链接一律交给系统浏览器打开，WebView 自身永不导航；
 * - 禁文件访问、禁 content://、禁 DOM storage、禁多窗口、禁混合内容。
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun MarkdownWebView(
    markdown: String,
    baseUrl: String,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val html = remember(baseUrl) { RenderTemplate.html(context) }
    val baseHost = remember(baseUrl) { runCatching { Uri.parse(baseUrl).host }.getOrNull().orEmpty() }

    var heightDp by remember { mutableIntStateOf(0) }
    var pageReady by remember { mutableStateOf(false) }
    var webView by remember { mutableStateOf<WebView?>(null) }

    val bridge = remember {
        MarkdownBridge().also { candidate ->
            candidate.heightCallback = { px ->
                val target = if (px > 0) px else 1
                webView?.post { heightDp = target }
            }
        }
    }

    AndroidView(
        modifier = modifier.height(if (heightDp > 0) heightDp.dp else 1.dp),
        factory = { ctx ->
            WebView(ctx).apply {
                layoutParams = ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                )
                setBackgroundColor(Color.TRANSPARENT)
                isVerticalScrollBarEnabled = false
                isHorizontalScrollBarEnabled = false
                overScrollMode = View.OVER_SCROLL_NEVER
                settings.apply {
                    javaScriptEnabled = true
                    domStorageEnabled = false
                    databaseEnabled = false
                    allowFileAccess = false
                    allowContentAccess = false
                    javaScriptCanOpenWindowsAutomatically = false
                    setSupportMultipleWindows(false)
                    setSupportZoom(false)
                    builtInZoomControls = false
                    displayZoomControls = false
                    loadsImagesAutomatically = true
                    blockNetworkImage = false
                    mediaPlaybackRequiresUserGesture = true
                    mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                    cacheMode = WebSettings.LOAD_DEFAULT
                    textZoom = 100
                    userAgentString = "$userAgentString AgentDeck/1.0"
                }
                isFocusable = false
                isFocusableInTouchMode = false

                addJavascriptInterface(bridge, "DeckBridge")

                webViewClient = object : WebViewClient() {
                    override fun onPageFinished(view: WebView, url: String) {
                        pageReady = true
                    }

                    // 任何点击都不在 WebView 内导航：一律交给系统浏览器
                    override fun shouldOverrideUrlLoading(
                        view: WebView,
                        request: WebResourceRequest,
                    ): Boolean {
                        openExternally(view.context, request.url)
                        return true
                    }

                    // 只放行本站 https 和 data:，其余全部掐断
                    override fun shouldInterceptRequest(
                        view: WebView,
                        request: WebResourceRequest,
                    ): WebResourceResponse? {
                        val uri = request.url
                        return when {
                            uri.scheme == "data" || uri.scheme == "about" -> null
                            uri.scheme != "https" -> blocked()
                            !uri.host.orEmpty().equals(baseHost, ignoreCase = true) -> blocked()
                            else -> null
                        }
                    }
                }
                webChromeClient = WebChromeClient()
                loadDataWithBaseURL(baseUrl, html, "text/html", "utf-8", null)
            }
        },
        update = { view -> webView = view },
    )

    // 流式输出时每次增量都重渲太浪费：等 90ms，把这段时间的增量合并成一次
    LaunchedEffect(markdown, pageReady, webView) {
        val view = webView ?: return@LaunchedEffect
        if (!pageReady) return@LaunchedEffect
        delay(90)
        val encoded = Base64.encodeToString(markdown.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
        view.evaluateJavascript("window.deckUpdate && window.deckUpdate('$encoded');", null)
    }
}

private fun blocked(): WebResourceResponse =
    WebResourceResponse(
        "text/plain",
        "utf-8",
        403,
        "Blocked",
        emptyMap(),
        ByteArrayInputStream(ByteArray(0)),
    )

private fun openExternally(context: Context, uri: Uri) {
    val scheme = uri.scheme?.lowercase()
    if (scheme != "http" && scheme != "https") return   // 其它 scheme（intent:// 之类）一律不碰
    val intent = Intent(Intent.ACTION_VIEW, uri).apply {
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
    runCatching { context.startActivity(intent) }
}
