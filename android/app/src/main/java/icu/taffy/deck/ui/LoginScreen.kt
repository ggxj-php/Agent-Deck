package icu.taffy.deck.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import icu.taffy.deck.BuildConfig
import icu.taffy.deck.ui.theme.DeckPalette
import icu.taffy.deck.ui.theme.LocalPalette

/**
 * 登录页：比网页端简化（没有注册/邀请码入口），但配色跟着身份卡走——
 * 背景渐变、按钮、输入框焦点色都用同一套 palette。
 */
@Composable
fun LoginScreen(
    state: ChatUiState,
    onLogin: (String, String) -> Unit,
) {
    val palette = LocalPalette.current
    var name by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    val loading = state.loggingIn
    val canSubmit = !loading && name.isNotBlank() && password.isNotEmpty()

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Brush.linearGradient(listOf(palette.pinkPale, palette.pinkMist)))
            .statusBarsPadding(),
        contentAlignment = Alignment.Center,
    ) {
        Surface(
            color = Color.White,
            shape = RoundedCornerShape(20.dp),
            shadowElevation = 18.dp,
            modifier = Modifier
                .padding(horizontal = 24.dp)
                .widthIn(max = 380.dp),
        ) {
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                modifier = Modifier.padding(horizontal = 24.dp, vertical = 28.dp),
            ) {
                AsyncImage(
                    model = state.persona.avatarUrl(BuildConfig.BASE_URL),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .size(76.dp)
                        .shadow(8.dp, CircleShape)
                        .clip(CircleShape),
                )
                Spacer(Modifier.height(12.dp))
                Text(
                    text = state.persona.name.ifBlank { "Agent Deck" },
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                    color = palette.ink,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    text = "先登录才能跟${state.persona.name.ifBlank { "助手" }}聊天",
                    fontSize = 12.5.sp,
                    color = palette.inkSoft,
                    textAlign = TextAlign.Center,
                )

                Spacer(Modifier.height(20.dp))
                AuthField(
                    value = name,
                    onValueChange = { name = it.take(32) },
                    label = "用户名",
                    enabled = !loading,
                    palette = palette,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                )
                Spacer(Modifier.height(10.dp))
                AuthField(
                    value = password,
                    onValueChange = { password = it.take(128) },
                    label = "密码",
                    enabled = !loading,
                    palette = palette,
                    isPassword = true,
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Password,
                        imeAction = ImeAction.Done,
                    ),
                    keyboardActions = KeyboardActions(
                        onDone = { if (canSubmit) onLogin(name, password) },
                    ),
                )

                if (state.loginError != null) {
                    Spacer(Modifier.height(10.dp))
                    Text(
                        text = state.loginError,
                        color = androidx.compose.material3.MaterialTheme.colorScheme.error,
                        fontSize = 12.5.sp,
                        textAlign = TextAlign.Center,
                    )
                }

                Spacer(Modifier.height(18.dp))
                Surface(
                    onClick = { if (canSubmit) onLogin(name, password) },
                    enabled = canSubmit,
                    shape = RoundedCornerShape(12.dp),
                    color = Color.Transparent,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(46.dp)
                        .shadow(if (canSubmit) 6.dp else 0.dp, RoundedCornerShape(12.dp))
                        .background(
                            if (canSubmit) {
                                Brush.linearGradient(listOf(palette.accent, palette.pinkDeep))
                            } else {
                                Brush.linearGradient(listOf(palette.line, palette.line))
                            },
                            RoundedCornerShape(12.dp),
                        ),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        if (loading) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                strokeWidth = 2.dp,
                                color = Color.White,
                            )
                        } else {
                            Text(
                                text = "登录",
                                fontSize = 15.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = if (canSubmit) Color.White else palette.inkSoft,
                            )
                        }
                    }
                }

                Spacer(Modifier.height(14.dp))
                Text(
                    text = "邀请制 · 账号由管理员发放",
                    fontSize = 10.5.sp,
                    color = palette.inkSoft.copy(alpha = 0.8f),
                    textAlign = TextAlign.Center,
                )
            }
        }
    }
}

@Composable
private fun AuthField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    enabled: Boolean,
    palette: DeckPalette,
    isPassword: Boolean = false,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
    keyboardActions: KeyboardActions = KeyboardActions.Default,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label, fontSize = 13.sp) },
        singleLine = true,
        enabled = enabled,
        visualTransformation = if (isPassword) PasswordVisualTransformation()
        else androidx.compose.ui.text.input.VisualTransformation.None,
        keyboardOptions = keyboardOptions,
        keyboardActions = keyboardActions,
        shape = RoundedCornerShape(12.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = palette.accent,
            unfocusedBorderColor = palette.line,
            focusedLabelColor = palette.accent,
            unfocusedLabelColor = palette.inkSoft,
            cursorColor = palette.accent,
            focusedContainerColor = Color.White,
            unfocusedContainerColor = Color.White,
        ),
        modifier = Modifier.fillMaxWidth(),
    )
}

/** 首屏探活时的占位。 */
@Composable
fun SplashScreen() {
    val palette = LocalPalette.current
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Brush.linearGradient(listOf(palette.pinkPale, palette.pinkMist))),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            CircularProgressIndicator(strokeWidth = 3.dp, color = palette.accent)
            Spacer(Modifier.height(14.dp))
            Text("连服务器…", fontSize = 13.sp, color = palette.inkSoft)
        }
    }
}
