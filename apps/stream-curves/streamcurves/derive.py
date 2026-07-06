"""Port of R/03_derive_variables.R — derived variables & factor recoding.

Computes derived predictors (derivation methods: exactly ``sum``, ``multiply``,
``multiply_by_constant``, ``divide_by_constant``), applies workbook factor
recodes (``forcats::fct_collapse`` semantics: values not named in the collapse
map stay unchanged), and materializes workbook-defined custom stratifications.
"""

from __future__ import annotations

import logging
import math
import re

import numpy as np
import pandas as pd

from ._rcompat import (
    as_character_scalar,
    as_numeric_r,
    compact_chr,
    is_na_scalar,
    is_true,
    or_,
    r_num_str,
)

logger = logging.getLogger("streamcurves")

_RULE_CLAUSE_RE = re.compile(r"^\s*(<=|>=|<|>)\s*(-?\d*\.?\d+(?:[eE][+-]?\d+)?)\s*$")


def _as_list(x) -> list:
    if x is None:
        return []
    if isinstance(x, (str, bytes)):
        return [x]
    if isinstance(x, (pd.Series, pd.Index, np.ndarray)):
        return list(x)
    if isinstance(x, (list, tuple, set)):
        return list(x)
    return [x]


def _is_finite_number(x) -> bool:
    if x is None or isinstance(x, (bool, np.bool_)):
        return False
    if not isinstance(x, (int, float, np.integer, np.floating)):
        return False
    return math.isfinite(float(x))


def evaluate_derivation_method(data: pd.DataFrame, predictor_key: str, predictor_cfg: dict):
    """Evaluate a predictor's derivation, returning
    ``{"column_name": ..., "values": Series | None}`` or None when nothing to do."""
    predictor_cfg = predictor_cfg or {}
    target_col = or_(predictor_cfg.get("column_name"), predictor_key)
    method = or_(predictor_cfg.get("derivation_method"), "none")
    source_cols = _as_list(or_(predictor_cfg.get("source_columns"), []))

    if not is_true(predictor_cfg.get("derived")):
        return None

    if method == "none":
        return None

    missing_sources = [c for c in source_cols if c not in data.columns]
    if missing_sources:
        logger.info(
            "Skipping %s: missing source column(s) %s",
            target_col,
            ", ".join(missing_sources),
        )
        return None

    if method == "sum":
        # NOTE(parity): with zero source columns R's Reduce() yields NULL and
        # the assignment in derive_variables() is a no-op — mirrored as None.
        result = None
        for col in source_cols:
            result = data[col] if result is None else result + data[col]
    elif method == "multiply":
        result = None
        for col in source_cols:
            result = data[col] if result is None else result * data[col]
    elif method == "multiply_by_constant":
        constant = predictor_cfg.get("constant")
        if len(source_cols) != 1 or not _is_finite_number(constant):
            raise ValueError(
                f"Predictor '{predictor_key}' requires exactly one source column and "
                "a finite constant for derivation_method='multiply_by_constant'."
            )
        result = data[source_cols[0]] * float(constant)
    elif method == "divide_by_constant":
        constant = predictor_cfg.get("constant")
        if (
            len(source_cols) != 1
            or not _is_finite_number(constant)
            or float(constant) == 0
        ):
            raise ValueError(
                f"Predictor '{predictor_key}' requires exactly one source column and "
                "a non-zero finite constant for derivation_method='divide_by_constant'."
            )
        result = data[source_cols[0]] / float(constant)
    else:
        raise ValueError(
            f"Unsupported derivation_method '{method}' for predictor '{predictor_key}'."
        )

    return {"column_name": target_col, "values": result}


def parse_numeric_rule_clause(clause_text) -> dict:
    """Parse one clause of a continuous grouping rule.

    Grammar: ``^\\s*(<=|>=|<|>)\\s*number\\s*$`` (number may be signed and may
    carry an exponent). Returns ``{"operator": ..., "threshold": float}``.
    """
    m = _RULE_CLAUSE_RE.match(str(clause_text))
    if m is None:
        raise ValueError(
            f"Invalid continuous rule clause '{clause_text}'. "
            "Use clauses like <= 1, > 1, or > 1 & <= 5."
        )
    return {"operator": m.group(1), "threshold": float(m.group(2))}


