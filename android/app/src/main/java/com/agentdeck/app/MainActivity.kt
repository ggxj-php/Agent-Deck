package com.agentdeck.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.Modifier
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.agentdeck.app.ui.ChatScreen
import com.agentdeck.app.ui.ChatViewModel
import com.agentdeck.app.ui.LoginScreen
import com.agentdeck.app.ui.SplashScreen
import com.agentdeck.app.ui.theme.DeckTheme

/**
 * 唯一的 Activity：整页都是 Compose（含正文的 markdown 渲染），没有 WebView。
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val viewModel: ChatViewModel = viewModel(factory = ChatViewModel.Factory)
            val state by viewModel.state.collectAsStateWithLifecycle()

            // 从后台回前台先探活：连接早断了的话，发消息才发现会很莫名其妙
            val lifecycleOwner = LocalLifecycleOwner.current
            DisposableEffect(lifecycleOwner) {
                val observer = LifecycleEventObserver { _, event ->
                    if (event == Lifecycle.Event.ON_RESUME) viewModel.onAppResume()
                }
                lifecycleOwner.lifecycle.addObserver(observer)
                onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
            }

            DeckTheme(accentHex = state.persona.theme) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    when {
                        state.booting -> SplashScreen()
                        state.account == null -> LoginScreen(
                            state = state,
                            onLogin = viewModel::login,
                            onRegister = viewModel::register,
                            onSwitchMode = viewModel::switchAuthMode,
                            onRefreshCaptcha = viewModel::refreshCaptcha,
                            onSetServer = viewModel::setServerUrl,
                        )
                        else -> ChatScreen(state, viewModel)
                    }
                }
            }
        }
    }
}
