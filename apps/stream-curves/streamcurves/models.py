"""Port of R/07_model_candidates.R + R/08_model_selection.R.

Best-subsets regression (OLS) or GLM (Poisson / negative binomial) candidates
for each metric, plus BIC-based final-model selection.

``best_subsets`` is a from-scratch replica of ``leaps::regsubsets(method =
"exhaustive", nbest = 1)``: it builds an R-style treatment-coded design matrix
(factor dummies drop the first level; default level order = sorted unique),
enumerates column subsets per size, and reports the exact leaps summary
statistics (verified against leaps 3.x source and outputs):

    i      = number of coefficients INCLUDING the intercept (= k + 1)
    rsq    = 1 - RSS/TSS
    adjr2  = 1 - (RSS/TSS) * (n - 1) / (n - i)
    cp     = RSS/sigma2 - (n - 2i),  sigma2 = RSS_full / (n - i_max)
             where RSS_full/i_max come from the model with ALL design columns
             (even when nvmax < #columns — confirmed against leaps)
    bic    = n*log(RSS/TSS) + i*log(n)   (relative to the null model, so
             values are negative like R's)

Count metrics go through Poisson GLM, switching to negative binomial when the
Pearson dispersion ratio exceeds 1.5 (R: ``MASS::glm.nb``; here: statsmodels
``NegativeBinomial`` NB2 MLE). GLM BIC is always ``-2*llf + k*log(n)`` — never
statsmodels' deviance-based ``.bic``.

Column-name constraint: count-model formulas are built with patsy; names that
are not valid Python identifiers are wrapped in ``Q("...")`` automatically.
Names containing double quotes are not supported.
"""

from __future__ import annotations

import keyword
import logging
from itertools import combinations

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

logger = logging.getLogger("streamcurves")

__all__ = [
    "resolve_dummy_names",
    "resolve_predictor_lookup",
    "map_columns_to_keys",
    "selected_terms_from_row",
    "best_subsets",
    "build_model_candidates",
    "build_count_model_candidates",
    "run_all_model_building",
    "select_final_models",
]


def _or(x, default):
    """R ``%||%``."""
    return x if x is not None else default


def _ordered_unique(seq) -> list:
    """R ``unique()`` — first occurrence wins, order preserved."""
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _is_factor(s: pd.Series) -> bool:
    """Whether a column behaves like an R factor in the model matrix.

    NOTE(parity): R distinguishes factor from character columns
    (``resolve_dummy_names`` only resolves true factors), but by the time data
    reaches the modeling step the R pipeline has factor-coded every
    categorical column. pandas has no such distinction, so the port treats
    every non-numeric column (Categorical, string/object, bool) as a factor —
    both for dummy expansion and for dummy-name resolution.
    """
    if isinstance(s.dtype, pd.CategoricalDtype):
        return True
    if pd.api.types.is_bool_dtype(s):
        return True
    return not pd.api.types.is_numeric_dtype(s)


def _factor_levels(s: pd.Series) -> list:
    """R factor levels: declared order for Categorical, else sorted unique."""
    if isinstance(s.dtype, pd.CategoricalDtype):
        return list(s.cat.categories)
    if pd.api.types.is_bool_dtype(s):
        # R logical -> factor(c("FALSE","TRUE")); dummy is named "<col>True" here.
        return [False, True]
    return sorted(pd.unique(s.dropna()).tolist(), key=str)


def _r_design_matrix(model_data: pd.DataFrame, terms: list[str]) -> tuple[np.ndarray, list[str]]:
    """R ``model.matrix`` minus the intercept column.

    Treatment (drop-first) coding, dummy names ``paste0(column, level)``
    exactly like R (e.g. column "g" level "b" -> "gb").
    """
    cols: list[np.ndarray] = []
    names: list[str] = []
    for term in terms:
        s = model_data[term]
        if _is_factor(s):
            levels = _factor_levels(s)
            for lv in levels[1:]:
                cols.append((s == lv).to_numpy(dtype=float))
                names.append(f"{term}{lv}")
        else:
            cols.append(s.to_numpy(dtype=float))
            names.append(str(term))
    X = np.column_stack(cols) if cols else np.empty((len(model_data), 0))
    return X, names


