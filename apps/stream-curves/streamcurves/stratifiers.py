"""National stratifier candidates for the Regional Analysis Agent.

The agent used to publish assessments with no stratification analysis at all:
``strat_config`` was null, no metric carried ``allowed_stratifications``, and so
the Exploratory, Cross-Metric and Verification tabs of every reopened assessment
truthfully reported that nothing had been run. There was nothing to screen
because no candidate stratification had ever been defined.

This module supplies that missing input. It reads
``config/national_stratifier_registry.yaml``, materializes each candidate's class
column from data already in the offline NRSA bundle, and decides which candidates
are eligible for the region at hand -- recording a reason for every one it
rejects, so "why was slope not screened here" is answerable from the run record
without re-running anything.

Pure: no shiny, no network, no global state. Breakpoints are declared constants,
so shuffling or subsetting the input rows cannot change a class assignment.
"""

from __future__ import annotations

import logging

import pandas as pd

from . import derive
from .config import read_yaml
from .paths import CONFIG_DIR
from .workbook import auto_pairwise_values

logger = logging.getLogger("streamcurves")

REGISTRY_PATH = CONFIG_DIR / "national_stratifier_registry.yaml"

DEFAULT_MIN_GROUP_SIZE = 5

#: One row per registered candidate, eligible or not.
LEDGER_COLUMNS = [
    "stratification",
    "display_name",
    "source_column",
    "source_present",
    "n_non_null",
    "n_levels_declared",
    "n_levels_populated",
    "populated_levels",
    "level_counts",
    "min_populated_n",
    "min_group_size",
    "eligible",
    "exclusion_reason",
]

# Fixed vocabulary so the reasons stay comparable across ecoregions.
REASON_SOURCE_MISSING = "source_column_missing"
REASON_ALL_NULL = "all_null"
REASON_MATERIALIZE_FAILED = "materialization_failed"
REASON_TOO_FEW_LEVELS = "fewer_than_2_populated_levels"
REASON_SPARSE_LEVEL = "sparse_level"


def load_national_registry(path=REGISTRY_PATH) -> dict:
    """The registry as authored. ``candidates`` entries are runtime strat_config
    entries already, so nothing here reshapes them."""
    registry = read_yaml(path) or {}
    return {
        "version": registry.get("version"),
        "applies_to": registry.get("applies_to"),
        "source_dataset": registry.get("source_dataset"),
        "candidates": registry.get("candidates") or {},
        "rejected": registry.get("rejected") or {},
    }


def source_columns(registry: dict) -> list[str]:
    """Raw columns the candidates need. The agent attaches these to the data
    before materializing, because none of them is a response metric and so none
    is pulled in by the metric config."""
    out: list[str] = []
    for cfg in (registry.get("candidates") or {}).values():
        column = (cfg or {}).get("source_column")
        if column and column not in out:
            out.append(column)
    return out


def _class_column(cfg: dict, strat_key: str) -> str:
    return cfg.get("column_name") or cfg.get("derived_column_name") or strat_key


def materialize_candidates(data: pd.DataFrame, registry: dict) -> tuple[pd.DataFrame, dict]:
    """Add each candidate's class column to ``data``.

    Materializes one candidate at a time so a single unusable source column
    (non-numeric, or values no rule matches) excludes just that candidate
    instead of aborting the run. Returns the augmented frame and
    ``{strat_key: reason}`` for the candidates that could not be built.
    """
    data = data.copy()
    skipped: dict[str, str] = {}

    for strat_key, cfg in (registry.get("candidates") or {}).items():
        cfg = cfg or {}
        source = cfg.get("source_column")
        if not source or source not in data.columns:
            skipped[strat_key] = REASON_SOURCE_MISSING
            continue
        if data[source].notna().sum() == 0:
            skipped[strat_key] = REASON_ALL_NULL
            continue
        try:
            built = derive.materialize_custom_stratifications(data, {strat_key: cfg})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Stratifier '%s' could not be materialized: %s", strat_key, exc)
            skipped[strat_key] = REASON_MATERIALIZE_FAILED
            continue
        column = _class_column(cfg, strat_key)
        # feasibility.assess_feasibility counts declared-but-unused categories as
        # zero-size groups and calls the stratifier infeasible, while
        # screening._factor drops them first and disagrees. Dropping empty
        # categories here is what keeps the two engines describing the same thing.
        data[column] = built[column].cat.remove_unused_categories()

    return data, skipped