def evaluate_numeric_rule(values, rule_expression) -> np.ndarray:
    """Evaluate a continuous grouping rule (clauses joined by ``&``) against
    numeric values. Returns a boolean ndarray; NA values never match."""
    clauses = compact_chr(str(rule_expression).split("&"))
    if len(clauses) == 0:
        raise ValueError("Continuous grouping rules cannot be blank.")

    if isinstance(values, pd.Series):
        vals = values.to_numpy(dtype=float, na_value=np.nan)
    else:
        vals = np.asarray(values, dtype=float)

    mask = np.ones(vals.shape[0], dtype=bool)
    for clause in clauses:
        parsed = parse_numeric_rule_clause(clause)
        op, thr = parsed["operator"], parsed["threshold"]
        with np.errstate(invalid="ignore"):
            if op == "<":
                clause_mask = vals < thr
            elif op == "<=":
                clause_mask = vals <= thr
            elif op == ">":
                clause_mask = vals > thr
            else:  # ">="
                clause_mask = vals >= thr
        mask &= clause_mask

    return mask & ~np.isnan(vals)


def materialize_categorical_custom_group(source_values, sc, strat_key) -> pd.Categorical:
    sc = sc or {}
    defs = or_(sc.get("group_definitions"), [])
    labels = [d["group_label"] for d in defs]
    assignments = [_as_list(d.get("source_values")) for d in defs]

    if any(len(a) == 0 for a in assignments):
        raise ValueError(
            f"Categorical custom grouping '{strat_key}' contains an empty "
            "source_values mapping."
        )

    flat = [v for a in assignments for v in a]
    seen: set = set()
    dup_values: list = []
    for v in flat:
        if v in seen and v not in dup_values:
            dup_values.append(v)
        seen.add(v)
    if dup_values:
        raise ValueError(
            f"Categorical custom grouping '{strat_key}' assigns source values to "
            f"multiple groups: {', '.join(str(v) for v in dup_values)}"
        )

    sv = pd.Series(source_values)
    sv_chr = [as_character_scalar(v) for v in sv]
    observed = sorted({s for s in sv_chr if s is not None})
    missing_values = [v for v in observed if v not in flat]
    if missing_values:
        raise ValueError(
            f"Categorical custom grouping '{strat_key}' does not assign these "
            f"observed values: {', '.join(missing_values)}"
        )

    out = np.array([None] * len(sv), dtype=object)
    for d, label in zip(defs, labels):
        wanted = set(_as_list(d.get("source_values")))
        hit = np.array([s is not None and s in wanted for s in sv_chr], dtype=bool)
        out[hit] = label

    levels = list(or_(sc.get("levels"), labels))
    return pd.Categorical(out, categories=levels)


def materialize_continuous_custom_group(source_values, sc, strat_key) -> pd.Categorical:
    sc = sc or {}
    sv = pd.Series(source_values)
    numeric_values = as_numeric_r(sv)
    bad = sv.notna().to_numpy() & numeric_values.isna().to_numpy()
    if bad.any():
        src = or_(sc.get("source_column"), sc.get("column_name"))
        raise ValueError(
            f"Continuous custom grouping '{strat_key}' references non-numeric data "
            f"in column '{'NA' if src is None else src}'."
        )

    defs = or_(sc.get("group_definitions"), [])
    labels = [d["group_label"] for d in defs]
    vals = numeric_values.to_numpy(dtype=float, na_value=np.nan)

    match_cols = []
    for d in defs:
        rule_expression = or_(d.get("rule_expression"), None)
        if rule_expression is None or is_na_scalar(rule_expression) or rule_expression == "":
            raise ValueError(
                f"Continuous custom grouping '{strat_key}' has a blank rule_expression."
            )
        match_cols.append(evaluate_numeric_rule(vals, rule_expression))

    match_matrix = (
        np.column_stack(match_cols)
        if match_cols
        else np.zeros((len(vals), 0), dtype=bool)
    )

    valid_rows = ~np.isnan(vals)
    match_count = match_matrix.sum(axis=1)
    bad_idx = np.where(valid_rows & (match_count != 1))[0]

    if bad_idx.size > 0:
        bad_values: list[float] = []
        for i in bad_idx:
            if vals[i] not in bad_values:
                bad_values.append(vals[i])
        bad_values = bad_values[: min(len(bad_values), 5)]
        problem_type = "unmatched" if np.any(match_count[bad_idx] == 0) else "overlapping"
        raise ValueError(
            f"Continuous custom grouping '{strat_key}' has {problem_type} rules for "
            f"observed values: {', '.join(r_num_str(v) for v in bad_values)}"
        )

    out = np.array([None] * len(vals), dtype=object)
    for idx, label in enumerate(labels):
        out[match_matrix[:, idx]] = label

    levels = list(or_(sc.get("levels"), labels))
    return pd.Categorical(out, categories=levels)


