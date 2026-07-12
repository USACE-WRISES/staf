"""Public-package redaction for a StreamCurves session payload.

A published library version ships two artifacts: a *redacted* public session (safe
to distribute) and a *restricted* ZIP (full detail, access-controlled). This module
produces the redacted public session and a gate that proves the redaction held.

What redaction removes: raw site coordinates (lat/lon), NHD comids, real site
identities (names/labels), the upload filename + input metadata + data fingerprint,
the per-site screening tables, the site-mask config, and validation records. What it
keeps: the analytic columns (metric / predictor / stratification / recode) under an
*opaque sequential* site id, the fitted curve artifacts, and an aggregate screening
summary. The redacted session still restores in the app; only re-screening (which
needs coordinates) is intentionally impossible.
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from . import run_state as rs
from . import session_io as sio

# Internal identity columns in the enriched ``data`` frame.
SITE_ID_COL = "..streamcurves_site_id"
SITE_LABEL_COL = "..streamcurves_site_label"

# Columns that must never survive into a public session.
_DENY_COLS = {
    "lat", "lon", "latitude", "longitude", "long", "comid", "huc8", "huc12",
    "site_id", "site_name", "gnis_name", "stream", "elev_3dep_m",
}

# Session fields nulled entirely (identity / provenance / restricted content).
_SCRUB_FIELDS = (
    "upload_filename",
    "input_metadata",
    "data_fingerprint",
    "site_mask_config",
    "validation_records",
    "easi_screening_sites",
    "easi_screening_metrics",
    "site_exclusions",
)


def _analytic_columns(fields: dict) -> set[str]:
    """Column names that carry analytic signal (safe to keep): the metric,
    stratification, predictor and recode columns plus any custom grouping columns."""
    cols: set[str] = set()
    for cfg_name in ("metric_config", "strat_config", "predictor_config",
                     "factor_recode_config"):
        cfg = fields.get(cfg_name) or {}
        if isinstance(cfg, dict):
            cols.update(str(k) for k in cfg.keys())
    groupings = fields.get("custom_groupings") or {}
    if isinstance(groupings, dict):
        for cg in groupings.values():
            col = (cg or {}).get("column_name")
            if col:
                cols.add(str(col))
    return cols


def _redact_data_frame(data: pd.DataFrame, keep: set[str]) -> tuple[pd.DataFrame, list[str]]:
    n = len(data)
    keep_cols = [c for c in data.columns
                 if str(c) in keep and str(c) not in (SITE_ID_COL, SITE_LABEL_COL)]
    dropped = [str(c) for c in data.columns if str(c) not in keep_cols
               and str(c) not in (SITE_ID_COL, SITE_LABEL_COL)]
    out = data[keep_cols].copy() if keep_cols else pd.DataFrame(index=range(n))
    # Opaque sequential site ids replace the real identity/label columns; downstream
    # grouping by the internal id keeps working, but the identity is gone.
    out.insert(0, SITE_ID_COL, [f"S{i + 1:04d}" for i in range(n)])
    return out.reset_index(drop=True), dropped


def _public_screening_summary(fields: dict) -> dict:
    sites = fields.get("easi_screening_sites")
    criteria = fields.get("easi_screening_criteria") or {}
    n_screened = 0
    n_retained = 0
    if isinstance(sites, pd.DataFrame) and len(sites):
        n_screened = int(len(sites))
        if "final_decision" in sites.columns:
            n_retained = int((sites["final_decision"] == "retained").sum())
    preset = criteria.get("criteria") if isinstance(criteria, dict) else None
    return {
        "public_screening_summary": {
            "n_screened": n_screened,
            "n_retained": n_retained,
            "criteria": preset if isinstance(preset, str) else None,
            "method_version": rs.SCREENING_METHOD_VERSION,
        }
    }


def redact_session_payload(payload: dict) -> tuple[dict, dict]:
    """Return ``(redacted_payload, report)``.

    ``payload`` is a session payload (from ``session_io.dump_session_fields`` or loaded
    from disk). The result is a re-encoded, distributable session; ``report`` records
    what was dropped/nulled.
    """
    fields = sio.decode_session_fields(payload)
    report: dict[str, Any] = {"dropped_columns": [], "nulled_fields": [], "site_count": 0}

    # Aggregate screening summary before the per-site tables are dropped.
    summary = _public_screening_summary(fields)

    data = fields.get("data")
    if isinstance(data, pd.DataFrame):
        keep = _analytic_columns(fields)
        redacted_data, dropped = _redact_data_frame(data, keep)
        fields["data"] = redacted_data
        report["dropped_columns"] = dropped
        report["site_count"] = int(len(redacted_data))

    for f in _SCRUB_FIELDS:
        if fields.get(f) is not None:
            fields[f] = None
            report["nulled_fields"].append(f)

    fields["easi_screening_criteria"] = summary

    # A restored redacted session has no coordinates; mark it so the UI can explain
    # why re-screening/enrichment is unavailable.
    meta = dict(fields.get("run_meta") or {})
    meta["redacted"] = True
    fields["run_meta"] = meta

    redacted = sio.dump_session_fields(
        fields,
        session_name=payload.get("session_name"),
    )
    return redacted, report


_OPAQUE_ID = re.compile(r"^S\d+$")


def redaction_violations(payload: dict) -> list[str]:
    """Return a list of residual-identity problems in a (redacted) payload; empty
    means the payload is clean. This is the gate ``_publish`` runs before writing a
    public session."""
    fields = sio.decode_session_fields(payload)
    problems: list[str] = []

    data = fields.get("data")
    if isinstance(data, pd.DataFrame):
        for c in data.columns:
            name = str(c)
            if name.lower() in _DENY_COLS or name == SITE_LABEL_COL:
                problems.append(f"data still carries identity column {name!r}")
        if SITE_ID_COL in data.columns and len(data):
            vals = data[SITE_ID_COL].astype(str)
            if not vals.map(lambda v: bool(_OPAQUE_ID.match(v))).all():
                problems.append("data site ids are not opaque (S0001-style) values")

    for f in ("upload_filename", "input_metadata", "data_fingerprint",
              "site_mask_config", "validation_records"):
        v = fields.get(f)
        if v not in (None, "", {}, []):
            problems.append(f"{f} was not scrubbed")

    for f in ("easi_screening_sites", "easi_screening_metrics"):
        v = fields.get(f)
        if isinstance(v, pd.DataFrame):
            if not v.empty:
                problems.append(f"{f} still holds per-site rows")
        elif v:
            problems.append(f"{f} still holds per-site rows")

    if fields.get("site_exclusions"):
        problems.append("site_exclusions still carries site ids")

    return problems
