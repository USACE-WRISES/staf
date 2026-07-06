"""Compare watershed delineation: EASI (NLDI/COMID basin) vs Model My Watershed.

Runs both methods on one or more points and reports how the delineated
watersheds differ — area, overlap (IoU), coverage, centroid offset, and how far
apart the two methods snap the input point. Writes an interactive Leaflet HTML
overlay (primary) and a matplotlib PNG (quick-look) per point so the basins can
be seen side by side.

Method A (EASI):  easi.delineation.run_delineation -> NLDI.get_basins([comid]),
                  the full contributing basin to the snapped COMID's *outlet*.
Method B (MMW):   easi.datasources.mmw.delineate_watershed_mmw -> MMW's point
                  split-catchment (NHDPlus v2.1 + TauDEM), via the token API.

The MMW API key comes from $MMW_API_KEY or the gitignored scripts/.mmw_api_key
(see easi/datasources/mmw.py); it is never printed. Outputs go to a gitignored
out/ dir. This is a diagnostic harness — it changes no production code path.

Examples:
  python scripts/compare_watersheds.py                      # default point set
  python scripts/compare_watersheds.py --lat 39.955 --lon -83.003 --label Scioto
  python scripts/compare_watersheds.py --simplify 0.0005 --max-wait 180
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from easi import delineation                      # noqa: E402
from easi.datasources import mmw                  # noqa: E402

CRS_WGS84 = 4326
CRS_ALBERS = 5070  # USGS CONUS Albers Equal Area (metres) — all area/distance math

# label, lat, lon, note. Two are EASI's own diagnostic points (delineation.py).
DEFAULT_POINTS = [
    ("Scioto River at Columbus OH", 39.9550, -83.0030,
     "mainstem; COMID 5218161; DA ~4191 km2 - expect high IoU"),
    ("Headwater trib near Worthington OH", 40.0962, -83.0203,
     "small basin; split-catchment ~ whole catchment - expect A ~= B"),
    ("Olentangy River at Delaware OH", 40.2987, -83.0680,
     "mid-size reach; where snap lands mid-reach, expect MMW (B) < EASI (A)"),
]


# --------------------------------------------------------------------------- #
# geometry helpers (local geo imports; never raise on bad geometry)
# --------------------------------------------------------------------------- #
def _dissolved_5070(fc):
    """Dissolve a FeatureCollection to one EPSG:5070 geometry, or None."""
    if not fc or not fc.get("features"):
        return None
    try:
        import geopandas as gpd
        g = gpd.GeoDataFrame.from_features(fc["features"], crs=CRS_WGS84)
        g = g[g.geometry.notna() & ~g.geometry.is_empty]
        if g.empty:
            return None
        return g.to_crs(CRS_ALBERS).geometry.union_all()
    except Exception as exc:  # noqa: BLE001
        print(f"     [geom] dissolve failed: {exc}")
        return None


def _point_5070(lon, lat):
    import geopandas as gpd
    from shapely.geometry import Point
    return gpd.GeoSeries([Point(lon, lat)], crs=CRS_WGS84).to_crs(CRS_ALBERS).iloc[0]


def _pt_lonlat(fc):
    """First point coordinate (lon, lat) from a point FeatureCollection, or None."""
    try:
        lon, lat = fc["features"][0]["geometry"]["coordinates"][:2]
        return float(lon), float(lat)
    except Exception:  # noqa: BLE001
        return None


def compare_geoms(fc_a, fc_b, snap_a=None, snap_b=None) -> dict:
    """Quantify A (EASI) vs B (MMW) overlap. All metrics zero-area-safe.

    ``snap_a``/``snap_b`` are (lon, lat) tuples. Returns a flat dict of rounded
    numbers (None where a side is missing).
    """
    out: dict = {}
    A = _dissolved_5070(fc_a)
    B = _dissolved_5070(fc_b)
    a_area = A.area if A and not A.is_empty else 0.0
    b_area = B.area if B and not B.is_empty else 0.0
    out["area_a_sqkm"] = round(a_area / 1e6, 3) if a_area else None
    out["area_b_sqkm"] = round(b_area / 1e6, 3) if b_area else None
    if a_area and b_area:
        inter = A.intersection(B).area
        union = A.union(B).area
        out["area_ratio"] = round(b_area / a_area, 3)
        out["area_diff_sqkm"] = round((b_area - a_area) / 1e6, 3)
        out["intersection_sqkm"] = round(inter / 1e6, 3)
        out["union_sqkm"] = round(union / 1e6, 3)
        out["iou"] = round(inter / union, 3) if union else None
        out["pct_a_covered_by_b"] = round(100 * inter / a_area, 1)
        out["pct_b_covered_by_a"] = round(100 * inter / b_area, 1)
        out["centroid_dist_m"] = round(A.centroid.distance(B.centroid), 1)
    if snap_a and snap_b:
        try:
            out["snap_dist_m"] = round(_point_5070(*snap_a).distance(_point_5070(*snap_b)), 1)
        except Exception:  # noqa: BLE001
            out["snap_dist_m"] = None
    return out


# --------------------------------------------------------------------------- #
# run each method
# --------------------------------------------------------------------------- #
def run_easi(lat, lon) -> dict:
    d = delineation.run_delineation(lat, lon)
    snap = ((d.snapped_lon, d.snapped_lat)
            if d.snapped_lon is not None and d.snapped_lat is not None else None)
    return {"watershed_geojson": d.watershed_geojson, "area_sqkm": d.watershed_area_sqkm,
            "comid": d.comid, "gnis_name": d.gnis_name,
            "drainage_area_sqkm": d.drainage_area_sqkm, "snap": snap, "warnings": d.warnings}


def run_mmw(lat, lon, **kw) -> dict:
    fc, area, pt, warnings = mmw.delineate_watershed_mmw(lat, lon, **kw)
    return {"watershed_geojson": fc, "area_sqkm": area,
            "snap": _pt_lonlat(pt) if pt else None, "warnings": warnings}


# --------------------------------------------------------------------------- #
# outputs: console, PNG, HTML
# --------------------------------------------------------------------------- #
def _fmt(v):
    return f"{v:.2f}" if isinstance(v, (int, float)) else "n/a"


def _fmt_pt(p):
    return f"({p[1]:.5f}, {p[0]:.5f})" if p else "n/a"   # (lat, lon)


def _s(v):
    return "n/a" if v is None else str(v)


_CMP_KEYS = ["area_a_sqkm", "area_b_sqkm", "area_ratio", "area_diff_sqkm",
             "intersection_sqkm", "union_sqkm", "iou", "pct_a_covered_by_b",
             "pct_b_covered_by_a", "centroid_dist_m", "snap_dist_m"]


def print_point(label, lat, lon, easi, mmw_res, cmp, note=""):
    print(f"\n=== {label}  ({lat}, {lon}) ===")
    if note:
        print(f"    {note}")
    print(f"  EASI : comid={_s(easi['comid'])} {easi.get('gnis_name') or ''} | "
          f"area={_fmt(easi['area_sqkm'])} km2 | DA(NHDPlus)={_fmt(easi['drainage_area_sqkm'])} km2 | "
          f"snap={_fmt_pt(easi['snap'])}")
    if easi["warnings"]:
        print(f"         warnings: {easi['warnings']}")
    print(f"  MMW  : area={_fmt(mmw_res['area_sqkm'])} km2 | snap={_fmt_pt(mmw_res['snap'])}")
    if mmw_res["warnings"]:
        print(f"         warnings: {mmw_res['warnings']}")
    print("  compare (A=EASI, B=MMW):")
    for k in _CMP_KEYS:
        if k in cmp:
            print(f"     {k:22s} {cmp[k]}")


def print_summary(rows):
    print("\n=========================== SUMMARY ===========================")
    print(f"{'point':36s} {'A km2':>9} {'B km2':>9} {'B/A':>6} {'IoU':>6} {'snap m':>8} {'comid':>9}")
    for label, cmp, comid in rows:
        print(f"{label[:36]:36s} {_s(cmp.get('area_a_sqkm')):>9} {_s(cmp.get('area_b_sqkm')):>9} "
              f"{_s(cmp.get('area_ratio')):>6} {_s(cmp.get('iou')):>6} "
              f"{_s(cmp.get('snap_dist_m')):>8} {_s(comid):>9}")
    print("Legend: IoU 1.0 = identical basins; B/A<1 = MMW smaller (split-catchment);")
    print("        large snap m = methods snapped the point to different stream locations.")


def render_overlay_png(path, label, easi, mmw_res, cmp):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    import geopandas as gpd

    fig, ax = plt.subplots(figsize=(8, 8))
    if easi.get("watershed_geojson"):
        gpd.GeoDataFrame.from_features(easi["watershed_geojson"]["features"], crs=CRS_WGS84) \
            .to_crs(CRS_ALBERS).plot(ax=ax, facecolor="#4a78c8", edgecolor="#1f3d7a",
                                     alpha=0.40, linewidth=1.5)
    if mmw_res.get("watershed_geojson"):
        gpd.GeoDataFrame.from_features(mmw_res["watershed_geojson"]["features"], crs=CRS_WGS84) \
            .to_crs(CRS_ALBERS).plot(ax=ax, facecolor="#e08a2e", edgecolor="#9c5a10",
                                     alpha=0.40, linewidth=1.5, linestyle="--")
    handles = [Patch(facecolor="#4a78c8", edgecolor="#1f3d7a", alpha=0.5,
                     label="EASI — NLDI reach-outlet basin"),
               Patch(facecolor="#e08a2e", edgecolor="#9c5a10", alpha=0.5,
                     label="MMW — point split-catchment")]
    if easi.get("snap"):
        p = _point_5070(*easi["snap"])
        ax.plot(p.x, p.y, marker="*", color="#10204a", markersize=15, linestyle="none")
        handles.append(Line2D([], [], marker="*", color="#10204a", linestyle="none",
                              markersize=12, label="EASI snap"))
    if mmw_res.get("snap"):
        p = _point_5070(*mmw_res["snap"])
        ax.plot(p.x, p.y, marker="o", color="#7a3d05", markersize=8, linestyle="none")
        handles.append(Line2D([], [], marker="o", color="#7a3d05", linestyle="none",
                              markersize=8, label="MMW snap"))
    txt = (f"EASI area: {_s(cmp.get('area_a_sqkm'))} km2\n"
           f"MMW area:  {_s(cmp.get('area_b_sqkm'))} km2\n"
           f"ratio B/A: {_s(cmp.get('area_ratio'))}\n"
           f"IoU:       {_s(cmp.get('iou'))}\n"
           f"snap dist: {_s(cmp.get('snap_dist_m'))} m")
    ax.text(0.02, 0.98, txt, transform=ax.transAxes, va="top", ha="left", fontsize=9,
            family="monospace", bbox=dict(boxstyle="round", fc="white", ec="#bbb", alpha=0.92))
    ax.legend(handles=handles, loc="lower right", fontsize=8)
    ax.set_aspect("equal")
    ax.set_title(label, fontsize=11)
    ax.set_xlabel("EPSG:5070 easting (m)")
    ax.set_ylabel("northing (m)")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>__TITLE__</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 html,body{margin:0;height:100%;font-family:system-ui,'Segoe UI',Arial,sans-serif}
 #map{position:absolute;inset:0}
 .panel{position:absolute;top:10px;right:10px;z-index:1000;background:rgba(255,255,255,.96);
   padding:10px 12px;border:1px solid #ccc;border-radius:8px;font-size:13px;max-width:330px;
   box-shadow:0 1px 8px rgba(0,0,0,.25)}
 .panel h3{margin:0 0 6px;font-size:14px}
 .sw{display:inline-block;width:13px;height:13px;margin-right:6px;vertical-align:middle;border:1px solid #555}
 table{border-collapse:collapse;margin-top:6px} td{padding:1px 8px 1px 0}
 td.v{font-weight:700;text-align:right}
</style></head>
<body>
<div id="map"></div>
<div class="panel">
 <h3>__TITLE__</h3>
 <div><span class="sw" style="background:#4a78c8"></span>EASI — NLDI reach-outlet basin</div>
 <div><span class="sw" style="background:#e08a2e"></span>MMW — point split-catchment</div>
 <table>__ROWS__</table>
</div>
<script>
 var easi = __EASI__, mmw = __MMW__;
 var map = L.map('map');
 L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
   {maxZoom:19, attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
 var layers = [];
 var ea = L.geoJSON(easi, {style:{color:'#1f3d7a',weight:2,fillColor:'#4a78c8',fillOpacity:0.30}});
 var mw = L.geoJSON(mmw, {style:{color:'#9c5a10',weight:2,dashArray:'5,4',fillColor:'#e08a2e',fillOpacity:0.30}});
 if (easi.features && easi.features.length){ ea.addTo(map); layers.push(ea); }
 if (mmw.features && mmw.features.length){ mw.addTo(map); layers.push(mw); }
 __MARKERS__
 if (layers.length){ map.fitBounds(L.featureGroup(layers).getBounds().pad(0.12)); }
 else { map.setView([__LAT__, __LON__], 11); }
</script>
</body></html>
"""


