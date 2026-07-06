"""Port of R/13_oh_parameter_map.R.

The SQT parameter map (``config/oh_parameter_map.yaml``) plus the runtime-editable
Discipline / Function / Metric mapping helpers. The mapping is carried as a pandas
DataFrame with columns ``[metric_key, discipline, function_label, sort_order]``
(the R tibble ``rv$discipline_function_mapping``). An NA/blank ``metric_key`` marks
an empty function bucket; a ``lib:<id>`` key is a STAF master-library
"planned / no-data" assignment.

Pure module: no shiny, no reactivity.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from . import config

MAPPING_COLUMNS = ["metric_key", "discipline", "function_label", "sort_order"]


# --------------------------------------------------------------------------- #
# Presence helpers (R ``!is.na(x) & nzchar(x)``)
# --------------------------------------------------------------------------- #
def _present(v: Any) -> bool:
    """True when ``v`` is neither NA/None nor the empty string.

    Mirrors R's ``!is.na(x) & nzchar(x)`` guard (note: no trimming — a
    whitespace-only string is *present*, matching ``nzchar``)."""
    if v is None:
        return False
    try:
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        pass
    return str(v) != ""


def _present_mask(s: pd.Series) -> pd.Series:
    if len(s) == 0:
        return pd.Series([], dtype=bool)
    return s.map(_present).astype(bool)


def _clean_keys(metric_keys) -> list[str]:
    """R ``metric_keys[!is.na & nzchar]`` then ``unique`` (order-preserving)."""
    out: list[str] = []
    seen: set = set()
    for k in list(metric_keys):
        if not _present(k):
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


# --------------------------------------------------------------------------- #
# oh_parameter_map.yaml loader + accessors
# --------------------------------------------------------------------------- #
def load_oh_parameter_map(path: str | None = None) -> dict:
    """Load config/oh_parameter_map.yaml (cached). Raises ValueError when the file
    has no ``metrics`` entries — matching R ``oh_parameter_map_load``."""
    if path is None:
        data = config.oh_parameter_map_raw()
    else:
        data = config.read_yaml(path) or {}
    metrics = data.get("metrics") if isinstance(data, dict) else None
    if not metrics:
        raise ValueError("oh_parameter_map.yaml has no 'metrics' entries.")
    return data


def _oh_metrics() -> dict:
    return load_oh_parameter_map().get("metrics") or {}


def oh_covered_metrics() -> list[str]:
    return list(_oh_metrics().keys())


def oh_category_order() -> list[str]:
    data = load_oh_parameter_map()
    order = data.get("category_order")
    if order:
        return list(order)
    seen: list[str] = []
    for m in _oh_metrics().values():
        cat = (m or {}).get("functional_category")
        if cat not in seen:
            seen.append(cat)
    return seen


def oh_metric_entry(metric_key: str) -> dict | None:
    return _oh_metrics().get(metric_key)


def oh_functional_category(metric_key: str):
    entry = oh_metric_entry(metric_key) or {}
    return entry.get("functional_category")


def oh_function_parameter(metric_key: str):
    entry = oh_metric_entry(metric_key) or {}
    return entry.get("function_based_parameter")


def oh_units_display(metric_key: str, metric_config: dict | None = None):
    entry = oh_metric_entry(metric_key) or {}
    display = entry.get("units_display")
    if display is not None and str(display) != "":
        return display
    if metric_config is not None:
        mc = metric_config.get(metric_key)
        if isinstance(mc, dict) and mc.get("units") is not None:
            return mc.get("units")
    return None


def oh_reference_notes(metric_key: str) -> str:
    entry = oh_metric_entry(metric_key) or {}
    notes = entry.get("reference_notes")
    return notes if notes is not None else ""


def oh_data_sources(metric_key: str) -> list:
    entry = oh_metric_entry(metric_key) or {}
    src = entry.get("data_sources")
    if src is None:
        return []
    if isinstance(src, (list, tuple)):
        return list(src)
    return [src]


def oh_metrics_for_category(category, metric_keys=None) -> list[str]:
    keys = oh_covered_metrics() if metric_keys is None else list(metric_keys)
    return [m for m in keys if oh_functional_category(m) == category]


def oh_metrics_for_parameter(category, parameter, metric_keys=None) -> list[str]:
    """Metrics in a functional category that map to a given function-based
    parameter (used by the Science Support Document grouping)."""
    return [
        m
        for m in oh_metrics_for_category(category, metric_keys)
        if oh_function_parameter(m) == parameter
    ]


# --------------------------------------------------------------------------- #
# Discipline / Function / Metric mapping
# --------------------------------------------------------------------------- #
def fixed_discipline_order() -> list[str]:
    return ["Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology"]


def _empty_mapping() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "metric_key": pd.Series([], dtype=object),
            "discipline": pd.Series([], dtype=object),
            "function_label": pd.Series([], dtype=object),
            "sort_order": pd.Series([], dtype="int64"),
        }
    )


def blank_function_mapping_scaffold(metric_keys) -> pd.DataFrame:
    """One row per (present, unique) metric_key with NA discipline/function_label.

    Replaces the former YAML-seeded scaffold — YAML is no longer the source of
    truth for discipline/function assignments (R lines 139-156)."""
    keys = _clean_keys(metric_keys)
    if not keys:
        return _empty_mapping()
    return pd.DataFrame(
        {
            "metric_key": pd.Series(keys, dtype=object),
            "discipline": pd.Series([None] * len(keys), dtype=object),
            "function_label": pd.Series([None] * len(keys), dtype=object),
            "sort_order": pd.Series(range(1, len(keys) + 1), dtype="int64"),
        }
    )


def function_mapping_full_coverage(mapping, required_metric_keys) -> bool:
    """Export gate: every present required key must appear with a present
    discipline AND function_label (R lines 201-212)."""
    required_list = list(required_metric_keys)
    if mapping is None or not isinstance(mapping, pd.DataFrame) or len(mapping) == 0:
        # NOTE(parity): early return uses the *raw* required length (R:204).
        return len(required_list) == 0
    required = _clean_keys(required_list)
    if not required:
        return True
    assigned = mapping[
        _present_mask(mapping["metric_key"])
        & _present_mask(mapping["discipline"])
        & _present_mask(mapping["function_label"])
    ]
    assigned_keys = set(assigned["metric_key"].tolist())
    return all(k in assigned_keys for k in required)


def validate_discipline_function_mapping(mapping) -> bool:
    """Invariants (R lines 214-257):
    * every fully-specified function_label belongs to exactly one discipline
      (case-insensitive), and
    * a metric is not assigned to the same function twice — the
      (metric_key, function_label) pair is unique.
    Raises ValueError on violation; returns True otherwise."""
    if mapping is None:
        return True
    missing = [c for c in MAPPING_COLUMNS if c not in mapping.columns]
    if missing:
        raise ValueError(
            "discipline_function_mapping missing columns: " + ", ".join(missing)
        )
    if len(mapping) == 0:
        return True

    # function_label -> exactly one discipline (case-insensitive).
    pair_mask = _present_mask(mapping["function_label"]) & _present_mask(mapping["discipline"])
    fn_disc = mapping.loc[pair_mask, ["function_label", "discipline"]].drop_duplicates()
    if len(fn_disc) > 0:
        counts: dict[str, int] = {}
        for label in fn_disc["function_label"]:
            key = str(label).lower()
            counts[key] = counts.get(key, 0) + 1
        multi = [name for name, c in counts.items() if c > 1]
        if multi:
            raise ValueError(
                "function_mappings: function name(s) assigned to more than one "
                "discipline: " + ", ".join(multi)
            )

    # (metric_key, function_label) pair uniqueness.
    assigned = mapping[
        _present_mask(mapping["metric_key"]) & _present_mask(mapping["function_label"])
    ]
    if len(assigned) > 0:
        pair = [
            f"{mk}\r{str(fl).strip().lower()}"
            for mk, fl in zip(assigned["metric_key"], assigned["function_label"])
        ]
        dup_pos = pd.Series(pair).duplicated().to_numpy()
        if dup_pos.any():
            mk_series = assigned["metric_key"].to_numpy()
            dups = list(dict.fromkeys(mk_series[dup_pos].tolist()))
            raise ValueError(
                "function_mappings: metric assigned to the same function more than "
                "once: " + ", ".join(str(d) for d in dups)
            )
    return True


def metric_usage_counts(mapping) -> dict[str, int]:
    """Count of DISTINCT functions each assigned metric is used in, keyed by
    metric_key (R lines 263-279). Sorted by key like R's ``table``."""
    if mapping is None or not isinstance(mapping, pd.DataFrame) or len(mapping) == 0:
        return {}
    mask = _present_mask(mapping["metric_key"]) & _present_mask(mapping["function_label"])
    rows = mapping[mask]
    if len(rows) == 0:
        return {}
    uniq = {
        (str(mk), str(fl).strip().lower())
        for mk, fl in zip(rows["metric_key"], rows["function_label"])
    }
    counts: dict[str, int] = {}
    for mk, _ in uniq:
        counts[mk] = counts.get(mk, 0) + 1
    return {k: counts[k] for k in sorted(counts)}


