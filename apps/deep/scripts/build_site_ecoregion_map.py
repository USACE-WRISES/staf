"""Write the STAF site's Level III ecoregion picker map (docs/assets/data/ecoregions-l3-map.json).

The Apply STAF page's "Find your ecoregion" opens a small map of the conterminous United States:
click your area and the DEEP calculator picker selects that ecoregion (owner, 2026-10-08). The
outlines are DEEP's own (``data/ecoregions_l3.geojson``, EPA Level III, the polygons DEEP resolves a
site against), so the picker and DEEP never disagree; the state lines
(``data/us_states.geojson.gz``, Census 1:500,000) are there for orientation. Both are projected to
CONUS Albers (EPSG:5070), simplified to under a pixel at the map's width, and written as SVG path
data in map pixels (relative moves, a tenth of a pixel), so the file stays small and the page
draws it without a mapping library. Islands and holes under two square pixels are dropped, and
the states contribute only the borders between them (the ecoregions already draw the coast).

The map records the sha256 of its inputs (both outline files and this script), so DEEP's
tests/test_site_ecoregion_map.py sees a stale map without rebuilding it.

Usage:
    py scripts/build_site_ecoregion_map.py           # rewrite the map
    py scripts/build_site_ecoregion_map.py --check   # exit 1 when it is stale, write nothing
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import MultiLineString, MultiPolygon, Polygon, shape
from shapely.ops import linemerge, transform, unary_union

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
ECOREGIONS = APP / "data" / "ecoregions_l3.geojson"
STATES = APP / "data" / "us_states.geojson.gz"
OUT = REPO / "docs" / "assets" / "data" / "ecoregions-l3-map.json"
WIDTH = 1000          # the viewBox width; the page scales the map to its dialog
PAD = 4               # pixels of margin around the conterminous states
TOLERANCE_PX = 0.75   # simplification tolerance, in map pixels
MIN_PART_PX2 = 2.0    # islands and holes smaller than this many square pixels are dropped
#: States and territories outside the conterminous United States (state FIPS)
NOT_CONUS = {"02", "15", "60", "66", "69", "72", "78"}


def input_digests() -> dict:
    """``{repo path: sha256}`` of what the map is built from (text with LF line ends, so a CRLF
    checkout records the same digest)."""
    out = {}
    for path in (ECOREGIONS, STATES, Path(__file__).resolve()):
        data = path.read_bytes()
        if path.suffix != ".gz":
            data = data.replace(b"\r\n", b"\n")
        out[path.relative_to(REPO).as_posix()] = hashlib.sha256(data).hexdigest()
    return out


def _load(path: Path) -> dict:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _num(tenths: int) -> str:
    text = f"{tenths / 10:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _parts(geom, min_area: float):
    """A (Multi)Polygon without the parts and holes smaller than ``min_area``, or None."""
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    keep = []
    for poly in polys:
        if poly.area < min_area:
            continue
        holes = [ring for ring in poly.interiors if Polygon(ring).area >= min_area]
        keep.append(Polygon(poly.exterior, holes))
    return MultiPolygon(keep) if keep else None


def _rings(geom):
    """The coordinate sequences of a (Multi)Polygon's rings or a (Multi)LineString's lines."""
    kind = geom.geom_type
    if kind == "Polygon":
        yield from ([geom.exterior.coords] + [r.coords for r in geom.interiors])
    elif kind == "LineString":
        yield geom.coords
    elif kind in ("MultiPolygon", "MultiLineString", "GeometryCollection"):
        for part in geom.geoms:
            yield from _rings(part)


def _path(geom, to_px, closed: bool) -> str:
    """SVG path data of a geometry in map pixels: each ring or line an absolute move, then
    relative lines in tenths of a pixel; one too small to draw is dropped."""
    out = []
    for coords in _rings(geom):
        pts = []
        for x, y in coords:
            p = to_px(x, y)
            if not pts or p != pts[-1]:
                pts.append(p)
        if closed and len(pts) > 1 and pts[0] == pts[-1]:
            pts.pop()
        if len(pts) < (3 if closed else 2):
            continue
        steps = " ".join(f"{_num(x - px)},{_num(y - py)}"
                         for (px, py), (x, y) in zip(pts, pts[1:]))
        out.append(f"M{_num(pts[0][0])},{_num(pts[0][1])}l{steps}" + ("z" if closed else ""))
    return "".join(out)


def build() -> dict:
    to_albers = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform
    regions = []
    for f in _load(ECOREGIONS)["features"]:
        p = f["properties"]
        regions.append((str(p["US_L3CODE"]), str(p["US_L3NAME"]),
                        transform(to_albers, shape(f["geometry"]))))
    minx = min(g.bounds[0] for *_rest, g in regions)
    miny = min(g.bounds[1] for *_rest, g in regions)
    maxx = max(g.bounds[2] for *_rest, g in regions)
    maxy = max(g.bounds[3] for *_rest, g in regions)
    scale = (WIDTH - 2 * PAD) / (maxx - minx)
    height = round((maxy - miny) * scale + 2 * PAD)
    tolerance = TOLERANCE_PX / scale

    min_area = MIN_PART_PX2 / scale ** 2

    def to_px(x, y):   # tenths of a map pixel
        return (round(((x - minx) * scale + PAD) * 10), round(((maxy - y) * scale + PAD) * 10))

    states = [_parts(transform(to_albers, shape(f["geometry"])), min_area)
              for f in _load(STATES)["features"]
              if str(f["properties"].get("fips")).zfill(2) not in NOT_CONUS]
    states = [s for s in states if s is not None]
    coast = unary_union(states).boundary
    pieces = unary_union([s.boundary for s in states])
    borders = [g for g in getattr(pieces, "geoms", [pieces])
               if coast.distance(g.interpolate(0.5, normalized=True)) > 1.0]
    lines = linemerge(MultiLineString(borders)).simplify(tolerance)
    return {
        "generated_by": "apps/deep/scripts/build_site_ecoregion_map.py",
        "sources": ["apps/deep/data/ecoregions_l3.geojson (EPA Level III ecoregions)",
                    "apps/deep/data/us_states.geojson.gz (US Census states, 1:500,000)"],
        "inputs": input_digests(),
        "projection": "EPSG:5070",
        "width": WIDTH,
        "height": height,
        "regions": [{"code": code, "name": name,
                     "d": _path(_parts(geom, min_area).simplify(tolerance, preserve_topology=True),
                                to_px, True)}
                    for code, name, geom in sorted(regions, key=lambda r: int(r[0]))],
        "states": _path(lines, to_px, False),
    }


def render() -> str:
    return json.dumps(build(), indent=1, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Write the STAF site's Level III ecoregion map.")
    parser.add_argument("--check", action="store_true", help="Exit 1 when the map is stale; write nothing.")
    args = parser.parse_args(argv)
    text = render()
    rel = OUT.relative_to(REPO).as_posix()
    if args.check:
        current = OUT.read_text(encoding="utf-8").replace("\r\n", "\n") if OUT.is_file() else ""
        if current != text:
            print(f"{rel} is stale: run py apps/deep/scripts/build_site_ecoregion_map.py")
            return 1
        print("ecoregion map: current")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"ecoregion map: {len(json.loads(text)['regions'])} regions, {len(text) // 1024} KB in {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
