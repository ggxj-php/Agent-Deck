package com.agentdeck.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
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
import com.agentdeck.app.BuildConfig
import com.agentdeck.app.ui.theme.DeckPalette
import com.agentdeck.app.ui.theme.LocalPalette

/**
 * 登录 / 邀请码注册。
 *
 * 两个页签，配色全部走身份卡派生出来的 palette：背景渐变、按钮渐变、输入框焦点色、
 * 验证码边框。注册要邀请码 + 图形验证码，和网页端一致。
 */
@Composable
fun LoginScreen(
    state: ChatUiState,
    onLogin: (String, String) -> Unit,
    onRegister: (String, String, String, String) -> Unit,
    onSwitchMode: (AuthMode) -> Unit,
    onRefreshCaptcha: () -> Unit,
) {
    val palette = LocalPalette.current
    var name by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var invite by rememberSaveable { mutableStateOf("") }
    var answer by rememberSaveable { mutableStateOf("") }

    val register = state.authMode == AuthMode.REGISTER
    val loading = state.loggingIn

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
                .padding(horizontal = 20.dp, vertical = 14.dp)
                .widthIn(max = 400.dp),
        ) {
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                modifier = Modifier
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 22.dp, vertical = 24.dp),
            ) {
                AsyncImage(
                    model = state.persona.avatarUrl(state.serverUrl),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .size(72.dp)
                        .shadow(8.dp, CircleShape)
                        .clip(CircleShape),
                )
                Spacer(Modifier.height(10.dp))
                Text(
                    text = state.persona.name.ifBlank { "Agent Deck" },
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                    color = palette.ink,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text = if (register) {
                        "需要一个邀请码才能注册"
                    } else {
                        "先登录才能跟${state.persona.name.ifBlank { "助手" }}聊天"
                    },
                    fontSize = 12.5.sp,
                    color = palette.inkSoft,
                    textAlign = TextAlign.Center,
                )

                Spacer(Modifier.height(18.dp))
                AuthTabs(state.authMode, palette, onSwitchMode)

                Spacer(Modifier.height(16.dp))
                AuthField(
                    value = name,
                    onValueChange = { name = it.take(20) },
                    label = if (register) "用户名（3~20 个字符）" else "用户名",
                    enabled = !loading,
                    palette = palette,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                )
                Spacer(Modifier.height(10.dp))
                AuthField(
                    value = password,
                    onValueChange = { password = it.take(128) },
                    label = if (register) "密码（至少 8 位）" else "密码",
                    enabled = !loading,
                    palette = palette,
                    isPassword = true,
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Password,
                        imeAction = if (register) ImeAction.Next else ImeAction.Done,
                    ),
                    keyboardActions = KeyboardActions(
                        onDone = { if (!register && !loading) onLogin(name, password) },
                    ),
                )

                if (register) {
                    Spacer(Modifier.height(10.dp))
                    AuthField(
                        value = invite,
                        onValueChange = { invite = it.take(16) },
                        label = "邀请码",
                        enabled = !loading,
                        palette = palette,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                    )
                    Spacer(Modifier.height(10.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(
                            value = answer,
                            onValueChange = { answer = it.take(6) },
                            label = { Text("验证码", fontSize = 13.sp) },
                            singleLine = true,
                            enabled = !loading,
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                            keyboardActions = KeyboardActions(
                                onDone = {
                                    if (!loading) {
                                        onRegister(name, password, invite, answer)
                                    }
                                },
                            ),
                            shape = RoundedCornerShape(12.dp),
                            colors = fieldColors(palette),
                            modifier = Modifier.weight(1f),
                        )
                        Spacer(Modifier.width(10.dp))
                        Surface(
                            onClick = { if (!state.captchaLoading) onRefreshCaptcha() },
                            shape = RoundedCornerShape(12.dp),
                            color = palette.softFill,
                            border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
                            modifier = Modifier
                                .width(108.dp)
                                .height(56.dp),
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                if (state.captchaLoading || state.captcha == null) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(18.dp),
                                        strokeWidth = 2.dp,
                                        color = palette.accent,
                                    )
                                } else {
                                    AsyncImage(
                                        model = state.captcha.image,
                                        contentDescription = "图形验证码，点一下换一张",
                                        contentScale = ContentScale.Fit,
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .height(56.dp),
                                    )
                                }
                            }
                        }
                    }
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text = "点验证码图片可以换一张",
                        fontSize = 10.5.sp,
                        color = palette.inkSoft,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }

                if (state.loginError != null) {
                    Spacer(Modifier.height(10.dp))
                    Text(
                        text = state.loginError,
                        color = androidx.compose.material3.MaterialTheme.colorScheme.error,
                        fontSize = 12.5.sp,
                        textAlign = TextAlign.Center,
                    )
                }

                Spacer(Modifier.height(16.dp))
                GradientButton(
                    text = when {
                        loading -> "处理中…"
                        register -> "注册并登录"
                        else -> "登录"
                    },
                    enabled = !loading,
                    palette = palette,
                    loading = loading,
                    onClick = {
                        if (register) onRegister(name, password, invite, answer)
                        else onLogin(name, password)
                    },
                )

                Spacer(Modifier.height(12.dp))
                Text(
                    text = if (register) "邀请码可以找已注册的朋友要" else "邀请制 · 找已经在用的朋友要邀请码",
                    fontSize = 10.5.sp,
                    color = palette.inkSoft.copy(alpha = 0.8f),
                    textAlign = TextAlign.Center,
                )
            }
        }
    }
}

