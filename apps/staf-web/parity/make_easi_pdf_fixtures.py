"""Goldens for EASI's report and desktop metrics PDFs in the static app (tests/easi-pdf.test.ts): EASI's own
report.build_pdf and build_desktop_metrics_pdf on saved EASI assessments, opened as the app opens them
(session.load, session.screened, scenario_state.scored_for, the notes on the rows: export_result).

reportlab runs in invariant mode; the pictures are pinned so the test compares the layout and text: the
summary plot and the cross-section are fixed RGBA PNGs (the kind matplotlib and the browser both write;
the static app draws its own, as matplotlib lays them out), and the basemap is the fixed palette PNG or
none. Each case writes ``tests/fixtures/easi_pdf/<case>.json`` (the export result, the scenario, the
calculator's entry cells) and ``<case>.report.pdf`` and ``<case>.metrics.pdf``.

    .venv\\Scripts\\python apps/staf-web/parity/make_easi_pdf_fixtures.py SAVED.json [MORE.json ...]
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "apps" / "easi"))
sys.path.insert(0, str(HERE))

from reportlab import rl_config  # noqa: E402

from easi import calculator, report, reportmap, scenario_state, session  # noqa: E402
import make_deep_pdf_fixtures as dp  # noqa: E402

OUT = HERE.parent / "tests" / "fixtures" / "easi_pdf"


def rgba_png(w: int, h: int, seed: int) -> bytes:
    from PIL import Image
    im = Image.new("RGBA", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x // 40 * 37 + seed) % 256, (y // 30 * 53) % 256, (x // 40 + y // 30) * seed % 256, 255)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def export_result(st: dict, scenario_index: int) -> tuple[dict, tuple | None]:
    screening = st["screening"]
    base = session.screened(st["delineation"], screening.get("huc12"), screening["report"])
    items = st["scenarios"]["items"]
    item = items[scenario_index]
    sc = scenario_state.scored_for(base["report"], item.get("state"))
    notes = st["notes"]
    rows = [{**r, "userNote": notes.get(r["metricId"], "")} for r in sc["metricRows"]]
    res = {**base, "report": {**sc, "metricRows": rows, "crossSection": base["report"].get("crossSection") or sc.get("crossSection")}}
    many = len(items) > 1
    scenario = (item["name"], item.get("description") or "") if (many or item.get("description")) else None
    return res, scenario


def main(paths: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    basemap = dp.basemap_png()
    summary = rgba_png(1080, 840, 5)
    xs = rgba_png(845, 279, 11)
    for name, data in (("basemap.png", basemap), ("summary.png", summary), ("xs.png", xs)):
        (OUT / name).write_bytes(data)
    rl_config.invariant = 1
    report._summary_plots_png = lambda rep: summary
    cells = calculator.entry_cells()
    names = []
    for k, path in enumerate(paths):
        st = session.load(Path(path).read_text(encoding="utf-8"))
        for i in range(len(st["scenarios"]["items"])):
            res, scenario = export_result(st, i)
            with_map = (k + i) % 2 == 0
            if res["report"].get("crossSection"):
                res["report"]["crossSection"] = {**res["report"]["crossSection"], "png_b64": base64.b64encode(xs).decode("ascii")}
            reportmap.topo_png = (lambda *a, **kw: basemap) if with_map else (lambda *a, **kw: None)
            pdf = report.build_pdf(res, scenario=scenario)
            metrics = report.build_desktop_metrics_pdf(res)
            name = f"f{k}s{i}"
            (OUT / f"{name}.report.pdf").write_bytes(pdf)
            (OUT / f"{name}.metrics.pdf").write_bytes(metrics)
            has_xs = bool((res["report"].get("crossSection") or {}).get("png_b64"))
            rec = {"name": name, "result": res, "scenario": list(scenario) if scenario else None, "basemap": with_map, "xs": has_xs, "cells": cells}
            (OUT / f"{name}.json").write_text(json.dumps(rec) + "\n", encoding="utf-8", newline="\n")
            names.append(name)
            print(f"{name}: {Path(path).name} scenario {i}: report {len(pdf)} bytes, metrics {len(metrics)} bytes")
    (OUT / "index.json").write_text(json.dumps({"cases": names}, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(sys.argv[1:])