def _marker_js(easi, mmw_res):
    js = []
    if easi.get("snap"):
        lon, lat = easi["snap"]
        js.append(f"L.circleMarker([{lat},{lon}],{{radius:6,color:'#10204a',"
                  f"fillColor:'#1f3d7a',fillOpacity:1,weight:2}}).addTo(map).bindTooltip('EASI snap');")
    if mmw_res.get("snap"):
        lon, lat = mmw_res["snap"]
        js.append(f"L.circleMarker([{lat},{lon}],{{radius:6,color:'#7a3d05',"
                  f"fillColor:'#e08a2e',fillOpacity:1,weight:2}}).addTo(map).bindTooltip('MMW snap');")
    return "\n ".join(js)


def _rows_html(cmp):
    labels = [("EASI area (km²)", "area_a_sqkm"), ("MMW area (km²)", "area_b_sqkm"),
              ("area ratio B/A", "area_ratio"), ("IoU (Jaccard)", "iou"),
              ("% EASI in MMW", "pct_a_covered_by_b"), ("% MMW in EASI", "pct_b_covered_by_a"),
              ("centroid dist (m)", "centroid_dist_m"), ("snap dist (m)", "snap_dist_m")]
    return "".join(f"<tr><td>{lbl}</td><td class='v'>{_s(cmp.get(k))}</td></tr>" for lbl, k in labels)


