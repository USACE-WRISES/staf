"""Reference scoring, words, markup and desktop values for DEEP's Assessment step, from DEEP's own code.

Reads the assessment bundles the static app reads (the browser layout's ``library/``, the release's
files) and writes ``tests/fixtures/deep_ref.json``:

- ``words``: for every metric of every bundle, the line under its name for each curve set
  (``describe.describe``), how to measure it (``field_form.method_text``), its units and its curve
  sets' labels;
- ``scores``: for a sample of bundles, a few random measured states and their scores
  (``curves.score_site``: the rollup with the coverage interval, each function's metric indices,
  score and warnings);
- ``rows``: the worksheet's markup for a sample of metrics and states (``app._metric_row``,
  ``_scorecard``, ``_unassessed_panel``, ``_fn_footer``), as htmltools renders it;
- ``strata``: the curve sets the five delineate sites preselect, and why (``auto_strata``,
  ``stratifier_note``);
- ``computed``: the desktop prefill at the five sites for the assessments covering them
  (``measure.compute_metrics_only``, the engine record of the site; the cross-section metrics left
  out, which read live 3DEP in the Python).

Run with the repo's .venv (the local layout must be built):

    .venv\\Scripts\\python apps/staf-web/parity/make_deep_fixtures.py
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parents[1]
LAYOUT = Path(os.environ.get("STAF_WEB_DATA") or r"D:\Data\staf-web\site")
OUT = HERE / "tests" / "fixtures" / "deep_ref.json"
CASES = json.loads((HERE / "tests" / "fixtures" / "delineate_ref.json").read_text(encoding="utf-8"))["cases"]
FAMILIES = ["baseflow", "dams", "landcover", "roads", "runoff", "soils"]
XS_IDS = {"floodplain-connectivity-entrenchment-ratio-er", "channel-and-floodplain-dynamics-bank-height-ratio-bhr",
          "spring-bank-height-ratio", "channel-evolution-width-depth-ratio"}


def layout_file(logical: str) -> Path:
    m = json.loads((LAYOUT / "web-manifest.json").read_text(encoding="utf-8"))
    return LAYOUT / m["files"][logical]["path"]


def catalog() -> list:
    """``[(ref, logical bundle name, version, status)]`` for every version with a bundle."""
    doc = json.loads(layout_file("library/library.json").read_text(encoding="utf-8"))
    out = []
    for entry in doc.get("assessments") or []:
        for v in entry.get("versions") or []:
            name = ((v.get("assets") or {}).get("bundle") or {}).get("name")
            if name and entry.get("id"):
                out.append((f"{entry['id']}@v{v['version']}", f"library/{name}", int(v["version"]), str(v.get("status") or "")))
    return out


def load(logical: str, version: int, status: str, ref: str):
    from deep.assessments import LoadedAssessment
    bundle = json.loads(layout_file(logical).read_text(encoding="utf-8"))
    return LoadedAssessment.from_dict({**bundle, "version": version, "lifecycle": status, "assessmentRef": ref})


def state_for(la, rng: random.Random) -> dict:
    from deep import curves
    state = {}
    for fn in la.metrics_by_function:
        for m in fn.get("metrics") or []:
            mid = m["metricId"]
            r = rng.random()
            if r < 0.35:
                continue
            strata = curves.curve_strata(m)
            stratum = rng.choice(strata) if strata and rng.random() < 0.5 else None
            if r < 0.45:
                state[mid] = {"na": True, "stratum": stratum}
                continue
            pts = [p for p in curves.active_points(m, stratum) if p.get("x") is not None]
            xs = [float(p["x"]) for p in pts] or [0.0, 1.0]
            lo, hi = min(xs), max(xs)
            span = (hi - lo) or 1.0
            value = round(rng.uniform(lo - 0.2 * span, hi + 0.2 * span), 3)
            origin = "desktop" if rng.random() < 0.3 else "field"
            rec = {"value": value, "origin": origin, "engine": origin == "desktop" and rng.random() < 0.6}
            if stratum is not None:
                rec["stratum"] = stratum
                rec["stratumAuto"] = rng.random() < 0.5
            if rng.random() < 0.2:
                rec["note"] = "checked in the field"
            state[mid] = rec
    return state


def main() -> int:
    os.environ["STAF_DATA_SOURCE"] = "bundle"
    os.environ["STAF_DATA_BUNDLE"] = str(REPO / "apps" / "hr-data" / "data")
    sys.path.insert(0, str(REPO / "apps" / "deep"))
    from htmltools import TagList

    import app as deep_app
    from deep import curves, describe, field_form, measure, pipeline, reference_support
    from deep._vendor.site_engine import anchor as anchor_mod
    from deep._vendor.site_engine.engine import compute_site
    from deep._vendor.site_engine.provenance import INTERACTIVE_CONFIG
    from deep.datasources import streamcat
    from deep.metrics import computed

    anchor_mod._request_snap = lambda *a, **k: ({"error": "offline (fixture)"}, False)
    computed._reach_geom = lambda ctx: {}
    computed._landcover = lambda ctx: {}

    def _offline(*a, **k):
        raise streamcat._Unanswered
    streamcat._fetch = _offline

    html = lambda tag: TagList(tag).get_html_string()  # noqa: E731
    cat = catalog()
    out: dict = {"words": [], "scores": [], "rows": [], "strata": [], "delineations": {}, "computed": []}
    rng = random.Random(20261011)
    loaded = {}
    for ref, logical, version, status in cat:
        try:
            loaded[ref] = (logical, load(logical, version, status, ref))
        except (KeyError, FileNotFoundError):
            continue
    print(f"{len(loaded)} bundles")
    latest = {}
    for ref in loaded:
        aid, ver = ref.split("@v")
        if aid not in latest or int(ver) > int(latest[aid].split("@v")[1]):
            latest[aid] = ref
    for ref in sorted(latest.values()):
        logical, la = loaded[ref]
        for fn in la.metrics_by_function:
            for m in fn.get("metrics") or []:
                strata = curves.curve_strata(m)
                out["words"].append({
                    "ref": ref, "functionId": fn["functionId"], "metricId": m["metricId"],
                    "describe": {str(s): describe.describe(m, s) for s in [None] + strata},
                    "method": field_form.method_text(m), "units": field_form.units_of(m), "strata": strata,
                    "labels": [reference_support.stratum_label(s, m) for s in strata]})
    sample = sorted(loaded)[::max(1, len(loaded) // 24)]
    for ref in sample:
        logical, la = loaded[ref]
        for k in range(3):
            state = state_for(la, rng)
            sc, frs = curves.score_site(la, measure.measured_from_state(state))
            out["scores"].append({"ref": ref, "logical": logical, "state": state, "result": sc,
                                  "functions": {fid: fr.to_dict() for fid, fr in frs.items()}})
            if k == 0 and ref in sample[::3]:
                for fn in la.metrics_by_function[:3]:
                    fr = frs.get(fn["functionId"])
                    for m in fn.get("metrics") or []:
                        rc = state.get(m["metricId"]) or {}
                        midx = fr.metric_indices.get(m["metricId"]) if fr else None
                        note = "Chosen from the reach's NHDPlus slope (0.42 percent)" if rc.get("stratumAuto") else ""
                        out["rows"].append({"ref": ref, "metricId": m["metricId"], "functionId": fn["functionId"], "rc": rc, "midx": midx,
                                            "note": note, "html": html(deep_app._metric_row(m, rc, midx=midx, stratum_note=note))})
                    out["rows"].append({"ref": ref, "functionId": fn["functionId"], "score": fr.score if fr else None,
                                        "html": html(deep_app._scorecard(fn["functionId"], fr.score if fr else None))})
                for w in reference_support.withheld(la)[:3]:
                    out["rows"].append({"ref": ref, "withheld": w, "html": html(deep_app._withheld_card(w))})
                for g in reference_support.unassessed_functions(la)[:2]:
                    fnb = {"functionId": g["functionId"], "functionName": g["functionName"], "discipline": g.get("discipline", ""),
                           "metrics": [], "unassessed": g}
                    out["rows"].append({"ref": ref, "unassessed": fnb, "html": html(deep_app._unassessed_panel(fnb, la, eyebrow="Function 3 of 20 · Hydrology"))})
                for idx, n, counts in ((0, 20, (2, 4)), (19, 20, None), (5, 20, (0, 1))):
                    out["rows"].append({"footer": [idx, n, list(counts) if counts else None],
                                        "html": html(deep_app._fn_footer(idx, n, counts))})
    regions = json.loads(layout_file("library/deep-regions.json").read_text(encoding="utf-8"))["features"]
    for case in CASES:
        lat, lon = case["lat"], case["lon"]
        print(case["name"], "...", flush=True)
        rec = compute_site(lat, lon, {**INTERACTIVE_CONFIG, "includeGeometry": True, "metricFamilies": FAMILIES})
        site = rec["site"]
        hr_hit = (site["snapLat"], site["snapLon"], 0.0, site["nhdplusId"])
        v2_hit = tuple(case["v2Hit"]) if case.get("v2Hit") else None
        res = anchor_mod.classify_click(lat, lon, v2_hit=v2_hit, hr_hit=hr_hit, snap_tol_ft=150.0)
        anchor_ = res.get("anchor")
        d = pipeline.delineate_from_engine(rec, lat, lon, 1000.0)
        ci = dict(d["ctx_inputs"])
        ci.pop("site_engine", None)
        ci.pop("watershed_geojson", None)
        ci["siteAnchor"] = anchor_
        ci["comid"] = ((anchor_ or {}).get("scoredReach") or {}).get("comid") if anchor_ else None
        record = deep_app.engine_prefill.strip_geometry(rec) if hasattr(deep_app, "engine_prefill") else rec
        delin = {"delineation": d["delineation"]}
        out["delineations"][case["name"]] = d["delineation"]
        for ref, (logical, la) in loaded.items():
            auto = reference_support.auto_strata(la, delin)
            if auto:
                notes = {}
                for fn in la.metrics_by_function:
                    for m in fn.get("metrics") or []:
                        if isinstance(m.get("stratifier"), dict):
                            notes[m["metricId"]] = reference_support.stratifier_note(m, delin)
                out["strata"].append({"case": case["name"], "ref": ref, "auto": auto, "notes": notes})
        covering = [f["assessmentId"] for f in regions if deep_app.assessments._point_in_geometry(lon, lat, f.get("geometry"))]
        for aid in covering:
            refs = sorted((r for r in loaded if r.split("@v")[0] == aid), key=lambda r: -int(r.split("@v")[1]))
            for ref in refs[:2]:
                logical, la = loaded[ref]
                ids = [m["metricId"] for fn in la.metrics_by_function for m in fn.get("metrics") or []
                       if m["metricId"] in computed.computable_ids() and m["metricId"] not in XS_IDS]
                got = measure.compute_metrics_only(ci, ids, assessment=la, engine_record=record)
                out["computed"].append({"case": case["name"], "ref": ref, "ids": ids, "ci": ci, "record": record, "values": got})
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"{OUT}: {len(out['words'])} metrics, {len(out['scores'])} states, {len(out['rows'])} rows, "
          f"{len(out['strata'])} strata, {len(out['computed'])} prefills, {OUT.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
