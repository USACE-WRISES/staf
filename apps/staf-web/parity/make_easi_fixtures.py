"""Reference EASI screenings for the web app's tests, from EASI's own Python (the code the TypeScript ports).

For the clicks of ``make_delineate_fixtures.py`` this writes (``tests/fixtures/easi_ref.json``):

- ``cases``: EASI's Assessment step on each delineation (``assessment.assess`` on the case's ``ctx_inputs``)
  from the local bundle alone: what the prefetch read (StreamCat's row, the HUC12, the ATTAINS units, the
  WQP summaries, the NAS taxa, the NID dams, the strata) and the whole report. The cross-sections come
  from the published grid by the static app's rule (the Shiny EASI samples live 3DEP): at each of the nine
  reach positions the nearest grid section of the HR lines under the reach (``grid_geometry`` below, the
  rule of ``apps/staf-web/src/engine/xsreach.ts``); then the report is re-scored with overrides and with
  observations (``rescore``, ``apply_observed_evidence``);
- ``evals``: the evaluator (``screening_methods.evaluate``) on random inputs for every method and variant,
  with strata, regions and evidence records, and ``criteria``: ``criteria_for`` and ``equation_for``;
- ``geomorph``: the cross-section math (``balanced_profile``, ``simplify_profile``, ``summarize_profile``,
  ``derive_from_stages``, ``flow_area``, ``display_window``, ``cross_section_quality``,
  ``bankfull_width_cv``, ``describe_reach``) on the grid's stored profiles and synthetic ones, and
  ``stages``: ``rate_metrics_from_stages`` on them.

Run with the repo's .venv; it reads the local bundle (``apps/hr-data/data``) and the cross-section release
(``D:\\Data\\xs-grid\\release``), no services (a request raises):

    .venv\\Scripts\\python apps/staf-web/parity/make_easi_fixtures.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import random
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parents[1]
CASES = json.loads((HERE / "tests" / "fixtures" / "delineate_ref.json").read_text(encoding="utf-8"))["cases"]
OUT = HERE / "tests" / "fixtures" / "easi_ref.json"
XS = Path(os.environ.get("STAF_WEB_XS") or r"D:\Data\xs-grid\release")
REACH_FT = 1000.0
SCORED = ("ok", "ok_reference")
STATUS = ("ok", "unbalanced", "no_dem", "too_few_points", "ok_reference")
BANK = ("slope_break", "crest_scan")


def _offline(*_a, **_k):
    raise RuntimeError("offline (fixture)")


def main() -> int:
    os.environ["STAF_DATA_SOURCE"] = "bundle"
    os.environ["STAF_DATA_BUNDLE"] = str(REPO / "apps" / "hr-data" / "data")
    os.environ["EASI_ADOPTED_METHOD"] = "0"
    os.environ["EASI_CRITERIA_SET"] = "regional"
    sys.path.insert(0, str(REPO / "apps" / "easi"))
    sys.path.insert(0, str(REPO / "tools" / "xs-grid"))
    import requests
    requests.get = _offline
    requests.post = _offline
    requests.Session.request = _offline

    from easi import assessment, config, geomorph, pipeline, screening_methods as sm, watershed
    from easi.datasources import attains, nas, nid_barriers, nlcd, threedep, wbd, wqp
    from easi._vendor.site_engine import bundle, geometry, hr, reach as reach_mod
    from easi._vendor.site_engine._hrslim import points as point_rules
    from xsgrid import codec

    assert config.criteria_set() == "regional"
    nlcd.watershed_landcover = lambda *_a, **_k: {}        # no NLCD service in the static app
    start = point_rules._ten_years_before(date.today())

    def line_ids(lat, lon, length_ft):
        half = 0.004
        recs = bundle.lines_in_box(lon - half, lat - half, lon + half, lat + half) or []
        hit = geometry.nearest_point_on_records(recs, lat, lon)
        if hit is None or hit[3] is None or hit[2] > 150.0:
            return []
        nid = int(hit[3])
        rec = bundle.flowline(nhdplusid=nid)
        if rec is None:
            return []
        length_km = (length_ft / reach_mod.FT_PER_M) / 1000.0
        own = rec.get("lengthkm") or 0.0
        needed = round(max(length_km * 4, own + length_km) + 0.3, 1)
        ids, total, up = [nid], own, rec.get("uphydroseq")
        for _ in range(reach_mod._HOP_CAP):
            if total >= needed or not up:
                break
            nxt = hr.feature_by_hydroseq(up)
            if nxt is None or not nxt.get("geometry"):
                break
            ids.append(int(nxt["nhdplusid"]))
            total += nxt.get("lengthkm") or 0.0
            up = nxt.get("uphydroseq")
        return ids

    def grid_geometry(ids, coords, extrapolated):
        """xsreach.gridReachGeometry: the grid's sections nearest the nine reach positions."""
        import pyarrow.parquet as pq
        from pyproj import Transformer
        from shapely.geometry import LineString
        ds = bundle.dataset()
        secs = []
        for nid in ids:
            found = ds._find("by_id", [int(nid)])
            if not found:
                continue
            vpu = found[0][0].vpu
            if not (XS / f"xs-{vpu}.parquet").exists():
                continue
            nums = pq.read_table(XS / f"xs-{vpu}.parquet", filters=[("nhdplusid", "=", int(nid))]).to_pylist()
            pulls = pq.read_table(XS / f"xs-{vpu}-sections.parquet", columns=["nhdplusid", "part", "k", "x", "y"],
                                  filters=[("nhdplusid", "=", int(nid))]).to_pylist()
            at = dict(((p["part"], p["k"]), p) for p in pulls)
            for n in sorted(nums, key=lambda r: (r["part"], r["k"])):
                p = at.get((n["part"], n["k"]))
                if p is not None:
                    secs.append((n, p["x"], p["y"]))
        if len(coords) < 2 or not secs:
            return None
        tr = Transformer.from_crs(4326, 5070, always_xy=True)
        line = LineString([tr.transform(x, y) for x, y in coords])
        length = line.length
        cands = []

        def scaled(v, s):
            return None if v is None else v / s
        for i in range(1, 10):
            p = line.interpolate(length * (i * 0.1))
            best, best_d = secs[0], math.inf
            for s in secs:
                dx, dy = s[1] - p.x, s[2] - p.y
                dd = dx * dx + dy * dy
                if dd < best_d:
                    best, best_d = s, dd
            n = best[0]
            if STATUS[n["status"]] not in SCORED:
                continue
            cands.append({"position_frac": i * 0.1, "entrenchment_ratio": scaled(n["er"], 100), "bank_height_ratio": scaled(n["bhr"], 100),
                          "bankfull_width_m": scaled(n["bfw"], 10), "bankfull_depth_m": scaled(n["bfd"], 100),
                          "flood_prone_width_m": scaled(n["fpw"], 10), "low_bank_capped": bool(n["flags"] & 1),
                          "edge_limited": bool(n["flags"] & 2), "bank_detection": None if n["bank"] == 255 else BANK[n["bank"]],
                          "res": n["res"]})
        if not cands:
            return None
        stats = geomorph.reach_stats(cands)
        d = geomorph.median_candidate(cands)
        return {"candidate_scalars": cands, "reach": stats,
                "entrenchment_ratio": (stats.get("entrenchment_ratio") or {}).get("median"),
                "bank_height_ratio": (stats.get("bank_height_ratio") or {}).get("median"),
                "bankfull_width_m": cands[d]["bankfull_width_m"], "bankfull_depth_m": cands[d]["bankfull_depth_m"],
                "edge_limited": geomorph.median_edge_limited(cands), "bankfull_extrapolated": bool(extrapolated),
                "dem_resolution_m": max(c["res"] for c in cands), "default_index": d}

    out = {"start": [start.year, start.month, start.day], "cases": [], "evals": [], "criteria": [], "geomorph": [], "stages": []}
    rng = random.Random(20261011)

    # ---- the five screenings
    for case in CASES:
        res = case["easi"]["result"]
        ci = copy.deepcopy(res["ctx_inputs"])
        print(case["name"], "...", flush=True)
        if ci.get("watershedEngine"):
            # the delineate fixtures leave the engine's metric values out: EASI's own engine block
            block = watershed.compute_reach_watershed(ci["siteAnchor"])
            block.pop("polygon", None)
            ci["watershedEngine"] = block
        ctx = pipeline._ctx_from_inputs(copy.deepcopy(ci))
        from easi import bieger
        bf = bieger.bankfull_geometry(ctx.drainage_area_sqkm, ctx.lat, ctx.lon)
        coords = ((ctx.reach_geojson or {}).get("features") or [{}])[0].get("geometry", {}).get("coordinates") or []
        ids = line_ids(ctx.lat, ctx.lon, REACH_FT)
        geom = grid_geometry(ids, coords, bf["extrapolated"]) if len(coords) >= 2 else None
        threedep.reach_geomorphology = lambda *_a, _g=geom, **_k: copy.deepcopy(_g) if _g else {}
        report = asyncio.run(assessment.assess(ctx))
        rows = report["metricRows"]
        overrides = dict((r["metricId"], rng.choice(["Good", "Fair", "Poor"])) for r in rng.sample(rows, 6))
        observed = {
            "channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition": {"erodingBankPct": 35.0, "armoredBankPct": 5.5},
            "channel-evolution-channel-evolution-stage-and-trends": {"stageClass": rng.choice(["Good", "Fair", "Poor"]),
                                                                      "indicators": "Headcut 40 m upstream, 2026 visit"},
        }
        out["cases"].append({
            "name": case["name"], "ci": ci, "lineIds": ids, "bankfull": bf,
            "prefetch": {
                "streamcat": ctx.extras["streamcat"], "huc12": ctx.huc12, "strata": ctx.extras["strata"],
                "geom": ctx.extras["reach_geomorph"], "watershed": ctx.extras["watershed"],
                "attainsExact": attains.impairment_at_point(ctx.lat, ctx.lon),
                "attainsNearby": attains.impairment_near_point(ctx.lat, ctx.lon),
                "wqp": dict((p, wqp.sample_summary(p, ctx.lat, ctx.lon)) for p in ("tn", "tp", "temp")),
                "nas": nas.established_taxa(huc12=ctx.huc12, huc8=ctx.huc8),
                "nid": nid_barriers.barriers_near(ctx.lat, ctx.lon, miles=1.0),
                "huc12Wbd": wbd.huc12_at_point(ctx.lat, ctx.lon),
            },
            "report": report,
            "overrides": overrides, "rescored": assessment.rescore(report, overrides),
            "observed": observed, "observedReport": assessment.apply_observed_evidence(report, observed),
            "observedPartial": assessment.apply_observed_evidence(report, {
                "channel-evolution-channel-evolution-stage-and-trends": {"stageClass": "Fair", "indicators": "  "}}),
        })

    # ---- the evaluator on random inputs
    methods = sm.catalog()["methods"]
    strata_keys = set()
    for s in sm.curve_sets().values():
        strata_keys.update(s.get("curves", {}).keys())
    nars = sorted(k for k in strata_keys if k != "national")

    def strata():
        return {"nars9": rng.choice(nars + [None, "ZZZ"]), "slope_class": rng.choice(["lt_0.5", "0.5_to_2", "ge_2", None]),
                "l3": rng.choice(["58", "45", None]), "l2": rng.choice(["5.3", "8.3", None]), "l2_name": rng.choice(["Atlantic Highlands", None])}

    def edges(rule, st):
        if rule.get("curve"):
            try:
                _, curve, _ = sm.resolve_curve(rule["curve"], st)
                xs = [float(curve["x39"]), float(curve["x69"])] + [float(p[0]) for p in curve["points"]]
            except Exception:  # noqa: BLE001
                return [0.0, 1.0]
            return xs
        vals = [float(v) for b in (rule.get("bands") or []) for v in (b.get("min"), b.get("max")) if v is not None]
        for pair in (rule.get("regionalBands") or {}).values():
            vals += [float(pair[0]), float(pair[1])]
        return vals or [0.0, 1.0]

    def value_for(method, inp, st):
        if rng.random() < 0.12:
            return None
        if inp.get("valueType") == "integer":
            return rng.randint(0, 5)
        key = inp["key"]
        if method["operator"] == "categorical_lookup" and key == (method.get("formula") or {}).get("input"):
            keys = list(((method.get("formula") or {}).get("lookup") or {}).keys())
            pick = rng.choice(keys + ["zz9", "4 a", "5"])
            return rng.choice([pick, pick.lower(), " " + pick + " "])
        if inp.get("contextOnly"):
            return rng.choice([None, 46006, 46003, 33600, "46007.0", 12.5, "text"])
        vals = edges(inp if (inp.get("bands") or inp.get("curve") or inp.get("regionalBands")) else method, st)
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        choice = rng.random()
        if choice < 0.3:
            return rng.choice(vals)
        if choice < 0.4:
            return str(round(rng.uniform(lo - span * 0.5, hi + span * 0.5), 4))
        return round(rng.uniform(lo - span * 0.5, hi + span * 0.5), rng.choice([2, 4, 6]))

    for _ in range(700):
        parent = rng.choice(methods)
        keys = [None, parent["methodKey"]] + [v["methodKey"] for v in parent.get("variants", [])]
        variant = rng.choice(keys)
        method = sm._resolved_method(parent, variant)
        st = strata()
        ctx = {"strata": st} if rng.random() < 0.8 else {}
        if any(i.get("regionalBands") for i in method.get("inputs", [])):
            ctx["region"] = rng.choice(list(next(i for i in method["inputs"] if i.get("regionalBands"))["regionalBands"]) + ["xer", None])
            ctx["regionName"] = rng.choice(["Northern Appalachians", None])
        values = dict((i["key"], value_for(method, i, st)) for i in method.get("inputs", []))
        if rng.random() < 0.2:
            values["unused"] = 3.5
        evidence = None
        if rng.random() < 0.6:
            flags = [f for f in ("out_of_range_bhr", "out_of_range_er", "low_quality") if rng.random() < 0.3]
            evidence = {"crossSectionQuality": {"flags": flags, "reasons": dict((f, f"{f} because") for f in flags if rng.random() < 0.7),
                                                "quality": "K2b"}}
        kw = {
            "context": ctx, "input_meta": dict((k, {"source": f"src {k}", **({"details": {"n": 3}} if rng.random() < 0.2 else {})})
                                              for k in values if rng.random() < 0.7),
            "confidence": rng.choice([None, "H", "M", "L"]), "variant_key": variant,
            "source_tier": rng.choice([None, "observed", "screening-proxy"]), "evidence_family": rng.choice([None, "fam"]),
            "used_fallback": rng.choice([None, True, False]), "observed_overrides_proxy": rng.choice([None, True, False]),
            "evidence": evidence,
        }
        ev = sm.evaluate(parent["metricId"], values, **kw)
        out["evals"].append({"mid": parent["metricId"], "values": values, "opts": kw,
                             "out": {"rating": ev.rating, "index": ev.index, "combined": ev.combined_value, "trace": ev.trace}})
    for parent in methods:
        for key in [parent["methodKey"]] + [v["methodKey"] for v in parent.get("variants", [])]:
            method = sm._resolved_method(parent, key)
            for _ in range(3):
                ctx = {"strata": strata(), "region": rng.choice(["NAP", "SAP", "xer", None])}
                out["criteria"].append({"mid": parent["metricId"], "key": key, "context": ctx,
                                        "criteria": sm.criteria_for(method, ctx), "equation": sm.equation_for(method)})
        out["criteria"].append({"mid": parent["metricId"], "bands": dict((ind or "", config.criteria_bands(parent["metricId"], ind))
                                                                        for ind in (None, "impervious", "agriculture", "nope"))})

    # ---- the cross-section math, on stored profiles and synthetic ones
    import numpy as np
    import pyarrow.parquet as pq
    profiles = []
    for case in out["cases"]:
        for nid in case["lineIds"][:2]:
            found = bundle.dataset()._find("by_id", [int(nid)])
            vpu = found[0][0].vpu
            path = XS / f"xs-{vpu}-median.parquet"
            if not path.exists():
                continue
            t = pq.read_table(path, filters=[("nhdplusid", "=", int(nid))]).to_pylist()
            for r in t:
                z = codec.decode(np.asarray(r["z"], dtype=np.int32)).astype(np.float64)
                ts = np.linspace(-r["wide_cm"] / 100.0, r["wide_cm"] / 100.0, r["n_pts"])
                ok = np.isfinite(z)
                profiles.append({"source": f"{nid}:{r['part']}:{r['k']}", "res": r["res"],
                                 "stations": ts[ok].tolist(), "elevs": z[ok].tolist()})
    for k in range(4):
        n = 301
        ts = np.linspace(-120.0, 120.0, n)
        base = 100.0 + 0.02 * np.abs(ts) ** 1.3 - 3.0 * np.exp(-((ts - 4.0 * k) / (6.0 + k)) ** 2)
        profiles.append({"source": f"synthetic {k}", "res": [1, 3, 10, 1][k],
                         "stations": ts.tolist(), "elevs": (base + 0.05 * np.sin(ts * (k + 1))).tolist()})
    for p in profiles:
        st, el = p["stations"], p["elevs"]
        bal = geomorph.balanced_profile(st, el)
        simp = geomorph.simplify_profile(bal[0], bal[1]) if bal else None
        use = simp or (st, el)
        da = rng.choice([3.5, 40.08, 250.0])
        w, d = rng.uniform(4, 30), rng.uniform(0.3, 2.0)
        area = rng.choice([None, round(w * d * 0.8, 2)])
        summ = geomorph.summarize_profile(list(use[0]), list(use[1]), da, bankfull=(w, d), bankfull_area_m2=area,
                                          division=rng.choice([None, "Appalachian Highlands"]), dem_res_m=p["res"])
        th = min(use[1])
        stages = [th + x for x in (0.2, 0.5, 1.0, 1.7, 3.0)]
        derived = [geomorph.derive_from_stages(list(use[0]), list(use[1]), thalweg=th, bankfull_stage=b, floodplain_stage=b + f)
                   for b in stages for f in (-0.3, 0.0, 0.8)]
        out["geomorph"].append({
            "source": p["source"], "res": p["res"], "stations": st, "elevs": el, "balanced": bal, "simplified": simp,
            "da": da, "bankfull": [w, d], "area": area, "summary": summ, "stages": stages, "derived": derived,
            "flowArea": [geomorph.flow_area(list(use[0]), list(use[1]), s) for s in stages],
            "window": [geomorph.display_window(list(use[0]), list(use[1]), ceiling_stage=s) for s in stages],
            "topOfBank": geomorph.top_of_bank_elev(list(use[0]), list(use[1])),
        })
        block = {"stations": list(use[0]), "elevs": list(use[1]), "thalweg": th, "label": "700 ft",
                 "fcode": rng.choice([46006, 33600, None])}
        st_ = {"nars9": rng.choice(nars), "slope_class": "0.5_to_2"}
        for reason in ("edited", "scrolled"):
            b = summ["bankfull_stage_m"]
            out["stages"].append({"block": block, "bf": b, "lb": summ["low_bank_stage_m"], "reason": reason, "strata": st_,
                                  "out": assessment.rate_metrics_from_stages(block, b, summ["low_bank_stage_m"], reason=reason, strata=st_)})
    # the cross-section plot's figure (xsplotly.figure), its data and layout as plotly.py writes them
    from easi import xsplotly
    out["figures"] = []
    for g in out["geomorph"][:6]:
        s = g["summary"]
        st, el = s["profile"]["stations"], s["profile"]["elevs"]
        for unit, bf, lb, src in (("ft", s["bankfull_stage_m"], s["low_bank_stage_m"], "USGS 3DEP 1 m DEM"),
                                  ("m", s["bankfull_stage_m"] + 0.2, s["low_bank_stage_m"] - 0.1, None),
                                  ("ft", None, s["low_bank_stage_m"], None), ("ft", s["bankfull_stage_m"], None, "USGS 3DEP DEM")):
            fig = xsplotly.figure(st, el, thalweg=s["thalweg"], bankfull_stage=bf, floodplain_stage=lb, unit=unit, source=src)
            j = json.loads(fig.to_json())
            out["figures"].append({"stations": st, "elevs": el, "thalweg": s["thalweg"], "bf": bf, "lb": lb, "unit": unit,
                                   "source": src, "data": j["data"], "layout": j["layout"]})
    # quality records and reach texts on synthetic geometries
    out["quality"] = []
    for _ in range(80):
        cands = []
        for i in range(rng.randint(0, 9)):
            cands.append({"bank_height_ratio": rng.choice([None, round(rng.uniform(-0.2, 2.0), 2), 2.0]),
                          "entrenchment_ratio": rng.choice([None, round(rng.uniform(0.5, 6.0), 2)]),
                          "low_bank_capped": rng.random() < 0.3, "bankfull_depth_m": rng.choice([None, round(rng.uniform(0.02, 1.5), 2)]),
                          "bankfull_width_m": rng.choice([None, round(rng.uniform(1, 40), 1)]),
                          "bank_detection": rng.choice([None, "slope_break", "crest_scan"]), "position_frac": round((i + 1) * 0.1, 4)})
        geom = {"candidate_scalars": cands, "reach": geomorph.reach_stats(cands) if cands and rng.random() < 0.8 else {},
                "bank_height_ratio": rng.choice([None, round(rng.uniform(-0.1, 2.0), 2)]),
                "entrenchment_ratio": rng.choice([None, round(rng.uniform(0.6, 5.0), 2)]),
                "dem_resolution_m": rng.choice([None, 1, 3, 10]), "bankfull_extrapolated": rng.random() < 0.2,
                "edge_limited": rng.random() < 0.2}
        out["quality"].append({"geom": geom, "quality": geomorph.cross_section_quality(geom),
                               "widthCv": geomorph.bankfull_width_cv(geom),
                               "describe": [geomorph.describe_reach(geom["reach"], k) for k in ("bank_height_ratio", "entrenchment_ratio")]})
    OUT.write_text(json.dumps(out, separators=(",", ":"), allow_nan=False), encoding="utf-8", newline="\n")
    print(f"{OUT}: {len(out['cases'])} cases, {len(out['evals'])} evaluations, {len(out['geomorph'])} profiles,"
          f" {OUT.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
