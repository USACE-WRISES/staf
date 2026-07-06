"""Tests for streamcurves.screening (port of R/05_stratification_screening.R).

Reference numbers marked "R:" were computed with R 4.x:
``kruskal.test``, ``wilcox.test(exact = FALSE)`` and ``p.adjust(method = "BH")``.

Golden parity runs ``run_all_stratification_screening`` on the exported R
pipeline data (``02_derived``) + configs (``01_bundle_meta``) and compares
against ``04_screening`` / ``04_pairwise`` (see scripts/export_golden.R).
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from streamcurves.screening import (
    run_all_stratification_screening,
    screen_paired_stratification,
    screen_stratification,
    streamcurves_site_id_column,
)

from tests.golden_io import (
    assert_frame_matches,
    has_golden,
    load_golden_df,
    load_golden_json,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

# Fields whose values are genuine lists in the R configs and must NOT be
# unboxed from their jsonlite length-1 wrapping.
_CONFIG_LIST_FIELDS = {
    "allowed_predictors",
    "allowed_stratifications",
    "pairwise_comparisons",
    "levels",
    "group_definitions",
}


def unbox_config(cfg: dict) -> dict:
    """Undo jsonlite ``auto_unbox=FALSE``: scalar config fields arrive as
    length-1 lists. Genuine list fields (``_CONFIG_LIST_FIELDS``) are kept."""
    out = {}
    for key, entry in (cfg or {}).items():
        e = {}
        for f, v in (entry or {}).items():
            if isinstance(v, list) and len(v) == 1 and f not in _CONFIG_LIST_FIELDS:
                e[f] = v[0]
            else:
                e[f] = v
        out[key] = e
    return out

RESULT_COLUMNS = [
    "metric", "stratification", "test", "statistic", "p_value",
    "n_groups", "min_group_n", "classification", "reason",
]
PAIRWISE_COLUMNS = [
    "metric", "stratification", "group1", "group2", "n1", "n2",
    "statistic", "p_value", "p_adjusted",
]


def _mc(**over):
    mc = {
        "column_name": "bkf_width",
        "display_name": "Bankfull Width",
        "units": "ft",
        "metric_family": "dimension",
        "higher_is_better": True,
        "min_sample_size": 3,
        "allowed_stratifications": ["region", "urban"],
    }
    mc.update(over)
    return {"bkf_width": mc}


def _sc(min_group_size=3, pairwise=None):
    return {
        "region": {
            "column_name": "region",
            "display_name": "Region",
            "min_group_size": min_group_size,
            **({"pairwise_comparisons": pairwise} if pairwise is not None else {}),
        }
    }


# --------------------------------------------------------------------------- #
# Kruskal-Wallis
# --------------------------------------------------------------------------- #


def test_kruskal_matches_r_reference():
    y = [1.1, 2.3, 2.3, 3.1, 4.0, 4.2, 5.5, 5.5, 6.1, 7.0, 7.2, 8.3]
    g = ["a"] * 4 + ["b"] * 4 + ["c"] * 4
    df = pd.DataFrame({"bkf_width": y, "region": g})

    out = screen_stratification(df, "bkf_width", "region", _mc(), _sc(min_group_size=3))
    row = out["result_row"].iloc[0]

    assert list(out["result_row"].columns) == RESULT_COLUMNS
    assert row["metric"] == "bkf_width"
    assert row["stratification"] == "region"
    assert row["test"] == "kruskal_wallis"
    # R: kruskal.test(y ~ g) -> chi-squared = 9.91549295774648, p = 0.00702874943407756
    assert row["statistic"] == pytest.approx(9.91549295774648, abs=1e-12)
    assert row["p_value"] == pytest.approx(0.00702874943407756, abs=1e-12)
    assert row["n_groups"] == 3
    assert row["min_group_n"] == 4
    assert row["classification"] == "selected"
    assert pd.isna(row["reason"])

    # and the same thing scipy computes directly
    ref = stats.kruskal(y[:4], y[4:8], y[8:])
    assert row["statistic"] == pytest.approx(float(ref.statistic), abs=1e-12)


def test_kruskal_runs_on_all_groups_even_below_min_group_size():
    """min_group_size only gates the attempt; the KW test itself includes
    every group present (any n >= 1)."""
    a = [1.0, 2, 3, 4, 5, 6]
    b = [11.0, 12, 13, 14, 15, 16]
    c = [8.0]
    df = pd.DataFrame(
        {"bkf_width": a + b + c, "region": ["a"] * 6 + ["b"] * 6 + ["c"]}
    )

    out = screen_stratification(df, "bkf_width", "region", _mc(), _sc(min_group_size=5))
    row = out["result_row"].iloc[0]

    with_tiny = float(stats.kruskal(a, b, c).statistic)
    without_tiny = float(stats.kruskal(a, b).statistic)
    assert row["statistic"] == pytest.approx(with_tiny, abs=1e-12)
    assert abs(row["statistic"] - without_tiny) > 1e-6  # tiny group really included
    assert row["n_groups"] == 3
    assert row["min_group_n"] == 1
    expected = "selected_sparse" if row["p_value"] < 0.05 else "not_selected_sparse"
    assert row["classification"] == expected


def test_kruskal_all_tied_values_gives_nan_like_r():
    """R kruskal.test returns NaN statistic/p on all-tied data (no error);
    scipy matches. Classification falls back to not_selected."""
    df = pd.DataFrame({"bkf_width": [5.0] * 6, "region": ["a"] * 3 + ["b"] * 3})
    out = screen_stratification(df, "bkf_width", "region", _mc(), _sc(min_group_size=3))
    row = out["result_row"].iloc[0]
    assert row["test"] == "kruskal_wallis"
    assert np.isnan(row["statistic"])
    assert np.isnan(row["p_value"])
    assert row["classification"] == "not_selected"


# --------------------------------------------------------------------------- #
# Early exits
# --------------------------------------------------------------------------- #


def test_missing_column_is_skipped():
    df = pd.DataFrame({"bkf_width": [1.0, 2.0]})
    out = screen_stratification(df, "bkf_width", "region", _mc(), _sc())
    row = out["result_row"].iloc[0]
    assert row["classification"] == "skipped"
    assert row["reason"] == "column_missing"
    assert pd.isna(row["test"])
    assert np.isnan(row["statistic"])
    assert np.isnan(row["p_value"])
    assert np.isnan(row["n_groups"])
    assert np.isnan(row["min_group_n"])
    assert len(out["pairwise_df"]) == 0
    assert out["plot_spec"] is None


def test_single_group_rejected_sparse():
    df = pd.DataFrame({"bkf_width": [1.0, 2, 3, 4], "region": ["only"] * 4})
    out = screen_stratification(df, "bkf_width", "region", _mc(), _sc())
    row = out["result_row"].iloc[0]
    assert row["classification"] == "rejected_sparse"
    assert row["reason"] == "fewer_than_2_groups"
    assert row["n_groups"] == 1
    assert row["min_group_n"] == 4
    assert out["plot_spec"] is None


def test_too_few_valid_groups_rejected_sparse_with_threshold_in_reason():
    df = pd.DataFrame(
        {"bkf_width": np.arange(12.0), "region": ["a"] * 10 + ["b"] * 2}
    )
    out = screen_stratification(df, "bkf_width", "region", _mc(), _sc(min_group_size=5))
    row = out["result_row"].iloc[0]
    assert row["classification"] == "rejected_sparse"
    assert row["reason"] == "fewer_than_2_groups_with_n>=5"
    assert row["n_groups"] == 2
    assert row["min_group_n"] == 2
    assert pd.isna(row["test"])
    assert out["plot_spec"] is None


# --------------------------------------------------------------------------- #
# Pairwise Wilcoxon + BH
# --------------------------------------------------------------------------- #


def test_pairwise_wilcoxon_matches_r_reference_and_sparse_pair_is_na():
    d1 = [1.5, 2.0, 2.0, 3.0, 4.5]          # "lo"
    d2 = [2.0, 3.5, 4.0, 4.5, 6.0, 7.0]     # "hi"
    df = pd.DataFrame(
        {
            "bkf_width": d1 + d2 + [9.9],
            "region": ["lo"] * 5 + ["hi"] * 6 + ["xx"],
        }
    )
    sc = _sc(min_group_size=2, pairwise=[["lo", "hi"], ["lo", "xx"]])

    out = screen_stratification(df, "bkf_width", "region", _mc(), sc)
    pw = out["pairwise_df"]
    assert list(pw.columns) == PAIRWISE_COLUMNS
    assert len(pw) == 2

    r0 = pw.iloc[0]
    assert (r0["group1"], r0["group2"]) == ("lo", "hi")
    assert (r0["n1"], r0["n2"]) == (5, 6)
    # R: wilcox.test(d1, d2, exact = FALSE) -> W = 5.5, p = 0.0964798035089584
    assert r0["statistic"] == pytest.approx(5.5, abs=1e-12)
    assert r0["p_value"] == pytest.approx(0.0964798035089584, abs=1e-12)
    # single non-NA p-value -> BH adjustment is the identity
    assert r0["p_adjusted"] == pytest.approx(0.0964798035089584, abs=1e-12)

    r1 = pw.iloc[1]
    assert (r1["n1"], r1["n2"]) == (5, 1)
    assert np.isnan(r1["statistic"])
    assert np.isnan(r1["p_value"])
    assert np.isnan(r1["p_adjusted"])


def _bh_reference(p):
    """Independent BH implementation (R p.adjust algorithm: descending cummin)."""
    p = np.asarray(p, dtype=float)
    m = len(p)
    order = np.argsort(p)[::-1]
    out = np.empty(m)
    running = np.inf
    for pos, idx in enumerate(order):
        rank = m - pos
        running = min(running, min(1.0, p[idx] * m / rank))
        out[idx] = running
    return out


def test_pairwise_bh_adjustment_across_multiple_pairs():
    rng = {"a": [1.0, 2, 3], "b": [2.0, 3, 4], "c": [5.0, 6, 7], "d": [1.5, 2.5, 3.5]}
    df = pd.DataFrame(
        {
            "bkf_width": rng["a"] + rng["b"] + rng["c"] + rng["d"],
            "region": ["a"] * 3 + ["b"] * 3 + ["c"] * 3 + ["d"] * 3,
        }
    )
    sc = _sc(min_group_size=2, pairwise=[["a", "b"], ["a", "c"], ["a", "d"]])
    out = screen_stratification(df, "bkf_width", "region", _mc(), sc)
    pw = out["pairwise_df"]
    assert len(pw) == 3
    assert pw["p_value"].notna().all()
    expected = _bh_reference(pw["p_value"].to_numpy())
    np.testing.assert_allclose(pw["p_adjusted"].to_numpy(), expected, atol=1e-12)
    # BH must actually rescale something here
    assert (np.abs(pw["p_adjusted"].to_numpy() - pw["p_value"].to_numpy()) > 1e-12).any()


def test_compute_pairwise_false_gives_empty_pairwise():
    df = pd.DataFrame({"bkf_width": np.arange(10.0), "region": ["a"] * 5 + ["b"] * 5})
    sc = _sc(min_group_size=2, pairwise=[["a", "b"]])
    out = screen_stratification(
        df, "bkf_width", "region", _mc(), sc, compute_pairwise=False
    )
    assert len(out["pairwise_df"]) == 0
    assert out["result_row"].iloc[0]["test"] == "kruskal_wallis"


# --------------------------------------------------------------------------- #
# Plot spec
# --------------------------------------------------------------------------- #


def test_plot_spec_labels_comparisons_and_site_id_dropna_quirk():
    df = pd.DataFrame(
        {
            "bkf_width": [1.0, 2, 3, 4, 5, 10, 11, 12, 13, 14],
            "region": ["north"] * 5 + ["south"] * 5,
            streamcurves_site_id_column: [1, 2, 3, 4, np.nan, 6, 7, 8, 9, 10],
        }
    )
    sc = _sc(min_group_size=2, pairwise=[["north", "south"], ["north", "west"]])
    out = screen_stratification(df, "bkf_width", "region", _mc(), sc)

    # tidyr::drop_na() quirk: the NA site id drops one north row entirely
    row = out["result_row"].iloc[0]
    assert row["min_group_n"] == 4
    assert row["n_groups"] == 2

    spec = out["plot_spec"]
    assert spec["type"] == "standard"
    assert spec["metric_key"] == "bkf_width"
    assert spec["strat_key"] == "region"
    assert spec["x_col"] == "group_label"
    assert spec["fill_col"] == "group_label"
    assert spec["y_col"] == "bkf_width"
    assert spec["title"] == "Bankfull Width by Region"
    assert spec["x_label"] == "Region"
    assert spec["y_label"] == "Bankfull Width"
    assert spec["palette"] == "viridis"
    # "west" has no data -> that comparison is filtered out
    assert spec["comparisons"] == [["north\n(n=4)", "south\n(n=5)"]]

    data_out = spec["data"]
    assert streamcurves_site_id_column in data_out.columns
    assert "group_label" in data_out.columns
    assert len(data_out) == 9
    assert sorted(set(data_out["group_label"])) == ["north\n(n=4)", "south\n(n=5)"]


# --------------------------------------------------------------------------- #
# Paired stratifications
# --------------------------------------------------------------------------- #


def _paired_configs(min_group_size=3):
    metric_config = _mc()
    strat_config = {
        "region": {"column_name": "region", "display_name": "Region"},
        "urban": {
            "column_name": "urban",
            "display_name": "Urbanization",
            "pairwise_comparisons": [["low", "high"]],
        },
        "region_x_urban": {
            "type": "paired",
            "display_name": "Region x Urban",
            "primary": "region",
            "secondary": "urban",
            "min_group_size": min_group_size,
        },
    }
    return metric_config, strat_config


def _paired_df(cells=(3, 3, 3, 2)):
    rows = []
    combos = [("north", "low"), ("north", "high"), ("south", "low"), ("south", "high")]
    v = 1.0
    for (region, urban), n in zip(combos, cells):
        for _ in range(n):
            rows.append({"bkf_width": v, "region": region, "urban": urban})
            v += 1.0
    return pd.DataFrame(rows)


def test_paired_screening_sparse_cells():
    metric_config, strat_config = _paired_configs(min_group_size=3)
    out = screen_stratification(
        _paired_df(), "bkf_width", "region_x_urban", metric_config, strat_config
    )
    row = out["result_row"].iloc[0]
    assert row["test"] == "paired_kruskal"
    assert np.isnan(row["statistic"])
    assert np.isnan(row["p_value"])
    assert row["n_groups"] == 4
    assert row["min_group_n"] == 2
    assert row["classification"] == "rejected_sparse"
    assert row["reason"] == "cells_with_n_lt_5"
    assert len(out["pairwise_df"]) == 0


def test_paired_screening_reason_threshold_is_hardcoded_5():
    """R quirk: classification uses min_group_size but the reason string check
    is a literal 5 — a 2-observation cell with min_group_size 2 is
    exploratory_only yet still carries the reason."""
    metric_config, strat_config = _paired_configs(min_group_size=2)
    out = screen_paired_stratification(
        _paired_df(), "bkf_width", "region_x_urban", metric_config, strat_config
    )
    row = out["result_row"].iloc[0]
    assert row["classification"] == "exploratory_only"
    assert row["reason"] == "cells_with_n_lt_5"


def test_paired_screening_ok_cells_and_plot_spec():
    metric_config, strat_config = _paired_configs(min_group_size=3)
    out = screen_stratification(
        _paired_df(cells=(5, 5, 5, 5)), "bkf_width", "region_x_urban",
        metric_config, strat_config,
    )
    row = out["result_row"].iloc[0]
    assert row["classification"] == "exploratory_only"
    assert pd.isna(row["reason"])
    assert row["n_groups"] == 4
    assert row["min_group_n"] == 5

    spec = out["plot_spec"]
    assert spec["type"] == "paired"
    assert spec["primary_key"] == "region"
    assert spec["secondary_key"] == "urban"
    assert spec["primary_col"] == "region"
    assert spec["secondary_col"] == "urban"
    assert spec["y_col"] == "bkf_width"
    assert spec["fill_col"] == "urban"
    assert spec["comparisons"] == [["low", "high"]]
    assert spec["title"] == "Bankfull Width by Region x Urban"
    assert spec["x_label"] == "Urbanization"
    assert spec["y_label"] == "Bankfull Width"


def test_paired_missing_column_is_skipped():
    metric_config, strat_config = _paired_configs()
    df = _paired_df().drop(columns=["urban"])
    out = screen_stratification(
        df, "bkf_width", "region_x_urban", metric_config, strat_config
    )
    row = out["result_row"].iloc[0]
    assert row["classification"] == "skipped"
    assert row["reason"] == "column_missing"
    assert out["plot_spec"] is None


# --------------------------------------------------------------------------- #
# run_all_stratification_screening
# --------------------------------------------------------------------------- #


def test_run_all_screening_work_list_and_shapes():
    metric_config, strat_config = _paired_configs(min_group_size=3)
    strat_config["region"]["min_group_size"] = 2
    strat_config["region"]["pairwise_comparisons"] = [["north", "south"]]
    metric_config["bkf_width"]["allowed_stratifications"] = [
        "region", "urban", "region_x_urban", "ghost",
    ]
    metric_config["substrate_class"] = {
        "column_name": "substrate_class",
        "display_name": "Substrate",
        "metric_family": "categorical",
        "allowed_stratifications": ["region"],
    }
    metric_config["no_strats"] = {
        "column_name": "bkf_width",
        "display_name": "No strats",
        "metric_family": "dimension",
        "allowed_stratifications": None,
    }

    df = _paired_df(cells=(5, 5, 5, 5))
    res = run_all_stratification_screening(df, metric_config, strat_config)

    assert set(res.keys()) == {"results", "pairwise", "plot_specs"}
    results = res["results"]
    assert list(results.columns) == RESULT_COLUMNS
    # categorical metric skipped, missing strat key skipped, None allowed skipped
    assert list(results["stratification"]) == ["region", "urban", "region_x_urban"]
    assert list(results["metric"]) == ["bkf_width"] * 3

    assert set(res["plot_specs"].keys()) == {
        "bkf_width_region", "bkf_width_urban", "bkf_width_region_x_urban",
    }
    # both region and urban carry pairwise_comparisons in these configs, so
    # both produce pairwise rows (R computes pairwise for any strat that has
    # pairwise_comparisons set, regardless of other config)
    assert set(res["pairwise"]["stratification"]) == {"region", "urban"}


# --------------------------------------------------------------------------- #
# Golden parity (skips when tests/golden/ fixtures are absent)
# --------------------------------------------------------------------------- #


def _golden_configs():
    meta = load_golden_json("01_bundle_meta")
    return unbox_config(meta["metric_config"]), unbox_config(meta["strat_config"])


def test_golden_screening_results():
    if not (has_golden("04_screening") and has_golden("02_derived") and has_golden("01_bundle_meta")):
        pytest.skip("screening golden fixtures not present (run scripts/export_golden.R)")
    metric_config, strat_config = _golden_configs()
    data = load_golden_df("02_derived")
    res = run_all_stratification_screening(data, metric_config, strat_config)

    golden = load_golden_df("04_screening")
    # 22 metrics, 147 metric x stratification combinations in the OSAM golden run
    assert len(res["results"]) == len(golden)
    assert_frame_matches(
        res["results"], golden, keys=["metric", "stratification"]
    )


def test_golden_screening_pairwise():
    if not (has_golden("04_pairwise") and has_golden("02_derived") and has_golden("01_bundle_meta")):
        pytest.skip("pairwise golden fixtures not present (run scripts/export_golden.R)")
    metric_config, strat_config = _golden_configs()
    data = load_golden_df("02_derived")
    res = run_all_stratification_screening(data, metric_config, strat_config)

    golden = load_golden_df("04_pairwise")
    assert len(res["pairwise"]) == len(golden)
    assert_frame_matches(
        res["pairwise"], golden, keys=["metric", "stratification", "group1", "group2"]
    )
