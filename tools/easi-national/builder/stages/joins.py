"""Per-HUC8 point-service joins that reproduce the app's four lookups from
the chunk's bulk pulls: WQP 5-mile station medians (``wqp.sample_summary``),
ATTAINS exact and nearest-within-2-km (``attains.impairment_*``), NID dams
within one mile (``nid_barriers.barriers_near``), NAS taxa by HUC12."""
from __future__ import annotations

import json
from statistics import median
from typing import Optional

from .. import config
from ..paths import DataRoot
from ..state import Control, Progress, UnitStates, digest
from ..units import Chunk
from . import common

STAGE = "joins"
WQP_PREFILTER_M = 8_500.0        # 5 mi = 8,046.7 m; the tree query is generous, haversine decides
NID_PREFILTER_M = 1_700.0

# The chunk-level lookup indexes, built once per process and reused across the
# HUC8s that process handles (a pool child runs several).
_INDEX_CACHE: dict = {}


def _cached(key, build):
    if key not in _INDEX_CACHE:
        if len(_INDEX_CACHE) > 12:
            _INDEX_CACHE.clear()
        _INDEX_CACHE[key] = build()
    return _INDEX_CACHE[key]


class _Points:
    """Points in EPSG:5070 with an STRtree, for radius prefilters."""

    def __init__(self, lats, lons):
        import geopandas as gpd
        from shapely import STRtree
        from shapely.geometry import Point
        self.n = len(lats)
        if self.n:
            series = gpd.GeoSeries([Point(x, y) for x, y in zip(lons, lats)], crs=4326).to_crs(5070)
            self.geoms = series.values
            self.tree = STRtree(self.geoms)
        else:
            self.tree = None

    def near(self, x5070, y5070, radius_m: float) -> list[int]:
        if self.tree is None:
            return []
        from shapely.geometry import Point
        return [int(i) for i in self.tree.query(Point(x5070, y5070).buffer(radius_m))]


def _project(lats, lons):
    import geopandas as gpd
    from shapely.geometry import Point
    series = gpd.GeoSeries([Point(x, y) for x, y in zip(lons, lats)], crs=4326).to_crs(5070)
    return [(g.x, g.y) for g in series.values]


# ------------------------------------------------------------------- WQP
def _wqp_index(root: DataRoot, chunk: Chunk, param: str):
    import pyarrow.parquet as pq
    path = root.chunk_raw(chunk.id, f"wqp_{param}")
    if not path.exists():
        return None
    rows = pq.read_table(path).to_pylist()
    stations: dict[str, dict] = {}
    for r in rows:
        s = stations.setdefault(r["station"], {"rows": [], "lat": None, "lon": None})
        s["rows"].append(r)
        if s["lat"] is None and r.get("lat") is not None and r.get("lon") is not None:
            s["lat"], s["lon"] = r["lat"], r["lon"]
    keys = [k for k, s in stations.items() if s["lat"] is not None]
    points = _Points([stations[k]["lat"] for k in keys], [stations[k]["lon"] for k in keys])
    return {"stations": stations, "keys": keys, "points": points}


def wqp_summary(index, param: str, lat: float, lon: float, x: float, y: float,
                radius_mi: float = config.WQP_RADIUS_MI) -> Optional[dict]:
    """``wqp.sample_summary`` from the bulk rows: stations within the radius by
    the app's haversine, then the same medians, counts, dates and exclusions."""
    from easi.datasources import wqp as app_wqp
    if index is None:
        return None
    excluded = {"blank": 0, "nonnumeric": 0, "unsupported_unit": 0,
                "non_total_fraction": 0, "rejected": 0, "censored": 0}
    by_station: dict[str, list[float]] = {}
    station_distances: dict[str, float] = {}
    dates = []
    for i in index["points"].near(x, y, WQP_PREFILTER_M):
        key = index["keys"][i]
        station = index["stations"][key]
        distance = app_wqp._distance_mi(lat, lon, station["lat"], station["lon"])
        if distance > radius_mi:
            continue
        for r in station["rows"]:
            if r["reason"] != "ok":
                excluded[r["reason"]] = excluded.get(r["reason"], 0) + 1
                continue
            by_station.setdefault(key, []).append(float(r["value"]))
            if r.get("date"):
                dates.append(r["date"])
            if r.get("lat") is not None and r.get("lon") is not None:
                d = app_wqp._distance_mi(lat, lon, r["lat"], r["lon"])
                station_distances[key] = min(d, station_distances.get(key, d))
    station_medians = {k: median(v) for k, v in by_station.items()}
    value = median(station_medians.values()) if station_medians else None
    return {
        "parameter": param,
        "value": None if value is None else round(float(value), 4),
        "units": "mg/L",
        "observation_count": sum(len(v) for v in by_station.values()),
        "station_count": len(by_station),
        "date_start": min(dates) if dates else None,
        "date_end": max(dates) if dates else None,
        "nearest_distance_mi": (round(min(station_distances.values()), 3)
                                if station_distances else None),
        "excluded_count": sum(excluded.values()),
        "excluded": excluded,
        "station_medians": {k: round(float(v), 4) for k, v in station_medians.items()},
        "query_ok": True,
    }