@Composable
private fun AuthTabs(
    mode: AuthMode,
    palette: DeckPalette,
    onSwitch: (AuthMode) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(palette.pinkMist.copy(alpha = 0.7f))
            .padding(3.dp),
    ) {
        TabItem(
            text = "登录",
            selected = mode == AuthMode.LOGIN,
            palette = palette,
            modifier = Modifier.weight(1f),
        ) { onSwitch(AuthMode.LOGIN) }
        TabItem(
            text = "用邀请码注册",
            selected = mode == AuthMode.REGISTER,
            palette = palette,
            modifier = Modifier.weight(1f),
        ) { onSwitch(AuthMode.REGISTER) }
    }
}

@Composable
private fun TabItem(
    text: String,
    selected: Boolean,
    palette: DeckPalette,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    Box(
        contentAlignment = Alignment.Center,
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(if (selected) Color.White else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(vertical = 8.dp),
    ) {
        Text(
            text = text,
            fontSize = 13.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            color = if (selected) palette.accent else palette.inkSoft,
        )
    }
}

@Composable
private fun GradientButton(
    text: String,
    enabled: Boolean,
    loading: Boolean,
    palette: DeckPalette,
    onClick: () -> Unit,
) {
    val shape = RoundedCornerShape(12.dp)
    Surface(
        onClick = { if (enabled) onClick() },
        enabled = enabled,
        shape = shape,
        color = Color.Transparent,
        modifier = Modifier
            .fillMaxWidth()
            .height(46.dp)
            .shadow(if (enabled) 6.dp else 0.dp, shape)
            .background(
                if (enabled) Brush.linearGradient(listOf(palette.accent, palette.pinkDeep))
                else Brush.linearGradient(listOf(palette.line, palette.line)),
                shape,
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
                    text = text,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = if (enabled) Color.White else palette.inkSoft,
                )
            }
        }
    }
}

@Composable
private fun fieldColors(palette: DeckPalette) = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = palette.accent,
    unfocusedBorderColor = palette.line,
    focusedLabelColor = palette.accent,
    unfocusedLabelColor = palette.inkSoft,
    cursorColor = palette.accent,
    focusedContainerColor = Color.White,
    unfocusedContainerColor = Color.White,
)

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
        colors = fieldColors(palette),
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
