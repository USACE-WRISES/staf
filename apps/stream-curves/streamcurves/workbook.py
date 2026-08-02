"""Port of R/00_input_workbook.R (workbook input & metadata parsing) and
R/01_load_data.R (``load_data`` thin wrapper).

Reads the StreamCurves workbook format (.xlsx) and rebuilds the runtime
configs. The public entry points are :func:`read_input_workbook` /
:func:`load_data` (path -> InputBundle dict), :func:`build_input_bundle_from_tables`
(tables dict -> InputBundle dict), :func:`normalize_workbook_tables` and
:func:`write_input_workbook`.

InputBundle keys (mirrors the R named list, R/00_input_workbook.R:1013-1022):
``raw_data`` (DataFrame), ``metric_config`` / ``strat_config`` /
``predictor_config`` / ``factor_recode_config`` (dicts keyed like the R named
lists), ``site_mask_config`` (dict), ``discipline_function_mapping``
(DataFrame[metric_key, discipline, function_label, sort_order] or None),
``mapping_covers_all_metrics`` (bool — R stores this as
``attr(mapping, "covers_all_metrics")``; here it is also mirrored on
``mapping.attrs["covers_all_metrics"]``), and ``metadata`` (dict of the 11
normalized sheet DataFrames).

Excel-reading parity notes:
- R uses ``readxl::read_excel`` which guesses column types from the first
  1000 rows (``guess_max``) and coerces every cell of a column to that type;
  ``pandas.read_excel(engine="openpyxl")`` infers from all rows and keeps
  mixed ``object`` columns. All cell-level helpers therefore go through
  :func:`_as_character` (an R ``as.character()`` replica, e.g. ``1.0`` ->
  ``"1"``) and coercers (:func:`coerce_flag`, :func:`coerce_optional_numeric`)
  so numeric-typed flag/key cells parse identically, and configs that demand
  numbers apply post-hoc numeric coercion regardless of the guessed dtype.
- readxl sizes a sheet by cells that contain *values*, while openpyxl also
  reports formatted-but-empty cells; :func:`read_required_sheet` drops
  phantom trailing all-NA rows and unnamed all-NA columns to match.
- R character sorts (``sort``, ``dplyr::arrange``) use locale collation;
  Python sorts by codepoint. # NOTE(parity): differs only for mixed-case /
  non-ASCII level names.
"""

from __future__ import annotations

import logging
import math
import re
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook

from .curves import CURVE_FORM_OPTIMUM

logger = logging.getLogger("streamcurves")

# R/00_input_workbook.R:15-16
SITE_ID_COL = "..streamcurves_site_id"
SITE_LABEL_COL = "..streamcurves_site_label"
# Verbatim R names kept as aliases.
streamcurves_site_id_column = SITE_ID_COL
streamcurves_site_label_column = SITE_LABEL_COL

# R/13_oh_parameter_map.R:130-132. build_function_mappings_from_tables() uses
# tryCatch(fixed_discipline_order(), error = <this same vector>) so the
# workbook module works standalone; the fallback and the real function return
# identical values, so we keep a local copy instead of importing the (possibly
# not yet ported) oh_parameter_map module.
_FIXED_DISCIPLINE_ORDER = (
    "Hydrology",
    "Hydraulics",
    "Geomorphology",
    "Physicochemistry",
    "Biology",
)


def _fixed_discipline_order() -> list[str]:
    try:  # prefer the rightful owner module (port of R/13_oh_parameter_map.R)
        from .mapping import fixed_discipline_order  # type: ignore

        return list(fixed_discipline_order())
    except Exception:
        return list(_FIXED_DISCIPLINE_ORDER)


def _or(x, default):
    """R ``%||%`` — default only when x is None (NULL), not merely falsy."""
    return x if x is not None else default


# --------------------------------------------------------------------------- #
# Sheet specs (R/00_input_workbook.R:18-106)
# --------------------------------------------------------------------------- #


def required_workbook_sheets() -> list[str]:
    return [
        "data",
        "metrics",
        "metric_predictors",
        "metric_stratifications",
        "stratifications",
        "strat_groups",
        "predictors",
        "factor_recodes",
    ]


def optional_workbook_sheets() -> list[str]:
    return ["site_masks", "site_mask_settings", "function_mappings"]


def workbook_sheet_specs() -> dict[str, dict]:
    return {
        "data": {"required": []},
        "metrics": {
            "required": [
                "metric_key", "display_name", "column_name", "metric_family",
                "higher_is_better", "monotonic_linear", "preferred_transform",
                "min_sample_size", "best_subsets_allowed", "count_model",
                "stratification_mode", "include_in_summary",
            ]
        },
        "metric_predictors": {"required": ["metric_key", "predictor_key"]},
        "metric_stratifications": {"required": ["metric_key", "strat_key"]},
        "stratifications": {
            "required": ["strat_key", "display_name", "strat_type", "min_group_size"]
        },
        "strat_groups": {"required": ["strat_key", "group_label"]},
        "predictors": {
            "required": ["predictor_key", "display_name", "column_name", "type", "derived"]
        },
        "factor_recodes": {
            "required": [
                "recode_key", "source_column", "target_column", "target_level", "source_values",
            ]
        },
        "site_masks": {"required": ["masked_sites", "site_label"]},
        "site_mask_settings": {"required": ["site_label_column"]},
        "function_mappings": {"required": ["discipline", "function_label", "metric_key"]},
    }


def workbook_sheet_columns() -> dict[str, list[str] | None]:
    return {
        "data": None,
        # ``curve_form`` is deliberately NOT listed here. It carries the two-sided
        # ("optimum") form, whose direction is a deliberate null -- without
        # somewhere to record it, higher_is_better reads back as TRUE and a
        # two-sided curve silently becomes monotone-increasing. It rides as an
        # extra column (ensure_workbook_sheet_columns appends extras) so a
        # pre-existing workbook does not gain an all-NA column it never had,
        # which would not survive an xlsx write/read round trip.
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
        # function_mappings sheet layout (user-maintained source of truth):
        #   discipline          — one of the 5 fixed disciplines (required)
        #   function_label      — user-defined function name (required)
        #   metric_key          — metric identifier, blank for empty-bucket rows
        #   metric_display_name — informational helper (ignored by reader)
        #   notes               — free-form user notes (optional)
        # Order within a discipline is first-appearance order in the sheet;
        # order of metrics within a function is row order. The reader derives
        # sort_order internally — the user does not maintain it in Excel.
        "function_mappings": [
            "discipline", "function_label", "metric_key", "metric_display_name", "notes",
        ],
    }


# --------------------------------------------------------------------------- #
# Low-level cell/vector helpers
# --------------------------------------------------------------------------- #


def _is_na(v) -> bool:
    """Scalar NA test covering None / float nan / np.nan / pd.NA / NaT."""
    if v is None:
        return True
    try:
        result = pd.isna(v)
    except (TypeError, ValueError):
        return False
    if isinstance(result, (np.ndarray, pd.Series, pd.DataFrame)):
        return False
    return bool(result)


def _as_character(v) -> str | None:
    """Replica of R ``as.character()`` for scalar cells (None for NA).

    Load-bearing for numeric-typed Excel cells: R prints whole doubles
    without the decimal (``as.character(1)`` -> ``"1"``), and uses up to 15
    significant digits (``%.15g``) otherwise; logicals become "TRUE"/"FALSE".
    """
    if _is_na(v):
        return None
    if isinstance(v, (bool, np.bool_)):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, np.integer)):
        return "%d" % int(v)
    if isinstance(v, (float, np.floating)):
        return "%.15g" % float(v)
    return str(v)


def _values(x) -> list:
    """Treat x as an R vector: Series/array/list -> list, scalar -> [scalar]."""
    if x is None:
        return []
    if isinstance(x, (pd.Series, pd.Index, np.ndarray)):
        return list(x)
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


def _first(vals: list):
    return vals[0] if vals else None


def compact_chr(x) -> list[str]:
    """R/00_input_workbook.R:149-153 — as.character + trim + drop NA/blank."""
    out: list[str] = []
    for v in _values(x):
        s = _as_character(v)
        if s is None:
            continue
        s = s.strip()
        if s:
            out.append(s)
    return out


