"""Choosing which NRSA data a run reads.

Two datasets exist:

``legacy-1819``
    The bundled ``nrsa_metrics.parquet`` and ``nrsa_sites.csv``, NRSA 2018-19
    only. This is the default and it must stay exactly as it is: three published
    assessments fingerprint those two files in their manifests
    (``provenance.build_inputs``), so changing them would break the
    reproducibility of work already published.

``multi-cycle-v1``
    The archive under ``data/nrsa/``, built from EPA's own files for 2013-14,
    2018-19 and 2023-24 and resolved into stations that persist across cycles.
    Rebuilding 2018-19 from it reproduces the legacy snapshot cell for cell, so
    the two agree wherever they overlap.

Pooling matters because the cycles are mostly *different places*, not repeat
visits: EPA renames every site each cycle, only 11 stations were sampled in all
three, and the station pool goes from 1,919 to 4,378.

``resolve_site_panel`` applies the selection policy: each station contributes one
row, taken from the most recent cycle that actually has the metrics a run needs.
"Complete" is judged against the run's own metric set rather than baked in, and
every station that is excluded is recorded with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Optional, Sequence

import pandas as pd

from .paths import DATA_DIR

LEGACY_DATASET_ID = "legacy-1819"
MULTI_CYCLE_DATASET_ID = "multi-cycle-v1"
DEFAULT_DATASET_ID = LEGACY_DATASET_ID

NRSA_DIR = DATA_DIR / "nrsa"

# newest first: "most recent" means the first of these a station satisfies
CYCLES_NEWEST_FIRST = ("2324", "1819", "1314")
CYCLE_LABELS = {"1314": "NRSA 2013-14", "1819": "NRSA 2018-19", "2324": "NRSA 2023-24"}

POLICY_MOST_RECENT = "most_recent_complete"
POLICIES = (POLICY_MOST_RECENT,)

# the shape select_candidates already consumes, from data/nrsa_sites.csv
PANEL_COLUMNS = [
    "site_id", "site_name", "lat", "lon", "state", "us_l3code", "us_l3name",
    "ag_eco9", "huc8", "source",
]


class DatasetUnavailable(RuntimeError):
    """The multi-cycle archive has not been built in this checkout."""


@dataclass(frozen=True)
class NrsaDataset:
    """A facade over one dataset, so callers do not care which is in play."""

    dataset_id: str
    sites: pd.DataFrame
    values: pd.DataFrame
    stations: Optional[pd.DataFrame] = None
    visits: Optional[pd.DataFrame] = None
    manifest: Optional[dict] = None

    @property
    def is_multi_cycle(self) -> bool:
        return self.stations is not None

    def metric_columns(self) -> list[str]:
        skip = {"site_id", "station_key", "cycle", "visit_no"}
        return [c for c in self.values.columns if c not in skip]


def multi_cycle_available() -> bool:
    return (NRSA_DIR / "values.parquet").exists() and (NRSA_DIR / "stations.parquet").exists()


def available_datasets() -> list[str]:
    out = [LEGACY_DATASET_ID]
    if multi_cycle_available():
        out.append(MULTI_CYCLE_DATASET_ID)
    return out


@lru_cache(maxsize=4)
def load_dataset(dataset_id: str = DEFAULT_DATASET_ID) -> NrsaDataset:
    """Load one dataset. Cached, because the parquet reads are not free."""
    dataset_id = str(dataset_id or DEFAULT_DATASET_ID)

    if dataset_id == LEGACY_DATASET_ID:
        from . import nrsa
        return NrsaDataset(
            dataset_id=dataset_id,
            sites=pd.read_csv(DATA_DIR / "nrsa_sites.csv", dtype={"us_l3code": str}),
            values=nrsa.load_nrsa_values(),
        )

    if dataset_id == MULTI_CYCLE_DATASET_ID:
        if not multi_cycle_available():
            raise DatasetUnavailable(
                "the multi-cycle archive is not built in this checkout; run "
                "scripts/nrsa/build_values_table.py or use the legacy dataset")
        import json
        stations = pd.read_parquet(NRSA_DIR / "stations.parquet")
        manifest_path = NRSA_DIR / "manifest.json"
        return NrsaDataset(
            dataset_id=dataset_id,
            sites=stations,
            values=pd.read_parquet(NRSA_DIR / "values.parquet"),
            stations=stations,
            visits=pd.read_parquet(NRSA_DIR / "site_visits.parquet"),
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists() else None,
        )

    raise ValueError(
        f"unknown NRSA dataset {dataset_id!r}; known: {', '.join(available_datasets())}")


def clear_cache() -> None:
    load_dataset.cache_clear()


# --------------------------------------------------------------------------- #
# panel selection
# --------------------------------------------------------------------------- #

def _legacy_panel(dataset: NrsaDataset, l3_code: Optional[str]) -> pd.DataFrame:
    sites = dataset.sites
    if l3_code is None:
        panel = sites.copy()
    else:
        panel = sites[
            sites["us_l3code"].astype(str).str.strip() == str(l3_code).strip()].copy()
    panel = panel.drop_duplicates("site_id")
    panel["station_key"] = panel["site_id"]
    panel["source_cycle"] = "1819"
    panel["visit_no"] = "1"
    return panel.reset_index(drop=True)


def resolve_site_panel(
    l3_code: Optional[str],
    *,
    dataset: str | NrsaDataset = DEFAULT_DATASET_ID,
    cycles: Sequence[str] = CYCLES_NEWEST_FIRST,
    policy: str = POLICY_MOST_RECENT,
    require_metrics: Iterable[str] = (),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One row per station for a Level III ecoregion, plus a rejection ledger.

    ``l3_code=None`` means every station, which is what the import wizard needs:
    its State and drawn-polygon region modes do not filter by ecoregion, so they
    have to start from the whole pool. Only the region filter is skipped, so the
    pooling policy, the ledger and the column shape stay in one implementation.

    Each station contributes exactly one row, taken from the most recent cycle in
    ``cycles`` that has a non-null value for every metric in ``require_metrics``.
    A station with no such cycle is excluded and appears in the ledger with the
    cycles that were tried and what was missing from each, so a shrunken pool is
    always explainable.

    Returns ``(panel, ledger)``. The panel carries the columns
    ``data/nrsa_sites.csv`` has, plus ``station_key``, ``source_cycle`` and
    ``visit_no``.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; known: {', '.join(POLICIES)}")
    ds = dataset if isinstance(dataset, NrsaDataset) else load_dataset(dataset)
    required = [m for m in dict.fromkeys(require_metrics) if m]

    if not ds.is_multi_cycle:
        panel = _legacy_panel(ds, l3_code)
        if required:
            values = ds.values.drop_duplicates("site_id").set_index("site_id")
            present = [m for m in required if m in values.columns]
            if present:
                joined = values.reindex(panel["site_id"])[present]
                ok = joined.notna().all(axis=1).to_numpy()
                rejected = panel.loc[~ok].copy()
                panel = panel.loc[ok].reset_index(drop=True)
                ledger = pd.DataFrame({
                    "station_key": rejected["station_key"],
                    "cycle": "1819",
                    "reason": "missing required metrics",
                    "missing": [
                        ", ".join(joined.loc[s][joined.loc[s].isna()].index)
                        for s in rejected["site_id"]
                    ],
                })
                return panel, ledger.reset_index(drop=True)
        return panel, pd.DataFrame(columns=["station_key", "cycle", "reason", "missing"])

    wanted = [c for c in CYCLES_NEWEST_FIRST if c in set(cycles)]
    stations = ds.stations
    if l3_code is None:
        in_region = stations
    else:
        in_region = stations[
            stations["us_l3code"].astype(str).str.strip() == str(l3_code).strip()]
    if in_region.empty:
        return (pd.DataFrame(columns=PANEL_COLUMNS + ["comid", "protocol", "station_key",
                                                      "source_cycle", "visit_no"]),
                pd.DataFrame(columns=["station_key", "cycle", "reason", "missing"]))

    keys = set(in_region["station_key"])
    visits = ds.visits[ds.visits["station_key"].isin(keys)]
    # the index visit is the one a curve should read
    visits = (visits.sort_values(["station_key", "cycle", "visit_no"])
              .drop_duplicates(["station_key", "cycle"]))

    values = ds.values
    have = [m for m in required if m in values.columns]

    ledger_rows: list[dict] = []
    if have:
        chosen = _pick_by_required_metrics(
            in_region, visits, values, have, wanted, ledger_rows)
    else:
        chosen = _pick_newest_cycle(in_region, visits, wanted, ledger_rows)

    panel = _build_panel(chosen, in_region)
    ledger = pd.DataFrame(ledger_rows, columns=["station_key", "cycle", "reason", "missing"])
    return panel, ledger


def _pick_newest_cycle(in_region, visits, wanted, ledger_rows) -> pd.DataFrame:
    """The newest cycle each station was visited in.

    With no metric requirement the pick has no per-station decision in it, so it
    vectorizes. The walk in :func:`_pick_by_required_metrics` costs about five
    seconds over the whole 4,378-station archive, and the import wizard asks for
    every station each time the region changes.
    """
    rank = {cycle: i for i, cycle in enumerate(wanted)}
    eligible = visits[visits["cycle"].isin(rank)].copy()
    eligible["_rank"] = eligible["cycle"].map(rank)
    eligible = (eligible.sort_values(["station_key", "_rank"])
                .drop_duplicates("station_key")
                .drop(columns="_rank")
                .set_index("station_key"))

    has_any_visit = set(visits["station_key"])
    picked_keys = []
    for key in in_region["station_key"]:
        if key in eligible.index:
            picked_keys.append(key)
        else:
            ledger_rows.append({
                "station_key": key, "cycle": "", "missing": "",
                "reason": ("no cycle in the requested set" if key in has_any_visit
                           else "no visit record"),
            })
    if not picked_keys:
        return pd.DataFrame(columns=eligible.reset_index().columns)
    return eligible.loc[picked_keys].reset_index()


def _pick_by_required_metrics(in_region, visits, values, have, wanted,
                              ledger_rows) -> pd.DataFrame:
    """The per-station walk: a cycle only counts when it has every required metric."""
    chosen = []
    # sorted so the per-station lookups below do not fall back to a linear scan
    value_index = values.set_index(["station_key", "cycle", "visit_no"]).sort_index()
    visits_by_station = {k: g for k, g in visits.groupby("station_key")}

    for key in in_region["station_key"]:
        group = visits_by_station.get(key)
        if group is None:
            ledger_rows.append({"station_key": key, "cycle": "", "reason": "no visit record",
                                "missing": ""})
            continue
        by_cycle = {row["cycle"]: row for _, row in group.iterrows()}
        picked = None
        tried = []
        for cycle in wanted:
            visit = by_cycle.get(cycle)
            if visit is None:
                continue
            if have:
                try:
                    row = value_index.loc[(key, cycle, visit["visit_no"])]
                except KeyError:
                    tried.append((cycle, "no values row"))
                    continue
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                missing = [m for m in have if pd.isna(row.get(m))]
                if missing:
                    tried.append((cycle, ", ".join(missing)))
                    continue
            picked = visit
            break
        if picked is None:
            for cycle, missing in tried:
                ledger_rows.append({"station_key": key, "cycle": cycle,
                                    "reason": "missing required metrics", "missing": missing})
            if not tried:
                ledger_rows.append({"station_key": key, "cycle": "",
                                    "reason": "no cycle in the requested set", "missing": ""})
            continue
        chosen.append(picked)

    return pd.DataFrame(chosen) if chosen else pd.DataFrame()


def _build_panel(chosen: pd.DataFrame, in_region: pd.DataFrame) -> pd.DataFrame:
    """The picked visits, shaped like ``data/nrsa_sites.csv`` plus the extras."""
    if chosen is None or len(chosen) == 0:
        panel = pd.DataFrame(
            columns=PANEL_COLUMNS + ["comid", "protocol", "station_key",
                                     "source_cycle", "visit_no"])
    else:
        picked = chosen
        # the station's COMID, which build_station_tables backfills from an older
        # cycle when the newest one has none: StreamCat is joined by COMID and
        # 2023-24 publishes it for only about a third of its sites
        by_station = in_region.set_index("station_key")
        station_comid = by_station["comid"]
        # WADEABLE or BOATABLE. Several habitat metrics are measured only on
        # wadeable reaches (embeddedness, bank angle, canopy density, sinuosity),
        # so a pool's protocol mix decides whether they survive the missingness
        # rules: on the Interior Plateau pool embeddedness is present for 30 of 30
        # wadeable stations and 1 of 34 boatable ones.
        station_protocol = (by_station["protocol"] if "protocol" in by_station.columns
                            else None)
        panel = pd.DataFrame({
            "site_id": picked["station_key"],
            "site_name": picked["site_id"],
            "lat": picked["lat"], "lon": picked["lon"],
            "state": picked["state"],
            "us_l3code": picked["us_l3code"], "us_l3name": picked["us_l3name"],
            "ag_eco9": picked["ag_eco9"], "huc8": picked["huc8"],
            "source": picked["cycle"].map(CYCLE_LABELS),
            "comid": picked["station_key"].map(station_comid),
            "protocol": (picked["station_key"].map(station_protocol)
                         if station_protocol is not None else None),
            "station_key": picked["station_key"],
            "source_cycle": picked["cycle"],
            "visit_no": picked["visit_no"],
        }).reset_index(drop=True)

    return panel


def panel_values(
    panel: pd.DataFrame,
    *,
    dataset: str | NrsaDataset = DEFAULT_DATASET_ID,
    metrics: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """The metric values for a resolved panel, one row per station, keyed by site_id.

    The result has the shape ``nrsa.attach_nrsa_metrics`` expects, so a pooled
    panel drops into the existing join without it knowing which cycle each row
    came from.
    """
    ds = dataset if isinstance(dataset, NrsaDataset) else load_dataset(dataset)
    if panel.empty:
        return pd.DataFrame({"site_id": pd.Series([], dtype=object)})

    if not ds.is_multi_cycle:
        values = ds.values.drop_duplicates("site_id")
        wanted = list(metrics) if metrics else ds.metric_columns()
        keep = ["site_id"] + [m for m in wanted if m in values.columns]
        return values[keep].reset_index(drop=True)

    merged = panel[["station_key", "source_cycle", "visit_no"]].merge(
        ds.values,
        left_on=["station_key", "source_cycle", "visit_no"],
        right_on=["station_key", "cycle", "visit_no"],
        how="left",
    )
    wanted = list(metrics) if metrics else ds.metric_columns()
    keep = [m for m in wanted if m in merged.columns]
    out = merged[["station_key"] + keep].rename(columns={"station_key": "site_id"})
    return out.reset_index(drop=True)
