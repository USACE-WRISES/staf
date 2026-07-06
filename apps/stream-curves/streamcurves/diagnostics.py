"""Port of R/09_diagnostics.R.

Model assumption checks: normality (Shapiro-Wilk), heteroscedasticity
(Breusch-Pagan, Koenker studentized — lmtest::bptest default),
collinearity (car::vif incl. GVIF for factor terms), influence
(Cook's distance, 4/n threshold).

Formula handling: ``run_diagnostics`` accepts an R-style formula STRING and
fits it with statsmodels' formula API (patsy; treatment coding == R default).
Constraint: the app only ever builds additive formulas of plain column names.
Column names must be valid Python identifiers to be used bare; otherwise wrap
them as ``Q("name")`` in the formula (both forms are recognized when the
formula's variables are extracted). Function calls / transforms inside the
formula are not supported by the variable extraction.

car::vif parity: R computes (G)VIF from ``cov2cor(vcov(model))`` partitioned
by term (Fox & Monette). For OLS this equals the classic design-matrix VIF;
for GLMs it uses the IRLS-weighted coefficient covariance — this port does the
same, so Poisson-model VIFs match ``car::vif`` (not the raw design VIF).

R "negbin" analog: alpha via statsmodels NB2 MLE, then a GLM refit with the
NegativeBinomial family at fixed alpha and scale 1 — mirroring the glm-like
object ``MASS::glm.nb`` returns (deviance residuals / influence / vif all
behave like R's, where ``summary.negbin`` fixes dispersion at 1).
"""

from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd
import scipy.stats
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.stats.diagnostic import het_breuschpagan

logger = logging.getLogger("streamcurves")

