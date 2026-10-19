"""The installed app's icons (page/icons/, named in page/manifest.webmanifest): page/favicon.svg's design
drawn at 192 and 512 px, and a maskable 512 px one (the full square in the favicon's blue, the bars inside
the middle 40%, which every mask keeps). Drawn four times larger and scaled down, for smooth edges.

    .venv\\Scripts\\python apps/staf-web/scripts/make_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "page" / "icons"
BLUE = "#243a61"
# favicon.svg's bars in its 32-unit square: x, y, width, height (radius half the height), color
BARS = [(7, 8, 18, 3.6, "#ffffff"), (7, 14.2, 13, 3.6, "#cdd8ec"), (7, 20.4, 8, 3.6, "#8fa6cc")]
SUPER = 4


def icon(size: int, maskable: bool) -> Image.Image:
    big = size * SUPER
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if maskable:
        d.rectangle([0, 0, big, big], fill=BLUE)
        # the bars' box (x 7 to 25, y 8 to 24) scaled to 40% of the side, centred
        scale = 0.40 * big / 18.0
        ox = big / 2.0 - 16.0 * scale
        oy = big / 2.0 - 16.0 * scale
    else:
        scale = big / 32.0
        d.rounded_rectangle([0, 0, big - 1, big - 1], radius=7 * scale, fill=BLUE)
        ox = oy = 0.0
    for x, y, w, h, color in BARS:
        box = [ox + x * scale, oy + y * scale, ox + (x + w) * scale, oy + (y + h) * scale]
        d.rounded_rectangle(box, radius=h / 2.0 * scale, fill=color)
    return im.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon(192, False).save(OUT / "staf-192.png", optimize=True)
    icon(512, False).save(OUT / "staf-512.png", optimize=True)
    icon(512, True).save(OUT / "staf-maskable-512.png", optimize=True)
    for name in sorted(p.name for p in OUT.iterdir()):
        print(name, (OUT / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
