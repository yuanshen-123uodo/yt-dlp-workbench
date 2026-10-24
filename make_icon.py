# -*- coding: utf-8 -*-
"""生成 yt-dlp-workbench 应用图标：蓝紫渐变圆角方块 + 白色播放三角（与前端 logo 一致）。"""
from PIL import Image, ImageDraw

SIZE = 256
RADIUS = 56          # 圆角
C1 = (76, 141, 255)  # #4c8dff
C2 = (139, 92, 246)  # #8b5cf6

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

# 渐变（左上 -> 右下）画进圆角蒙版
grad = Image.new("RGBA", (SIZE, SIZE))
px = grad.load()
for y in range(SIZE):
    for x in range(SIZE):
        t = (x + y) / (2 * (SIZE - 1))
        px[x, y] = (
            int(C1[0] + (C2[0] - C1[0]) * t),
            int(C1[1] + (C2[1] - C1[1]) * t),
            int(C1[2] + (C2[2] - C1[2]) * t),
            255,
        )

mask = Image.new("L", (SIZE, SIZE), 0)
md = ImageDraw.Draw(mask)
md.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=RADIUS, fill=255)
img.paste(grad, (0, 0), mask)

# 播放三角（对应前端 path: M8 5.5v13l11-6.5-11-6.5Z，居中放大）
d = ImageDraw.Draw(img)
cx, cy = SIZE / 2, SIZE / 2
tri = [
    (cx - 34, cy - 46),
    (cx - 34, cy + 46),
    (cx + 50, cy),
]
d.polygon(tri, fill=(255, 255, 255, 255))

img.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon.ico 已生成")
img.save("icon_preview.png")
print("icon_preview.png 已生成（预览用）")
