"""Probe the NHDPlus HR flowline service before load-bearing use (plan gate G0).

EASI's full-NHD coverage work reads the NetworkNHDFlowline layer of the
NHDPlus_HR MapServer for two things: display vectors for the map (bbox queries)
and per-reach value-added attributes for routing and re-anchoring (by-id
queries). HR VAAs have had beta-quality regions, so this harness samples one
point per major VPU region and reports:

  * field contract — every required attribute exists on the layer's rows
  * quality — null/sentinel rates (slope=-9998, totdasqkm<=0) per region
  * innetwork / visibilityfilter distributions (the display filter's basis)
  * exceededTransferLimit frequency at the app's zoom-14 bbox size
  * latency per query (p50/p95 in the summary)
  * nhdplusid float64 -> int round-trip stability (ids must sit below 2^53)
  * uphydroseq chain resolution (fetch-by-hydroseq returns exactly one feature)

Writes a JSON report to a gitignored out/ dir and prints a summary. Exits
nonzero when a required field is missing from the service, so the output can
gate the production code paths. This is a diagnostic harness — it changes no
production code path.

Examples:
  python scripts/probe_nhd_hr.py                # all sample regions
  python scripts/probe_nhd_hr.py --limit 3      # quick smoke
  python scripts/probe_nhd_hr.py --timeout 60
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

URL = ("https://hydro.nationalmap.gov/arcgis/rest/services/"
       "NHDPlus_HR/MapServer/3/query")

# Fields the production code relies on (easi/datasources/nhd_hr.py).
REQUIRED_FIELDS = (
    "nhdplusid", "gnis_name", "reachcode", "lengthkm", "totdasqkm", "slope",
    "fcode", "ftype", "streamorde", "hydroseq", "uphydroseq", "dnhydroseq",
    "vpuid", "innetwork", "visibilityfilter",
)

SLOPE_SENTINEL = -9998.0

# The app fetches display vectors on a center-radius box of +-0.06 deg at zoom 14.
DISPLAY_HALF_DEG = 0.06
# Attribute-quality sampling uses a smaller box so the stats query stays under
# the 2000-record cap in dense regions.
STATS_HALF_DEG = 0.02

# label, lat, lon — one sample point per major NHDPlus HR VPU region (CONUS).
SAMPLE_POINTS = [
    ("VPU01 New England (ME)", 44.30, -70.50),
    ("VPU02 Mid-Atlantic (PA)", 40.80, -76.50),
    ("VPU03 South Atlantic (GA)", 33.50, -83.30),
    ("VPU04 Great Lakes (MI)", 43.50, -84.60),
    ("VPU05 Ohio (Worthington OH)", 40.0962, -83.0203),
    ("VPU06 Tennessee (TN)", 35.80, -84.20),
    ("VPU07 Upper Mississippi (WI/IA)", 43.20, -91.50),
    ("VPU08 Lower Mississippi (LA/MS)", 32.50, -91.00),
    ("VPU09 Souris-Red-Rainy (MN/ND)", 47.80, -96.50),
    ("VPU10 Missouri upper (SD)", 44.50, -103.50),
    ("VPU10 Missouri lower (KS)", 39.00, -96.50),
    ("VPU11 Arkansas-White-Red (AR/OK)", 35.50, -94.50),
    ("VPU12 Texas (TX)", 30.30, -98.00),
    ("VPU13 Rio Grande (NM)", 33.90, -105.50),
    ("VPU14 Upper Colorado (CO)", 39.00, -107.50),
    ("VPU15 Lower Colorado (AZ)", 34.20, -111.50),
    ("VPU16 Great Basin (UT)", 40.50, -111.80),
    ("VPU17 Pacific Northwest (WA)", 46.90, -122.20),
    ("VPU18 California (CA)", 38.80, -121.00),
]


def _get(params: dict, timeout: float) -> tuple[dict | None, float, str | None]:
    """One GET with a single retry. Returns (payload, seconds, error)."""
    err = None
    for attempt in range(2):
        t0 = time.time()
        try:
            r = requests.get(URL, params=params, timeout=timeout)
            secs = time.time() - t0
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and "error" in data:
                    err = f"service error: {data['error']}"
                else:
                    return data, secs, None
            else:
                err = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001 - diagnostic harness
            secs = time.time() - t0
            err = repr(exc)
        time.sleep(0.5 * (attempt + 1))
    return None, secs, err


def _exceeded(payload: dict | None) -> bool:
    if not payload:
        return False
    if payload.get("exceededTransferLimit"):
        return True
    props = payload.get("properties")
    return bool(isinstance(props, dict) and props.get("exceededTransferLimit"))


def _bbox(lat: float, lon: float, half: float) -> str:
    return f"{lon - half},{lat - half},{lon + half},{lat + half}"


def probe_point(label: str, lat: float, lon: float, timeout: float) -> dict:
    out: dict = {"label": label, "lat": lat, "lon": lon}

    # 1) Display-shaped query: the exact request the map layer will make.
    disp, secs, err = _get({
        "geometry": _bbox(lat, lon, DISPLAY_HALF_DEG),
        "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "where": "innetwork=1",
        "outFields": "nhdplusid", "returnGeometry": "true",
        "outSR": "4326", "maxAllowableOffset": "0.0001",
        "geometryPrecision": "5", "f": "geojson"}, timeout)
    out["display"] = {
        "seconds": round(secs, 2), "error": err,
        "n_features": len((disp or {}).get("features") or []),
        "exceeded_transfer_limit": _exceeded(disp),
    }

    # 2) Attribute-quality query: full field list, no geometry, no filter.
    stats, secs2, err2 = _get({
        "geometry": _bbox(lat, lon, STATS_HALF_DEG),
        "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "where": "1=1",
        "outFields": ",".join(REQUIRED_FIELDS), "returnGeometry": "false",
        "f": "json"}, timeout)
    rows = [f.get("attributes") or {} for f in (stats or {}).get("features") or []]
    fields_seen = {f.get("name") for f in (stats or {}).get("fields") or []}
    q: dict = {"seconds": round(secs2, 2), "error": err2, "n_rows": len(rows),
               "exceeded_transfer_limit": _exceeded(stats),
               "missing_fields": sorted(set(REQUIRED_FIELDS) - fields_seen) if fields_seen else None}
    if rows:
        n = len(rows)
        net = [r for r in rows if r.get("innetwork") == 1]
        q["innetwork_share"] = round(len(net) / n, 3)
        vis: dict[str, int] = {}
        for r in rows:
            key = str(r.get("visibilityfilter"))
            vis[key] = vis.get(key, 0) + 1
        q["visibilityfilter_counts"] = vis
        base = net or rows
        nb = len(base)
        q["null_rates_innetwork"] = {
            "slope_null_or_sentinel": round(sum(
                1 for r in base
                if r.get("slope") is None or float(r.get("slope") or 0) <= SLOPE_SENTINEL
                or float(r.get("slope") or 0) < 0) / nb, 3),
            "totdasqkm_null_or_nonpos": round(sum(
                1 for r in base
                if r.get("totdasqkm") is None or float(r.get("totdasqkm") or 0) <= 0) / nb, 3),
            "uphydroseq_null_or_zero": round(sum(
                1 for r in base if not r.get("uphydroseq")) / nb, 3),
        }
        # nhdplusid float64 round-trip: int(round(float(x))) must be stable and < 2^53
        bad_ids = 0
        for r in rows:
            v = r.get("nhdplusid")
            if v is None:
                bad_ids += 1
                continue
            iv = int(round(float(v)))
            if iv >= 2 ** 53 or abs(float(v) - iv) > 0.0001:
                bad_ids += 1
        q["id_roundtrip_failures"] = bad_ids

        # 3) uphydroseq chain: one fetch-by-hydroseq must return exactly one row.
        chain = next((r for r in base if r.get("uphydroseq")), None)
        if chain is not None:
            up = int(round(float(chain["uphydroseq"])))
            cres, secs3, err3 = _get({
                "where": f"hydroseq = {up}",
                "outFields": "nhdplusid,hydroseq", "returnGeometry": "false",
                "f": "json"}, timeout)
            nfound = len((cres or {}).get("features") or [])
            q["uphydroseq_chain"] = {"queried": up, "n_found": nfound,
                                     "seconds": round(secs3, 2), "error": err3,
                                     "ok": nfound == 1}
    out["quality"] = q
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=None,
                    help="probe only the first N sample points")
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = ap.parse_args()

    points = SAMPLE_POINTS[: args.limit] if args.limit else SAMPLE_POINTS
    results = []
    for label, lat, lon in points:
        print(f"probing {label} ...", flush=True)
        results.append(probe_point(label, lat, lon, args.timeout))

    latencies = [r["display"]["seconds"] for r in results
                 if not r["display"]["error"]]
    missing = sorted({m for r in results
                      for m in (r["quality"].get("missing_fields") or [])})
    summary = {
        "n_points": len(results),
        "display_errors": sum(1 for r in results if r["display"]["error"]),
        "display_exceeded": sum(1 for r in results
                                if r["display"]["exceeded_transfer_limit"]),
        "latency_p50_s": round(statistics.median(latencies), 2) if latencies else None,
        "latency_p95_s": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 2)
        if latencies else None,
        "missing_fields": missing,
        "id_roundtrip_failures": sum(r["quality"].get("id_roundtrip_failures", 0)
                                     for r in results),
        "chain_failures": [r["label"] for r in results
                           if r["quality"].get("uphydroseq_chain")
                           and not r["quality"]["uphydroseq_chain"]["ok"]],
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "probe_nhd_hr.json"
    out_path.write_text(json.dumps({"summary": summary, "results": results},
                                   indent=2), encoding="utf-8")

    print("\n== summary ==")
    print(json.dumps(summary, indent=2))
    for r in results:
        q = r["quality"]
        nulls = q.get("null_rates_innetwork") or {}
        print(f"  {r['label']}: display {r['display']['n_features']} feats "
              f"in {r['display']['seconds']}s"
              f"{' (EXCEEDED)' if r['display']['exceeded_transfer_limit'] else ''}; "
              f"rows {q.get('n_rows')}, innetwork {q.get('innetwork_share')}, "
              f"slope-null {nulls.get('slope_null_or_sentinel')}, "
              f"da-null {nulls.get('totdasqkm_null_or_nonpos')}")
    print(f"\nreport: {out_path}")

    if missing:
        print(f"FIELD CONTRACT FAILURE: missing {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
