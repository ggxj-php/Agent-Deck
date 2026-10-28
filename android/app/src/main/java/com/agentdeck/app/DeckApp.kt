package com.agentdeck.app

import android.app.Application
import coil.Coil
import coil.ImageLoader
import coil.decode.SvgDecoder
import com.agentdeck.app.data.DeckApi

/**
 * 进程级单例：OkHttp 连接池、cookie 仓库、图片加载器都只该有一份。
 *
 * Coil 复用带 cookie 的 OkHttpClient，这样 /api/uploads 的图片和 /api/artifact 的
 * 产物图（需要登录）都能直接显示；同时注册 SVG 解码器，产物图不用开 WebView。
 */
class DeckApp : Application() {

    val api: DeckApi by lazy { DeckApi(this) }

    override fun onCreate() {
        super.onCreate()
        instance = this
        val loader = ImageLoader.Builder(this)
            .okHttpClient { api.httpClient }
            .components { add(SvgDecoder.Factory()) }
            .build()
        Coil.setImageLoader(loader)
    }

    companion object {
        lateinit var instance: DeckApp
            private set
    }
}