def materialize_custom_stratifications(data: pd.DataFrame, strat_config) -> pd.DataFrame:
    custom_keys = [
        k for k, sc in (strat_config or {}).items() if is_true((sc or {}).get("is_custom_grouping"))
    ]
    if not custom_keys:
        return data

    data = data.copy()
    for strat_key in custom_keys:
        sc = strat_config[strat_key] or {}
        source_col = or_(sc.get("source_column"), None)
        target_col = or_(sc.get("column_name"), strat_key)

        if source_col not in data.columns:
            raise ValueError(
                f"Custom grouping '{strat_key}' references missing source column "
                f"'{'NA' if source_col is None else source_col}'."
            )

        if or_(sc.get("source_data_type"), "categorical") == "continuous":
            data[target_col] = materialize_continuous_custom_group(
                data[source_col], sc, strat_key
            )
        else:
            data[target_col] = materialize_categorical_custom_group(
                data[source_col], sc, strat_key
            )

        logger.info(
            "Materialized custom stratification %s from %s", target_col, source_col
        )

    return data


def _fct_collapse(values: pd.Series, collapse_map: dict) -> pd.Categorical:
    """``forcats::fct_collapse`` semantics: named groups of old levels collapse
    to the group name at the position of their first member; unnamed levels
    stay unchanged. Unknown old levels warn and are ignored."""
    if isinstance(values.dtype, pd.CategoricalDtype):
        old_levels = list(values.cat.categories)
    else:
        cat = pd.Categorical(values)
        old_levels = list(cat.categories)

    mapping: dict = {}
    unknown: list = []
    level_set = set(old_levels)
    for new_level, olds in (collapse_map or {}).items():
        for old in _as_list(olds):
            if old not in level_set:
                unknown.append(old)
                continue
            mapping[old] = new_level
    if unknown:
        logger.warning("Unknown levels in `f`: %s", ", ".join(str(u) for u in unknown))

    new_levels: list = []
    for lvl in old_levels:
        nl = mapping.get(lvl, lvl)
        if nl not in new_levels:
            new_levels.append(nl)

    new_values = [
        None if is_na_scalar(v) else mapping.get(v, v) for v in values.tolist()
    ]
    return pd.Categorical(new_values, categories=new_levels)


def derive_variables(
    data: pd.DataFrame,
    factor_recode_config,
    predictor_config,
    strat_config=None,
) -> pd.DataFrame:
    """Derive computed variables, apply factor recoding, materialize custom
    stratifications. Returns the augmented DataFrame."""
    strat_config = strat_config if strat_config is not None else {}

    logger.info("Deriving variables and recoding factors...")
    data = data.copy()

    derived_msgs: list[str] = []
    for predictor_key, predictor_cfg in (predictor_config or {}).items():
        derivation = evaluate_derivation_method(data, predictor_key, predictor_cfg)
        if derivation is None:
            continue
        if derivation["values"] is not None:
            data[derivation["column_name"]] = derivation["values"]
        derived_msgs.append(derivation["column_name"])

    if derived_msgs:
        logger.info("Derived: %s", ", ".join(derived_msgs))

    for recode_name, recode in (factor_recode_config or {}).items():
        src_col = recode["source_column"]
        tgt_col = recode["target_column"]

        if src_col not in data.columns:
            logger.warning(
                "Source column %s not found for %s, skipping", src_col, recode_name
            )
            continue

        if not isinstance(data[src_col].dtype, pd.CategoricalDtype):
            data[src_col] = pd.Categorical(data[src_col])

        data[tgt_col] = _fct_collapse(data[src_col], recode.get("collapse_map") or {})

        levels = list(data[tgt_col].cat.categories)
        logger.info(
            "Recoded %s -> %s (%d levels: %s)",
            src_col,
            tgt_col,
            len(levels),
            ", ".join(str(lv) for lv in levels),
        )

    data = materialize_custom_stratifications(data, strat_config)

    custom_cols = [
        (sc or {}).get("column_name")
        for sc in (strat_config or {}).values()
        if is_true((sc or {}).get("is_custom_grouping"))
    ]
    derived_cols: list[str] = []
    for col in derived_msgs + custom_cols:
        if col is None or is_na_scalar(col):
            continue
        if col not in derived_cols:
            derived_cols.append(col)

    for col in derived_cols:
        if col in data.columns:
            n_na = int(data[col].isna().sum())
            if n_na > 0:
                logger.warning("Derived column %s has %d NAs", col, n_na)

    logger.info("Variable derivation complete: %d total columns", data.shape[1])

    return data
