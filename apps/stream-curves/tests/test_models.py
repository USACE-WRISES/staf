"""Tests for streamcurves.models (port of R/07_model_candidates.R + R/08_model_selection.R).

The R_* constants below were produced by running leaps 3.x / MASS on this
exact dataset with R 4.4.2 (see the frozen values' comments); they pin the
leaps-replica formulas (including the sigma2-from-full-model Cp semantics and
factor dummy handling) against the real thing.
"""

from itertools import combinations

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
import statsmodels.formula.api as smf

from streamcurves import models as M
from tests.golden_io import has_golden, load_golden_df, load_golden_json

# --------------------------------------------------------------------------- #
# R-verified dataset (R 4.4.2, leaps 3.2)
# --------------------------------------------------------------------------- #

X1 = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
X2 = [2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11]
X3 = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8]
G = ["a", "b", "c"] * 4
Y = [3.3, 4.6, 7.9, 8.1, 11.9, 12.2, 15.8, 16.1, 19.6, 20.3, 23.1, 24.9]


def r_frame() -> pd.DataFrame:
    return pd.DataFrame({"y": Y, "x1": X1, "x2": X2, "x3": X3, "g": G})


# summary(regsubsets(y ~ x1 + x2 + x3, data=d, method="exhaustive", nvmax=3))
R_CASE_A = {
    "rsq": [0.99015534571735009, 0.99809299323071821, 0.99810647112829742],
    "adjr2": [0.98917088028908506, 0.99766921394865560, 0.99739639780140898],
    "cp": [33.5928351757239056, 2.0569429820929308, 4.0],
    "bic": [-50.480106902917903, -67.691924882653367, -65.292130156154712],
    "rss": [5.5409324009324052, 1.0733333333333337, 1.0657474783861676],
}

# summary(regsubsets(y ~ x1 + x2 + x3 + g, data=d, method="exhaustive", nvmax=4))
# 5 design columns (x1, x2, x3, gb, gc) but nvmax=4: sizes 1..4 only, while
# Cp's sigma2 still comes from the FULL 6-coefficient model.
R_CASE_B = {
    "rsq": [0.99015534571735009, 0.99809299323071821, 0.99840237135681842, 0.99849215547202719],
    "adjr2": [0.98917088028908506, 0.99766921394865560, 0.99780326061562530, 0.99763053002747137],
    "cp": [33.1223917856412697, 1.9658134509084206, 2.6735010805469006, 4.2984612410829159],
    "bic": [-50.480106902917903, -67.691924882653367, -67.331191566547034, -65.540356105531771],
}
R_CASE_C_SIZE5 = {"cp": 6.0, "bic": -63.638000098411915}

# glm(cnt ~ x1, family=poisson): BIC() and dispersion ratio from R
CNT = [1, 3, 2, 5, 4, 8, 6, 12, 9, 15, 11, 20]
R_POIS_DISP_RATIO = 0.58770419163155208
R_POIS_BIC = 54.9628294979195

# Overdispersed: MASS::glm.nb(cnt2 ~ x1): theta=1.1008572, logLik=-43.702037
CNT2 = [0, 6, 1, 14, 2, 25, 3, 40, 5, 60, 8, 90]
R_NB_DISP_RATIO = 14.076604642875807
R_NB_BIC = 94.858793704332768


# --------------------------------------------------------------------------- #
# best_subsets: leaps replica
# --------------------------------------------------------------------------- #


def test_best_subsets_continuous_matches_r_leaps():
    s = M.best_subsets(r_frame(), "y", ["x1", "x2", "x3"], nvmax=3)

    assert list(s["which"].columns) == ["(Intercept)", "x1", "x2", "x3"]
    # per-size min-RSS choices as R found them
    assert s["which"].iloc[0].tolist() == [True, True, False, False]
    assert s["which"].iloc[1].tolist() == [True, True, True, False]
    assert s["which"].iloc[2].tolist() == [True, True, True, True]

    np.testing.assert_allclose(s["rsq"], R_CASE_A["rsq"], rtol=1e-9)
    np.testing.assert_allclose(s["adjr2"], R_CASE_A["adjr2"], rtol=1e-9)
    np.testing.assert_allclose(s["cp"], R_CASE_A["cp"], rtol=1e-8)
    np.testing.assert_allclose(s["bic"], R_CASE_A["bic"], rtol=1e-9)
    np.testing.assert_allclose(s["rss"], R_CASE_A["rss"], rtol=1e-9)


