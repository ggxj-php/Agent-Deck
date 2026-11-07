package com.agentdeck.app.ui

import android.annotation.SuppressLint
import android.graphics.Color as AndroidColor
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.agentdeck.app.DeckApp
import com.agentdeck.app.ui.theme.DeckPalette

/**
 * 「接管浏览器」：把服务器上的浏览器画面（noVNC）投到 App 里，手动点验证码 / 按钮。
 *
 * 网页端是侧边栏新开一个 `/takeover` 页；App 里没有浏览器标签，就用一个 WebView 打开同一个地址。
 * 关键点：
 * - 先 `syncWebViewCookie()`：`/takeover` 和 `/api/takeover/ws` 都认登录 cookie，
 *   不塞进去会 303 跳到登录页；
 * - WebView 的 WebSocket 走同一个 CookieManager，所以 noVNC 那条 wss 也能连上；
 * - 退出页面时 `destroy()`，不然 websockify 那条隧道会一直挂着。
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun TakeoverScreen(palette: DeckPalette, onBack: () -> Unit) {
    val api = DeckApp.instance.api
    val url = remember { api.baseUrl.trimEnd('/') + "/takeover" }

    var loading by remember { mutableStateOf(true) }
    var failed by remember { mutableStateOf<String?>(null) }
    // 用数组当 holder：factory 是在组合过程中跑的，往 MutableState 里写会触发「组合期改状态」告警
    val webHolder = remember { arrayOfNulls<WebView>(1) }

    // noVNC 的画面是一块块刷出来的：页面 load 完时画布还是空的，这时撤掉遮罩就是一片黑，
    // 用户会以为坏了。所以轮询 iframe 里的 canvas，真出图了再撤遮罩；桌面本身就是全黑
    // （服务器上没开窗口）时到点也得撤，不然遮罩一直盖着。
    val handler = remember { android.os.Handler(android.os.Looper.getMainLooper()) }
    val probeTask = remember { arrayOfNulls<Runnable>(1) }

    fun stopProbe() {
        probeTask[0]?.let { handler.removeCallbacks(it) }
        probeTask[0] = null
    }

    fun startProbe(web: WebView) {
        stopProbe()
        val deadline = System.currentTimeMillis() + 12_000
        val task = object : Runnable {
            override fun run() {
                if (webHolder[0] !== web) return
                web.evaluateJavascript(PROBE_JS) { value ->
                    val painted = value?.contains("true") == true
                    if (painted || System.currentTimeMillis() > deadline) {
                        loading = false
                    } else {
                        handler.postDelayed(this, 500)
                    }
                }
            }
        }
        probeTask[0] = task
        handler.postDelayed(task, 400)
    }

    androidx.activity.compose.BackHandler { onBack() }

    // 兜底：万一 onPageFinished 因为某个子资源一直挂不到（页面里有 iframe），
    // 遮罩也不能永远盖着，20 秒后强制撤掉，让用户自己看页面上的状态。
    LaunchedEffect(Unit) {
        kotlinx.coroutines.delay(20_000)
        stopProbe()
        loading = false
    }

    // 必须在 WebView 建出来之前塞 cookie：晚一步第一次加载就已经被跳到登录页了
    remember(Unit) { api.syncWebViewCookie() }

    DisposableEffect(Unit) {
        onDispose {
            stopProbe()
            webHolder[0]?.apply {
                stopLoading()
                loadUrl("about:blank")
                destroy()
            }
            webHolder[0] = null
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF111111))
            .statusBarsPadding()
            .navigationBarsPadding(),
    ) {
        // 顶栏：退出 / 标题 / 刷新。深色是为了和 noVNC 的黑底连成一片
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier
                .fillMaxWidth()
                .background(Color(0xFF1B1B1F))
                .padding(horizontal = 4.dp, vertical = 4.dp),
        ) {
            IconButton(onClick = onBack) {
                Icon(
                    Icons.AutoMirrored.Rounded.ArrowBack,
                    contentDescription = "返回",
                    tint = Color(0xFFDDDDDD),
                )
            }
            Column(modifier = Modifier.weight(1f)) {
                Text("接管浏览器", fontSize = 16.sp, color = Color(0xFFEDEDED))
                Text(
                    text = "服务器上的浏览器画面，可以手动点验证码",
                    fontSize = 10.5.sp,
                    color = Color(0xFF9A9AA6),
                )
            }
            IconButton(onClick = { webHolder[0]?.reload() }) {
                Icon(
                    Icons.Rounded.Refresh,
                    contentDescription = "重新加载画面",
                    tint = Color(0xFF9A9AA6),
                )
            }
        }

        Box(modifier = Modifier.weight(1f)) {
            AndroidView(
                modifier = Modifier.fillMaxSize(),
                factory = { context ->
                    WebView(context).apply {
                        setBackgroundColor(AndroidColor.BLACK)
                        settings.apply {
                            javaScriptEnabled = true          // noVNC 全靠 JS
                            domStorageEnabled = true
                            mediaPlaybackRequiresUserGesture = false
                            useWideViewPort = true
                            loadWithOverviewMode = false     // 页面自己按 1280×800 缩放
                            setSupportZoom(true)             // 手机上好放大看
                            builtInZoomControls = true
                            displayZoomControls = false
                            cacheMode = WebSettings.LOAD_DEFAULT
                        }
                        webViewClient = object : WebViewClient() {
                            override fun onPageStarted(
                                view: WebView?,
                                url: String?,
                                favicon: android.graphics.Bitmap?,
                            ) {
                                stopProbe()
                                loading = true
                                failed = null
                            }

                            override fun onPageFinished(view: WebView?, url: String?) {
                                // 注意：这里不能直接撤遮罩——壳子出来了，画面还没出来
                                if (view != null) startProbe(view) else loading = false
                            }

                            override fun onReceivedError(
                                view: WebView?,
                                request: WebResourceRequest?,
                                error: WebResourceError?,
                            ) {
                                // 只有主文档失败才算「打不开」，子资源（字体、图标）失败不管
                                if (request?.isForMainFrame == true) {
                                    stopProbe()
                                    loading = false
                                    failed = error?.description?.toString()
                                        ?.takeIf { it.isNotBlank() } ?: "连不上服务器"
                                }
                            }

                            override fun shouldOverrideUrlLoading(
                                view: WebView?,
                                request: WebResourceRequest?,
                            ): Boolean = false       // 站内跳转都留在 WebView 里
                        }
                        // noVNC 自己在 iframe 里跑，出问题只会黑屏；把它的报错打到 logcat，
                        // 装到手机上排查时 `adb logcat -s TakeoverWV` 就能看见
                        webChromeClient = object : android.webkit.WebChromeClient() {
                            override fun onConsoleMessage(msg: android.webkit.ConsoleMessage?): Boolean {
                                android.util.Log.d(
                                    "TakeoverWV",
                                    "[${msg?.messageLevel()}] ${msg?.message()} " +
                                        "@${msg?.sourceId()}:${msg?.lineNumber()}",
                                )
                                return true
                            }
                        }
                        loadUrl(url)
                        webHolder[0] = this
                    }
                },
            )

            if (loading && failed == null) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color(0xCC000000)),
                    contentAlignment = Alignment.Center,
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        CircularProgressIndicator(color = palette.accent, strokeWidth = 2.dp)
                        Spacer(Modifier.height(10.dp))
                        Text("正在连接服务器上的浏览器…", fontSize = 12.sp, color = Color(0xFFDDDDDD))
                    }
                }
            }

            failed?.let { reason ->
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color(0xFF111111)),
                    contentAlignment = Alignment.Center,
                ) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        modifier = Modifier.padding(28.dp),
                    ) {
                        Icon(
                            Icons.Rounded.ErrorOutline,
                            contentDescription = null,
                            tint = Color(0xFF9A9AA6),
                        )
                        Spacer(Modifier.height(8.dp))
                        Text("打不开浏览器画面", fontSize = 14.sp, color = Color(0xFFEDEDED))
                        Spacer(Modifier.height(6.dp))
                        Text(
                            text = reason,
                            fontSize = 12.sp,
                            lineHeight = 18.sp,
                            color = Color(0xFF9A9AA6),
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(
                            text = "先让助手打开网页，再回来接管；服务器重启后要等浏览器重新起来。",
                            fontSize = 11.sp,
                            lineHeight = 17.sp,
                            color = Color(0xFF77777F),
                        )
                        Spacer(Modifier.height(14.dp))
                        Surface(
                            onClick = {
                                failed = null
                                loading = true
                                webHolder[0]?.reload()
                            },
                            shape = RoundedCornerShape(999.dp),
                            color = Color(0xFF2C2C33),
                        ) {
                            Text(
                                "重试",
                                fontSize = 12.5.sp,
                                color = Color(0xFFEDEDED),
                                modifier = Modifier.padding(horizontal = 18.dp, vertical = 9.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

/**
 * 探测 noVNC 画布是否已经有画面：把 iframe 里的 canvas 缩到 24×16 采一次色，
 * 只要有一个明显不黑的像素就算出图了。取不到（跨域/还没建好）时当「已出图」，
 * 免得遮罩因为拿不到结果一直不撤。
 */
private const val PROBE_JS = """(function(){try{
var f=document.getElementById('v');
var d=f&&f.contentDocument;
var c=d&&d.querySelector('canvas');
if(!c||!c.width)return false;
var t=document.createElement('canvas');
t.width=24;t.height=16;
var x=t.getContext('2d');
x.drawImage(c,0,0,24,16);
var p=x.getImageData(0,0,24,16).data;
for(var i=0;i<p.length;i+=4){if(p[i]+p[i+1]+p[i+2]>24)return true;}
return false;
}catch(e){return true;}})()"""
