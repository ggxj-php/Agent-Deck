package icu.taffy.deck.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.animateScrollBy
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.rounded.DeleteOutline
import androidx.compose.material.icons.rounded.KeyboardArrowDown
import androidx.compose.material.icons.rounded.Menu
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import icu.taffy.deck.BuildConfig
import icu.taffy.deck.data.SessionSummary
import icu.taffy.deck.ui.components.Composer
import icu.taffy.deck.ui.components.MessageItem
import icu.taffy.deck.ui.components.ZoomableImageDialog
import icu.taffy.deck.ui.theme.LocalPalette
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(state: ChatUiState, viewModel: ChatViewModel) {
    val palette = LocalPalette.current
    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var pendingDelete by remember { mutableStateOf<SessionSummary?>(null) }
    var showPasswordDialog by remember { mutableStateOf(false) }
    var previewUrl by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(state.toast) {
        state.toast?.let { message ->
            snackbar.showSnackbar(message)
            viewModel.dismissToast()
        }
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        gesturesEnabled = !state.busy,
        drawerContent = {
            ModalDrawerSheet(
                drawerState = drawerState,
                drawerContainerColor = palette.bubble,
                modifier = Modifier.width(300.dp),
            ) {
                DrawerContent(
                    state = state,
                    palette = palette,
                    onNewSession = {
                        scope.launch { drawerState.close() }
                        viewModel.newSession()
                    },
                    onOpenSession = { id ->
                        scope.launch { drawerState.close() }
                        viewModel.openSession(id)
                    },
                    onDeleteSession = { pendingDelete = it },
                    onLogout = viewModel::logout,
                    onChangePassword = { showPasswordDialog = true },
                )
            }
        },
    ) {
        Scaffold(
            containerColor = palette.bg,
            snackbarHost = { SnackbarHost(snackbar) },
            topBar = {
                ChatHeader(
                    state = state,
                    palette = palette,
                    onMenu = { scope.launch { drawerState.open() } },
                    onNewSession = viewModel::newSession,
                    onToggleThinking = viewModel::toggleThinking,
                )
            },
            bottomBar = {
                Column(
                    modifier = Modifier
                        .background(palette.bubble)
                        .navigationBarsPadding()
                        .imePadding(),
                ) {
                    ContextBar(state, palette)
                    Composer(
                        busy = state.busy,
                        persona = state.persona,
                        palette = palette,
                        onSend = { text, images, files -> viewModel.send(text, images, files) },
                        onStop = viewModel::stop,
                    )
                }
            },
        ) { padding ->
            MessageList(state, palette, Modifier.padding(padding)) { previewUrl = it }
        }
    }

    pendingDelete?.let { session ->
        AlertDialog(
            onDismissRequest = { pendingDelete = null },
            containerColor = palette.bubble,
            title = { Text("删掉这条会话？") },
            text = { Text("「${session.title}」的历史会一起删掉，删了找不回来。") },
            confirmButton = {
                TextButton(onClick = { viewModel.deleteSession(session.id); pendingDelete = null }) {
                    Text("删除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { pendingDelete = null }) { Text("留着", color = palette.inkSoft) }
            },
        )
    }

    if (showPasswordDialog) {
        PasswordDialog(
            palette = palette,
            onDismiss = { showPasswordDialog = false },
            onSubmit = { oldPassword, newPassword ->
                viewModel.changePassword(oldPassword, newPassword)
                showPasswordDialog = false
            },
        )
    }

    previewUrl?.let { url ->
        ZoomableImageDialog(model = url, description = null) { previewUrl = null }
    }
}

/** 改密码：旧密码 + 新密码 + 确认，和网页端「改密码」一致（至少 8 位）。 */
@Composable
private fun PasswordDialog(
    palette: icu.taffy.deck.ui.theme.DeckPalette,
    onDismiss: () -> Unit,
    onSubmit: (String, String) -> Unit,
) {
    var oldPassword by remember { mutableStateOf("") }
    var newPassword by remember { mutableStateOf("") }
    var repeatPassword by remember { mutableStateOf("") }
    var localError by remember { mutableStateOf<String?>(null) }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = palette.bubble,
        title = { Text("修改密码") },
        text = {
            Column {
                OutlinedTextField(
                    value = oldPassword,
                    onValueChange = { oldPassword = it.take(128) },
                    label = { Text("当前密码", fontSize = 12.5.sp) },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = palette.accent,
                        unfocusedBorderColor = palette.line,
                        focusedLabelColor = palette.accent,
                        cursorColor = palette.accent,
                    ),
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = newPassword,
                    onValueChange = { newPassword = it.take(128) },
                    label = { Text("新密码（至少 8 位）", fontSize = 12.5.sp) },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = palette.accent,
                        unfocusedBorderColor = palette.line,
                        focusedLabelColor = palette.accent,
                        cursorColor = palette.accent,
                    ),
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = repeatPassword,
                    onValueChange = { repeatPassword = it.take(128) },
                    label = { Text("再输一次新密码", fontSize = 12.5.sp) },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = palette.accent,
                        unfocusedBorderColor = palette.line,
                        focusedLabelColor = palette.accent,
                        cursorColor = palette.accent,
                    ),
                    modifier = Modifier.fillMaxWidth(),
                )
                localError?.let {
                    Spacer(Modifier.height(6.dp))
                    Text(it, fontSize = 11.5.sp, color = MaterialTheme.colorScheme.error)
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    localError = when {
                        oldPassword.isEmpty() -> "当前密码得填"
                        newPassword.length < 8 -> "新密码至少 8 位"
                        newPassword != repeatPassword -> "两次输入的新密码不一样"
                        else -> null
                    }
                    if (localError == null) onSubmit(oldPassword, newPassword)
                },
            ) { Text("确认修改", color = palette.accent) }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消", color = palette.inkSoft) }
        },
    )
}

