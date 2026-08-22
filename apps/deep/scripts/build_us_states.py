"""Build DEEP's US state boundary layer from the Census cartographic boundary file.

DEEP resolves a snapped site to its US state by an exact, boundary-inclusive
point-in-polygon test (``deep/geo.py``, shapely ``covers``). That test is only as
good as its polygons, and the layer DEEP shipped until 2026-08-22 was the Leaflet
choropleth tutorial dataset (52 features, 28 vertices for all of New Hampshire),
whose New Hampshire western edge ran about 2.7 km east of the Connecticut River,
so a Hanover NH site read as Vermont. This script replaces it with the US Census
Bureau cartographic boundary file at 1:500,000, the most detailed generalized
product, which is topologically consistent (neighboring states share edges, with
no gaps or overlaps along the lines).

Source:    https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip
           (Census cartographic boundary files, 2024 vintage, NAD83)
Retrieved: 2026-08-22

Output: ``data/us_states.geojson.gz``, a gzipped GeoJSON FeatureCollection with one
feature per state, district, or territory (56), properties
``{"state": STUSPS, "name": NAME, "fips": STATEFP}``, WGS84 coordinates rounded to
six decimals, features sorted by name, written with a zero gzip mtime so a rebuild
from the same source is byte-identical.

Usage:
    py scripts/build_us_states.py [--source URL_OR_ZIP] [--out PATH] [--work DIR]
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

DEEP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip"
DEFAULT_OUT = DEEP_ROOT / "data" / "us_states.geojson.gz"
VINTAGE = "Census cartographic boundary files 2024, 1:500,000 (cb_2024_us_state_500k)"
RETRIEVED = "2026-08-22"
PRECISION = 6  # decimal degrees, about 0.1 m


def fetch(source: str, work: Path) -> Path:
    """Return a local zip path for ``source`` (a URL is downloaded into ``work``)."""
    if "://" not in source:
        path = Path(source)
        if not path.exists():
            raise SystemExit(f"source zip not found: {path}")
        return path
    work.mkdir(parents=True, exist_ok=True)
    dest = work / source.rsplit("/", 1)[-1]
    if dest.exists() and dest.stat().st_size > 0:
        print(f"using the cached download {dest}")
        return dest
    print(f"downloading {source}")
    with urllib.request.urlopen(source, timeout=120) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    print(f"  {dest.stat().st_size:,} bytes")
    return dest


def _polygonal(geom):
    """Keep only the polygon parts of a geometry (``make_valid`` may return a
    GeometryCollection with stray lines or points)."""
    import shapely

    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts) if parts else geom


def build(zip_path: Path) -> tuple[dict, dict]:
    """Read the Census shapefile and return ``(feature_collection, summary)``."""
    import geopandas as gpd
    import numpy as np
    import shapely
    from shapely.geometry import mapping

    gdf = gpd.read_file(zip_path)
    required = {"STUSPS", "NAME", "STATEFP"}
    missing = required - set(gdf.columns)
    if missing:
        raise SystemExit(f"source lacks the expected columns: {sorted(missing)}")
    gdf = gdf.to_crs(4326).sort_values("NAME").reset_index(drop=True)

    features = []
    repaired: list[str] = []
    vertices_by_state: dict[str, int] = {}
    for row in gdf.itertuples(index=False):
        geom = shapely.transform(row.geometry, lambda c: np.round(c, PRECISION))
        if not geom.is_valid:
            geom = _polygonal(shapely.make_valid(geom))
            repaired.append(str(row.STUSPS))
        if geom.is_empty:
            raise SystemExit(f"{row.STUSPS}: empty geometry after rounding")
        vertices_by_state[str(row.STUSPS)] = int(shapely.get_num_coordinates(geom))
        features.append({
            "type": "Feature",
            "properties": {"state": str(row.STUSPS), "name": str(row.NAME), "fips": str(row.STATEFP)},
            "geometry": mapping(geom),
        })

    fc = {
        "type": "FeatureCollection",
        "source": {
            "dataset": VINTAGE,
            "url": DEFAULT_SOURCE,
            "retrieved": RETRIEVED,
            "crs": "EPSG:4326",
            "coordinate_precision": PRECISION,
            "builder": "apps/deep/scripts/build_us_states.py",
        },
        "features": features,
    }
    summary = {
        "features": len(features),
        "vertices": sum(vertices_by_state.values()),
        "vertices_by_state": vertices_by_state,
        "repaired": repaired,
    }
    return fc, summary


def write_gz(fc: dict, out: Path) -> int:
    """Write ``fc`` as compact gzipped JSON with a zero mtime (reproducible bytes)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(fc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with open(out, "wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=9) as gz:
            gz.write(text)
    return len(text)


def self_check() -> None:
    """Resolve a few known points through ``deep.geo`` against the written layer."""
    sys.path.insert(0, str(DEEP_ROOT))
    try:
        from deep import geo
    except Exception as exc:  # noqa: BLE001
        print(f"self-check skipped ({exc})")
        return
    checks = [
        ((43.6896, -72.2840), "NH", "Mink Brook, Hanover NH, 2 km east of the Vermont line"),
        ((40.0, -83.5), "OH", "central Ohio"),
        ((30.0, -70.0), None, "mid-Atlantic offshore"),
    ]
    for (lat, lon), want, label in checks:
        got = geo.state_at(lat, lon)
        code = None if got is None else got.get("code")
        flag = "ok" if code == want else "MISMATCH"
        print(f"  {flag:8s} {label}: {code!r} (expected {want!r})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=DEFAULT_SOURCE, help="Census zip URL or a local zip path")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--work", type=Path, default=Path(tempfile.gettempdir()) / "deep_us_states",
                    help="download directory for a URL source")
    args = ap.parse_args(argv)

    zip_path = fetch(args.source, args.work)
    fc, summary = build(zip_path)
    raw_bytes = write_gz(fc, args.out)
    gz_bytes = args.out.stat().st_size

    print(f"wrote {args.out}")
    print(f"  features {summary['features']}, vertices {summary['vertices']:,}, "
          f"raw {raw_bytes / 1e6:.1f} MB, gzipped {gz_bytes / 1e6:.1f} MB")
    for code in ("NH", "VT", "CO", "AK"):
        print(f"  {code} vertices {summary['vertices_by_state'].get(code, 0):,}")
    if summary["repaired"]:
        print(f"  geometries repaired with make_valid: {', '.join(summary['repaired'])}")
    print("self-check through deep.geo:")
    self_check()
    return 0


if __name__ == "__main__":
    sys.exit(main())