def resolve_dummy_names(selected_names, data: pd.DataFrame) -> list[str]:
    """Resolve regsubsets dummy variable names back to original column names.

    The design matrix expands factor variables into dummy indicators
    (e.g. "Ecoregion" -> "EcoregionRegion_B"). This maps those dummy names
    back to the original factor column names (first factor column whose name
    prefixes the dummy name, like R's ``startsWith`` loop).
    """
    data_cols = list(data.columns)
    resolved = []
    for nm in selected_names:
        if nm in data_cols:
            resolved.append(nm)
            continue
        hit = nm
        for col in data_cols:
            if _is_factor(data[col]) and str(nm).startswith(str(col)):
                hit = col
                break
        resolved.append(hit)
    return _ordered_unique(resolved)


def resolve_predictor_lookup(allowed_preds, predictor_config, data: pd.DataFrame) -> pd.DataFrame:
    """Tibble of (predictor_key, column_name) filtered to columns present in data."""
    keys = list(allowed_preds)
    col_names = []
    for k in keys:
        cfg = (predictor_config or {}).get(k) or {}
        col_names.append(_or(cfg.get("column_name"), k))  # R: $column_name %||% key
    lookup = pd.DataFrame({"predictor_key": keys, "column_name": col_names})
    return lookup[lookup["column_name"].isin(data.columns)].reset_index(drop=True)


def map_columns_to_keys(column_names, key_by_col: dict) -> list[str]:
    """Map data column names back to predictor keys (unmapped names pass through)."""
    return _ordered_unique(key_by_col.get(c, c) for c in column_names)


def selected_terms_from_row(row: pd.Series, model_data: pd.DataFrame, key_by_col: dict) -> list[str]:
    """Predictor keys for one ``which`` row (bool Series over dummy names)."""
    selected_names = list(row.index[row.astype(bool)])
    resolved_columns = resolve_dummy_names(selected_names, model_data)
    return map_columns_to_keys(resolved_columns, key_by_col)


# --------------------------------------------------------------------------- #
# leaps::regsubsets replica
# --------------------------------------------------------------------------- #


