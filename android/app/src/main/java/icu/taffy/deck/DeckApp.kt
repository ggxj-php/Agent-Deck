package icu.taffy.deck

import android.app.Application
import icu.taffy.deck.data.DeckApi

/**
 * 进程级单例：OkHttp 连接池、cookie 仓库都只该有一份。
 *
 * 这里不放任何密钥——服务地址来自 BuildConfig，账号密码只在登录那一刻出现，用完即弃。
 */
class DeckApp : Application() {

    val api: DeckApi by lazy { DeckApi(this) }

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    companion object {
        lateinit var instance: DeckApp
            private set
    }
}
