"""Cache each NRSA station's NHDPlus V2 stream order.

The reference panel is wadeable-only, and wadeable means Strahler order 1 to 5
(the owner's definition, methodology 0.10). The NRSA archive carries no order:
it carries a ``protocol`` flag, WADEABLE or BOATABLE, which is the field crew's
call about whether they could wade the reach on the day. The two are not the
same population. Measured on the three pilot ecoregions (301 stations,
2026-09-07): every WADEABLE station is order 1 to 5, but **57 stations of order
1 to 5 were sampled as BOATABLE**, so filtering on the protocol alone would
drop them from the reference frame the owner asked for.

So order comes from the network that defines it. This pulls ``streamorde`` for
every station COMID from the USGS fabric API (the same NHDPlus V2 flowline
collection the apps read) and caches it in ``data/nrsa/stream_order.csv``,
which ``nrsa_dataset`` maps onto the panel. Committed, so a run is offline and
reproducible; rerun only when the archive gains stations.

    py -3.12 scripts/nrsa/build_stream_order.py
    py -3.12 scripts/nrsa/build_stream_order.py --limit 200   # a smoke test
    py -3.12 scripts/nrsa/build_stream_order.py --force       # refetch everything
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT))

OUT_DIR = APP_ROOT / "data" / "nrsa"
CACHE_PATH = OUT_DIR / "stream_order.csv"
CACHE_COLUMNS = ["comid", "stream_order", "drainage_area_sqkm", "fetched_at"]

FABRIC_URL = ("https://api.water.usgs.gov/fabric/pygeoapi/collections/"
              "nhdflowline_network/items")
CHUNK = 40                      # COMIDs per CQL `IN` filter
TIMEOUT = 120.0
RETRIES = 3


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_cache(path: Path = CACHE_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame({c: pd.Series([], dtype=object) for c in CACHE_COLUMNS})
    return pd.read_csv(path)


def station_comids() -> list[int]:
    """Every distinct COMID in the multi-cycle station table (0 is the archive's
    sentinel for "EPA published none")."""
    stations = pd.read_parquet(OUT_DIR / "stations.parquet", columns=["comid"])
    ids = pd.to_numeric(stations["comid"], errors="coerce").dropna().astype("int64")
    return sorted({int(c) for c in ids if c > 0})


def fetch_orders(comids: list[int], *, chunk: int = CHUNK,
                 on_progress=None) -> tuple[dict[int, dict], list[int]]:
    """``({comid: {stream_order, drainage_area_sqkm}}, failed_chunk_starts)``."""
    out: dict[int, dict] = {}
    failed: list[int] = []
    for i in range(0, len(comids), chunk):
        part = comids[i:i + chunk]
        params = {"filter": "comid IN (" + ",".join(str(c) for c in part) + ")",
                  "properties": "comid,streamorde,totdasqkm",
                  "f": "json", "limit": len(part) + 5}
        got = None
        for attempt in range(RETRIES):
            try:
                r = requests.get(FABRIC_URL, params=params, timeout=TIMEOUT)
                r.raise_for_status()
                got = r.json().get("features") or []
                break
            except Exception as exc:  # noqa: BLE001 - a flaky service must not end the run
                print(f"  chunk at {i}: attempt {attempt + 1} failed "
                      f"({type(exc).__name__})", flush=True)
                time.sleep(2.0 * (attempt + 1))
        if got is None:
            failed.append(i)
            continue
        for f in got:
            p = f.get("properties") or {}
            if p.get("comid") is None:
                continue
            out[int(p["comid"])] = {"stream_order": p.get("streamorde"),
                                    "drainage_area_sqkm": p.get("totdasqkm")}
        if on_progress is not None:
            on_progress(min(i + chunk, len(comids)), len(comids))
    return out, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None,
                    help="fetch at most this many COMIDs (a smoke test)")
    ap.add_argument("--force", action="store_true",
                    help="refetch every COMID, not just the ones with no order yet")
    a = ap.parse_args()

    cache = load_cache()
    have = set()
    if not a.force and len(cache):
        have = {int(c) for c in pd.to_numeric(cache["comid"], errors="coerce").dropna()}
    wanted = [c for c in station_comids() if c not in have]
    if a.limit:
        wanted = wanted[:a.limit]
    print(f"{len(have)} cached, {len(wanted)} to fetch", flush=True)
    if not wanted:
        return 0

    fetched, failed = fetch_orders(
        wanted, on_progress=lambda i, n: print(f"  {i}/{n}", flush=True))
    rows = [{"comid": c, "stream_order": v.get("stream_order"),
             "drainage_area_sqkm": v.get("drainage_area_sqkm"), "fetched_at": _now()}
            for c, v in sorted(fetched.items())]
    out = pd.concat([cache, pd.DataFrame(rows, columns=CACHE_COLUMNS)],
                    ignore_index=True) if len(cache) and not a.force else pd.DataFrame(
        rows, columns=CACHE_COLUMNS)
    out = out.drop_duplicates("comid", keep="last").sort_values("comid")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(CACHE_PATH, index=False)
    unresolved = [c for c in wanted if c not in fetched]
    print(f"wrote {CACHE_PATH.relative_to(APP_ROOT)}: {len(out)} COMIDs "
          f"({len(unresolved)} unresolved, {len(failed)} chunks failed)", flush=True)
    if unresolved[:10]:
        print("  unresolved sample:", unresolved[:10], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
