"""Precomputed watershed values against the live site engine (bundle v2, Phase 3).

The baseline is ``D:\\Data\\nhdplus-hr\\slim\\metric_accuracy.json``: the engine's metric families
run on the original USGS watersheds and flowlines of the engine's walk panel and small headwater
sites (Phase 0, live services). Version 2 watersheds are those same polygons, so differences
come from the precompute alone (native grid against MRLC's resampled image, original lines
against the service's, sources and vintages).

    python tools/hr-slim/scripts/parity.py landcover
    python tools/hr-slim/scripts/parity.py vectors
    python tools/hr-slim/scripts/parity.py points     # WQP, NID and NWIS against the apps' live calls
    python tools/hr-slim/scripts/parity.py nas        # established taxa by HUC12 against EASI's live call
    python tools/hr-slim/scripts/parity.py streamcat  # the V2 StreamCat slices against the live API
    python tools/hr-slim/scripts/parity.py v2         # V2 attributes, EROM, sinuosity, lines vs the fabric API
    python tools/hr-slim/scripts/parity.py nwi        # NWI strips against the live USFWS wetlands service
    python tools/hr-slim/scripts/parity.py basins     # local V2 basins against NLDI get_basins
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "apps" / "hr-data"))

from hrslim import Dataset2  # noqa: E402
from hrslim.values import ValueTables, watershed_landcover  # noqa: E402

V2 = Path(r"D:\Data\nhdplus-hr\slim2")
BASELINE = Path(r"D:\Data\nhdplus-hr\slim\metric_accuracy.json")


def cmd_landcover(args) -> None:
    ds = Dataset2(V2 / "data")
    tables = ValueTables(V2 / args.values)
    base = [r for r in json.loads(BASELINE.read_text(encoding="utf-8")) if r.get("status") == "ok"]
    diffs: dict = {}
    rows = []
    for r in base:
        engine = r["variants"]["original"]["values"]
        got = watershed_landcover(ds, tables, r["nhdplusid"])
        if got.get("status") != "ok":
            rows.append(f"| {r['label']} | {got.get('status')}: {got.get('reason')} | | |")
            continue
        worst = (0.0, "")
        for key, ev in engine.items():
            if not (key.endswith("Watershed") or key.endswith("Riparian")) or "Pct" not in key:
                continue
            gv = got.get(key)
            if gv is None or ev is None:
                continue
            d = gv - ev
            diffs.setdefault(key, []).append(d)
            if abs(d) > abs(worst[0]):
                worst = (d, key)
        rows.append(f"| {r['label']} | {r['reaches']} | {r['variants']['original']['areaSqkm']:.2f} | "
                    f"{worst[0]:+.2f} ({worst[1]}) |")
    print("| Site | reaches | km2 | largest difference, percentage points |")
    print("|---|---|---|---|")
    print("\n".join(rows))
    print()
    print("| Metric | sites | median difference | median absolute | max absolute |")
    print("|---|---|---|---|---|")
    for key in sorted(diffs, key=lambda k: (k.endswith("Riparian"), k)):
        d = np.asarray(diffs[key])
        print(f"| {key} | {len(d)} | {np.median(d):+.2f} | {np.median(np.abs(d)):.2f} | {np.abs(d).max():.2f} |")
    out = V2 / "accept" / f"parity_landcover_{args.values}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict((k, list(map(float, v))) for k, v in diffs.items()), indent=1), encoding="utf-8")


def cmd_vectors(args) -> None:
    """Roads, crossings and dams against the engine baseline, site by site."""
    from hrslim.values import watershed_values
    ds = Dataset2(V2 / "data")
    tables = ValueTables(V2 / args.values)
    base = [r for r in json.loads(BASELINE.read_text(encoding="utf-8")) if r.get("status") == "ok"]
    keys = ("roadLengthKm", "roadDensity", "roadCrossings", "damCount", "damStorageAcreFt", "damNidStorageAcreFt",
            "soilKFactor")
    print("| Site | km2 | " + " | ".join(keys) + " |")
    print("|---|---|" + "---|" * len(keys))
    for r in base:
        engine = r["variants"]["original"]["values"]
        try:
            got = watershed_values(ds, tables, r["nhdplusid"])
        except FileNotFoundError as exc:
            print(f"| {r['label']} | not built yet ({Path(str(exc.filename)).name}) |")
            continue
        if got.get("status") != "ok":
            print(f"| {r['label']} | {got.get('reason')} |")
            continue
        cells = [f"{got.get(k)} / {engine.get(k)}" for k in keys]
        print(f"| {r['label']} | {got['areaSqkm']:.2f} | " + " | ".join(cells) + " |")


def _offset(rng, lat, lon, lo_m, hi_m):
    """A point ``lo_m`` to ``hi_m`` metres from (lat, lon) at a random bearing."""
    d, b = rng.uniform(lo_m, hi_m), rng.uniform(0, 2 * np.pi)
    return lat + d * np.cos(b) / 111_320.0, lon + d * np.sin(b) / (111_320.0 * np.cos(np.radians(lat)))


def cmd_points(args) -> None:
    """The bundled point tables against the apps' own live functions, at points near WQP stations,
    near NID dams, and on flowlines with gage-sized drainage areas, in every lower-48 pilot region.
    The NWIS rows show what SFARI gets today (its live path times out); ``nwis_check.py`` compares
    the data with long timeouts and retries."""
    from datetime import date

    import shapely
    sys.path.insert(0, str(HERE.parents[2] / "tools" / "hr-slim"))
    sys.path.insert(0, str(HERE.parents[2] / "apps" / "easi"))
    sys.path.insert(0, str(HERE.parents[2] / "apps" / "sfari"))
    from easi.datasources import nid_barriers as easi_nid
    from easi.datasources import wqp as easi_wqp
    from hrbuild import sources
    from hrslim import points
    from sfari.datasources import nid_barriers as sfari_nid
    from sfari.datasources import nwis as sfari_nwis
    from sfari.datasources import wqp as sfari_wqp

    tables = points.PointTables(V2 / "tables")
    ds = Dataset2(V2 / "data")
    manifest = json.loads((V2 / "data" / "manifest.json").read_text(encoding="utf-8"))
    vpus = sources.lower48(sorted(manifest["vpus"]))
    outlines = sources.region_outlines(vpus)
    rng = np.random.default_rng(args.seed)
    w = tables.wqp()
    start = np.datetime64(points._ten_years_before(date.today()), "D")
    recent_ok = np.unique(w["station"][(w["reason"] == 0) & (w["date"] >= start)])
    nid = tables.nid()
    tally: dict = {}
    records = []

    def agree(name, ok):
        a = tally.setdefault(name, [0, 0])
        a[0] += int(bool(ok))
        a[1] += 1

    print("| Region | lookup | point | bundle | live |")
    print("|---|---|---|---|---|")
    for vpu in vpus:
        outline = outlines[vpu]
        inside = recent_ok[shapely.contains_xy(outline, w["lon"][recent_ok], w["lat"][recent_ok])]
        for s in rng.choice(inside, min(args.per_region, len(inside)), replace=False):
            lat, lon = _offset(rng, float(w["lat"][s]), float(w["lon"][s]), 500, 3000)
            for param in ("tn", "tp"):
                live = easi_wqp.sample_summary(param, lat, lon, timeout=120)
                got = points.wqp_easi(tables, param, lat, lon)
                if live is None:
                    agree("WQP EASI live failed", True)
                else:
                    keys = ("value", "station_count", "observation_count", "excluded_count")
                    agree(f"WQP EASI {param}", all(live.get(k) == got.get(k) for k in keys))
                    b = "/".join(str(got.get(k)) for k in keys)
                    lv = "/".join(str(live.get(k)) for k in keys)
                    print(f"| {vpu} | EASI {param} value/stations/results/excluded | {lat:.4f}, {lon:.4f} | {b} | {lv} |",
                          flush=True)
                records.append({"vpu": vpu, "lookup": f"easi_{param}", "lat": lat, "lon": lon, "bundle": got,
                                "live": live})
                live_s = sfari_wqp.median_value(param, lat, lon, timeout=120)
                got_s = points.wqp_sfari(tables, param, lat, lon)
                agree(f"WQP SFARI {param}", live_s == got_s)
                print(f"| {vpu} | SFARI {param} median | {lat:.4f}, {lon:.4f} | {got_s} | {live_s} |", flush=True)
                records.append({"vpu": vpu, "lookup": f"sfari_{param}", "lat": lat, "lon": lon, "bundle": got_s,
                                "live": live_s})
            # EASI's temperature context (shown beside M13, never scored) at the same points
            if (V2 / "tables" / "wqp_temperature.parquet").exists():
                # live EASI counts every Celsius number and no Fahrenheit, so the check is EASI's own
                # rule (fahrenheit=False, screen=False); the bundle's default, which adds converted
                # Fahrenheit results and leaves out unrealistic Celsius ones, is reported beside it
                live = easi_wqp.sample_summary("temp", lat, lon, timeout=120)
                got = points.wqp_easi(tables, "temp", lat, lon, fahrenheit=False, screen=False)
                with_f = points.wqp_easi(tables, "temp", lat, lon)
                if live is not None:
                    keys = ("value", "station_count", "observation_count", "excluded_count")
                    agree("WQP EASI temp (EASI's Celsius rule)", all(live.get(k) == got.get(k) for k in keys))
                    agree("WQP temp, the Fahrenheit rule adds results", with_f.get("fahrenheit_converted"))
                    agree("WQP temp, the screen leaves results out", with_f["excluded"].get("implausible"))
                    print(f"| {vpu} | EASI temp value/stations/results/excluded (STAF rule) | {lat:.4f}, {lon:.4f} | "
                          + "/".join(str(got.get(k)) for k in keys)
                          + f" ({with_f.get('value')}, +{with_f.get('fahrenheit_converted')} F, "
                          + f"-{with_f['excluded'].get('implausible')} screened) | "
                          + "/".join(str(live.get(k)) for k in keys) + " |", flush=True)
                records.append({"vpu": vpu, "lookup": "easi_temp", "lat": lat, "lon": lon, "bundle": got,
                                "bundle_with_fahrenheit": with_f, "live": live})
        dams = np.nonzero(shapely.contains_xy(outline, nid["lon"], nid["lat"]))[0]
        for d in rng.choice(dams, min(args.per_region, len(dams)), replace=False):
            lat, lon = _offset(rng, float(nid["lat"][d]), float(nid["lon"][d]), 100, 1500)
            live = easi_nid.barriers_near(lat, lon, 1.0, timeout=60)
            got = points.nid_radius(tables, lat, lon, 1.0)
            if live is not None:
                # the bundle's dams come from the same FeatureServer; distances agree to rounding
                same = len(got) == len(live) and all(abs(a["distance_m"] - b["distance_m"]) <= 2.0
                                                     for a, b in zip(got, live))
                agree("NID 1 mile radius (EASI, DEEP), distances within 2 m", same)
                print(f"| {vpu} | NID radius: dams, distances m | {lat:.4f}, {lon:.4f} | "
                      f"{len(got)}: {[x['distance_m'] for x in got]} | {len(live)}: {[x['distance_m'] for x in live]} |",
                      flush=True)
            live_b = sfari_nid.barriers_near(lat, lon, 1.0, timeout=60)
            got_b = points.nid_box(tables, lat, lon, 1.0)
            if live_b is not None:
                agree("NID 1 mile box (SFARI)", len(live_b) == len(got_b))
                print(f"| {vpu} | NID box: dams | {lat:.4f}, {lon:.4f} | {len(got_b)} | {len(live_b)} |", flush=True)
            records.append({"vpu": vpu, "lookup": "nid", "lat": lat, "lon": lon, "radius": [got, live],
                            "box": [len(got_b), None if live_b is None else len(live_b)]})
        topo = ds._regions[vpu].topo()
        da = np.nan_to_num(np.asarray(topo["da"], float))
        cand = np.nonzero((da > 50) & (da < 2000))[0]
        for row in rng.choice(cand, min(args.per_region, len(cand)), replace=False):
            t = ds.reach(nhdplusid=int(topo["ids"][row]))
            lon = (t.column("xmin")[0].as_py() + t.column("xmax")[0].as_py()) / 2e5
            lat = (t.column("ymin")[0].as_py() + t.column("ymax")[0].as_py()) / 2e5
            live = sfari_nwis.flow_stats(lat, lon, float(da[row]))
            got = points.nwis_flow_stats(tables, lat, lon, float(da[row]))
            same = (live or {}).get("site") == (got or {}).get("site") and all(
                abs(((live or {}).get(k) or 0) - ((got or {}).get(k) or 0))
                <= 0.02 * max(1.0, abs((live or {}).get(k) or 0)) for k in ("q10", "q50", "q90"))
            agree("NWIS gage statistics (SFARI)", same)

            def show(r):
                return None if r is None else f"{r['site']} n={r['n_days']} Q50={r['q50']} Q90={r['q90']}"
            print(f"| {vpu} | NWIS gage | {lat:.4f}, {lon:.4f} | {show(got)} | {show(live)} |", flush=True)
            records.append({"vpu": vpu, "lookup": "nwis", "lat": lat, "lon": lon, "bundle": got, "live": live})
    print()
    for k, (a, n) in tally.items():
        print(f"{k}: {a} of {n} agree")
    out = V2 / "accept" / "parity_points.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"tally": tally, "records": records}, indent=1, default=str), encoding="utf-8")


def cmd_nas(args) -> None:
    """EASI's established taxa per HUC12 (M19) from the bundled NAS table against the live API, for
    HUC12s the pilot's flowlines carry (``extras2_*``), half of them HUC12s the table has records for."""
    import pyarrow.parquet as pq
    sys.path.insert(0, str(HERE.parents[2] / "apps" / "easi"))
    from easi.datasources import nas
    from hrslim import points
    tables = points.PointTables(V2 / "tables")
    t = tables.nas()
    with_records = set(t["huc12"][t["huc12"] != ""].tolist())
    rng = np.random.default_rng(args.seed)
    tally = {"same": 0, "bundle_more_live_capped": 0, "differ": 0, "live_failed": 0}
    rows = []
    print("| Region | HUC12 | bundle taxa | live taxa | note |")
    print("|---|---|---|---|---|")
    for path in sorted((V2 / "values").glob("extras2_*.parquet")):
        vpu = path.stem.split("_")[1]
        hucs = np.unique(pq.read_table(path, columns=["huc12_50"]).column("huc12_50").to_numpy())
        hucs = [f"{int(h):012d}" for h in hucs if h > 0]
        known = [h for h in hucs if h in with_records]
        picks = list(rng.choice(known, min(args.per_region, len(known)), replace=False)) if known else []
        others = [h for h in hucs if h not in with_records]
        picks += list(rng.choice(others, min(args.per_region, len(others)), replace=False)) if others else []
        for huc in picks:
            got = points.nas_established(tables, huc12=huc)
            live = nas.established_taxa(huc12=huc, timeout=120)
            if live is None:
                tally["live_failed"] += 1
                note = "live failed"
            elif got == live:
                tally["same"] += 1
                note = ""
            elif set(live) <= set(got) and int((t["huc12"] == huc).sum()) > 500:
                tally["bundle_more_live_capped"] += 1
                note = "live stops at 500 records"
            else:
                tally["differ"] += 1
                note = f"only bundle {sorted(set(got) - set(live or []))[:3]}, only live {sorted(set(live or []) - set(got))[:3]}"
            print(f"| {vpu} | {huc} | {len(got)} | {None if live is None else len(live)} | {note} |", flush=True)
            rows.append({"vpu": vpu, "huc12": huc, "bundle": got, "live": live})
    print()
    print(tally)
    out = V2 / "accept" / "parity_nas.json"
    out.write_text(json.dumps({"tally": tally, "rows": rows}, indent=1), encoding="utf-8")


