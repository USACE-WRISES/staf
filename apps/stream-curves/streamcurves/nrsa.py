"""Port of app/helpers/nrsa_metrics.R.

Bundled NRSA 2018-19 measured-metric catalog + per-site values. Metric columns are
namespaced by a category prefix (chem_ / phab_ / bent_ / fish_ / land_). Values are
keyed by ``site_id`` (SITE_ID).

NOTE(parity): the R app loads ``data/nrsa_metrics.rds``; in this repo the same
1920x789 frame is ``data/nrsa_metrics.parquet`` (site_id as a string column).
"""

from __future__ import annotations

import re

import pandas as pd

from .paths import DATA_DIR

CATALOG_COLUMNS = ["name", "raw_name", "category", "label", "units", "core"]

_CACHE: dict[str, pd.DataFrame] = {}


def _as_logical(v) -> bool | None:
    """R ``as.logical`` for the ``core`` column (TRUE/FALSE/T/F, any case)."""
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip().upper()
    if s in ("TRUE", "T", "1"):
        return True
    if s in ("FALSE", "F", "0"):
        return False
    return None


def load_nrsa_catalog(path=None) -> pd.DataFrame:
    """Metric catalog: DataFrame[name, raw_name, category, label, units, core].
    Unreadable file -> empty frame with those columns (R lines 10-22). Cached."""
    if path is None:
        path = DATA_DIR / "nrsa_metric_catalog.csv"
    key = f"cat:{path}"
    if key in _CACHE:
        return _CACHE[key]
    try:
        cat_df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    except Exception:
        cat_df = None
    if cat_df is None:
        cat_df = pd.DataFrame({c: pd.Series([], dtype=object) for c in CATALOG_COLUMNS})
    else:
        if "core" in cat_df.columns:
            cat_df["core"] = cat_df["core"].map(_as_logical)
    _CACHE[key] = cat_df
    return cat_df


def load_nrsa_values(path=None) -> pd.DataFrame:
    """Per-site values: DataFrame[site_id, <namespaced metric columns...>].
    Unreadable file -> DataFrame(site_id=[]) (R lines 25-31). Cached."""
    if path is None:
        path = DATA_DIR / "nrsa_metrics.parquet"
    key = f"val:{path}"
    if key in _CACHE:
        return _CACHE[key]
    try:
        v = pd.read_parquet(path)
    except Exception:
        v = pd.DataFrame({"site_id": pd.Series([], dtype=object)})
    _CACHE[key] = v
    return v


def nrsa_category_label(code) -> str:
    """Category code -> human source label (R lines 34-39)."""
    return {
        "chem": "NRSA: Water chemistry",
        "phab": "NRSA: Physical habitat",
        "bent": "NRSA: Benthic",
        "fish": "NRSA: Fish",
        "land": "NRSA: Landscape",
    }.get(code, "NRSA")


def nrsa_source_for(metric_name) -> str:
    """Provenance source label for a namespaced NRSA metric column."""
    prefix = re.sub(r"_.*$", "", str(metric_name))
    return nrsa_category_label(prefix)


def attach_nrsa_metrics(sites, selected, nrsa_values, site_id_col="site_id"):
    """Left-join selected NRSA metric columns onto ``sites`` by site_id (missing ->
    NA). Only columns present in ``nrsa_values`` are attached; ``selected`` order is
    preserved (R lines 46-52)."""
    value_cols = [c for c in nrsa_values.columns if c != "site_id"]
    selected = [c for c in selected if c in value_cols]
    if not selected or len(sites) == 0 or site_id_col not in sites.columns:
        return sites
    sites = sites.copy()
    lookup = nrsa_values.drop_duplicates("site_id").copy()
    lookup.index = lookup["site_id"].astype(str)
    keys = sites[site_id_col].astype(str)
    for m in selected:
        sites[m] = keys.map(lookup[m]).to_numpy()
    return sites


def nrsa_catalog_role_for(code):
    """Classify-role suggestion from the NRSA catalog category (R lines 61-79):
    measured indicators -> "metric"; landscape -> "predictor"; else None. Matches
    the namespaced ``name``; a bare ``raw_name`` is honored only when it maps to a
    single category."""
    try:
        cat_df = load_nrsa_catalog()
    except Exception:
        cat_df = None
    if cat_df is None or len(cat_df) == 0:
        return None
    hit = cat_df[cat_df["name"] == code]
    if len(hit) == 0:
        rhit = cat_df[cat_df["raw_name"] == code]
        if len(rhit) and rhit["category"].nunique() == 1:
            hit = rhit
    if len(hit) == 0:
        return None
    category = hit["category"].iloc[0]
    if category in ("Water chemistry", "Physical habitat",
                    "Benthic macroinvertebrates", "Fish"):
        return "metric"
    if category == "Landscape":
        return "predictor"
    return None