def assess_eligibility(
    data: pd.DataFrame,
    registry: dict,
    *,
    skipped: dict | None = None,
    min_group_size_default: int = DEFAULT_MIN_GROUP_SIZE,
) -> pd.DataFrame:
    """One row per registered candidate, eligible or not, with a reason.

    A candidate is eligible when its class column has at least two populated
    levels and every populated level clears ``min_group_size``. A level that is
    populated but sparse fails the candidate rather than being quietly dropped.
    """
    skipped = skipped or {}
    rows = []

    for strat_key, cfg in (registry.get("candidates") or {}).items():
        cfg = cfg or {}
        source = cfg.get("source_column")
        column = _class_column(cfg, strat_key)
        min_group_size = int(cfg.get("min_group_size") or min_group_size_default)
        declared = list(cfg.get("levels") or [])

        row = {
            "stratification": strat_key,
            "display_name": cfg.get("display_name") or strat_key,
            "source_column": source,
            "source_present": bool(source and source in data.columns),
            "n_non_null": 0,
            "n_levels_declared": len(declared),
            "n_levels_populated": 0,
            "populated_levels": "",
            "level_counts": "",
            "min_populated_n": 0,
            "min_group_size": min_group_size,
            "eligible": False,
            "exclusion_reason": None,
        }

        if strat_key in skipped or column not in data.columns:
            row["exclusion_reason"] = skipped.get(strat_key, REASON_SOURCE_MISSING)
            rows.append(row)
            continue

        counts = data[column].value_counts(dropna=True)
        counts = counts[counts > 0]
        row["n_non_null"] = int(counts.sum())
        row["n_levels_populated"] = int(len(counts))
        # Declared order, not count order, so the record is stable run to run.
        ordered = [lvl for lvl in declared if lvl in counts.index]
        ordered += [lvl for lvl in counts.index if lvl not in ordered]
        row["populated_levels"] = "|".join(str(lvl) for lvl in ordered)
        row["level_counts"] = "|".join(f"{lvl}={int(counts[lvl])}" for lvl in ordered)

        if len(counts) < 2:
            row["exclusion_reason"] = REASON_TOO_FEW_LEVELS
            rows.append(row)
            continue

        row["min_populated_n"] = int(counts.min())
        sparse = [lvl for lvl in ordered if int(counts[lvl]) < min_group_size]
        if sparse:
            first = sparse[0]
            row["exclusion_reason"] = f"{REASON_SPARSE_LEVEL}:{first}(n={int(counts[first])})"
            rows.append(row)
            continue

        row["eligible"] = True
        rows.append(row)

    ledger = pd.DataFrame(rows, columns=LEDGER_COLUMNS)
    # DataFrame construction turns a missing reason into NaN, which serializes as
    # a float into the run manifest. An eligible candidate has no reason at all.
    ledger["exclusion_reason"] = ledger["exclusion_reason"].astype(object).where(
        ledger["exclusion_reason"].notna(), None
    )
    return ledger


def eligible_keys(ledger: pd.DataFrame) -> list[str]:
    """Eligible candidates in registry order. A list, never a set: the screening
    work list is built from it and must not reorder between runs."""
    if ledger is None or len(ledger) == 0:
        return []
    return [str(k) for k in ledger.loc[ledger["eligible"], "stratification"]]


def strat_config_for(registry: dict, keys, data: pd.DataFrame) -> dict:
    """Runtime ``strat_config`` for the eligible candidates.

    ``levels`` is narrowed to the levels the region actually populates, matching
    the pruned class column, and pairwise comparisons are derived from that
    narrowed set. ``group_definitions`` stays complete because the rules have to
    keep partitioning the reals; a class with no observations simply assigns no
    rows.
    """
    candidates = registry.get("candidates") or {}
    out: dict[str, dict] = {}

    for strat_key in keys or []:
        cfg = candidates.get(strat_key)
        if not cfg:
            continue
        cfg = dict(cfg)
        column = _class_column(cfg, strat_key)
        declared = list(cfg.get("levels") or [])
        if column in data.columns:
            counts = data[column].value_counts(dropna=True)
            populated = [lvl for lvl in declared if int(counts.get(lvl, 0)) > 0]
            if populated:
                cfg["levels"] = populated
        cfg["pairwise_comparisons"] = auto_pairwise_values(cfg["levels"])
        cfg.setdefault("derived_column_name", column)
        cfg["column_name"] = column
        out[strat_key] = cfg

    return out
