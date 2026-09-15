"""Build EASI's Level III -> Level II / I crosswalk from the approved analysis CSV.

Run from apps/easi with the shared workspace interpreter. The GeoJSON coverage
check prevents an incomplete crosswalk from silently forcing national curves.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path("D:/Data/easi-national/analysis/l3_to_l2_l1.csv")


def _integer_code(value) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"invalid ecoregion code {value!r}")
    return str(int(text))


def build_crosswalk(source: Path, ecoregions: Path) -> dict:
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    result = {"l3": {}, "l2": {}}
    for row in rows:
        l3, l1 = _integer_code(row["l3"]), _integer_code(row["l1"])
        l2 = row["l2"].strip()
        l2_name, l3_name = row["l2_name"].strip(), row["l3_name"].strip()
        if not l2 or not l2_name or not l3_name:
            raise ValueError(f"missing ecoregion identity for Level III {l3}")
        if l3 in result["l3"]:
            raise ValueError(f"duplicate Level III code {l3}")
        if l2.split(".", 1)[0] != l1:
            raise ValueError(f"Level II {l2} does not belong to Level I {l1}")
        name = {"name": l2_name}
        if l2 in result["l2"] and result["l2"][l2] != name:
            raise ValueError(f"inconsistent Level II name for {l2}")
        result["l3"][l3] = {"l2": l2, "l1": l1, "name": l3_name}
        result["l2"][l2] = name
    features = json.loads(ecoregions.read_text(encoding="utf-8")).get("features", [])
    required = {_integer_code(feature["properties"]["US_L3CODE"]) for feature in features}
    missing = sorted(required - result["l3"].keys(), key=int)
    if not required or missing:
        raise ValueError(f"crosswalk does not cover bundled Level III codes: {missing}")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--ecoregions", type=Path, default=APP_ROOT / "data/ecoregions_l3.geojson")
    parser.add_argument("--out", type=Path, default=APP_ROOT / "data/ecoregion-crosswalk.json")
    args = parser.parse_args(argv)
    crosswalk = build_crosswalk(args.source, args.ecoregions)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(crosswalk, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {args.out}: {len(crosswalk['l3'])} Level III codes, {len(crosswalk['l2'])} Level II regions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