def _streamcat_request(col: str) -> tuple:
    """(base name, area of interest) of a slice column, as the API names them."""
    for suffix in ("wsrp100", "ws", "cat"):
        if col.endswith(suffix):
            return col[: -len(suffix)], suffix
    return col, "other"


def cmd_streamcat(args) -> None:
    """Every column of the V2 StreamCat slices against the StreamCat API (the national builder's own
    POST), for random COMIDs per region; float32 storage is checked at the apps' rounding."""
    import pyarrow.parquet as pq
    sys.path.insert(0, str(HERE.parents[2] / "tools" / "easi-national"))
    from builder.stages.streamcat import _normalize, _post
    v2_root = Path(r"D:\Data\nhdplus-hr\v2pilot")
    rng = np.random.default_rng(args.seed)
    bundle: dict = {}
    for path in sorted((v2_root / "data").glob("streamcat2_*.parquet")):
        t = pq.read_table(path).to_pandas()
        t = t[t.drop(columns=["comid"]).notna().any(axis=1)]
        for _, row in t.iloc[rng.choice(len(t), min(args.per_region, len(t)), replace=False)].iterrows():
            bundle[int(row["comid"])] = row.drop("comid").to_dict()
    columns = sorted(next(iter(bundle.values())))
    by_aoi: dict = {}
    for col in columns:
        name, aoi = _streamcat_request(col)
        by_aoi.setdefault(aoi, []).append(name)
    live: dict = {}
    comids = sorted(bundle)
    for aoi, names in by_aoi.items():
        for gi in range(0, len(names), 5):
            group = names[gi:gi + 5]
            for ci in range(0, len(comids), 500):
                batch = comids[ci:ci + 500]
                rows = _normalize(_post({"name": ",".join(group), "aoi": aoi,
                                         "comid": ",".join(str(c) for c in batch)}))
                for c, r in rows.items():
                    live.setdefault(c, {}).update(r)
    print(f"{len(comids)} COMIDs, {len(columns)} columns; live rows for {len(live)}")
    print("| Column | compared | identical at the apps' rounding (2 / 4 decimals) | max abs difference | missing here / live |")
    print("|---|---|---|---|---|")
    worst = {}
    for col in columns:
        n = same2 = same4 = miss_b = miss_l = 0
        mx = 0.0
        for c in comids:
            b = bundle[c].get(col)
            lv = (live.get(c) or {}).get(col)
            b = None if b is None or (isinstance(b, float) and np.isnan(b)) else float(b)
            lv = None if lv is None else float(lv)
            if b is None and lv is None:
                continue
            if b is None or lv is None:
                miss_b += int(b is None)
                miss_l += int(lv is None)
                continue
            n += 1
            same2 += int(round(b, 2) == round(lv, 2))
            same4 += int(round(b, 4) == round(lv, 4))
            mx = max(mx, abs(b - lv))
        worst[col] = (n, same2, same4, mx, miss_b, miss_l)
        print(f"| {col} | {n} | {same2} / {same4} | {mx:.3g} | {miss_b} / {miss_l} |")
    out = V2 / "accept" / "parity_streamcat.json"
    out.write_text(json.dumps(worst, indent=1), encoding="utf-8")


