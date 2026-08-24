"""Port of app/helpers/data_profiler.R — data profiler & role -> workbook tables.

Pure helpers used by the setup wizard to turn a raw DataFrame (e.g. an uploaded
CSV) into the StreamCurves workbook ``tables`` dict. Columns can carry MULTIPLE
roles (metric / predictor / stratifier); a numeric column used as a stratifier
is routed through a binned, derived column (custom_group, continuous).

``current_role_membership`` / ``reconcile_role_membership`` reuse workbook
table helpers that live in R/00_input_workbook.R and
app/modules/mod_data_overview.R. Their canonical Python ports are not part of
this module set yet, so the needed pure pieces are ported privately below
(``_ensure_workbook_sheet_columns``, ``_normalize_workbook_tables``,
``_coerce_flag``, ``_metadata_table_to_editor_df``, ``_build_metrics_editor_df``,
``_delete_rows_from_tables`` + cascade). Consolidate with the canonical
workbook port when it lands.
"""

from __future__ import annotations

import importlib
import io
import logging
import math
import re

import numpy as np
import pandas as pd

from ._rcompat import (
    as_character_scalar,
    compact_chr,
    is_na_scalar,
    or_,
    r_format_common,
    r_num_str,
    r_round,
    r_signif,
    trim_character_columns,
)
from . import metric_names

logger = logging.getLogger("streamcurves")


def data_role_levels() -> list[str]:
    """Column roles a user assigns in the wizard's "Classify columns" step."""
    return ["metric", "predictor", "stratifier"]


def metric_family_levels() -> list[str]:
    return ["continuous", "proportion", "count", "categorical"]


# ---------------------------------------------------------------------------
# profile_columns(df) -> one row per column describing type / spread / hints
# ---------------------------------------------------------------------------

_PROFILE_COLUMNS = [
    "column",
    "position",
    "r_type",
    "is_numeric",
    "n_total",
    "n_unique",
    "n_missing",
    "pct_missing",
    "min",
    "max",
    "examples",
    "is_constant",
    "looks_like_id",
    "looks_like_coord",
    "suggested_family",
]

_ID_NAME_RE = re.compile(r"^(id|site|name|code|station|sample|gage|gauge|reach)s?$")
_COORD_NAME_RE = re.compile(r"lat|long|lon|coord|utm|easting|northing")
_PROP_NAME_RE = re.compile(r"per|pct|prop|ratio|percent|frac|share|%")


def _is_numeric_series(x: pd.Series) -> bool:
    """R is.numeric(): numeric storage — excludes logical, factor, character."""
    if isinstance(x.dtype, pd.CategoricalDtype):
        return False
    if pd.api.types.is_bool_dtype(x.dtype):
        return False
    return pd.api.types.is_numeric_dtype(x.dtype)


def _empty_profile() -> pd.DataFrame:
    d = {
        "column": pd.Series([], dtype=object),
        "position": pd.Series([], dtype="int64"),
        "r_type": pd.Series([], dtype=object),
        "is_numeric": pd.Series([], dtype=bool),
        "n_total": pd.Series([], dtype="int64"),
        "n_unique": pd.Series([], dtype="int64"),
        "n_missing": pd.Series([], dtype="int64"),
        "pct_missing": pd.Series([], dtype="float64"),
        "min": pd.Series([], dtype="float64"),
        "max": pd.Series([], dtype="float64"),
        "examples": pd.Series([], dtype=object),
        "is_constant": pd.Series([], dtype=bool),
        "looks_like_id": pd.Series([], dtype=bool),
        "looks_like_coord": pd.Series([], dtype=bool),
        "suggested_family": pd.Series([], dtype=object),
    }
    return pd.DataFrame(d)


def profile_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols = list(df.columns)
    n_total = len(df)

    if len(cols) == 0:
        return _empty_profile()

    rows = []
    for i, col in enumerate(cols, start=1):
        x = df[col]
        non_na = x[x.notna()]
        n_missing = int(x.isna().sum())
        n_unique = int(non_na.nunique())
        is_num = _is_numeric_series(x)
        int_like = bool(
            is_num
            and len(non_na) > 0
            and bool(
                np.all(
                    non_na.to_numpy(dtype=float)
                    == np.round(non_na.to_numpy(dtype=float))
                )
            )
        )

        if isinstance(x.dtype, pd.CategoricalDtype):
            r_type = "factor"
        elif pd.api.types.is_bool_dtype(x.dtype):
            r_type = "logical"
        elif is_num and int_like:
            r_type = "integer"
        elif is_num:
            r_type = "numeric"
        else:
            r_type = "character"

        rng_min = float(non_na.min()) if is_num and len(non_na) > 0 else np.nan
        rng_max = float(non_na.max()) if is_num and len(non_na) > 0 else np.nan

        uniq_chr: list[str] = []
        for v in non_na:
            s = as_character_scalar(v)
            if s not in uniq_chr:
                uniq_chr.append(s)
            if len(uniq_chr) >= 4:
                break
        examples = ", ".join(uniq_chr)

        name_l = str(col).lower()
        looks_like_id = (
            (i == 1 and not is_num)
            or _ID_NAME_RE.search(name_l) is not None
            or (not is_num and n_total > 0 and n_unique >= n_total * 0.95)
        )
        looks_like_coord = _COORD_NAME_RE.search(name_l) is not None

        rows.append(
            {
                "column": col,
                "position": i,
                "r_type": r_type,
                "is_numeric": bool(is_num),
                "n_total": n_total,
                "n_unique": n_unique,
                "n_missing": n_missing,
                "pct_missing": r_round(100 * n_missing / n_total, 1)
                if n_total > 0
                else 0.0,
                "min": rng_min,
                "max": rng_max,
                "examples": examples,
                "is_constant": bool(n_unique <= 1),
                "looks_like_id": bool(looks_like_id),
                "looks_like_coord": bool(looks_like_coord),
                "suggested_family": suggest_metric_family(x, col),
            }
        )

    return pd.DataFrame(rows, columns=_PROFILE_COLUMNS)


# ---------------------------------------------------------------------------
# suggest_metric_family(x, col_name) -> "continuous" | "proportion" | "count"
#                                       | "categorical"
# ---------------------------------------------------------------------------


def suggest_metric_family(x, col_name: str = "") -> str:
    s = x if isinstance(x, pd.Series) else pd.Series(x)
    if not _is_numeric_series(s):
        return "categorical"
    non_na = s.dropna().to_numpy(dtype=float)
    if non_na.size == 0:
        return "continuous"

    name_l = str(col_name).lower()
    prop_name = _PROP_NAME_RE.search(name_l) is not None
    int_like = bool(np.all(non_na == np.round(non_na)))
    rng_min = float(non_na.min())
    rng_max = float(non_na.max())

    if (rng_min >= 0 and rng_max <= 1) or (rng_min >= 0 and rng_max <= 100 and prop_name):
        return "proportion"
    if int_like and rng_min >= 0 and rng_max <= 50:
        return "count"
    return "continuous"


