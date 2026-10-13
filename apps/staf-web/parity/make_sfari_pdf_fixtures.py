"""Goldens for SFARI's report and desktop metrics PDFs in the static app (tests/sfari-pdf.test.ts): SFARI's
own report.build_pdf and build_desktop_metrics_pdf on a saved SFARI assessment (Mink Brook, opened as the
app opens it), in reportlab's invariant mode with the basemap fetch replaced.

Cases: the saved file as it is (its watershed, the fixed basemap PNG); the same with an alternative
scenario named and described, two PNG photos and a borrowed evidence mark; the same with no geometry and
no basemap; the same with JPEG photos (from tests/fixtures/jpeg: Edge's canvas encoder, gray, CMYK,
progressive), which reportlab names after the samples Pillow decodes. Each writes ``tests/fixtures/sfari_pdf/<case>.json`` (the state the builders read: delineation,
metric and function scores, evidence, the scored rollup, the scenario) and ``<case>.report.pdf`` and
``<case>.metrics.pdf``.

    .venv\\Scripts\\python apps/staf-web/parity/make_sfari_pdf_fixtures.py SAVED.json
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "apps" / "sfari"))
sys.path.insert(0, str(HERE))

from reportlab import rl_config  # noqa: E402

from sfari import report, reportmap, scoring, session  # noqa: E402
import make_deep_pdf_fixtures as dp  # noqa: E402

OUT = HERE.parent / "tests" / "fixtures" / "sfari_pdf"


def png_photo(w: int, h: int, seed: int) -> str:
    from PIL import Image
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 3 + seed) % 256, (y * 5 + seed * 7) % 256, ((x + y) * seed) % 256)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def jpeg_photo(name: str) -> str:
    data = (HERE.parent / "tests" / "fixtures" / "jpeg" / f"{name}.jpg").read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


#: the JPEG photos of the "photos" case, two metrics' worth
JPEGS = [["edge-photo-300", "pil-photo-gray"], ["pil-photo-cmyk", "pil-photo-s2-prog"]]


def main(saved: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    png = dp.basemap_png()
    (OUT / "basemap.png").write_bytes(png)
    rl_config.invariant = 1
    st = session.load(Path(saved).read_text(encoding="utf-8"))
    state = st["scenarios"]["items"][0]["state"]
    base = {"delin": st["delineation"], "metric_scores": state.get("metric_scores") or {},
            "function_scores": state.get("function_scores") or {}, "evidence": st["evidence"]}
    second = json.loads(json.dumps(base))
    mids = list(second["metric_scores"])
    second["metric_scores"].setdefault(mids[0], {})["photos"] = [{"id": "p1", "uri": png_photo(120, 80, 3)},
                                                                 {"id": "p2", "uri": png_photo(64, 140, 9)}]
    ev = next(e for e in second["evidence"].values() if e.get("status") == "ok" and e.get("value_text"))
    ev.update(origin="streamcat", anchor_label="Mink Brook (COMID 9327042), 446 ft downstream",
              fallback_reason="The HR watershed was not available.")
    third = json.loads(json.dumps(base))
    for k in ("watershed_geojson", "reach_geojson"):
        third["delin"][k] = None
    fourth = json.loads(json.dumps(base))
    for mid, files in zip(list(fourth["metric_scores"])[:2], JPEGS):
        fourth["metric_scores"][mid]["photos"] = [{"id": f, "uri": jpeg_photo(f)} for f in files]
    cases = [("saved", base, None, True), ("scenario", second, ("Restored riffle", "Banks graded & replanted <2 yr>."), True),
             ("nomap", third, None, False), ("photos", fourth, None, True)]
    names = []
    for name, c, scenario, with_map in cases:
        sc = scoring.score_assessment({fid: v.get("score") for fid, v in c["function_scores"].items()})
        reportmap.topo_png = (lambda *a, **k: png) if with_map else (lambda *a, **k: None)
        pdf = report.build_pdf(c["delin"], c["metric_scores"], c["function_scores"], c["evidence"], sc, scenario=scenario)
        metrics = report.build_desktop_metrics_pdf(c["delin"], c["evidence"])
        (OUT / f"{name}.report.pdf").write_bytes(pdf)
        (OUT / f"{name}.metrics.pdf").write_bytes(metrics)
        rec = dict(c, scored=sc, scenario=list(scenario) if scenario else None, basemap=with_map)
        (OUT / f"{name}.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8", newline="\n")
        names.append(name)
        print(f"{name}: report {len(pdf)} bytes, metrics {len(metrics)} bytes")
    (OUT / "index.json").write_text(json.dumps({"cases": names}, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(sys.argv[1])
