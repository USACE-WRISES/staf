"""Resolve NRSA sites into stations that persist across survey cycles.

EPA renames every site each cycle (``NDLS-1072`` in 2013-14, ``NRS18_MN_10556``
in 2018-19, ``NRS23_AL_10003`` in 2023-24), so SITE_ID has exactly zero overlap
between cycles and cannot be used to tell whether two cycles sampled the same
place. Two links do work:

* ``UNIQUE_ID``, the persistent NARS station key, present in 2018-19 and
  2023-24 but absent from 2013-14.
* the NHDPlus ``COMID`` plus a coordinate check, which is the only option for
  2013-14.

The COMID rule was calibrated against the UNIQUE_ID pairing where both exist:
at a 100 m cut it recovers 0.964 of the UNIQUE_ID pairs with zero false
positives, and genuinely matched pairs sit a median of 0 m apart. Both links
feed one union-find, so a 2013-14 site that matches 2018-19 by COMID joins the
same station as the 2023-24 record that 2018-19 links to by UNIQUE_ID, which
matters because 2023-24 publishes a COMID for only about a third of its sites.

Outputs, under ``data/nrsa/``:

* ``site_visits.parquet``  one row per site-visit
* ``stations.parquet``     one row per distinct physical location
* ``stations.geojson.gz``  the same points, for the map tool

    py -3.12 scripts/nrsa/build_station_tables.py
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nrsa_io import normalize_visit_no, read_epa_csv  # noqa: E402

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parents[1]
RAW_DIR = REPO_ROOT / "notes" / "DEEP_Working" / "nrsa_raw"
OUT_DIR = APP_ROOT / "data" / "nrsa"

# EPA ships these as latin-1; reading them as UTF-8 raises on the degree signs
# and accented place names.
ENCODING = "latin-1"

# oldest first, so a station is keyed by the first cycle that sampled it
CYCLES = ("1314", "1819", "2324")

SITE_FILES = {
    "1314": "1314/nrsa1314_siteinformation_wide_04292019.csv",
    "1819": "1819/NRSA_1819_SiteInfo.csv",
    "2324": "2324/nrsa2324_siteinfo.csv",
}

# WADEABLE or BOATABLE, from the physical habitat file rather than site info.
# It matters because several habitat metrics are measured only on wadeable reaches
# (embeddedness, bank angle, canopy density, sinuosity), so a pool's protocol mix
# decides whether those metrics survive the missingness rules.
PHAB_FILES = {
    "1314": "1314/nrsa1314_phabmed_04232019.csv",
    "1819": "1819/nrsa_1819_physical_habitat_larger_set_of_metrics_-_data.csv",
    "2324": "2324/nrsa2324_physicalhabitat_metrics.csv",
}

CYCLE_YEARS = {"1314": 2014, "1819": 2019, "2324": 2024}

# canonical name -> the spellings a cycle might use
COLUMN_ALIASES = {
    "site_id": ["SITE_ID"],
    "unique_id": ["UNIQUE_ID"],
    "visit_no": ["VISIT_NO"],
    "year": ["YEAR"],
    "date_col": ["DATE_COL"],
    "lat": ["LAT_DD83"],
    "lon": ["LON_DD83"],
    "comid": ["COMID"],
    "us_l3code": ["US_L3CODE"],
    "us_l3name": ["US_L3NAME"],
    "ag_eco9": ["AG_ECO9"],
    "huc8": ["HUC8"],
    "state": ["STATE_NM", "STATE"],
    "pstl_code": ["PSTL_CODE"],
    "sitetype": ["SITETYPE"],
}

# the calibrated cut: true pairs sit a median of 0 m apart, the furthest at 120 m
MATCH_DISTANCE_M = 100.0

EARTH_RADIUS_M = 6371008.8


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlam = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _protocol_for(raw_dir: Path, cycle: str, sites: pd.DataFrame) -> pd.Series:
    """WADEABLE / BOATABLE per site-visit, from the cycle's physical habitat file."""
    path = raw_dir / PHAB_FILES[cycle]
    if not path.exists():
        return pd.Series([None] * len(sites), index=sites.index, dtype=object)
    phab = read_epa_csv(path, usecols=lambda c: c.upper() in
                        {"SITE_ID", "VISIT_NO", "PROTOCOL", "REALM"})
    column = next((c for c in ("PROTOCOL", "REALM") if c in phab.columns), None)
    if column is None or "SITE_ID" not in phab.columns:
        return pd.Series([None] * len(sites), index=sites.index, dtype=object)
    if "VISIT_NO" not in phab.columns:
        phab["VISIT_NO"] = 1
    phab["SITE_ID"] = phab["SITE_ID"].astype(str).str.strip()
    phab["VISIT_NO"] = normalize_visit_no(phab["VISIT_NO"])
    lookup = (phab.dropna(subset=[column])
              .drop_duplicates(["SITE_ID", "VISIT_NO"])
              .set_index(["SITE_ID", "VISIT_NO"])[column]
              .astype(str).str.strip().str.upper())
    keys = pd.MultiIndex.from_arrays([
        sites["site_id"].astype(str).str.strip(),
        normalize_visit_no(sites["visit_no"]),
    ])
    return pd.Series(lookup.reindex(keys).to_numpy(), index=sites.index, dtype=object)


