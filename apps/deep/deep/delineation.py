"""Point -> snapped COMID -> upstream watershed -> assessment reach.

Verified engine choices (see plan.md):
  * snap:        pynhd NLDI.comid_byloc((lon,lat))  (+ flow_trace fallback)
  * watershed:   pynhd NLDI.get_basins([comid]) — the NHD reach's contributing
                 basin. This is exactly the watershed EASI's StreamCat metrics are
                 computed for, so basin and scoring stay consistent. (Point-precise
                 split_catchment was tried but is unreliable — it returns either the
                 local catchment only or the whole reach-outlet basin — so it is not
                 used.)
  * reach:       comid flowline + NLDI.navigate_byid('upstreamMain') geometry,
                 merged, reprojected to EPSG:5070, then trimmed to ~length_ft
                 *upstream of the snapped point* (anchored at the point's projection
                 along the line) with shapely substring.
  * context:     WaterData('nhdflowline_network').byid -> totdasqkm, gnis_name.

All distance math happens in EPSG:5070 (Albers, metres); inputs/outputs are
EPSG:4326. Heavy imports are local so the rest of the package stays importable
without the geospatial stack. Run ``python -m easi.delineation`` for a live
diagnostic on a known point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

FT_PER_M = 3.28083989501312
DEFAULT_REACH_FT = 1000.0
CRS_WGS84 = 4326
CRS_ALBERS = 5070  # USGS CONUS Albers Equal Area (metres)


@dataclass
class Delineation:
    lat: float
    lon: float
    comid: Optional[int] = None
    gnis_name: Optional[str] = None
    drainage_area_sqkm: Optional[float] = None
    huc8: Optional[str] = None
    slope: Optional[float] = None
    fcode: Optional[int] = None
    stream_order: Optional[int] = None
    sinuosity: Optional[float] = None
    snapped_lat: Optional[float] = None
    snapped_lon: Optional[float] = None
    watershed_geojson: Optional[dict] = None
    watershed_area_sqkm: Optional[float] = None
    reach_geojson: Optional[dict] = None
    reach_length_ft: Optional[float] = None
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _albers(gdf):
    return gdf.to_crs(CRS_ALBERS)


def _largest_polygon(gdf):
    """Return the single largest (by area) polygon geometry in a GeoDataFrame."""
    g = _albers(gdf)
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    if g.empty:
        return None, 0.0
    areas = g.geometry.area
    idx = areas.idxmax()
    area_sqkm = float(areas.loc[idx]) / 1e6
    return gdf.loc[idx].geometry, area_sqkm


def display_simplify(geojson: Optional[dict], max_vertices: int = 1500,
                     tol_deg: float = 0.0002) -> Optional[dict]:
    """Display-only simplification of a polygon FeatureCollection.

    Returns ``geojson`` unchanged when it has <= ``max_vertices`` (normal reaches
    stay fully faithful to the NHDPlus catchment boundary); otherwise simplifies
    the geometry (~22 m at ``tol_deg``, topology-preserving) so very large river
    basins render on the map without overwhelming the widget. Callers keep the
    full-resolution geometry for the reported area and exports. Never raises.
    """
    if not geojson or not geojson.get("features"):
        return geojson
    try:
        import geopandas as gpd
        g = gpd.GeoDataFrame.from_features(geojson["features"], crs=CRS_WGS84)
        if int(g.geometry.count_coordinates().sum()) <= max_vertices:
            return geojson
        g = g.copy()
        g["geometry"] = g.geometry.simplify(tol_deg, preserve_topology=True)
        return g.__geo_interface__
    except Exception:  # noqa: BLE001 - display nicety; fall back to full geometry
        return geojson


def geojson_bounds(*geojsons, pad: float = 0.06):
    """Combined bounds of one or more FeatureCollections as ``[[S, W], [N, E]]``
    (the format for ``ipyleaflet.Map.fit_bounds``), with a small margin. Returns
    None if nothing usable. Never raises.
    """
    try:
        import geopandas as gpd
        boxes = []
        for gj in geojsons:
            if gj and gj.get("features"):
                gdf = gpd.GeoDataFrame.from_features(gj["features"], crs=CRS_WGS84)
                gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
                if not gdf.empty:
                    boxes.append(gdf.total_bounds)  # (minx, miny, maxx, maxy)
        if not boxes:
            return None
        minx = min(b[0] for b in boxes); miny = min(b[1] for b in boxes)
        maxx = max(b[2] for b in boxes); maxy = max(b[3] for b in boxes)
        dx = (maxx - minx) * pad or 0.001
        dy = (maxy - miny) * pad or 0.001
        return [[miny - dy, minx - dx], [maxy + dy, maxx + dx]]
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def flowline_attrs(comid: int) -> dict:
    """NHDPlus attributes for a COMID (gnis_name, drainage area, huc8, slope,
    fcode, stream order, sinuosity). Best-effort; never raises."""
    from pynhd import WaterData

    out: dict[str, Any] = {"gnis_name": None, "drainage_area_sqkm": None, "huc8": None,
                           "slope": None, "fcode": None, "stream_order": None,
                           "sinuosity": None}
    try:
        fl = WaterData("nhdflowline_network").byid("comid", [str(comid)])
        row = fl.iloc[0]
        for c in ("gnis_name", "GNIS_NAME"):
            if c in fl.columns and row.get(c):
                out["gnis_name"] = str(row[c]).strip() or None; break
        for c in ("totdasqkm", "TotDASqKM", "totdasqkm_1"):
            if c in fl.columns and row.get(c) is not None:
                out["drainage_area_sqkm"] = float(row[c]); break
        for c in ("reachcode", "REACHCODE", "reachcode_1"):
            if c in fl.columns and row.get(c):
                out["huc8"] = str(row[c])[:8]; break
        for c in ("slope", "SLOPE"):
            if c in fl.columns and row.get(c) is not None:
                s = float(row[c]); out["slope"] = s if s >= 0 else None; break
        for c in ("fcode", "FCODE"):
            if c in fl.columns and row.get(c) is not None:
                out["fcode"] = int(row[c]); break
        for c in ("streamorde", "StreamOrde", "streamorder"):
            if c in fl.columns and row.get(c) is not None:
                out["stream_order"] = int(row[c]); break
        try:  # sinuosity = flowline length / straight-line endpoint distance
            import geopandas as gpd
            from shapely.geometry import Point
            g = gpd.GeoSeries([fl.geometry.iloc[0]], crs=CRS_WGS84).to_crs(CRS_ALBERS).iloc[0]
            line = g.geoms[0] if g.geom_type == "MultiLineString" else g
            straight = Point(line.coords[0]).distance(Point(line.coords[-1]))
            if straight > 0:
                out["sinuosity"] = round(line.length / straight, 3)
        except Exception:
            pass
    except Exception as exc:  # pragma: no cover - network/version guard
        out["_flowline_error"] = str(exc)
    return out


def snap_point(lat: float, lon: float) -> dict:
    """Snap (lat,lon) to the nearest NHDPlus COMID and read flowline context."""
    from pynhd import NLDI

    out: dict[str, Any] = {"comid": None, "gnis_name": None,
                           "drainage_area_sqkm": None, "huc8": None,
                           "slope": None, "fcode": None, "stream_order": None,
                           "sinuosity": None,
                           "snapped_lat": None, "snapped_lon": None}

    # NLDI raises (or returns an empty/columnless frame) when the point is far
    # from any mapped flowline (e.g. offshore). Treat that as "no stream here"
    # (comid stays None) so the caller shows the friendly guidance message.
    try:
        snapped = NLDI().comid_byloc((lon, lat))  # GeoDataFrame
    except Exception:
        return out
    if snapped is None or len(snapped) == 0:
        return out
    # comid column name has varied across versions; find it defensively
    comid = None
    for col in ("comid", "COMID", "nhdplus_comid", "featureid"):
        if col in snapped.columns:
            try:
                comid = int(snapped.iloc[0][col]); break
            except (TypeError, ValueError):
                continue
    if comid is None:
        try:
            comid = int(snapped.iloc[0].get(snapped.columns[0]))
        except (TypeError, ValueError, IndexError):
            comid = None
    out["comid"] = comid
    try:
        geom = snapped.iloc[0].geometry
        if geom is not None and geom.geom_type == "Point":
            out["snapped_lon"], out["snapped_lat"] = float(geom.x), float(geom.y)
    except Exception:
        pass

    if comid is not None:
        out.update(flowline_attrs(comid))
    return out


def delineate_watershed(comid: int, simplified: bool = False) -> tuple[Optional[dict], float, list[str]]:
    """Full upstream contributing basin to the COMID's *outlet* (NLDI basin).

    Aggregates all upstream NHDPlus catchments to the downstream end of the COMID
    reach. ``simplified=False`` keeps the detailed catchment boundary (faithful to
    the NHDPlus grid); ``simplified=True`` returns a coarse generalized polygon.
    This is the primary basin method: it matches the COMID watershed the StreamCat
    metrics are computed for, and is reliable (unlike point-precise split-catchment).
    """
    from pynhd import NLDI

    warnings: list[str] = []
    try:
        basins = NLDI().get_basins([str(comid)], fsource="comid", simplified=simplified)
    except Exception as exc:  # pragma: no cover - network guard
        return None, 0.0, [f"watershed delineation failed: {exc}"]
    if basins is None or basins.empty:
        return None, 0.0, ["no basin returned for COMID"]

    geom, area_sqkm = _largest_polygon(basins)
    if geom is None:
        return None, 0.0, ["no polygon in basin result"]
    import geopandas as gpd
    basin = gpd.GeoSeries([geom], crs=basins.crs or CRS_WGS84).to_crs(CRS_WGS84)
    return basin.__geo_interface__, area_sqkm, warnings


def _outlet_at_start(merged, own_geoms: list) -> Optional[bool]:
    """Which end of ``merged`` is the COMID *outlet* (downstream) node?

    ``upstreamMain`` returns the COMID plus its upstream mainstem, so the COMID is
    the most-downstream segment: its outlet node coincides with one **endpoint** of
    the merged path while its upstream node is *internal* to the path. We match the
    COMID's own endpoints (EPSG:5070, ~1 m tol) against the merged endpoints.

    Returns ``True`` if ``merged.coords[0]`` is the outlet, ``False`` if
    ``coords[-1]`` is, or ``None`` when undetermined (a headwater single-segment
    COMID where both ends match, or no match) — the caller then falls back to a
    longer-side heuristic.
    """
    if not own_geoms:
        return None
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        from shapely.ops import linemerge
        # explode any MultiLineString into LineStrings, then stitch into one line so
        # we read the COMID's TRUE endpoints (not an interior sub-part endpoint)
        parts: list = []
        for g in own_geoms:
            parts.extend(g.geoms if g.geom_type == "MultiLineString" else [g])
        cl = gpd.GeoSeries(parts, crs=CRS_WGS84).to_crs(CRS_ALBERS)
        c = cl.iloc[0] if len(cl) == 1 else linemerge(cl.tolist())
        if c.geom_type == "MultiLineString":
            c = max(c.geoms, key=lambda g: g.length)
        m0, m1 = Point(merged.coords[0]), Point(merged.coords[-1])
        c_ends = (Point(c.coords[0]), Point(c.coords[-1]))
        hits0 = any(ce.distance(m0) < 1.0 for ce in c_ends)
        hits1 = any(ce.distance(m1) < 1.0 for ce in c_ends)
        if hits0 and not hits1:
            return True
        if hits1 and not hits0:
            return False
    except Exception:  # noqa: BLE001 - orientation is best-effort
        pass
    return None


def _trim_upstream(merged, snap, length_m: float, outlet_at_start: Optional[bool]):
    """``length_m`` of ``merged`` immediately UPSTREAM of ``snap``'s projection.

    The snapped point becomes the reach's downstream end. ``outlet_at_start`` marks
    the downstream (outlet) end of ``merged``: ``True``=coords[0], ``False``=coords[-1],
    ``None``=unknown — in which case the longer side of the projection is taken as
    upstream (the upstream-mainstem query returns upstream-biased geometry, so the
    shorter side is the outlet). Pure shapely (EPSG:5070); no I/O.
    """
    from shapely.ops import substring
    total = merged.length
    proj = merged.project(snap)
    if outlet_at_start is None:
        outlet_at_start = proj <= (total - proj)        # outlet on the shorter side
    if outlet_at_start:                                 # upstream = toward coords[-1]
        return substring(merged, proj, min(proj + length_m, total))
    return substring(merged, max(0.0, proj - length_m), proj)  # toward coords[0]


def derive_reach(comid: int, lat: float, lon: float,
                 length_ft: float = DEFAULT_REACH_FT) -> tuple[Optional[dict], Optional[float], list[str]]:
    """Trim the mainstem to ~length_ft upstream of the snap point (EPSG:5070).

    The snapped point is the reach's downstream end; the reach extends ``length_ft``
    upstream along the mainstem — anchored at the point's projection on the line,
    not at the COMID's downstream node.
    """
    import geopandas as gpd
    from shapely.geometry import Point
    from shapely.ops import linemerge
    from pynhd import NLDI, WaterData

    warnings: list[str] = []
    length_m = length_ft / FT_PER_M
    length_km = length_m / 1000.0

    # The snapped COMID's own flowline: orients the trim (its outlet node) and is the
    # geometry fallback if upstream navigation fails. Cheap + cached.
    own_geoms: list = []
    own_len_km = 0.0
    try:
        own = WaterData("nhdflowline_network").byid("comid", [str(comid)])
        own_geoms = [g for g in own.geometry if g is not None and not g.is_empty]
        if own_geoms:
            own_len_km = float(gpd.GeoSeries(own_geoms, crs=CRS_WGS84)
                               .to_crs(CRS_ALBERS).length.sum()) / 1000.0
    except Exception as exc:
        warnings.append(f"comid flowline fetch failed: {exc}")

    # Prefer NLDI upstreamMain flowlines (the COMID + upstream mainstem) as a single
    # geometry source. Size the navigation (km) to clear the COMID's own length so
    # upstream segments — and thus the COMID's *internal* upstream node — are returned
    # even for long reaches; that lets the trim be oriented deterministically.
    nav_km = round(max(length_km * 4, own_len_km + length_km) + 0.3, 1)
    try:
        up = NLDI().navigate_byid(
            fsource="comid", fid=str(comid), navigation="upstreamMain",
            source="flowlines", distance=max(1.0, nav_km),
        )
        geoms = [g for g in up.geometry if g is not None and not g.is_empty]
    except Exception as exc:
        warnings.append(f"upstream navigation failed: {exc}")
        geoms = []
    if not geoms:                       # fall back to the COMID's own flowline
        geoms = own_geoms
    if not geoms:
        return None, None, warnings or ["no flowline geometry for reach"]

    lines = gpd.GeoSeries(geoms, crs=CRS_WGS84).to_crs(CRS_ALBERS)
    snap = gpd.GeoSeries([Point(lon, lat)], crs=CRS_WGS84).to_crs(CRS_ALBERS).iloc[0]
    merged = lines.iloc[0] if len(lines) == 1 else linemerge(lines.tolist())
    if merged.geom_type == "MultiLineString":
        # pick the connected component nearest the snap point (outlet segment)
        merged = min(merged.geoms, key=lambda g: g.distance(snap))
        warnings.append("flowline had gaps; used nearest mainstem component")

    # Anchor the trim at the snapped point and extend length_m UPSTREAM (so the snap
    # is the reach's downstream end), oriented by the COMID outlet node.
    seg = _trim_upstream(merged, snap, length_m, _outlet_at_start(merged, own_geoms))
    actual_ft = seg.length * FT_PER_M
    if actual_ft < length_ft - 1:
        warnings.append(f"only {actual_ft:.0f} ft of mainstem available upstream")

    reach = gpd.GeoSeries([seg], crs=CRS_ALBERS).to_crs(CRS_WGS84)
    return reach.__geo_interface__, round(actual_ft, 1), warnings


def run_delineation(lat: float, lon: float,
                    length_ft: float = DEFAULT_REACH_FT, *,
                    comid: Optional[int] = None,
                    snapped_lat: Optional[float] = None,
                    snapped_lon: Optional[float] = None) -> Delineation:
    """Full point -> snap -> watershed -> reach pipeline.

    If ``comid`` is given (the user clicked an NHD flowline vector, which carries
    its COMID), it is used directly — bypassing the less reliable NLDI point-snap.
    Otherwise the point is snapped server-side via NLDI. The assessment reach is
    always traced ``length_ft`` upstream from the snapped point along the mainstem.
    """
    d = Delineation(lat=lat, lon=lon)
    if comid is not None:
        d.comid = int(comid)
        d.snapped_lat = snapped_lat if snapped_lat is not None else lat
        d.snapped_lon = snapped_lon if snapped_lon is not None else lon
        attrs = flowline_attrs(d.comid)
        d.gnis_name = attrs.get("gnis_name")
        d.drainage_area_sqkm = attrs.get("drainage_area_sqkm")
        d.huc8 = attrs.get("huc8")
        d.slope = attrs.get("slope")
        d.fcode = attrs.get("fcode")
        d.stream_order = attrs.get("stream_order")
        d.sinuosity = attrs.get("sinuosity")
        if attrs.get("_flowline_error"):
            d.warnings.append(f"flowline context: {attrs['_flowline_error']}")
    else:
        snap = snap_point(lat, lon)
        d.comid = snap.get("comid")
        d.gnis_name = snap.get("gnis_name")
        d.drainage_area_sqkm = snap.get("drainage_area_sqkm")
        d.huc8 = snap.get("huc8")
        d.slope = snap.get("slope")
        d.fcode = snap.get("fcode")
        d.stream_order = snap.get("stream_order")
        d.sinuosity = snap.get("sinuosity")
        d.snapped_lat = snap.get("snapped_lat")
        d.snapped_lon = snap.get("snapped_lon")
        if snap.get("_flowline_error"):
            d.warnings.append(f"flowline context: {snap['_flowline_error']}")

    if d.comid is None:
        d.warnings.append("no COMID found at point; cannot delineate")
        return d

    ws, ws_area, w1 = delineate_watershed(d.comid, simplified=False)
    d.watershed_geojson = ws
    d.watershed_area_sqkm = ws_area
    d.warnings.extend(w1)

    reach, rlen, w2 = derive_reach(d.comid, d.snapped_lat or lat,
                                   d.snapped_lon or lon, length_ft)
    d.reach_geojson = reach
    d.reach_length_ft = rlen
    d.warnings.extend(w2)
    return d


if __name__ == "__main__":  # live diagnostic
    import json
    points = {
        "Scioto River @ Columbus OH (mainstem, DA~4191 km2)": (39.9550, -83.0030),
        "Headwater tributary near Worthington OH": (40.0962, -83.0203),
    }
    for label, (test_lat, test_lon) in points.items():
        print(f"\n== {label}: ({test_lat}, {test_lon}) ==")
        res = run_delineation(test_lat, test_lon)
        print(json.dumps({
            "comid": res.comid,
            "gnis_name": res.gnis_name,
            "drainage_area_sqkm": res.drainage_area_sqkm,
            "snapped": [res.snapped_lat, res.snapped_lon],
            "watershed_area_sqkm": round(res.watershed_area_sqkm or 0, 2),
            "reach_length_ft": res.reach_length_ft,
            "has_watershed_geojson": res.watershed_geojson is not None,
            "has_reach_geojson": res.reach_geojson is not None,
            "warnings": res.warnings,
        }, indent=2))
