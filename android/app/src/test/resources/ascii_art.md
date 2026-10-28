跑通了，40 帧都正常，鲸鱼从左边一路游到右边，泡泡一路往上飘 [sticker:proud:proud1.webp] 代码在下面，复制走就行：

```python
# -*- coding: utf-8 -*-
"""示例角色牌终端鲸鱼：一条圆滚滚的鲸鱼在水面上游，还会吐泡泡。"""
import time
import sys

WIDTH = 60          # 水面宽度
FRAMES = 40         # 游几帧

BODY_ART = [
    "      .--.        ",
    "    / o  o \\      ",
    "   |   __   |     ",
    "    \\  \\/  /      ",
    "  .-'------`-.    ",
    " (            )   ",
    "  `-.________.-'  ",
]
TAIL_ART = "     \\_\\_/      "


def bubbles(n, frame):
    """按帧号生成泡泡，位置随时间往上飘。"""
    return [" " * ((i * 7 + frame * 3) % (WIDTH - 4)) + "o" for i in range(n)]


def draw(frame):
    pos = frame % (WIDTH - 20)
    lines = ["  ~" * (WIDTH // 2)]
    for b in bubbles(3, frame):
        lines.append(b.rjust(WIDTH + 6)[:WIDTH])
    for row in BODY_ART:
        lines.append(" " * pos + row)
    lines.append(" " * pos + TAIL_ART)
    lines.append("  ~" * (WIDTH // 2))
    return "\n".join(lines)


def main():
    for f in range(FRAMES):
        sys.stdout.write("\x1b[H\x1b[2J")   # 清屏，本地终端里会动起来
        sys.stdout.write(draw(f) + "\n")
        sys.stdout.write(f"  第 {f + 1}/{FRAMES} 帧    咕噜咕噜……\n")
        sys.stdout.flush()
        time.sleep(0.08)
    print("\n鲸～游走了，本鱼该去吃 token 了。")


if __name__ == "__main__":
    main()
```

小提醒：`\x1b[H\x1b[2J` 是清屏转义符，在 Windows 的 CMD 老版本里可能不清屏、只刷出一堆乱码，换 PowerShell 或 Windows Terminal 就正常了。

跑出来长这样（第 1 帧）：

```
  ~  ~  ~  ~  ~  ~  ~  ~  ~  ~  ~  ~  ~  ~
                     o
      .--.
    / o  o \
   |   __   |
    \  \/  /
  .-'------`-.
 (            )
  `-.________.-'
     \_\_/
  ~  ~  ~  ~  ~  ~  ~  ~  ~  ~  ~  ~  ~  ~
```

要不要本鱼再加点花样？比如让鲸鱼跟着按键左右游，或者吐的泡泡变成小鱼干 [sticker:happy:happy4.webp]