def test_best_subsets_factor_nvmax_below_p_matches_r_leaps():
    """Factor expands to 2 dummies (5 columns > nvmax=4); leaps picks single
    dummies, and sigma2 for Cp uses the full-model RSS regardless of nvmax."""
    s = M.best_subsets(r_frame(), "y", ["x1", "x2", "x3", "g"], nvmax=4)

    assert s["xnames"] == ["x1", "x2", "x3", "gb", "gc"]  # R model.matrix names
    assert len(s["bic"]) == 4  # sizes 1..nvmax only

    w = s["which"]
    assert w.iloc[2][["x1", "x2", "gc"]].all() and not w.iloc[2][["x3", "gb"]].any()
    assert w.iloc[3][["x1", "x2", "x3", "gc"]].all() and not w.iloc[3]["gb"]

    np.testing.assert_allclose(s["rsq"], R_CASE_B["rsq"], rtol=1e-9)
    np.testing.assert_allclose(s["adjr2"], R_CASE_B["adjr2"], rtol=1e-9)
    np.testing.assert_allclose(s["cp"], R_CASE_B["cp"], rtol=1e-8)
    np.testing.assert_allclose(s["bic"], R_CASE_B["bic"], rtol=1e-9)


def test_best_subsets_full_size_matches_r_leaps():
    s = M.best_subsets(r_frame(), "y", ["x1", "x2", "x3", "g"], nvmax=5)
    assert len(s["bic"]) == 5
    np.testing.assert_allclose(s["cp"][:4], R_CASE_B["cp"], rtol=1e-8)
    np.testing.assert_allclose(s["cp"][4], R_CASE_C_SIZE5["cp"], atol=1e-8)
    np.testing.assert_allclose(s["bic"][4], R_CASE_C_SIZE5["bic"], rtol=1e-9)


def test_best_subsets_min_rss_per_size_vs_statsmodels_bruteforce():
    """Independent brute force: every subset refit with statsmodels OLS; the
    replica must pick the min-RSS subset per size and report leaps' formulas."""
    rng = np.random.default_rng(0)
    n = 25
    df = pd.DataFrame(rng.normal(size=(n, 4)), columns=["a", "b", "c", "d"])
    df["y"] = 1.5 * df["a"] - 2.0 * df["c"] + rng.normal(scale=0.8, size=n)

    terms = ["a", "b", "c", "d"]
    s = M.best_subsets(df, "y", terms, nvmax=4)

    tss = float(((df["y"] - df["y"].mean()) ** 2).sum())
    full = smf.ols("y ~ a + b + c + d", data=df).fit()
    sigma2 = full.ssr / (n - 5)

    for row_i, k in enumerate(range(1, 5)):
        fits = {
            combo: smf.ols("y ~ " + " + ".join(combo), data=df).fit()
            for combo in combinations(terms, k)
        }
        best_combo = min(fits, key=lambda cmb: fits[cmb].ssr)
        best = fits[best_combo]

        chosen = [t for t in terms if s["which"].iloc[row_i][t]]
        assert tuple(chosen) == best_combo

        i = k + 1
        np.testing.assert_allclose(s["rsq"][row_i], best.rsquared, rtol=1e-10)
        np.testing.assert_allclose(s["adjr2"][row_i], best.rsquared_adj, rtol=1e-10)
        np.testing.assert_allclose(
            s["cp"][row_i], best.ssr / sigma2 - (n - 2 * i), rtol=1e-10
        )
        np.testing.assert_allclose(
            s["bic"][row_i], n * np.log(best.ssr / tss) + i * np.log(n), rtol=1e-10
        )


def test_best_subsets_really_big_guard():
    n = 40
    rng = np.random.default_rng(1)
    df = pd.DataFrame(rng.normal(size=(n, 32)), columns=[f"p{i}" for i in range(32)])
    df["y"] = rng.normal(size=n)
    with pytest.raises(ValueError, match="really.big"):
        M.best_subsets(df, "y", [f"p{i}" for i in range(32)], nvmax=32)


# --------------------------------------------------------------------------- #
# Dummy resolution helpers
# --------------------------------------------------------------------------- #


