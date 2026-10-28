# WebView 里暴露给 JS 的高度回调，混淆后必须保留名字
-keepclassmembers class icu.taffy.deck.ui.components.MarkdownBridge {
    @android.webkit.JavascriptInterface <methods>;
}
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}

# OkHttp / Okio 的平台探测
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