def load_site_visits(raw_dir: Path) -> pd.DataFrame:
    frames = []
    for cycle in CYCLES:
        path = raw_dir / SITE_FILES[cycle]
        if not path.exists():
            raise SystemExit(f"missing {path}. Run scripts/nrsa/fetch_nrsa_raw.py first.")
        raw = read_epa_csv(path)
        out = pd.DataFrame({"cycle": cycle})if False else pd.DataFrame(index=raw.index)
        out["cycle"] = cycle
        for canonical, options in COLUMN_ALIASES.items():
            found = next((o for o in options if o in raw.columns), None)
            out[canonical] = raw[found] if found else None
        if out["year"].isna().all():
            out["year"] = CYCLE_YEARS[cycle]
        out["protocol"] = _protocol_for(raw_dir, cycle, out)
        frames.append(out)
    visits = pd.concat(frames, ignore_index=True)
    visits["site_id"] = visits["site_id"].astype(str).str.strip()
    # VISIT_NO is not always a number: "R" marks a repeat sample and must not
    # collapse onto visit 1
    visits["visit_no"] = normalize_visit_no(visits["visit_no"])
    for col in ("lat", "lon"):
        visits[col] = pd.to_numeric(visits[col], errors="coerce")
    visits["comid"] = pd.to_numeric(visits["comid"], errors="coerce").astype("Int64")
    visits["us_l3code"] = visits["us_l3code"].astype(str).str.strip().replace({"nan": None})
    return visits


