"""Reference snaps for the web app's tests, from the Python the TypeScript ports.

For click points around the stream views of ``streams_ref.json`` (``make_stream_fixtures.py``), this
writes what the Shiny tools compute when a stream is clicked (``tests/fixtures/snap_ref.json``):

- ``v2Hit`` and ``hrHit``: the nearest point on the view's V2 and HR lines, with its distance in feet
  and its line's id (EASI's ``flowlines.nearest_point_on_lines``, ``nhd_hr.nearest_point_on_hr_lines``);
- ``catchment``: the NHDPlus V2 catchment that contains the click (``bundle.v2_catchment_at``);
- ``raindrop``: the reach a raindrop from the HR snap reaches (``bundle.raindrop``) and that reach's
  attributes (``bundle.v2_flowline``);
- ``route``: EASI's anchor for a click on a stream outside the V2 network (``routing.route_from_hr``).

Run with the repo's .venv; it reads the local bundle (``apps/hr-data/data``), no services:

    .venv\\Scripts\\python apps/staf-web/parity/make_snap_fixtures.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parents[1]
STREAMS = HERE / "tests" / "fixtures" / "streams_ref.json"
OUT = HERE / "tests" / "fixtures" / "snap_ref.json"
ATTRS = ("gnis_name", "reachcode", "totdasqkm", "slope", "fcode", "stream_order", "lengthkm")


def main() -> int:
    os.environ["STAF_DATA_SOURCE"] = "bundle"
    os.environ["STAF_DATA_BUNDLE"] = str(REPO / "apps" / "hr-data" / "data")
    sys.path.insert(0, str(REPO / "apps" / "easi"))
    from pyproj import Transformer

    from easi import routing
    from easi._vendor.site_engine import bundle
    from easi.datasources import flowlines, nhd_hr

    # the engine's own NLDI fallback must never answer here: the bundle holds every point
    import easi._vendor.site_engine.anchor as anchor_mod
    anchor_mod._request_snap = lambda *a, **k: ({"error": "offline (fixture)"}, False)

    to_m = Transformer.from_crs(4326, 5070, always_xy=True)
    to_deg = Transformer.from_crs(5070, 4326, always_xy=True)
    rng = np.random.default_rng(11)
    cases = json.loads(STREAMS.read_text(encoding="utf-8"))["cases"]
    out = {"points": []}
    for case in cases:
        feats = case["hr"]["features"]
        for k in range(14):
            feat = feats[int(rng.integers(len(feats)))]
            coords = feat["geometry"]["coordinates"]
            line = coords if feat["geometry"]["type"] == "LineString" else coords[0]
            lon0, lat0 = line[int(rng.integers(len(line)))]
            x, y = to_m.transform(lon0, lat0)
            ang = rng.uniform(0, 2 * math.pi)
            off = rng.uniform(0, 260) / 3.28083989501312      # up to 260 ft off a vertex
            lon, lat = to_deg.transform(x + off * math.cos(ang), y + off * math.sin(ang))
            v2_hit = flowlines.nearest_point_on_lines(case["v2"], lat, lon)
            hr_hit = nhd_hr.nearest_point_on_hr_lines(case["hr"], lat, lon)
            rec = {"case": case["name"], "lat": lat, "lon": lon,
                   "v2Hit": list(v2_hit) if v2_hit else None, "hrHit": list(hr_hit) if hr_hit else None,
                   "catchment": bundle.v2_catchment_at(lat, lon)}
            if hr_hit:
                rain = bundle.raindrop(hr_hit[0], hr_hit[1])
                rec["raindrop"] = rain
                if rain and rain.get("comid") is not None:
                    v2 = bundle.v2_flowline(int(rain["comid"])) or {}
                    rec["raindropReach"] = dict((a, v2.get(a)) for a in ATTRS)
                if hr_hit[2] <= 150.0:
                    rec["route"] = routing.route_from_hr(lat, lon, tuple(hr_hit))
            out["points"].append(rec)
    OUT.write_text(json.dumps(out, indent=None, separators=(",", ":")), encoding="utf-8", newline="\n")
    n = len(out["points"])
    print(f"{OUT}: {n} points, {sum(1 for p in out['points'] if p.get('route'))} routed, "
          f"{OUT.stat().st_size / 1e3:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
