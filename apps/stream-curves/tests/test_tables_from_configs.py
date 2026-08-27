"""Rebuilding workbook tables from a built project's configs.

Published sessions carry the OUTCOME of a build, not the workbook that produced
it, so every library assessment reopened with ``input_metadata: null`` and an
empty Workbook panel. These pin the reconstruction -- above all that it keeps
each metric's real settings instead of regenerating them from defaults, which
would invert every "more is worse" metric on the next rebuild.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from streamcurves import session_io as sio
from streamcurves import workbook as wb
from streamcurves.profiler import build_config_tables_from_roles

NH = (
    Path(__file__).resolve().parents[2]
    / "library" / "assessments" / "northeastern-highlands" / "v2"
    / "session.streamcurves.json"
)


@pytest.fixture(scope="module")
def nh_fields():
    if not NH.exists():
        pytest.skip(f"library assessment not present: {NH}")
    return sio.decode_session_fields(json.loads(NH.read_text(encoding="utf-8")))


def _assignments_for(fields):
    """Role assignments as the classify step would produce them."""
    metric_cols = {v["column_name"] for v in fields["metric_config"].values()}
    cols = list(fields["data"].columns)
    return pd.DataFrame({
        "column": cols,
        "is_metric": [c in metric_cols for c in cols],
        "is_predictor": False,
        "is_stratifier": False,
    })


# --- the canary ------------------------------------------------------------- #
def test_round_trip_preserves_every_metric_direction(nh_fields):
    """config -> tables -> config must not touch higher_is_better.

    Many of Northeastern Highlands' metrics are `False` (turbidity, conductivity,
    phosphorus, nitrogen, chlorophyll, tolerant taxa, fine sediment, embeddedness,
    impervious cover, road density...). Regenerating from role defaults flips them
    all to True, which silently inverts the scoring of every "more is worse"
    metric. Counts are derived from the fixture, which changes on every republish;
    what must hold is that the round trip is exact.
    """
    mc = nh_fields["metric_config"]
    falses = {k for k, v in mc.items() if v.get("higher_is_better") is False}
    assert falses, "fixture carries no 'more is worse' metric; the canary is blind"

    tables = wb.tables_from_configs(nh_fields["data"], mc)
    rebuilt = wb.build_input_bundle_from_tables(tables)["metric_config"]

    assert set(rebuilt) == set(mc)
    for key, cfg in mc.items():
        assert rebuilt[key]["higher_is_better"] == cfg["higher_is_better"], key
    assert {k for k, v in rebuilt.items() if v["higher_is_better"] is False} == falses


def test_round_trip_preserves_a_two_sided_curve(nh_fields):
    """A two-sided ("optimum") metric degrades at BOTH extremes, so its direction
    is deliberately null. The metrics sheet had no curve_form column, so the null
    read back as TRUE and the curve silently became monotone-increasing."""
    mc = nh_fields["metric_config"]
    two_sided = {k for k, v in mc.items() if v.get("curve_form") == "optimum"}
    assert two_sided, "fixture carries no two-sided curve; the canary is blind"
    assert all(mc[k].get("higher_is_better") is None for k in two_sided)

    rebuilt = wb.build_input_bundle_from_tables(
        wb.tables_from_configs(nh_fields["data"], mc)
    )["metric_config"]
    for key in two_sided:
        assert rebuilt[key]["higher_is_better"] is None, key
        assert rebuilt[key].get("curve_form") == "optimum", key


def test_round_trip_preserves_the_other_curated_fields(nh_fields):
    mc = nh_fields["metric_config"]
    rebuilt = wb.build_input_bundle_from_tables(
        wb.tables_from_configs(nh_fields["data"], mc)
    )["metric_config"]
    for key, cfg in mc.items():
        for field in ("column_name", "display_name", "metric_family", "units",
                      "include_in_summary", "notes"):
            if field in cfg:
                assert rebuilt[key][field] == cfg[field], f"{key}.{field}"


def test_the_naive_rebuild_is_what_we_are_protecting_against(nh_fields):
    """Documents WHY tables_from_configs exists rather than reusing roles."""
    mc = nh_fields["metric_config"]
    tables = build_config_tables_from_roles(nh_fields["data"], _assignments_for(nh_fields))
    rebuilt = wb.build_input_bundle_from_tables(tables)["metric_config"]
    by_col = {v["column_name"]: v for v in rebuilt.values()}
    flipped = [
        k for k, v in mc.items()
        if v.get("higher_is_better") is False
        and by_col.get(v["column_name"], {}).get("higher_is_better") is not False
    ]
    expected = {k for k, v in mc.items() if v.get("higher_is_better") is False}
    assert set(flipped) == expected, "the naive rebuild should flip every False"


def test_overlay_restores_settings_onto_role_regenerated_tables(nh_fields):
    """The interlock: Build from a restored project must not flip directions."""
    mc = nh_fields["metric_config"]
    tables = build_config_tables_from_roles(nh_fields["data"], _assignments_for(nh_fields))
    rebuilt = wb.build_input_bundle_from_tables(
        wb.overlay_metric_settings(tables, mc)
    )["metric_config"]
    by_col = {v["column_name"]: v for v in rebuilt.values()}
    for cfg in mc.values():
        got = by_col[cfg["column_name"]]
        assert got["higher_is_better"] == cfg["higher_is_better"], cfg["column_name"]
        assert got["metric_family"] == cfg["metric_family"], cfg["column_name"]


def test_overlay_leaves_unknown_columns_on_their_defaults(nh_fields):
    """A column added after the fact has no curated settings to restore."""
    mc = dict(nh_fields["metric_config"])
    data = nh_fields["data"].copy()
    data["brand_new_col"] = 1.0
    asg = _assignments_for(nh_fields)
    asg = pd.concat([asg, pd.DataFrame([{
        "column": "brand_new_col", "is_metric": True,
        "is_predictor": False, "is_stratifier": False,
    }])], ignore_index=True)
    tables = wb.overlay_metric_settings(build_config_tables_from_roles(data, asg), mc)
    rebuilt = wb.build_input_bundle_from_tables(tables)["metric_config"]
    by_col = {v["column_name"]: v for v in rebuilt.values()}
    assert by_col["brand_new_col"]["higher_is_better"] is True   # the default
    assert by_col["chem_TURB"]["higher_is_better"] is False      # curated, kept


# --- shape ------------------------------------------------------------------ #
def test_sheets_match_the_workbook_schema(nh_fields):
    tables = wb.tables_from_configs(nh_fields["data"], nh_fields["metric_config"])
    assert len(tables["metrics"]) == len(nh_fields["metric_config"])
    assert set(tables["metrics"]["metric_key"]) == set(nh_fields["metric_config"])
    for sheet in ("metrics", "predictors", "stratifications", "factor_recodes"):
        desired = wb.workbook_sheet_columns()[sheet]
        assert list(tables[sheet].columns)[:len(desired)] == list(desired)


def test_count_family_drives_count_model(nh_fields):
    """metric_config has no count_model key, and the reader defaults it False."""
    mc = nh_fields["metric_config"]
    counts = {k for k, v in mc.items() if v.get("metric_family") == "count"}
    assert counts, "fixture changed; expected some count metrics"
    rebuilt = wb.build_input_bundle_from_tables(
        wb.tables_from_configs(nh_fields["data"], mc)
    )["metric_config"]
    for key in counts:
        assert rebuilt[key]["count_model"] is True, key


def test_empty_configs_yield_valid_empty_sheets():
    tables = wb.tables_from_configs(pd.DataFrame({"site_id": ["A"]}), {}, {}, {}, {})
    assert len(tables["metrics"]) == 0
    assert list(tables["metrics"].columns) == list(wb.workbook_sheet_columns()["metrics"])
    assert len(tables["data"]) == 1


def test_none_data_does_not_raise():
    tables = wb.tables_from_configs(None, {})
    assert tables["data"].empty


# --- stratifications --------------------------------------------------------- #
# These were latent because session_fields() called tables_from_configs without a
# strat_config. The moment a publisher passes one, publish() raised outright and a
# custom grouping round-tripped into a raw stratification on its own continuous
# source column, which would make screening treat every distinct value as a level.
def _da_class_config() -> dict:
    return {
        "DrainageAreaClass": {
            "display_name": "Drainage Area Class",
            "column_name": "DrainageAreaClass",
            "type": "single",
            "is_custom_grouping": True,
            "source_column": "land_WSAREASQKM",
            "source_data_type": "continuous",
            "min_group_size": 5,
            "levels": ["Headwater", "Small", "Large"],
            "pairwise_comparisons": [
                ["Headwater", "Small"], ["Small", "Large"], ["Headwater", "Large"],
            ],
            "notes": "Breakpoints 10 / 100 km2 are fixed constants.",
            "group_definitions": [
                {"group_label": "Headwater", "source_values": [],
                 "rule_expression": "<= 10", "sort_order": 1},
                {"group_label": "Small", "source_values": [],
                 "rule_expression": "> 10 & <= 100", "sort_order": 2},
                {"group_label": "Large", "source_values": [],
                 "rule_expression": "> 100", "sort_order": 3},
            ],
        }
    }


def _da_class_data() -> pd.DataFrame:
    data = pd.DataFrame({
        "site_id": ["a", "b", "c"],
        "land_WSAREASQKM": [5.0, 50.0, 500.0],
        "chem_PTL": [1.0, 2.0, 3.0],
    })
    data["DrainageAreaClass"] = pd.Categorical(
        ["Headwater", "Small", "Large"],
        categories=["Headwater", "Small", "Large"],
    )
    return data


def test_list_pairwise_comparisons_do_not_raise():
    """_flag_text got a list of pairs and reported it as an unparseable boolean."""
    tables = wb.tables_from_configs(_da_class_data(), {}, {}, _da_class_config())
    assert tables["stratifications"]["pairwise_comparisons"].iloc[0] == (
        "Headwater~Small|Small~Large|Headwater~Large"
    )


def test_custom_grouping_round_trips():
    sc = _da_class_config()
    tables = wb.tables_from_configs(_da_class_data(), {}, {}, sc)
    rebuilt = wb.build_input_bundle_from_tables(tables)["strat_config"]

    assert set(rebuilt) == set(sc)
    got = rebuilt["DrainageAreaClass"]
    assert got["is_custom_grouping"] is True
    assert got["source_column"] == "land_WSAREASQKM"
    assert got["column_name"] == "DrainageAreaClass"
    assert got["source_data_type"] == "continuous"
    assert got["levels"] == ["Headwater", "Small", "Large"]
    assert got["pairwise_comparisons"] == sc["DrainageAreaClass"]["pairwise_comparisons"]
    assert [d["group_label"] for d in got["group_definitions"]] == [
        "Headwater", "Small", "Large",
    ]
    assert [d["rule_expression"] for d in got["group_definitions"]] == [
        "<= 10", "> 10 & <= 100", "> 100",
    ]


def test_data_sheet_omits_derived_grouping_columns():
    """The data sheet is raw data by contract: build_input_bundle_from_tables
    refuses a grouping whose derived column already exists, so leaving it in makes
    Apply raise on any reopened assessment."""
    tables = wb.tables_from_configs(_da_class_data(), {}, {}, _da_class_config())
    assert "DrainageAreaClass" not in tables["data"].columns
    assert "land_WSAREASQKM" in tables["data"].columns


def test_allowed_stratifications_round_trip():
    mc = {"chem_PTL": {
        "column_name": "chem_PTL", "metric_family": "continuous",
        "higher_is_better": False, "allowed_stratifications": ["DrainageAreaClass"],
    }}
    tables = wb.tables_from_configs(_da_class_data(), mc, {}, _da_class_config())
    rebuilt = wb.build_input_bundle_from_tables(tables)["metric_config"]
    assert rebuilt["chem_PTL"]["allowed_stratifications"] == ["DrainageAreaClass"]


def test_dangling_metric_links_are_dropped_not_emitted():
    """A metric_config carries allowed_predictors and allowed_stratifications
    whether or not the caller passes the matching configs. Emitting a link row for
    a key the companion sheet does not define fails the foreign-key validation and
    makes the whole workbook unreadable."""
    mc = {"chem_PTL": {
        "column_name": "chem_PTL", "metric_family": "continuous",
        "higher_is_better": False,
        "allowed_predictors": ["elevws"],
        "allowed_stratifications": ["DrainageAreaClass"],
    }}
    data = pd.DataFrame({"site_id": ["a"], "chem_PTL": [1.0]})

    # No predictor or strat config: both links are dangling and must be dropped.
    tables = wb.tables_from_configs(data, mc)
    assert len(tables["metric_predictors"]) == 0
    assert len(tables["metric_stratifications"]) == 0
    wb.build_input_bundle_from_tables(tables)  # must not raise

    # Config supplied: the link survives.
    tables = wb.tables_from_configs(
        data, mc, {"elevws": {"column_name": "elevws", "type": "continuous"}})
    assert list(tables["metric_predictors"]["predictor_key"]) == ["elevws"]
    assert len(tables["metric_stratifications"]) == 0


# --------------------------------------------------------------------------- #
# What the Review & build tables show
#
# metric_key and predictor_key are sanitize_keys(column_name), so for NRSA and
# StreamCat data the two columns read the same and column_name was dropped from
# the display. source_column stays on Stratifications, where it is never
# redundant. These pin the reasoning so a later tidy-up does not take the wrong
# one away.
# --------------------------------------------------------------------------- #

def test_a_key_matches_its_column_for_a_published_metric_name():
    data = pd.DataFrame({"phab_XEMBED": [1.0, 2.0], "bfiws": [3.0, 4.0]})
    assignments = pd.DataFrame({
        "column": ["phab_XEMBED", "bfiws"],
        "is_metric": [True, False], "is_predictor": [False, True],
        "is_stratifier": [False, False],
    })
    tables = build_config_tables_from_roles(data, assignments)
    assert list(tables["metrics"]["metric_key"]) == list(tables["metrics"]["column_name"])
    assert list(tables["predictors"]["predictor_key"]) == list(
        tables["predictors"]["column_name"])


def test_an_uploaded_header_makes_the_key_and_the_column_diverge():
    """Why the workbook table keeps column_name even though the Review tab no
    longer shows it: an upload's header is not always a valid identifier."""
    data = pd.DataFrame({"Sand + fines (%)": [1.0, 2.0], "2019 imperv": [3.0, 4.0]})
    assignments = pd.DataFrame({
        "column": ["Sand + fines (%)", "2019 imperv"],
        "is_metric": [True, True], "is_predictor": [False, False],
        "is_stratifier": [False, False],
    })
    metrics = build_config_tables_from_roles(data, assignments)["metrics"]
    assert set(metrics["metric_key"]) == {"Sand_fines", "X2019_imperv"}
    assert set(metrics["column_name"]) == {"Sand + fines (%)", "2019 imperv"}


def test_a_numeric_stratifier_is_keyed_on_a_derived_column():
    """source_column is kept on the Stratifications table because of this: the
    key is the binned column, the source is the one it was binned from."""
    data = pd.DataFrame({"elevws": [10.0, 200.0, 400.0, 800.0, 1600.0, 3200.0]})
    assignments = pd.DataFrame({
        "column": ["elevws"], "is_metric": [False], "is_predictor": [False],
        "is_stratifier": [True],
    })
    strats = build_config_tables_from_roles(data, assignments)["stratifications"]
    assert len(strats) == 1
    assert strats["strat_key"].iat[0] != strats["source_column"].iat[0]
    assert strats["source_column"].iat[0] == "elevws"
    assert strats["strat_key"].iat[0].startswith("elevws")