def render_overlay_html(path, label, lat, lon, easi, mmw_res, cmp):
    empty = {"type": "FeatureCollection", "features": []}
    html = (_HTML
            .replace("__TITLE__", label)
            .replace("__ROWS__", _rows_html(cmp))
            .replace("__EASI__", json.dumps(easi.get("watershed_geojson") or empty))
            .replace("__MMW__", json.dumps(mmw_res.get("watershed_geojson") or empty))
            .replace("__MARKERS__", _marker_js(easi, mmw_res))
            .replace("__LAT__", str(lat)).replace("__LON__", str(lon)))
    Path(path).write_text(html, encoding="utf-8")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60] or "point"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, help="single-point latitude (with --lon)")
    ap.add_argument("--lon", type=float, help="single-point longitude (with --lat)")
    ap.add_argument("--label", default="custom point", help="label for the single point")
    ap.add_argument("--data-source", default="nhd", choices=["nhd", "drb", "tdx"],
                    help="MMW delineation source (default nhd = CONUS high-res)")
    ap.add_argument("--no-snapping", action="store_true", help="disable MMW point snapping")
    ap.add_argument("--simplify", type=float, default=0.0, help="MMW simplify tolerance (0 = full)")
    ap.add_argument("--out", default=str(ROOT / "out" / "compare"), help="output dir (gitignored)")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--require-mmw", action="store_true",
                    help="exit non-zero if no MMW watershed was returned")
    ap.add_argument("--max-wait", type=float, default=120.0, help="MMW job budget seconds")
    ap.add_argument("--timeout", type=float, default=30.0, help="per-request timeout seconds")
    args = ap.parse_args(argv)

    if (args.lat is None) != (args.lon is None):
        ap.error("--lat and --lon must be given together")
    points = ([(args.label, args.lat, args.lon, "")] if args.lat is not None
              else DEFAULT_POINTS)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    mmw_kw = dict(data_source=args.data_source, snapping=not args.no_snapping,
                  simplify=args.simplify, max_wait=args.max_wait, timeout=args.timeout)

    print(f"Comparing {len(points)} point(s); MMW dataSource={args.data_source}, "
          f"snapping={not args.no_snapping}. Outputs -> {outdir}")
    summary, any_mmw = [], False
    for (label, lat, lon, note) in points:
        easi = run_easi(lat, lon)
        mmw_res = run_mmw(lat, lon, **mmw_kw)
        any_mmw = any_mmw or bool(mmw_res["watershed_geojson"])
        cmp = compare_geoms(easi["watershed_geojson"], mmw_res["watershed_geojson"],
                            easi["snap"], mmw_res["snap"])
        print_point(label, lat, lon, easi, mmw_res, cmp, note)
        slug = _slug(label)
        has_geom = bool(easi["watershed_geojson"] or mmw_res["watershed_geojson"])
        if has_geom and not args.no_png:
            try:
                render_overlay_png(outdir / f"{slug}.png", label, easi, mmw_res, cmp)
                print(f"     png  -> {outdir / (slug + '.png')}")
            except Exception as exc:  # noqa: BLE001
                print(f"     png  failed: {exc}")
        if has_geom and not args.no_html:
            try:
                render_overlay_html(outdir / f"{slug}.html", label, lat, lon, easi, mmw_res, cmp)
                print(f"     html -> {outdir / (slug + '.html')}")
            except Exception as exc:  # noqa: BLE001
                print(f"     html failed: {exc}")
        summary.append((label, cmp, easi["comid"]))

    print_summary(summary)
    if args.require_mmw and not any_mmw:
        print("\nERROR: --require-mmw set but no MMW watershed returned "
              "(check MMW_API_KEY / network).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
