"""Port of R/21_staf_metric_library.R.

Loads ``config/staf_metric_library.json`` (the bundled master Function -> Metric
default mapping) and derives each metric's discipline from its function's category
in ``config/staf_functions.json``. Feeds the Discipline -> Function -> Metric
workbench prefill and the right-hand library palette.
"""

from __future__ import annotations

import logging

import pandas as pd

from . import config
from .mapping import blank_function_mapping_scaffold, fixed_discipline_order

_logger = logging.getLogger("streamcurves")

ENTRY_COLUMNS = [
    "library_id",
    "label",
    "function_id",
    "function_name",
    "discipline",
    "is_primary",
    "app_metric_key",
    "catalog_source",
    "source_citation",
    "prefill",
]

_ENTRIES_CACHE: pd.DataFrame | None = None
_FN_LOOKUP_CACHE: dict | None = None
_CANON_FN_CACHE: dict | None = None


def load_staf_metric_library(path: str | None = None) -> dict:
    """Load config/staf_metric_library.json (cached). Raises ValueError when the
    file has no ``metrics`` entries — matching R ``staf_metric_library_load``."""
    if path is None:
        data = config.staf_metric_library_raw()
    else:
        data = config.read_json(path)
    metrics = data.get("metrics") if isinstance(data, dict) else None
    if not metrics:
        raise ValueError("staf_metric_library.json has no 'metrics' entries.")
    return data


def staf_metric_library(path: str | None = None) -> dict:
    return load_staf_metric_library(path)


def _staf_function_lookup() -> dict:
    """Canonical function id -> {name, discipline} from staf_functions.json."""
    global _FN_LOOKUP_CACHE
    if _FN_LOOKUP_CACHE is not None:
        return _FN_LOOKUP_CACHE
    fns = config.staf_functions_raw().get("functions") or []
    lk: dict = {}
    for f in fns:
        lk[f["id"]] = {"name": f["name"], "discipline": f["category"]}
    _FN_LOOKUP_CACHE = lk
    return lk


def _canonical_function_index() -> dict:
    """``lower(name | alias) -> {"id","name","discipline"}`` from staf_functions.json
    (cached). The ``aliases`` field reconciles known label spellings (e.g. metric_map
    uses "Water and soil quality" for the canonical "Water & soil quality")."""
    global _CANON_FN_CACHE
    if _CANON_FN_CACHE is not None:
        return _CANON_FN_CACHE
    fns = config.staf_functions_raw().get("functions") or []
    idx: dict = {}
    for f in fns:
        canon = {"id": f.get("id"), "name": f.get("name"), "discipline": f.get("category")}
        for nm in [f.get("name"), *(f.get("aliases") or [])]:
            if nm is None:
                continue
            key = str(nm).strip().lower()
            if key:
                idx.setdefault(key, canon)
    _CANON_FN_CACHE = idx
    return idx


def staf_canonical_function(name) -> dict | None:
    """Resolve a function label (canonical name OR a staf_functions.json alias,
    case-insensitive) to ``{"id","name","discipline"}``, or ``None`` if unknown."""
    if name is None:
        return None
    try:
        if pd.isna(name):
            return None
    except (TypeError, ValueError):
        pass
    key = str(name).strip().lower()
    if not key:
        return None
    return _canonical_function_index().get(key)


def staf_function_meta() -> pd.DataFrame:
    """Canonical 20-function metadata: DataFrame[id, name, discipline, order] in
    file (canonical) order (R lines 83-94)."""
    lk = _staf_function_lookup()
    ids = list(lk.keys())
    return pd.DataFrame(
        {
            "id": ids,
            "name": [lk[i]["name"] for i in ids],
            "discipline": [lk[i]["discipline"] for i in ids],
            "order": list(range(1, len(ids) + 1)),
        }
    )


def staf_functions_by_discipline() -> dict[str, list[str]]:
    """Ordered ``discipline -> [function names]`` in canonical order for the fixed
    workbench skeleton (R lines 98-106)."""
    meta = staf_function_meta()
    try:
        discs = fixed_discipline_order()
    except Exception:
        discs = ["Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology"]
    out: dict[str, list[str]] = {}
    for d in discs:
        out[d] = meta.loc[meta["discipline"] == d, "name"].tolist()
    return out