def cmd_v2(args) -> None:
    """The bundled NHDPlus V2 regions against EASI's own fabric API reads (``easi.datasources.fabric``):
    the attributes EASI keeps, the gage-adjusted EROM flows, the sinuosity EASI computes from the
    fabric geometry (``delineation.line_sinuosity``) and the line vertices."""
    import pyarrow.parquet as pq
    import shapely
    sys.path.insert(0, str(HERE.parents[2] / "apps" / "easi"))
    from easi import delineation
    from easi.datasources import fabric
    v2_data = Path(r"D:\Data\nhdplus-hr\v2pilot\data")
    manifest = json.loads((v2_data / "manifest.json").read_text(encoding="utf-8"))
    rng = np.random.default_rng(args.seed)
    tally: dict = {}
    worst_offset = 0.0
    bad: list = []

    def agree(name, ok):
        a = tally.setdefault(name, [0, 0])
        a[0] += int(bool(ok))
        a[1] += 1

    for hu4, entry in sorted(manifest["vpus"].items()):
        lines = pq.read_table(v2_data / entry["lines"]["file"]).to_pandas()
        attrs = pq.read_table(v2_data / f"v2attrs2_{hu4}.parquet").to_pandas()
        for row in rng.choice(len(lines), min(args.per_region, len(lines)), replace=False):
            r = lines.iloc[row]
            comid = int(r["nhdplusid"])
            feat = fabric.feature_by_comid(comid)
            if not feat:
                agree("fabric answered", False)
                continue
            agree("fabric answered", True)
            live = fabric.attrs_from_feature(feat)
            props = feat.get("properties") or {}
            slope = None if pd_isnan(r["slope"]) or r["slope"] < 0 else float(r["slope"])
            checks = {
                "gnis_name": (None if r["gnis_name"] in (None, "") else r["gnis_name"]) == live["gnis_name"],
                "drainage area": float(r["totdasqkm"]) == live["drainage_area_sqkm"],
                "slope": slope == live["slope"],
                "fcode": int(r["fcode"]) == live["fcode"],
                "stream order": int(r["streamorde"]) == live["stream_order"],
                "length": float(r["lengthkm"]) == float(props.get("lengthkm")),
            }
            erom = live.get("erom") or {}
            checks["EROM qe (13 values)"] = all(
                (pd_isnan(attrs.iloc[row][k]) and erom.get(k) is None) or float(attrs.iloc[row][k]) == erom.get(k)
                for k in fabric.EROM_PROPERTIES)
            geom = shapely.geometry.shape(feat["geometry"])
            live_sin = delineation.line_sinuosity(geom)
            here_sin = None if pd_isnan(attrs.iloc[row]["sinuosity"]) else float(attrs.iloc[row]["sinuosity"])
            checks["sinuosity"] = live_sin == here_sin
            mine = shapely.linestrings(np.column_stack([np.asarray(r["x"]) / 1e5, np.asarray(r["y"]) / 1e5]))
            live_line = geom.geoms[0] if geom.geom_type == "MultiLineString" else geom
            checks["vertices"] = shapely.get_num_coordinates(mine) == len(live_line.coords) or \
                geom.geom_type == "MultiLineString"
            off = shapely.hausdorff_distance(mine, live_line) * 111_000
            worst_offset = max(worst_offset, off)
            for name, ok in checks.items():
                agree(name, ok)
                if not ok and len(bad) < 15:
                    bad.append((hu4, comid, name))
    print("| Check | agree |")
    print("|---|---|")
    for k, (a, n) in tally.items():
        print(f"| {k} | {a} of {n} |")
    print(f"largest line offset from the fabric geometry: {worst_offset:.2f} m (coordinates to 1e-5 degree)")
    for b in bad:
        print("differs:", b)


