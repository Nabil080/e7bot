"""Draw the bot's icon, a blue tile with "E7", into ui/icon.ico (window, taskbar, .exe) and ui/icon.png.

Run once after changing the drawing: python tools/build_icon.py. Pillow is a dev tool here; the bot only reads the files.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SIZE = 256
GRAPHITE = "#1a1d23"
EDGE = "#3a3f48"
LABEL = "#2b5cff"
WHITE = "#ffffff"
# Windows' condensed DIN face, the window's own: present on every Windows 10 and 11.
FONT = Path("C:/Windows/Fonts/bahnschrift.ttf")
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def draw() -> Image.Image:
    """Return the icon at 256 px: a dark tile with a blue label carrying "E7"."""
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    pen.rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=44, fill=GRAPHITE, outline=EDGE, width=6)
    pen.rounded_rectangle((28, 28, SIZE - 28, SIZE - 28), radius=26, fill=LABEL)
    font = ImageFont.truetype(str(FONT), 150)
    font.set_variation_by_name("Bold SemiCondensed")
    pen.text((SIZE / 2, SIZE / 2 + 4), "E7", font=font, fill=WHITE, anchor="mm")
    return image


def main() -> None:
    icon = draw()
    icon.save(ROOT / "ui" / "icon.png")
    icon.save(ROOT / "ui" / "icon.ico", sizes=ICO_SIZES)
    print("ui/icon.ico and ui/icon.png written")


if __name__ == "__main__":
    main()
