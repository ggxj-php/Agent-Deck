package com.agentdeck.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.agentdeck.app.DeckApp
import com.agentdeck.app.data.DeckException
import com.agentdeck.app.data.SkillInfo
import com.agentdeck.app.ui.theme.DeckPalette
import kotlinx.coroutines.launch

/**
 * 「技能」：服务器上装了哪些技能，只读。
 *
 * 和网页端侧边栏那个「技能」弹窗同一份数据（`GET /api/skills`）——技能本身在后台
 * 安装 / 启停，这里只负责让用户知道助手现在会些什么。样式和意见反馈页一致：
 * 顶栏渐变、卡片描边、下拉刷新，颜色跟着身份卡走。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SkillsScreen(palette: DeckPalette, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()

    var skills by remember { mutableStateOf<List<SkillInfo>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var refreshing by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    androidx.activity.compose.BackHandler { onBack() }

    /** [first] = true 是第一次进页面（整页转圈），false 是下拉刷新。 */
    suspend fun reload(first: Boolean = false) {
        if (first) loading = true else refreshing = true
        error = null
        runCatching { DeckApp.instance.api.skills() }
            .onSuccess { skills = it }
            .onFailure {
                error = if (it is DeckException) it.message else "拉取失败：${it.message ?: "网络波动"}"
            }
        loading = false
        refreshing = false
    }

    LaunchedEffect(Unit) { reload(first = true) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(palette.bg)
            .statusBarsPadding()
            .navigationBarsPadding(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .background(
                    Brush.verticalGradient(
                        listOf(palette.pinkPale, palette.pinkMist.copy(alpha = 0.35f), palette.bg),
                    ),
                )
                .padding(start = 6.dp, end = 10.dp, top = 6.dp, bottom = 14.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) {
                    Icon(
                        Icons.AutoMirrored.Rounded.ArrowBack,
                        contentDescription = "返回",
                        tint = palette.ink,
                    )
                }
                Column(modifier = Modifier.weight(1f)) {
                    Text("技能", fontSize = 17.sp, color = palette.ink)
                    Text(
                        text = when {
                            loading -> "正在加载…"
                            skills.isEmpty() -> "助手现在会些什么"
                            else -> "共 ${skills.size} 个 · 在后台安装和启停"
                        },
                        fontSize = 11.sp,
                        color = palette.inkSoft,
                    )
                }
                IconButton(onClick = { scope.launch { reload() } }) {
                    Icon(
                        Icons.Rounded.Refresh,
                        contentDescription = "刷新技能列表",
                        tint = palette.inkSoft,
                    )
                }
            }
        }

        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = { scope.launch { reload() } },
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
        ) {
            when {
                loading && skills.isEmpty() -> Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator(color = palette.accent, strokeWidth = 2.dp)
                }

                error != null -> Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(28.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Icon(Icons.Rounded.ErrorOutline, contentDescription = null, tint = palette.inkSoft)
                    Spacer(Modifier.height(8.dp))
                    Text(error ?: "", fontSize = 13.sp, color = palette.inkSoft)
                    Spacer(Modifier.height(12.dp))
                    Surface(
                        onClick = { scope.launch { reload() } },
                        shape = RoundedCornerShape(999.dp),
                        color = palette.bubble,
                        border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
                    ) {
                        Text(
                            "重试",
                            fontSize = 12.sp,
                            color = palette.ink,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                        )
                    }
                }

                skills.isEmpty() -> Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("还没有安装技能", fontSize = 14.sp, color = palette.ink)
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "在管理后台装好之后，这里会列出来",
                            fontSize = 12.sp,
                            color = palette.inkSoft,
                        )
                    }
                }

                else -> LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 10.dp),
                    verticalArrangement = Arrangement.spacedBy(9.dp),
                ) {
                    items(skills, key = { it.name }) { skill ->
                        SkillCard(skill, palette)
                    }
                    item {
                        Text(
                            text = "技能由服务器统一安装，这里只能看；想让助手做某件事，直接跟它说就行。",
                            fontSize = 11.sp,
                            lineHeight = 17.sp,
                            color = palette.inkSoft,
                            modifier = Modifier.padding(horizontal = 4.dp, vertical = 6.dp),
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun SkillCard(skill: SkillInfo, palette: DeckPalette) {
    Surface(
        color = palette.bubble,
        shape = RoundedCornerShape(14.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, palette.line),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(horizontal = 13.dp, vertical = 11.dp)) {
            Text(
                text = skill.name,
                fontSize = 14.5.sp,
                fontWeight = FontWeight.SemiBold,
                color = palette.ink,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                text = skill.summary,
                fontSize = 12.5.sp,
                lineHeight = 18.sp,
                color = palette.inkSoft,
            )
        }
    }
}
