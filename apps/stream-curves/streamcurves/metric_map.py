"""Port of R/19_metric_map.R.

Loads ``config/metric_map.yaml`` (the SFARI function -> metric crosswalk) and
exposes the helpers the import wizard uses to pre-check default metrics and to
annotate compiled columns with their discipline/function/role.
"""

from __future__ import annotations

import re

import pandas as pd

from . import config
from .nrsa import load_nrsa_catalog
from .paths import DATA_DIR

ENTRY_COLUMNS = [
    "discipline",
    "function_name",
    "code",
    "source",
    "label",
    "default_selected",
    "role",
]

_ENTRIES_CACHE: pd.DataFrame | None = None


def load_metric_map(path: str | None = None) -> dict:
    """Load config/metric_map.yaml (cached). Raises ValueError when the file has
    no ``functions`` entries — matching R ``metric_map_load``."""
    if path is None:
        data = config.metric_map_raw()
    else:
        data = config.read_yaml(path) or {}
    funcs = data.get("functions") if isinstance(data, dict) else None
    if not funcs:
        raise ValueError("metric_map.yaml has no 'functions' entries.")
    return data


def metric_map_default_role(source) -> str:
    """NRSA measured indicators are metrics; landscape variables are usable as
    both a metric and a predictor (R lines 58-60)."""
    return "metric" if source == "nrsa" else "both"


def metric_map_entries() -> pd.DataFrame:
    """Flatten the map to DataFrame[discipline, function_name, code, source, label,
    default_selected, role] (R lines 64-93). Cached."""
    global _ENTRIES_CACHE
    if _ENTRIES_CACHE is not None:
        return _ENTRIES_CACHE
    mm = load_metric_map()
    rows: list[dict] = []
    for fe in mm.get("functions") or []:
        # R uses NA_character_ for an absent discipline/function; we surface Python
        # None so the DataFrame keeps an object (character) column, not "NA" text.
        disc = fe.get("discipline")
        fun = fe.get("function")
        for m in fe.get("metrics") or []:
            src = m.get("source")
            role = m.get("role")
            role = role if role is not None else metric_map_default_role(src)
            rows.append(
                {
                    "discipline": None if disc is None else str(disc),
                    "function_name": None if fun is None else str(fun),
                    "code": None if m.get("code") is None else str(m.get("code")),
                    "source": None if src is None else str(src),
                    "label": "" if m.get("label") is None else str(m.get("label")),
                    "default_selected": m.get("default_selected") is True,
                    "role": str(role).strip().lower(),
                }
            )
    if rows:
        df = pd.DataFrame(rows, columns=ENTRY_COLUMNS)
    else:
        df = pd.DataFrame(
            {
                "discipline": pd.Series([], dtype=object),
                "function_name": pd.Series([], dtype=object),
                "code": pd.Series([], dtype=object),
                "source": pd.Series([], dtype=object),
                "label": pd.Series([], dtype=object),
                "default_selected": pd.Series([], dtype=bool),
                "role": pd.Series([], dtype=object),
            }
        )
    _ENTRIES_CACHE = df
    return df


def _reset_cache() -> None:
    """Test hook: drop the flattened-entries cache."""
    global _ENTRIES_CACHE
    _ENTRIES_CACHE = None


def metric_map_default_codes(source) -> list[str]:
    """Codes pre-checked by default for a source (unique, order-preserved)."""
    df = metric_map_entries()
    sel = df[(df["source"] == source) & (df["default_selected"])]
    return list(dict.fromkeys(sel["code"].tolist()))


def metric_map_codes(source) -> list[str]:
    """All mapped codes for a source (unique, order-preserved)."""
    df = metric_map_entries()
    return list(dict.fromkeys(df[df["source"] == source]["code"].tolist()))


def _code_candidates(code) -> list[str]:
    """[code, code without ss_ prefix, code without ws/cat suffix] (unique)."""
    cands = [
        code,
        re.sub(r"^ss_", "", code, count=1),
        re.sub(r"(ws|cat)$", "", code, count=1),
    ]
    return list(dict.fromkeys(cands))


def metric_map_function_for(code) -> dict | None:
    """First {discipline, function_name} a code serves, or None. Tolerates a
    StreamStats ``ss_`` prefix and a StreamCAT ``ws``/``cat`` suffix (R 112-119)."""
    df = metric_map_entries()
    for cc in _code_candidates(code):
        hit = df[df["code"] == cc]
        if len(hit) > 0:
            return {
                "discipline": hit["discipline"].iloc[0],
                "function_name": hit["function_name"].iloc[0],
            }
    return None


