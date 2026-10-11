"""Reference stream displays for the web app's tests, from the Python the TypeScript ports.

The Shiny tools draw the streams of a view by ``network_display.fetch_streams`` (EASI's, which SFARI
and DEEP copy): the NHDPlus HR lines of the fetch box split by the click rule against the NHDPlus V2
lines (150 ft in EPSG:5070), from the STAF data bundle. For each case this writes the inputs (the
fetch box, the HR and V2 feature collections the bundle gave) and the display (``mode``,
``covered``, ``uncovered``) to ``tests/fixtures/streams_ref.json``.

Run from the repo with its .venv; it reads the local bundle (``apps/hr-data/data``, the folder the
browser layout mirrors), as a deployed tool reads the published one:

    .venv\\Scripts\\python apps/staf-web/parity/make_stream_fixtures.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "streams_ref.json"
#: (name, lat, lon, zoom): Mink Brook (the parity panel's site), Utoy Creek (dense, urban), a view
#: across two HR regions (0108 / 0202 near the Connecticut-New York line)
CASES = (("mink-brook", 43.6858, -72.2367, 16), ("utoy-creek", 33.7258, -84.5047, 16),
         ("region-edge", 41.2, -73.53, 15))


def main() -> int:
    os.environ["STAF_DATA_SOURCE"] = "bundle"
    os.environ["STAF_DATA_BUNDLE"] = str(REPO / "apps" / "hr-data" / "data")
    sys.path.insert(0, str(REPO / "apps" / "easi"))
    from easi import network_display, viewport
    out = {"tol_ft": 150.0, "cases": []}
    for name, lat, lon, zoom in CASES:
        bbox = viewport.fetch_box(lat, lon, zoom)
        res = network_display.fetch_streams(bbox, tol_ft=150.0, fetch_v2=None,
                                            fetch_hr=network_display._hr_for_display)
        out["cases"].append({"name": name, "lat": lat, "lon": lon, "zoom": zoom, "bbox": list(bbox),
                             "hr": res["hr"], "v2": res["v2"], "mode": res["mode"],
                             "covered": res["covered"], "uncovered": res["uncovered"]})
        print(f"{name}: {res['mode']}, hr {len((res['hr'] or {}).get('features') or [])}, "
              f"v2 {len((res['v2'] or {}).get('features') or [])}, covered {len(res['covered']['features'])}, "
              f"uncovered {len(res['uncovered']['features'])}")
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8", newline="\n")
    print(f"{OUT}: {OUT.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