def test_resolve_dummy_names_maps_factor_dummies_back():
    df = r_frame()
    out = M.resolve_dummy_names(["x1", "gb", "gc", "x2"], df)
    assert out == ["x1", "g", "x2"]  # unique, first occurrence order


def test_map_columns_to_keys_falls_back_to_column_name():
    assert M.map_columns_to_keys(["x1", "g"], {"x1": "k_x1"}) == ["k_x1", "g"]


# --------------------------------------------------------------------------- #
# build_model_candidates: OLS path
# --------------------------------------------------------------------------- #


def _ols_configs(pred_keys_equal_cols=True):
    if pred_keys_equal_cols:
        predictor_config = {k: {"column_name": k} for k in ["x1", "x2", "x3"]}
        allowed = ["x1", "x2", "x3"]
    else:
        predictor_config = {
            "k1": {"column_name": "x1"},
            "k2": {"column_name": "x2"},
            "k3": {"column_name": "x3"},
        }
        allowed = ["k1", "k2", "k3"]
    metric_config = {
        "met": {
            "column_name": "y",
            "display_name": "Metric Y",
            "metric_family": "dimension",
            "allowed_predictors": allowed,
            "min_sample_size": 5,
            "best_subsets_allowed": True,
        }
    }
    strat_config = {"region": {"column_name": "g"}}
    return metric_config, predictor_config, strat_config


def _single_strat_decision(metric="met", strat="region"):
    return pd.DataFrame(
        {"metric": [metric], "decision_type": ["single"], "selected_strat": [strat]}
    )


def test_build_model_candidates_with_factor_strat():
    metric_config, predictor_config, strat_config = _ols_configs()
    out = M.build_model_candidates(
        r_frame(), "met", _single_strat_decision(), metric_config, predictor_config, strat_config
    )
    cands = out["candidates_df"]

    # nvmax = 3 predictors + 1 strat = 4 < 5 design columns -> 4 sizes
    assert len(cands) == 4
    assert list(cands.columns) == [
        "metric", "model_id", "predictors", "n_predictors", "r_squared",
        "adj_r2", "cp", "bic", "n_obs", "delta_bic", "rank",
    ]
    assert (cands["metric"] == "met").all()
    assert (cands["n_obs"] == 12).all()

    # sorted by BIC; model_id keeps the original size index
    assert cands["bic"].is_monotonic_increasing
    assert cands["rank"].tolist() == [1, 2, 3, 4]
    assert cands["delta_bic"].iloc[0] == 0.0

    # strat column enters the formula first and dummy "gc" resolves back to
    # the g COLUMN name (key_by_col only maps predictor columns)
    by_id = cands.set_index("model_id")
    assert by_id.loc[1, "predictors"] == "x1"
    assert by_id.loc[2, "predictors"] == "x1, x2"
    assert by_id.loc[3, "predictors"] == "g, x1, x2"
    assert by_id.loc[3, "n_predictors"] == 3  # one term per factor, not per dummy
    assert by_id.loc[4, "predictors"] == "g, x1, x2, x3"

    # importance covers predictor KEYS only (strat excluded); top models are
    # sizes 2 and 3 (delta BIC < 2) with differing term counts -> true freqs
    imp = out["importance_df"]
    assert set(imp["predictor"]) == {"x1", "x2", "x3"}
    imp_by_pred = imp.set_index("predictor")["importance"]
    assert imp_by_pred["x1"] == 1.0
    assert imp_by_pred["x2"] == 1.0
    assert imp_by_pred["x3"] == 0.0

    # plot data
    plots = out["plots"]
    assert list(plots["bic_vs_npred"].columns) == ["n_predictors", "bic"]
    assert list(plots["adjr2_vs_npred"].columns) == ["n_predictors", "adj_r2"]
    heat = plots["model_heatmap"]
    assert list(heat.columns) == ["model", "predictor", "included"]
    # 2 top models x 5 dummy columns (design-matrix order: strat first)
    assert len(heat) == 10
    assert set(heat["predictor"]) == {"gb", "gc", "x1", "x2", "x3"}
    assert set(heat["model"]) == {"Model 1", "Model 2"}
    assert list(plots["predictor_importance"]["predictor"]) == ["x1", "x2"]


