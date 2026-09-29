"""Fetch and simplify EPA's official nine-region NARS polygon layer.

Run from anywhere:
    python apps/easi/scripts/fetch_nars_ecoregions.py [--allow-direct-write]

While the library's EASI version is the authority for the method files
(``easi.method_authority``: the library entry sits beside this EASI and its default version
equals ``data/``), this script refuses to write ``data/nars-ecoregions-9.geojson.gz`` (a method
file: the NARS-9 geography is inside the method version): a refreshed layer enters an authored
StreamCurves version and reaches EASI through
``apps/stream-curves/scripts/export_easi_method.py --write-easi-data``.
``--allow-direct-write`` is the transition escape, removed at adoption.
"""
from __future__ import annotations

import argparse
import json
import gzip
import sys
from pathlib import Path

import requests
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from easi import method_authority  # noqa: E402

OUT = ROOT / "data" / "nars-ecoregions-9.geojson.gz"
URL = (
    "https://geopub.epa.gov/ArcGIS/rest/services/OWOWM/NARS/MapServer/5/query"
)
PARAMS = {
    "where": "1=1",
    "outFields": "WSA_9,WSA_9_NM,ECOREGIONS",
    "returnGeometry": "true",
    "outSR": "4326",
    "f": "geojson",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--allow-direct-write", action="store_true",
                    help="write data/nars-ecoregions-9.geojson.gz although the library's EASI "
                         "version is the authority for it (transition only; the exporter is "
                         "the writer)")
    args = ap.parse_args(argv)
    if not args.allow_direct_write and method_authority.authority_active():
        method_authority.refuse_direct_write("fetch_nars_ecoregions.py")
    response = requests.get(URL, params=PARAMS, timeout=120)
    response.raise_for_status()
    source = response.json()
    features = []
    for feature in source.get("features") or []:
        geom = shape(feature["geometry"]).simplify(0.001, preserve_topology=True)
        props = feature.get("properties") or {}
        features.append({
            "type": "Feature",
            "properties": {
                "WSA_9": props.get("WSA_9"),
                "WSA_9_NM": props.get("WSA_9_NM"),
                "ECOREGIONS": props.get("ECOREGIONS"),
            },
            "geometry": mapping(geom),
        })
    output = {
        "type": "FeatureCollection",
        "name": "EPA NARS nine-region ecoregions",
        "source": URL,
        "features": features,
    }
    payload = (json.dumps(output, ensure_ascii=False, separators=(",", ":"))
               + "\n").encode("utf-8")
    with OUT.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           compresslevel=9, mtime=0) as stream:
            stream.write(payload)
    print(f"Wrote {OUT} with {len(features)} features ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