def pd_isnan(v) -> bool:
    try:
        return v is None or bool(np.isnan(v))
    except TypeError:
        return False


NWI_URL = ("https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/"
           "Wetlands/MapServer/0/query")


def _nwi_live(strip_4326, attr_field="Wetlands.ATTRIBUTE"):
    """Wetland polygons of the live service intersecting the strip, as (system letter, geometry)."""
    import requests
    import shapely
    feats, offset = [], 0
    while True:
        r = requests.post(NWI_URL, data={
            "geometry": json.dumps({"rings": [list(map(list, strip_4326.exterior.coords))],
                                    "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryPolygon", "inSR": "4326", "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects", "outFields": attr_field, "returnGeometry": "true",
            "resultOffset": offset, "f": "geojson"}, timeout=180)
        data = r.json()
        page = data.get("features") or []
        for f in page:
            props = f.get("properties") or {}
            code = str(props.get(attr_field) or props.get("ATTRIBUTE") or "")
            if f.get("geometry"):
                feats.append((code[:1].upper(), shapely.geometry.shape(f["geometry"])))
        if not data.get("exceededTransferLimit") and not (data.get("properties") or {}).get("exceededTransferLimit"):
            return feats
        offset += len(page)


def cmd_nwi(args) -> None:
    """Each sampled flowline's 150 m strip (from its original geometry) against the live USFWS
    wetlands service SFARI reads: wetland area inside the strip by NWI system, here and live."""
    import pyarrow.parquet as pq
    import shapely
    from pyproj import Transformer
    sys.path.insert(0, str(HERE.parents[2] / "tools" / "hr-slim"))
    from hrbuild.extras import NWI_STRIP_M, NWI_SYSTEMS
    from hrbuild.zonal import original_lines, region_paths
    manifest = json.loads((V2 / "data" / "manifest.json").read_text(encoding="utf-8"))
    to4326 = Transformer.from_crs(5070, 4326, always_xy=True)
    to5070 = Transformer.from_crs(4326, 5070, always_xy=True)
    rng = np.random.default_rng(args.seed)
    rows_out = []
    for vpu in args.vpus:
        ex = pq.read_table(V2 / "values" / f"extras2_{vpu}.parquet").to_pandas()
        wet = ex[[f"nwi_{c.lower()}_m2" for c in NWI_SYSTEMS]].sum(axis=1).to_numpy()
        cand = np.nonzero(wet > 1000)[0]
        _, ids = region_paths(V2 / "data", manifest["vpus"][vpu])
        lines = original_lines(manifest["vpus"][vpu], ids)
        for row in rng.choice(cand, min(args.per_region, len(cand)), replace=False):
            strip = shapely.buffer(lines[row], NWI_STRIP_M, cap_style="flat")
            strip4326 = shapely.transform(strip, lambda q: np.column_stack(to4326.transform(q[:, 0], q[:, 1])))
            if strip4326.geom_type != "Polygon":
                continue
            live = dict((c, 0.0) for c in NWI_SYSTEMS)
            seen = set()          # the service can return a wetland twice, as the state downloads do
            for code, g in _nwi_live(strip4326):
                if code in live:
                    gm = shapely.transform(shapely.make_valid(g),
                                           lambda q: np.column_stack(to5070.transform(q[:, 0], q[:, 1])))
                    c = shapely.centroid(gm)
                    key = (code, round(shapely.area(gm)), round(c.x), round(c.y))
                    if key in seen:
                        continue
                    seen.add(key)
                    live[code] += shapely.area(shapely.intersection(strip, gm))
            here = dict((c, float(ex.iloc[row][f"nwi_{c.lower()}_m2"])) for c in NWI_SYSTEMS)
            tot_l, tot_h = sum(live.values()), sum(here.values())
            rows_out.append((vpu, int(ex.iloc[row]["nhdplusid"]), tot_h, tot_l))
            print(f"| {vpu} | {int(ex.iloc[row]['nhdplusid'])} | {tot_h:,.0f} | {tot_l:,.0f} | "
                  f"{100 * (tot_h - tot_l) / max(tot_l, 1):+.2f}% | "
                  + ", ".join(f"{c} {here[c]:,.0f}/{live[c]:,.0f}" for c in NWI_SYSTEMS if here[c] or live[c]) + " |",
                  flush=True)
    d = np.array([100 * (h - l) / max(l, 1) for _, _, h, l in rows_out])
    if len(d):
        print(f"\n{len(d)} strips: median difference {np.median(d):+.3f}%, largest {np.abs(d).max():.3f}%")


def cmd_basins(args) -> None:
    """Local V2 basins (the upstream walk over the bundled V2 topology, catchments joined) against
    NLDI ``get_basins`` (EASI's ``delineation.delineate_watershed``): areas and shape agreement."""
    import pyarrow.parquet as pq
    import shapely
    sys.path.insert(0, str(HERE.parents[2] / "apps" / "easi"))
    from easi import delineation
    from pyproj import Transformer
    v2_data = Path(r"D:\Data\nhdplus-hr\v2pilot\data")
    ds = Dataset2(v2_data)
    manifest = json.loads((v2_data / "manifest.json").read_text(encoding="utf-8"))
    to5070 = Transformer.from_crs(4326, 5070, always_xy=True)
    rng = np.random.default_rng(args.seed)
    print("| Region | COMID | TotDASqKM | local km2 | NLDI km2 | shape difference |")
    print("|---|---|---|---|---|---|")
    diffs = []
    for hu4, entry in sorted(manifest["vpus"].items()):
        lines = pq.read_table(v2_data / entry["lines"]["file"], columns=["nhdplusid", "totdasqkm"]).to_pandas()
        cand = lines[(lines["totdasqkm"] > 5) & (lines["totdasqkm"] < 3000)]
        for _, r in cand.iloc[rng.choice(len(cand), min(args.per_region, len(cand)), replace=False)].iterrows():
            comid = int(r["nhdplusid"])
            ws = ds.watershed(comid, max_reaches=1_000_000, max_hops=100_000)
            if ws.get("status") != "ok" or not ws.get("geometry"):
                print(f"| {hu4} | {comid} | {r['totdasqkm']:.1f} | {ws.get('status')} | | |")
                continue
            geo, area, warn = delineation.delineate_watershed(comid)
            if geo is None:
                print(f"| {hu4} | {comid} | {r['totdasqkm']:.1f} | {ws['areaSqkm']:.2f} | NLDI failed: {warn} | |")
                continue
            live = shapely.geometry.shape(geo["features"][0]["geometry"])
            mine = shapely.geometry.shape(ws["geometry"])
            lm = shapely.transform(live, lambda q: np.column_stack(to5070.transform(q[:, 0], q[:, 1])))
            mm = shapely.transform(mine, lambda q: np.column_stack(to5070.transform(q[:, 0], q[:, 1])))
            sym = shapely.area(shapely.symmetric_difference(shapely.make_valid(lm), shapely.make_valid(mm)))
            rel = sym / max(shapely.area(lm), 1)
            diffs.append(rel)
            print(f"| {hu4} | {comid} | {r['totdasqkm']:.1f} | {ws['areaSqkm']:.2f} | {area:.2f} | {100 * rel:.2f}% |",
                  flush=True)
    if diffs:
        d = np.array(diffs) * 100
        print(f"\n{len(d)} basins: shape difference median {np.median(d):.3f}%, 90th {np.percentile(d, 90):.3f}%, "
              f"max {d.max():.3f}%")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("landcover", "vectors"):
        s = sub.add_parser(name)
        s.add_argument("--values", default="values", help="folder under the v2 root: values (exact) or values_lean")
    s = sub.add_parser("points")
    s.add_argument("--per-region", type=int, default=2)
    s.add_argument("--seed", type=int, default=11)
    s = sub.add_parser("nas")
    s.add_argument("--per-region", type=int, default=2)
    s.add_argument("--seed", type=int, default=13)
    s = sub.add_parser("streamcat")
    s.add_argument("--per-region", type=int, default=100)
    s.add_argument("--seed", type=int, default=17)
    s = sub.add_parser("v2")
    s.add_argument("--per-region", type=int, default=25)
    s.add_argument("--seed", type=int, default=19)
    s = sub.add_parser("nwi")
    s.add_argument("vpus", nargs="*", default=["0108", "0206", "0307", "0710", "1711"])
    s.add_argument("--per-region", type=int, default=4)
    s.add_argument("--seed", type=int, default=23)
    s = sub.add_parser("basins")
    s.add_argument("--per-region", type=int, default=3)
    s.add_argument("--seed", type=int, default=29)
    args = ap.parse_args(argv)
    {"landcover": cmd_landcover, "vectors": cmd_vectors, "points": cmd_points, "nas": cmd_nas,
     "streamcat": cmd_streamcat, "v2": cmd_v2, "nwi": cmd_nwi, "basins": cmd_basins}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