def best_subsets(
    model_data: pd.DataFrame,
    response: str,
    terms: list[str],
    nvmax: int,
    really_big: bool = False,
) -> dict:
    """Exhaustive best-subsets replica of ``leaps::regsubsets`` (nbest = 1).

    Enumerates subsets of design-matrix COLUMNS (factor dummies are selectable
    individually, exactly like leaps) for sizes 1..min(nvmax, p), keeping the
    minimum-RSS subset per size.

    Returns a dict with:
      - ``which``: bool DataFrame, one row per size (index = size), columns
        ``["(Intercept)"] + dummy names`` (like ``summary(...)$which``)
      - ``rsq``, ``adjr2``, ``cp``, ``bic``, ``rss``: float arrays per row
      - ``xnames``: design-matrix column names (no intercept), ``n``: rows.

    Linear dependencies are handled the way leaps does (verified against R):
    columns flagged dependent by the in-order QR ("<k> linear dependencies
    found") are moved to the END of the search order — they stay searchable
    (a dependent column can win a size outright, e.g. x3 = x1 + x2 alone at
    size 1), but exact-RSS ties resolve to the earlier, independent columns —
    and the maximum reported size is capped at the design rank ("nvmax
    reduced to ..."). Cp's sigma2 keeps ALL columns (dependent ones included)
    in its degrees of freedom, exactly like leaps.
    """
    y = model_data[response].to_numpy(dtype=float)
    X, xnames = _r_design_matrix(model_data, terms)
    n, p = X.shape
    if p == 0:
        raise ValueError("no predictor columns in design matrix")
    if p > 31 and not really_big:
        # leaps guards huge exhaustive searches behind really.big=T.
        raise ValueError("Exhaustive search will be S L O W, must specify really.big=T")

    ones = np.ones((n, 1))

    def _rss(idx: tuple) -> float:
        Xs = np.concatenate([ones, X[:, list(idx)]], axis=1) if idx else ones
        beta, *_ = np.linalg.lstsq(Xs, y, rcond=None)
        r = y - Xs @ beta
        return float(r @ r)

    tss = _rss(())  # null (intercept-only) RSS
    rss_full = _rss(tuple(range(p)))
    i_max = p + 1  # leaps: 'last' includes ALL columns, even dependent ones
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma2 = float(np.divide(rss_full, float(n - i_max)))

    # -- Linear-dependency detection (leaps' sing/tolset: in-order QR) --------
    # A column is dependent when its residual against the intercept and all
    # PRECEDING independent columns is negligible (greedy left-to-right, like
    # the sequential Householder/Gentleman factorization leaps uses).
    q_basis = ones / np.sqrt(float(n))
    lindep = np.zeros(p, dtype=bool)
    for j in range(p):
        v = X[:, j]
        nrm0 = float(np.linalg.norm(v))
        r = v - q_basis @ (q_basis.T @ v)
        r = r - q_basis @ (q_basis.T @ r)  # re-orthogonalize for stability
        if nrm0 == 0.0 or float(np.linalg.norm(r)) <= 1e-7 * nrm0:
            lindep[j] = True
        else:
            q_basis = np.column_stack([q_basis, r / np.linalg.norm(r)])

    search_order = list(range(p))
    nvmax_eff = min(int(nvmax), p)
    if lindep.any():
        logger.warning("%d  linear dependencies found", int(lindep.sum()))
        # Reorder: independent columns first, dependent last (leaps recursion)
        search_order = [j for j in range(p) if not lindep[j]] + [
            j for j in range(p) if lindep[j]
        ]
        lastsafe = int((~lindep).sum()) + 1  # position of last independent col, incl. intercept
        if lastsafe < nvmax_eff + 1:  # R: lastsafe < min(nvmax, last)
            nvmax_eff = lastsafe - 1
            logger.warning("nvmax reduced to  %d", nvmax_eff)

    sizes, idx_list, rss_list = [], [], []
    for k in range(1, nvmax_eff + 1):
        best_idx, best_rss = None, np.inf
        for combo_pos in combinations(range(p), k):
            combo = tuple(search_order[t] for t in combo_pos)
            r = _rss(combo)
            # Strictly-better-beyond-rounding wins: leaps evaluates every
            # subset from one shared QR, so exact ties stay exact and the
            # first subset in search order is kept; recomputing each subset
            # independently needs a tolerance to preserve that tie-breaking
            # (matters when dependent columns create equal-span subsets).
            if r < best_rss * (1.0 - 1e-10):
                best_rss, best_idx = r, combo
        sizes.append(k)
        idx_list.append(best_idx)
        rss_list.append(best_rss)

    rss_arr = np.asarray(rss_list, dtype=float)
    i_arr = np.asarray(sizes, dtype=float) + 1.0
    with np.errstate(divide="ignore", invalid="ignore"):
        vr = rss_arr / tss
        rsq = 1.0 - vr
        adjr2 = 1.0 - vr * (n - 1.0) / (n - i_arr)
        cp = rss_arr / sigma2 - (n - 2.0 * i_arr)
        bic = n * np.log(vr) + i_arr * np.log(n)

    which = pd.DataFrame(False, index=pd.Index(sizes, name="size"),
                         columns=["(Intercept)"] + xnames)
    which["(Intercept)"] = True
    for row_i, idx in enumerate(idx_list):
        for j in idx:
            which.iloc[row_i, j + 1] = True

    return {
        "which": which,
        "rsq": rsq,
        "adjr2": adjr2,
        "cp": cp,
        "bic": bic,
        "rss": rss_arr,
        "xnames": xnames,
        "n": n,
    }


