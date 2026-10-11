"""Reference desktop evidence for SFARI's Assessment step, from SFARI's own pull (sfari/evidence.py).

For the clicks of ``make_delineate_fixtures.py`` this writes (``tests/fixtures/sfari_ref.json``) what
the Shiny SFARI's pull returns from the local bundle, no services:

- ``ci``: the pull's inputs (SFARI's delineation ``ctx_inputs`` with the site's StreamCat anchor, the
  engine record left out);
- ``state``: the site engine's state the app passes (the record of ``compute_site`` with SFARI's
  families less the cross-sections, which read live 3DEP in the Python);
- ``prefetched``: what the pull looked up (NID dams, WQP medians, the NWIS gage, NWI along the reach,
  the StreamCat row), so a difference names its source;
- ``evidence``: the 22 entries.

Each site is pulled with the engine ok, then failed (StreamCat stands in), and Mink Brook once more
with cross-section values put in the record (the adapters that read them). Run with the repo's .venv:

    .venv\\Scripts\\python apps/staf-web/parity/make_sfari_fixtures.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parents[1]
CASES = json.loads((HERE / "tests" / "fixtures" / "delineate_ref.json").read_text(encoding="utf-8"))["cases"]
OUT = HERE / "tests" / "fixtures" / "sfari_ref.json"
FAMILIES = ["baseflow", "dams", "landcover", "roads", "runoff", "soils"]
XS = {"entrenchmentRatio": 2.314, "bankHeightRatio": 1.12, "bankfullWidthM": 8.43}


def main() -> int:
    os.environ["STAF_DATA_SOURCE"] = "bundle"
    os.environ["STAF_DATA_BUNDLE"] = str(REPO / "apps" / "hr-data" / "data")
    sys.path.insert(0, str(REPO / "apps" / "sfari"))
    from sfari import engine_prefill, evidence, pipeline
    from sfari._vendor.site_engine import anchor as anchor_mod
    from sfari._vendor.site_engine.engine import compute_site
    from sfari._vendor.site_engine.provenance import INTERACTIVE_CONFIG
    from sfari.datasources import nwi, streamcat

    # no services: the pull reads the bundle only
    anchor_mod._request_snap = lambda *a, **k: ({"error": "offline (fixture)"}, False)
    nwi.wetlands_near = lambda *a, **k: None

    def _offline(*a, **k):
        raise streamcat._Unanswered
    streamcat._fetch = _offline

    seen: dict = {}
    real = dict((name, getattr(mod, name)) for mod, name in ((evidence.nid_barriers, "barriers_near"), (evidence.wqp, "median_value"),
                                                              (evidence.nwis, "flow_stats"), (evidence.nwi, "wetlands_for_reach"),
                                                              (evidence.streamcat, "metrics_by_comid")))

    def spy(key, fn):
        def run(*a, **k):
            out = fn(*a, **k)
            seen[key if key != "median_value" else a[0]] = out
            return out
        return run
    evidence.nid_barriers.barriers_near = spy("nid", real["barriers_near"])
    evidence.wqp.median_value = spy("median_value", real["median_value"])
    evidence.nwis.flow_stats = spy("flow", real["flow_stats"])
    evidence.nwi.wetlands_for_reach = spy("nwi", real["wetlands_for_reach"])
    evidence.streamcat.metrics_by_comid = spy("streamcat", real["metrics_by_comid"])

    out = {"families": FAMILIES, "cases": []}
    for case in CASES:
        lat, lon = case["lat"], case["lon"]
        print(case["name"], "...", flush=True)
        rec = compute_site(lat, lon, {**INTERACTIVE_CONFIG, "includeGeometry": True, "metricFamilies": FAMILIES,
                                      "landcoverBaseline": True})
        site = rec["site"]
        hr_hit = (site["snapLat"], site["snapLon"], 0.0, site["nhdplusId"])
        v2_hit = tuple(case["v2Hit"]) if case.get("v2Hit") else None
        res = anchor_mod.classify_click(lat, lon, v2_hit=v2_hit, hr_hit=hr_hit, snap_tol_ft=150.0)
        anchor = res.get("anchor")
        d = pipeline.delineate_from_engine(rec, lat, lon, 1000.0)
        ci = dict(d["ctx_inputs"])
        ci.pop("site_engine", None)
        ci.pop("watershed_geojson", None)
        ci["siteAnchor"] = anchor
        ci["comid"] = (anchor or {}).get("scoredReach", {}).get("comid") if anchor else None
        record = engine_prefill.strip_geometry(rec)
        states = [("ok", {"status": "ok", "reason": None, "record": record}),
                  ("failed", {"status": "failed", "reason": "upstream tree query failed", "record": None})]
        if case["name"] == "mink-covered":
            xs = copy.deepcopy(record)
            for k, v in XS.items():
                xs["metrics"][k] = {"value": v}
            states.append(("ok-xs", {"status": "ok", "reason": None, "record": xs}))
        for label, state in states:
            seen.clear()
            got = asyncio.run(evidence.pull(ci, engine=state))
            out["cases"].append({"name": f"{case['name']}:{label}", "ci": ci, "state": state,
                                 "prefetched": {"nid": seen.get("nid"), "tn": seen.get("tn"), "tp": seen.get("tp"),
                                                "flow": seen.get("flow"), "nwi": seen.get("nwi"),
                                                "streamcat": seen.get("streamcat") or {}},
                                 "evidence": got})
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"{OUT}: {len(out['cases'])} cases, {OUT.stat().st_size / 1e3:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
