"""Build the point-to-package lookup bundled with the benchmark app.

Fetches the NHDPlus HR HU4 outlines from the USGS fabric API
(``nhdplushr-huc04``), plus the HU8 outlines of the Alaska units that have
their own packages (``nhdplushr-huc08``), simplifies them to about 200 m, and
writes ``posit_app/vpu_index.geojson`` with one feature per package code. The
Great Lakes "i" packages (0418i and friends) reuse their HU4's outline, so a
lookup there returns both candidates.

    python tools/hr-gdal-poc/vpu_index.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests
import shapely

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "posit_app"))
import gdalpoc  # noqa: E402

FABRIC = "https://api.water.usgs.gov/fabric/pygeoapi/collections"
SIMPLIFY_DEG = 0.002


def _get(url: str, params: dict) -> dict:
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=180)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def fetch_huc04() -> dict:
    out, offset = {}, 0
    while True:
        page = _get(f"{FABRIC}/nhdplushr-huc04/items", {"f": "json", "limit": 10, "offset": offset})
        feats = page.get("features") or []
        for f in feats:
            out[str(f["id"])] = shapely.geometry.shape(f["geometry"])
        offset += len(feats)
        print(f"  HU4 outlines: {offset} of {page.get('numberMatched')}", flush=True)
        if not feats or offset >= int(page.get("numberMatched") or 0):
            return out


def fetch_huc08(codes: list[str]) -> dict:
    out = {}
    for code in codes:
        f = _get(f"{FABRIC}/nhdplushr-huc08/items/{code}", {"f": "json"})
        out[code] = shapely.geometry.shape(f["geometry"])
    return out


def main() -> int:
    packages = gdalpoc.list_packages()
    codes = [p["vpu"] for p in packages]
    print(f"{len(codes)} packages on S3")
    hu4 = fetch_huc04()
    ak = [c for c in codes if len(c) == 8]
    hu8 = fetch_huc08(ak)
    feats = []
    for code in codes:
        if len(code) == 8:
            geom = hu8.get(code)
        else:
            geom = hu4.get(code[:4])
        if geom is None:
            print("  no outline for", code)
            continue
        simple = shapely.set_precision(shapely.simplify(geom, SIMPLIFY_DEG, preserve_topology=True), 1e-4)
        feats.append({"type": "Feature", "properties": {"vpu": code},
                      "geometry": json.loads(shapely.to_geojson(simple))})
    path = HERE / "posit_app" / "vpu_index.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")),
                    encoding="utf-8")
    print(f"wrote {len(feats)} outlines to {path} ({path.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