def scalar_text(value, default: str | None = None) -> str | None:
    """R/00_input_workbook.R:155-161 (default NA_character_ -> None)."""
    vals = compact_chr(value)
    if not vals:
        return default
    return vals[0]


def scalar_number(value, default: float = float("nan")) -> float:
    """R/00_input_workbook.R:163-169 (default NA_real_ -> nan)."""
    out = coerce_optional_numeric(value)
    if math.isnan(out):
        return default
    return out


def parse_pipe_values(value) -> list[str]:
    """R/00_input_workbook.R:171-178 — split ``a|b|c`` into compacted parts."""
    vals = _values(value)
    if not vals or all(_is_na(v) for v in vals):
        return []
    s = _as_character(vals[0])
    if s is None:
        return []
    parts = re.split(r"\|", s)
    return compact_chr(parts)


def parse_pairwise_values(value) -> list[list[str]]:
    """R/00_input_workbook.R:180-196 — parse ``g1~g2|g3~g4`` pair lists."""
    pairs = parse_pipe_values(value)
    out: list[list[str]] = []
    for pair_text in pairs:
        pair = compact_chr(pair_text.split("~"))
        if len(pair) != 2:
            raise ValueError(
                f"Invalid pairwise comparison entry: '{pair_text}'. Use group1~group2."
            )
        out.append(pair)
    return out


def auto_pairwise_values(levels) -> list[list[str]]:
    """R/00_input_workbook.R:198-206 — all combn(levels, 2) pairs."""
    levels = compact_chr(levels)
    if len(levels) < 2:
        return []
    return [list(pair) for pair in combinations(levels, 2)]


def _blank_cell(value) -> bool:
    """True when a workbook cell is empty/NA — i.e. carries no assertion at all."""
    vals = _values(value)
    if not vals or all(_is_na(v) for v in vals):
        return True
    raw = _as_character(vals[0])
    return raw is None or not raw.strip()


def coerce_flag(value, default: bool = False) -> bool:
    """R/00_input_workbook.R:208-223 — parse workbook boolean cells."""
    vals = _values(value)
    if not vals or all(_is_na(v) for v in vals):
        return default
    v = vals[0]
    raw = _as_character(v)
    if raw is None:
        # NOTE(parity): R reaches stop() with paste0(NA) -> the literal "NA"
        # when the first element is NA but not all elements are (R:213-222).
        raise ValueError("Could not parse logical value 'NA'.")
    raw = raw.strip().lower()
    if not raw:
        return default
    if raw in ("true", "t", "1", "yes", "y"):
        return True
    if raw in ("false", "f", "0", "no", "n"):
        return False
    raise ValueError(f"Could not parse logical value '{_as_character(v)}'.")


def coerce_optional_numeric(value) -> float:
    """R/00_input_workbook.R:225-235 — as.numeric with error on unparseable."""
    vals = _values(value)
    if not vals or all(_is_na(v) for v in vals):
        return float("nan")
    v = vals[0]
    if _is_na(v):
        return float("nan")
    if isinstance(v, (bool, np.bool_)):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    s = str(v)
    try:
        return float(s.strip())
    except ValueError:
        # R errors only when the original is non-NA and nzchar (untrimmed).
        if s != "":
            raise ValueError(f"Could not parse numeric value '{_as_character(v)}'.") from None
        return float("nan")


def _to_numeric_or_nan(v) -> float:
    """R ``suppressWarnings(as.numeric(v))`` — nan instead of an error."""
    if _is_na(v):
        return float("nan")
    if isinstance(v, (bool, np.bool_)):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return float("nan")


# --------------------------------------------------------------------------- #
# Table normalization (R/00_input_workbook.R:108-147)
# --------------------------------------------------------------------------- #


def trim_character_columns(df: pd.DataFrame) -> pd.DataFrame:
    """R/00_input_workbook.R:144-147 — str_trim every character column."""
    df = df.copy()
    for col in df.columns:
        ser = df[col]
        if ser.dtype == object or pd.api.types.is_string_dtype(ser):
            df[col] = ser.map(lambda v: v.strip() if isinstance(v, str) else v)
    return df


def ensure_workbook_sheet_columns(df, sheet_name: str) -> pd.DataFrame:
    """R/00_input_workbook.R:108-131 — trim, add missing sheet columns as NA,
    order desired columns first (extras appended)."""
    if df is None:
        df = pd.DataFrame()
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    df = trim_character_columns(df)

    desired = workbook_sheet_columns().get(sheet_name)
    if desired is None:
        return df

    for col_name in desired:
        if col_name not in df.columns:
            df[col_name] = None  # rep(NA, nrow(df)); empty column when 0 rows

    extra_cols = [c for c in df.columns if c not in desired]
    return df[list(desired) + extra_cols]


def normalize_workbook_tables(tables) -> dict[str, pd.DataFrame]:
    """R/00_input_workbook.R:133-142 — every spec'd sheet present & normalized."""
    tables = tables or {}
    normalized: dict[str, pd.DataFrame] = {}
    for sheet_name in workbook_sheet_specs():
        normalized[sheet_name] = ensure_workbook_sheet_columns(
            tables.get(sheet_name), sheet_name
        )
    return normalized


# --------------------------------------------------------------------------- #
# Sheet reading & validation
# --------------------------------------------------------------------------- #


def _drop_phantom_excel_cells(df: pd.DataFrame) -> pd.DataFrame:
    """readxl parity: readxl sizes sheets by cells with values, while openpyxl
    also reports formatted-but-empty cells. Drop trailing all-NA rows and
    header-less ("Unnamed: N") all-NA columns."""
    if df.shape[1] > 0:
        phantom_cols = [
            c
            for c in df.columns
            if isinstance(c, str) and re.fullmatch(r"Unnamed: \d+", c) and df[c].isna().all()
        ]
        if phantom_cols:
            df = df.drop(columns=phantom_cols)
    if df.shape[0] > 0 and df.shape[1] > 0:
        nonempty = df.notna().any(axis=1).to_numpy()
        if nonempty.any():
            last = int(np.max(np.nonzero(nonempty)[0]))
            df = df.iloc[: last + 1]
        else:
            df = df.iloc[0:0]
    return df


def read_required_sheet(input_path, sheet_name: str, required_cols) -> pd.DataFrame:
    """R/00_input_workbook.R:237-253 — read one sheet, trim, check columns.

    ``input_path`` may be a path or an open ``pd.ExcelFile``. Uses
    ``engine="openpyxl"``. (readxl guesses column types from the first 1000
    rows; pandas scans all rows — see module docstring.)
    """
    if isinstance(input_path, pd.ExcelFile):
        df = input_path.parse(sheet_name)
    else:
        df = pd.read_excel(input_path, sheet_name=sheet_name, engine="openpyxl")
    df = _drop_phantom_excel_cells(df)
    df = trim_character_columns(df)

    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        raise ValueError(
            f"Sheet '{sheet_name}' is missing required columns: " + ", ".join(missing_cols)
        )
    return df


def validate_unique_keys(df: pd.DataFrame, column_name: str, sheet_name: str) -> None:
    """R/00_input_workbook.R:255-279 — non-blank, unique key column."""
    values = list(df[column_name]) if column_name in df.columns else []
    blanks = [
        v for v in values
        if _is_na(v) or not str(_as_character(v)).strip()
    ]
    if blanks:
        raise ValueError(
            f"Sheet '{sheet_name}' contains blank values in column '{column_name}'."
        )

    chars = [_as_character(v) for v in values]
    counts: dict[str, int] = {}
    for c in chars:
        counts[c] = counts.get(c, 0) + 1
    dupes = sorted(k for k, n in counts.items() if k is not None and n > 1)
    if dupes:
        raise ValueError(
            f"Sheet '{sheet_name}' has duplicate {column_name} values: " + ", ".join(dupes)
        )