def function_label_owner_discipline(mapping, function_label):
    """Case-insensitive: discipline owning ``function_label`` in the mapping, or
    None (R lines 284-291)."""
    if mapping is None or len(mapping) == 0:
        return None
    if not _present(function_label):
        return None
    key = str(function_label).strip().lower()
    labels = mapping["function_label"].map(
        lambda v: str(v).strip().lower() if _present(v) else None
    )
    hits = mapping[labels == key]
    if len(hits) == 0:
        return None
    return hits["discipline"].iloc[0]


def realign_discipline_function_mapping(mapping, metric_keys) -> dict:
    """Drop rows for absent metrics; add blank-scaffold rows for missing ones.
    Preserves user assignments, ``lib:`` rows, and empty buckets verbatim
    (R lines 293-329). Returns ``{"mapping", "added", "dropped"}``."""
    keys = _clean_keys(metric_keys)
    if mapping is None or not isinstance(mapping, pd.DataFrame) or len(mapping) == 0:
        return {
            "mapping": blank_function_mapping_scaffold(keys),
            "added": list(keys),
            "dropped": [],
        }

    mk = mapping["metric_key"]
    is_lib = mk.map(lambda v: _present(v) and str(v).startswith("lib:")).astype(bool)
    present = _present_mask(mk)
    lib_rows = mapping[is_lib]
    existing_named = mapping[(~is_lib) & present]
    empty_buckets = mapping[(~is_lib) & (~present)]

    existing_keys = existing_named["metric_key"].tolist()
    keyset = set(keys)
    dropped = [k for k in dict.fromkeys(existing_keys) if k not in keyset]
    kept = existing_named[existing_named["metric_key"].isin(keyset)]
    kept_keys = set(kept["metric_key"].tolist())
    missing_keys = [k for k in keys if k not in kept_keys]

    frames = [kept]
    if missing_keys:
        frames.append(blank_function_mapping_scaffold(missing_keys))
    frames.extend([lib_rows, empty_buckets])
    kept = pd.concat([f for f in frames if len(f) > 0], ignore_index=True) if any(
        len(f) > 0 for f in frames
    ) else _empty_mapping()

    if len(kept) > 0:
        kept = kept.reindex(columns=_union_columns(mapping))
        kept["sort_order"] = pd.Series(range(1, len(kept) + 1), dtype="int64")
    return {"mapping": kept, "added": list(missing_keys), "dropped": list(dropped)}


