"""Tests for streamcurves.cleaning (port of R/02_clean_data.R).

Synthetic expectations are pinned against the actual R functions (R 4.4,
D:/Code/Work/stream-curves) run on the same inputs.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from streamcurves.cleaning import (
    clean_data,
    expected_levels_for_column,
    factor_columns_from_metadata,
    factor_recode_target_columns,
)
from tests.golden_io import assert_frame_matches, has_golden, load_golden_df

FIXTURE = Path(__file__).parent / "fixtures" / "OSAM_summarydata.xlsx"


def _raw():
    return pd.DataFrame(
        {
            "Site": [" s1 ", "s2", "s3", "s4"],
            "Region": ["N", "S", "W", None],
            "Cover": ["none", "sparse", "dense", None],
            "Slope": [1.5, np.nan, 3.2, 8.8],
            "Width": [10.0, 20.0, np.nan, np.nan],
        }
    )


def _strat_config():
    return {
        "region": {
            "type": "single",
            "source_data_type": "categorical",
            "source_column": "Region",
            "column_name": "Region",
            "levels": ["N", "S"],
        },
        # continuous single strat: source is NOT factored
        "slope_grp": {
            "type": "single",
            "source_data_type": "continuous",
            "source_column": "Slope",
        },
        # categorical strat whose source is a factor-recode TARGET: skipped silently
        "cover_grp_strat": {
            "type": "single",
            "source_data_type": "categorical",
            "source_column": "CoverGrp",
            "column_name": "CoverGrp",
        },
        # categorical strat with a missing source column: QA warning
        "missing_strat": {
            "type": "single",
            "source_data_type": "categorical",
            "source_column": "Missing1",
            "column_name": "Missing1",
        },
    }


def _recode_config():
    return {
        "r1": {
            "source_column": "Cover",
            "target_column": "CoverGrp",
            "collapse_map": {"Low": ["none", "sparse"], "High": ["dense"]},
        }
    }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def test_factor_columns_from_metadata_order_and_filters():
    cols = factor_columns_from_metadata(_strat_config(), _recode_config())
    # strat sources first (config order), then recode sources; deduped
    assert cols == ["Region", "CoverGrp", "Missing1", "Cover"]


def test_factor_columns_from_metadata_skips_non_single_and_continuous():
    sc = {
        "paired": {"type": "paired", "source_data_type": "categorical", "source_column": "A"},
        "cont": {"type": "single", "source_data_type": "continuous", "source_column": "B"},
        "no_sdt": {"type": "single", "source_column": "C"},
        "ok": {"type": "single", "source_data_type": "categorical", "column_name": "D"},
    }
    # source_column %||% column_name fallback; missing source_data_type != "categorical"
    assert factor_columns_from_metadata(sc, {}) == ["D"]


def test_factor_recode_target_columns():
    assert factor_recode_target_columns(_recode_config()) == ["CoverGrp"]
    assert factor_recode_target_columns({}) == []
    assert factor_recode_target_columns(None) == []


def test_expected_levels_for_column():
    sc = _strat_config()
    assert expected_levels_for_column(sc, "Region") == ["N", "S"]
    assert expected_levels_for_column(sc, "CoverGrp") == []  # no levels declared
    # custom groupings are excluded
    sc2 = {
        "cust": {
            "type": "single",
            "is_custom_grouping": True,
            "column_name": "X",
            "levels": ["a"],
        }
    }
    assert expected_levels_for_column(sc2, "X") == []


# --------------------------------------------------------------------------- #
# clean_data — R-pinned probe case
# --------------------------------------------------------------------------- #


def test_clean_data_probe_case():
    data, qa_log = clean_data(_raw(), {}, _strat_config(), _recode_config())

    # qa_log rows exactly as the R run produced them
    assert list(qa_log.columns) == ["step", "message", "level"]
    assert qa_log["step"].tolist() == [
        "factor_conversion",
        "factor_conversion",
        "missing_data",
        "missing_data",
        "missing_data",
        "missing_data",
        "clean_summary",
    ]
    assert qa_log["message"].tolist() == [
        "Region: unexpected levels found: W",
        "Expected categorical column 'Missing1' not found in data",
        "Region: 1 missing (25%)",
        "Cover: 1 missing (25%)",
        "Slope: 1 missing (25%)",
        "Width: 2 missing (50%)",
        "Cleaned data: 4 rows x 5 columns",
    ]
    # 50% missing is NOT > 50 -> info
    assert qa_log["level"].tolist() == [
        "warning", "warning", "info", "info", "info", "info", "info",
    ]

    # character columns trimmed
    assert data["Site"].tolist() == ["s1", "s2", "s3", "s4"]
    # categorical strat source + recode source became factors (sorted levels)
    assert isinstance(data["Region"].dtype, pd.CategoricalDtype)
    assert list(data["Region"].cat.categories) == ["N", "S", "W"]
    assert isinstance(data["Cover"].dtype, pd.CategoricalDtype)
    assert list(data["Cover"].cat.categories) == ["dense", "none", "sparse"]
    # continuous columns untouched
    assert not isinstance(data["Slope"].dtype, pd.CategoricalDtype)
    assert not isinstance(data["Width"].dtype, pd.CategoricalDtype)


def test_clean_data_returns_tuple_and_does_not_mutate_input():
    raw = _raw()
    out = clean_data(raw, {}, _strat_config(), _recode_config())
    assert isinstance(out, tuple) and len(out) == 2
    # input untouched (R semantics: copy-on-modify)
    assert raw["Site"].tolist() == [" s1 ", "s2", "s3", "s4"]
    assert not isinstance(raw["Region"].dtype, pd.CategoricalDtype)


def test_clean_data_empty_configs_only_summary():
    df = pd.DataFrame({"a": [1.0, 2.0], "b": ["x", "y"]})
    data, qa_log = clean_data(df, {}, {}, {})
    assert qa_log["step"].tolist() == ["clean_summary"]
    assert qa_log["message"].tolist() == ["Cleaned data: 2 rows x 2 columns"]
    assert data["a"].tolist() == [1.0, 2.0]


def test_clean_data_missing_over_50_pct_is_warning():
    df = pd.DataFrame({"a": [1.0, np.nan, np.nan, np.nan]})
    _, qa_log = clean_data(df, {}, {}, {})
    row = qa_log[qa_log["step"] == "missing_data"].iloc[0]
    assert row["message"] == "a: 3 missing (75%)"
    assert row["level"] == "warning"


def test_clean_data_default_factor_recode_config():
    # factor_recode_config defaults to {} like the R signature's list()
    df = pd.DataFrame({"Region": ["N", "S"]})
    data, qa_log = clean_data(
        df,
        {},
        {"region": {"type": "single", "source_data_type": "categorical",
                    "source_column": "Region", "column_name": "Region"}},
    )
    assert isinstance(data["Region"].dtype, pd.CategoricalDtype)
    assert qa_log["step"].tolist() == ["clean_summary"]


def test_clean_data_no_unexpected_levels_when_all_declared():
    df = pd.DataFrame({"Region": ["N", "S", None]})
    sc = {
        "region": {
            "type": "single",
            "source_data_type": "categorical",
            "source_column": "Region",
            "column_name": "Region",
            "levels": ["N", "S"],
        }
    }
    _, qa_log = clean_data(df, {}, sc, {})
    assert "factor_conversion" not in qa_log["step"].tolist()


# --------------------------------------------------------------------------- #
# golden parity (skips when fixtures are absent)
# --------------------------------------------------------------------------- #


def _osam_bundle():
    if not FIXTURE.exists():
        pytest.skip("tests/fixtures/OSAM_summarydata.xlsx not present")
    try:
        from streamcurves import workbook as wb

        return wb.read_input_workbook(FIXTURE)
    except Exception as exc:  # pragma: no cover - sibling module regression
        pytest.skip(f"workbook bundle unavailable: {exc}")


def test_golden_qa_log():
    if not has_golden("02_qa_log"):
        pytest.skip("tests/golden/02_qa_log.json not present (run scripts/export_golden.R)")
    bundle = _osam_bundle()
    _, qa_log = clean_data(
        bundle["raw_data"],
        bundle["metric_config"],
        bundle["strat_config"],
        bundle["factor_recode_config"],
    )
    golden = load_golden_df("02_qa_log")
    assert_frame_matches(qa_log, golden, check_extra_py_cols=True)
