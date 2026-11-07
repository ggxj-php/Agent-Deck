package com.agentdeck.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.agentdeck.app.DeckApp
import com.agentdeck.app.MainActivity
import com.agentdeck.app.R
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * 回复期间的前台服务。
 *
 * 模型是在服务器上跑的，但**这条 SSE 连接**握在 App 手里：进程一被系统回收、
 * 或者 CPU 睡下去，流就断了。以前切到别的应用待久一点回来就是半截回复，
 * 现在一开跑就挂个前台服务 + 部分唤醒锁，进程和连接都能撑住。
 *
 * 服务在跑的时候通知栏会常驻一条「正在回复」，上面的「停止」会真的叫停
 * 服务器上那一轮（`POST /api/chat/stop`），不是只掐本地连接。
 */
class ChatService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var ticker: Handler? = null
    private var lastText = ""

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP_TURN) {
            // 通知栏的「停止」：先叫服务器别跑了，再掐掉本地这条流
            val session = intent.getStringExtra(EXTRA_SESSION).orEmpty()
            if (session.isNotEmpty()) {
                val api = DeckApp.instance.api
                kotlinx.coroutines.CoroutineScope(Dispatchers.IO).launch {
                    runCatching { api.requestStop(session) }
                }
            }
            // 标记一下「是用户自己停的」，否则 ViewModel 会把这条断流画成红色报错
            ChatForeground.userStopped = true
            DeckApp.instance.api.cancelChat()
            ChatForeground.reset()
            stopSelfSafely()
            return START_NOT_STICKY
        }

        val text = intent?.getStringExtra(EXTRA_TEXT)?.takeIf { it.isNotBlank() } ?: "正在回复…"
        ChatForeground.text = text
        lastText = text
        startForegroundCompat(buildRunning(text))
        acquireLocks()
        startTicker()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        ticker?.removeCallbacksAndMessages(null)
        ticker = null
        runCatching { wakeLock?.takeIf { it.isHeld }?.release() }
        wakeLock = null
        runCatching { wifiLock?.takeIf { it.isHeld }?.release() }
        wifiLock = null
        super.onDestroy()
    }

    /** 通知里的进度：每 1.2 秒看一眼状态文案，变了才刷，别把通知栏刷出残影。 */
    private fun startTicker() {
        if (ticker != null) return
        val handler = Handler(Looper.getMainLooper())
        ticker = handler
        val task = object : Runnable {
            override fun run() {
                val current = ChatForeground.text
                if (current.isNotBlank() && current != lastText) {
                    lastText = current
                    runCatching {
                        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                        nm.notify(NOTIFICATION_ID, buildRunning(current))
                    }
                }
                handler.postDelayed(this, 1200)
            }
        }
        handler.postDelayed(task, 1200)
    }

    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun stopSelfSafely() {
        runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
        stopSelf()
    }

    private fun acquireLocks() {
        if (wakeLock?.isHeld != true) {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = runCatching {
                pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "agentdeck:reply").apply {
                    setReferenceCounted(false)
                    acquire()
                }
            }.getOrNull()
        }
        if (wifiLock?.isHeld != true) {
            val wm = getSystemService(Context.WIFI_SERVICE) as WifiManager
            wifiLock = runCatching {
                wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "agentdeck:reply").apply {
                    setReferenceCounted(false)
                    acquire()
                }
            }.getOrNull()
        }
    }

    private fun buildRunning(text: String): Notification =
        NotificationCompat.Builder(this, CHANNEL_RUNNING)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("正在回复")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setSilent(true)
            .setContentIntent(openAppIntent(this))
            .addAction(0, "停止", stopIntent(this))
            .build()

    companion object {
        const val ACTION_START = "com.agentdeck.app.chat.START"
        const val ACTION_STOP_TURN = "com.agentdeck.app.chat.STOP_TURN"
        const val EXTRA_SESSION = "session_id"
        const val EXTRA_TEXT = "text"

        private const val NOTIFICATION_ID = 4101
        const val CHANNEL_RUNNING = "reply_running"
        const val CHANNEL_DONE = "reply_done"

        /** 两个频道一起建：跑的时候用低优先级的常驻条，跑完用会响的那条。 */
        fun ensureChannel(context: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CHANNEL_RUNNING) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(CHANNEL_RUNNING, "回复进行中", NotificationManager.IMPORTANCE_LOW).apply {
                        description = "模型还在写的时候常驻，方便随时看进度或叫停"
                        setShowBadge(false)
                    },
                )
            }
            if (nm.getNotificationChannel(CHANNEL_DONE) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(CHANNEL_DONE, "回复完成", NotificationManager.IMPORTANCE_DEFAULT).apply {
                        description = "在别的应用里时，回复写完提醒一声"
                    },
                )
            }
        }

        fun openAppIntent(context: Context): PendingIntent {
            val intent = Intent(context, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            return PendingIntent.getActivity(
                context, 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        }

        private fun stopIntent(context: Context): PendingIntent {
            val intent = Intent(context, ChatService::class.java)
                .setAction(ACTION_STOP_TURN)
                .putExtra(EXTRA_SESSION, ChatForeground.session)
            return PendingIntent.getService(
                context, 1, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        }
    }
}

/**
 * 前台服务的开关入口 + 一条给通知用的状态槽。
 *
 * 状态就几行字，走进程内变量最省事：ViewModel 往里写，服务那边轮询着刷通知。
 */
object ChatForeground {

    /** 通知上要显示的进度文案，ViewModel 更新。 */
    @Volatile
    var text: String = ""

    @Volatile
    var session: String = ""

    /** App 现在是不是在前台：在后台跑完才值得弹「回复完成了」。 */
    @Volatile
    var inForeground: Boolean = false

    /** 通知栏点了「停止」。ViewModel 靠它区分「用户叫停」和「网络断了」。 */
    @Volatile
    var userStopped: Boolean = false

    private var running = false

    fun start(context: Context, sessionId: String) {
        ChatService.ensureChannel(context)
        session = sessionId
        text = "正在回复…"
        running = true
        // 后台被系统拦下来最多是个没通知，别让它把发送流程炸了
        runCatching {
            ContextCompat.startForegroundService(
                context,
                Intent(context, ChatService::class.java)
                    .setAction(ChatService.ACTION_START)
                    .putExtra(ChatService.EXTRA_SESSION, sessionId)
                    .putExtra(ChatService.EXTRA_TEXT, text),
            )
        }
    }

    /** 更新进度文案（内容尾巴、正在跑的工具名）。服务每秒来取。 */
    fun update(value: String) {
        if (value.isNotBlank()) text = value
    }

    fun stop(context: Context) {
        running = false
        runCatching { context.stopService(Intent(context, ChatService::class.java)) }
    }

    fun reset() {
        running = false
        session = ""
        text = ""
    }

    /**
     * 一轮跑完。人在别的应用里就补一条「回复完成了」，点开直接回聊天页；
     * 人本来就在看就别再弹了，通知栏刷屏很烦。
     */
    fun finished(context: Context, summary: String) {
        val wasRunning = running
        stop(context)
        if (!wasRunning || inForeground) return
        val body = summary.trim().replace(Regex("\\s+"), " ").take(120)
        if (body.isEmpty()) return
        runCatching {
            ChatService.ensureChannel(context)
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.notify(
                DONE_ID,
                NotificationCompat.Builder(context, ChatService.CHANNEL_DONE)
                    .setSmallIcon(R.drawable.ic_notify)
                    .setContentTitle("回复完成了")
                    .setContentText(body)
                    .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                    .setAutoCancel(true)
                    .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                    .setContentIntent(ChatService.openAppIntent(context))
                    .build(),
            )
        }
    }

    private const val DONE_ID = 4102
}
