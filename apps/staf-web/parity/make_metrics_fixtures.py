"""Reference metric families for the web app's tests, from the STAF site engine the TypeScript ports.

For the clicks of ``make_delineate_fixtures.py`` this writes (``tests/fixtures/metrics_ref.json``):

- ``values``: the bundle's watershed values for the anchored reach (``_hrslim.values.watershed_values``
  under the interactive budget): land cover of the watershed and its riparian strip, roads, crossings,
  dams, soils;
- ``metrics``: ``compute_site``'s metric entries with the families that need no live service (base
  flow, dams, land cover with the 2001 baseline, roads, runoff, soils), as SFARI asks for them less its
  cross-sections (live 3DEP in the Python; the web app reads the published grid instead).

Run with the repo's .venv; it reads the local bundle (``apps/hr-data/data``), no services:

    .venv\\Scripts\\python apps/staf-web/parity/make_metrics_fixtures.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parents[1]
CASES = json.loads((HERE / "tests" / "fixtures" / "delineate_ref.json").read_text(encoding="utf-8"))["cases"]
OUT = HERE / "tests" / "fixtures" / "metrics_ref.json"
FAMILIES = ["baseflow", "dams", "landcover", "roads", "runoff", "soils"]


def main() -> int:
    os.environ["STAF_DATA_SOURCE"] = "bundle"
    os.environ["STAF_DATA_BUNDLE"] = str(REPO / "apps" / "hr-data" / "data")
    sys.path.insert(0, str(REPO / "apps" / "easi"))
    from easi._vendor.site_engine import bundle, provenance
    from easi._vendor.site_engine._hrslim.values import watershed_values
    from easi._vendor.site_engine.engine import compute_site

    budget = provenance.INTERACTIVE_CONFIG
    out = {"families": FAMILIES, "cases": []}
    for case in CASES:
        lat, lon = case["lat"], case["lon"]
        nid = case["engine"]["site"]["nhdplusId"]
        print(case["name"], "...", flush=True)
        vals = watershed_values(bundle.dataset(), bundle.values(), int(nid),
                                max_reaches=budget["maxReaches"], max_hops=budget["maxHops"])
        rec = compute_site(lat, lon, {**budget, "includeGeometry": False, "metricFamilies": FAMILIES,
                                      "landcoverBaseline": True})
        out["cases"].append({"name": case["name"], "lat": lat, "lon": lon, "nhdplusId": nid,
                             "values": vals, "status": rec["status"], "metrics": rec["metrics"],
                             "warnings": rec.get("warnings")})
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8", newline="\n")
    print(f"{OUT}: {len(out['cases'])} cases, {OUT.stat().st_size / 1e3:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
