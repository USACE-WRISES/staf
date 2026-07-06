"""Session save/load as schema-versioned JSON.

Replaces the R app's ``saveRDS(rv)`` sessions (mod_data_overview.R:2706,
2936-2977) — user decision: JSON-only, no .rds restore (the R app remains
available for old sessions). Follows DEEP's ``deep/session.py`` shape:
``SCHEMA_VERSION`` + migration chain, and the shared rule *serialize inputs
and user decisions, never fitted objects* — the app's lazy ``ensure_*``
backfill rebuilds artifacts on demand after a restore.

The encoder is recursive and typed: DataFrames round-trip with dtypes and
(ordered) categoricals; NaN/Inf encode explicitly so files are strict JSON
(``allow_nan=False``). Unknown objects raise loudly — except inside
``completed_metrics``, where non-serializable values (cached plot objects)
are dropped with a warning, matching the R app's move toward artifact-mode
sessions.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("streamcurves")

SCHEMA_VERSION = 1
APP_ID = "streamcurves"
SESSION_SUFFIX = ".streamcurves.json"

## State fields persisted in a session (everything a restore needs; startup_*
## snapshots, nonces, and workspace-modal state are rebuilt per session).
SESSION_FIELDS = [
    # data
    "data",
    "qa_log",
    "precheck_df",
    "data_source",
    "data_fingerprint",
    "upload_filename",
    "input_metadata",
    "site_mask_config",
    # configs
    "metric_config",
    "strat_config",
    "predictor_config",
    "factor_recode_config",
    "output_config",
    "config_version",
    "current_metric",
    # phase results (live, current-metric)
    "phase1_screening",
    "phase1_effect_sizes",
    "phase1_candidates",
    "all_layer1_results",
    "all_layer2_results",
    "phase2_ranking",
    "cross_metric_consistency",
    "phase2_settings",
    "phase2_metric_overrides",
    "curve_stratification",
    "summary_available_overrides",
    "summary_edit_notes",
    "phase3_patterns",
    "phase3_feasibility",
    "phase3_verification",
    "strat_decision_user",
    "reference_curve",
    "phase4_data",
    "current_stratum_level",
    "stratum_results",
    "completed_metrics",
    "decision_log",
    "metric_phase_cache",
    # STAF workbench mapping
    "discipline_function_mapping",
    "discipline_function_mapping_confirmed",
    "mapping_user_touched",
    "workbook_provided_mapping",
    # custom groupings + misc
    "custom_groupings",
    "custom_grouping_counter",
    "session_name",
    "app_data_loaded",
    "cross_sections",
    "column_sources",
    "column_functions",
]

## Fields whose dict payloads may contain non-serializable cached objects
## (plots) that are silently dropped instead of raising.
_LENIENT_FIELDS = {"completed_metrics"}


# --------------------------------------------------------------------------- #
# Recursive typed encoder / decoder
# --------------------------------------------------------------------------- #


def _encode_frame(df: pd.DataFrame) -> dict:
    dtypes: dict[str, str] = {}
    categories: dict[str, dict] = {}
    data_cols: dict[str, list] = {}
    for col in df.columns:
        s = df[col]
        if isinstance(s.dtype, pd.CategoricalDtype):
            dtypes[str(col)] = "category"
            categories[str(col)] = {
                "ordered": bool(s.cat.ordered),
                "levels": [None if pd.isna(v) else v for v in s.cat.categories.tolist()],
            }
            values = s.astype(object)
        else:
            dtypes[str(col)] = str(s.dtype)
            values = s
        data_cols[str(col)] = [_encode_cell(v) for v in values.tolist()]
    return {
        "__type__": "frame",
        "columns": [str(c) for c in df.columns],
        "n": int(len(df)),
        "data": data_cols,
        "dtypes": dtypes,
        "categories": categories,
    }


def _encode_cell(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (np.floating, float)):
        f = float(v)
        if math.isnan(f):
            return None
        if math.isinf(f):
            return {"__type__": "float", "value": "inf" if f > 0 else "-inf"}
        return f
    if isinstance(v, (np.integer, int)) and not isinstance(v, bool):
        return int(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return {"__type__": "datetime", "iso": v.isoformat()}
    if pd.isna(v):
        return None
    return v


def _decode_frame(obj: dict) -> pd.DataFrame:
    cols = obj["columns"]
    data = {}
    for col in cols:
        values = [_decode_value(v) for v in obj["data"][col]]
        dtype = obj["dtypes"].get(col, "object")
        if dtype == "category":
            meta = obj["categories"][col]
            data[col] = pd.Categorical(
                values, categories=meta["levels"], ordered=bool(meta["ordered"])
            )
        else:
            s = pd.Series(values, dtype="object")
            try:
                data[col] = s.astype(dtype)
            except (TypeError, ValueError):
                data[col] = s  # best effort; keep values
    return pd.DataFrame(data, columns=cols)


def encode_value(v: Any, *, lenient: bool = False, path: str = "$") -> Any:
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        if math.isnan(f):
            return {"__type__": "float", "value": "nan"}
        if math.isinf(f):
            return {"__type__": "float", "value": "inf" if f > 0 else "-inf"}
        return f
    if isinstance(v, pd.DataFrame):
        return _encode_frame(v)
    if isinstance(v, pd.Series):
        return {
            "__type__": "series",
            "values": [_encode_cell(x) for x in v.tolist()],
            "dtype": str(v.dtype),
        }
    if isinstance(v, (pd.Timestamp, datetime)):
        return {"__type__": "datetime", "iso": v.isoformat()}
    if isinstance(v, np.ndarray):
        return {
            "__type__": "ndarray",
            "values": [_encode_cell(x) for x in v.tolist()],
            "dtype": str(v.dtype),
        }
    if isinstance(v, dict):
        out = {}
        for k, item in v.items():
            if not isinstance(k, str):
                k = str(k)
            if k == "__type__":
                raise ValueError(f"{path}: reserved key '__type__' in session dict")
            try:
                out[k] = encode_value(item, lenient=lenient, path=f"{path}.{k}")
            except TypeError:
                if lenient:
                    logger.warning("session: dropping non-serializable %s.%s", path, k)
                    continue
                raise
        return out
    if isinstance(v, (list, tuple, set)):
        return [
            encode_value(item, lenient=lenient, path=f"{path}[{i}]")
            for i, item in enumerate(v)
        ]
    raise TypeError(f"{path}: cannot serialize {type(v).__name__} into a session")


def _decode_value(v: Any) -> Any:
    if isinstance(v, dict):
        t = v.get("__type__")
        if t == "frame":
            return _decode_frame(v)
        if t == "float":
            return {"nan": math.nan, "inf": math.inf, "-inf": -math.inf}[v["value"]]
        if t == "datetime":
            return pd.Timestamp(v["iso"])
        if t == "series":
            return pd.Series([_decode_value(x) for x in v["values"]]).astype(
                v.get("dtype", "object"), errors="ignore"
            )
        if t == "ndarray":
            return np.asarray([_decode_value(x) for x in v["values"]])
        return {k: _decode_value(item) for k, item in v.items()}
    if isinstance(v, list):
        return [_decode_value(item) for item in v]
    return v


# --------------------------------------------------------------------------- #
# Session envelope
# --------------------------------------------------------------------------- #


def dump_session_fields(
    fields: dict[str, Any],
    *,
    session_name: str | None = None,
    created: datetime | None = None,
    app_version: str = "0.1.0",
) -> dict:
    """Build the session payload from an already-materialized field dict."""
    created = created or datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "app": APP_ID,
        "app_version": app_version,
        "created": created.isoformat(),
        "session_name": session_name,
        "fields": {},
    }
    for name in SESSION_FIELDS:
        value = fields.get(name)
        payload["fields"][name] = encode_value(
            value, lenient=name in _LENIENT_FIELDS, path=f"$.{name}"
        )
    return payload


def dumps_session(payload: dict) -> str:
    return json.dumps(payload, allow_nan=False, ensure_ascii=False, indent=1)


def write_session(payload: dict, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(dumps_session(payload), encoding="utf-8")
    return path


def load_session_payload(text_or_path: str | Path) -> dict:
    """Parse + validate + migrate a session payload (raises ValueError with a
    user-facing message on bad input — mirrors the R app's 'not a valid
    StreamCurves .rds snapshot' check, mod_data_overview.R:1508)."""
    if isinstance(text_or_path, Path) or (
        isinstance(text_or_path, str) and "\n" not in text_or_path and Path(text_or_path).exists()
    ):
        text = Path(text_or_path).read_text(encoding="utf-8")
    else:
        text = str(text_or_path)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"This is not a valid StreamCurves session file ({e})") from e

    if not isinstance(payload, dict) or payload.get("app") != APP_ID:
        raise ValueError("This is not a valid StreamCurves session file.")

    version = payload.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise ValueError("This session file has no valid schema version.")
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"This session was saved by a newer StreamCurves (schema v{version}; "
            f"this app reads up to v{SCHEMA_VERSION}). Update the app to open it."
        )
    for v in range(version, SCHEMA_VERSION):
        payload = _MIGRATIONS[v](payload)
    return payload


def decode_session_fields(payload: dict) -> dict[str, Any]:
    fields = payload.get("fields") or {}
    return {name: _decode_value(fields.get(name)) for name in SESSION_FIELDS}


## v -> migration fn producing v+1
_MIGRATIONS: dict[int, Any] = {}