def staf_metric_library_entries() -> pd.DataFrame:
    """Explode the library into one row per (metric x function) assignment
    (primary + additional_function_ids), joined to function name + derived
    discipline (R lines 112-159). Cached."""
    global _ENTRIES_CACHE
    if _ENTRIES_CACHE is not None:
        return _ENTRIES_CACHE
    data = load_staf_metric_library()
    lk = _staf_function_lookup()
    rows: list[dict] = []

    def add_row(m: dict, fid, primary: bool) -> None:
        fn = lk.get(fid)
        if fn is None:
            _logger.warning(
                "staf_metric_library: unknown function id '%s' for metric '%s' — skipped.",
                fid,
                m.get("library_id"),
            )
            return
        cites = m.get("source_citations") or []
        rows.append(
            {
                "library_id": m.get("library_id"),
                "label": m.get("label") or m.get("library_id"),
                "function_id": fid,
                "function_name": fn["name"],
                "discipline": fn["discipline"],
                "is_primary": bool(primary),
                "app_metric_key": m.get("app_metric_key"),
                "catalog_source": m.get("catalog_source"),
                "source_citation": "; ".join(str(c) for c in cites),
                "prefill": bool(m.get("prefill") if m.get("prefill") is not None else True),
            }
        )

    for m in data.get("metrics") or []:
        add_row(m, m.get("primary_function_id"), True)
        for fid in m.get("additional_function_ids") or []:
            add_row(m, fid, False)

    if rows:
        entries = pd.DataFrame(rows, columns=ENTRY_COLUMNS)
    else:
        entries = pd.DataFrame(
            {c: pd.Series([], dtype=(bool if c in ("is_primary", "prefill") else object))
             for c in ENTRY_COLUMNS}
        )

    expected = data.get("functionPairCount")
    if expected is not None and len(entries) != expected:
        _logger.warning(
            "staf_metric_library: exploded %d assignment rows but header says %d.",
            len(entries),
            expected,
        )
    _ENTRIES_CACHE = entries
    return entries


def _reset_cache() -> None:
    """Test hook: drop cached lookup + exploded entries."""
    global _ENTRIES_CACHE, _FN_LOOKUP_CACHE, _CANON_FN_CACHE
    _ENTRIES_CACHE = None
    _FN_LOOKUP_CACHE = None
    _CANON_FN_CACHE = None


def staf_metric_library_default_mapping(metric_keys=(), metric_config=None) -> pd.DataFrame:
    """Build the pre-filled discipline_function_mapping from the master library
    (R lines 172-211).

    Each (metric x function) assignment becomes a row keyed by the real app
    metric_key when the library entry's ``app_metric_key`` is present in
    ``metric_keys`` (data-backed), else a synthetic ``lib:<library_id>`` key
    (planned / no-data). Primary assignments sort ahead of their additionals;
    the (metric_key, function_label) pair is de-duplicated primary-first. Any
    workbook metric with no library home is appended as a blank scaffold row.
    """
    keys: list[str] = []
    seen: set = set()
    for k in list(metric_keys):
        if k is None:
            continue
        try:
            if pd.isna(k):
                continue
        except (TypeError, ValueError):
            pass
        if str(k) == "" or k in seen:
            continue
        seen.add(k)
        keys.append(k)
    keyset = set(keys)

    ent = staf_metric_library_entries()
    if len(ent) == 0:
        return blank_function_mapping_scaffold(keys)

    def _has_key(v) -> bool:
        return v is not None and str(v) != "" and v in keyset

    real = ent["app_metric_key"].map(_has_key)
    mk = [
        (ent["app_metric_key"].iloc[i] if real.iloc[i]
         else f"lib:{ent['library_id'].iloc[i]}")
        for i in range(len(ent))
    ]

    df = pd.DataFrame(
        {
            "metric_key": mk,
            "discipline": ent["discipline"].tolist(),
            "function_label": ent["function_name"].tolist(),
            "is_primary": ent["is_primary"].tolist(),
            "_orig": list(range(len(ent))),
        }
    )
    # primary-first within each metric_key; stable to preserve library order.
    df["_not_primary"] = ~df["is_primary"].astype(bool)
    df = df.sort_values(["metric_key", "_not_primary", "_orig"], kind="mergesort")

    pair = [
        f"{r_mk}\r{str(r_fl).strip().lower()}"
        for r_mk, r_fl in zip(df["metric_key"], df["function_label"])
    ]
    keep_mask = ~pd.Series(pair, index=df.index).duplicated()
    df = df[keep_mask.to_numpy()]

    assign = pd.DataFrame(
        {
            "metric_key": pd.Series(df["metric_key"].tolist(), dtype=object),
            "discipline": pd.Series(df["discipline"].tolist(), dtype=object),
            "function_label": pd.Series(df["function_label"].tolist(), dtype=object),
            "sort_order": pd.Series(range(1, len(df) + 1), dtype="int64"),
        }
    )

    used_real = {m for m in df["metric_key"].tolist() if m in keyset}
    missing = [k for k in keys if k not in used_real]
    if missing:
        scaffold = blank_function_mapping_scaffold(missing)
        assign = pd.concat([assign, scaffold], ignore_index=True)
        assign["sort_order"] = pd.Series(range(1, len(assign) + 1), dtype="int64")
    return assign