# ---------------------------------------------------------------------------
# suggest_roles_from_library(col_name) -> subset of data_role_levels(), or []
# metric_map.yaml (curated) wins; the NRSA catalog is the broad fallback.
# Guarded like the R exists()/tryCatch pattern so the profiler works when the
# metric-map / NRSA modules are not available (isolated tests).
#
# NOTE: the fallback used to name streamcurves.nrsa_metrics and two
# streamcurves.datasources.* paths, none of which exist -- they were guesses at
# the R filename (app/helpers/nrsa_metrics.R). The import error fell into the
# except below, so the catalog fallback never ran and 762 of the 788 catalog
# codes got no role suggestion at all. test_profiler_role_library.py now asserts
# every path in this tuple imports.
# ---------------------------------------------------------------------------

_LIBRARY_LOOKUPS = (
    ("streamcurves.metric_map", "metric_map_role_for"),
    ("streamcurves.nrsa", "nrsa_catalog_role_for"),
)


def _valid_role(role) -> bool:
    return isinstance(role, str) and not is_na_scalar(role)


def suggest_roles_from_library(col_name: str) -> list[str]:
    role = None
    for mod_path, func_name in _LIBRARY_LOOKUPS:
        try:
            mod = importlib.import_module(mod_path)
            candidate = getattr(mod, func_name)(col_name)
        except Exception:
            candidate = None
        if _valid_role(candidate):
            role = candidate
            break
    if role is None:
        return []
    return {
        "metric": ["metric"],
        "predictor": ["predictor"],
        "both": ["metric", "predictor"],
        "stratifier": ["stratifier"],
    }.get(role, [])


# ---------------------------------------------------------------------------
# suggest_roles(profile) -> adds `role` (single best-guess primary) plus the
# multi-role flags role_metric / role_predictor / role_stratifier.
# ---------------------------------------------------------------------------


def suggest_roles(profile: pd.DataFrame) -> pd.DataFrame:
    profile = profile.copy()
    if len(profile) == 0:
        profile["role"] = pd.Series([], dtype=object)
        profile["role_metric"] = pd.Series([], dtype=bool)
        profile["role_predictor"] = pd.Series([], dtype=bool)
        profile["role_stratifier"] = pd.Series([], dtype=bool)
        return profile

    roles: list[str] = []
    flags: list[tuple[bool, bool, bool]] = []
    for _, row in profile.iterrows():
        # Primary single-role guess (back-compat)
        if bool(row["is_constant"]) or row["pct_missing"] >= 100:
            roles.append("ignore")
        elif bool(row["looks_like_id"]):
            roles.append("identifier")
        elif bool(row["looks_like_coord"]):
            roles.append("ignore")
        elif bool(row["is_numeric"]):
            roles.append("predictor")
        elif 2 <= row["n_unique"] <= 15:
            roles.append("stratifier")
        else:
            roles.append("ignore")

        # Multi-role flags: exclusions first, then library match, then structural
        if (
            bool(row["is_constant"])
            or row["pct_missing"] >= 100
            or bool(row["looks_like_id"])
            or bool(row["looks_like_coord"])
        ):
            flags.append((False, False, False))
            continue
        lib = suggest_roles_from_library(row["column"])
        if lib:
            flags.append(("metric" in lib, "predictor" in lib, "stratifier" in lib))
        elif bool(row["is_numeric"]):
            flags.append((False, True, False))
        elif 2 <= row["n_unique"] <= 15:
            flags.append((False, False, True))
        else:
            flags.append((False, False, False))

    profile["role"] = roles
    profile["role_metric"] = [f[0] for f in flags]
    profile["role_predictor"] = [f[1] for f in flags]
    profile["role_stratifier"] = [f[2] for f in flags]
    return profile


def profile_and_suggest(df: pd.DataFrame) -> pd.DataFrame:
    """Convenience: profile + suggest in one call."""
    return suggest_roles(profile_columns(df))


# ---------------------------------------------------------------------------
# sanitize_keys(cols) -> syntactically clean, unique identifier keys.
# make.names(unique=TRUE) semantics, THEN dots -> underscores and
# trailing-underscore strip, blank -> col_<i>, final make.unique(sep="_").
# ---------------------------------------------------------------------------

_R_KEYWORDS = {
    "if", "else", "repeat", "while", "function", "for", "in", "next", "break",
    "TRUE", "FALSE", "NULL", "Inf", "NaN", "NA",
    "NA_integer_", "NA_real_", "NA_character_", "NA_complex_",
}


def _make_names(values) -> list[str]:
    """base::make.names() translation (without uniquing).

    Invalid characters -> "."; "X" prepended when the ORIGINAL name does not
    start with a letter or a dot (or is a dot followed by a digit); R keywords
    get a "." appended. ASCII letter check (R uses locale collation).
    """
    out = []
    for nm in values:
        s = "NA" if nm is None or is_na_scalar(nm) else str(nm)
        first = s[0] if s else ""
        need_x = (s == "") or not (
            (first.isascii() and first.isalpha()) or first == "."
        )
        if not need_x and first == "." and len(s) > 1 and s[1].isdigit():
            need_x = True
        t = re.sub(r"[^A-Za-z0-9._]", ".", s)
        if need_x:
            t = "X" + t
        if t in _R_KEYWORDS:
            t += "."
        out.append(t)
    return out


def _make_unique(values, sep: str = ".") -> list[str]:
    """base::make.unique(): 2nd+ occurrences get <sep><k> suffixes, skipping
    candidates that collide with any name in the vector."""
    values = [str(v) for v in values]
    taken = set(values)
    counts: dict[str, int] = {}
    seen: dict[str, int] = {}
    out = []
    for v in values:
        occ = seen.get(v, 0)
        seen[v] = occ + 1
        if occ == 0:
            out.append(v)
            continue
        k = counts.get(v, 1)
        cand = f"{v}{sep}{k}"
        while cand in taken:
            k += 1
            cand = f"{v}{sep}{k}"
        counts[v] = k + 1
        taken.add(cand)
        out.append(cand)
    return out


def _make_names_unique(values) -> list[str]:
    """make.names(x, unique = TRUE): names that were already valid win the
    dedupe (R reorders with ``order(names != names2)`` before make.unique)."""
    values = list(values)
    translated = _make_names(values)
    changed = [
        ("NA" if v is None or is_na_scalar(v) else str(v)) != t
        for v, t in zip(values, translated)
    ]
    order_idx = sorted(range(len(translated)), key=lambda i: changed[i])
    uniqued = _make_unique([translated[i] for i in order_idx], sep=".")
    out: list[str] = [""] * len(translated)
    for pos, i in enumerate(order_idx):
        out[i] = uniqued[pos]
    return out


def sanitize_keys(cols) -> list[str]:
    if isinstance(cols, str):
        cols = [cols]
    cols = list(cols)
    if len(cols) == 0:
        return []
    keys = _make_names_unique(cols)
    keys = [re.sub(r"\.+", "_", k) for k in keys]
    keys = [re.sub(r"_+$", "", k) for k in keys]
    keys = [k if k else f"col_{i + 1}" for i, k in enumerate(keys)]
    return _make_unique(keys, sep="_")


def make_unique_name(candidate, existing) -> str:
    candidate = str(candidate) if candidate is not None else ""
    if not candidate:
        candidate = "col"
    existing = set(str(e) for e in existing)
    if candidate not in existing:
        return candidate
    i = 2
    while True:
        cand = f"{candidate}_{i}"
        if cand not in existing:
            return cand
        i += 1


# ---------------------------------------------------------------------------
# numeric_strat_bins(values, opts) -> list of dicts (group_label,
# rule_expression, sort_order) for a continuous custom_group stratification,
# or None when the column cannot be meaningfully binned (constant/empty).
# rule_expression matches derive.parse_numeric_rule_clause().
# ---------------------------------------------------------------------------


