"""National stratifier candidates for the regional agent.

The agent published assessments with no stratification analysis because no
candidate stratification had ever been defined, so there was nothing to screen.
These pin the replacement: that breakpoints stay declared constants, that every
candidate is accounted for whether it qualifies or not, and that the pruned class
column means the screening and feasibility engines describe the same groups.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import feasibility, screening, stratifiers


@pytest.fixture(scope="module")
def registry():
    return stratifiers.load_national_registry()


def _frame(**columns) -> pd.DataFrame:
    return pd.DataFrame(columns)


# --- breakpoints ------------------------------------------------------------ #
def test_registry_rules_partition_the_reals(registry):
    """Every value, boundaries included, must land in exactly one class.

    materialize_continuous_custom_group raises on an unmatched or overlapping
    value, so an off-by-one between a "<= x" and a "> x" pair would take the
    whole run down rather than mislabel one site.
    """
    for strat_key, cfg in registry["candidates"].items():
        edges = [10, 100, 0.5, 2, 300, 1000]
        probe = sorted({0.0, *edges, *[e - 1e-9 for e in edges], *[e + 1e-9 for e in edges],
                        1e-6, 1e6})
        built = stratifiers.derive.materialize_continuous_custom_group(
            pd.Series(probe, dtype=float), cfg, strat_key
        )
        assert pd.Series(built).notna().all(), f"{strat_key} leaves values unassigned"


def test_breakpoints_are_not_sample_derived(registry):
    """STRAT-08: shuffling or subsetting the rows must not move a class boundary."""
    values = pd.Series([1.0, 9.9, 10.0, 10.1, 99.0, 100.0, 101.0, 5000.0])
    cfg = registry["candidates"]["DrainageAreaClass"]

    full = list(stratifiers.derive.materialize_continuous_custom_group(
        values, cfg, "DrainageAreaClass"))
    shuffled_index = [5, 0, 7, 2, 4, 1, 6, 3]
    shuffled = list(stratifiers.derive.materialize_continuous_custom_group(
        values.iloc[shuffled_index].reset_index(drop=True), cfg, "DrainageAreaClass"))
    subset = list(stratifiers.derive.materialize_continuous_custom_group(
        values.iloc[:3].reset_index(drop=True), cfg, "DrainageAreaClass"))

    assert [full[i] for i in shuffled_index] == shuffled
    assert full[:3] == subset


def test_boundary_values_take_the_lower_class(registry):
    """The rules are written "<= x" / "> x", so exactly-10 is a headwater."""
    cfg = registry["candidates"]["DrainageAreaClass"]
    built = list(stratifiers.derive.materialize_continuous_custom_group(
        pd.Series([10.0, 10.0001, 100.0, 100.0001]), cfg, "DrainageAreaClass"))
    assert built[0].startswith("Headwater")
    assert built[1].startswith("Small")
    assert built[2].startswith("Small")
    assert built[3].startswith("Large")


# --- eligibility ledger ------------------------------------------------------ #
def _nrsa_like(n_small=6, n_large=6, slope=None, elev=None) -> pd.DataFrame:
    n = n_small + n_large
    return _frame(
        site_id=[f"S{i}" for i in range(n)],
        chem_PTL=np.linspace(1.0, 20.0, n),
        land_WSAREASQKM=[5.0] * n_small + [500.0] * n_large,
        phab_XSLOPE_use=slope if slope is not None else [0.2] * n,
        land_ELEVWS=elev if elev is not None else [500.0] * n,
    )


def test_ledger_records_every_candidate_including_passes(registry):
    data, skipped = stratifiers.materialize_candidates(_nrsa_like(), registry)
    ledger = stratifiers.assess_eligibility(data, registry, skipped=skipped)

    assert list(ledger["stratification"]) == list(registry["candidates"])
    assert list(ledger.columns) == stratifiers.LEDGER_COLUMNS
    passing = ledger.loc[ledger["stratification"] == "DrainageAreaClass"].iloc[0]
    assert passing["eligible"] is True or passing["eligible"] == True  # noqa: E712
    assert passing["exclusion_reason"] is None
    assert passing["level_counts"]  # populated even when the candidate passes


def test_ledger_excludes_constant_column(registry):
    """One populated level cannot be tested against anything."""
    data, skipped = stratifiers.materialize_candidates(_nrsa_like(n_small=12, n_large=0), registry)
    ledger = stratifiers.assess_eligibility(data, registry, skipped=skipped)
    row = ledger.loc[ledger["stratification"] == "DrainageAreaClass"].iloc[0]
    assert not row["eligible"]
    assert row["exclusion_reason"] == stratifiers.REASON_TOO_FEW_LEVELS


def test_ledger_excludes_sparse_level_and_names_it(registry):
    data, skipped = stratifiers.materialize_candidates(_nrsa_like(n_small=2, n_large=10), registry)
    ledger = stratifiers.assess_eligibility(data, registry, skipped=skipped)
    row = ledger.loc[ledger["stratification"] == "DrainageAreaClass"].iloc[0]
    assert not row["eligible"]
    assert row["exclusion_reason"].startswith(stratifiers.REASON_SPARSE_LEVEL)
    assert "n=2" in row["exclusion_reason"]


def test_ledger_excludes_missing_source_column(registry):
    data = _nrsa_like().drop(columns=["phab_XSLOPE_use"])
    data, skipped = stratifiers.materialize_candidates(data, registry)
    ledger = stratifiers.assess_eligibility(data, registry, skipped=skipped)
    row = ledger.loc[ledger["stratification"] == "ChannelSlopeClass"].iloc[0]
    assert not row["eligible"]
    assert row["exclusion_reason"] == stratifiers.REASON_SOURCE_MISSING
    assert not row["source_present"]
    # one bad column must not take the other candidates down with it
    assert stratifiers.eligible_keys(ledger) == ["DrainageAreaClass"]


def test_eligible_keys_follow_registry_order(registry):
    slope = [0.2] * 5 + [1.0] * 5 + [5.0] * 5
    data, skipped = stratifiers.materialize_candidates(
        _nrsa_like(n_small=8, n_large=7, slope=slope), registry)
    ledger = stratifiers.assess_eligibility(data, registry, skipped=skipped)
    keys = stratifiers.eligible_keys(ledger)
    assert keys == ["DrainageAreaClass", "ChannelSlopeClass"]
    assert isinstance(keys, list)


# --- the pruning contract ---------------------------------------------------- #
def test_unused_levels_are_pruned_so_feasibility_agrees_with_screening(registry):
    """assess_feasibility counts declared-but-unused categories as zero-size
    groups and calls the stratifier infeasible; screening._factor drops them
    first. Left unpruned the two engines disagree about the same column and an
    otherwise good candidate scores zero."""
    data, skipped = stratifiers.materialize_candidates(_nrsa_like(), registry)
    keys = stratifiers.eligible_keys(
        stratifiers.assess_eligibility(data, registry, skipped=skipped))
    strat_config = stratifiers.strat_config_for(registry, keys, data)

    # Only two of the three drainage classes are populated by this fixture.
    assert len(strat_config["DrainageAreaClass"]["levels"]) == 2

    feas = feasibility.assess_feasibility(data, ["DrainageAreaClass"], strat_config)
    assert feas["feasibility_flag"].iloc[0] == "feasible"
    assert int(feas["n_levels"].iloc[0]) == 2

    metric_config = {"chem_PTL": {
        "column_name": "chem_PTL", "metric_family": "continuous",
        "higher_is_better": False, "allowed_stratifications": ["DrainageAreaClass"],
    }}
    screened = screening.screen_stratification(
        data, "chem_PTL", "DrainageAreaClass", metric_config, strat_config)
    assert int(screened["result_row"]["n_groups"].iloc[0]) == int(feas["n_levels"].iloc[0])


def test_strat_config_entries_are_runtime_shaped(registry):
    data, skipped = stratifiers.materialize_candidates(_nrsa_like(), registry)
    keys = stratifiers.eligible_keys(
        stratifiers.assess_eligibility(data, registry, skipped=skipped))
    cfg = stratifiers.strat_config_for(registry, keys, data)["DrainageAreaClass"]

    assert cfg["is_custom_grouping"] is True
    assert cfg["column_name"] == "DrainageAreaClass"
    assert cfg["source_column"] == "land_WSAREASQKM"
    assert cfg["source_data_type"] == "continuous"
    # pairs come from the narrowed levels, not the declared three
    assert cfg["pairwise_comparisons"] == [list(cfg["levels"])]
    # the rules still partition the reals even though a class went unpopulated
    assert len(cfg["group_definitions"]) == 3


def test_rejected_candidates_carry_a_reason(registry):
    assert set(registry["rejected"]) == {"state", "huc8", "ag_eco9"}
    for key, entry in registry["rejected"].items():
        assert entry.get("reason"), key
        assert entry.get("detail"), key
