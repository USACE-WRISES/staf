"""Parity: the live EASI path against the precomputed record, reach by reach.

``xs_mode`` sets how the live path reads elevation for the four cross-section
metrics: ``"off"`` (the four are expected to differ: Tier 1 records carry no
cross-sections), ``"best"`` (the live app unchanged: 1 m where the 3DEP index
says so, else 10 m) or ``"10m"`` (the live path forced to the 10 m seamless,
an exact check of the arithmetic and the reach line). Both modes also record
whether the index offered 1 m for each reach, and the Hausdorff distance
between the stored reach line and the live one."""
from __future__ import annotations

import asyncio
import contextlib
import random
from typing import Optional

XS_METRICS = (
    "floodplain-connectivity-floodplain-access-entrenchment",
    "high-flow-dynamics-floodplain-engagement-frequency-bankfull-recurrence",
    "channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition",
    "channel-evolution-channel-evolution-stage-and-trends",
)


@contextlib.contextmanager
def live_dem_mode(mode: str, seen: dict):
    """Temporarily steer the live app's elevation source; ``seen`` receives
    ``one_metre`` (what the 3DEP index said for the last buffer)."""
    from easi.datasources import threedep
    original = threedep._best_available_dem
    if mode == "off":
        yield
        return

    def _availability(buf4326):
        try:
            import py3dep
            avail = py3dep.check_3dep_availability(tuple(buf4326.bounds))
            seen["one_metre"] = None if isinstance(avail, str) else bool(avail and avail.get("1m") is True)
        except Exception:  # noqa: BLE001 - provenance only
            seen["one_metre"] = None

    def forced_10m(buf4326):
        import py3dep
        _availability(buf4326)
        return py3dep.get_dem(buf4326, resolution=10).rio.reproject(5070), 10

    def best(buf4326):
        _availability(buf4326)
        return original(buf4326)

    threedep._best_available_dem = forced_10m if mode == "10m" else best
    try:
        yield
    finally:
        threedep._best_available_dem = original


def live_resolution(cross_section: Optional[dict]) -> Optional[int]:
    """The DEM resolution the live report used, from its section block."""
    geom = (cross_section or {}).get("geom") or {}
    value = geom.get("dem_resolution_m")
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def xs_compare(offline_geom: Optional[dict], live_cross_section: Optional[dict]) -> dict:
    """The offline record's medians against the live report's."""
    offline = offline_geom or {}
    live = live_cross_section or {}
    off = {"er": offline.get("entrenchment_ratio"), "bhr": offline.get("bank_height_ratio"),
           "n": offline.get("n_transects"), "res": offline.get("dem_resolution_m")}
    lv = {"er": live.get("entrenchment_ratio"), "bhr": live.get("bank_height_ratio"),
          "n": live.get("n_transects"), "res": live_resolution(live)}

    def diff(a, b):
        return round(abs(float(a) - float(b)), 3) if a is not None and b is not None else None
    return {"offline": off, "live": lv, "er_diff": diff(off["er"], lv["er"]),
            "bhr_diff": diff(off["bhr"], lv["bhr"]), "res_same": off["res"] == lv["res"]}


def reach_hausdorff_m(stored_geometry, live_reach_geojson: Optional[dict]) -> Optional[float]:
    """Hausdorff distance (EPSG:5070) between the stored reach line and the
    live one, None when either is missing."""
    if stored_geometry is None or not live_reach_geojson:
        return None
    try:
        import geopandas as gpd
        from shapely.geometry import shape
        features = live_reach_geojson.get("features") or []
        if not features:
            return None
        live = shape(features[0]["geometry"])
        pair = gpd.GeoSeries([stored_geometry, live], crs="EPSG:4326").to_crs("EPSG:5070")
        return round(float(pair.iloc[0].hausdorff_distance(pair.iloc[1])), 1)
    except Exception:  # noqa: BLE001 - diagnostics only
        return None


