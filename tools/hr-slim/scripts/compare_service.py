"""Compare the slim files with the live USGS NHDPlus HR service, feature by feature.

For each converted VPU, takes the flowline and catchment ids in its QA boxes (the
original full-precision geometry is stored there), asks the USGS MapServer for
the same ids (lookups by id are the service's fast query), and reports:

- attribute differences for the 15 fields the engine reads (service vs slim);
- coordinate differences service vs original package geometry (a datum shift or
  rounding in the service would show here) and service vs slim (simplification);
- catchment area differences service vs slim.

    python tools/hr-slim/scripts/compare_service.py [VPU ...]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import requests
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hrbuild import config  # noqa: E402
from hrslim import Dataset, fmt  # noqa: E402

SERVICE = "https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer"
FIELDS = [f for f in fmt.SERVICE_FIELDS]


def ask(layer: int, ids: list[int], fields: str) -> dict:
    where = "nhdplusid IN (" + ",".join(str(i) for i in ids) + ")"
    for attempt in range(3):
        try:
            r = requests.post(f"{SERVICE}/{layer}/query",
                              data={"where": where, "outFields": fields, "returnGeometry": "true",
                                    "outSR": "4326", "f": "geojson"}, timeout=120)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # the service is erratic; try a little, then report
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"service did not answer: {last}")


def main(argv: list[str]) -> int:
    root = config.data_root()
    ds = Dataset(root / "data")
    vpus = argv or ds.vpus
    from pyproj import Transformer
    to5070 = Transformer.from_crs(4269, 5070, always_xy=True)
    out = {}
    for vpu in vpus:
        entry = ds.manifest["vpus"][vpu]
        if not entry.get("qa"):
            continue
        qa = pq.read_table(root / "data" / entry["qa"]["file"]).to_pylist()
        orig = dict(((r["kind"], r["nhdplusid"]), shapely.from_wkb(r["wkb"])) for r in qa)
        line_ids = sorted({r["nhdplusid"] for r in qa if r["kind"] == "line"})[:60]
        cat_ids = sorted({r["nhdplusid"] for r in qa if r["kind"] == "catchment"})[:30]
        t0 = time.time()
        svc_lines = ask(3, line_ids, ",".join(FIELDS))
        svc_cats = ask(10, cat_ids, "nhdplusid,areasqkm")
        secs = round(time.time() - t0, 1)
        slim = dict((f["properties"]["nhdplusid"], f) for f in
                    __import__("hrslim").line_features(ds.flowlines_by_ids(line_ids, attributes=True)))
        attr_diff = dict((k, 0.0) for k in FIELDS)
        mismatched = dict((k, 0) for k in FIELDS)
        geo_orig, geo_slim, missing = [], [], 0
        order = [int(round(float(f["properties"]["nhdplusid"]))) for f in svc_lines.get("features", [])]
        for f in svc_lines.get("features", []):
            p = f["properties"]
            nid = int(round(float(p["nhdplusid"])))
            s = slim.get(nid)
            if s is None:
                missing += 1
                continue
            for k in FIELDS:
                a, b = p.get(k), s["properties"].get(k)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    d = abs(float(a) - float(b))
                    attr_diff[k] = max(attr_diff[k], d)
                    if d > 1e-6 * max(1.0, abs(float(b))):
                        mismatched[k] += 1
                elif (a or None) != (b or None) and str(a).strip() != str(b).strip():
                    mismatched[k] += 1
            g = shapely.geometry.shape(f["geometry"])
            o = orig.get(("line", nid))
            if o is not None:
                geo_orig.append(float(np.abs(shapely.get_coordinates(g)
                                             - shapely.get_coordinates(shapely.force_2d(o))).max())
                                if shapely.get_num_coordinates(g) == shapely.get_num_coordinates(o) else np.nan)
            sg = shapely.geometry.shape(s["geometry"])
            geo_slim.append(float(shapely.hausdorff_distance(
                shapely.transform(g, lambda c: np.column_stack(to5070.transform(c[:, 0], c[:, 1]))),
                shapely.transform(sg, lambda c: np.column_stack(to5070.transform(c[:, 0], c[:, 1]))))))
        area = []
        for f in svc_cats.get("features", []):
            nid = int(round(float(f["properties"]["nhdplusid"])))
            g = shapely.geometry.shape(f["geometry"])
            o = orig.get(("catchment", nid))
            if o is not None:
                a0 = shapely.area(shapely.transform(o, lambda c: np.column_stack(to5070.transform(c[:, 0], c[:, 1]))))
                a1 = shapely.area(shapely.transform(g, lambda c: np.column_stack(to5070.transform(c[:, 0], c[:, 1]))))
                area.append(abs(a1 - a0) / a0)
        res = {
            "service_seconds": secs,
            "lines_asked": len(line_ids), "lines_answered": len(svc_lines.get("features", [])),
            "missing_in_slim": missing,
            "attribute_mismatches": dict((k, v) for k, v in mismatched.items() if v),
            "attribute_max_abs_diff": dict((k, v) for k, v in attr_diff.items() if v),
            "coords_service_vs_original_max_deg": float(np.nanmax(geo_orig)) if geo_orig else None,
            "vertex_count_mismatch_vs_original": int(np.isnan(geo_orig).sum()) if geo_orig else None,
            "service_vs_slim_hausdorff_m": {"median": round(float(np.median(geo_slim)), 2),
                                            "max": round(float(np.max(geo_slim)), 2)} if geo_slim else None,
            "catchments_asked": len(cat_ids), "catchments_answered": len(svc_cats.get("features", [])),
            "catchment_area_service_vs_original_max_rel": float(max(area)) if area else None,
            "service_order_sorted_by_id": order == sorted(order),
        }
        out[vpu] = res
        print(vpu, json.dumps(res), flush=True)
    path = root / "compare_service.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("written", path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