def test_build_model_candidates_predictor_key_mapping():
    metric_config, predictor_config, strat_config = _ols_configs(pred_keys_equal_cols=False)
    out = M.build_model_candidates(
        r_frame(), "met", None, metric_config, predictor_config, strat_config
    )
    cands = out["candidates_df"]
    assert len(cands) == 3
    by_id = cands.set_index("model_id")
    assert by_id.loc[1, "predictors"] == "k1"
    assert by_id.loc[2, "predictors"] == "k1, k2"
    assert by_id.loc[3, "predictors"] == "k1, k2, k3"
    np.testing.assert_allclose(sorted(cands["bic"]), sorted(R_CASE_A["bic"]), rtol=1e-9)
    assert set(out["importance_df"]["predictor"]) <= {"k1", "k2", "k3"}


def test_build_model_candidates_insufficient_cases():
    metric_config, predictor_config, strat_config = _ols_configs()
    metric_config["met"]["min_sample_size"] = 50
    out = M.build_model_candidates(
        r_frame(), "met", None, metric_config, predictor_config, strat_config
    )
    assert out["candidates_df"].empty
    assert out["importance_df"].empty
    assert out["plots"] == {}


def test_build_model_candidates_no_predictors():
    metric_config, predictor_config, strat_config = _ols_configs()
    metric_config["met"]["allowed_predictors"] = []
    out = M.build_model_candidates(
        r_frame(), "met", None, metric_config, predictor_config, strat_config
    )
    assert out["candidates_df"].empty and out["importance_df"].empty


def test_importance_collapse_wart_matches_r_apply():
    """R's apply() simplification: equal-length top-model term sets collapse
    importance into pooled 0/1 membership (verified in R); differing lengths
    give true per-model frequencies."""
    equal_len = [["x1", "x3"], ["x1", "x2"]]
    assert M._importance_values(["x1", "x2", "x3"], equal_len) == [1.0, 1.0, 1.0]

    differing = [["x1"], ["x1", "x2"]]
    assert M._importance_values(["x1", "x2", "x3"], differing) == [1.0, 0.5, 0.0]


# --------------------------------------------------------------------------- #
# Count-model path (Poisson / NB)
# --------------------------------------------------------------------------- #


def _count_configs(allowed):
    metric_config = {
        "taxa": {
            "column_name": "cnt",
            "display_name": "Taxa count",
            "metric_family": "count",
            "allowed_predictors": allowed,
            "min_sample_size": 5,
            "count_model": True,
        }
    }
    predictor_config = {k: {"column_name": k} for k in allowed}
    return metric_config, predictor_config


def test_count_model_poisson_bic_matches_r():
    df = pd.DataFrame({"cnt": CNT, "x1": X1})
    metric_config, predictor_config = _count_configs(["x1"])
    out = M.build_model_candidates(df, "taxa", None, metric_config, predictor_config, {})
    cands = out["candidates_df"]

    assert len(cands) == 1
    row = cands.iloc[0]
    assert row["predictors"] == "x1"
    assert row["model_id"] == 1 and row["rank"] == 1  # count path renumbers by BIC
    assert np.isnan(row["r_squared"]) and np.isnan(row["adj_r2"]) and np.isnan(row["cp"])
    # R: BIC(glm(cnt ~ x1, family=poisson)) — dispersion 0.588 < 1.5 keeps Poisson
    np.testing.assert_allclose(row["bic"], R_POIS_BIC, atol=1e-6)
    assert row["n_obs"] == 12

    imp = out["importance_df"]
    assert imp.iloc[0]["predictor"] == "x1" and imp.iloc[0]["importance"] == 1.0
    assert out["plots"] == {}


def test_count_model_switches_to_negbin_when_overdispersed():
    df = pd.DataFrame({"cnt": CNT2, "x1": X1})
    metric_config, predictor_config = _count_configs(["x1"])
    out = M.build_model_candidates(df, "taxa", None, metric_config, predictor_config, {})
    cands = out["candidates_df"]
    assert len(cands) == 1
    # R glm.nb BIC = 94.8588 (theta 1.10); statsmodels joint NB2 MLE lands close
    np.testing.assert_allclose(cands.iloc[0]["bic"], R_NB_BIC, atol=0.5)
    # far away from what the (wrong) Poisson BIC would be (~460)
    assert cands.iloc[0]["bic"] < 150


