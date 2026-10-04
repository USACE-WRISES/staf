"""Accuracy on real metrics: the STAF site engine's watershed metric families run
on the original USGS watershed and on the slim watersheds (each catchment
tolerance), for the engine's walk panel and a set of small headwater sites.

The original watershed is the union of the tree's catchments as the USGS service
serves them (asked by id, the service's fast query); the tree's flowlines come
from the same place. The slim watershed and flowlines come from the slim files.
Everything else (NLCD, TIGER roads, NID, SSURGO, the base-flow grid) is the
engine's own code and services, unchanged.

    python tools/hr-slim/scripts/metric_accuracy.py [--headwaters 2] [--workers 3]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
import shapely

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from hrbuild import REPO_ROOT, config  # noqa: E402
from hrslim import Dataset, fmt, line_features  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "libs" / "site_engine"))
from site_engine import metrics as engine_metrics  # noqa: E402

SERVICE = "https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer"
FAMILIES = ["baseflow", "dams", "landcover", "roads", "soils"]
#: The engine's walk panel (libs/site_engine/scripts/walk_equivalence.py).
PANEL = [
    ("Ohio (Waldo) trib", 24000800011818),
    ("Great Lakes (MI)", 60001800017690),
    ("Mid-Atlantic (PA)", 10000600001253),
    ("South Atlantic (GA)", 15001600099044),
    ("Mountain West (CO)", 41000600139191),
    ("Ozarks (AR/OK)", 21000200078589),
    ("Pacific Northwest (WA)", 55000800088012),
]


def _service(layer: int, ids: list[int], fields: str) -> list[dict]:
    out = []
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        where = "nhdplusid IN (" + ",".join(str(v) for v in chunk) + ")"
        for attempt in range(4):
            try:
                r = requests.post(f"{SERVICE}/{layer}/query", timeout=180, data={
                    "where": where, "outFields": fields, "returnGeometry": "true",
                    "outSR": "4326", "f": "geojson"})
                r.raise_for_status()
                out.extend(r.json().get("features") or [])
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))
    return out


def _albers():
    from pyproj import Transformer
    return (Transformer.from_crs(4326, 5070, always_xy=True),
            Transformer.from_crs(5070, 4326, always_xy=True))


def _union_fc(geoms: list) -> tuple[dict, float]:
    fwd, back = _albers()
    proj = lambda g, t: shapely.transform(g, lambda c: np.column_stack(t.transform(c[:, 0], c[:, 1])))  # noqa: E731
    g5070 = proj(np.asarray(geoms, dtype=object), fwd)
    try:
        u = shapely.union_all(g5070)
    except shapely.errors.GEOSException:
        u = shapely.union_all(shapely.make_valid(g5070))
    area = round(float(u.area) / 1e6, 4)
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": json.loads(shapely.to_geojson(proj(u, back)))}]}
    return fc, area


def _values(entries: dict) -> dict:
    return dict((k, v.get("value")) for k, v in entries.items()
                if isinstance(v, dict) and isinstance(v.get("value"), (int, float)))


def run_site(ds: Dataset, label: str, nid: int) -> dict:
    tree = ds.upstream_tree(nid, max_reaches=3000, max_hops=190)
    if tree["status"] != "ok":
        return {"label": label, "nhdplusid": nid, "status": tree["status"], "reason": tree.get("reason")}
    ids = tree["ids"]
    reach = line_features(ds.reach(nhdplusid=nid))[0]
    mid = shapely.line_interpolate_point(shapely.geometry.shape(reach["geometry"]), 0.5, normalized=True)
    site = {"snapLat": round(mid.y, 6), "snapLon": round(mid.x, 6)}
    out = {"label": label, "nhdplusid": nid, "status": "ok", "reaches": len(ids),
           "vaaAreaSqkm": reach["properties"]["totdasqkm"], "variants": {}}
    # original: the USGS service's catchments and flowlines for the same tree
    cats = _service(10, ids, "nhdplusid")
    lines = _service(3, ids, "nhdplusid")
    fc, area = _union_fc([shapely.geometry.shape(f["geometry"]) for f in cats])
    variants = {"original": (fc, area, [f["geometry"] for f in lines])}
    slim_lines = [f["geometry"] for f in line_features(ds.flowlines_by_ids(ids), attributes=False)]
    for tol in ds.tolerances:
        ws = ds.watershed(nid, tol, max_reaches=3000, max_hops=190)
        variants[f"{tol:g}m"] = ({"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {}, "geometry": ws["geometry"]}]}, ws["areaSqkm"], slim_lines)
    for name, (polygon, area_sqkm, tree_geoms) in variants.items():
        record = {"site": site, "watershed": {"polygon": polygon, "areaSqkm": area_sqkm},
                  "input": {"config": {}}}
        t0 = time.time()
        vals = _values(engine_metrics.compute_all(record, tree_geoms=tree_geoms, families=FAMILIES))
        out["variants"][name] = {"areaSqkm": area_sqkm, "seconds": round(time.time() - t0, 1), "values": vals}
    return out


def summarize(results: list[dict]) -> str:
    rows = []
    diffs: dict = {}
    for r in results:
        if r.get("status") != "ok":
            rows.append(f"{r['label'][:24]:24}  {r['status']}: {r.get('reason')}")
            continue
        o = r["variants"]["original"]
        line = f"{r['label'][:24]:24} {r['reaches']:5d} reaches {o['areaSqkm']:9.2f} km2"
        for name, v in r["variants"].items():
            if name == "original":
                continue
            worst = 0.0
            for k, val in v["values"].items():
                ov = o["values"].get(k)
                if ov is None:
                    continue
                d = abs(val - ov)
                diffs.setdefault((name, k), []).append(d)
                worst = max(worst, d if "Pct" in k or "pct" in k.lower() else 0.0)
            line += f" | {name}: area {100 * abs(v['areaSqkm'] - o['areaSqkm']) / o['areaSqkm']:.2f}%, worst pct-point diff {worst:.2f}"
        rows.append(line)
    rows.append("")
    rows.append("Per metric, absolute difference from the original (median / max over sites):")
    for (name, k) in sorted(diffs, key=lambda x: (x[1], x[0])):
        d = np.asarray(diffs[(name, k)])
        rows.append(f"  {k:42} {name:>5}: {np.median(d):10.4f} / {d.max():10.4f}")
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headwaters", type=int, default=2, help="small headwater sites per VPU")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args(argv)
    root = config.data_root()
    ds = Dataset(root / "data")
    sites = [(label, nid) for label, nid in PANEL]
    rng = np.random.default_rng(11)
    import pyarrow.parquet as pq
    for vpu in ds.vpus:
        t = pq.read_table(root / "data" / fmt.lines_file(vpu), columns=["nhdplusid", "totdasqkm", "streamorde"])
        da = t.column("totdasqkm").to_numpy()
        ids = t.column("nhdplusid").to_numpy()
        cand = np.nonzero((da > 0.3) & (da < 3.0))[0]
        for i in rng.choice(cand, size=min(args.headwaters, cand.size), replace=False):
            sites.append((f"headwater {vpu} ({da[i]:.2f} km2)", int(ids[i])))
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda s: _safe(ds, *s), sites))
    path = root / "metric_accuracy.json"
    path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(summarize(results))
    print(f"\n{len(sites)} sites in {time.time() - t0:.0f} s; written {path}")
    return 0


def _safe(ds, label, nid):
    try:
        res = run_site(ds, label, nid)
    except Exception as exc:
        res = {"label": label, "nhdplusid": nid, "status": "error", "reason": str(exc)[:200]}
    print(f"  done {label}: {res.get('status')}", flush=True)
    return res


if __name__ == "__main__":
    sys.exit(main())