def _union_columns(mapping: pd.DataFrame) -> list[str]:
    cols = list(MAPPING_COLUMNS)
    for c in mapping.columns:
        if c not in cols:
            cols.append(c)
    return cols


# --------------------------------------------------------------------------- #
# Resolvers (mapping is source of truth; fall back to YAML when mapping is None)
# --------------------------------------------------------------------------- #
def _scalar_na(metric_key) -> bool:
    if metric_key is None:
        return True
    try:
        return bool(pd.isna(metric_key))
    except (TypeError, ValueError):
        return False


def _resolve_field(metric_key, mapping, column):
    if _scalar_na(metric_key):
        return None
    if mapping is not None and isinstance(mapping, pd.DataFrame) and len(mapping) > 0:
        hit = mapping[_present_mask(mapping["metric_key"]) & (mapping["metric_key"] == metric_key)]
        if len(hit) > 0:
            val = hit[column].iloc[0]  # [1] = primary (lowest sort_order) when reused
            if not _present(val):
                return None
            return val
        return None
    # legacy fallback
    if column == "discipline":
        return oh_functional_category(metric_key)
    return oh_function_parameter(metric_key)


def resolve_metric_discipline(metric_key, mapping=None):
    return _resolve_field(metric_key, mapping, "discipline")


def resolve_metric_function(metric_key, mapping=None):
    return _resolve_field(metric_key, mapping, "function_label")


def resolved_category_order(mapping=None) -> list[str]:
    ordered = fixed_discipline_order()
    if mapping is None or not isinstance(mapping, pd.DataFrame) or len(mapping) == 0:
        return ordered
    active: list[str] = []
    for d in mapping["discipline"]:
        if _present(d) and d not in active:
            active.append(d)
    keep = [d for d in ordered if d in active]
    extras = [d for d in active if d not in ordered]
    return keep + extras


def metrics_for_resolved_discipline(discipline, mapping=None, metric_keys=None) -> list[str]:
    if mapping is not None and isinstance(mapping, pd.DataFrame) and len(mapping) > 0:
        mask = (
            _present_mask(mapping["metric_key"])
            & _present_mask(mapping["discipline"])
            & (mapping["discipline"] == discipline)
        )
        rows = mapping[mask]
        if metric_keys is not None:
            rows = rows[rows["metric_key"].isin(list(metric_keys))]
        if len(rows) == 0:
            return []
        rows = rows.sort_values(
            ["function_label", "sort_order"], na_position="last", kind="mergesort"
        )
        return rows["metric_key"].tolist()
    keys = oh_covered_metrics() if metric_keys is None else list(metric_keys)
    return oh_metrics_for_category(discipline, keys)


def functions_for_resolved_discipline(discipline, mapping=None, include_empty=True) -> list[str]:
    if mapping is not None and isinstance(mapping, pd.DataFrame) and len(mapping) > 0:
        rows = mapping[_present_mask(mapping["discipline"]) & (mapping["discipline"] == discipline)]
        if not include_empty:
            rows = rows[_present_mask(rows["metric_key"])]
        labels: list[str] = []
        for v in rows["function_label"]:
            if _present(v) and v not in labels:
                labels.append(v)
        return labels
    keys = oh_covered_metrics()
    labels_out: list[str] = []
    for k in keys:
        disc = oh_functional_category(k)
        lab = oh_function_parameter(k)
        if disc == discipline and _present(lab) and lab not in labels_out:
            labels_out.append(lab)
    return labels_out