def numeric_strat_bins(values, opts=None):
    opts = opts or {}
    max_value_groups = int(or_(opts.get("strat_max_value_groups"), 8))
    n_bins = max(2, int(or_(opts.get("strat_bins"), 3)))

    s = values if isinstance(values, pd.Series) else pd.Series(values)
    non_na = s.dropna().to_numpy(dtype=float)
    if non_na.size == 0:
        return None
    uniq = np.sort(np.unique(non_na))
    if uniq.size < 2:
        return None

    def build_from_cuts(cuts, labels):
        cuts = np.sort(np.unique(r_signif(np.asarray(cuts, dtype=float), 6)))
        if cuts.size == 0:
            return None
        n = int(cuts.size) + 1
        if labels is None or len(labels) != n:
            labels = [f"Group {i}" for i in range(1, n + 1)]
        out = []
        for i in range(1, n + 1):
            if i == 1:
                rule = f"<= {r_num_str(cuts[0])}"
            elif i == n:
                rule = f"> {r_num_str(cuts[n - 2])}"
            else:
                rule = f"> {r_num_str(cuts[i - 2])} & <= {r_num_str(cuts[i - 1])}"
            out.append(
                {"group_label": labels[i - 1], "rule_expression": rule, "sort_order": i}
            )
        return out

    if uniq.size <= max_value_groups:
        # one group per distinct value; cut at midpoints between consecutive values
        cuts = (uniq[:-1] + uniq[1:]) / 2
        return build_from_cuts(cuts, labels=r_format_common(uniq))
    probs = np.arange(1, n_bins) / n_bins
    cuts = np.quantile(non_na, probs)  # R type 7
    labels = (
        ["Low", "Medium", "High"]
        if n_bins == 3
        else [f"Group {i}" for i in range(1, n_bins + 1)]
    )
    return build_from_cuts(cuts, labels)


# ---------------------------------------------------------------------------
# Private ports of workbook-table helpers (R/00_input_workbook.R and
# app/modules/mod_data_overview.R) needed by build/reconcile below.
# ---------------------------------------------------------------------------

_WORKBOOK_SHEET_COLUMNS: dict[str, list[str] | None] = {
    "data": None,
    "metrics": [
        "metric_key", "display_name", "column_name", "units", "metric_family",
        "higher_is_better", "monotonic_linear", "preferred_transform",
        "min_sample_size", "best_subsets_allowed", "count_model",
        "stratification_mode", "include_in_summary", "missing_data_rule", "notes",
    ],
    "metric_predictors": ["metric_key", "predictor_key", "sort_order"],
    "metric_stratifications": ["metric_key", "strat_key", "sort_order"],
    "stratifications": [
        "strat_key", "display_name", "strat_type", "source_column",
        "source_data_type", "primary_strat_key", "secondary_strat_key",
        "derived_column_name", "levels", "pairwise_comparisons",
        "min_group_size", "notes",
    ],
    "strat_groups": [
        "strat_key", "group_label", "sort_order", "source_values", "rule_expression",
    ],
    "predictors": [
        "predictor_key", "display_name", "column_name", "type", "derived",
        "derivation_method", "source_columns", "constant", "expected_min",
        "expected_max", "missing_data_rule", "notes",
    ],
    "factor_recodes": [
        "recode_key", "source_column", "target_column", "target_level",
        "source_values", "notes",
    ],
    "site_masks": ["masked_sites", "site_label"],
    "site_mask_settings": ["site_label_column"],
    "function_mappings": [
        "discipline", "function_label", "metric_key", "metric_display_name", "notes",
    ],
}


def _ensure_workbook_sheet_columns(df, sheet_name: str) -> pd.DataFrame:
    if df is None:
        df = pd.DataFrame()
    df = pd.DataFrame(df).copy()
    df = trim_character_columns(df)

    desired = _WORKBOOK_SHEET_COLUMNS[sheet_name]
    if desired is None:
        return df

    for col_name in desired:
        if col_name not in df.columns:
            df[col_name] = pd.Series([None] * len(df), index=df.index, dtype=object)

    extra_cols = [c for c in df.columns if c not in desired]
    return df[[*desired, *extra_cols]]


def _normalize_workbook_tables(tables) -> dict:
    tables = tables or {}
    return {
        sheet_name: _ensure_workbook_sheet_columns(tables.get(sheet_name), sheet_name)
        for sheet_name in _WORKBOOK_SHEET_COLUMNS
    }


def _coerce_flag(value, default: bool = False) -> bool:
    """Port of coerce_flag() (R/00_input_workbook.R:208)."""
    if isinstance(value, (list, tuple, np.ndarray, pd.Series, pd.Index)):
        if len(value) == 0:
            return default
        value = list(value)[0]
    if value is None or is_na_scalar(value):
        return default
    raw = (as_character_scalar(value) or "").strip().lower()
    if not raw:
        return default
    if raw in ("true", "t", "1", "yes", "y"):
        return True
    if raw in ("false", "f", "0", "no", "n"):
        return False
    raise ValueError(f"Could not parse logical value '{value}'.")


def _role_flag_true(v) -> bool:
    try:
        return _coerce_flag(v) is True
    except Exception:
        return False


def _metadata_table_to_editor_df(df, sheet_name: str) -> pd.DataFrame:
    """All-character editor view of a sheet, NA -> "" (mod_data_overview.R:506)."""
    df = _ensure_workbook_sheet_columns(df, sheet_name)
    out = {}
    for col in df.columns:
        out[col] = [
            "" if (s := as_character_scalar(v)) is None else s for v in df[col]
        ]
    return pd.DataFrame(out, columns=list(df.columns))


def _collapse_pipe_text(values) -> str:
    vals = compact_chr(values)
    return "|".join(vals) if vals else ""


def _metric_link_text(link_df: pd.DataFrame, metric_key, value_col: str) -> str:
    if len(link_df) == 0:
        return ""
    rows = link_df[link_df["metric_key"] == metric_key]
    if len(rows) == 0:
        return ""
    return _collapse_pipe_text(rows[value_col])


def _metadata_editor_columns() -> list[str]:
    return [
        *_WORKBOOK_SHEET_COLUMNS["metrics"],
        "allowed_predictors",
        "allowed_stratifications",
    ]


def _build_metrics_editor_df(tables) -> pd.DataFrame:
    metrics_df = _metadata_table_to_editor_df(tables.get("metrics"), "metrics")
    metric_predictors_df = _metadata_table_to_editor_df(
        tables.get("metric_predictors"), "metric_predictors"
    )
    metric_stratifications_df = _metadata_table_to_editor_df(
        tables.get("metric_stratifications"), "metric_stratifications"
    )

    metrics_df["allowed_predictors"] = [
        _metric_link_text(metric_predictors_df, k, "predictor_key")
        for k in metrics_df["metric_key"]
    ]
    metrics_df["allowed_stratifications"] = [
        _metric_link_text(metric_stratifications_df, k, "strat_key")
        for k in metrics_df["metric_key"]
    ]

    for col_name in _metadata_editor_columns():
        if col_name not in metrics_df.columns:
            metrics_df[col_name] = ""

    return metrics_df[_metadata_editor_columns()]


