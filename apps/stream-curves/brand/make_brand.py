"""Generate the StreamCurves mark: SVG masters, PNGs, the shell icon and the favicon.

The mark sits in HYPE Desktop's family (the same navy tile, sheen and orange accent): two faint
chart axes, a white reference curve rising from low to high condition, and an orange point on
it, a site read off its curve. Every color is a token from www/shell.css.

The 16 and 24 px faces are drawn from their own geometry, not downscaled: at those sizes the
axes vanish and a 19 px stroke on a 256 grid lands on about one device pixel, so the small faces
drop the axes and thicken the curve and the point.

Deterministic: rerunning writes the same bytes. Requires Pillow and numpy (both in the .venv).

    python apps/stream-curves/brand/make_brand.py            # writes every output below
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
APP = HERE.parent                      # apps/stream-curves
REPO = APP.parents[1]

# Tokens (www/shell.css), shared with HYPE's brand.
TILE_TOP = "#41669f"
TILE_BOTTOM = "#1e3255"
ACCENT = "#ff9500"
WHITE = "#ffffff"

# Geometry on a 256 grid.
AXES = "M 50 46 L 50 206 L 214 206"
CURVE = ((64, 186), (126, 186), (146, 72), (208, 72))     # cubic Bezier control points
POINT_T = 0.6                                              # where the site sits on the curve
POINT_R = 19
CURVE_W = 20
AXES_W = 9

# Small faces: no axes, a heavier curve and point, pulled in from the tile's edge.
SMALL_CURVE = ((40, 196), (120, 196), (136, 60), (216, 60))
SMALL_CURVE_W = 34
SMALL_POINT_R = 30
SMALL_POINT_T = 0.58


def _bezier(p, t):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p
    u = 1 - t
    x = u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3
    y = u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3
    return x, y


def _path_d(p) -> str:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p
    return f"M {x0} {y0} C {x1} {y1} {x2} {y2} {x3} {y3}"


def _svg(*, tile: bool, curve_color: str = WHITE, axes_opacity: float = .38) -> str:
    px, py = _bezier(CURVE, POINT_T)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" '
             'height="256" role="img" aria-label="StreamCurves">']
    if tile:
        parts += [
            '  <defs><linearGradient id="tile" x1="0" y1="0" x2=".38" y2="1">',
            f'      <stop offset="0" stop-color="{TILE_TOP}"/><stop offset="1" '
            f'stop-color="{TILE_BOTTOM}"/>',
            '    </linearGradient>',
            '    <linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1">',
            '      <stop offset="0" stop-color="#ffffff" stop-opacity=".15"/>',
            '      <stop offset=".52" stop-color="#ffffff" stop-opacity="0"/>',
            '    </linearGradient></defs>',
            '  <rect width="256" height="256" rx="56" fill="url(#tile)"/>'
            '<rect width="256" height="256" rx="56" fill="url(#sheen)"/>',
        ]
    parts += [
        f'  <path d="{AXES}" fill="none" stroke="{curve_color}" stroke-opacity="{axes_opacity}" '
        f'stroke-width="{AXES_W}" stroke-linecap="round" stroke-linejoin="round"/>',
        f'  <path d="{_path_d(CURVE)}" fill="none" stroke="{curve_color}" '
        f'stroke-width="{CURVE_W}" stroke-linecap="round"/>',
        f'  <circle cx="{px:.1f}" cy="{py:.1f}" r="{POINT_R}" fill="{ACCENT}"/>',
        '</svg>',
    ]
    return "\n".join(parts) + "\n"


def _hex(c: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha


def _tile(size: int) -> Image.Image:
    """The gradient tile with its sheen, as HYPE draws it (gradient vector (0,0) -> (.38,1))."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64) / (size - 1)
    gx, gy = .38, 1.0
    t = np.clip((xx * gx + yy * gy) / (gx * gx + gy * gy), 0, 1)
    top = np.array(_hex(TILE_TOP)[:3], dtype=np.float64)
    bot = np.array(_hex(TILE_BOTTOM)[:3], dtype=np.float64)
    rgb = top[None, None, :] * (1 - t[..., None]) + bot[None, None, :] * t[..., None]
    sheen = np.clip(1 - yy / .52, 0, 1) * .15
    rgb = rgb * (1 - sheen[..., None]) + 255 * sheen[..., None]
    img = Image.fromarray(np.dstack([rgb, np.full((size, size), 255.0)]).round().astype(np.uint8),
                          "RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (size - 1, size - 1)],
                                           radius=round(size * 56 / 256), fill=255)
    img.putalpha(mask)
    return img


