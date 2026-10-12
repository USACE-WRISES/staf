"""Goldens for DEEP's report and metrics PDFs in the static app (tests/deep-pdf.test.ts): DEEP's own
report.build_pdf and build_metrics_pdf on library bundles with desk and field values, a delineated site
with a watershed and a reach, a region and a scenario.

The report is built with reportlab's invariant mode switched on (DEEP builds it with the download's
date) and the basemap fetch replaced: one case draws a fixed palette PNG (``basemap.png``, the format the
USGS export returns), one has none (the outline, a frame and the "unavailable" caption). Each case writes
``tests/fixtures/deep_pdf/<case>.json`` (inputs and the scored result) and ``<case>.report.pdf`` and
``<case>.metrics.pdf``.

    .venv\\Scripts\\python apps/staf-web/parity/make_deep_pdf_fixtures.py
"""
from __future__ import annotations

import io
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "apps" / "deep"))
sys.path.insert(0, str(HERE))

from reportlab import rl_config  # noqa: E402

from deep import assessments, curves, measure, report, reportmap  # noqa: E402
import make_deep_fieldform_fixtures as ff  # noqa: E402

OUT = HERE.parent / "tests" / "fixtures" / "deep_pdf"


def basemap_png() -> bytes:
    """A 580 x 360 palette PNG with an sRGB chunk, as the USGS export answers."""
    from PIL import Image
    im = Image.new("P", (580, 360))
    im.putpalette([v for i in range(256) for v in ((i * 7) % 256, (i * 13) % 256, (255 - i) % 256)])
    px = im.load()
    for y in range(360):
        for x in range(580):
            px[x, y] = (x // 9 + y // 7) % 256
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def site(base: dict) -> dict:
    """The case's delineation with a watershed polygon (a wobbly ring of 60 points) and a reach line."""
    d = json.loads(json.dumps(base))
    lat0, lon0 = 43.6858, -72.2367
    ring = []
    for i in range(60):
        a = 2 * math.pi * i / 60
        r = 0.012 * (1 + 0.25 * math.sin(3 * a) + 0.1 * math.cos(7 * a))
        ring.append([round(lon0 + 1.4 * r * math.cos(a), 6), round(lat0 + r * math.sin(a), 6)])
    ring.append(ring[0])
    d["watershed_geojson"] = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
    d["reach_geojson"] = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [
            [lon0 - 0.004, lat0 - 0.002], [lon0 - 0.001, lat0 - 0.0005], [lon0, lat0], [lon0 + 0.002, lat0 + 0.0015]]}}]}
    d["delineation"]["watershed_area_sqkm"] = 11.98
    d["delineation"]["huc8"] = "01080104"
    return d


REGION = {"level3": {"code": "58", "name": "Northeastern Highlands"}, "state": {"code": "50", "abbr": "VT", "name": "Vermont"}}

CASES = [
    # name, fieldform case, basemap, scenario, region
    ("wgcp", "wgcp", True, ("Culvert removed", "The road crossing replaced by a span."), REGION),
    ("ecbp", "ecbp", False, None, None),
    ("neh", "neh", True, ("Existing Conditions", ""), {"level3": {"code": "58", "name": "Northeastern Highlands"}, "state": None}),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    png = basemap_png()
    (OUT / "basemap.png").write_bytes(png)
    rl_config.invariant = 1
    names = []
    for name, src, with_map, scenario, region in CASES:
        case = json.loads((ff.OUT / f"{src}.json").read_text(encoding="utf-8"))
        bundle = json.loads((REPO / case["bundle"]).read_text(encoding="utf-8"))
        la = assessments.LoadedAssessment.from_dict(bundle)
        measured = case["measured"]
        delin = site(case["delineation"] or ff.SITE)
        sc, _fr = curves.score_site(la, measure.measured_from_state(measured))
        reportmap.topo_png = (lambda *a, **k: png) if with_map else (lambda *a, **k: None)
        pdf = report.build_pdf(delin, la, measured, sc, region=region, scenario=scenario)
        metrics = report.build_metrics_pdf(la, ref=case["ref"], measured=measured, delineation=delin)
        (OUT / f"{name}.report.pdf").write_bytes(pdf)
        (OUT / f"{name}.metrics.pdf").write_bytes(metrics)
        rec = {"name": name, "bundle": case["bundle"], "ref": case["ref"], "measured": measured, "delineation": delin, "scored": sc,
               "region": region, "scenario": list(scenario) if scenario else None, "basemap": with_map}
        (OUT / f"{name}.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8", newline="\n")
        names.append(name)
        print(f"{name}: report {len(pdf)} bytes, metrics {len(metrics)} bytes")
    (OUT / "index.json").write_text(json.dumps({"cases": names}, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
