"""Draws the screenadb icon: a white phone with viewfinder corners on a coral tile.

Drawn at 1024px and scaled down, so every size from 16px up stays smooth. Run it again after changing the design;
it writes screenadb.png (for the window) and screenadb.ico (for the shortcut and taskbar) beside itself.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
SIZE = 1024
CORAL_LIGHT = (255, 92, 100)
CORAL_DARK = (196, 28, 40)
WHITE = (255, 255, 255, 255)


def diagonal_gradient(size, start, end):
    """Top-left to bottom-right blend from start to end."""
    small = Image.new("RGB", (2, 2))
    small.putpixel((0, 0), start)
    small.putpixel((1, 1), end)
    mid = tuple((a + b) // 2 for a, b in zip(start, end))
    small.putpixel((1, 0), mid)
    small.putpixel((0, 1), mid)
    return small.resize((size, size), Image.BILINEAR)


def tile():
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=230, fill=255)
    art = diagonal_gradient(SIZE, CORAL_LIGHT, CORAL_DARK).convert("RGBA")
    # A soft light from the top-left corner gives the flat tile a little depth.
    glow = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(glow).ellipse((-260, -320, 640, 520), fill=70)
    art.paste(Image.new("RGBA", (SIZE, SIZE), WHITE), (0, 0), glow.filter(ImageFilter.GaussianBlur(120)))
    art.putalpha(mask)
    return art


def phone(draw):
    left, top, right, bottom = 302, 168, 722, 856
    draw.rounded_rectangle((left, top, right, bottom), radius=96, outline=WHITE, width=46)
    # The speaker slot at the top.
    draw.rounded_rectangle((462, 222, 562, 246), radius=12, fill=WHITE)
    # Viewfinder corners on the screen: the "capture" part of the mark.
    inset_left, inset_top, inset_right, inset_bottom = 388, 330, 636, 728
    arm, width = 70, 34
    for x, y, dx, dy in (
        (inset_left, inset_top, 1, 1),
        (inset_right, inset_top, -1, 1),
        (inset_left, inset_bottom, 1, -1),
        (inset_right, inset_bottom, -1, -1),
    ):
        draw.line((x, y, x + dx * arm, y), fill=WHITE, width=width)
        draw.line((x, y, x, y + dy * arm), fill=WHITE, width=width)
        draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=WHITE)
    # The shutter dot in the middle.
    draw.ellipse((470, 487, 554, 571), fill=WHITE)


def sparkle(draw, cx, cy, r):
    """The four-pointed star used across AccuPilot Pro, here as a small glint on the phone's corner."""
    points = []
    for step in range(8):
        angle = math.pi / 4 * step - math.pi / 2
        reach = r if step % 2 == 0 else r * 0.22
        points.append((cx + math.cos(angle) * reach, cy + math.sin(angle) * reach))
    draw.polygon(points, fill=WHITE)


def build():
    art = tile()
    # A soft shadow under the phone lifts it off the tile.
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((302, 196, 722, 884), radius=96, outline=(90, 0, 10, 110), width=46)
    art = Image.alpha_composite(art, shadow.filter(ImageFilter.GaussianBlur(18)))
    draw = ImageDraw.Draw(art)
    phone(draw)
    sparkle(draw, 760, 186, 92)
    return art


def main():
    art = build()
    art.resize((256, 256), Image.LANCZOS).save(HERE / "screenadb.png")
    art.save(HERE / "screenadb.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Wrote screenadb.png and screenadb.ico")


if __name__ == "__main__":
    main()