# --------------------------------------------------------------------------- #
# Model candidates per metric
# --------------------------------------------------------------------------- #


def _empty_result() -> dict:
    return {"candidates_df": pd.DataFrame(), "importance_df": pd.DataFrame(), "plots": {}}


def _decision_row(strat_decision):
    """Normalize a one-row tibble / Series / dict to a mapping (or None)."""
    if strat_decision is None:
        return None
    if isinstance(strat_decision, pd.DataFrame):
        if len(strat_decision) == 0:
            return None
        return strat_decision.iloc[0]
    return strat_decision


def _importance_values(all_predictor_keys: list[str], selected_top_terms: list[list[str]]) -> list[float]:
    """Inclusion frequency of each key among the top models' term lists.

    NOTE(parity): R computes the term lists with ``apply(..., 1, ...)``, which
    simplifies to a matrix/vector whenever every top model resolves to the
    SAME number of terms; the code then wraps that in ``list(...)`` producing a
    single pooled element, so importance collapses to 1/0 = "appears in ANY
    top model" instead of a true frequency (verified in R). Only when term
    counts differ does R return per-model frequencies. Ported verbatim.
    """
    if len({len(t) for t in selected_top_terms}) == 1 and len(selected_top_terms) > 0:
        pooled = [t for terms in selected_top_terms for t in terms]
        selected_top_terms = [pooled]
    return [
        float(np.mean([key in sel for sel in selected_top_terms]))
        for key in all_predictor_keys
    ]


