"""Build ``data/FieldForm/manifest.json`` — the layout manifest for the field-form
overlay engine (``sfari.report.build_field_forms_pdf``).

The five JPEGs are the paper SFARI Field Worksheet v1.0, one page per discipline
(Hydrology, Hydraulics, Geomorphology, Physicochemistry, Biology), 1700x2200 px at
200 DPI (exact US Letter: 1700/200=8.5 in, 2200/200=11 in, so PDF scale = 72/200 =
0.36 pt/px uniformly). Each page has four function blocks; every block ends in a
"Score: | Notes/Other Metrics:" row that the overlay engine white-fills and reprints
with the pulled desktop values.

HOW RECTANGLES WERE MEASURED (all in original 1700x2200 pixel coords, origin
top-left):

* Column x-boundaries and every horizontal rule were found by thresholding the
  grayscale raster (<128 = ink) and collapsing rows/cols whose ink-fraction crosses
  a span threshold into single line positions. The four vertical rules land at
  x = 101 (left border), 417 (Function | Metrics), 1461 (Metrics | Score), 1555
  (right border) on ALL five pages -> HIGH confidence.
* Per-function "Notes/Other Metrics" rows: on every page the four function-block
  separators are the four bottom-most "thick" rules (ink band >= 4 px). Each Notes
  row is bounded below by that separator and above by the nearest rule above it. The
  Notes cell spans the Metrics column (x 417..1461). Detected automatically here ->
  HIGH confidence on the row bounds (they are real ruled cells, not estimates).
* Page-1 metadata label runs ("Reach ID:", "Reach Length:", "Coordinates:") were
  found by profiling ink columns across the metadata row (y 140..174). The value
  rects are the blank gaps just after each label -> MEDIUM/HIGH confidence.
* The baked-in "Page 1 of 1" footer bbox is x 785..912, y 2106..2130 on ALL pages
  (measured by profiling ink below the table). The mask rect pads it generously and
  sits in white space below every table -> HIGH confidence.

Regenerate after any asset change (the structural test gates on the checksums):

    D:/Code/Work/staf/.venv/Scripts/python.exe scripts/build_fieldform_manifest.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]              # apps/sfari
FORM = ROOT / "data" / "FieldForm"
sys.path.insert(0, str(ROOT))
from sfari import config  # noqa: E402

W, H, DPI = 1700, 2200, 200
PDF_SCALE = 72.0 / DPI                                   # 0.36 pt/px

# Vertical rules (constant across all five pages).
COL = {"left": 101, "func_metrics": 417, "metrics_score": 1461, "right": 1555}
NOTES_X0, NOTES_X1 = COL["func_metrics"], COL["metrics_score"]

# Page-1 metadata value rects (blank gaps after each label) + shared text baseline.
METADATA_RECTS = {
    "reach_id": [210, 146, 116, 26],
    "reach_length": [476, 146, 136, 26],
    "coordinates": [1295, 146, 255, 26],
}
METADATA_BASELINE_Y = 168

# Baked-in "Page 1 of 1" footer: measured bbox x785-912 y2106-2130; padded mask.
FOOTER_MASK = [758, 2098, 184, 42]

# Disciplines in page order (== config.CATEGORY_ORDER).
PAGE_DISCIPLINES = list(config.CATEGORY_ORDER)


def _hbands(gray: np.ndarray, thr: float = 0.45):
    """Return (top, bottom, thickness) for each horizontal ink rule."""
    h, w = gray.shape
    rows = (gray < 128).sum(axis=1) / w > thr
    bands, y = [], 0
    while y < h:
        if rows[y]:
            y0 = y
            while y < h and rows[y]:
                y += 1
            bands.append((y0, y - 1, y - y0))
        else:
            y += 1
    return bands


def _notes_rows(gray: np.ndarray) -> list[tuple[int, int]]:
    """Four (top, bottom) Notes-row bounds: the four bottom-most thick rules are the
    block separators (row bottoms); each row's top is the nearest rule above it."""
    bands = _hbands(gray)
    centers = [(a + b) // 2 for a, b, _ in bands]
    # Function-block separators are the double-weight rules (ink band >= 5 px); the
    # four bottom-most of those are the four Notes-row bottoms (metric-row rules are
    # single-weight, ~3-4 px, and are correctly excluded).
    thick = [(a + b) // 2 for a, b, t in bands if t >= 5]
    separators = sorted(thick)[-4:]                      # bottom-most four
    rows = []
    for sep in separators:
        above = [c for c in centers if c < sep - 30]
        rows.append((max(above), sep))
    return rows


def _functions_for_discipline(discipline: str) -> list[dict]:
    return config.functions_by_category().get(discipline, [])


def build() -> dict:
    desk_by_fn: dict[str, list[dict]] = {}
    for m in config.desktop_metrics():
        desk_by_fn.setdefault(m["functionId"], []).append(m)

    pages, desktop_index = [], []
    for order, discipline in enumerate(PAGE_DISCIPLINES, start=1):
        fname = f"Page{order}.jpg"
        raw = (FORM / fname).read_bytes()
        gray = np.asarray(Image.open(FORM / fname).convert("L"))
        assert gray.shape == (H, W), f"{fname} is {gray.shape}, expected {(H, W)}"
        notes = _notes_rows(gray)
        fns = _functions_for_discipline(discipline)
        assert len(fns) == 4, f"{discipline} has {len(fns)} functions, expected 4"

        notes_rows = []
        for f, (top, bot) in zip(fns, notes):
            notes_rows.append({
                "functionId": f["id"],
                "function": f["name"],
                "rect": [NOTES_X0, top, NOTES_X1 - NOTES_X0, bot - top],
            })
            for mi, m in enumerate(desk_by_fn.get(f["id"], [])):
                ds = m.get("desktopSource") or {}
                desktop_index.append({
                    "metricId": m["metricId"], "page": order, "discipline": discipline,
                    "functionId": f["id"], "function": f["name"], "metric": m["name"],
                    "method": ds.get("label") or "", "order": len(desktop_index) + 1,
                })
        pages.append({
            "filename": fname, "order": order, "discipline": discipline,
            "width": W, "height": H, "dpi": DPI,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "notes_rows": notes_rows,
        })

    return {
        "version": 1,
        "note": ("SFARI Field Worksheet v1.0 overlay manifest. Rectangles are in "
                 "original 1700x2200 px coords (origin top-left); multiply by pdf_scale "
                 "(0.36) for US Letter points, and flip y (pdf_y = (height - y - h) * "
                 "scale). See scripts/build_fieldform_manifest.py for how each rect was "
                 "measured and its confidence. Per-function Notes rects are the real "
                 "ruled 'Notes/Other Metrics' cells (detected from the raster gridlines)."),
        "page_px": [W, H], "dpi": DPI, "pdf_scale": PDF_SCALE,
        "columns_px": COL,
        "notes_cell_x_px": [NOTES_X0, NOTES_X1],
        "metadata_rects_px": METADATA_RECTS,
        "metadata_baseline_px": METADATA_BASELINE_Y,
        "footer_mask_px": FOOTER_MASK,
        "pages": pages,
        "desktop_metrics": desktop_index,
    }


def main():
    manifest = build()
    out = FORM / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    n_fns = sum(len(p["notes_rows"]) for p in manifest["pages"])
    print(f"wrote {out}")
    print(f"  pages: {len(manifest['pages'])}  functions mapped: {n_fns}  "
          f"desktop metrics: {len(manifest['desktop_metrics'])}")
    for p in manifest["pages"]:
        rects = ", ".join(f"{nr['functionId']}={nr['rect']}" for nr in p["notes_rows"])
        print(f"  P{p['order']} {p['discipline']}: {rects}")


if __name__ == "__main__":
    main()
