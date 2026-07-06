"""Tests for streamcurves.mapping (port of R/13_oh_parameter_map.R)."""

import pandas as pd
import pytest

from streamcurves import mapping as M


def _mk(rows):
    return pd.DataFrame(rows, columns=M.MAPPING_COLUMNS)


# --------------------------------------------------------------------------- #
# oh_parameter_map.yaml accessors (real bundled config)
# --------------------------------------------------------------------------- #
def test_fixed_discipline_order():
    assert M.fixed_discipline_order() == [
        "Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology",
    ]


def test_oh_accessors_real_config():
    # perRiffle is a Geomorphology / Bedform Diversity entry in the bundled yaml.
    assert M.oh_functional_category("perRiffle") == "Geomorphology"
    assert M.oh_function_parameter("perRiffle") == "Bedform Diversity"
    assert M.oh_units_display("perRiffle") == "%"
    assert isinstance(M.oh_reference_notes("perRiffle"), str)
    assert isinstance(M.oh_data_sources("perRiffle"), list)
    assert "perRiffle" in M.oh_covered_metrics()
    # unknown metric -> Nones / empties, never raises
    assert M.oh_functional_category("__nope__") is None
    assert M.oh_reference_notes("__nope__") == ""
    assert M.oh_data_sources("__nope__") == []


def test_oh_units_display_falls_back_to_metric_config():
    # a metric absent from the map with units in the (uploaded) metric_config
    cfg = {"widgetX": {"units": "mm"}}
    assert M.oh_units_display("widgetX", cfg) == "mm"
    assert M.oh_units_display("widgetX", None) is None


def test_oh_category_order_real_config():
    assert M.oh_category_order()[:2] == ["Hydrology", "Hydraulics"]


# --------------------------------------------------------------------------- #
# blank_function_mapping_scaffold
# --------------------------------------------------------------------------- #
def test_blank_scaffold_dedupes_and_filters():
    df = M.blank_function_mapping_scaffold(["a", "b", "a", "", None, "c"])
    assert df["metric_key"].tolist() == ["a", "b", "c"]
    assert df["sort_order"].tolist() == [1, 2, 3]
    assert df["discipline"].isna().all()
    assert df["function_label"].isna().all()


def test_blank_scaffold_empty():
    df = M.blank_function_mapping_scaffold([])
    assert list(df.columns) == M.MAPPING_COLUMNS
    assert len(df) == 0


# --------------------------------------------------------------------------- #
# function_mapping_full_coverage
# --------------------------------------------------------------------------- #
def test_full_coverage():
    m = _mk([
        {"metric_key": "a", "discipline": "Hydrology", "function_label": "F1", "sort_order": 1},
        {"metric_key": "b", "discipline": None, "function_label": None, "sort_order": 2},
    ])
    assert M.function_mapping_full_coverage(m, ["a"]) is True
    assert M.function_mapping_full_coverage(m, ["a", "b"]) is False
    # empty required is always covered
    assert M.function_mapping_full_coverage(m, []) is True
    # empty mapping: covered only when nothing required
    assert M.function_mapping_full_coverage(_mk([]), []) is True
    assert M.function_mapping_full_coverage(None, ["a"]) is False


# --------------------------------------------------------------------------- #
# validate_discipline_function_mapping
# --------------------------------------------------------------------------- #
def test_validate_ok_and_scaffold_rows_allowed():
    m = _mk([
        {"metric_key": "a", "discipline": "Hydrology", "function_label": "Catchment hydrology", "sort_order": 1},
        {"metric_key": "a", "discipline": "Biology", "function_label": "Habitat provision", "sort_order": 2},
        {"metric_key": "b", "discipline": None, "function_label": None, "sort_order": 3},
    ])
    assert M.validate_discipline_function_mapping(m) is True
    assert M.validate_discipline_function_mapping(None) is True


def test_validate_missing_columns_raises():
    with pytest.raises(ValueError, match="missing columns"):
        M.validate_discipline_function_mapping(pd.DataFrame({"metric_key": ["a"]}))


def test_validate_function_in_two_disciplines_raises():
    m = _mk([
        {"metric_key": "a", "discipline": "Hydrology", "function_label": "Flowy", "sort_order": 1},
        {"metric_key": "b", "discipline": "Biology", "function_label": "flowy", "sort_order": 2},
    ])
    with pytest.raises(ValueError, match="more than one discipline"):
        M.validate_discipline_function_mapping(m)


def test_validate_metric_same_function_twice_raises():
    m = _mk([
        {"metric_key": "a", "discipline": "Hydrology", "function_label": "F1", "sort_order": 1},
        {"metric_key": "a", "discipline": "Hydrology", "function_label": " f1 ", "sort_order": 2},
    ])
    with pytest.raises(ValueError, match="same function more than once"):
        M.validate_discipline_function_mapping(m)


# --------------------------------------------------------------------------- #
# metric_usage_counts
# --------------------------------------------------------------------------- #
def test_metric_usage_counts():
    m = _mk([
        {"metric_key": "a", "discipline": "Hydrology", "function_label": "F1", "sort_order": 1},
        {"metric_key": "a", "discipline": "Hydrology", "function_label": "F2", "sort_order": 2},
        {"metric_key": "a", "discipline": "Hydrology", "function_label": "F1", "sort_order": 3},
        {"metric_key": "b", "discipline": "Biology", "function_label": "F3", "sort_order": 4},
        {"metric_key": None, "discipline": "Biology", "function_label": "F4", "sort_order": 5},
    ])
    assert M.metric_usage_counts(m) == {"a": 2, "b": 1}
    assert M.metric_usage_counts(_mk([])) == {}