/* ---------------- 顶栏 ---------------- */

@Composable
private fun ChatHeader(
    state: ChatUiState,
    palette: icu.taffy.deck.ui.theme.DeckPalette,
    onMenu: () -> Unit,
    onNewSession: () -> Unit,
    onToggleThinking: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(Brush.linearGradient(listOf(palette.pinkPale, palette.pinkMist)))
            .statusBarsPadding(),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 14.dp, end = 14.dp, top = 8.dp, bottom = 8.dp),
        ) {
            // 汉堡按钮：半透明白圆，和网页端 #side-toggle 一致
            Surface(
                onClick = onMenu,
                shape = CircleShape,
                color = android.graphics.Color.WHITE.let { androidx.compose.ui.graphics.Color(it) }
                    .copy(alpha = 0.55f),
                modifier = Modifier.size(34.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(
                        Icons.Rounded.Menu,
                        contentDescription = "会话",
                        modifier = Modifier.size(18.dp),
                        tint = palette.ink,
                    )
                }
            }
            Spacer(Modifier.width(10.dp))

            AsyncImage(
                model = state.persona.avatarUrl(BuildConfig.BASE_URL),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .size(38.dp)
                    .shadow(6.dp, CircleShape)
                    .clip(CircleShape)
                    .border(2.dp, androidx.compose.ui.graphics.Color.White, CircleShape),
            )
            Spacer(Modifier.width(10.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = state.persona.name,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                    color = palette.ink,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = when {
                        state.busy -> state.persona.statusThinking.ifBlank { "正在思考…" }
                        else -> state.persona.statusOnline.ifBlank { "在线" }
                    },
                    fontSize = 12.sp,
                    color = palette.pinkDeep,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            ThinkToggle(enabled = state.thinking, palette = palette, onToggle = onToggleThinking)
            Spacer(Modifier.width(8.dp))
            Surface(
                onClick = onNewSession,
                enabled = !state.busy,
                shape = RoundedCornerShape(999.dp),
                color = androidx.compose.ui.graphics.Color.Transparent,
                modifier = Modifier
                    .shadow(3.dp, RoundedCornerShape(999.dp))
                    .background(
                        Brush.linearGradient(listOf(palette.accent, palette.pinkDeep)),
                        RoundedCornerShape(999.dp),
                    ),
            ) {
                Text(
                    text = "新会话",
                    fontSize = 13.sp,
                    color = androidx.compose.ui.graphics.Color.White,
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
                )
            }
        }
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(palette.line),
        )
    }
}

@Composable
private fun ThinkToggle(
    enabled: Boolean,
    palette: icu.taffy.deck.ui.theme.DeckPalette,
    onToggle: () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(androidx.compose.ui.graphics.Color.White.copy(alpha = 0.65f))
            .clickable(onClick = onToggle)
            .padding(horizontal = 9.dp, vertical = 5.dp),
    ) {
        Text(
            text = "思考",
            fontSize = 11.sp,
            color = if (enabled) palette.accent else palette.inkSoft,
        )
        Spacer(Modifier.width(5.dp))
        Box(
            modifier = Modifier
                .size(width = 26.dp, height = 14.dp)
                .clip(CircleShape)
                .background(if (enabled) palette.accent else palette.line),
        ) {
            Box(
                modifier = Modifier
                    .offset(x = if (enabled) 13.dp else 2.dp, y = 2.dp)
                    .size(10.dp)
                    .clip(CircleShape)
                    .background(androidx.compose.ui.graphics.Color.White),
            )
        }
    }
}