__all__ = ["run_diagnostics", "run_all_diagnostics"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_FORMULA_TOKEN = re.compile(
    r"""Q\(\s*(?P<quote>['"])(?P<q>.*?)(?P=quote)\s*\)|(?P<id>[A-Za-z_.][A-Za-z0-9._]*)"""
)


def _formula_vars(formula: str) -> list[str]:
    """R ``all.vars()`` for the additive formulas this app builds.

    Extracts bare identifiers and ``Q("...")``-quoted names, in order of first
    appearance (response first).
    """
    out: list[str] = []
    for m in _FORMULA_TOKEN.finditer(formula):
        nm = m.group("q") if m.group("q") is not None else m.group("id")
        if nm and nm != "Q" and nm not in out:
            out.append(nm)
    return out


def _p_status(p) -> str:
    """pass / caution / fail bands for a test p-value (NA -> not_applicable)."""
    if p is None or pd.isna(p):
        return "not_applicable"
    if p >= 0.05:
        return "pass"
    if p >= 0.01:
        return "caution"
    return "fail"


def _fit_model(formula: str, model_data: pd.DataFrame, model_family: str):
    """lm / glm(poisson) / glm.nb analog; returns None on failure (R tryCatch)."""
    try:
        if model_family == "gaussian":
            return smf.ols(formula, data=model_data).fit()
        if model_family == "poisson":
            return smf.glm(formula, data=model_data, family=sm.families.Poisson()).fit()
        if model_family == "negbin":
            return _fit_negbin_glm(formula, model_data)
        # R falls back to lm() for unknown families
        return smf.ols(formula, data=model_data).fit()
    except Exception:
        return None


def _fit_negbin_glm(formula: str, model_data: pd.DataFrame):
    """MASS::glm.nb analog (see module docstring)."""
    mle = smf.negativebinomial(formula, data=model_data).fit(disp=0, maxiter=500)
    try:
        alpha = float(mle.params["alpha"])
    except (KeyError, IndexError):
        alpha = float(np.asarray(mle.params)[-1])
    if not np.isfinite(alpha) or alpha <= 0:
        alpha = 1e-8
    return smf.glm(
        formula, data=model_data, family=sm.families.NegativeBinomial(alpha=alpha)
    ).fit(scale=1.0)


def _residuals(model) -> np.ndarray:
    """R ``residuals(model)``: response residuals for lm, deviance for glm."""
    if hasattr(model, "resid_deviance"):
        return np.asarray(model.resid_deviance, dtype=float)
    return np.asarray(model.resid, dtype=float)


def _shapiro_p(resids: np.ndarray) -> float:
    """stats::shapiro.test p-value with R's limits (errors -> NA like tryCatch).

    R errors when n < 3 or n > 5000 ("sample size must be between 3 and 5000")
    and when all values are identical; those cases return NaN here.
    """
    try:
        n = resids.size
        if n < 3 or n > 5000:
            raise ValueError("sample size must be between 3 and 5000")
        if np.ptp(resids) == 0:
            raise ValueError("all 'x' values are identical")
        p = float(scipy.stats.shapiro(resids).pvalue)
    except Exception:
        return float("nan")
    return p


def _cov2cor(v: np.ndarray) -> np.ndarray:
    sd = np.sqrt(np.diag(v))
    return v / np.outer(sd, sd)


def _car_vif(model) -> pd.DataFrame:
    """car::vif.default replica.

    Partitions ``cov2cor(vcov(model))`` (intercept dropped) into the term's
    coefficient block R11 and the rest R22:
    ``GVIF_j = det(R11) * det(R22) / det(R)``. Returns one row per model term
    with columns GVIF, Df, and ``GVIF^(1/(2*Df))``; single-df terms' GVIF is
    the classic VIF.
    """
    params = np.asarray(model.params, dtype=float)
    if np.isnan(params).any():
        raise ValueError("there are aliased coefficients in the model")

    design_info = model.model.data.design_info
    term_names = list(design_info.term_names)
    v = np.asarray(model.cov_params(), dtype=float)

    coef_idx: dict[str, list[int]] = {
        t: list(range(sl.start, sl.stop)) for t, sl in design_info.term_name_slices.items()
    }
    if "Intercept" in term_names:
        keep = [i for t in term_names if t != "Intercept" for i in coef_idx[t]]
        v = v[np.ix_(keep, keep)]
        remap = {old: new for new, old in enumerate(keep)}
        coef_idx = {
            t: [remap[i] for i in idx]
            for t, idx in coef_idx.items()
            if t != "Intercept"
        }
        term_names = [t for t in term_names if t != "Intercept"]
    else:
        logger.warning("No intercept: vifs may not be sensible.")

    if len(term_names) < 2:
        raise ValueError("model contains fewer than 2 terms")

    corr = _cov2cor(v)
    det_r = np.linalg.det(corr)
    rows = []
    for term in term_names:
        subs = coef_idx[term]
        others = [i for i in range(corr.shape[0]) if i not in subs]
        gvif = (
            np.linalg.det(corr[np.ix_(subs, subs)])
            * np.linalg.det(corr[np.ix_(others, others)])
            / det_r
        )
        df = len(subs)
        rows.append(
            {"GVIF": float(gvif), "Df": df, "GVIF^(1/(2*Df))": float(gvif ** (1.0 / (2 * df)))}
        )
    return pd.DataFrame(rows, index=pd.Index(term_names, name="term"))


def _failure_summary_row(metric_key, formula, model_family, n_obs, notes) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "metric": metric_key,
                "formula": formula,
                "model_family": model_family,
                "n_obs": n_obs,
                "shapiro_p": np.nan,
                "shapiro_status": "not_applicable",
                "bp_p": np.nan,
                "bp_status": "not_applicable",
                "max_vif": np.nan,
                "vif_status": "not_applicable",
                "max_cooks": np.nan,
                "cooks_status": "not_applicable",
                "overall_status": "fail",
                "notes": notes,
            }
        ]
    )


# --------------------------------------------------------------------------- #
# Single-model diagnostics
# --------------------------------------------------------------------------- #