def resolve_levels_from_data(data: pd.DataFrame, column_name) -> list[str]:
    """R/00_input_workbook.R:281-292 — factor levels or sorted unique values."""
    if column_name is None or not str(column_name) or column_name not in data.columns:
        return []
    vals = data[column_name]
    if isinstance(vals.dtype, pd.CategoricalDtype):
        return [_as_character(c) for c in vals.cat.categories]
    uniq = {_as_character(v) for v in vals if not _is_na(v)}
    # NOTE(parity): R sort() collates by locale; Python sorts by codepoint.
    return sorted(uniq)


# --------------------------------------------------------------------------- #
# Site masks (R/00_input_workbook.R:294-419)
# --------------------------------------------------------------------------- #


def default_site_label_source_column(raw_data) -> str:
    """R/00_input_workbook.R:294-300 — first data column names site labels."""
    if raw_data is None:
        raw_data = pd.DataFrame()
    column_names = list(raw_data.columns)
    if not column_names:
        raise ValueError(
            "Sheet 'data' must include at least one column to support site labels."
        )
    return column_names[0]


def resolve_site_label_values(raw_data: pd.DataFrame, site_ids, label_column: str) -> list[str]:
    """R/00_input_workbook.R:302-311 — label values with 'Site {id}' fallback.

    ``site_ids`` are 1-based row numbers into ``raw_data``.
    """
    site_ids = list(site_ids)
    if not site_ids:
        return []
    col = raw_data[label_column]
    out: list[str] = []
    for sid in site_ids:
        s = _as_character(col.iloc[int(sid) - 1])
        if s is None or not s.strip():
            s = f"Site {int(sid)}"
        out.append(s)
    return out


def build_site_mask_config_from_workbook(
    site_masks_tbl: pd.DataFrame,
    site_mask_settings_tbl: pd.DataFrame,
    raw_data: pd.DataFrame,
) -> dict:
    """R/00_input_workbook.R:313-376 — validate masks + resolve label column."""
    default_label_column = default_site_label_source_column(raw_data)
    label_column_values = compact_chr(site_mask_settings_tbl.get("site_label_column"))

    if len(set(label_column_values)) > 1:
        raise ValueError(
            "Sheet 'site_mask_settings' must contain at most one distinct "
            "site_label_column value."
        )

    label_column = label_column_values[0] if label_column_values else default_label_column
    if label_column not in raw_data.columns:
        raise ValueError(
            f"Sheet 'site_mask_settings' references missing site_label_column "
            f"'{label_column}'."
        )

    site_id_values = compact_chr(site_masks_tbl.get("masked_sites"))
    site_ids: list[int] = []
    if site_id_values:
        numeric_ids: list[int | None] = []
        for s in site_id_values:
            try:
                numeric_ids.append(int(float(s)))  # as.integer(): truncates
            except ValueError:
                numeric_ids.append(None)

        n_rows = len(raw_data)
        bad_idx = [
            i for i, v in enumerate(numeric_ids)
            if v is None or v <= 0 or v > n_rows
        ]
        if bad_idx:
            bad_values: list[str] = []
            for i in bad_idx:
                if site_id_values[i] not in bad_values:
                    bad_values.append(site_id_values[i])
            raise ValueError(
                "Sheet 'site_masks' contains invalid masked_sites values: "
                + ", ".join(bad_values)
            )

        seen: set[int] = set()
        dup_ids: list[int] = []
        for v in numeric_ids:
            if v in seen and v not in dup_ids:
                dup_ids.append(v)
            seen.add(v)
        if dup_ids:
            raise ValueError(
                "Sheet 'site_masks' contains duplicate masked_sites values: "
                + ", ".join(str(v) for v in dup_ids)
            )

        site_ids = sorted(set(numeric_ids))

    return {
        "site_label_column": label_column,
        "masked_site_ids": site_ids,
        "site_labels": resolve_site_label_values(raw_data, site_ids, label_column),
    }


def site_mask_tables_from_config(raw_data: pd.DataFrame, site_mask_config: dict) -> dict:
    """R/00_input_workbook.R:378-396 — canonical site_masks/settings tables."""
    masked_ids = [int(i) for i in _or(site_mask_config.get("masked_site_ids"), [])]
    label_column = _or(
        site_mask_config.get("site_label_column"),
        default_site_label_source_column(raw_data),
    )
    site_labels = resolve_site_label_values(raw_data, masked_ids, label_column)

    site_masks_tbl = pd.DataFrame(
        {
            "masked_sites": pd.Series(masked_ids, dtype="int64"),
            "site_label": pd.Series(site_labels, dtype=object),
        }
    )
    site_mask_settings_tbl = pd.DataFrame({"site_label_column": [label_column]})

    return {
        "site_masks": ensure_workbook_sheet_columns(site_masks_tbl, "site_masks"),
        "site_mask_settings": ensure_workbook_sheet_columns(
            site_mask_settings_tbl, "site_mask_settings"
        ),
    }


def annotate_site_identity_columns(raw_data: pd.DataFrame, site_mask_config: dict) -> pd.DataFrame:
    """R/00_input_workbook.R:398-409 — add 1-based site id + label columns."""
    data = pd.DataFrame(raw_data).copy()
    label_column = _or(
        (site_mask_config or {}).get("site_label_column"),
        default_site_label_source_column(data),
    )
    data[SITE_ID_COL] = np.arange(1, len(data) + 1, dtype="int64")
    data[SITE_LABEL_COL] = resolve_site_label_values(
        data, list(data[SITE_ID_COL]), label_column
    )
    return data


def apply_global_site_masks(raw_data: pd.DataFrame, site_mask_config: dict) -> pd.DataFrame:
    """R/00_input_workbook.R:411-419 — drop rows whose site id is masked."""
    masked_ids = [int(i) for i in _or((site_mask_config or {}).get("masked_site_ids"), [])]
    if not masked_ids:
        return raw_data
    keep = ~raw_data[SITE_ID_COL].isin(masked_ids)
    return raw_data[keep].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Foreign keys (R/00_input_workbook.R:421-447)
# --------------------------------------------------------------------------- #


def validate_foreign_keys(
    metrics_tbl: pd.DataFrame,
    metric_predictors_tbl: pd.DataFrame,
    metric_stratifications_tbl: pd.DataFrame,
    predictors_tbl: pd.DataFrame,
    stratifications_tbl: pd.DataFrame,
) -> None:
    metric_keys = compact_chr(metrics_tbl.get("metric_key"))
    predictor_keys = compact_chr(predictors_tbl.get("predictor_key"))
    strat_keys = compact_chr(stratifications_tbl.get("strat_key"))

    def _has_bad_rows(df: pd.DataFrame, col_a: str, keys_a: list, col_b: str, keys_b: list) -> bool:
        if len(df) == 0:
            return False
        for va, vb in zip(df[col_a], df[col_b]):
            # NA/blank keys fail the %in% test exactly like R (NA %in% x is FALSE).
            if _as_character(va) not in keys_a or _as_character(vb) not in keys_b:
                return True
        return False

    if _has_bad_rows(metric_predictors_tbl, "metric_key", metric_keys, "predictor_key", predictor_keys):
        raise ValueError(
            "Sheet 'metric_predictors' contains unknown metric_key or predictor_key values."
        )
    if _has_bad_rows(metric_stratifications_tbl, "metric_key", metric_keys, "strat_key", strat_keys):
        raise ValueError(
            "Sheet 'metric_stratifications' contains unknown metric_key or strat_key values."
        )


# --------------------------------------------------------------------------- #
# Split / filter / arrange helpers shared by the config builders
# --------------------------------------------------------------------------- #


def _split_by_key(df: pd.DataFrame, key_col: str) -> dict[str, pd.DataFrame]:
    """R ``split(df, factor(df[[key]], levels = unique(df[[key]])))`` —
    first-appearance grouping. # NOTE(parity): factor() drops NA keys, so
    NA-keyed rows silently vanish here too ("" is a valid key like in R)."""
    groups: dict[str, list[int]] = {}
    values = list(df[key_col]) if key_col in df.columns else []
    for i, v in enumerate(values):
        key = _as_character(v)
        if key is None:
            continue
        groups.setdefault(key, []).append(i)
    return {k: df.iloc[idx] for k, idx in groups.items()}