def build_model_candidates(
    data: pd.DataFrame,
    metric_key: str,
    strat_decision,
    metric_config: dict,
    predictor_config: dict,
    strat_config: dict,
) -> dict:
    """Build model candidates for a single metric.

    Returns ``{"candidates_df": DataFrame, "importance_df": DataFrame,
    "plots": dict}``. ``plots`` holds plain plot DATA (the views layer
    renders): ``bic_vs_npred`` / ``adjr2_vs_npred`` (per-candidate columns),
    ``model_heatmap`` (long model x dummy-predictor inclusion table for
    ΔBIC < 2 models) and ``predictor_importance`` (importance > 0 rows) —
    mirroring the ggplot objects R builds from the same data.
    """
    mc = metric_config[metric_key]
    col_name = mc["column_name"]

    # -- Determine predictors -------------------------------------------------
    allowed_preds = mc.get("allowed_predictors")
    if isinstance(allowed_preds, str):
        allowed_preds = [allowed_preds]
    if allowed_preds is None or len(allowed_preds) == 0:
        logger.warning("No allowed predictors for %s", metric_key)
        return _empty_result()

    pred_lookup = resolve_predictor_lookup(allowed_preds, predictor_config, data)
    available_pred_keys = pred_lookup["predictor_key"].tolist()
    available_pred_cols = pred_lookup["column_name"].tolist()
    key_by_col = dict(zip(pred_lookup["column_name"], pred_lookup["predictor_key"]))

    if len(available_pred_cols) == 0:
        logger.warning("No available predictors found in the data for %s", metric_key)
        return _empty_result()

    # -- Handle stratification mode -------------------------------------------
    strat_var = None
    strat_mode = _or(mc.get("stratification_mode"), "covariate")

    row = _decision_row(strat_decision)
    if row is not None and row["decision_type"] == "single":
        strat_key = row["selected_strat"]
        strat_cfg = (strat_config or {}).get(strat_key) or {}
        strat_var = _or(strat_cfg.get("column_name"), strat_key)
        if strat_var not in data.columns:
            logger.warning("Stratification variable %s not in data", strat_var)
            strat_var = None

    # -- Prepare model data ----------------------------------------------------
    model_cols = [col_name] + list(available_pred_cols)
    if strat_var is not None and strat_mode == "covariate":
        model_cols.append(strat_var)
    model_cols = [c for c in _ordered_unique(model_cols) if c in data.columns]

    model_data = data.loc[:, model_cols].dropna().reset_index(drop=True)

    n_complete = len(model_data)
    logger.info(
        "%s: %d complete cases for modeling (of %d total)", metric_key, n_complete, len(data)
    )

    if n_complete < mc["min_sample_size"]:
        logger.warning("%s: insufficient complete cases (%d)", metric_key, n_complete)
        return _empty_result()

    # -- Count model pathway -----------------------------------------------------
    if mc.get("count_model") is True:
        return build_count_model_candidates(
            model_data, metric_key, col_name, available_pred_cols, strat_var, mc, key_by_col
        )

    # -- Best subsets regression (OLS) -------------------------------------------
    terms = list(available_pred_cols)
    if strat_var is not None and strat_mode == "covariate":
        terms = [strat_var] + terms  # R: paste(strat_var, "+", pred_formula)

    # NOTE(parity): R's nvmax counts the strat variable whenever a single
    # stratification was decided — even when strat_mode != "covariate" and the
    # variable is NOT in the formula. Ported verbatim (leaps caps at #columns).
    nvmax = len(available_pred_cols) + (1 if strat_var is not None else 0)

    try:
        s = best_subsets(model_data, col_name, terms, nvmax)
    except Exception as e:  # R: tryCatch around regsubsets
        logger.warning("Best subsets failed for %s: %s", metric_key, e)
        return _empty_result()

    which_no_int = s["which"].iloc[:, 1:]  # drop the intercept column
    term_lists = [
        selected_terms_from_row(which_no_int.iloc[i], model_data, key_by_col)
        for i in range(len(which_no_int))
    ]

    candidates_df = pd.DataFrame(
        {
            "metric": metric_key,
            "model_id": np.arange(1, len(s["bic"]) + 1),
            "predictors": [", ".join(t) for t in term_lists],
            "n_predictors": [len(t) for t in term_lists],
            "r_squared": s["rsq"],
            "adj_r2": s["adjr2"],
            "cp": s["cp"],
            "bic": s["bic"],
            "n_obs": n_complete,
        }
    )

    # Sort by BIC and compute delta BIC
    order = np.argsort(candidates_df["bic"].to_numpy(), kind="stable")
    candidates_df = candidates_df.iloc[order].reset_index(drop=True)
    candidates_df["delta_bic"] = candidates_df["bic"] - candidates_df["bic"].min()
    candidates_df["rank"] = np.arange(1, len(candidates_df) + 1)

    # -- Predictor importance -----------------------------------------------------
    top_mask = (candidates_df["delta_bic"] < 2).to_numpy()
    all_predictor_keys = list(available_pred_keys)
    if top_mask.sum() > 0:
        sorted_terms = [term_lists[i] for i in order]  # R: s$which reordered by order(bic)
        selected_top_terms = [t for t, m in zip(sorted_terms, top_mask) if m]
        importance_df = pd.DataFrame(
            {
                "metric": metric_key,
                "predictor": all_predictor_keys,
                "importance": _importance_values(all_predictor_keys, selected_top_terms),
            }
        )
        importance_df = importance_df.sort_values(
            "importance", ascending=False, kind="stable", ignore_index=True
        )
    else:
        importance_df = pd.DataFrame(
            {"metric": metric_key, "predictor": all_predictor_keys, "importance": 0.0}
        )

    # -- Plot data (R returns ggplot objects; the port returns their data) --------
    plots: dict = {}
    plots["bic_vs_npred"] = candidates_df[["n_predictors", "bic"]].copy()
    plots["adjr2_vs_npred"] = candidates_df[["n_predictors", "adj_r2"]].copy()

    if top_mask.sum() > 0:
        sorted_which = which_no_int.iloc[order].reset_index(drop=True)
        top_which = sorted_which.loc[top_mask]
        records = []
        for m_i in range(len(top_which)):
            for pred in top_which.columns:
                records.append(
                    {
                        "model": f"Model {m_i + 1}",
                        "predictor": pred,
                        "included": bool(top_which.iloc[m_i][pred]),
                    }
                )
        plots["model_heatmap"] = pd.DataFrame(records, columns=["model", "predictor", "included"])

    imp_plot_data = importance_df[importance_df["importance"] > 0]
    if len(imp_plot_data) > 0:
        plots["predictor_importance"] = imp_plot_data.reset_index(drop=True)

    return {"candidates_df": candidates_df, "importance_df": importance_df, "plots": plots}


