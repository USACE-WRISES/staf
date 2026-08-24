"""Snap the stations EPA published no COMID for to an NHDPlus flowline.

2023-24 publishes a COMID for only 865 of its 2,102 site-visits, and the other two
cycles publish one for every site. A station that links to an older cycle inherits
that cycle's COMID, which leaves 1,209 stations, essentially all of them new in
2023-24, with none at all.

That matters twice over: StreamCat is joined by COMID, and the reference-evidence
index drops any record it cannot place on a reach, so those stations are invisible
to the screen's evidence lookup.

The result is cached in ``data/nrsa/comid_snapped.csv`` and read by
``build_station_tables.py``, so this runs once. Rerunning only snaps what is still
missing unless ``--force`` is given.

    py -3.12 scripts/nrsa/snap_missing_comids.py
    py -3.12 scripts/nrsa/snap_missing_comids.py --limit 25   # a smoke test
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT))

OUT_DIR = APP_ROOT / "data" / "nrsa"
CACHE_PATH = OUT_DIR / "comid_snapped.csv"
CACHE_COLUMNS = ["station_key", "comid", "lat", "lon", "source", "snapped_at"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame({c: pd.Series([], dtype=object) for c in CACHE_COLUMNS})
    return pd.read_csv(path, dtype={"station_key": str})


def missing_stations(stations: pd.DataFrame, cache: pd.DataFrame) -> pd.DataFrame:
    """Stations with no COMID, no cached snap, and usable coordinates."""
    known = set(cache.loc[cache["comid"].notna(), "station_key"].astype(str))
    out = stations[stations["comid"].isna()].copy()
    out = out[~out["station_key"].astype(str).isin(known)]
    return out[out["lat"].notna() & out["lon"].notna()].reset_index(drop=True)


def snap(frame: pd.DataFrame, *, progress_every: int = 25) -> pd.DataFrame:
    """One NLDI lookup per point. Serial by nature; the cache is what makes it once."""
    from streamcurves.datasources.nldi import nldi_comid

    rows = []
    total = len(frame)
    for i, row in enumerate(frame.itertuples(index=False), start=1):
        try:
            comid = nldi_comid(float(row.lon), float(row.lat))
        except Exception:  # noqa: BLE001 - a failed point is recorded, not fatal
            comid = None
        rows.append({
            "station_key": str(row.station_key),
            "comid": None if comid is None else int(comid),
            "lat": float(row.lat), "lon": float(row.lon),
            "source": "nldi_snap", "snapped_at": _now(),
        })
        if progress_every and (i % progress_every == 0 or i == total):
            found = sum(1 for r in rows if r["comid"] is not None)
            print(f"  [{i}/{total}] snapped, {found} resolved", flush=True)
    return pd.DataFrame(rows, columns=CACHE_COLUMNS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stations", type=Path, default=OUT_DIR / "stations.parquet")
    ap.add_argument("--cache", type=Path, default=CACHE_PATH)
    ap.add_argument("--limit", type=int, default=None, help="snap at most this many")
    ap.add_argument("--force", action="store_true", help="re-snap what is already cached")
    args = ap.parse_args(argv)

    if not args.stations.exists():
        raise SystemExit(f"missing {args.stations}. Run build_station_tables.py first.")
    stations = pd.read_parquet(args.stations)
    cache = pd.DataFrame({c: pd.Series([], dtype=object) for c in CACHE_COLUMNS}) \
        if args.force else load_cache(args.cache)

    todo = missing_stations(stations, cache)
    print(f"{int(stations['comid'].isna().sum())} stations have no COMID; "
          f"{len(cache)} already cached; {len(todo)} to snap")
    if args.limit:
        todo = todo.head(args.limit)
        print(f"  limited to {len(todo)}")
    if todo.empty:
        print("nothing to do")
        return 0

    fresh = snap(todo)
    combined = pd.concat([cache, fresh], ignore_index=True)
    combined = combined.drop_duplicates("station_key", keep="last")
    combined = combined.sort_values("station_key")
    args.cache.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(args.cache, index=False, encoding="utf-8", lineterminator="\n")

    resolved = int(combined["comid"].notna().sum())
    print(f"\ncache now holds {len(combined)} stations, {resolved} resolved "
          f"({resolved / max(len(combined), 1) * 100:.0f}%)")
    print(f"wrote {args.cache.relative_to(APP_ROOT).as_posix()}")
    print("rerun scripts/nrsa/build_station_tables.py to fold these in")
    return 0


if __name__ == "__main__":
    sys.exit(main())