def _filter_by_key(df: pd.DataFrame, key_col: str, key: str) -> pd.DataFrame:
    """dplyr::filter(col == key) with R's coercing `==` (numeric vs character)."""
    if len(df) == 0:
        return df
    mask = np.array([_as_character(v) == key for v in df[key_col]], dtype=bool)
    return df[mask]


def _arrange_by_sort_order(df: pd.DataFrame, tiebreak_col: str) -> pd.DataFrame:
    """R ``mutate(sort_order = as.numeric(sort_order)) |> arrange(sort_order, tiebreak)``
    — stable, numeric sort_order first (NA last), then tiebreak (NA last)."""
    so = [_to_numeric_or_nan(v) for v in df["sort_order"]]
    tb = (
        [_as_character(v) for v in df[tiebreak_col]]
        if tiebreak_col in df.columns
        else [None] * len(df)
    )
    order = sorted(
        range(len(df)),
        key=lambda i: (
            1 if math.isnan(so[i]) else 0,
            0.0 if math.isnan(so[i]) else so[i],
            1 if tb[i] is None else 0,
            tb[i] if tb[i] is not None else "",
        ),
    )
    out = df.iloc[order].copy()
    out["sort_order"] = [so[i] for i in order]  # keep the as.numeric mutate
    return out


