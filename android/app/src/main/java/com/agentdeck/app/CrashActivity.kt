package com.agentdeck.app

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.agentdeck.app.data.CrashReporter
import kotlinx.coroutines.launch
import androidx.compose.runtime.rememberCoroutineScope

/**
 * 崩溃提示页：App 出错时不是直接消失，而是停在这里，让用户看到原因、
 * 复制日志、重新打开，同时后台把日志上报到服务器。
 */
class CrashActivity : ComponentActivity() {

    companion object {
        const val EXTRA_MESSAGE = "crash_message"
        const val EXTRA_LOG = "crash_log"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val message = intent.getStringExtra(EXTRA_MESSAGE) ?: "未知错误"
        val log = intent.getStringExtra(EXTRA_LOG) ?: message
        setContent {
            CrashScreen(
                message = message,
                log = log,
                onCopy = { copy(log) },
                onRestart = {
                    val intent = Intent(this, MainActivity::class.java).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                    }
                    startActivity(intent)
                    finish()
                },
            )
        }
    }

    private fun copy(text: String) {
        val manager = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        manager.setPrimaryClip(ClipData.newPlainText("崩溃日志", text))
    }
}

@Composable
private fun CrashScreen(
    message: String,
    log: String,
    onCopy: () -> Unit,
    onRestart: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val context = androidx.compose.ui.platform.LocalContext.current
    var uploadState by remember { mutableStateOf("日志已保存在本地") }

    LaunchedEffect(Unit) {
        // 远端上报默认是关的（服务端还没有 /api/app-crash 这个接口），
        // 关掉时别骗用户说「上报失败」，直接告诉他日志在本地、可以复制。
        if (!CrashReporter.remoteEnabled()) {
            uploadState = "日志已保存在本地，可以点下面「复制日志」发给开发者"
            return@LaunchedEffect
        }
        val ok = CrashReporter.upload(context, log)
        uploadState = if (ok) "日志已上报到服务器" else "上报失败，日志已保存在本地"
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFFFFF6F7))
            .statusBarsPadding()
            .padding(horizontal = 18.dp, vertical = 14.dp),
    ) {
        Text("😵 应用崩溃了", fontSize = 22.sp, color = Color(0xFFB3261E))
        Spacer(Modifier.height(6.dp))
        Text("已经帮你把现场记下来了", fontSize = 13.sp, color = Color(0xFF7A5C5E))
        Spacer(Modifier.height(14.dp))

        Surface(
            color = Color.White,
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(modifier = Modifier.padding(12.dp)) {
                Text("原因", fontSize = 12.sp, color = Color(0xFF9A8A8B))
                Spacer(Modifier.height(4.dp))
                Text(message, fontSize = 13.sp, color = Color(0xFF3D2C2E))
                Spacer(Modifier.height(10.dp))
                Text(uploadState, fontSize = 11.5.sp, color = Color(0xFF7A5C5E))
            }
        }

        Spacer(Modifier.height(12.dp))
        Text("崩溃日志", fontSize = 12.sp, color = Color(0xFF9A8A8B))
        Spacer(Modifier.height(6.dp))
        Surface(
            color = Color(0xFF1F2028),
            shape = RoundedCornerShape(10.dp),
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            SelectionContainer {
                Text(
                    text = log,
                    color = Color(0xFFE6E6EE),
                    fontFamily = FontFamily.Monospace,
                    fontSize = 10.5.sp,
                    lineHeight = 15.sp,
                    modifier = Modifier
                        .fillMaxSize()
                        .verticalScroll(rememberScrollState())
                        .padding(10.dp),
                )
            }
        }

        Spacer(Modifier.height(12.dp))
        Row(
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth(),
        ) {
            TextButton(onClick = onCopy) {
                Text("复制日志", color = Color(0xFF7A5C5E), fontSize = 13.sp)
            }
            Spacer(Modifier.weight(1f))
            Button(
                onClick = onRestart,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFB3261E)),
                shape = RoundedCornerShape(10.dp),
            ) {
                Text("重新打开", fontSize = 13.sp)
            }
        }
    }
}