def metric_map_function_label(code) -> str:
    """Short ``Discipline: Function`` tag for a code, or "" (R 122-125)."""
    ff = metric_map_function_for(code)
    if ff is None:
        return ""
    return f"{ff['discipline']}: {ff['function_name']}"


def metric_map_functions_for(code) -> list[dict]:
    """ALL ``{discipline, function_name}`` a code serves, in file order (a metric
    is listed under every function it informs). Same ``ss_``/``ws``/``cat`` code
    tolerance as :func:`metric_map_function_for`; returns the matches for the
    first candidate that hits, or ``[]``."""
    df = metric_map_entries()
    for cc in _code_candidates(code):
        hit = df[df["code"] == cc]
        if len(hit) > 0:
            return [
                {"discipline": r["discipline"], "function_name": r["function_name"]}
                for _, r in hit.iterrows()
            ]
    return []


def metric_map_role_for(code):
    """Role suggestion ``metric|predictor|both`` for a code, or None if the code
    is not in the map. Same code-naming tolerance as function_for (R 131-136)."""
    df = metric_map_entries()
    for cc in _code_candidates(code):
        hit = df[df["code"] == cc]
        if len(hit) > 0:
            return hit["role"].iloc[0]
    return None


# --------------------------------------------------------------------------- #
# Static source-catalog code lists needed by the validator.
# NOTE(parity): these mirror app/helpers/data_sources.R ``ss_core_bcs()`` and
# ``mmw_core_metrics()`` (data_sources.R is not in this port's module set; the
# validator only needs the *names*).
# --------------------------------------------------------------------------- #
def _ss_core_bcs_codes() -> list[str]:
    return ["DRNAREA", "PRECIP", "FOREST", "LC11DEV", "LC11IMP", "BSLDEM10ff"]


def _mmw_core_metrics_codes() -> list[str]:
    return [
        "mmw_developed_pct",
        "mmw_forest_pct",
        "mmw_agriculture_pct",
        "mmw_wetland_pct",
        "mmw_soil_cd_pct",
        "mmw_mean_slope_pct",
        "mmw_mean_elev_m",
        "mmw_annual_precip_cm",
        "mmw_da_sqmi",
    ]


def _site_engine_codes() -> list[str]:
    # Mirrors site_engine_source.SE_PREDICTORS (names only, kept import-light
    # like the other validator lists).
    return [
        "se_pctimpws",
        "se_agws",
        "se_rddensws",
        "se_runoffmm",
        "se_wsareasqkm",
        "se_damnrmstor",
        "se_kffactws",
    ]


def _streamcat_catalog_names() -> list[str]:
    try:
        df = pd.read_csv(DATA_DIR / "streamcat_metrics.csv")
        return df["name"].astype(str).tolist()
    except Exception:
        return []


def metric_map_validate() -> list[str]:
    """Validate the map against the offline source catalogs. Returns a list of
    warning strings (empty when clean); never raises (R lines 140-179)."""
    w: list[str] = []
    try:
        df = metric_map_entries()
    except Exception:
        df = None
    if df is None or len(df) == 0:
        return ["metric_map: no entries loaded"]

    # 20 discipline/function pairs expected.
    keys = pd.unique(df["discipline"].astype(str) + " / " + df["function_name"].astype(str))
    if len(keys) != 20:
        w.append(
            f"metric_map: expected 20 discipline/function pairs, found {len(keys)}"
        )

    valid_src = {"nrsa", "streamcat", "streamstats", "mmw", "site_engine"}
    bad_src = [s for s in pd.unique(df["source"]) if s not in valid_src]
    if bad_src:
        w.append("metric_map: unknown source(s): " + ", ".join(str(s) for s in bad_src))

    valid_role = {"metric", "predictor", "both"}
    bad_role = [r for r in pd.unique(df["role"]) if r not in valid_role]
    if bad_role:
        w.append("metric_map: unknown role(s): " + ", ".join(str(r) for r in bad_role))

    try:
        nrsa_names = load_nrsa_catalog()["name"].astype(str).tolist()
    except Exception:
        nrsa_names = []
    known = {
        "nrsa": nrsa_names,
        "streamcat": _streamcat_catalog_names(),
        "streamstats": _ss_core_bcs_codes(),
        "mmw": _mmw_core_metrics_codes(),
        "site_engine": _site_engine_codes(),
    }
    for _, row in df.iterrows():
        src = row["source"]
        code = row["code"]
        ref = known.get(src)
        if ref is not None and len(ref) and code not in ref:
            w.append(f"metric_map: code '{code}' not found in {src} catalog")
    return w