def _arrange_by_column(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """R ``arrange(col)`` on a character column (NA last, stable)."""
    vals = [_as_character(v) for v in df[col]]
    order = sorted(
        range(len(df)),
        key=lambda i: (1 if vals[i] is None else 0, vals[i] if vals[i] is not None else ""),
    )
    return df.iloc[order]


# --------------------------------------------------------------------------- #
# Config builders (R/00_input_workbook.R:449-788)
# --------------------------------------------------------------------------- #


def build_predictor_config_from_workbook(predictors_tbl: pd.DataFrame) -> dict:
    """R/00_input_workbook.R:449-486. Defaults: type "continuous", derived
    False, derivation_method "none", missing_data_rule "error",
    expected_range [min, max] (nan when blank)."""
    predictor_rows = _split_by_key(predictors_tbl, "predictor_key")
    config: dict[str, dict] = {}
    for predictor_key, row_df in predictor_rows.items():
        row = row_df.iloc[0]
        cols = row_df.columns
        col_name = scalar_text(row.get("column_name"))
        if col_name is None:
            raise ValueError(f"Predictor '{predictor_key}' is missing column_name.")

        expected_min = (
            coerce_optional_numeric(row.get("expected_min"))
            if "expected_min" in cols
            else float("nan")
        )
        expected_max = (
            coerce_optional_numeric(row.get("expected_max"))
            if "expected_max" in cols
            else float("nan")
        )

        config[predictor_key] = {
            "display_name": scalar_text(row.get("display_name"), predictor_key),
            "column_name": col_name,
            "type": scalar_text(row.get("type"), "continuous"),
            "derived": coerce_flag(row.get("derived"), default=False),
            "derivation_method": (
                scalar_text(row.get("derivation_method"), "none")
                if "derivation_method" in cols
                else "none"
            ),
            "source_columns": (
                parse_pipe_values(row.get("source_columns")) if "source_columns" in cols else []
            ),
            "constant": (
                scalar_number(row.get("constant"), float("nan"))
                if "constant" in cols
                else float("nan")
            ),
            "expected_range": [expected_min, expected_max],
            "missing_data_rule": (
                scalar_text(row.get("missing_data_rule"), "error")
                if "missing_data_rule" in cols
                else "error"
            ),
            "notes": scalar_text(row.get("notes"), "") if "notes" in cols else "",
        }
    return config


def build_factor_recode_config_from_workbook(factor_recodes_tbl: pd.DataFrame) -> dict:
    """R/00_input_workbook.R:488-524 — recode_key -> collapse_map config."""
    if len(factor_recodes_tbl) == 0:
        return {}

    recode_rows = _split_by_key(factor_recodes_tbl, "recode_key")
    config: dict[str, dict] = {}
    for recode_key, row_df in recode_rows.items():
        target_rows = _split_by_key(row_df, "target_level")
        collapse_map: dict[str, list[str]] = {}
        for target_level, target_df in target_rows.items():
            values: list[str] = []
            source_col = (
                target_df["source_values"] if "source_values" in target_df.columns else []
            )
            for sv in _values(source_col):
                for part in parse_pipe_values(sv):
                    if part not in values:  # unique(), first-appearance order
                        values.append(part)
            collapse_map[target_level] = values

        first_row = row_df.iloc[0]
        source_column = scalar_text(first_row.get("source_column"))
        target_column = scalar_text(first_row.get("target_column"))
        if source_column is None:
            raise ValueError(f"Factor recode '{recode_key}' is missing source_column.")
        if target_column is None:
            raise ValueError(f"Factor recode '{recode_key}' is missing target_column.")
        config[recode_key] = {
            "source_column": source_column,
            "target_column": target_column,
            "collapse_map": collapse_map,
            "notes": (
                scalar_text(first_row.get("notes"), "") if "notes" in row_df.columns else ""
            ),
        }
    return config


def build_strat_config_from_workbook(
    stratifications_tbl: pd.DataFrame,
    strat_groups_tbl: pd.DataFrame,
    raw_data: pd.DataFrame,
    factor_recode_config: dict | None = None,
) -> dict:
    """R/00_input_workbook.R:526-734 — strat_key -> config with per-type rules
    (paired / raw_single / custom_group)."""
    factor_recode_config = factor_recode_config or {}
    strat_rows = _split_by_key(stratifications_tbl, "strat_key")
    config: dict[str, dict] = {}

    for strat_key, row_df in strat_rows.items():
        row = row_df.iloc[0]
        cols = row_df.columns

        # NOTE(parity): R computes compact_chr(row$strat_type)[1] %||% stop(...).
        # Indexing an empty character vector yields NA (never NULL), so the
        # stop() is dead code and a blank strat_type falls through to the
        # "Unsupported strat_type 'NA'" error below (R:538-541).
        strat_type = _first(compact_chr(row.get("strat_type")))

        min_group_size = (
            int(scalar_number(row.get("min_group_size"), 5))
            if "min_group_size" in cols
            else 5
        )
        notes = scalar_text(row.get("notes"), "") if "notes" in cols else ""
        levels = parse_pipe_values(row.get("levels")) if "levels" in cols else []
        pairwise = (
            parse_pairwise_values(row.get("pairwise_comparisons"))
            if "pairwise_comparisons" in cols
            else []
        )
        source_column = (
            _first(compact_chr(row.get("source_column"))) if "source_column" in cols else None
        )
        source_data_type = (
            _first(compact_chr(row.get("source_data_type")))
            if "source_data_type" in cols
            else None
        )
        derived_column_name = (
            _first(compact_chr(row.get("derived_column_name")))
            if "derived_column_name" in cols
            else None
        )
        recode_target_columns = list(
            dict.fromkeys(
                recode.get("target_column") for recode in factor_recode_config.values()
            )
        )

        if strat_type == "paired":
            primary = (
                _first(compact_chr(row.get("primary_strat_key")))
                if "primary_strat_key" in cols
                else None
            )
            secondary = (
                _first(compact_chr(row.get("secondary_strat_key")))
                if "secondary_strat_key" in cols
                else None
            )
            if primary is None or secondary is None:
                raise ValueError(
                    f"Paired stratification '{strat_key}' must provide primary_strat_key "
                    "and secondary_strat_key."
                )
            config[strat_key] = {
                "display_name": scalar_text(row.get("display_name"), strat_key),
                "column_name": None,
                "type": "paired",
                "primary": primary,
                "secondary": secondary,
                "min_group_size": min_group_size,
                "levels": [],
                "pairwise_comparisons": [],
                "notes": notes,
            }
            continue

        if strat_type == "raw_single":
            if source_column is None or not source_column:
                raise ValueError(
                    f"Raw stratification '{strat_key}' must provide source_column."
                )
            if source_column not in raw_data.columns and source_column not in recode_target_columns:
                raise ValueError(
                    f"Raw stratification '{strat_key}' references missing data column "
                    f"'{source_column}'."
                )

            if not levels:
                if source_column in raw_data.columns:
                    levels = resolve_levels_from_data(raw_data, source_column)
                else:
                    matching_recode = next(
                        (
                            recode
                            for recode in factor_recode_config.values()
                            if recode.get("target_column") == source_column
                        ),
                        None,
                    )
                    levels = list((matching_recode or {}).get("collapse_map", {}).keys())

            if not pairwise:
                pairwise = auto_pairwise_values(levels)

            config[strat_key] = {
                "display_name": scalar_text(row.get("display_name"), strat_key),
                "column_name": source_column,
                "type": "single",
                "source_column": source_column,
                "source_data_type": (
                    source_data_type if source_data_type is not None else "categorical"
                ),
                "min_group_size": min_group_size,
                "levels": levels,
                "pairwise_comparisons": pairwise,
                "notes": notes,
            }
            continue

        if strat_type != "custom_group":
            st_text = "NA" if strat_type is None else strat_type
            raise ValueError(
                f"Unsupported strat_type '{st_text}' for stratification '{strat_key}'."
            )

        if source_column is None or not source_column:
            raise ValueError(f"Custom grouping '{strat_key}' must provide source_column.")

        if derived_column_name is None or not derived_column_name:
            derived_column_name = strat_key

        if source_column not in raw_data.columns:
            raise ValueError(
                f"Custom grouping '{strat_key}' references missing data column "
                f"'{source_column}'."
            )

        if source_data_type not in ("categorical", "continuous"):
            raise ValueError(
                f"Custom grouping '{strat_key}' must set source_data_type to "
                "'categorical' or 'continuous'."
            )

        if derived_column_name in raw_data.columns:
            raise ValueError(
                f"Custom grouping '{strat_key}' would overwrite existing data column "
                f"'{derived_column_name}'."
            )

        group_rows = _filter_by_key(strat_groups_tbl, "strat_key", strat_key)
        if len(group_rows) == 0:
            raise ValueError(
                f"Custom grouping '{strat_key}' has no rows in sheet 'strat_groups'."
            )

        if "sort_order" in group_rows.columns:
            group_rows = _arrange_by_sort_order(group_rows, "group_label")
        else:
            group_rows = _arrange_by_column(group_rows, "group_label")

        group_definitions = []
        for _, entry in group_rows.iterrows():
            group_definitions.append(
                {
                    # NOTE(parity): stringified via as.character(); R keeps the
                    # raw value and would fail later in vapply() for non-text
                    # group_label columns.
                    "group_label": _as_character(entry.get("group_label")),
                    "source_values": parse_pipe_values(entry.get("source_values")),
                    "rule_expression": scalar_text(entry.get("rule_expression"), None),
                    "sort_order": _to_numeric_or_nan(entry.get("sort_order")),
                }
            )

        labels = [d["group_label"] for d in group_definitions]
        seen: list = []
        has_dup = False
        for lab in labels:
            if lab in seen:
                has_dup = True
                break
            seen.append(lab)
        if has_dup:
            raise ValueError(
                f"Custom grouping '{strat_key}' has duplicate group_label values."
            )

        if not levels:
            levels = labels
        if not pairwise:
            pairwise = auto_pairwise_values(levels)

        config[strat_key] = {
            "display_name": scalar_text(row.get("display_name"), strat_key),
            "column_name": derived_column_name,
            "type": "single",
            "source_column": source_column,
            "source_data_type": (
                source_data_type if source_data_type is not None else "categorical"
            ),
            "min_group_size": min_group_size,
            "levels": levels,
            "pairwise_comparisons": pairwise,
            "notes": notes,
            "is_custom_grouping": True,
            "group_definitions": group_definitions,
        }
    return config


def build_metric_config_from_workbook(
    metrics_tbl: pd.DataFrame,
    metric_predictors_tbl: pd.DataFrame,
    metric_stratifications_tbl: pd.DataFrame,
) -> dict:
    """R/00_input_workbook.R:736-788. Defaults: units "", metric_family
    "continuous", higher_is_better True, monotonic_linear True,
    preferred_transform "none", min_sample_size 10, best_subsets_allowed True,
    count_model False, stratification_mode "subset", include_in_summary True."""
    metric_rows = _split_by_key(metrics_tbl, "metric_key")
    config: dict[str, dict] = {}

    for metric_key, row_df in metric_rows.items():
        row = row_df.iloc[0]
        cols = row_df.columns

        predictor_rows = _filter_by_key(metric_predictors_tbl, "metric_key", metric_key)
        if "sort_order" in predictor_rows.columns:
            predictor_rows = _arrange_by_sort_order(predictor_rows, "predictor_key")

        strat_rows = _filter_by_key(metric_stratifications_tbl, "metric_key", metric_key)
        if "sort_order" in strat_rows.columns:
            strat_rows = _arrange_by_sort_order(strat_rows, "strat_key")

        column_name = scalar_text(row.get("column_name"))
        if column_name is None:
            raise ValueError(f"Metric '{metric_key}' is missing column_name.")

        curve_form = (scalar_text(row.get("curve_form"), None)
                      if "curve_form" in cols else None)
        config[metric_key] = {
            "display_name": scalar_text(row.get("display_name"), metric_key),
            "column_name": column_name,
            "units": scalar_text(row.get("units"), "") if "units" in cols else "",
            "metric_family": scalar_text(row.get("metric_family"), "continuous"),
            # A two-sided curve degrades at BOTH extremes, so it has no monotone
            # direction; the blank cell means null, not "default to TRUE".
            "higher_is_better": (
                None if (curve_form == CURVE_FORM_OPTIMUM
                         and _blank_cell(row.get("higher_is_better")))
                else coerce_flag(row.get("higher_is_better"), default=True)
            ),
            "monotonic_linear": coerce_flag(row.get("monotonic_linear"), default=True),
            "allowed_predictors": compact_chr(predictor_rows.get("predictor_key")),
            "allowed_stratifications": compact_chr(strat_rows.get("strat_key")),
            "preferred_transform": scalar_text(row.get("preferred_transform"), "none"),
            "min_sample_size": int(scalar_number(row.get("min_sample_size"), 10)),
            "best_subsets_allowed": coerce_flag(row.get("best_subsets_allowed"), default=True),
            "count_model": coerce_flag(row.get("count_model"), default=False),
            "stratification_mode": scalar_text(row.get("stratification_mode"), "subset"),
            "include_in_summary": coerce_flag(row.get("include_in_summary"), default=True),
            "missing_data_rule": (
                scalar_text(row.get("missing_data_rule"), None)
                if "missing_data_rule" in cols
                else None
            ),
            "notes": scalar_text(row.get("notes"), "") if "notes" in cols else "",
        }
        if curve_form:
            config[metric_key]["curve_form"] = curve_form
    return config


# --------------------------------------------------------------------------- #
# Function mappings (R/00_input_workbook.R:790-939)
# --------------------------------------------------------------------------- #


def _nzchar(v) -> bool:
    """R ``nzchar(keepNA = FALSE)`` — NA counts as TRUE (non-zero length)."""
    if v is None:
        return True
    return len(v) > 0


def build_function_mappings_from_tables(tables, metric_keys) -> pd.DataFrame | None:
    """R/00_input_workbook.R:790-939.

    Returns None if the function_mappings sheet is absent or empty; otherwise
    a DataFrame(metric_key, discipline, function_label, sort_order). One row
    per metric x function; a row with discipline + function_label filled but
    metric_key blank is an empty function bucket. sort_order derives from row
    order. metric_display_name is informational and ignored. Rows referencing
    unknown metrics are dropped with a warning. The result carries
    ``df.attrs["covers_all_metrics"]`` (bool) — R uses attr(); the bundle also
    exposes it as ``bundle["mapping_covers_all_metrics"]``.
    """
    metric_keys = list(
        dict.fromkeys(
            k
            for k in (_as_character(v) for v in _values(metric_keys))
            if k is not None and len(k) > 0
        )
    )
    df = (tables or {}).get("function_mappings")
    if df is None or len(df) == 0:
        return None

    df = pd.DataFrame(df)
    cols = df.columns
    records: list[dict] = []
    for i in range(len(df)):
        def _cell(col_name):
            if col_name not in cols:
                return None
            s = _as_character(df[col_name].iloc[i])
            return s.strip() if s is not None else None

        records.append(
            {
                "metric_key": _cell("metric_key"),
                "discipline": _cell("discipline"),
                "function_label": _cell("function_label"),
                "sort_order": i + 1,  # row order drives sort_order internally
            }
        )

    # Drop rows that are entirely blank. NOTE(parity): R uses nzchar() with
    # keepNA=FALSE, where nzchar(NA) is TRUE — so only rows whose cells are
    # non-NA empty strings get dropped; all-NA rows survive as scaffold rows
    # (R:820-821).
    records = [
        r
        for r in records
        if _nzchar(r["metric_key"]) or _nzchar(r["discipline"]) or _nzchar(r["function_label"])
    ]
    if not records:
        return None

    def _has_metric(r) -> bool:
        return r["metric_key"] is not None and len(r["metric_key"]) > 0

    named_rows = [r for r in records if _has_metric(r)]
    bucket_rows = [r for r in records if not _has_metric(r)]

    kept_rows = [r for r in named_rows if r["metric_key"] in metric_keys]
    dropped: list[str] = []
    for r in named_rows:
        if r["metric_key"] not in metric_keys and r["metric_key"] not in dropped:
            dropped.append(r["metric_key"])
    if dropped:
        logger.warning(
            "function_mappings: ignoring %d row(s) whose metric_key is not in the "
            "metrics sheet: %s",
            len(dropped),
            ", ".join(dropped),
        )

    for r in bucket_rows:
        r["metric_key"] = None
    records = kept_rows + bucket_rows
    if not records:
        return None

    fixed_disc = _fixed_discipline_order()

    # Blank discipline is allowed (scaffold rows); reject non-blank unknowns.
    disc_nonblank = [
        r["discipline"] for r in records if r["discipline"] is not None and r["discipline"]
    ]
    bad_disc = list(dict.fromkeys(d for d in disc_nonblank if d not in fixed_disc))
    if bad_disc:
        raise ValueError(
            "Sheet 'function_mappings' has unknown discipline values: "
            + ", ".join(bad_disc)
            + ". Allowed: "
            + ", ".join(fixed_disc)
            + "."
        )

    # A metric may be reused across functions, but the same (metric_key,
    # function_label) pair — case-insensitive on the label — must be unique.
    assign_rows = [
        r
        for r in records
        if r["metric_key"] is not None
        and r["metric_key"]
        and r["function_label"] is not None
        and r["function_label"]
    ]
    seen_pairs: set[str] = set()
    dup_metrics: list[str] = []
    for r in assign_rows:
        pair = r["metric_key"] + "\r" + r["function_label"].strip().lower()
        if pair in seen_pairs:
            if r["metric_key"] not in dup_metrics:
                dup_metrics.append(r["metric_key"])
        seen_pairs.add(pair)
    if dup_metrics:
        raise ValueError(
            "Sheet 'function_mappings' assigns a metric to the same function more "
            "than once: " + ", ".join(dup_metrics)
        )

    # Blank discipline/function_label stays NA for scaffold rows.
    for r in records:
        if r["discipline"] is not None and not r["discipline"]:
            r["discipline"] = None
        if r["function_label"] is not None and not r["function_label"]:
            r["function_label"] = None

    # A function belongs to exactly one discipline (case-insensitive label).
    pair_rows = [
        r for r in records if r["function_label"] is not None and r["discipline"] is not None
    ]
    fn_disc_pairs: list[tuple[str, str]] = []
    for r in pair_rows:
        pair = (r["function_label"], r["discipline"])
        if pair not in fn_disc_pairs:
            fn_disc_pairs.append(pair)
    lcs = [fl.strip().lower() for fl, _ in fn_disc_pairs]
    collisions = {lc for lc in lcs if lcs.count(lc) > 1}
    if collisions:
        bad = list(
            dict.fromkeys(
                fl for (fl, _), lc in zip(fn_disc_pairs, lcs) if lc in collisions
            )
        )
        raise ValueError(
            "Sheet 'function_mappings' assigns the same function to more than one "
            "discipline: " + ", ".join(bad)
            + ". Each function belongs to exactly one discipline."
        )

    # Order: assigned rows by (discipline order, function_label, row order),
    # scaffold rows (NA discipline) last. R:915-921.
    def _order_key(r):
        disc = r["discipline"]
        fl = r["function_label"]
        return (
            1 if disc is None else 0,
            fixed_disc.index(disc) if disc in fixed_disc else math.inf,
            1 if fl is None else 0,
            fl if fl is not None else "",
            r["sort_order"],
        )

    ordered = sorted(records, key=_order_key)

    result = pd.DataFrame(
        {
            "metric_key": pd.Series([r["metric_key"] for r in ordered], dtype=object),
            "discipline": pd.Series([r["discipline"] for r in ordered], dtype=object),
            "function_label": pd.Series([r["function_label"] for r in ordered], dtype=object),
            "sort_order": pd.Series(range(1, len(ordered) + 1), dtype="int64"),
        }
    )

    # covers_all_metrics: every workbook metric appears with BOTH discipline
    # and function_label filled in (scaffold rows do not count).
    assigned_keys = {
        r["metric_key"]
        for r in ordered
        if r["metric_key"] is not None
        and r["metric_key"]
        and r["discipline"] is not None
        and r["function_label"] is not None
    }
    result.attrs["covers_all_metrics"] = len(set(metric_keys) - assigned_keys) == 0
    return result


# --------------------------------------------------------------------------- #
# Config -> tables (the inverse of build_input_bundle_from_tables below)
# --------------------------------------------------------------------------- #


def _flag_text(value, default: bool) -> str:
    """Workbook sheets store flags as the strings coerce_flag() parses."""
    if value is None or _is_na(value):
        return "TRUE" if default else "FALSE"
    return "TRUE" if coerce_flag(value, default=default) else "FALSE"


def _pipe_text(values) -> str:
    """Workbook sheets store multi-values as the "a|b|c" parse_pipe_values() reads."""
    return "|".join(compact_chr(values))


def _pairwise_text(pairs) -> str:
    """Workbook sheets store pair lists as the "g1~g2|g3~g4" parse_pairwise_values()
    reads. A bool or blank means "no explicit pairs"; the reader then derives them
    from the levels via auto_pairwise_values()."""
    if pairs is None or isinstance(pairs, (bool, np.bool_)):
        return ""
    out = []
    for pair in _values(pairs):
        members = compact_chr(pair)
        if len(members) == 2:
            out.append("~".join(members))
    return "|".join(out)


def tables_from_configs(
    data,
    metric_config=None,
    predictor_config=None,
    strat_config=None,
    factor_recode_config=None,
) -> dict:
    """Rebuild workbook ``tables`` from a built project's configs.

    The inverse of :func:`build_input_bundle_from_tables`, for sessions that
    carry the OUTCOME of a build but not the workbook that produced it -- every
    published library assessment, because the headless writers never had a
    workbook to save.

    Deliberately not ``build_config_tables_from_roles``: that regenerates every
    per-metric setting from defaults (``higher_is_better`` TRUE for all), which
    silently flips the direction of every "more is worse" metric the moment
    anyone rebuilds. Here each sheet value comes from the config, and only keys
    the config genuinely lacks fall back -- to the same defaults
    ``build_metric_config_from_workbook`` applies, so config -> tables -> config
    is stable.
    """
    metric_config = metric_config or {}
    predictor_config = predictor_config or {}
    strat_config = strat_config or {}
    factor_recode_config = factor_recode_config or {}

    if data is None:
        data = pd.DataFrame()
    elif not isinstance(data, pd.DataFrame):
        data = pd.DataFrame(data)

    metric_rows, mp_rows, ms_rows = [], [], []
    for metric_key, cfg in metric_config.items():
        cfg = cfg or {}
        family = _as_character(cfg.get("metric_family")) or "continuous"
        curve_form = _as_character(cfg.get("curve_form")) or ""
        two_sided = curve_form == CURVE_FORM_OPTIMUM
        metric_rows.append({
            "metric_key": metric_key,
            "display_name": _as_character(cfg.get("display_name")) or metric_key,
            "column_name": _as_character(cfg.get("column_name")) or metric_key,
            "units": _as_character(cfg.get("units")) or "",
            "metric_family": family,
            # A two-sided curve has no monotone direction; write the blank the
            # reader turns back into null instead of defaulting it to TRUE.
            "higher_is_better": ("" if (two_sided and cfg.get("higher_is_better") is None)
                                 else _flag_text(cfg.get("higher_is_better"), True)),
            "curve_form": curve_form,
            "monotonic_linear": _flag_text(cfg.get("monotonic_linear"), True),
            "preferred_transform": _as_character(cfg.get("preferred_transform")) or "none",
            "min_sample_size": int(_or(cfg.get("min_sample_size"), 10)),
            "best_subsets_allowed": _flag_text(cfg.get("best_subsets_allowed"), True),
            # The reader defaults count_model to False, so derive it from the
            # family rather than leaving a count metric silently continuous.
            "count_model": _flag_text(cfg.get("count_model"), family == "count"),
            "stratification_mode": _as_character(cfg.get("stratification_mode")) or "subset",
            "include_in_summary": _flag_text(cfg.get("include_in_summary"), True),
            "missing_data_rule": _as_character(cfg.get("missing_data_rule")) or "",
            "notes": _as_character(cfg.get("notes")) or "",
        })
        # Only keys the companion sheets actually define. build_input_bundle_from_tables
        # validates these as foreign keys, so emitting a row for a predictor or
        # stratification that was not passed in makes the workbook unreadable --
        # which is easy to hit, because a metric_config carries allowed_predictors
        # and allowed_stratifications whether or not the caller supplied the
        # matching configs.
        for i, pk in enumerate(
            k for k in compact_chr(cfg.get("allowed_predictors")) if k in predictor_config
        ):
            mp_rows.append({"metric_key": metric_key, "predictor_key": pk, "sort_order": i + 1})
        for i, sk in enumerate(
            k for k in compact_chr(cfg.get("allowed_stratifications")) if k in strat_config
        ):
            ms_rows.append({"metric_key": metric_key, "strat_key": sk, "sort_order": i + 1})

    pred_rows = []
    for key, cfg in predictor_config.items():
        cfg = cfg or {}
        pred_rows.append({
            "predictor_key": key,
            "display_name": _as_character(cfg.get("display_name")) or key,
            "column_name": _as_character(cfg.get("column_name")) or key,
            "type": _as_character(cfg.get("type")) or "continuous",
            "derived": _flag_text(cfg.get("derived"), False),
            "derivation_method": _as_character(cfg.get("derivation_method")) or "",
            "source_columns": ", ".join(compact_chr(cfg.get("source_columns"))),
            "constant": cfg.get("constant"),
            "expected_min": cfg.get("expected_min"),
            "expected_max": cfg.get("expected_max"),
            "missing_data_rule": _as_character(cfg.get("missing_data_rule")) or "",
            "notes": _as_character(cfg.get("notes")) or "",
        })

    strat_rows, group_rows, derived_strat_columns = [], [], []
    for key, cfg in strat_config.items():
        cfg = cfg or {}
        is_custom = coerce_flag(cfg.get("is_custom_grouping"), default=False)
        # build_strat_config_from_workbook accepts only paired / raw_single /
        # custom_group. The runtime config reports type "single" for both raw and
        # custom stratifications, so the writer has to recover the distinction
        # from is_custom_grouping or the round trip degrades a custom grouping
        # into a raw stratification on its own continuous source column.
        if _as_character(cfg.get("type")) == "paired":
            strat_type = "paired"
        elif is_custom:
            strat_type = "custom_group"
        else:
            strat_type = "raw_single"
        # The reader sets column_name from derived_column_name, so they must agree.
        derived_column_name = (
            _as_character(cfg.get("derived_column_name"))
            or (_as_character(cfg.get("column_name")) if is_custom else None)
            or ""
        )
        if is_custom and derived_column_name:
            derived_strat_columns.append(derived_column_name)
        strat_rows.append({
            "strat_key": key,
            "display_name": _as_character(cfg.get("display_name")) or key,
            "strat_type": strat_type,
            "source_column": _as_character(cfg.get("source_column")) or "",
            "source_data_type": _as_character(cfg.get("source_data_type")) or "",
            "primary_strat_key": _as_character(cfg.get("primary")) or "",
            "secondary_strat_key": _as_character(cfg.get("secondary")) or "",
            "derived_column_name": derived_column_name,
            "levels": _pipe_text(cfg.get("levels")),
            "pairwise_comparisons": _pairwise_text(cfg.get("pairwise_comparisons")),
            "min_group_size": int(_or(cfg.get("min_group_size"), 5)),
            "notes": _as_character(cfg.get("notes")) or "",
        })
        # The runtime shape is group_definitions/group_label/source_values/
        # rule_expression; the legacy groups/label/values/rule shape is still
        # read so an older config does not lose its groups.
        definitions = cfg.get("group_definitions")
        if definitions:
            for i, grp in enumerate(definitions):
                grp = grp or {}
                group_rows.append({
                    "strat_key": key,
                    "group_label": _as_character(grp.get("group_label")) or "",
                    "sort_order": _or(grp.get("sort_order"), i + 1),
                    "source_values": _pipe_text(grp.get("source_values")),
                    "rule_expression": _as_character(grp.get("rule_expression")) or "",
                })
        else:
            for i, grp in enumerate(cfg.get("groups") or []):
                grp = grp or {}
                group_rows.append({
                    "strat_key": key,
                    "group_label": _as_character(grp.get("label")) or "",
                    "sort_order": i + 1,
                    "source_values": _pipe_text(grp.get("values")),
                    "rule_expression": _as_character(grp.get("rule")) or "",
                })

    # The data sheet is raw data by contract: build_input_bundle_from_tables
    # refuses a custom grouping whose derived column already exists there, so an
    # Apply in the Workbook panel would raise. derive_variables regenerates them.
    if derived_strat_columns:
        data = data.drop(columns=[c for c in derived_strat_columns if c in data.columns])

    recode_rows = []
    for key, cfg in factor_recode_config.items():
        cfg = cfg or {}
        for lvl in cfg.get("levels") or []:
            lvl = lvl or {}
            recode_rows.append({
                "recode_key": key,
                "source_column": _as_character(cfg.get("source_column")) or "",
                "target_column": _as_character(cfg.get("target_column")) or "",
                "target_level": _as_character(lvl.get("label")) or "",
                "source_values": ", ".join(compact_chr(lvl.get("values"))),
                "notes": _as_character(cfg.get("notes")) or "",
            })

    tables = {
        "data": data,
        "metrics": pd.DataFrame(metric_rows),
        "metric_predictors": pd.DataFrame(mp_rows),
        "metric_stratifications": pd.DataFrame(ms_rows),
        "predictors": pd.DataFrame(pred_rows),
        "stratifications": pd.DataFrame(strat_rows),
        "strat_groups": pd.DataFrame(group_rows),
        "factor_recodes": pd.DataFrame(recode_rows),
    }
    return {
        name: (df if name == "data" else ensure_workbook_sheet_columns(df, name))
        for name, df in tables.items()
    }


_CURATED_METRIC_FIELDS = (
    "display_name", "units", "metric_family", "higher_is_better", "curve_form",
    "monotonic_linear", "preferred_transform", "min_sample_size",
    "best_subsets_allowed", "count_model", "stratification_mode",
    "include_in_summary", "missing_data_rule", "notes",
)


def overlay_metric_settings(tables, metric_config) -> dict:
    """Restore curated per-metric settings onto role-regenerated ``tables``.

    ``build_config_tables_from_roles`` knows only which columns are metrics, so
    it fills every setting with a default -- including ``higher_is_better`` TRUE
    for all. Rebuilding a project that way silently inverts every "more is
    worse" metric. This puts the project's own settings back for metrics it
    already knows about, matching on ``column_name`` (role-derived metric_keys
    are sanitized, so the column is the stable join). Genuinely new columns keep
    their defaults.
    """
    metric_config = metric_config or {}
    tables = dict(tables or {})
    metrics = tables.get("metrics")
    if metrics is None or len(metrics) == 0 or not metric_config:
        return tables

    by_column = {}
    for cfg in metric_config.values():
        col = _as_character((cfg or {}).get("column_name"))
        if col:
            by_column[col] = cfg

    # Role-regenerated tables carry only the columns the role step knows about, so
    # a curated field with no column would be silently dropped -- which is how a
    # two-sided metric lost its curve_form and read back as monotone-increasing.
    metrics = metrics.copy()
    for field in _CURATED_METRIC_FIELDS:
        if field not in metrics.columns and any(field in (c or {}) for c in by_column.values()):
            metrics[field] = ""
    for idx, col in zip(metrics.index, metrics.get("column_name", [])):
        cfg = by_column.get(_as_character(col))
        if not cfg:
            continue
        for field in _CURATED_METRIC_FIELDS:
            if field not in cfg or field not in metrics.columns:
                continue
            value = cfg[field]
            if isinstance(value, bool):
                value = "TRUE" if value else "FALSE"
            elif value is None:
                # A null direction is an assertion ("no monotone direction"), not a
                # missing value; the blank is what the reader turns back into None.
                value = ""
            metrics.at[idx, field] = value
    tables["metrics"] = metrics
    return tables


# --------------------------------------------------------------------------- #
# Bundle assembly (R/00_input_workbook.R:941-1023)
# --------------------------------------------------------------------------- #


def build_input_bundle_from_tables(tables) -> dict:
    """R/00_input_workbook.R:941-1023 — normalize + validate + build configs."""
    tables = normalize_workbook_tables(tables)
    validate_unique_keys(tables["metrics"], "metric_key", "metrics")
    validate_unique_keys(tables["stratifications"], "strat_key", "stratifications")
    validate_unique_keys(tables["predictors"], "predictor_key", "predictors")
    if len(tables["factor_recodes"]) > 0:
        # NOTE(parity): R checks is.na() only — non-NA blank strings pass.
        has_invalid = any(
            _is_na(rk) or _is_na(tl)
            for rk, tl in zip(
                tables["factor_recodes"]["recode_key"],
                tables["factor_recodes"]["target_level"],
            )
        )
        if has_invalid:
            raise ValueError(
                "Sheet 'factor_recodes' contains blank recode_key or target_level rows."
            )

    validate_foreign_keys(
        tables["metrics"],
        tables["metric_predictors"],
        tables["metric_stratifications"],
        tables["predictors"],
        tables["stratifications"],
    )

    raw_data_unmasked = tables["data"]
    site_mask_config = build_site_mask_config_from_workbook(
        tables["site_masks"],
        tables["site_mask_settings"],
        raw_data_unmasked,
    )
    site_mask_tables = site_mask_tables_from_config(raw_data_unmasked, site_mask_config)
    tables["site_masks"] = site_mask_tables["site_masks"]
    tables["site_mask_settings"] = site_mask_tables["site_mask_settings"]

    predictor_config = build_predictor_config_from_workbook(tables["predictors"])
    factor_recode_config = build_factor_recode_config_from_workbook(tables["factor_recodes"])
    strat_config = build_strat_config_from_workbook(
        tables["stratifications"],
        tables["strat_groups"],
        raw_data_unmasked,
        factor_recode_config,
    )
    bad_paired = [
        strat_key
        for strat_key, sc in strat_config.items()
        if sc.get("type") == "paired"
        and (sc.get("primary") not in strat_config or sc.get("secondary") not in strat_config)
    ]
    if bad_paired:
        raise ValueError(
            "Paired stratifications reference unknown base stratification keys: "
            + ", ".join(bad_paired)
        )
    metric_config = build_metric_config_from_workbook(
        tables["metrics"],
        tables["metric_predictors"],
        tables["metric_stratifications"],
    )

    raw_data = apply_global_site_masks(
        annotate_site_identity_columns(raw_data_unmasked, site_mask_config),
        site_mask_config,
    )

    logger.info(
        "Workbook loaded: %d analysis rows x %d columns | %d metrics | "
        "%d stratifications | %d predictors | %d recodes | %d masked sites",
        len(raw_data),
        raw_data.shape[1],
        len(metric_config),
        len(strat_config),
        len(predictor_config),
        len(factor_recode_config),
        len(_or(site_mask_config.get("masked_site_ids"), [])),
    )

    discipline_function_mapping = build_function_mappings_from_tables(
        tables, metric_keys=list(metric_config.keys())
    )
    mapping_covers_all_metrics = (
        bool(discipline_function_mapping.attrs.get("covers_all_metrics", False))
        if discipline_function_mapping is not None
        else False
    )

    return {
        "raw_data": raw_data,
        "metric_config": metric_config,
        "strat_config": strat_config,
        "predictor_config": predictor_config,
        "factor_recode_config": factor_recode_config,
        "site_mask_config": site_mask_config,
        "discipline_function_mapping": discipline_function_mapping,
        "mapping_covers_all_metrics": mapping_covers_all_metrics,
        "metadata": tables,
    }


# --------------------------------------------------------------------------- #
# Workbook writing (R/00_input_workbook.R:1025-1101)
# --------------------------------------------------------------------------- #


def _excel_cell(v):
    """One cell for openpyxl: NA -> None (blank), numpy scalars -> Python."""
    if _is_na(v):
        return None
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, np.str_):
        return str(v)
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def write_input_workbook(tables, output_path):
    """R/00_input_workbook.R:1052-1101. The R app shells out to Python
    (scripts/write_workbook_from_json.py) via a JSON payload; this port writes
    the same output directly with openpyxl — header row + values per sheet,
    NA cells blank, no styling.

    ``output_path`` may be a filesystem path (str/PathLike) or an open binary
    file-like object (e.g. an ``io.BytesIO`` from a Shiny download handler);
    openpyxl's ``save`` accepts either."""
    tables = normalize_workbook_tables(tables)

    workbook = Workbook()
    workbook.remove(workbook.active)

    for sheet_name, df in tables.items():
        worksheet = workbook.create_sheet(title=sheet_name)
        columns = list(df.columns)
        if columns:
            worksheet.append(columns)
        for row in df.itertuples(index=False, name=None):
            worksheet.append([_excel_cell(v) for v in row])

    if hasattr(output_path, "write"):
        # Already an open binary stream (download buffer) — write in place.
        workbook.save(output_path)
        return output_path

    output_path_obj = Path(output_path)
    output_path_obj.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path_obj)
    return output_path