def test_function_label_owner_discipline():
    m = _mk([
        {"metric_key": "a", "discipline": "Hydrology", "function_label": "Catchment hydrology", "sort_order": 1},
    ])
    assert M.function_label_owner_discipline(m, "catchment HYDROLOGY") == "Hydrology"
    assert M.function_label_owner_discipline(m, "nope") is None
    assert M.function_label_owner_discipline(None, "x") is None


# --------------------------------------------------------------------------- #
# realign_discipline_function_mapping
# --------------------------------------------------------------------------- #
def test_realign_added_dropped_and_preserves_lib_and_buckets():
    m = _mk([
        {"metric_key": "a", "discipline": "Hydrology", "function_label": "F1", "sort_order": 1},
        {"metric_key": "c", "discipline": "Biology", "function_label": "F2", "sort_order": 2},
        {"metric_key": "lib:x", "discipline": "Biology", "function_label": "Habitat provision", "sort_order": 3},
        {"metric_key": None, "discipline": "Biology", "function_label": "Population support", "sort_order": 4},
    ])
    res = M.realign_discipline_function_mapping(m, ["a", "b", "d"])
    assert res["added"] == ["b", "d"]
    assert res["dropped"] == ["c"]
    out = res["mapping"]
    keys = out["metric_key"].tolist()
    # retained named 'a' first, then added scaffolds, then lib row + empty bucket
    assert keys[0] == "a"
    assert "b" in keys and "d" in keys
    assert "lib:x" in keys
    assert "c" not in keys
    # the empty bucket (NA metric_key) is preserved with its discipline/function
    na_rows = out[out["metric_key"].isna()]
    assert len(na_rows) == 1
    assert na_rows.iloc[0]["function_label"] == "Population support"
    # a's user assignment preserved
    arow = res["mapping"][res["mapping"]["metric_key"] == "a"].iloc[0]
    assert arow["discipline"] == "Hydrology" and arow["function_label"] == "F1"
    # sort_order renumbered 1..n
    assert res["mapping"]["sort_order"].tolist() == list(range(1, len(res["mapping"]) + 1))


def test_realign_empty_mapping_returns_scaffold():
    res = M.realign_discipline_function_mapping(None, ["a", "b"])
    assert res["added"] == ["a", "b"]
    assert res["dropped"] == []
    assert res["mapping"]["metric_key"].tolist() == ["a", "b"]


# --------------------------------------------------------------------------- #
# resolvers
# --------------------------------------------------------------------------- #
def test_resolvers_with_mapping_primary_first():
    m = _mk([
        {"metric_key": "a", "discipline": "Hydrology", "function_label": "F1", "sort_order": 1},
        {"metric_key": "a", "discipline": "Biology", "function_label": "F2", "sort_order": 2},
    ])
    assert M.resolve_metric_discipline("a", m) == "Hydrology"
    assert M.resolve_metric_function("a", m) == "F1"
    assert M.resolve_metric_discipline("zzz", m) is None
    assert M.resolve_metric_discipline(None, m) is None


def test_resolvers_fallback_to_yaml():
    assert M.resolve_metric_discipline("perRiffle") == "Geomorphology"
    assert M.resolve_metric_function("perRiffle") == "Bedform Diversity"


def test_resolved_category_order():
    assert M.resolved_category_order(None) == M.fixed_discipline_order()
    m = _mk([
        {"metric_key": "a", "discipline": "Biology", "function_label": "F1", "sort_order": 1},
        {"metric_key": "b", "discipline": "Zephyr", "function_label": "F2", "sort_order": 2},
        {"metric_key": "c", "discipline": None, "function_label": None, "sort_order": 3},
    ])
    # fixed disciplines present kept in canonical order, then extras appended
    assert M.resolved_category_order(m) == ["Biology", "Zephyr"]


def test_metrics_for_resolved_discipline_orders_by_function_then_sort():
    m = _mk([
        {"metric_key": "m2", "discipline": "Hydrology", "function_label": "B", "sort_order": 1},
        {"metric_key": "m1", "discipline": "Hydrology", "function_label": "A", "sort_order": 2},
        {"metric_key": "m3", "discipline": "Biology", "function_label": "A", "sort_order": 3},
    ])
    assert M.metrics_for_resolved_discipline("Hydrology", m) == ["m1", "m2"]
    # metric_keys filter
    assert M.metrics_for_resolved_discipline("Hydrology", m, ["m2"]) == ["m2"]
    assert M.metrics_for_resolved_discipline("Nope", m) == []


def test_functions_for_resolved_discipline():
    m = _mk([
        {"metric_key": "a", "discipline": "Hydrology", "function_label": "F1", "sort_order": 1},
        {"metric_key": None, "discipline": "Hydrology", "function_label": "F2", "sort_order": 2},
    ])
    assert M.functions_for_resolved_discipline("Hydrology", m) == ["F1", "F2"]
    assert M.functions_for_resolved_discipline("Hydrology", m, include_empty=False) == ["F1"]