def run_diagnostics(
    data: pd.DataFrame,
    metric_key: str,
    formula: str,
    model_family: str = "gaussian",
    metric_config: dict | None = None,
) -> dict:
    """Run diagnostics for a single metric model.

    Returns ``{"summary_row": one-row DataFrame, "plots": dict, "model": fit}``
    (the ``model`` key is absent when fitting fails, matching R's early
    return). ``plots["diagnostic_4panel"]`` — gaussian models only — is a
    DataFrame with the exact data behind R's 4-panel plot: columns ``fitted``,
    ``residuals``, ``std_resid``, ``sqrt_std_resid``, ``leverage``,
    ``cooks_d``, ``obs`` (residuals-vs-fitted, normal QQ of std_resid,
    scale-location, residuals-vs-leverage; the views layer computes the QQ
    theoretical quantiles).
    """
    logger.info("Running diagnostics for %s...", metric_key)

    # R assigns mc here but never uses it (plots have generic titles);
    # kept out of the port.

    # -- Fit model --------------------------------------------------------------
    formula_vars = _formula_vars(formula)
    subset_cols = [v for v in formula_vars if v in data.columns]
    model_data = data.dropna(subset=subset_cols) if subset_cols else data

    model = _fit_model(formula, model_data, model_family)

    if model is None:
        logger.warning("Model fitting failed for %s", metric_key)
        return {
            "summary_row": _failure_summary_row(
                metric_key, formula, model_family, len(model_data), "model_fitting_failed"
            ),
            "plots": {},
        }

    # -- Shapiro-Wilk test (normality) --------------------------------------------
    resids = _residuals(model)
    shapiro_p = _shapiro_p(resids)
    shapiro_status = _p_status(shapiro_p)

    # -- Breusch-Pagan test (heteroscedasticity) -----------------------------------
    bp_p = np.nan
    bp_status = "not_applicable"
    if model_family == "gaussian":
        try:
            # lmtest::bptest default = studentized Koenker LM test with the
            # model's own regressors; exog includes the intercept column.
            bp_p = float(
                het_breuschpagan(np.asarray(model.resid), np.asarray(model.model.exog))[1]
            )
        except Exception:
            bp_p = np.nan
        bp_status = _p_status(bp_p)

    # -- VIF (collinearity) ---------------------------------------------------------
    max_vif = np.nan
    vif_status = "not_applicable"
    n_pred = len(formula_vars) - 1
    if n_pred >= 2:
        try:
            vif_tbl = _car_vif(model)
        except Exception:
            vif_tbl = None
        if vif_tbl is not None:
            # R: factor terms make car::vif return a matrix -> take the
            # GVIF^(1/(2*Df)) column; otherwise the plain VIF vector.
            if (vif_tbl["Df"] > 1).any():
                max_vif = float(vif_tbl["GVIF^(1/(2*Df))"].max())
            else:
                max_vif = float(vif_tbl["GVIF"].max())
            if max_vif < 5:
                vif_status = "pass"
            elif max_vif < 10:
                vif_status = "caution"
            else:
                vif_status = "fail"

    # -- Cook's distance (influence) --------------------------------------------
    # No tryCatch in R: errors propagate to run_all_diagnostics' handler.
    influence = model.get_influence()
    cooks = np.asarray(influence.cooks_distance[0], dtype=float)
    max_cooks = float(np.nanmax(cooks))
    cooks_threshold = 4.0 / len(model_data)
    n_influential = int(np.nansum(cooks > cooks_threshold))
    if max_cooks < cooks_threshold:
        cooks_status = "pass"
    elif n_influential <= 2:
        cooks_status = "caution"
    else:
        cooks_status = "fail"

    # -- Overall status ------------------------------------------------------------
    statuses = [
        s
        for s in (shapiro_status, bp_status, vif_status, cooks_status)
        if s != "not_applicable"
    ]
    if "fail" in statuses:
        overall = "fail"
    elif "caution" in statuses:
        overall = "caution"
    else:
        overall = "pass"

    # -- Diagnostic plot data --------------------------------------------------------
    plots: dict = {}
    if model_family == "gaussian":
        try:
            std_resid = np.asarray(influence.resid_studentized_internal, dtype=float)
            plots["diagnostic_4panel"] = pd.DataFrame(
                {
                    "fitted": np.asarray(model.fittedvalues, dtype=float),
                    "residuals": resids,
                    "std_resid": std_resid,
                    "sqrt_std_resid": np.sqrt(np.abs(std_resid)),
                    "leverage": np.asarray(influence.hat_matrix_diag, dtype=float),
                    "cooks_d": cooks,
                    "obs": np.arange(1, resids.size + 1),
                }
            )
        except Exception as e:
            logger.warning("Diagnostic plot failed for %s: %s", metric_key, e)

    summary_row = pd.DataFrame(
        [
            {
                "metric": metric_key,
                "formula": formula,
                "model_family": model_family,
                "n_obs": len(model_data),
                "shapiro_p": shapiro_p,
                "shapiro_status": shapiro_status,
                "bp_p": bp_p,
                "bp_status": bp_status,
                "max_vif": max_vif,
                "vif_status": vif_status,
                "max_cooks": max_cooks,
                "cooks_status": cooks_status,
                "overall_status": overall,
                "notes": None,
            }
        ]
    )

    return {"summary_row": summary_row, "plots": plots, "model": model}