def test_count_model_combination_truncation_and_combn_order():
    """Sizes with >50 combinations keep the FIRST 50 in R combn order
    (== itertools.combinations order)."""
    rng = np.random.default_rng(7)
    n = 40
    pred_names = [f"p{i}" for i in range(1, 9)]
    df = pd.DataFrame(rng.normal(size=(n, 8)), columns=pred_names)
    df["cnt"] = [5, 6, 7] * 13 + [5]  # deterministic, strongly underdispersed

    metric_config, predictor_config = _count_configs(pred_names)
    metric_config["taxa"]["column_name"] = "cnt"
    out = M.build_model_candidates(df, "taxa", None, metric_config, predictor_config, {})
    cands = out["candidates_df"]

    counts = cands["n_predictors"].value_counts().to_dict()
    assert counts == {1: 8, 2: 28, 3: 50, 4: 50, 5: 50, 6: 28, 7: 8, 8: 1}

    expected_size3 = {
        ", ".join(c) for c in list(combinations(pred_names, 3))[:50]
    }
    got_size3 = set(cands.loc[cands["n_predictors"] == 3, "predictors"])
    assert got_size3 == expected_size3


def test_count_importance_substring_wart():
    """NOTE(parity) pin: importance uses substring matching (grepl fixed=TRUE),
    so a predictor named like a prefix of another counts spuriously."""
    df = pd.DataFrame(
        {
            "cnt": CNT,
            "x1": X1,
            "x10": X2,
        }
    )
    metric_config, predictor_config = _count_configs(["x1", "x10"])
    out = M.build_model_candidates(df, "taxa", None, metric_config, predictor_config, {})
    imp = out["importance_df"].set_index("predictor")["importance"]
    top = out["candidates_df"][out["candidates_df"]["delta_bic"] < 2]
    # every top model containing "x10" also substring-matches "x1"
    n_top = len(top)
    n_with_x10 = int(top["predictors"].str.contains("x10", regex=False).sum())
    n_x1_direct = int(top["predictors"].str.contains("x1", regex=False).sum())
    assert imp["x1"] == n_x1_direct / n_top
    assert n_x1_direct >= n_with_x10  # substring wart: x10 rows count for x1 too


# --------------------------------------------------------------------------- #
# run_all_model_building
# --------------------------------------------------------------------------- #


def test_run_all_model_building_combines_metrics():
    df = r_frame()
    df["cnt"] = CNT
    metric_config = {
        "met": {
            "column_name": "y",
            "metric_family": "dimension",
            "allowed_predictors": ["x1", "x2", "x3"],
            "min_sample_size": 5,
            "best_subsets_allowed": True,
        },
        "taxa": {
            "column_name": "cnt",
            "metric_family": "count",
            "allowed_predictors": ["x1"],
            "min_sample_size": 5,
            "count_model": True,
        },
        "cat_metric": {
            "column_name": "g",
            "metric_family": "categorical",
            "allowed_predictors": ["x1"],
            "min_sample_size": 5,
            "best_subsets_allowed": True,
        },
        "not_eligible": {
            "column_name": "y",
            "metric_family": "dimension",
            "allowed_predictors": ["x1"],
            "min_sample_size": 5,
            "best_subsets_allowed": False,
        },
    }
    predictor_config = {k: {"column_name": k} for k in ["x1", "x2", "x3"]}
    strat_config = {"region": {"column_name": "g"}}
    strat_decisions = pd.DataFrame(
        {
            "metric": ["met", "taxa"],
            "decision_type": ["single", "none"],
            "selected_strat": ["region", None],
        }
    )

    out = M.run_all_model_building(
        df, strat_decisions, metric_config, predictor_config, strat_config
    )
    assert set(out["all_candidates"]["metric"]) == {"met", "taxa"}
    assert set(out["all_importance"]["metric"]) == {"met", "taxa"}
    assert "met_bic_vs_npred" in out["all_plots"]
    assert "met_model_heatmap" in out["all_plots"]
    assert not any(k.startswith("cat_metric") for k in out["all_plots"])


# --------------------------------------------------------------------------- #
# select_final_models (08)
# --------------------------------------------------------------------------- #


