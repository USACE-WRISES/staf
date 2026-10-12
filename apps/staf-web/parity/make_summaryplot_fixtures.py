"""Reference renders of EASI's report summary plot (easi/report.py _summary_plots_png, matplotlib) for the
web app's tests.

The static app draws the same figure on a canvas (src/tools/easi/summaryplot.ts: matplotlib's grid of two
axes, its tick locator and formatter, its tight layout and DejaVu Sans). For reports with every kind of
score (missing ones, 0, 15, every band) this writes ``tests/fixtures/summaryplot_ref.json``: each render's
inputs and its layout as matplotlib drew it at 150 dpi (both axes, the ticks with their labels, the
titles, the axis labels and every text's box, in display pixels from the bottom left of the 1080 x 840
figure), and the PNG of a few.

    .venv\\Scripts\\python apps/staf-web/parity/make_summaryplot_fixtures.py
"""
from __future__ import annotations

import base64
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]
OUT = HERE / "tests" / "fixtures" / "summaryplot_ref.json"
PNGS = 1


def main() -> int:
    sys.path.insert(0, str(REPO / "apps" / "easi"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.figure
    from easi import config, report

    captured = {}
    save = matplotlib.figure.Figure.savefig

    def spy(fig, fname, *a, **k):
        save(fig, fname, *a, **k)
        fig.set_dpi(k.get("dpi", 150))
        fig.canvas.draw()
        r = fig.canvas.get_renderer()

        def box(artist):
            b = artist.get_window_extent(r)
            return [float(b.x0), float(b.y0), float(b.x1), float(b.y1)]

        def ticks(axis, labels, lo, hi):
            out = []
            for loc, lab in zip(axis.get_majorticklocs(), labels):
                if min(lo, hi) - 1e-9 <= loc <= max(lo, hi) + 1e-9 and lab.get_visible():
                    out.append([float(loc), lab.get_text(), box(lab), lab.get_fontweight() if isinstance(lab.get_fontweight(), str) else str(lab.get_fontweight())])
            return out

        axes = []
        for ax in fig.axes:
            xlo, xhi = ax.get_xlim()
            ylo, yhi = ax.get_ylim()
            axes.append({
                "axes": [float(v) for v in ax.get_window_extent(r).extents], "xlim": [float(xlo), float(xhi)], "ylim": [float(ylo), float(yhi)],
                "xticks": ticks(ax.xaxis, ax.get_xticklabels(), xlo, xhi), "yticks": ticks(ax.yaxis, ax.get_yticklabels(), ylo, yhi),
                "title": box(ax.title), "xlabel": box(ax.xaxis.label),
                "texts": [[t.get_text(), box(t)] for t in ax.texts],
                "bars": [[float(p.get_x()), float(p.get_y()), float(p.get_width()), float(p.get_height()), [float(v) for v in p.get_window_extent(r).extents]] for p in ax.patches],
            })
        captured["layout"] = axes

    matplotlib.figure.Figure.savefig = spy
    fns = config.functions()
    rng = random.Random(11)
    reps = []
    # every score missing; every score present at each band edge; a random mix
    reps.append({"functionScores": {}, "subIndices": {}, "ecosystemConditionIndex": None})
    reps.append({"functionScores": dict((f["id"], (0, 5, 6, 10, 11, 15)[i % 6]) for i, f in enumerate(fns)),
                 "subIndices": {"physical": 0.39, "chemical": 0.4, "biological": 0.69}, "ecosystemConditionIndex": 0.7})
    reps.append({"functionScores": dict((f["id"], 15) for f in fns),
                 "subIndices": {"physical": 1.0, "chemical": 1.0, "biological": 1.0}, "ecosystemConditionIndex": 1.0})
    for _ in range(9):
        scores = {}
        for f in fns:
            if rng.random() < 0.8:
                scores[f["id"]] = rng.randint(0, 15)
        sub = dict((k, round(rng.random(), 2)) for k in ("physical", "chemical", "biological") if rng.random() < 0.85)
        eci = round(rng.random(), 2) if rng.random() < 0.85 else None
        reps.append({"functionScores": scores, "subIndices": sub, "ecosystemConditionIndex": eci})
    renders = []
    for i, rep in enumerate(reps):
        png = report._summary_plots_png(rep)
        entry = {"rep": rep, "layout": captured["layout"]}
        if i < PNGS:
            entry["png"] = base64.b64encode(png).decode("ascii")
        renders.append(entry)
    out = {"source": f"matplotlib {matplotlib.__version__}", "functions": [[f["id"], f["name"], f["category"]] for f in fns],
           "renders": renders}
    OUT.write_text(json.dumps(out) + "\n", encoding="utf-8", newline="\n")
    print(f"{OUT}: {len(renders)} renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