def dedupe_visits(visits: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """One row per (cycle, site_id, visit_no), earliest record wins.

    EPA ships genuine duplicates: two Minnesota sites carry a visit 1 in both
    2018 and 2019, and 41 of the 2023-24 hand-picked "HP" sites appear twice.
    Dropping them here keeps site_visits and values on the same key.
    """
    before = len(visits)
    ordered = visits.sort_values(
        ["cycle", "site_id", "visit_no", "year", "date_col"], na_position="last")
    deduped = ordered.drop_duplicates(["cycle", "site_id", "visit_no"]).reset_index(drop=True)
    return deduped, before - len(deduped)


def site_table(visits: pd.DataFrame) -> pd.DataFrame:
    """One row per (cycle, site_id): the location the matching works on."""
    sites = (visits.sort_values(["cycle", "site_id", "visit_no"])
             .drop_duplicates(["cycle", "site_id"])
             .reset_index(drop=True))
    sites["key"] = sites["cycle"] + "|" + sites["site_id"]
    return sites


class Union:
    """Union-find over (cycle, site_id) keys."""

    def __init__(self, keys):
        self.parent = {k: k for k in keys}

    def find(self, k):
        root = k
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[k] != root:   # path compression
            self.parent[k], k = root, self.parent[k]
        return root

    def union(self, a, b) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        # keep the older cycle's key as the root so station keys stay stable
        self.parent[max(ra, rb)] = min(ra, rb)
        return True


def link_by_unique_id(sites: pd.DataFrame, union: Union) -> int:
    merged = sites[sites["unique_id"].notna() & (sites["unique_id"].astype(str) != "")]
    joined = 0
    for _, group in merged.groupby(merged["unique_id"].astype(str)):
        keys = list(group["key"])
        for other in keys[1:]:
            joined += int(union.union(keys[0], other))
    return joined


def link_by_comid(sites: pd.DataFrame, union: Union, *, cut_m: float) -> tuple[int, int]:
    """Same COMID and within ``cut_m``. Returns (joined, rejected_too_far)."""
    have = sites[sites["comid"].notna() & sites["lat"].notna() & sites["lon"].notna()]
    joined = rejected = 0
    for _, group in have.groupby(have["comid"].astype("int64")):
        if len(group) < 2:
            continue
        rows = group.to_dict("records")
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                if a["cycle"] == b["cycle"]:
                    continue    # two distinct sites in one cycle stay distinct
                dist = float(haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]))
                if dist <= cut_m:
                    joined += int(union.union(a["key"], b["key"]))
                else:
                    rejected += 1
    return joined, rejected


def build(raw_dir: Path, *, cut_m: float) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    visits, n_dropped = dedupe_visits(load_site_visits(raw_dir))
    sites = site_table(visits)

    union = Union(list(sites["key"]))
    n_uid = link_by_unique_id(sites, union)
    n_comid, n_far = link_by_comid(sites, union, cut_m=cut_m)

    sites["root"] = [union.find(k) for k in sites["key"]]
    # the root is the oldest cycle's key in the component; its site_id names the station
    roots = sites.drop_duplicates("root").set_index("root")
    sites["station_key"] = sites["root"].map(roots["site_id"])
    sites["match_basis"] = np.where(
        sites["key"] == sites["root"], "origin",
        np.where(sites["unique_id"].notna() & (sites["unique_id"].astype(str) != ""),
                 "unique_id", "comid_distance"))

    lookup = sites.set_index("key")["station_key"]
    visits["key"] = visits["cycle"] + "|" + visits["site_id"]
    visits["station_key"] = visits["key"].map(lookup)
    visits = visits.drop(columns=["key"])

    # one row per physical location
    ordered = sites.sort_values(["station_key", "cycle"])
    stations = ordered.groupby("station_key", as_index=False).agg(
        lat=("lat", "first"), lon=("lon", "first"),
        comid=("comid", "first"),
        us_l3code=("us_l3code", "first"), us_l3name=("us_l3name", "first"),
        ag_eco9=("ag_eco9", "first"), huc8=("huc8", "first"),
        state=("state", "first"), pstl_code=("pstl_code", "first"),
        sitetype=("sitetype", "first"),
        protocol=("protocol", "first"),
        first_cycle=("cycle", "first"), most_recent_cycle=("cycle", "last"),
        n_cycles=("cycle", "nunique"),
    )
    cycles_by_station = (ordered.groupby("station_key")["cycle"]
                         .apply(lambda s: ",".join(sorted(set(s)))))
    stations["cycles_sampled"] = stations["station_key"].map(cycles_by_station)
    visit_counts = visits.groupby("station_key").size()
    stations["n_visits"] = stations["station_key"].map(visit_counts).fillna(0).astype(int)

    # a station whose newest COMID is missing can still borrow an older one:
    # StreamCat is joined by COMID, and 2023-24 publishes it for only a third
    best_comid = (ordered[ordered["comid"].notna()]
                  .groupby("station_key")["comid"].first())
    stations["comid"] = stations["station_key"].map(best_comid).astype("Int64")

    # and where EPA published none in any cycle, the cached NHD snap
    # (scripts/nrsa/snap_missing_comids.py), so StreamCat and the evidence index
    # can still place the station on a reach
    n_snapped = 0
    snap_path = OUT_DIR / "comid_snapped.csv"
    if snap_path.exists():
        snapped = pd.read_csv(snap_path, dtype={"station_key": str})
        lookup = (snapped[snapped["comid"].notna()]
                  .drop_duplicates("station_key")
                  .set_index("station_key")["comid"])
        blank = stations["comid"].isna()
        filled = stations.loc[blank, "station_key"].astype(str).map(lookup)
        n_snapped = int(filled.notna().sum())
        stations.loc[blank, "comid"] = filled.astype("Int64").to_numpy()
        stations["comid_source"] = np.where(
            stations["station_key"].astype(str).isin(set(lookup.index)) & blank,
            "nldi_snap", np.where(stations["comid"].notna(), "epa_published", None))
    else:
        stations["comid_source"] = np.where(
            stations["comid"].notna(), "epa_published", None)

    report = {
        "site_visits": int(len(visits)),
        "dropped_duplicate_rows": int(n_dropped),
        "cycle_sites": {c: int((sites["cycle"] == c).sum()) for c in CYCLES},
        "stations": int(len(stations)),
        "linked_by_unique_id": n_uid,
        "linked_by_comid_distance": n_comid,
        "rejected_same_comid_too_far": n_far,
        "match_distance_m": cut_m,
        "stations_by_cycle_count": {
            int(k): int(v) for k, v in stations["n_cycles"].value_counts().sort_index().items()
        },
        "stations_missing_comid": int(stations["comid"].isna().sum()),
        "comid_filled_by_snap": n_snapped,
        "protocol_mix": {str(k): int(v) for k, v in
                         stations["protocol"].value_counts(dropna=False).items()},
    }
    return visits, stations, report