def _cand_row(metric, model_id, predictors, n_pred, bic, delta, rank, adj_r2=0.5):
    return {
        "metric": metric,
        "model_id": model_id,
        "predictors": predictors,
        "n_predictors": n_pred,
        "r_squared": adj_r2 + 0.05 if not pd.isna(adj_r2) else np.nan,
        "adj_r2": adj_r2,
        "cp": np.nan,
        "bic": bic,
        "n_obs": 39,
        "delta_bic": delta,
        "rank": rank,
    }


def test_select_final_models_parsimonious_and_flags():
    cands = pd.DataFrame(
        [
            # m1: 5 top models (>3), most parsimonious has 1 predictor
            _cand_row("m1", 1, "a", 1, 100.0, 0.0, 1),
            _cand_row("m1", 2, "a, b", 2, 100.3, 0.3, 2),
            _cand_row("m1", 3, "a, c", 2, 100.4, 0.4, 3),
            _cand_row("m1", 4, "a, b, c", 3, 101.0, 1.0, 4),
            _cand_row("m1", 5, "a, b, d", 3, 101.5, 1.5, 5),
            _cand_row("m1", 6, "a, b, c, d", 4, 103.0, 3.0, 6),
            # m2: complex + low adj R2 + tied
            _cand_row("m2", 1, "a, b, c, d, e, f", 6, 50.0, 0.0, 1, adj_r2=0.10),
            _cand_row("m2", 2, "a, b, c, d, e, g", 6, 50.2, 0.2, 2, adj_r2=0.11),
            # m3: clean selection (2 predictors, single top model)
            _cand_row("m3", 1, "a, b", 2, 10.0, 0.0, 1, adj_r2=0.6),
            _cand_row("m3", 2, "a, b, c", 3, 12.5, 2.5, 2, adj_r2=0.61),
            # m4: count-style (adj_r2 NA) — low_r_squared must NOT fire
            _cand_row("m4", 1, "a, b", 2, 20.0, 0.0, 1, adj_r2=np.nan),
        ]
    )

    sel = M.select_final_models(cands, pd.DataFrame(), {})

    assert list(sel.columns) == [
        "metric", "selected_rank", "selected_model_id", "predictors", "n_predictors",
        "r_squared", "adj_r2", "bic", "delta_bic", "n_obs", "n_top_models",
        "selection_method", "needs_review", "review_reason",
    ]
    assert sel["metric"].tolist() == ["m1", "m2", "m3", "m4"]
    assert (sel["selection_method"] == "min_bic_parsimonious").all()

    m1 = sel.set_index("metric").loc["m1"]
    assert m1["predictors"] == "a"
    assert m1["n_top_models"] == 5
    assert bool(m1["needs_review"])
    assert m1["review_reason"] == "many_top_models; too_simple"

    m2 = sel.set_index("metric").loc["m2"]
    assert m2["predictors"] == "a, b, c, d, e, f"  # tie on n_pred -> lower BIC
    assert m2["review_reason"] == "too_complex; low_r_squared; tied_top_models"

    m3 = sel.set_index("metric").loc["m3"]
    assert m3["predictors"] == "a, b"
    assert not bool(m3["needs_review"])
    assert m3["review_reason"] is None or pd.isna(m3["review_reason"])
    assert m3["selected_rank"] == 1 and m3["n_top_models"] == 1

    m4 = sel.set_index("metric").loc["m4"]
    assert "low_r_squared" not in str(m4["review_reason"])


def test_select_final_models_empty_input():
    assert M.select_final_models(pd.DataFrame(), pd.DataFrame(), {}).empty


def test_select_final_models_prefers_fewer_predictors_over_lower_bic():
    cands = pd.DataFrame(
        [
            _cand_row("m", 1, "a, b", 2, 100.0, 0.0, 1),
            _cand_row("m", 2, "c", 1, 101.5, 1.5, 2),
        ]
    )
    sel = M.select_final_models(cands, pd.DataFrame(), {})
    assert sel.iloc[0]["predictors"] == "c"  # parsimony beats BIC within the pool
    assert sel.iloc[0]["selected_rank"] == 2


# --------------------------------------------------------------------------- #
# Golden-fixture parity (skips until scripts/export_golden.R has run)
# --------------------------------------------------------------------------- #

_CONFIG_LIST_FIELDS = {
    "allowed_predictors", "allowed_stratifications", "pairwise_comparisons",
    "levels", "group_definitions",
}