# --------------------------------------------------------------------------- #
# Count models (Poisson / Negative Binomial)
# --------------------------------------------------------------------------- #


def _patsy_term(name: str) -> str:
    """Quote a column name for patsy unless it is already a safe identifier."""
    if name.isidentifier() and not keyword.iskeyword(name):
        return name
    return f'Q("{name}")'


def _glm_bic(fit, n: int) -> float:
    """R stats::BIC for GLMs: -2*llf + k*log(n).

    ``k = len(params)`` — for the discrete NB fit this includes alpha, matching
    R where ``logLik.negbin`` df = rank + 1 (theta counted).
    Never statsmodels' deviance-based ``.bic``.
    """
    return float(-2.0 * fit.llf + len(fit.params) * np.log(n))


def build_count_model_candidates(
    model_data: pd.DataFrame,
    metric_key: str,
    col_name: str,
    available_preds: list[str],
    strat_var,
    mc: dict,
    key_by_col: dict,
) -> dict:
    """Build count model candidates (Poisson / Negative Binomial).

    Enumerates predictor combinations of sizes 1..p in R ``combn`` order
    (== ``itertools.combinations``), truncating each size at 50 combos.
    """
    logger.info("%s: building count model candidates (Poisson/NB)", metric_key)

    n = len(model_data)
    entries: list[dict] = []

    # NOTE(parity): R stores fits at model_list[[model_id]], leaving NULL holes
    # when a fit fails; a failure followed by a later success would actually
    # crash R's map_dfr (BIC(NULL) errors). The port simply skips failed fits,
    # which matches R output in every case where R doesn't crash.
    for k in range(1, len(available_preds) + 1):
        combos = list(combinations(available_preds, k))
        if len(combos) > 50:  # R: combos[1:50]
            combos = combos[:50]

        for combo in combos:
            pred_terms = list(combo)
            formula_terms = ([strat_var] if strat_var is not None else []) + pred_terms
            formula = (
                f"{_patsy_term(col_name)} ~ "
                + " + ".join(_patsy_term(t) for t in formula_terms)
            )

            try:
                pois_fit = smf.glm(
                    formula, data=model_data, family=sm.families.Poisson()
                ).fit()
            except Exception:
                continue

            # Check overdispersion
            with np.errstate(divide="ignore", invalid="ignore"):
                disp_ratio = float(
                    np.divide(np.sum(np.asarray(pois_fit.resid_pearson) ** 2),
                              float(pois_fit.df_resid))
                )
            use_nb = disp_ratio > 1.5

            if use_nb:
                try:
                    fit = smf.negativebinomial(formula, data=model_data).fit(
                        disp=0, maxiter=500
                    )
                    family = "negbin"
                except Exception:
                    continue
            else:
                fit, family = pois_fit, "poisson"

            predictor_keys = map_columns_to_keys(pred_terms, key_by_col)
            term_labels = _ordered_unique(
                ([strat_var] if strat_var is not None else []) + predictor_keys
            )
            entries.append(
                {
                    "model": fit,
                    "family": family,
                    "predictors": ", ".join(term_labels),
                    "n_predictors": len(term_labels),
                    "bic": _glm_bic(fit, n),
                }
            )

    if len(entries) == 0:
        return _empty_result()

    candidates_df = pd.DataFrame(
        {
            "metric": metric_key,
            "model_id": np.nan,
            "predictors": [e["predictors"] for e in entries],
            "n_predictors": [e["n_predictors"] for e in entries],
            "r_squared": np.nan,
            "adj_r2": np.nan,
            "cp": np.nan,
            "bic": [e["bic"] for e in entries],
            "n_obs": n,
        }
    )
    order = np.argsort(candidates_df["bic"].to_numpy(), kind="stable")
    candidates_df = candidates_df.iloc[order].reset_index(drop=True)
    candidates_df["model_id"] = np.arange(1, len(candidates_df) + 1)
    candidates_df["delta_bic"] = candidates_df["bic"] - candidates_df["bic"].min()
    candidates_df["rank"] = np.arange(1, len(candidates_df) + 1)

    # Importance from top models
    top_models = candidates_df[candidates_df["delta_bic"] < 2]
    all_preds = _ordered_unique(
        t for preds in top_models["predictors"] for t in str(preds).split(", ")
    )
    # NOTE(parity): R uses grepl(p, predictors, fixed=TRUE) — plain SUBSTRING
    # matching on the comma-joined string, so e.g. "x1" also matches "x10".
    # Ported verbatim.
    importance_df = pd.DataFrame(
        {
            "metric": metric_key,
            "predictor": all_preds,
            "importance": [
                float(np.mean([p in str(s) for s in top_models["predictors"]]))
                for p in all_preds
            ],
        }
    )
    importance_df = importance_df.sort_values(
        "importance", ascending=False, kind="stable", ignore_index=True
    )

    return {"candidates_df": candidates_df, "importance_df": importance_df, "plots": {}}


