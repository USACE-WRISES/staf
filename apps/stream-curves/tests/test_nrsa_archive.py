"""The multi-cycle NRSA archive under data/nrsa/.

These read the built artifacts and skip when they have not been built, in the
pattern the library replay tests already use. The load-bearing one is
``test_rebuilt_2018_19_reproduces_the_legacy_snapshot``: it is the evidence that
the crosswalk, the site matching, and the encoding are all right, because the
same values come out of EPA's own files as the prior R application put in the
bundled parquet.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from streamcurves.paths import DATA_DIR

NRSA_DIR = Path(DATA_DIR) / "nrsa"
CYCLES = ("1314", "1819", "2324")

pytestmark = pytest.mark.skipif(
    not (NRSA_DIR / "values.parquet").exists(),
    reason="multi-cycle archive not built (scripts/nrsa/build_values_table.py)",
)


@pytest.fixture(scope="module")
def stations() -> pd.DataFrame:
    return pd.read_parquet(NRSA_DIR / "stations.parquet")


@pytest.fixture(scope="module")
def visits() -> pd.DataFrame:
    return pd.read_parquet(NRSA_DIR / "site_visits.parquet")


@pytest.fixture(scope="module")
def values() -> pd.DataFrame:
    return pd.read_parquet(NRSA_DIR / "values.parquet")


# --------------------------------------------------------------------------- #
# station identity
# --------------------------------------------------------------------------- #

def test_every_cycle_is_present_and_sites_are_not_lost(visits):
    assert set(visits["cycle"]) == set(CYCLES)
    per_cycle = visits.groupby("cycle")["site_id"].nunique()
    # EPA's own site counts for the three cycles
    assert per_cycle["1314"] == 2069
    assert per_cycle["1819"] == 1919
    assert per_cycle["2324"] == 1916


def test_site_ids_never_collide_across_cycles(visits):
    """EPA renames every site each cycle, which is why SITE_ID cannot be the key."""
    by_cycle = {c: set(g["site_id"]) for c, g in visits.groupby("cycle")}
    assert by_cycle["1314"] & by_cycle["1819"] == set()
    assert by_cycle["1819"] & by_cycle["2324"] == set()
    assert by_cycle["1314"] & by_cycle["2324"] == set()


def test_pooling_more_than_doubles_the_station_pool(stations):
    legacy = pd.read_csv(Path(DATA_DIR) / "nrsa_sites.csv")
    assert len(stations) > 2 * legacy["site_id"].nunique()
    assert stations["station_key"].is_unique


def test_every_station_key_resolves_and_no_visit_is_orphaned(visits, stations):
    assert visits["station_key"].notna().all()
    assert set(visits["station_key"]) == set(stations["station_key"])


def test_two_sites_from_one_cycle_share_a_station_only_when_co_located(visits):
    """EPA sometimes samples a reference site and a probability site at the same
    place in the same cycle, and both then link to the same later record. That is
    a correct merge, but it means a panel selector has to pick one row per
    station per cycle rather than assuming there is only one."""
    per = visits.groupby(["station_key", "cycle"])["site_id"].nunique()
    shared = per[per > 1]
    assert len(shared) <= 10, shared.to_dict()
    assert shared.max() == 2


def test_repeat_visits_are_kept_as_separate_rows(visits):
    assert (visits["visit_no"] != "1").sum() > 100
    # "R" marks a repeat sample and must stay distinct from visit 1
    assert "R" in set(visits["visit_no"])
    assert not visits.duplicated(["cycle", "site_id", "visit_no"]).any()


def test_stations_carry_the_cycles_that_sampled_them(stations):
    for row in stations.head(200).itertuples():
        listed = set(str(row.cycles_sampled).split(","))
        assert listed <= set(CYCLES)
        assert int(row.n_cycles) == len(listed)
        assert str(row.most_recent_cycle) == max(listed)
        assert str(row.first_cycle) == min(listed)


def test_every_station_has_a_comid_by_inheritance_or_by_snapping(stations):
    """StreamCat is joined by COMID and the evidence index drops any record it
    cannot place on a reach, so a station without one is invisible to both.

    2023-24 publishes a COMID for only about a third of its sites. A station that
    links to an older cycle inherits that cycle's; the rest were snapped to NHD
    once and cached (scripts/nrsa/snap_missing_comids.py)."""
    assert stations["comid"].notna().all()
    linked = stations[stations["n_cycles"] > 1]
    assert linked["comid"].notna().all()

    if "comid_source" in stations.columns:
        sources = set(stations["comid_source"].dropna())
        assert sources <= {"epa_published", "nldi_snap"}
        # the snap is recorded rather than passed off as EPA's
        assert (stations["comid_source"] == "nldi_snap").sum() > 1000
        only_new = stations[stations["cycles_sampled"] == "2324"]
        assert (only_new["comid_source"] == "nldi_snap").sum() > 1000


# --------------------------------------------------------------------------- #
# values
# --------------------------------------------------------------------------- #

def test_values_cover_every_catalog_metric(values):
    catalog = pd.read_csv(Path(DATA_DIR) / "nrsa_metric_catalog.csv")
    for key in catalog["name"]:
        assert key in values.columns
    assert list(values.columns[:4]) == ["station_key", "cycle", "site_id", "visit_no"]


def test_rebuilt_2018_19_reproduces_the_legacy_snapshot(values):
    """The bundled parquet and EPA's own files must agree, or the crosswalk is wrong.

    They agree exactly, cell for cell, across all 1,919 sites and every metric
    EPA actually publishes for 2018-19. Getting there needed VISIT_NO to stay a
    string: two South Dakota sites carry both a "1" row and an "R" repeat row,
    and coercing to int collapsed them onto each other.

    The legacy files still stay byte identical, because three published
    assessments fingerprint them.
    """
    legacy = pd.read_parquet(Path(DATA_DIR) / "nrsa_metrics.parquet").set_index("site_id")
    rebuilt = values[(values.cycle == "1819") & (values.visit_no == "1")].set_index("site_id")
    sites = legacy.index.intersection(rebuilt.index)
    assert len(sites) >= 1919

    compared = differing = 0
    dissenting_sites: set[str] = set()
    for column in legacy.columns:
        if column not in rebuilt.columns:
            continue
        a = pd.to_numeric(legacy.loc[sites, column], errors="coerce")
        b = pd.to_numeric(rebuilt.loc[sites, column], errors="coerce")
        both = a.notna() & b.notna()
        if not both.any():
            continue
        mismatch = ~np.isclose(a[both], b[both], rtol=1e-4, atol=1e-6)
        compared += int(both.sum())
        differing += int(mismatch.sum())
        dissenting_sites.update(a[both].index[mismatch])

    assert compared > 800_000
    # exact, not approximate: any drift here means the crosswalk, the visit
    # handling, or the encoding is wrong
    assert differing == 0, f"{differing} of {compared} cells differ at {sorted(dissenting_sites)[:5]}"


def test_the_cycles_are_complementary_not_redundant(values):
    """No single cycle carries every metric family, which is the case for pooling."""
    def filled(cycle: str, prefix: str) -> int:
        sub = values[values.cycle == cycle]
        cols = [c for c in values.columns if c.startswith(prefix)]
        return int((sub[cols].notna().sum() > 0).sum())

    # 2018-19 is the only source of NRSA landscape metrics
    assert filled("1819", "land_") > 300
    assert filled("2324", "land_") <= 1
    # EPA publishes no site-level benthic or fish metrics for 2018-19; those columns
    # are backfilled from the legacy snapshot instead of recomputed, so they are
    # present and value_origins.csv says where they came from
    assert filled("1819", "bent_") == 125
    assert filled("1819", "fish_") == 180
    assert filled("1314", "bent_") >= 110
    assert filled("2324", "bent_") >= 110
    assert filled("2324", "fish_") >= 170
    # physical habitat is complete in the two newer cycles
    assert filled("1819", "phab_") == 155
    assert filled("2324", "phab_") == 155


def test_the_2018_19_biology_is_backfilled_not_invented():
    """EPA publishes none of it, so the values come from the bundled legacy snapshot
    that the published assessments were built on, exactly, and the origin table says so."""
    origins = pd.read_csv(NRSA_DIR / "value_origins.csv", dtype={"cycle": str})
    legacy = origins[origins.origin == "legacy_r_app"]
    assert set(legacy["cycle"]) == {"1819"}
    assert len(legacy) == 305, len(legacy)
    assert legacy["metric_key"].str.startswith(("bent_", "fish_")).all()

    # and the values are the legacy ones, cell for cell
    snapshot = pd.read_parquet(Path(DATA_DIR) / "nrsa_metrics.parquet").set_index("site_id")
    values = pd.read_parquet(NRSA_DIR / "values.parquet")
    rows = values[(values.cycle == "1819") & (values.visit_no == "1")].set_index("site_id")
    sites = snapshot.index.intersection(rows.index)
    for column in ("bent_EPT_NTAX", "bent_TOLRPIND", "fish_NAT_TOTLNTAX"):
        a = pd.to_numeric(snapshot.loc[sites, column], errors="coerce")
        b = pd.to_numeric(rows.loc[sites, column], errors="coerce")
        both = a.notna() & b.notna()
        assert both.sum() > 1000
        assert np.isclose(a[both], b[both], rtol=1e-4, atol=1e-6).all()


def test_nothing_epa_published_was_overwritten_by_the_backfill():
    origins = pd.read_csv(NRSA_DIR / "value_origins.csv", dtype={"cycle": str})
    # 2013-14 and 2023-24 publish their own benthic and fish metrics, so none of
    # their rows may be attributed to the legacy snapshot
    assert origins[origins.cycle.isin(["1314", "2324"])].origin.eq("epa_published").all()


# --------------------------------------------------------------------------- #
# crosswalk and manifest
# --------------------------------------------------------------------------- #

def test_the_crosswalk_resolves_every_metric_in_at_least_one_cycle():
    crosswalk = pd.read_csv(NRSA_DIR / "metric_crosswalk.csv", dtype={"cycle": str})
    catalog = pd.read_csv(Path(DATA_DIR) / "nrsa_metric_catalog.csv")
    assert set(crosswalk["metric_key"]) == set(catalog["name"])
    # one source per metric per cycle, never two
    assert not crosswalk.duplicated(["metric_key", "cycle"]).any()


def test_the_crosswalk_handles_the_two_naming_traps():
    crosswalk = pd.read_csv(NRSA_DIR / "metric_crosswalk.csv", dtype={"cycle": str})
    by = crosswalk.set_index(["metric_key", "cycle"])["source_column"]
    # chemistry columns are <ANALYTE>_RESULT in every cycle's wide file; the bare
    # analyte spelling only appears in 2013-14's indicator table, which loses
    assert by[("chem_ANC", "1314")] == "ANC_RESULT"
    assert by[("chem_ANC", "1819")] == "ANC_RESULT"
    assert by[("chem_ANC", "2324")] == "ANC_RESULT"
    # and the phab files disagree on case
    assert by[("phab_LRBS_use", "1819")].upper() == "LRBS_USE"
    assert by[("phab_LRBS_use", "2324")].upper() == "LRBS_USE"


def test_the_manifest_matches_what_is_on_disk():
    manifest = json.loads((NRSA_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["datasetId"] == "multi-cycle-v1"
    assert set(manifest["cycles"]) == set(CYCLES)
    for relative, record in manifest["files"].items():
        path = NRSA_DIR / relative
        assert path.exists(), relative
        assert path.stat().st_size == record["bytes"], relative
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == record["sha256"], relative


def test_the_archive_stays_inside_its_size_budget():
    manifest = json.loads((NRSA_DIR / "manifest.json").read_text(encoding="utf-8"))
    # everything under apps/ ships in the desktop payload
    assert manifest["totalBytes"] / 1e6 < 40.0


def test_the_source_lock_pins_every_downloaded_file():
    lock = json.loads((NRSA_DIR / "sources.lock.json").read_text(encoding="utf-8"))
    assert len(lock["files"]) == 112
    for record in lock["files"].values():
        assert record["sha256"].startswith("sha256:")
        assert record["bytes"] > 0
        assert record["url"].startswith("https://")
        assert record["kind"] in ("data", "metadata")