_GOLDEN_MODEL_FIXTURES = (
    "01_bundle_meta", "02_derived", "06_decisions", "07_candidates", "08_selection",
)


def _unbox_config(cfg: dict) -> dict:
    """Undo jsonlite auto_unbox=FALSE: scalars arrive as length-1 lists."""
    out = {}
    for key, entry in (cfg or {}).items():
        e = {}
        for f, v in (entry or {}).items():
            if isinstance(v, list) and f not in _CONFIG_LIST_FIELDS and len(v) == 1:
                e[f] = v[0]
            else:
                e[f] = v
        out[key] = e
    return out


def test_golden_candidates_and_selection_parity():
    if not all(has_golden(n) for n in _GOLDEN_MODEL_FIXTURES):
        pytest.skip("model golden fixtures not present (run scripts/export_golden.R)")
    meta = load_golden_json("01_bundle_meta")
    derived = load_golden_df("02_derived")
    decisions = load_golden_df("06_decisions")
    expected_cands = load_golden_df("07_candidates")
    expected_sel = load_golden_df("08_selection")

    mc = _unbox_config(meta["metric_config"])
    pc = _unbox_config(meta["predictor_config"])
    sc = _unbox_config(meta["strat_config"])

    out = M.run_all_model_building(derived, decisions, mc, pc, sc)
    got_cands = out["all_candidates"]

    assert set(got_cands["metric"]) == set(expected_cands["metric"])

    def _rss_of(metric: str, predictor_keys: list[str]) -> float:
        """RSS of the OLS on the metric's complete-case data for these
        predictors — used to verify span-equivalence of tied subsets."""
        col = mc[metric]["column_name"]
        pred_cols = [pc[k]["column_name"] if k in pc else k for k in predictor_keys]
        sub = derived[[col] + pred_cols].dropna()
        y = sub[col].to_numpy(float)
        X = np.column_stack([np.ones(len(sub))] + [sub[c].to_numpy(float) for c in pred_cols])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        r = y - X @ beta
        return float(r @ r)

    for metric in sorted(set(expected_cands["metric"])):
        exp = expected_cands[expected_cands["metric"] == metric].sort_values("rank")
        got = got_cands[got_cands["metric"] == metric].sort_values("rank")
        assert len(got) == len(exp), f"{metric}: candidate count mismatch"
        ols = exp["r_squared"].notna().any()
        if ols:
            np.testing.assert_allclose(
                got["bic"].to_numpy(float), exp["bic"].to_numpy(float),
                rtol=1e-6, err_msg=f"{metric}: bic mismatch",
            )
            # Predictor strings must match, EXCEPT when exactly-collinear
            # derived columns (bufferwidth = L + R) create equal-span subsets:
            # leaps' Fortran branch-and-bound emits an arbitrary representative
            # among ties (R itself flip-flops between sizes for avgBasalarea).
            # Accept a differing string only when both subsets have identical
            # RSS on the same data (span equivalence).
            for (_, grow), (_, erow) in zip(got.iterrows(), exp.iterrows()):
                gp, ep = grow["predictors"], erow["predictors"]
                if gp == ep:
                    continue
                g_rss = _rss_of(metric, [s.strip() for s in gp.split(",")])
                e_rss = _rss_of(metric, [s.strip() for s in ep.split(",")])
                assert abs(g_rss - e_rss) <= 1e-7 * max(1.0, abs(e_rss)), (
                    f"{metric}: predictors differ without RSS tie: "
                    f"py={gp!r} (rss={g_rss}) vs R={ep!r} (rss={e_rss})"
                )
        else:  # count metrics: NB fits differ slightly between MASS and statsmodels
            np.testing.assert_allclose(
                got["bic"].to_numpy(float), exp["bic"].to_numpy(float),
                atol=1.0, err_msg=f"{metric}: count bic mismatch",
            )
            assert got.iloc[0]["predictors"] == exp.iloc[0]["predictors"], metric

    got_sel = M.select_final_models(got_cands, out["all_importance"], mc)
    merged = got_sel.merge(expected_sel, on="metric", suffixes=("_py", "_r"))
    assert (merged["predictors_py"] == merged["predictors_r"]).all()
    assert (merged["needs_review_py"].astype(bool) == merged["needs_review_r"].astype(bool)).all()
