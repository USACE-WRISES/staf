"""Generate the STAF mark: desktop/resources/icon.ico (app icon) and docs/favicon.ico (site).

Three white stream meanders — the three assessment tiers — over the site's accent blue
(#2f4b7c family), on a rounded square. Drawn at 512 px with supersampling headroom and
downsampled into multi-resolution .ico files. Deterministic: rerunning produces the same art.

Usage:  python desktop/scripts/make_icon.py [repo-root]
Requires Pillow (repo .venv has it).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

CANVAS = 512
TOP_COLOR = (58, 90, 148)      # lighter accent
BOTTOM_COLOR = (26, 53, 94)    # deeper accent
STREAM = (255, 255, 255)

# (baseline y, amplitude, stroke width, opacity) — thickest at the bottom, like tiers deepening.
RIBBONS = [
    (0.30, 0.045, 34, 235),
    (0.50, 0.055, 44, 245),
    (0.71, 0.065, 56, 255),
]


def build_mark(size: int = CANVAS, supersample: int = 2) -> Image.Image:
    big = size * supersample
    art = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(art)

    # Vertical gradient background.
    for y in range(big):
        t = y / (big - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(TOP_COLOR, BOTTOM_COLOR))
        draw.line([(0, y), (big, y)], fill=color + (255,))

    # Stream ribbons as filled bands (a stroked polyline this wide grows fringe artifacts).
    for index, (baseline, amplitude, width, alpha) in enumerate(RIBBONS):
        half = width * supersample / 2
        phase = index * 0.9

        def center(x: float) -> float:
            return big * baseline + big * amplitude * math.sin(2 * math.pi * 1.25 * x / big + phase)

        xs = list(range(-8, big + 9, 4))
        upper = [(x, center(x) - half) for x in xs]
        lower = [(x, center(x) + half) for x in reversed(xs)]
        draw.polygon(upper + lower, fill=STREAM + (alpha,))

    # Rounded-square mask.
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(0, 0), (big - 1, big - 1)], radius=round(big * 0.18), fill=255)
    art.putalpha(mask)
    return art.resize((size, size), Image.LANCZOS)


def save_ico(art: Image.Image, path: Path, sizes: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    art.save(path, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"wrote {path} ({path.stat().st_size} bytes, sizes {sizes})")


def main() -> None:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
    art = build_mark()
    save_ico(art, repo / "desktop" / "resources" / "icon.ico", [16, 24, 32, 48, 64, 128, 256])
    save_ico(art, repo / "docs" / "favicon.ico", [16, 32, 48])


if __name__ == "__main__":
    main()
