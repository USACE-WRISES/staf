"""Tests for streamcurves.staf_library (port of R/21_staf_metric_library.R).

Exercises the REAL bundled config/staf_metric_library.json (102 metrics,
functionPairCount 114) + config/staf_functions.json (20 functions).
"""

import pandas as pd

from streamcurves import mapping as M
from streamcurves import staf_library as SL


def test_function_meta_20_functions_in_order():
    meta = SL.staf_function_meta()
    assert list(meta.columns) == ["id", "name", "discipline", "order"]
    assert len(meta) == 20
    assert meta["order"].tolist() == list(range(1, 21))
    # first canonical function is Catchment hydrology / Hydrology
    assert meta.iloc[0]["id"] == "catchment-hydrology"
    assert meta.iloc[0]["name"] == "Catchment hydrology"
    assert meta.iloc[0]["discipline"] == "Hydrology"


def test_functions_by_discipline_covers_all_20():
    by = SL.staf_functions_by_discipline()
    assert list(by.keys()) == M.fixed_discipline_order()
    assert sum(len(v) for v in by.values()) == 20


def test_library_metrics_and_entries_counts():
    data = SL.load_staf_metric_library()
    assert data["metricCount"] == 102
    assert len(data["metrics"]) == 102
    ent = SL.staf_metric_library_entries()
    assert list(ent.columns) == SL.ENTRY_COLUMNS
    # exploded rows == header's functionPairCount (else R warns) == 114
    assert len(ent) == data["functionPairCount"] == 114
    # every primary metric is represented
    assert ent["is_primary"].sum() == 102
    # discipline is derived from the function's category
    hit = ent[ent["function_id"] == "catchment-hydrology"].iloc[0]
    assert hit["discipline"] == "Hydrology"
    assert hit["function_name"] == "Catchment hydrology"


def test_default_mapping_real_vs_lib_substitution():
    # pctimp2019 is a real app_metric_key in the library; when present in the
    # workbook metric_keys it keys real rows, else all rows fall back to lib:.
    dm = SL.staf_metric_library_default_mapping(["pctimp2019"])
    assert M.validate_discipline_function_mapping(dm) is True
    keys = dm["metric_key"].astype(str)
    assert (keys == "pctimp2019").any()             # real substitution
    assert keys.str.startswith("lib:").any()        # planned / no-data rows
    # no pctimp2019 assigned to the same function twice (pair-dedupe held)
    real = dm[dm["metric_key"] == "pctimp2019"]
    fns = real["function_label"].str.lower().str.strip()
    assert fns.is_unique


def test_default_mapping_primary_first():
    dm = SL.staf_metric_library_default_mapping([])
    # for a lib metric with an additional function, the primary sorts ahead.
    # pctimp2019's library entries are data-backed; use a purely-lib metric with
    # >1 function instead: find one from the exploded entries.
    ent = SL.staf_metric_library_entries()
    multi = (
        ent[~ent["is_primary"]]["library_id"].value_counts().index.tolist()
    )
    assert multi, "expected at least one metric with an additional function"
    lib_id = multi[0]
    key = f"lib:{lib_id}"
    rows = dm[dm["metric_key"] == key].sort_values("sort_order")
    # the primary function for this library metric appears at the lowest sort_order
    primary_fn = ent[(ent["library_id"] == lib_id) & (ent["is_primary"])]["function_name"].iloc[0]
    assert rows.iloc[0]["function_label"] == primary_fn


def test_default_mapping_unmatched_workbook_metric_appended_as_scaffold():
    dm = SL.staf_metric_library_default_mapping(["totally_custom_metric"])
    scaffold = dm[dm["metric_key"] == "totally_custom_metric"]
    assert len(scaffold) == 1
    assert pd.isna(scaffold.iloc[0]["discipline"])
    assert pd.isna(scaffold.iloc[0]["function_label"])
    # sort_order stays a contiguous 1..n after appending
    assert dm["sort_order"].tolist() == list(range(1, len(dm) + 1))


def test_default_mapping_empty_keys_all_lib():
    dm = SL.staf_metric_library_default_mapping([])
    assert (dm["metric_key"].astype(str).str.startswith("lib:")).all()
    assert M.validate_discipline_function_mapping(dm) is True