# --------------------------------------------------------------------------- #
# Run all metrics (07) and final selection (08)
# --------------------------------------------------------------------------- #


def run_all_model_building(
    data: pd.DataFrame,
    strat_decisions,
    metric_config: dict,
    predictor_config: dict,
    strat_config: dict,
) -> dict:
    """Run model building for all metrics.

    Returns ``{"all_candidates": DataFrame, "all_importance": DataFrame,
    "all_plots": dict}`` (plot-data dicts keyed ``"<metric>_<plot>"``).
    """
    logger.info("Building model candidates for all metrics...")

    # NOTE(parity): R crashes on a metric with no metric_family
    # (if(logical(0))); the port treats a missing family as non-categorical.
    eligible = [
        mk
        for mk, mc in metric_config.items()
        if mc.get("metric_family") not in ("categorical",)
        and (mc.get("best_subsets_allowed") is True or mc.get("count_model") is True)
    ]

    logger.info("Processing %d eligible metrics...", len(eligible))

    results_list = []
    for metric_key in eligible:
        strat_dec = None
        if strat_decisions is not None and len(strat_decisions) > 0:
            hits = strat_decisions[strat_decisions["metric"] == metric_key]
            strat_dec = None if len(hits) == 0 else hits.iloc[0]
        results_list.append(
            build_model_candidates(
                data, metric_key, strat_dec, metric_config, predictor_config, strat_config
            )
        )

    cand_frames = [r["candidates_df"] for r in results_list if len(r["candidates_df"]) > 0]
    imp_frames = [r["importance_df"] for r in results_list if len(r["importance_df"]) > 0]
    all_candidates = (
        pd.concat(cand_frames, ignore_index=True) if cand_frames else pd.DataFrame()
    )
    all_importance = (
        pd.concat(imp_frames, ignore_index=True) if imp_frames else pd.DataFrame()
    )
    all_plots: dict = {}
    for mk, res in zip(eligible, results_list):
        for name, plot in res["plots"].items():
            all_plots[f"{mk}_{name}"] = plot

    n_metrics = all_candidates["metric"].nunique() if "metric" in all_candidates else 0
    logger.info("Model building complete: %d metrics processed", n_metrics)

    return {
        "all_candidates": all_candidates,
        "all_importance": all_importance,
        "all_plots": all_plots,
    }