def default_discipline_function_mapping(metric_keys=(), metric_config=None) -> pd.DataFrame:
    """Comprehensive default mapping for the workbench prefill + "Reset to STAF
    defaults" button.

    Unions two sources so the workbench default matches the import wizard's Compile
    coverage:

    * the STAF master library (:func:`staf_metric_library_default_mapping`) — keeps
      its real assignments AND its ``lib:`` planned / no-data rows, and
    * ``config/metric_map.yaml`` (:func:`~streamcurves.metric_map.metric_map_functions_for`)
      — the complete metric -> function crosswalk that drives the Compile screen,
      applied to every compiled metric_key (assigned to EVERY function it serves).

    Function labels are canonicalized (:func:`staf_canonical_function`, resolving
    staf_functions.json aliases) so buckets align with the workbench skeleton and the
    ``(metric_key, function_label)`` pair de-dupes cleanly across the two sources. A
    compiled metric with no home in either source becomes a blank scaffold row. Always
    passes :func:`~streamcurves.mapping.validate_discipline_function_mapping`.
    """
    from .metric_map import metric_map_functions_for

    # clean, unique, order-preserving keys (matches the base builder).
    keys: list = []
    seen: set = set()
    for k in list(metric_keys):
        if k is None:
            continue
        try:
            if pd.isna(k):
                continue
        except (TypeError, ValueError):
            pass
        if str(k) == "" or k in seen:
            continue
        seen.add(k)
        keys.append(k)
    keyset = {str(k) for k in keys}

    base = staf_metric_library_default_mapping(keys, metric_config)

    def _present(v) -> bool:
        if v is None:
            return False
        try:
            if pd.isna(v):
                return False
        except (TypeError, ValueError):
            pass
        return str(v).strip() != ""

    rows_mk: list = []
    rows_disc: list = []
    rows_fl: list = []
    seen_pairs: set = set()

    # 1) base library rows: real assignments + lib: planned rows. Drop the base's
    #    blank scaffolds — coverage for compiled metrics is re-derived from
    #    metric_map below so the workbench default matches the Compile screen.
    if base is not None and len(base) > 0:
        for _, r in base.iterrows():
            mk, fl = r["metric_key"], r["function_label"]
            if not (_present(mk) and _present(fl)):
                continue
            pair = (str(mk), str(fl).strip().lower())
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            rows_mk.append(mk)
            rows_disc.append(r["discipline"])
            rows_fl.append(fl)

    # 2) metric_map.yaml assignments for every compiled metric_key, one row per
    #    function the metric serves (canonicalized to the workbench's function name).
    for mk in keys:
        for ff in metric_map_functions_for(str(mk)):
            canon = staf_canonical_function(ff.get("function_name"))
            if canon is None:
                continue
            pair = (str(mk), str(canon["name"]).strip().lower())
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            rows_mk.append(mk)
            rows_disc.append(canon["discipline"])
            rows_fl.append(canon["name"])

    # 3) compiled metrics with no home in either source -> blank scaffold row.
    assigned_real = {str(m) for m in rows_mk if str(m) in keyset}
    for k in keys:
        if str(k) not in assigned_real:
            rows_mk.append(k)
            rows_disc.append(None)
            rows_fl.append(None)

    return pd.DataFrame(
        {
            "metric_key": pd.Series(rows_mk, dtype=object),
            "discipline": pd.Series(rows_disc, dtype=object),
            "function_label": pd.Series(rows_fl, dtype=object),
            "sort_order": pd.Series(range(1, len(rows_mk) + 1), dtype="int64"),
        }
    )