/* ---------------- 上下文条（在输入框上方，和网页端一致） ---------------- */

@Composable
private fun ContextBar(state: ChatUiState, palette: icu.taffy.deck.ui.theme.DeckPalette) {
    val usage = state.context
    if (!usage.known || usage.limit <= 0) return
    val ratio = (usage.percent / 100.0).toFloat().coerceIn(0f, 1f)
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .background(palette.bubble)
            .padding(start = 12.dp, end = 12.dp, top = 7.dp),
    ) {
        Text("上下文", fontSize = 11.5.sp, color = palette.inkSoft)
        Spacer(Modifier.width(8.dp))
        Box(
            modifier = Modifier
                .weight(1f)
                .height(5.dp)
                .clip(RoundedCornerShape(999.dp))
                .background(palette.trackFill),
        ) {
            Box(
                modifier = Modifier
                    .fillMaxWidth(ratio)
                    .height(5.dp)
                    .clip(RoundedCornerShape(999.dp))
                    .background(Brush.horizontalGradient(listOf(palette.pink, palette.accent))),
            )
        }
        Spacer(Modifier.width(8.dp))
        Text(
            text = "${"%.1f".format(usage.percent)}% · ${usage.tokens / 1000}k / ${usage.limit / 1000}k",
            fontSize = 11.5.sp,
            color = palette.inkSoft,
        )
    }
}

/* ---------------- 消息列表 ---------------- */

@Composable
private fun MessageList(
    state: ChatUiState,
    palette: icu.taffy.deck.ui.theme.DeckPalette,
    modifier: Modifier = Modifier,
    onPreview: (String) -> Unit,
) {
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val latestMessages by rememberUpdatedState(state.messages)
    val streaming by rememberUpdatedState(state.busy)

    // 是否贴着底部：最后一条完整露出、且离底不超过 120px 才算「跟随中」
    fun pinned(): Boolean {
        val info = listState.layoutInfo
        val last = info.visibleItemsInfo.lastOrNull() ?: return true
        return last.index == info.totalItemsCount - 1 &&
            (last.offset + last.size - info.viewportEndOffset) <= 120
    }

    val showJump by remember {
        derivedStateOf {
            latestMessages.isNotEmpty() && !pinned()
        }
    }

    // 只有正在流式输出时才跟随滚动；空闲时这个循环什么都不做，
    // 免得一边看历史、它一边每 120ms 动一下布局。
    LaunchedEffect(listState) {
        while (isActive) {
            delay(120)
            if (!streaming) continue
            val messages = latestMessages
            if (messages.isEmpty()) continue
            if (!listState.isScrollInProgress && pinned()) {
                listState.scrollToItem(messages.lastIndex)
                listState.scrollBy(500_000f)
            }
        }
    }

    Box(modifier = modifier.fillMaxSize()) {
        if (state.messages.isEmpty()) EmptyHero(state, palette)
        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 2.dp),
        ) {
            items(
                items = state.messages,
                key = { it.id },
                contentType = { it.role.name },
            ) { message ->
                MessageItem(
                    message = message,
                    baseUrl = BuildConfig.BASE_URL,
                    palette = palette,
                    avatarUrl = state.persona.avatarUrl(BuildConfig.BASE_URL),
                    onPreview = onPreview,
                )
            }
            item { Spacer(Modifier.height(8.dp)) }
        }

        // 和网页端右下角那个「↓」一样：翻上去了就冒出来，点一下回到最新
        AnimatedVisibility(
            visible = showJump,
            enter = fadeIn() + scaleIn(initialScale = 0.8f),
            exit = fadeOut() + scaleOut(targetScale = 0.8f),
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(end = 14.dp, bottom = 18.dp),
        ) {
            Surface(
                onClick = {
                    scope.launch {
                        val messages = latestMessages
                        if (messages.isEmpty()) return@launch
                        listState.animateScrollToItem(messages.lastIndex)
                        listState.animateScrollBy(500_000f)
                    }
                },
                shape = CircleShape,
                color = palette.bubble,
                shadowElevation = 8.dp,
                border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
                modifier = Modifier.size(36.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(
                        Icons.Rounded.KeyboardArrowDown,
                        contentDescription = "回到底部",
                        tint = palette.accent,
                        modifier = Modifier.size(20.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun EmptyHero(state: ChatUiState, palette: icu.taffy.deck.ui.theme.DeckPalette) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        AsyncImage(
            model = state.persona.avatarUrl(BuildConfig.BASE_URL),
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier
                .size(84.dp)
                .shadow(10.dp, CircleShape)
                .clip(CircleShape)
                .border(2.dp, androidx.compose.ui.graphics.Color.White, CircleShape),
        )
        Spacer(Modifier.height(14.dp))
        Text(
            text = state.persona.welcome.ifBlank { "有什么想聊的？" },
            fontSize = 15.sp,
            color = palette.ink,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            text = "发条消息开始吧",
            fontSize = 12.sp,
            color = palette.inkSoft,
        )
    }
}

/* ---------------- 抽屉 ---------------- */

@Composable
private fun DrawerContent(
    state: ChatUiState,
    palette: icu.taffy.deck.ui.theme.DeckPalette,
    onNewSession: () -> Unit,
    onOpenSession: (String) -> Unit,
    onDeleteSession: (SessionSummary) -> Unit,
    onLogout: () -> Unit,
    onChangePassword: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxHeight()
            .background(palette.bubble)
            .statusBarsPadding()
            .padding(horizontal = 12.dp, vertical = 14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            AsyncImage(
                model = state.persona.avatarUrl(BuildConfig.BASE_URL),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .size(44.dp)
                    .clip(CircleShape),
            )
            Spacer(Modifier.width(12.dp))
            Column {
                Text(
                    text = state.account?.name ?: "未登录",
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                    color = palette.ink,
                )
                Text(
                    text = state.account?.inviteCode?.let { "邀请码 $it" }.orEmpty(),
                    fontSize = 11.sp,
                    color = palette.inkSoft,
                )
            }
        }

        Spacer(Modifier.height(14.dp))
        Surface(
            onClick = onNewSession,
            shape = RoundedCornerShape(10.dp),
            color = androidx.compose.ui.graphics.Color.Transparent,
            border = androidx.compose.foundation.BorderStroke(
                1.dp,
                palette.accent.copy(alpha = 0.55f),
            ),
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(10.dp)),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.Center,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 10.dp),
            ) {
                Icon(
                    Icons.Rounded.Add,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                    tint = palette.accent,
                )
                Spacer(Modifier.width(6.dp))
                Text("新会话", fontSize = 13.5.sp, color = palette.accent)
            }
        }

        Spacer(Modifier.height(14.dp))
        LazyColumn(modifier = Modifier.weight(1f)) {
            items(items = state.sessions, key = { it.id }) { session ->
                SessionRow(
                    session = session,
                    selected = session.id == state.currentSessionId,
                    palette = palette,
                    onClick = { onOpenSession(session.id) },
                    onDelete = { onDeleteSession(session) },
                )
            }
        }

        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 10.dp),
        ) {
            DrawerFootButton("改密码", palette, onChangePassword, Modifier.weight(1f))
            DrawerFootButton("退出登录", palette, onLogout, Modifier.weight(1f))
        }
    }
}

