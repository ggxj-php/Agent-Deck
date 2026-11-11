package com.agentdeck.app.ui.components

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.BrokenImage
import androidx.compose.material.icons.rounded.InsertPhoto
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImagePainter
import coil.compose.rememberAsyncImagePainter
import com.agentdeck.app.ui.theme.DeckPalette
import com.agentdeck.app.ui.theme.LocalPalette

/**
 * 图片加载中的骨架占位。
 *
 * 配色全部从 [LocalPalette] 取——也就是当前身份卡（人设）那套派生色，
 * 换角色卡时占位会跟着变，不会突然冒出一块跟主题无关的灰。
 *
 * 层次（自下而上）：
 * 1. `pinkMist -> bg` 的柔和斜渐变，跟气泡/页面底色同一色系；
 * 2. 一条扫光带，用 accent 的 9% 透明度，只画不重组（在 draw 阶段读动画值）；
 * 3. 居中的圆形托底 + 图片图标，图标做呼吸（alpha 只在 draw 阶段变）。
 */
@Composable
fun ImageLoadingPlaceholder(
    modifier: Modifier = Modifier,
    palette: DeckPalette = LocalPalette.current,
    label: String? = null,
    shape: Shape = RoundedCornerShape(10.dp),
    iconSize: Dp = 20.dp,
) {
    val transition = rememberInfiniteTransition(label = "image-skeleton")

    // 扫光：0 → 1 线性循环，1.6s 一个来回
    val sweep = transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 1600, easing = LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "sweep",
    )
    // 呼吸：图标忽明忽暗，节奏比扫光快一点，看起来是「在等」而不是「卡住了」
    val pulse = transition.animateFloat(
        initialValue = 0.30f,
        targetValue = 0.62f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 900, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "pulse",
    )

    Box(
        modifier = modifier
            .clip(shape)
            .background(Brush.linearGradient(listOf(palette.pinkMist, palette.bg))),
        contentAlignment = Alignment.Center,
    ) {
        // 扫光层：drawBehind 里读动画值 → 只触发重绘，不触发重组
        Box(
            modifier = Modifier
                .matchParentSize()
                .drawBehind {
                    val w = size.width
                    val band = w * 0.62f
                    val left = -band + (w + band * 2f) * sweep.value
                    drawRect(
                        brush = Brush.linearGradient(
                            colors = listOf(
                                Color.Transparent,
                                palette.accent.copy(alpha = 0.10f),
                                Color.Transparent,
                            ),
                            start = Offset(left, 0f),
                            end = Offset(left + band, size.height),
                        ),
                    )
                },
        )

        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Box(
                modifier = Modifier
                    .size(iconSize * 1.9f)
                    .clip(CircleShape)
                    .background(Color.White.copy(alpha = 0.72f)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = Icons.Rounded.InsertPhoto,
                    contentDescription = null,
                    tint = palette.accent,
                    modifier = Modifier
                        .size(iconSize)
                        .graphicsLayer { alpha = pulse.value },
                )
            }
            if (label != null) {
                Spacer(Modifier.height(7.dp))
                Text(
                    text = label,
                    fontSize = 11.sp,
                    color = palette.inkSoft.copy(alpha = 0.9f),
                )
            }
        }
    }
}

/** 加载失败的静态占位：不再扫光，避免「明明挂了还在假装加载」。 */
@Composable
fun ImageUnavailablePlaceholder(
    modifier: Modifier = Modifier,
    palette: DeckPalette = LocalPalette.current,
    label: String = "图片加载失败",
    shape: Shape = RoundedCornerShape(10.dp),
) {
    Box(
        modifier = modifier
            .clip(shape)
            .background(palette.softFill),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(
                imageVector = Icons.Rounded.BrokenImage,
                contentDescription = null,
                tint = palette.inkSoft.copy(alpha = 0.7f),
                modifier = Modifier.size(19.dp),
            )
            Spacer(Modifier.height(6.dp))
            Text(text = label, fontSize = 11.sp, color = palette.inkSoft)
        }
    }
}

/**
 * 带骨架占位的异步图片。
 *
 * 为什么不用 `AsyncImage` 的 `placeholder` 参数：那个只收 `Painter`，画不了扫光这种
 * 组合动画。这里用 `rememberAsyncImagePainter` 自己读 state，加载中/失败时把占位
 * 铺在图片位置，成功后再换成图片本体。
 *
 * @param placeholderAspectRatio 加载中的占位按这个比例撑满宽度（图片还没解码，没有
 *   intrinsic size，不给比例会塌成 0 高度——那才是「一片空白」的由来）。传 null 表示
 *   由调用方的 modifier 决定高度。
 */
@Composable
fun DeckAsyncImage(
    model: Any?,
    contentDescription: String?,
    modifier: Modifier = Modifier,
    contentScale: ContentScale = ContentScale.Fit,
    shape: Shape = RoundedCornerShape(10.dp),
    placeholderAspectRatio: Float? = 3f / 2f,
    placeholderLabel: String? = null,
    errorLabel: String = "图片加载失败",
    onError: (() -> Unit)? = null,
    onClick: (() -> Unit)? = null,
) {
    val painter = rememberAsyncImagePainter(model)
    val state = painter.state
    val busy = state is AsyncImagePainter.State.Empty || state is AsyncImagePainter.State.Loading
    val failed = state is AsyncImagePainter.State.Error

    LaunchedEffect(state) {
        if (state is AsyncImagePainter.State.Error) onError?.invoke()
    }

    // 点击区跟图片一起裁圆角，水波纹不会露出直角
    val outer = if (onClick != null) {
        modifier.clip(shape).clickable { onClick() }
    } else {
        modifier
    }
    val slot = Modifier.fillMaxWidth().let {
        if (placeholderAspectRatio != null) it.aspectRatio(placeholderAspectRatio) else it
    }

    Box(modifier = outer, contentAlignment = Alignment.Center) {
        when {
            busy -> ImageLoadingPlaceholder(
                modifier = slot,
                label = placeholderLabel,
                shape = shape,
            )

            failed -> ImageUnavailablePlaceholder(
                modifier = slot,
                label = errorLabel,
                shape = shape,
            )

            else -> Image(
                painter = painter,
                contentDescription = contentDescription,
                contentScale = contentScale,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}