def parity(root, huc8: str, *, n: int = 10, seed: int = 1, xs_mode: str = "off",
           reach_check: bool = True) -> dict:
    """Score ``n`` random reaches of a scored HUC8 live (NLDI + services) and
    from their stored evidence; compare every metric's rating and the rollup."""
    import pyarrow.parquet as pq
    from easi import pipeline
    from easi.national import client, records
    path = root.huc8_file(huc8, "evidence")
    rows = pq.read_table(path).to_pylist()
    rng = random.Random(seed)
    sample = rng.sample(rows, min(n, len(rows)))
    stored_reaches: dict = {}
    reaches_path = root.huc8_file(huc8, "reaches")
    if reach_check and reaches_path.exists():
        import geopandas as gpd
        frame = gpd.read_parquet(reaches_path)
        stored_reaches = {int(c): g for c, g in zip(frame["comid"].tolist(), frame.geometry.tolist())}
    results = []
    for row in sample:
        record = records.from_row(row)
        offline = client.score_record(record, cross_section=False)
        seen: dict = {}
        with live_dem_mode(xs_mode, seen):
            live_delin = asyncio.run(pipeline.delineate_only(record["lat"], record["lon"],
                                                             comid=int(record["comid"])))
            if live_delin.get("status") != "ok":
                results.append({"comid": record["comid"], "error": live_delin.get("message")})
                continue
            reach_geojson = live_delin.get("reach_geojson")
            live = asyncio.run(pipeline.assess_only(live_delin.pop("ctx_inputs"), prefetch=False))["report"]
        result = _compare(record["comid"], live, offline)
        if xs_mode != "off":
            result["xs"] = xs_compare(record.get("geomorph") if isinstance(record.get("geomorph"), dict) else None,
                                      live.get("crossSection"))
            result["xs"]["live_one_metre"] = seen.get("one_metre")
            result["reach_hausdorff_m"] = reach_hausdorff_m(stored_reaches.get(int(record["comid"])), reach_geojson)
        results.append(result)
    return {"huc8": huc8, "n": len(sample), "xs_mode": xs_mode, "results": results}


def _compare(comid: int, live: dict, offline: dict) -> dict:
    live_rows = {r["metricId"]: r for r in live.get("metricRows", [])}
    diffs = []
    for r in offline.get("metricRows", []):
        l = live_rows.get(r["metricId"]) or {}
        if l.get("rating") != r.get("rating"):
            diffs.append({"metric": r["functionId"], "metric_id": r["metricId"], "live": l.get("rating"),
                          "offline": r.get("rating"), "live_source": l.get("source"),
                          "offline_source": r.get("source")})
    return {"comid": comid, "eci_live": live.get("ecosystemConditionIndex"),
            "eci_offline": offline.get("ecosystemConditionIndex"), "diffs": diffs,
            "xs_identical": not any(d.get("metric_id") in XS_METRICS for d in diffs)}


def _median(values: list) -> Optional[float]:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else round((values[mid - 1] + values[mid]) / 2, 3)


def format_report(report: dict) -> str:
    lines = [f"HUC8 {report['huc8']}: {report['n']} reaches"
             + (f", cross-sections compared with the live path on {report['xs_mode']}"
                if report.get("xs_mode", "off") != "off" else "")]
    agree = 0
    per_metric: dict[str, int] = {}
    xs_same = xs_total = res_same = one_metre = 0
    er_diffs: list = []
    bhr_diffs: list = []
    hausdorff: list = []
    for r in report["results"]:
        if r.get("error"):
            lines.append(f"  {r['comid']}: live run failed ({r['error']})")
            continue
        if not r["diffs"]:
            agree += 1
        for d in r["diffs"]:
            per_metric[d["metric"]] = per_metric.get(d["metric"], 0) + 1
        extra = ""
        xs = r.get("xs")
        if xs:
            xs_total += 1
            xs_same += int(r.get("xs_identical", False))
            res_same += int(bool(xs.get("res_same")))
            one_metre += int(bool(xs.get("live_one_metre")))
            if xs.get("res_same"):
                er_diffs.append(xs.get("er_diff"))
                bhr_diffs.append(xs.get("bhr_diff"))
            hausdorff.append(r.get("reach_hausdorff_m"))
            extra = (f"; ER {xs['offline']['er']} vs {xs['live']['er']}, BHR {xs['offline']['bhr']} vs "
                     f"{xs['live']['bhr']}, DEM {xs['offline']['res']} vs {xs['live']['res']} m"
                     + (f", reach {r['reach_hausdorff_m']} m" if r.get("reach_hausdorff_m") is not None else ""))
        lines.append(f"  {r['comid']}: ECI live {r['eci_live']} offline {r['eci_offline']}"
                     + (f"; differs on {', '.join(d['metric'] for d in r['diffs'])}" if r["diffs"] else "; identical")
                     + extra)
    lines.append(f"identical reaches: {agree}/{report['n']}")
    for metric, count in sorted(per_metric.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {metric}: {count} differences")
    if xs_total:
        lines.append(f"cross-section ratings identical: {xs_same}/{xs_total}; same DEM resolution: "
                     f"{res_same}/{xs_total}; live would use 1 m on {one_metre}/{xs_total}")
        lines.append(f"  median ER difference {_median(er_diffs)}, median BHR difference {_median(bhr_diffs)} "
                     f"(same resolution only); median reach Hausdorff {_median(hausdorff)} m")
    return "\n".join(lines)