@Composable
private fun DrawerFootButton(
    text: String,
    palette: icu.taffy.deck.ui.theme.DeckPalette,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(10.dp),
        color = palette.softFill,
        modifier = modifier,
    ) {
        Text(
            text = text,
            fontSize = 12.5.sp,
            color = palette.inkSoft,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 9.dp),
        )
    }
}

@Composable
private fun SessionRow(
    session: SessionSummary,
    selected: Boolean,
    palette: icu.taffy.deck.ui.theme.DeckPalette,
    onClick: () -> Unit,
    onDelete: () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 1.dp)
            .clip(RoundedCornerShape(9.dp))
            .background(if (selected) palette.activeFill else androidx.compose.ui.graphics.Color.Transparent)
            .clickable(onClick = onClick)
            .padding(start = 6.dp, end = 2.dp, top = 7.dp, bottom = 7.dp),
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = session.title,
                fontSize = 13.5.sp,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                color = if (selected) palette.activeText else palette.ink,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = "${session.messages} 条 · ${formatTime(session.updatedAt)}",
                fontSize = 10.sp,
                color = palette.inkSoft,
            )
        }
        IconButton(onClick = onDelete, modifier = Modifier.size(26.dp)) {
            Icon(
                Icons.Rounded.DeleteOutline,
                contentDescription = "删除会话",
                modifier = Modifier.size(14.dp),
                tint = palette.inkSoft.copy(alpha = 0.7f),
            )
        }
    }
}

private fun formatTime(epochSeconds: Double): String {
    if (epochSeconds <= 0) return ""
    return runCatching {
        SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()).format(Date((epochSeconds * 1000).toLong()))
    }.getOrDefault("")
}