_SELECTION_COLUMNS = [
    "metric",
    "selected_rank",
    "selected_model_id",
    "predictors",
    "n_predictors",
    "r_squared",
    "adj_r2",
    "bic",
    "delta_bic",
    "n_obs",
    "n_top_models",
    "selection_method",
    "needs_review",
    "review_reason",
]


def select_final_models(
    model_candidates: pd.DataFrame,
    predictor_importance,
    metric_config: dict,
) -> pd.DataFrame:
    """Select final models for all metrics (port of R/08_model_selection.R).

    Picks the most parsimonious model among the ΔBIC < 2 pool (fewest
    predictors, then lowest BIC) and flags selections for review.
    ``predictor_importance`` is accepted for signature parity with R but — as
    in R — unused.
    """
    logger.info("Selecting final models...")

    if model_candidates is None or len(model_candidates) == 0:
        logger.warning("No model candidates to select from")
        return pd.DataFrame()

    metrics_with_models = _ordered_unique(model_candidates["metric"].tolist())

    rows = []
    for metric_key in metrics_with_models:
        cands = model_candidates[model_candidates["metric"] == metric_key]

        if len(cands) == 0:  # unreachable via unique(); kept for parity
            rows.append(
                {
                    "metric": metric_key,
                    "selected_rank": np.nan,
                    "selected_model_id": np.nan,
                    "predictors": None,
                    "n_predictors": np.nan,
                    "r_squared": np.nan,
                    "adj_r2": np.nan,
                    "bic": np.nan,
                    "delta_bic": np.nan,
                    "n_obs": np.nan,
                    "n_top_models": np.nan,
                    "selection_method": None,
                    "needs_review": False,
                    "review_reason": None,
                }
            )
            continue

        # Top models (delta BIC < 2)
        top = cands[cands["delta_bic"] < 2]
        n_top = len(top)

        # Most parsimonious among top models (fewest predictors, then lowest BIC)
        selected = top.sort_values(["n_predictors", "bic"], kind="stable").iloc[0]

        needs_review = False
        review_reasons: list[str] = []

        if n_top > 3:
            needs_review = True
            review_reasons.append("many_top_models")

        if selected["n_predictors"] <= 1:
            needs_review = True
            review_reasons.append("too_simple")

        if selected["n_predictors"] > 5:
            needs_review = True
            review_reasons.append("too_complex")

        if not pd.isna(selected["adj_r2"]) and selected["adj_r2"] < 0.15:
            needs_review = True
            review_reasons.append("low_r_squared")

        # Tied top models (BIC range < 0.5)
        if n_top > 1:
            bic_range = top["bic"].max() - top["bic"].min()
            if bic_range < 0.5:
                needs_review = True
                review_reasons.append("tied_top_models")

        rows.append(
            {
                "metric": metric_key,
                "selected_rank": selected["rank"],
                "selected_model_id": selected["model_id"],
                "predictors": selected["predictors"],
                "n_predictors": selected["n_predictors"],
                "r_squared": selected["r_squared"],
                "adj_r2": selected["adj_r2"],
                "bic": selected["bic"],
                "delta_bic": selected["delta_bic"],
                "n_obs": selected["n_obs"],
                "n_top_models": n_top,
                "selection_method": "min_bic_parsimonious",
                "needs_review": needs_review,
                "review_reason": "; ".join(review_reasons) if review_reasons else None,
            }
        )

    selections = pd.DataFrame(rows, columns=_SELECTION_COLUMNS)

    n_selected = int(selections["predictors"].notna().sum())
    n_review = int(selections["needs_review"].fillna(False).sum())
    logger.info(
        "Model selection complete: %d models selected, %d need review", n_selected, n_review
    )

    return selections