# --------------------------------------------------------------------------- #
# All selected models
# --------------------------------------------------------------------------- #


def run_all_diagnostics(
    data: pd.DataFrame,
    model_selections: pd.DataFrame,
    strat_decisions,
    metric_config: dict,
) -> dict:
    """Run diagnostics for all selected models.

    Returns ``{"summary_df": DataFrame, "all_plots": dict, "all_models": dict}``
    (plots keyed ``"<metric>_<plot>"``, models keyed by metric).
    """
    logger.info("Running diagnostics for all selected models...")

    valid_rows = np.flatnonzero(model_selections["predictors"].notna().to_numpy())
    logger.info("Processing %d models...", len(valid_rows))

    results_list = []
    metric_keys = []
    for i in valid_rows:
        sel = model_selections.iloc[i]
        metric_key = sel["metric"]
        metric_keys.append(metric_key)
        formula_str = ""
        model_family = "gaussian"

        try:
            # NOTE(parity): R degrades to a one-sided formula (and an inner
            # "model_fitting_failed" row) when metric_config lacks the metric;
            # here the KeyError lands in this handler instead. Both fail.
            mc = metric_config[metric_key]

            pred_list = [t.strip() for t in str(sel["predictors"]).split(",")]
            response = mc["column_name"]

            # NOTE(parity): selected_strat is the stratification KEY and
            # pred_list holds predictor KEYS, yet both are checked against —
            # and pasted into a formula of — data COLUMN names. Ported
            # verbatim from R; it only works when key == column name.
            if strat_decisions is not None and len(strat_decisions) > 0:
                strat_dec = strat_decisions[strat_decisions["metric"] == metric_key]
                if len(strat_dec) > 0 and strat_dec.iloc[0]["decision_type"] == "single":
                    strat_var = strat_dec.iloc[0]["selected_strat"]
                    if (
                        not pd.isna(strat_var)
                        and strat_var not in pred_list
                        and strat_var in data.columns
                    ):
                        pred_list = [strat_var] + pred_list

            formula_str = f"{response} ~ {' + '.join(pred_list)}"
            # NOTE(parity): always "poisson" for count metrics — R never passes
            # "negbin" here even when the selected candidate was NB.
            model_family = "poisson" if mc.get("count_model") is True else "gaussian"

            res = run_diagnostics(data, metric_key, formula_str, model_family, metric_config)
        except Exception as e:
            res = {
                "summary_row": _failure_summary_row(
                    metric_key, formula_str, model_family, np.nan, f"error: {e}"
                ),
                "plots": {},
                "model": None,
            }
        results_list.append(res)

    if results_list:
        summary_df = pd.concat(
            [r["summary_row"] for r in results_list], ignore_index=True
        )
    else:
        summary_df = pd.DataFrame()

    all_plots: dict = {}
    for mk, res in zip(metric_keys, results_list):
        for name, plot in res["plots"].items():
            all_plots[f"{mk}_{name}"] = plot

    all_models = {
        mk: res["model"]
        for mk, res in zip(metric_keys, results_list)
        if res.get("model") is not None
    }

    status = summary_df["overall_status"] if "overall_status" in summary_df else pd.Series(dtype=object)
    n_pass = int((status == "pass").sum())
    n_caution = int((status == "caution").sum())
    n_fail = int((status == "fail").sum())
    logger.info("Diagnostics complete: %d pass, %d caution, %d fail", n_pass, n_caution, n_fail)
    if n_fail > 0:
        fail_metrics = summary_df.loc[status == "fail", "metric"].tolist()
        logger.warning("  Failed: %s", ", ".join(map(str, fail_metrics)))
    if n_caution > 0:
        caution_metrics = summary_df.loc[status == "caution", "metric"].tolist()
        logger.info("  Caution: %s", ", ".join(map(str, caution_metrics)))

    return {"summary_df": summary_df, "all_plots": all_plots, "all_models": all_models}