# --------------------------------------------------------------- ATTAINS
def _attains_index(root: DataRoot, chunk: Chunk):
    import numpy as np
    import pyarrow.parquet as pq
    path = root.chunk_raw(chunk.id, "attains")
    if not path.exists():
        return None
    table = pq.read_table(path)
    rows = table.to_pylist()
    return {"rows": rows,
            "minx": np.array([r["minx"] for r in rows]), "miny": np.array([r["miny"] for r in rows]),
            "maxx": np.array([r["maxx"] for r in rows]), "maxy": np.array([r["maxy"] for r in rows]),
            "shapes": {}}


def _attains_candidates(index, lat: float, lon: float, pad_deg: float):
    import numpy as np
    if index is None or not len(index["rows"]):
        return []
    hit = np.flatnonzero((index["minx"] <= lon + pad_deg) & (index["maxx"] >= lon - pad_deg)
                         & (index["miny"] <= lat + pad_deg) & (index["maxy"] >= lat - pad_deg))
    return [int(i) for i in hit]


def _attains_record(row: dict, *, distance_m: float, match_type: str) -> dict:
    return {"assessment_unit": row.get("assessment_unit"),
            "assessment_name": row.get("assessment_name"),
            "overallstatus": row.get("overallstatus"), "isimpaired": row.get("isimpaired"),
            "ircategory": row.get("ircategory"), "distance_m": round(float(distance_m), 1),
            "match_type": match_type, "source_layer": int(row["layer"])}


def attains_lookup(index, lat: float, lon: float) -> tuple[dict, dict]:
    """``(exact, nearby)`` as ``attains.impairment_at_point`` / ``_near_point``
    would answer from the stored assessment units."""
    from easi.datasources import attains as app_attains
    if index is None:
        return {}, {}
    buffer_m = config.ATTAINS_BUFFER_M
    pad = buffer_m / 111_000.0 * 1.5
    exact_candidates, nearby_candidates = [], []
    for i in _attains_candidates(index, lat, lon, pad):
        row = index["rows"][i]
        try:
            geometry = json.loads(row["geometry"])
        except (TypeError, ValueError):
            continue
        distance = app_attains._distance_to_geometry_m(lat, lon, geometry)
        if distance is None:
            continue
        if distance <= 0.5:                       # the point-in-feature intersect query
            exact_candidates.append((row, distance))
        if distance <= buffer_m:
            nearby_candidates.append((distance, row))
    exact = {}
    if exact_candidates:
        row, distance = min(exact_candidates,
                            key=lambda item: (str(item[0].get("assessment_unit") or ""), int(item[0]["layer"])))
        exact = _attains_record(row, distance_m=0.0, match_type="intersect")
    nearby = {}
    if nearby_candidates:
        distance, row = min(nearby_candidates,
                            key=lambda item: (item[0], str(item[1].get("assessment_unit") or ""), int(item[1]["layer"])))
        nearby = _attains_record(row, distance_m=distance, match_type="nearby")
    return exact, nearby


# ------------------------------------------------------------------- NID
def _nid_index(root: DataRoot):
    import pyarrow.parquet as pq
    if not root.nid.exists():
        return None
    rows = pq.read_table(root.nid).to_pylist()
    rows = [r for r in rows if r.get("lat") is not None and r.get("lon") is not None]
    return {"rows": rows, "points": _Points([r["lat"] for r in rows], [r["lon"] for r in rows])}


