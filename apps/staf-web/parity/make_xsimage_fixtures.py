"""Reference renders of EASI's report cross-section image (easi/xsplot.py, matplotlib) for the web app's tests.

The static app draws the same figure on a canvas (src/tools/easi/xsimage.ts: matplotlib's tick locator and
formatter, its tight layout and DejaVu Sans). For the profiles of ``tests/fixtures/easi_ref.json`` (the
grid's stored sections and the synthetic ones) at their own stages, edited ones, in feet and in metres,
this writes ``tests/fixtures/xsimage_ref.json``: each render's inputs and its layout as matplotlib drew it
(the axes box, the ticks with their labels, every text's box, in display pixels from the bottom left of the
845 x 279.5 figure), and the PNG of a few.

    .venv\\Scripts\\python apps/staf-web/parity/make_xsimage_fixtures.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]
REF = HERE / "tests" / "fixtures" / "easi_ref.json"
OUT = HERE / "tests" / "fixtures" / "xsimage_ref.json"
PNGS = 4


def main() -> int:
    sys.path.insert(0, str(REPO / "apps" / "easi"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.figure
    from easi import xsplot

    captured = {}
    save = matplotlib.figure.Figure.savefig

    def spy(fig, fname, *a, **k):
        save(fig, fname, *a, **k)
        # tight_layout ran at the figure's own 100 dpi and savefig drew it at 130: read it at 130
        fig.set_dpi(k.get("dpi", 130))
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        ax = fig.axes[0] if fig.axes else None
        if ax is None:
            captured["layout"] = None
            return

        def box(artist):
            b = artist.get_window_extent(r)
            return [b.x0, b.y0, b.x1, b.y1]

        def ticks(axis, labels, lo, hi):
            out = []
            for loc, lab in zip(axis.get_majorticklocs(), labels):
                if min(lo, hi) - 1e-9 <= loc <= max(lo, hi) + 1e-9 and lab.get_visible():
                    out.append([float(loc), lab.get_text(), box(lab)])
            return out
        xlo, xhi = ax.get_xlim()
        ylo, yhi = ax.get_ylim()
        captured["layout"] = {
            "axes": list(ax.get_window_extent(r).extents), "xlim": [xlo, xhi], "ylim": [ylo, yhi],
            "xticks": ticks(ax.xaxis, ax.get_xticklabels(), xlo, xhi), "yticks": ticks(ax.yaxis, ax.get_yticklabels(), ylo, yhi),
            "title": box(ax.title), "xlabel": box(ax.xaxis.label), "ylabel": box(ax.yaxis.label),
            "texts": [[t.get_text(), t.get_ha(), box(t)] for t in ax.texts],
            "figtexts": [[t.get_text(), box(t)] for t in fig.texts],
        }

    matplotlib.figure.Figure.savefig = spy
    ref = json.loads(REF.read_text(encoding="utf-8"))
    renders = []
    for g in ref["geomorph"]:
        s = g["summary"]
        prof = s["profile"]
        st, el = prof["stations"], prof["elevs"]
        th = s["thalweg"]
        bf, lb = s["bankfull_stage_m"], s["low_bank_stage_m"]
        variants = [
            dict(bf=bf, lb=lb, unit="ft", source="USGS 3DEP 1 m DEM"),
            dict(bf=bf, lb=lb, unit="m", source="USGS 3DEP 10 m DEM"),
            dict(bf=bf + 0.3, lb=lb + 0.5, unit="ft", source="USGS 3DEP 1 m DEM"),
            dict(bf=th + 0.9 * (bf - th), lb=bf, unit="ft", source=None),
        ]
        for v in variants:
            captured.clear()
            png = xsplot.cross_section_png(st, el, bankfull_stage=v["bf"], floodplain_stage=v["lb"], thalweg=th,
                                           unit=v["unit"], source=v["source"])
            item = {"stations": st, "elevs": el, "thalweg": th, "bankfull_stage": v["bf"], "floodplain_stage": v["lb"],
                    "unit": v["unit"], "source": v["source"], "layout": captured.get("layout")}
            if len(renders) < PNGS:
                item["png"] = base64.b64encode(png).decode("ascii")
            renders.append(item)
    out = {"matplotlib": matplotlib.__version__, "renders": renders}
    OUT.write_text(json.dumps(out) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(renders)} renders -> {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
