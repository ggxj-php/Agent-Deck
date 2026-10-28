package icu.taffy.deck

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import icu.taffy.deck.ui.ChatScreen
import icu.taffy.deck.ui.ChatViewModel
import icu.taffy.deck.ui.LoginScreen
import icu.taffy.deck.ui.SplashScreen
import icu.taffy.deck.ui.theme.DeckTheme

/**
 * 唯一的 Activity：Compose 负责壳、列表、输入框和工具卡片；单条消息的正文交给 WebView。
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val viewModel: ChatViewModel = viewModel(factory = ChatViewModel.Factory)
            val state by viewModel.state.collectAsStateWithLifecycle()

            DeckTheme(accentHex = state.persona.theme) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    when {
                        state.booting -> SplashScreen()
                        state.account == null -> LoginScreen(state, viewModel::login)
                        else -> ChatScreen(state, viewModel)
                    }
                }
            }
        }
    }
}