def _reattach_extra_metric_columns(prior, metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Local twin of ``workbook_tables._reattach_extra_metric_columns``.

    Kept private here for the same reason the other workbook helpers are: this
    module deliberately carries its own copies rather than importing them.
    """
    if prior is None or len(prior) == 0 or "metric_key" not in getattr(prior, "columns", []):
        return metrics_df
    extras = [
        c for c in prior.columns
        if c not in metrics_df.columns and c not in _metadata_editor_columns()
    ]
    if not extras:
        return metrics_df

    def _txt(v) -> str:
        return "" if (s := as_character_scalar(v)) is None else s

    metrics_df = metrics_df.copy()
    keys = [_txt(k) for k in prior["metric_key"]]
    for col in extras:
        by_key = dict(zip(keys, (_txt(v) for v in prior[col])))
        metrics_df[col] = [by_key.get(_txt(mk), "") for mk in metrics_df["metric_key"]]
    return metrics_df


def _expanded_removed_strat_keys(stratifications_df, removed_keys) -> list[str]:
    """Cascade: paired strats whose primary/secondary is removed go too."""
    removed = compact_chr(removed_keys)
    if not removed:
        return []
    sdf = _metadata_table_to_editor_df(stratifications_df, "stratifications")
    while True:
        dependent = [
            sk
            for sk, st, pk, sk2 in zip(
                sdf["strat_key"],
                sdf["strat_type"],
                sdf["primary_strat_key"],
                sdf["secondary_strat_key"],
            )
            if sk in removed or (st == "paired" and (pk in removed or sk2 in removed))
        ]
        updated = list(dict.fromkeys([*removed, *compact_chr(dependent)]))
        if len(updated) == len(removed):
            return updated
        removed = updated


def _delete_rows_from_tables(tables, tab_key: str, selected_rows) -> dict:
    """Delete cascade (mod_data_overview.R:956) for the tabs reconcile uses.
    ``selected_rows`` are 0-based positions (R uses 1-based)."""
    selected = sorted({int(i) for i in selected_rows})
    if not selected:
        return tables
    tables = dict(tables)

    if tab_key == "metrics":
        editor_df = _build_metrics_editor_df(tables)
        removed_keys = compact_chr(editor_df["metric_key"].iloc[selected])
        keep = editor_df.drop(index=editor_df.index[selected])
        keep = keep[
            [c for c in keep.columns if c not in ("allowed_predictors", "allowed_stratifications")]
        ]
        # _build_metrics_editor_df projects to the editor column list, which omits
        # curve_form by design; carry it (and any other extra) back or unchecking
        # one metric column silently turns every two-sided metric monotone.
        keep = _reattach_extra_metric_columns(tables.get("metrics"), keep)
        tables["metrics"] = _ensure_workbook_sheet_columns(keep, "metrics")
        mp = _metadata_table_to_editor_df(tables.get("metric_predictors"), "metric_predictors")
        tables["metric_predictors"] = _ensure_workbook_sheet_columns(
            mp[~mp["metric_key"].isin(removed_keys)].reset_index(drop=True),
            "metric_predictors",
        )
        ms = _metadata_table_to_editor_df(
            tables.get("metric_stratifications"), "metric_stratifications"
        )
        tables["metric_stratifications"] = _ensure_workbook_sheet_columns(
            ms[~ms["metric_key"].isin(removed_keys)].reset_index(drop=True),
            "metric_stratifications",
        )
        return tables

    if tab_key == "stratifications":
        editor_df = _metadata_table_to_editor_df(tables.get("stratifications"), "stratifications")
        removed_keys = _expanded_removed_strat_keys(
            editor_df, editor_df["strat_key"].iloc[selected]
        )
        tables["stratifications"] = _ensure_workbook_sheet_columns(
            editor_df[~editor_df["strat_key"].isin(removed_keys)].reset_index(drop=True),
            "stratifications",
        )
        ms = _metadata_table_to_editor_df(
            tables.get("metric_stratifications"), "metric_stratifications"
        )
        tables["metric_stratifications"] = _ensure_workbook_sheet_columns(
            ms[~ms["strat_key"].isin(removed_keys)].reset_index(drop=True),
            "metric_stratifications",
        )
        sg = _metadata_table_to_editor_df(tables.get("strat_groups"), "strat_groups")
        tables["strat_groups"] = _ensure_workbook_sheet_columns(
            sg[~sg["strat_key"].isin(removed_keys)].reset_index(drop=True),
            "strat_groups",
        )
        return tables

    if tab_key == "predictors":
        editor_df = _metadata_table_to_editor_df(tables.get("predictors"), "predictors")
        removed_keys = compact_chr(editor_df["predictor_key"].iloc[selected])
        tables["predictors"] = _ensure_workbook_sheet_columns(
            editor_df.drop(index=editor_df.index[selected]).reset_index(drop=True),
            "predictors",
        )
        mp = _metadata_table_to_editor_df(tables.get("metric_predictors"), "metric_predictors")
        tables["metric_predictors"] = _ensure_workbook_sheet_columns(
            mp[~mp["predictor_key"].isin(removed_keys)].reset_index(drop=True),
            "metric_predictors",
        )
        return tables

    raise ValueError(f"Unsupported metadata tab: {tab_key}")


# ---------------------------------------------------------------------------
# build_config_tables_from_roles(data, assignments, opts) -> workbook `tables`
# ---------------------------------------------------------------------------


def _as_flag_series(x) -> pd.Series:
    """R as.logical() semantics with NA -> FALSE (as in the R as_flag helper):
    only TRUE/T/true/True (and FALSE variants) parse from strings."""

    def conv(v):
        if isinstance(v, (bool, np.bool_)):
            return bool(v)
        if v is None or is_na_scalar(v):
            return False
        if isinstance(v, (int, float, np.integer, np.floating)):
            return float(v) != 0
        return {
            "TRUE": True, "true": True, "T": True, "True": True,
            "FALSE": False, "false": False, "F": False, "False": False,
        }.get(str(v), False)

    if isinstance(x, pd.Series):
        return pd.Series([conv(v) for v in x], index=x.index, dtype=bool)
    return pd.Series([conv(v) for v in x], dtype=bool)


def _empty_sheet(cols, int_cols=(), num_cols=()) -> pd.DataFrame:
    d = {}
    for c in cols:
        if c in int_cols:
            d[c] = pd.Series([], dtype="int64")
        elif c in num_cols:
            d[c] = pd.Series([], dtype="float64")
        else:
            d[c] = pd.Series([], dtype=object)
    return pd.DataFrame(d)


def build_config_tables_from_roles(data, assignments, opts=None) -> dict:
    """Turn role assignments into workbook ``tables``.

    ``assignments``: DataFrame with ``column`` plus EITHER boolean role columns
    ``is_metric``/``is_predictor``/``is_stratifier`` OR a legacy single ``role``
    column. Optional ``family`` is consulted only for metric columns.
    """
    if not isinstance(data, pd.DataFrame) or not isinstance(assignments, pd.DataFrame):
        raise ValueError("is.data.frame(data), is.data.frame(assignments) are not all TRUE")

    opts = opts or {}
    assignments = assignments.copy()

    # Accept the multi-role format or a legacy single `role` column.
    if "is_metric" not in assignments.columns and "role" in assignments.columns:
        assignments["is_metric"] = assignments["role"] == "metric"
        assignments["is_predictor"] = assignments["role"] == "predictor"
        assignments["is_stratifier"] = assignments["role"] == "stratifier"
    for flag_col in ("is_metric", "is_predictor", "is_stratifier"):
        if flag_col not in assignments.columns:
            assignments[flag_col] = False
        assignments[flag_col] = _as_flag_series(assignments[flag_col])
    if "family" not in assignments.columns:
        assignments["family"] = None

    min_sample_size = int(or_(opts.get("min_sample_size"), 8))
    min_group_size = int(or_(opts.get("min_group_size"), 5))
    higher_is_better_default = or_(opts.get("higher_is_better_default"), "TRUE")

    metric_cols = assignments.loc[assignments["is_metric"], "column"].tolist()
    pred_cols = assignments.loc[assignments["is_predictor"], "column"].tolist()
    strat_cols = assignments.loc[assignments["is_stratifier"], "column"].tolist()

    metric_keys = dict(zip(metric_cols, sanitize_keys(metric_cols)))
    pred_keys = dict(zip(pred_cols, sanitize_keys(pred_cols)))

    def family_for(col):
        matches = assignments.loc[assignments["column"] == col, "family"]
        fam = matches.iloc[0] if len(matches) else None
        if fam is None or is_na_scalar(fam) or not str(fam):
            return suggest_metric_family(data[col], col)
        return str(fam)

    def is_num_col(col):
        return _is_numeric_series(data[col])

    # -- metrics sheet -------------------------------------------------------
    if metric_cols:
        fams = [family_for(c) for c in metric_cols]
        metrics = pd.DataFrame(
            {
                "metric_key": [metric_keys[c] for c in metric_cols],
                # a bare column name is a code, not a name: resolve it against
                # the metric dictionary and keep the column name as the fallback
                "display_name": [
                    metric_names.display_name_for(metric_keys[c], c) for c in metric_cols
                ],
                "column_name": metric_cols,
                "units": [metric_names.units_for(metric_keys[c], "") or "" for c in metric_cols],
                "metric_family": fams,
                "higher_is_better": [higher_is_better_default] * len(metric_cols),
                "monotonic_linear": ["TRUE"] * len(metric_cols),
                "preferred_transform": ["none"] * len(metric_cols),
                "min_sample_size": [min_sample_size] * len(metric_cols),
                "best_subsets_allowed": ["TRUE"] * len(metric_cols),
                "count_model": ["TRUE" if f == "count" else "FALSE" for f in fams],
                "stratification_mode": ["covariate"] * len(metric_cols),
                "include_in_summary": ["TRUE"] * len(metric_cols),
                "missing_data_rule": [""] * len(metric_cols),
                "notes": [""] * len(metric_cols),
            }
        )
    else:
        metrics = _empty_sheet(
            _WORKBOOK_SHEET_COLUMNS["metrics"], int_cols=("min_sample_size",)
        )

    # -- predictors sheet ----------------------------------------------------
    if pred_cols:
        predictors = pd.DataFrame(
            {
                "predictor_key": [pred_keys[c] for c in pred_cols],
                "display_name": pred_cols,
                "column_name": pred_cols,
                "type": [
                    "continuous" if is_num_col(c) else "categorical" for c in pred_cols
                ],
                "derived": ["FALSE"] * len(pred_cols),
                "derivation_method": [""] * len(pred_cols),
                "source_columns": [""] * len(pred_cols),
                "constant": [np.nan] * len(pred_cols),
                "expected_min": [np.nan] * len(pred_cols),
                "expected_max": [np.nan] * len(pred_cols),
                "missing_data_rule": [""] * len(pred_cols),
                "notes": [""] * len(pred_cols),
            }
        )
    else:
        predictors = _empty_sheet(
            _WORKBOOK_SHEET_COLUMNS["predictors"],
            num_cols=("constant", "expected_min", "expected_max"),
        )

    # -- stratifications + strat_groups (routed by source type) ---------------
    strat_rows: list[dict] = []
    group_rows: list[dict] = []
    strat_key_for: dict = {}
    strat_keys_used: list[str] = []
    derived_names_used: list[str] = [str(c) for c in data.columns]

    for col in strat_cols:
        base_key = sanitize_keys([col])[0]
        if is_num_col(col):
            bins = numeric_strat_bins(data[col], opts)
            if bins is None:
                continue  # cannot bin (constant) -> skip strat
            derived = make_unique_name(f"{base_key}_grp", derived_names_used)
            derived_names_used.append(derived)
            sk = make_unique_name(derived, strat_keys_used)
            strat_keys_used.append(sk)
            strat_key_for[col] = sk
            strat_rows.append(
                {
                    "strat_key": sk,
                    "display_name": f"{col} (grouped)",
                    "strat_type": "custom_group",
                    "source_column": col,
                    "source_data_type": "continuous",
                    "primary_strat_key": "",
                    "secondary_strat_key": "",
                    "derived_column_name": derived,
                    "levels": "",
                    "pairwise_comparisons": "",
                    "min_group_size": min_group_size,
                    "notes": "",
                }
            )
            for b in bins:
                group_rows.append(
                    {
                        "strat_key": sk,
                        "group_label": b["group_label"],
                        "sort_order": int(b["sort_order"]),
                        "source_values": "",
                        "rule_expression": b["rule_expression"],
                    }
                )
        else:
            sk = make_unique_name(base_key, strat_keys_used)
            strat_keys_used.append(sk)
            strat_key_for[col] = sk
            strat_rows.append(
                {
                    "strat_key": sk,
                    "display_name": col,
                    "strat_type": "raw_single",
                    "source_column": col,
                    "source_data_type": "categorical",
                    "primary_strat_key": "",
                    "secondary_strat_key": "",
                    "derived_column_name": "",
                    "levels": "",
                    "pairwise_comparisons": "",
                    "min_group_size": min_group_size,
                    "notes": "",
                }
            )

    stratifications = (
        pd.DataFrame(strat_rows, columns=_WORKBOOK_SHEET_COLUMNS["stratifications"])
        if strat_rows
        else _empty_sheet(
            _WORKBOOK_SHEET_COLUMNS["stratifications"], int_cols=("min_group_size",)
        )
    )
    strat_groups = (
        pd.DataFrame(group_rows, columns=_WORKBOOK_SHEET_COLUMNS["strat_groups"])
        if group_rows
        else _empty_sheet(
            _WORKBOOK_SHEET_COLUMNS["strat_groups"], int_cols=("sort_order",)
        )
    )

    # -- link sheets (allow-all; exclude predicting a metric with its own col) --
    if metric_cols and pred_cols:
        # expand.grid: first factor (metric) varies fastest
        combos = [(m, p) for p in pred_cols for m in metric_cols if m != p]
        if combos:
            metric_predictors = pd.DataFrame(
                {
                    "metric_key": [metric_keys[m] for m, _ in combos],
                    "predictor_key": [pred_keys[p] for _, p in combos],
                    "sort_order": list(range(1, len(combos) + 1)),
                }
            )
        else:
            metric_predictors = _empty_sheet(
                ("metric_key", "predictor_key", "sort_order"), int_cols=("sort_order",)
            )
    else:
        metric_predictors = _empty_sheet(
            ("metric_key", "predictor_key", "sort_order"), int_cols=("sort_order",)
        )

    strat_key_vec = list(strat_key_for.values())
    if metric_cols and strat_key_vec:
        grid = [
            (metric_keys[m], sk) for sk in strat_key_vec for m in metric_cols
        ]
        metric_stratifications = pd.DataFrame(
            {
                "metric_key": [mk for mk, _ in grid],
                "strat_key": [sk for _, sk in grid],
                "sort_order": list(range(1, len(grid) + 1)),
            }
        )
    else:
        metric_stratifications = _empty_sheet(
            ("metric_key", "strat_key", "sort_order"), int_cols=("sort_order",)
        )

    return {
        "data": data.copy(),
        "metrics": metrics,
        "metric_predictors": metric_predictors,
        "metric_stratifications": metric_stratifications,
        "stratifications": stratifications,
        "strat_groups": strat_groups,
        "predictors": predictors,
        "factor_recodes": _empty_sheet(
            (
                "recode_key", "source_column", "target_column", "target_level",
                "source_values", "notes",
            )
        ),
    }


# ---------------------------------------------------------------------------
# Role membership + reconcile (loaded-project "Choose columns" checkbox editor)
# ---------------------------------------------------------------------------


def current_role_membership(tables) -> pd.DataFrame:
    """Which RAW data columns are currently used as metric / predictor /
    stratifier. Derived predictors and custom_group / paired stratifications
    are NOT raw-column governed and are excluded here."""
    tables = tables or {}
    data = tables.get("data")
    cols = list(data.columns) if isinstance(data, pd.DataFrame) else []
    metrics_df = _ensure_workbook_sheet_columns(tables.get("metrics"), "metrics")
    preds_df = _ensure_workbook_sheet_columns(tables.get("predictors"), "predictors")
    strat_df = _ensure_workbook_sheet_columns(tables.get("stratifications"), "stratifications")

    metric_cols = compact_chr(metrics_df["column_name"])
    derived = (
        [_role_flag_true(v) for v in preds_df["derived"]] if len(preds_df) else []
    )
    pred_cols = compact_chr(
        [cn for cn, d in zip(preds_df["column_name"], derived) if not d]
    )
    strat_cols = compact_chr(
        [
            sc
            for sc, st in zip(strat_df["source_column"], strat_df["strat_type"])
            if as_character_scalar(st) == "raw_single"
        ]
    )

    return pd.DataFrame(
        {
            "column": cols,
            "is_metric": [c in metric_cols for c in cols],
            "is_predictor": [c in pred_cols for c in cols],
            "is_stratifier": [c in strat_cols for c in cols],
        }
    )


def _to_character_df(df: pd.DataFrame) -> pd.DataFrame:
    """df[] <- lapply(df, as.character) — NA stays NA (None)."""
    out = {}
    for col in df.columns:
        out[col] = [as_character_scalar(v) for v in df[col]]
    return pd.DataFrame(out, columns=list(df.columns))


def _rbind_sheet_rows(a, b, sheet: str) -> pd.DataFrame:
    """Character-coerce + rbind two config sheets on a common column set."""
    a = _ensure_workbook_sheet_columns(a, sheet)
    b = _ensure_workbook_sheet_columns(b, sheet)
    if len(b) == 0:
        return a
    a = _to_character_df(a)
    b = _to_character_df(b)
    cols = list(a.columns) + [c for c in b.columns if c not in a.columns]
    for cc in cols:
        if cc not in a.columns:
            a[cc] = pd.Series([None] * len(a), dtype=object)
        if cc not in b.columns:
            b[cc] = pd.Series([None] * len(b), dtype=object)
    if len(a) == 0:
        return b[cols]
    return pd.concat([a[cols], b[cols]], ignore_index=True)


def _dedupe_keys_against(keys, existing) -> list[str]:
    """Rename each key so it does not collide with `existing`, in order."""
    out: list[str] = []
    seen = [str(e) for e in existing]
    for k in keys:
        nk = make_unique_name(str(k), seen)
        seen.append(nk)
        out.append(nk)
    return out


def _rekey_fresh_tables(fresh: dict, tables: dict) -> dict:
    """Rekey freshly-minted rows so keys don't collide with existing tables;
    propagate strat_key renames into strat_groups."""
    ex_m = compact_chr(
        _ensure_workbook_sheet_columns(tables.get("metrics"), "metrics")["metric_key"]
    )
    ex_p = compact_chr(
        _ensure_workbook_sheet_columns(tables.get("predictors"), "predictors")["predictor_key"]
    )
    ex_s = compact_chr(
        _ensure_workbook_sheet_columns(tables.get("stratifications"), "stratifications")["strat_key"]
    )

    if len(fresh["metrics"]):
        fresh["metrics"]["metric_key"] = _dedupe_keys_against(
            fresh["metrics"]["metric_key"], ex_m
        )
    if len(fresh["predictors"]):
        fresh["predictors"]["predictor_key"] = _dedupe_keys_against(
            fresh["predictors"]["predictor_key"], ex_p
        )
    if len(fresh["stratifications"]):
        old_sk = [str(v) for v in fresh["stratifications"]["strat_key"]]
        new_sk = _dedupe_keys_against(old_sk, ex_s)
        fresh["stratifications"]["strat_key"] = new_sk
        if fresh.get("strat_groups") is not None and len(fresh["strat_groups"]):
            m = dict(zip(old_sk, new_sk))
            fresh["strat_groups"]["strat_key"] = [
                m.get(str(sg), str(sg)) for sg in fresh["strat_groups"]["strat_key"]
            ]
    return fresh


def _append_link_rows(existing, pairs, sheet: str) -> pd.DataFrame:
    """Append allow-all link rows (deduped) with a continuing per-metric
    sort_order (recomputed across ALL rows, like the R stats::ave call)."""
    existing = _ensure_workbook_sheet_columns(existing, sheet)
    if pairs is None or len(pairs) == 0:
        return existing
    kc = list(pairs.columns)  # ("metric_key", "predictor_key"|"strat_key")
    pairs = _to_character_df(pairs)
    ex_key = (
        [f"{a}\r{b}" for a, b in zip(existing[kc[0]], existing[kc[1]])]
        if len(existing)
        else []
    )
    pk = [f"{a}\r{b}" for a, b in zip(pairs[kc[0]], pairs[kc[1]])]
    keep = []
    seen: set = set()
    for key in pk:
        keep.append(key not in seen and key not in ex_key)
        seen.add(key)
    pairs = pairs[np.array(keep, dtype=bool)]
    if not len(pairs):
        return existing

    k1 = [as_character_scalar(v) for v in existing[kc[0]]] + list(pairs[kc[0]])
    k2 = [as_character_scalar(v) for v in existing[kc[1]]] + list(pairs[kc[1]])
    counter: dict = {}
    sort_order = []
    for key in k1:
        counter[key] = counter.get(key, 0) + 1
        sort_order.append(counter[key])
    combined = pd.DataFrame({kc[0]: k1, kc[1]: k2, "sort_order": sort_order})
    return _ensure_workbook_sheet_columns(combined, sheet)


def _grid_or_none(a, b, names):
    """expand.grid(a, b): first factor varies fastest; None when either empty."""
    if not len(a) or not len(b):
        return None
    rows = [(x, y) for y in b for x in a]
    return pd.DataFrame({names[0]: [r[0] for r in rows], names[1]: [r[1] for r in rows]})


def _unique_rows(df):
    if df is None:
        return None
    return df.drop_duplicates().reset_index(drop=True)


def _add_allow_all_links(tables: dict, fresh: dict) -> dict:
    """Allow-all links between new entities and every existing counterpart."""
    metrics_df = _ensure_workbook_sheet_columns(tables.get("metrics"), "metrics")
    preds_df = _ensure_workbook_sheet_columns(tables.get("predictors"), "predictors")
    strat_df = _ensure_workbook_sheet_columns(tables.get("stratifications"), "stratifications")

    all_m = compact_chr(metrics_df["metric_key"])
    new_m = compact_chr(fresh["metrics"]["metric_key"])
    all_p = compact_chr(preds_df["predictor_key"])
    new_p = compact_chr(fresh["predictors"]["predictor_key"])
    all_s = compact_chr(strat_df["strat_key"])
    new_s = compact_chr(fresh["stratifications"]["strat_key"])

    mcol = {
        as_character_scalar(k): as_character_scalar(c)
        for k, c in zip(metrics_df["metric_key"], metrics_df["column_name"])
    }
    pcol = {
        as_character_scalar(k): as_character_scalar(c)
        for k, c in zip(preds_df["predictor_key"], preds_df["column_name"])
    }

    grids = [
        _grid_or_none(new_m, all_p, ("metric_key", "predictor_key")),
        _grid_or_none(all_m, new_p, ("metric_key", "predictor_key")),
    ]
    grids = [g for g in grids if g is not None]
    mp = _unique_rows(pd.concat(grids, ignore_index=True)) if grids else None
    if mp is not None and len(mp):
        # exclude predicting a metric with its own column
        keep = []
        for _, row in mp.iterrows():
            cm = or_(mcol.get(row["metric_key"]), "") or ""
            cp = or_(pcol.get(row["predictor_key"]), "") or ""
            keep.append(not (bool(cm) and cm == cp))
        mp = mp[np.array(keep, dtype=bool)]
    tables["metric_predictors"] = _append_link_rows(
        tables.get("metric_predictors"), mp, "metric_predictors"
    )

    grids = [
        _grid_or_none(new_m, all_s, ("metric_key", "strat_key")),
        _grid_or_none(all_m, new_s, ("metric_key", "strat_key")),
    ]
    grids = [g for g in grids if g is not None]
    ms = _unique_rows(pd.concat(grids, ignore_index=True)) if grids else None
    tables["metric_stratifications"] = _append_link_rows(
        tables.get("metric_stratifications"), ms, "metric_stratifications"
    )
    return tables


def reconcile_role_membership(tables, assignments, opts=None) -> dict:
    """Reconcile workbook ``tables`` so raw-column role membership matches
    ``assignments`` (DataFrame(column, is_metric, is_predictor, is_stratifier,
    family)). Keeps every unchanged row untouched: drops rows for unchecked
    columns (delete cascade), adds default rows + allow-all links for
    newly-checked columns, preserves derived predictors, custom_group / paired
    stratifications, factor recodes, site masks and function mappings."""
    opts = opts or {}
    tables = _normalize_workbook_tables(tables)
    data = tables["data"]

    want_metric = compact_chr(
        assignments.loc[_as_flag_series(assignments["is_metric"]), "column"]
    )
    want_pred = compact_chr(
        assignments.loc[_as_flag_series(assignments["is_predictor"]), "column"]
    )
    want_strat = compact_chr(
        assignments.loc[_as_flag_series(assignments["is_stratifier"]), "column"]
    )

    memb = current_role_membership(tables)
    have_metric = memb.loc[memb["is_metric"], "column"].tolist()
    have_pred = memb.loc[memb["is_predictor"], "column"].tolist()
    have_strat = memb.loc[memb["is_stratifier"], "column"].tolist()

    # -- DROP unchecked columns (delete cascade) --
    drop_metric = [c for c in have_metric if c not in want_metric]
    if drop_metric:
        mdf = _build_metrics_editor_df(tables)
        pos = [i for i, cn in enumerate(mdf["column_name"]) if cn in drop_metric]
        if pos:
            tables = _delete_rows_from_tables(tables, "metrics", pos)
    drop_pred = [c for c in have_pred if c not in want_pred]
    if drop_pred:
        pdf = _metadata_table_to_editor_df(tables.get("predictors"), "predictors")
        dflag = [_role_flag_true(v) for v in pdf["derived"]] if len(pdf) else []
        pos = [
            i
            for i, (cn, d) in enumerate(zip(pdf["column_name"], dflag))
            if cn in drop_pred and not d
        ]
        if pos:
            tables = _delete_rows_from_tables(tables, "predictors", pos)
    drop_strat = [c for c in have_strat if c not in want_strat]
    if drop_strat:
        sdf = _metadata_table_to_editor_df(tables.get("stratifications"), "stratifications")
        pos = [
            i
            for i, (st, sc) in enumerate(zip(sdf["strat_type"], sdf["source_column"]))
            if st == "raw_single" and sc in drop_strat
        ]
        if pos:
            tables = _delete_rows_from_tables(tables, "stratifications", pos)

    # -- ADD newly-checked columns (default rows + allow-all links) --
    add_metric = [c for c in want_metric if c not in have_metric]
    add_pred = [c for c in want_pred if c not in have_pred]
    add_strat = [c for c in want_strat if c not in have_strat]
    added_cols = list(dict.fromkeys([*add_metric, *add_pred, *add_strat]))
    if added_cols:

        def fam_of(col):
            matches = assignments.loc[assignments["column"] == col, "family"]
            f = matches.iloc[0] if len(matches) == 1 else (
                matches.iloc[0] if len(matches) else None
            )
            if f is None or is_na_scalar(f) or not str(f):
                return None
            return str(f)

        add_assign = pd.DataFrame(
            {
                "column": added_cols,
                "is_metric": [c in add_metric for c in added_cols],
                "is_predictor": [c in add_pred for c in added_cols],
                "is_stratifier": [c in add_strat for c in added_cols],
                "family": [fam_of(c) for c in added_cols],
            }
        )
        fresh = build_config_tables_from_roles(data, add_assign, opts)
        fresh = _rekey_fresh_tables(fresh, tables)
        tables["metrics"] = _rbind_sheet_rows(tables["metrics"], fresh["metrics"], "metrics")
        tables["predictors"] = _rbind_sheet_rows(
            tables["predictors"], fresh["predictors"], "predictors"
        )
        tables["stratifications"] = _rbind_sheet_rows(
            tables["stratifications"], fresh["stratifications"], "stratifications"
        )
        tables["strat_groups"] = _rbind_sheet_rows(
            tables["strat_groups"], fresh["strat_groups"], "strat_groups"
        )
        tables = _add_allow_all_links(tables, fresh)

    return _normalize_workbook_tables(tables)


# ---------------------------------------------------------------------------
# reconcile_tables_with_new_data(...) -> delta-safe wizard rebuild
# ---------------------------------------------------------------------------


def _cell_str(v) -> str:
    """Editor-df cell to a stripped string ('' for blank/NA)."""
    s = as_character_scalar(v)
    return s.strip() if s else ""


def _producible_columns(tables, data_cols: set) -> set:
    """Columns ``derive_variables`` will create over ``data_cols``.

    Honors derive's sequential semantics: derived predictors run first in sheet
    order (a later one may source an earlier one's output), factor recodes next
    (sourced on raw columns), custom groups last (may source raw columns,
    derived outputs, or live recode targets). Dead rows contribute nothing, so a
    metric or stratification pointed at a dead recode's target is correctly
    seen as unproducible and pruned.
    """
    out: set = set()
    pdf = _metadata_table_to_editor_df(tables.get("predictors"), "predictors")
    for _, r in pdf.iterrows():
        if not _role_flag_true(r.get("derived")):
            continue
        srcs = [s.strip() for s in _cell_str(r.get("source_columns")).split(",") if s.strip()]
        cn = _cell_str(r.get("column_name"))
        if cn and srcs and all(s in data_cols or s in out for s in srcs):
            out.add(cn)
    rdf = _metadata_table_to_editor_df(tables.get("factor_recodes"), "factor_recodes")
    for src, tgt in zip(rdf["source_column"], rdf["target_column"]):
        if _cell_str(src) in data_cols and _cell_str(tgt):
            out.add(_cell_str(tgt))
    sdf = _metadata_table_to_editor_df(tables.get("stratifications"), "stratifications")
    for _, r in sdf.iterrows():
        if _cell_str(r.get("strat_type")) != "custom_group":
            continue
        src = _cell_str(r.get("source_column"))
        dcn = _cell_str(r.get("derived_column_name"))
        if dcn and src and (src in data_cols or src in out):
            out.add(dcn)
    return out


def _site_mask_sheets_for_new_data(tables, new_data: pd.DataFrame,
                                   site_mask_config) -> dict:
    """Replacement site_masks/site_mask_settings sheets for a swapped-in frame.

    Mask ids are 1-based row positions into the data sheet, so the preserved
    sheet is never transplantable across a frame swap: after a re-compile the
    same position names a different site (or none). The screening-derived
    config from the compile step IS valid against the new frame by
    construction, so it wins; with no fresh config the masks reset to empty
    (they are already baked into the compiled frame) and only the label-column
    choice is carried, when that column still exists.
    """
    from . import workbook as wb

    if site_mask_config:
        cfg = dict(site_mask_config)
        label = _cell_str(cfg.get("site_label_column"))
        if not label or label not in new_data.columns:
            cfg["site_label_column"] = wb.default_site_label_source_column(new_data)
        n = len(new_data)
        cfg["masked_site_ids"] = [
            int(i) for i in (cfg.get("masked_site_ids") or []) if 1 <= int(i) <= n
        ]
        return wb.site_mask_tables_from_config(new_data, cfg)

    settings_tbl = None
    settings = _metadata_table_to_editor_df(
        tables.get("site_mask_settings"), "site_mask_settings"
    )
    if len(settings):
        cand = _cell_str(settings["site_label_column"].iloc[0])
        if cand and cand in new_data.columns:
            settings_tbl = pd.DataFrame({"site_label_column": [cand]})
    return {
        "site_masks": _ensure_workbook_sheet_columns(None, "site_masks"),
        "site_mask_settings": _ensure_workbook_sheet_columns(
            settings_tbl, "site_mask_settings"
        ),
    }


def reconcile_tables_with_new_data(tables, new_data, assignments,
                                   site_mask_config=None, opts=None) -> dict:
    """Reconcile existing workbook ``tables`` onto a freshly compiled data frame.

    The wizard-rebuild twin of :func:`reconcile_role_membership`: that one
    reconciles role checkboxes over an unchanged frame (the workbook grid's
    Apply); this one first swaps ``new_data`` in as the data sheet, prunes rows
    whose raw reference no longer exists anywhere in it, then delegates the
    role deltas to :func:`reconcile_role_membership` -- so a Build over a
    loaded project preserves everything the grid preserves (derived predictors,
    factor recodes, custom_group / paired stratifications, curated
    metric-predictor / metric-stratification links, function mappings) instead
    of regenerating every sheet from scratch.

    Pruning details: derived predictor rows are never pruned (derive skips a
    dormant one and it revives when its sources return); factor recodes are
    kept even when dead for the same reason; custom_group stratifications with
    a vanished source MUST be pruned because ``derive_variables`` raises on
    them. ``site_mask_config`` is the screening-derived config computed against
    ``new_data`` (or None when the frame was not re-compiled this session); see
    :func:`_site_mask_sheets_for_new_data` for why the preserved mask sheet
    never survives a frame swap.
    """
    tables = _normalize_workbook_tables(tables)
    new_data = pd.DataFrame(new_data).copy()
    tables["data"] = new_data
    cols = {str(c) for c in new_data.columns}
    present = cols | _producible_columns(tables, cols)

    # -- prune rows whose raw reference vanished (each editor df is rebuilt
    #    after the previous delete so positions stay aligned) --
    mdf = _build_metrics_editor_df(tables)
    pos = [
        i for i, cn in enumerate(mdf["column_name"])
        if _cell_str(cn) and _cell_str(cn) not in present
    ]
    if pos:
        tables = _delete_rows_from_tables(tables, "metrics", pos)
    pdf = _metadata_table_to_editor_df(tables.get("predictors"), "predictors")
    pos = [
        i for i, (cn, d) in enumerate(zip(pdf["column_name"], pdf["derived"]))
        if not _role_flag_true(d) and _cell_str(cn) and _cell_str(cn) not in present
    ]
    if pos:
        tables = _delete_rows_from_tables(tables, "predictors", pos)
    sdf = _metadata_table_to_editor_df(tables.get("stratifications"), "stratifications")
    pos = [
        i for i, (st, sc) in enumerate(zip(sdf["strat_type"], sdf["source_column"]))
        if _cell_str(st) in ("raw_single", "custom_group")
        and _cell_str(sc) and _cell_str(sc) not in present
    ]
    if pos:
        tables = _delete_rows_from_tables(tables, "stratifications", pos)

    # -- role deltas over the new frame --
    asg = pd.DataFrame(assignments).copy()
    if "family" not in asg.columns:
        # The hydration shape (current_role_membership) has no family column;
        # reconcile's add path indexes it unconditionally.
        asg["family"] = None
    asg = asg.loc[[str(c) in cols for c in asg["column"]]].reset_index(drop=True)
    tables = reconcile_role_membership(tables, asg, opts)

    tables.update(_site_mask_sheets_for_new_data(tables, new_data, site_mask_config))
    return _normalize_workbook_tables(tables)


# ---------------------------------------------------------------------------
# parse_pasted_table(text, has_header) -> DataFrame from clipboard TSV/CSV
# ---------------------------------------------------------------------------


def parse_pasted_table(text, has_header: bool = True):
    """Parse clipboard text: tab-separated when the first line contains a tab,
    otherwise comma-separated. Mirrors R read.table(..., na.strings = c("",
    "NA"), fill = TRUE) + type.convert. Returns None when unparseable.

    NOTE(parity): like read.table, a body row with exactly one more field than
    the header turns the first field into row names (pandas index)."""
    if text is None or not str(text).strip():
        return None
    text = str(text)

    first_line = re.split(r"\r?\n", text)[0]
    sep = "\t" if "\t" in first_line else ","

    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=sep,
            header=0 if has_header else None,
            quotechar='"',
            na_values=["", "NA"],
            keep_default_na=False,
            skip_blank_lines=True,
        )
    except Exception:
        return None
    if df is None or df.shape[1] == 0:
        return None

    if not has_header:
        df.columns = [f"V{i + 1}" for i in range(df.shape[1])]
    return df