def write_geojson(stations: pd.DataFrame, path: Path) -> None:
    features = []
    for row in stations.itertuples(index=False):
        if pd.isna(row.lat) or pd.isna(row.lon):
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(row.lon), 6),
                                                          round(float(row.lat), 6)]},
            "properties": {
                "station_key": row.station_key,
                "cycles": row.cycles_sampled,
                "n_cycles": int(row.n_cycles),
                "n_visits": int(row.n_visits),
                "us_l3code": None if pd.isna(row.us_l3code) else str(row.us_l3code),
                "us_l3name": None if pd.isna(row.us_l3name) else str(row.us_l3name),
                "state": None if pd.isna(row.state) else str(row.state),
            },
        })
    payload = {"type": "FeatureCollection", "features": features}
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(path, "wb", mtime=0) as handle:   # mtime=0 keeps rebuilds byte-stable
        handle.write(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=Path, default=RAW_DIR)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--distance", type=float, default=MATCH_DISTANCE_M)
    args = ap.parse_args(argv)

    visits, stations, report = build(args.raw, cut_m=args.distance)
    args.out.mkdir(parents=True, exist_ok=True)
    visits.to_parquet(args.out / "site_visits.parquet", index=False, compression="zstd")
    stations.to_parquet(args.out / "stations.parquet", index=False, compression="zstd")
    write_geojson(stations, args.out / "stations.geojson.gz")

    print(json.dumps(report, indent=2))
    print()
    for name in ("site_visits.parquet", "stations.parquet", "stations.geojson.gz"):
        size = (args.out / name).stat().st_size
        print(f"  {name:<26} {size / 1e6:8.2f} MB")

    per_cycle = stations["cycles_sampled"].value_counts().sort_index()
    print("\nstations by the set of cycles that sampled them:")
    for combo, count in per_cycle.items():
        print(f"  {combo:<16} {count:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