def nid_lookup(index, lat: float, lon: float, x: float, y: float,
               miles: float = config.NID_RADIUS_MI) -> Optional[list[dict]]:
    from easi.datasources import nid_barriers as app_nid
    if index is None:
        return None
    radius_m = float(miles) * 1609.344
    output = []
    for i in index["points"].near(x, y, NID_PREFILTER_M):
        r = index["rows"][i]
        distance = app_nid._distance_m(lat, lon, float(r["lat"]), float(r["lon"]))
        if distance > radius_m:
            continue
        output.append({"name": r.get("name"), "storage": r.get("storage"),
                       "height": r.get("height"), "distance_m": round(distance, 1)})
    return sorted(output, key=lambda d: (d["distance_m"], str(d.get("name") or "")))


# ------------------------------------------------------------------- NAS
def _nas_index(root: DataRoot, chunk: Chunk) -> dict[str, list[str]]:
    import pyarrow.parquet as pq
    path = root.chunk_raw(chunk.id, "nas")
    if not path.exists():
        return {}
    out = {}
    for r in pq.read_table(path).to_pylist():
        try:
            out[str(r["huc12"])] = list(json.loads(r["taxa"]))
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------- driver
def run_joins(root: DataRoot, chunk: Chunk, huc8: str, states: UnitStates,
              progress: Progress, control: Control, *, force: bool = False) -> None:
    inputs = digest(STAGE, huc8, chunk.id, config.WQP_RADIUS_MI, config.NID_RADIUS_MI,
                    config.ATTAINS_BUFFER_M, 1)
    out = root.huc8_file(huc8, "joins")

    def work():
        import pyarrow as pa
        import pyarrow.parquet as pq
        derived = pq.read_table(root.huc8_file(huc8, "derived")).to_pylist()
        wqp_idx = {p: _cached(("wqp", chunk.id, p), lambda p=p: _wqp_index(root, chunk, p))
                   for p in ("tn", "tp")}
        att_idx = _cached(("attains", chunk.id), lambda: _attains_index(root, chunk))
        nid_idx = _cached(("nid", str(root.nid)), lambda: _nid_index(root))
        nas_idx = _cached(("nas", chunk.id), lambda: _nas_index(root, chunk))
        xy = _project([r["lat"] for r in derived], [r["lon"] for r in derived])
        rows = []
        total = len(derived)
        progress.begin(huc8, STAGE, total=total, message=f"joins {huc8}: {total:,} reaches")
        for i, r in enumerate(derived):
            if i % 200 == 0:
                control.check()
                progress.tick(done=i)
            lat, lon = r["lat"], r["lon"]
            x, y = xy[i]
            exact, nearby = attains_lookup(att_idx, lat, lon)
            huc12 = r.get("huc12")
            taxa = nas_idx.get(str(huc12)) if huc12 else None
            rows.append({
                "comid": int(r["comid"]),
                "attains_exact": json.dumps(exact, separators=(",", ":")),
                "attains_nearby": json.dumps(nearby, separators=(",", ":")),
                "wqp_tn": _dump(wqp_summary(wqp_idx["tn"], "tn", lat, lon, x, y)),
                "wqp_tp": _dump(wqp_summary(wqp_idx["tp"], "tp", lat, lon, x, y)),
                "nid_dams": _dump(nid_lookup(nid_idx, lat, lon, x, y)),
                "nas_taxa": _dump(taxa), "nas_scope": "huc12" if huc12 else None,
            })
        table = pa.Table.from_pylist(rows, schema=pa.schema([
            ("comid", pa.int64()), ("attains_exact", pa.string()), ("attains_nearby", pa.string()),
            ("wqp_tn", pa.string()), ("wqp_tp", pa.string()), ("nid_dams", pa.string()),
            ("nas_taxa", pa.string()), ("nas_scope", pa.string())]))
        common.write_parquet(table, out)
        progress.say(f"{huc8} joins.parquet: {len(rows):,} reaches")

    common.run_stage(states, huc8, STAGE, inputs, work, progress, force=force)


def _dump(value) -> Optional[str]:
    return None if value is None else json.dumps(value, separators=(",", ":"))