# --------------------------------------------------------------------------- #
# Workbook reading (R/00_input_workbook.R:1103-1136, R/01_load_data.R)
# --------------------------------------------------------------------------- #


def read_input_workbook(input_path) -> dict:
    """R/00_input_workbook.R:1103-1136 — path -> InputBundle dict."""
    logger.info("Loading workbook input from %s", input_path)

    path = Path(input_path)
    if not path.exists():
        raise ValueError(f"Input workbook not found: {input_path}")

    if path.suffix.lower() != ".xlsx":
        raise ValueError("Input workbook must be an .xlsx file.")

    with pd.ExcelFile(path, engine="openpyxl") as xls:
        sheets = xls.sheet_names
        missing_sheets = [s for s in required_workbook_sheets() if s not in sheets]
        if missing_sheets:
            raise ValueError(
                "Workbook is missing required sheets: " + ", ".join(missing_sheets)
            )

        tables: dict[str, pd.DataFrame] = {}
        for sheet_name, spec in workbook_sheet_specs().items():
            if sheet_name not in sheets:
                tables[sheet_name] = ensure_workbook_sheet_columns(pd.DataFrame(), sheet_name)
            else:
                tables[sheet_name] = read_required_sheet(xls, sheet_name, spec["required"])

    return build_input_bundle_from_tables(tables)


def load_data(input_path) -> dict:
    """Port of R/01_load_data.R::load_data — thin wrapper around the parser."""
    return read_input_workbook(input_path)
