"""Build ``staf_workbook/data/ecoregions.json`` from EASI's bundled region data (development only).

For each EPA Level III ecoregion: its name, Level II code and name, Level I code, and the NARS-9
region that holds most of its area (measured on EPSG:5070), with that share. Sources: EASI's
``data/ecoregion-crosswalk.json``, ``data/ecoregions_l3.geojson`` and
``data/nars-ecoregions-9.geojson.gz``; their sha256 values are recorded in the output.

Run:  python libs/staf_workbook/scripts/build_ecoregion_table.py
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[3]
EASI_DATA = REPO / "apps" / "easi" / "data"
OUT = HERE.parents[1] / "staf_workbook" / "data" / "ecoregions.json"
KEEP_UPPER = {"USA"}
LOWER = {"AND", "OF", "THE"}


def nice(name: str) -> str:
    words = []
    for i, w in enumerate(str(name).title().split(" ")):
        up = w.upper()
        if up in KEEP_UPPER:
            words.append(up)
        elif up in LOWER and i:
            words.append(w.lower())
        else:
            words.append(w)
    return " ".join(words)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    cross_p = EASI_DATA / "ecoregion-crosswalk.json"
    l3_p = EASI_DATA / "ecoregions_l3.geojson"
    nars_p = EASI_DATA / "nars-ecoregions-9.geojson.gz"
    cross = json.loads(cross_p.read_text(encoding="utf-8"))
    to5070 = Transformer.from_crs(4326, 5070, always_xy=True).transform
    nars = []
    with gzip.open(nars_p, "rt", encoding="utf-8") as fh:
        for f in json.load(fh)["features"]:
            nars.append((f["properties"]["WSA_9"], transform(to5070, shape(f["geometry"])).buffer(0)))
    l3_geoms = {}
    for f in json.loads(l3_p.read_text(encoding="utf-8"))["features"]:
        code = str(f["properties"]["US_L3CODE"])
        g = transform(to5070, shape(f["geometry"])).buffer(0)
        l3_geoms[code] = l3_geoms[code].union(g) if code in l3_geoms else g
    out = {}
    for code, entry in sorted(cross["l3"].items(), key=lambda kv: int(kv[0])):
        l2 = entry.get("l2")
        row = {"name": entry.get("name"), "l2": l2, "l2_name": nice((cross["l2"].get(l2) or {}).get("name") or ""),
               "l1": entry.get("l1")}
        g = l3_geoms.get(code)
        if g is not None and g.area > 0:
            shares = sorted(((g.intersection(ng).area / g.area, n) for n, ng in nars), reverse=True)
            row["nars9"], row["nars9_share"] = shares[0][1], round(shares[0][0], 4)
        out[code] = row
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = {"sources": {"ecoregion-crosswalk.json": sha(cross_p), "ecoregions_l3.geojson": sha(l3_p),
                       "nars-ecoregions-9.geojson.gz": sha(nars_p)}, "l3": out}
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    low = min(v.get("nars9_share", 1) for v in out.values())
    print(f"wrote {OUT} ({len(out)} Level III ecoregions; smallest NARS-9 majority share {low:.4f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
