"""Compare the static app's captures with the Shiny STAF app's (parity/capture.py).

For each tool, writes to ``parity/diff/``:

- ``<tool>.png``: the static screenshot, grayed, with every pixel that differs from the Shiny one in
  red; the share of differing pixels and the 40 px blocks they fall in are printed;
- ``<tool>.diff``: a unified diff of the two pages' markup after Shiny's binding marks are taken
  out (``shiny-bound-*`` classes, session download links, Leaflet's generated names).

Also compares the two apps' About and Help dialogs and stylesheet lists.

    python apps/staf-web/parity/compare.py [--tolerance 0]
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
TOOLS = ("easi", "sfari", "deep")
BLOCK = 40

NORMALIZE = [
    (re.compile(r" ?\bshiny-bound-(?:input|output)\b"), ""),
    (re.compile(r'href="session/[^"]*"'), 'href=""'),
    (re.compile(r'name="leaflet-base-layers_\d+"'), 'name="leaflet-base-layers"'),
    (re.compile(r' class=""'), ""),
    (re.compile(r"^<!-- .* -->$", re.M), ""),
]


def normalize(html: str) -> list[str]:
    for pattern, repl in NORMALIZE:
        html = pattern.sub(repl, html)
    return [line.strip() for line in html.splitlines() if line.strip()]


def pixels(tool: str, tolerance: int) -> str:
    if not (HERE / "static" / f"{tool}.png").exists() or not (HERE / "shiny" / f"{tool}.png").exists():
        return "not captured in both"
    a = np.asarray(Image.open(HERE / "shiny" / f"{tool}.png").convert("RGB")).astype(np.int16)
    b = np.asarray(Image.open(HERE / "static" / f"{tool}.png").convert("RGB")).astype(np.int16)
    if a.shape != b.shape:
        return f"sizes differ: {a.shape} vs {b.shape}"
    diff = np.abs(a - b).max(axis=2) > tolerance
    gray = (b.mean(axis=2) * 0.35 + 150).astype(np.uint8)
    img = np.stack([gray, gray, gray], axis=2)
    img[diff] = (230, 20, 20)
    (HERE / "diff").mkdir(exist_ok=True)
    Image.fromarray(img).save(HERE / "diff" / f"{tool}.png")
    h, w = diff.shape
    blocks = sorted({(y // BLOCK, x // BLOCK) for y, x in zip(*np.nonzero(diff))})
    share = diff.mean() * 100
    where = ", ".join(f"({x * BLOCK},{y * BLOCK})" for y, x in blocks[:12]) + (" ..." if len(blocks) > 12 else "")
    return f"{share:.3f}% of {w}x{h} pixels differ, in {len(blocks)} blocks of {BLOCK} px{': ' + where if blocks else ''}"


def markup(tool: str) -> str:
    a = normalize((HERE / "shiny" / f"{tool}.html").read_text(encoding="utf-8"))
    b = normalize((HERE / "static" / f"{tool}.html").read_text(encoding="utf-8"))
    lines = list(difflib.unified_diff(a, b, "shiny", "static", lineterm="", n=1))
    (HERE / "diff" / f"{tool}.diff").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    changed = sum(1 for line in lines if line[:1] in "+-" and not line.startswith(("+++", "---")))
    return f"{changed} markup lines differ"


def main(tolerance: int) -> None:
    for tool in TOOLS:
        print(f"{tool}: {pixels(tool, tolerance)}; {markup(tool)}")
        for which in ("about", "help"):
            print(f"  {which}: {pixels(f'{tool}-{which}', tolerance)}")
    # captures at a view (capture.py --view ... --name NAME): <tool>-<NAME>.png and .html
    views = sorted({p.stem for p in (HERE / "static").glob("*-*.png")} - {f"{t}-{w}" for t in TOOLS for w in ("about", "help")})
    for stem in views:
        if (HERE / "shiny" / f"{stem}.png").exists():
            print(f"{stem}: {pixels(stem, tolerance)}; {markup(stem)}")
    a = json.loads((HERE / "shiny" / "dialogs.json").read_text(encoding="utf-8"))
    b = json.loads((HERE / "static" / "dialogs.json").read_text(encoding="utf-8"))
    print(f"dialogs.json: {'same' if a == b else 'DIFFERENT'}")
    for app in ("shiny", "static"):
        styles = json.loads((HERE / app / "styles.json").read_text(encoding="utf-8"))
        names = [s["href"].split("?")[0].rsplit("/", 1)[-1] + (f" [{s['tool']} {s['media']}]" if s["tool"] else "")
                 for s in styles]
        print(f"{app} stylesheets: {', '.join(names)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tolerance", type=int, default=0, help="largest channel difference counted as equal")
    main(ap.parse_args().tolerance)