def _stroke(draw: ImageDraw.ImageDraw, pts, width: float, fill) -> None:
    """A round-capped stroke drawn as a dense run of discs. Pillow's wide polylines leave
    seams between short segments; discs spaced well under their radius never do."""
    r = width / 2
    step = max(r / 6, .5)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = max(1, int(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** .5 / step))
        for i in range(n + 1):
            x, y = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n
            draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def _render(size: int, *, tile: bool, small: bool, ink: str = WHITE) -> Image.Image:
    ss = 8 if size <= 64 else 4
    big = size * ss
    k = big / 256
    base = _tile(big) if tile else Image.new("RGBA", (big, big), (0, 0, 0, 0))
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    curve = SMALL_CURVE if small else CURVE
    if not small:
        axes = [(50, 46), (50, 206), (214, 206)]
        axes_layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        _stroke(ImageDraw.Draw(axes_layer), [(x * k, y * k) for x, y in axes], AXES_W * k,
                _hex(ink, round(255 * .38)))
        base = Image.alpha_composite(base, axes_layer)
    pts = [(x * k, y * k) for x, y in (_bezier(curve, i / 160) for i in range(161))]
    _stroke(draw, pts, (SMALL_CURVE_W if small else CURVE_W) * k, _hex(ink))
    px, py = _bezier(curve, SMALL_POINT_T if small else POINT_T)
    r = (SMALL_POINT_R if small else POINT_R) * k
    draw.ellipse([px * k - r, py * k - r, px * k + r, py * k + r], fill=_hex(ACCENT))
    out = Image.alpha_composite(base, layer)
    return out.resize((size, size), Image.LANCZOS)


def _icon_frames(sizes, *, tile: bool = True) -> list[Image.Image]:
    return [_render(s, tile=tile, small=s <= 24) for s in sizes]


def _save_ico(path: Path, sizes) -> None:
    frames = _icon_frames(sizes)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Pillow writes one ICO entry per requested size from the largest frame unless each size
    # has its own image; append_images supplies the hand-drawn small faces.
    frames[-1].save(path, format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    print(f"wrote {path.relative_to(REPO)} ({path.stat().st_size} bytes)")


def _save_png(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True)
    print(f"wrote {path.relative_to(REPO)} ({path.stat().st_size} bytes)")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    print(f"wrote {path.relative_to(REPO)}")


def main() -> int:
    icon_svg = _svg(tile=True)
    glyph_white = _svg(tile=False)
    glyph_navy = _svg(tile=False, curve_color="#2f4b7c", axes_opacity=.45)
    _write_text(HERE / "streamcurves-icon.svg", icon_svg)
    _write_text(HERE / "streamcurves-glyph-white.svg", glyph_white)
    _write_text(HERE / "streamcurves-glyph-navy.svg", glyph_navy)
    for s in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        _save_png(_render(s, tile=True, small=s <= 24), HERE / "png" / f"streamcurves-icon-{s}.png")
    _save_ico(HERE / "icon.ico", [16, 24, 32, 48, 64, 128, 256])
    _save_ico(HERE / "favicon.ico", [16, 24, 32, 48, 64])
    # Deployed copies: the app's www/, the shell's resources and launcher page.
    www = APP / "www"
    _write_text(www / "streamcurves-icon.svg", icon_svg)
    _write_text(www / "streamcurves-glyph-white.svg", glyph_white)
    _save_ico(www / "favicon.ico", [16, 24, 32, 48, 64])
    _save_ico(REPO / "desktop" / "resources" / "icon.ico", [16, 24, 32, 48, 64, 128, 256])
    _save_png(_render(64, tile=True, small=False),
              REPO / "desktop" / "launcher" / "streamcurves-icon-64.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
