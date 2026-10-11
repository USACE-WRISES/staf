"""Reference delineations for the web app's tests, from the Python the TypeScript ports.

For a few stream clicks this writes what Delineate computes in each Shiny tool
(``tests/fixtures/delineate_ref.json``), from the local bundle alone:

- ``engine``: the STAF site engine's layer A at the click (SFARI and DEEP for every site, EASI for a
  routed one): the site block (``context.site_context``), the HR reach watershed
  (``delineate.delineate_watershed`` with the interactive budget, the walk's ids from
  ``bundle.watershed``) and the assessment reach (``reach.derive_reach``);
- ``v2``: EASI's covered-reach delineation (``delineation.run_delineation`` with the reach's COMID and
  snap): the V2 basin, its planar area, the V2 reach and the flowline attributes;
- ``display`` and ``bounds``: what the map draws (``delineation.display_simplify``,
  ``geojson_bounds``);
- ``easi``: EASI's whole Delineate result (``pipeline.delineate_only`` with the pick's COMID and anchor,
  the engine's metrics left out), and ``sfari``: SFARI's (``pipeline.delineate_from_engine`` on the
  engine's record without metric families).

Run with the repo's .venv; it reads the local bundle (``apps/hr-data/data``), no services:

    .venv\\Scripts\\python apps/staf-web/parity/make_delineate_fixtures.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parents[1]
OUT = HERE / "tests" / "fixtures" / "delineate_ref.json"
REACH_FT = 1000.0

# clicks on streams: Mink Brook (the capture site, a covered reach), an unnamed tributary of it (routed),
# Utoy Creek (Atlanta), the Mill River by the 0110/0202 region edge, a headwater above Mink Brook
CASES = [
    ("mink-covered", 43.689098, -72.255635),
    ("mink-routed", 43.698188, -72.220128),
    ("utoy", 33.73595, -84.51888),
    ("region-edge", 41.21511, -73.55128),
    ("headwater", 43.68404, -72.24059),
]


def main() -> int:
    os.environ["STAF_DATA_SOURCE"] = "bundle"
    os.environ["STAF_DATA_BUNDLE"] = str(REPO / "apps" / "hr-data" / "data")
    sys.path.insert(0, str(REPO / "apps" / "easi"))

    import asyncio

    from easi import delineation, pipeline, routing
    from easi._vendor.site_engine import bundle, context, delineate, provenance
    from easi._vendor.site_engine import reach as reach_mod
    from easi.datasources import flowlines

    import easi._vendor.site_engine.anchor as anchor_mod
    anchor_mod._request_snap = lambda *a, **k: ({"error": "offline (fixture)"}, False)
    budget = provenance.INTERACTIVE_CONFIG

    def engine(lat, lon):
        ctx = context.site_context(lat, lon, snap_tol_ft=budget["snapTolFt"])
        if ctx["status"] != "ok":
            return {"status": ctx["status"], "reason": ctx["reason"]}
        anchor = ctx["anchor"]
        site = ctx["site"]
        ws = delineate.delineate_watershed(anchor, max_hops=budget["maxHops"], max_reaches=budget["maxReaches"])
        tree = ws.pop("treeFlowlines", [])
        walked = bundle.watershed(int(anchor["nhdplusid"]), max_hops=budget["maxHops"],
                                  max_reaches=budget["maxReaches"]) or {}
        out = {"input": [lat, lon], "site": site,
               "anchor": {k: v for k, v in anchor.items() if k != "geometry"},
               "watershed": ws, "ids": walked.get("ids"), "nCatchments": walked.get("nCatchments"),
               "treeFlowlines": len(tree)}
        if ws["status"] == "ok":
            gj, ft, warns = reach_mod.derive_reach(anchor, site["snapLat"], site["snapLon"], REACH_FT)
            out["reach"] = {"lengthFt": ft, "geometry": gj, "warnings": warns}
            out["display"] = delineation.display_simplify(ws["polygon"])
            out["bounds"] = delineation.geojson_bounds(ws["polygon"], gj)
        return out

    def v2(lat, lon, comid, snap_lat, snap_lon):
        d = delineation.run_delineation(lat, lon, REACH_FT, comid=comid, snapped_lat=snap_lat,
                                        snapped_lon=snap_lon)
        raw = bundle.v2_watershed(int(comid)) or {}
        return {"input": [lat, lon, comid, snap_lat, snap_lon],
                "attrs": {k: getattr(d, k) for k in ("comid", "gnis_name", "drainage_area_sqkm", "huc8", "slope",
                                                     "fcode", "stream_order", "sinuosity", "erom")},
                "watershed": d.watershed_geojson, "watershedAreaSqkm": d.watershed_area_sqkm,
                "nReaches": raw.get("nReaches"), "bundleAreaSqkm": raw.get("areaSqkm"),
                "reach": d.reach_geojson, "reachLengthFt": d.reach_length_ft, "warnings": d.warnings,
                "display": delineation.display_simplify(d.watershed_geojson),
                "bounds": delineation.geojson_bounds(d.watershed_geojson, d.reach_geojson)}

    sys.path.insert(0, str(REPO / "apps" / "sfari"))
    from sfari import pipeline as sfari_pipeline
    from easi._vendor.site_engine.engine import compute_site

    def no_metrics(res):
        rec = ((res.get("ctx_inputs") or {}).get("watershedEngine") or {}).get("record")
        if rec:
            rec["metrics"] = {}
            rec.pop("hrSource", None)
        return res

    out = {"reachFt": REACH_FT, "cases": []}
    for name, lat, lon in CASES:
        probe = 0.012
        recs = bundle.v2_lines_in_box(lon - probe, lat - probe, lon + probe, lat + probe) or []
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"comid": r["nhdplusid"]}, "geometry": r["geometry"]} for r in recs]}
        v2_hit = flowlines.nearest_point_on_lines(fc, lat, lon) if recs else None
        case = {"name": name, "lat": lat, "lon": lon, "v2Hit": list(v2_hit) if v2_hit else None}
        print(name, "engine ...", flush=True)
        case["engine"] = engine(lat, lon)
        if v2_hit and v2_hit[2] <= 150.0:
            print(name, "v2 ...", flush=True)
            case["v2"] = v2(lat, lon, int(v2_hit[3]), v2_hit[0], v2_hit[1])
        else:
            site = case["engine"].get("site") or {}
            hit = (site.get("snapLat"), site.get("snapLon"), site.get("snapDistFt"), site.get("nhdplusId"))
            if hit[3] is not None:
                routed = routing.route_from_hr(lat, lon, hit)
                case["route"] = routed
                scored = (routed.get("anchor") or {}).get("scoredReach") or {}
                if scored.get("comid") is not None:
                    print(name, "routed v2 ...", flush=True)
                    case["v2"] = v2(lat, lon, int(scored["comid"]), scored["snapLat"], scored["snapLon"])
                    anchor = dict(routed["anchor"])
                    case["reanchor"] = routing.reanchor_inputs(anchor, REACH_FT)
        if case.get("v2"):
            if case.get("route"):
                anchor = json.loads(json.dumps(case["route"]["anchor"]))
                clicked = anchor["clickedStream"]
                pin = (clicked["snapLat"], clicked["snapLon"])
            else:
                anchor = routing.v2_anchor(int(v2_hit[3]), lat, lon, v2_hit[0], v2_hit[1], round(v2_hit[2], 1))
                pin = (v2_hit[0], v2_hit[1])
            comid = int(anchor["scoredReach"]["comid"])
            anchor_in = json.loads(json.dumps(anchor))
            print(name, "easi delineate_only ...", flush=True)
            res = asyncio.run(pipeline.delineate_only(pin[0], pin[1], REACH_FT, comid=comid, anchor=anchor))
            case["easi"] = {"input": [pin[0], pin[1], comid], "anchor": anchor_in, "result": no_metrics(res)}
        rec = compute_site(lat, lon, {**budget, "includeGeometry": True, "metricFamilies": []})
        rec.pop("hrSource", None)
        case["sfari"] = {"record": rec, "result": sfari_pipeline.delineate_from_engine(rec, lat, lon, REACH_FT)}
        out["cases"].append(case)
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8", newline="\n")
    print(f"{OUT}: {len(out['cases'])} cases, {OUT.stat().st_size / 1e6:.2f} MB")
    for c in out["cases"]:
        e = c["engine"]
        w = e.get("watershed") or {}
        print(f"  {c['name']}: engine {w.get('status')} {w.get('nReaches')} reaches {w.get('nHops')} hops "
              f"{w.get('areaSqkm')} km2; v2 {'yes' if c.get('v2') else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
