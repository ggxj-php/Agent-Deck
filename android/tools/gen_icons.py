from PIL import Image, ImageDraw
import os

src = Image.open(r"APP_ICON_SOURCE").convert("RGBA")
res = r"android/app/src/main/res"
bg = (255, 255, 255, 255)
tint = (238, 242, 255, 255)

def rounded(img, radius_ratio=0.22):
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(w * radius_ratio), fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out

def legacy(size):
    canvas = Image.new("RGBA", (size, size), tint)
    inner = int(size * 0.90)
    art = src.resize((inner, inner), Image.LANCZOS)
    canvas.paste(art, ((size - inner) // 2, (size - inner) // 2), art)
    return rounded(canvas)

def foreground(size):
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inner = int(size * 0.62)   # 自适应图标安全区
    art = src.resize((inner, inner), Image.LANCZOS)
    canvas.paste(art, ((size - inner) // 2, (size - inner) // 2), art)
    return canvas

dens = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}
for name, scale in dens.items():
    d = os.path.join(res, f"mipmap-{name}")
    os.makedirs(d, exist_ok=True)
    legacy(int(48 * scale)).save(os.path.join(d, "ic_launcher.png"))
    foreground(int(108 * scale)).save(os.path.join(d, "ic_launcher_avatar_fg.png"))

print("icons generated")

