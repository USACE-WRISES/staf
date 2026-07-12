"""Restricted publication package for a StreamCurves library version.

The public library ships a *redacted* session (see ``redaction.py``); the *restricted*
package is the full-detail counterpart, distributed only under access control. It is a
single deterministic ZIP (same inputs -> byte-identical archive -> stable sha256), so a
maintainer can attach its checksum to ``meta.json`` and a recipient can verify integrity.

Contents: the full (unredacted) session, the DEEP bundle, the full per-site screening
CSVs, the raw validation records, the full reference-data CSV, an optional workbook, and
a MANIFEST describing every entry with its own sha256. The ZIP is returned in memory and
is never written under ``apps/library`` (which holds only public artifacts).
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any, Optional

import pandas as pd

from . import run_state as rs
from . import session_io as sio

# Fixed ZIP entry timestamp so the archive is reproducible (DOS epoch).
_FIXED_DT = (1980, 1, 1, 0, 0, 0)


def _as_frame(obj: Any) -> Optional[pd.DataFrame]:
    if obj is None:
        return None
    if isinstance(obj, pd.DataFrame):
        return obj
    try:
        return pd.DataFrame(obj)
    except Exception:  # noqa: BLE001
        return None


def _csv_bytes(df: Optional[pd.DataFrame]) -> bytes:
    if df is None or len(df) == 0:
        return b""
    return df.to_csv(index=False).encode("utf-8")


def _json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True, default=str)
            + "\n").encode("utf-8")


def public_summary(full_session: dict, bundle: dict) -> dict:
    """Aggregate, distribution-safe summary of a package (goes into meta.json)."""
    fields = sio.decode_session_fields(full_session)
    sites = fields.get("easi_screening_sites")
    n_screened = n_retained = 0
    if isinstance(sites, pd.DataFrame) and len(sites):
        n_screened = int(len(sites))
        if "final_decision" in sites.columns:
            n_retained = int((sites["final_decision"] == "retained").sum())
    data = fields.get("data")
    n_sites = int(len(data)) if isinstance(data, pd.DataFrame) else 0
    n_functions = len(bundle.get("metricsByFunction") or [])
    n_metrics = sum(len(b.get("metrics") or []) for b in (bundle.get("metricsByFunction") or []))
    region = bundle.get("region") or (bundle.get("library") or {}).get("region") or {}
    return {
        "region": {"kind": region.get("kind"), "code": region.get("code"),
                   "name": region.get("name")} if isinstance(region, dict) else None,
        "n_reference_sites": n_sites,
        "n_screened": n_screened,
        "n_retained": n_retained,
        "n_functions": n_functions,
        "n_metrics": n_metrics,
        "curve_method_version": rs.CURVE_METHOD_VERSION,
        "screening_method_version": rs.SCREENING_METHOD_VERSION,
        "scoring_method_version": (bundle.get("scoringContract") or {}).get("methodVersion"),
    }


def build_restricted_package(
    full_session: dict,
    bundle: dict,
    screening: Optional[dict] = None,
    validation_records: Optional[list] = None,
    decision_log: Any = None,
    workbook_bytes: Optional[bytes] = None,
) -> tuple[bytes, str, dict]:
    """Build the restricted ZIP. Returns ``(zip_bytes, sha256_hex, public_summary)``.

    ``screening``: optional ``{sites, metrics, criteria}`` (falls back to the tables
    embedded in ``full_session``). ``decision_log``: optional DataFrame. ``workbook_bytes``:
    optional pre-rendered workbook.
    """
    fields = sio.decode_session_fields(full_session)
    summary = public_summary(full_session, bundle)

    # Assemble the (name -> bytes) entries, then write them in sorted order.
    entries: dict[str, bytes] = {}
    entries["session.streamcurves.json"] = sio.dumps_session(full_session).encode("utf-8")
    entries["assessment.deep.json"] = _json_bytes(bundle)

    scr = screening or {}
    sites = _as_frame(scr.get("sites") if scr else None)
    if sites is None:
        sites = _as_frame(fields.get("easi_screening_sites"))
    metrics = _as_frame(scr.get("metrics") if scr else None)
    if metrics is None:
        metrics = _as_frame(fields.get("easi_screening_metrics"))
    if sites is not None:
        entries["screening/sites.csv"] = _csv_bytes(sites)
    if metrics is not None:
        entries["screening/metrics.csv"] = _csv_bytes(metrics)

    val = validation_records if validation_records is not None else fields.get("validation_records")
    entries["validation.json"] = _json_bytes(val or [])

    dlog = _as_frame(decision_log if decision_log is not None else fields.get("decision_log"))
    if dlog is not None and len(dlog):
        entries["decision_log.csv"] = _csv_bytes(dlog)

    ref = _as_frame(fields.get("data"))
    if ref is not None:
        entries["reference/reference_data.csv"] = _csv_bytes(ref)

    if workbook_bytes:
        entries["workbook.xlsx"] = workbook_bytes

    manifest = {
        "package": "streamcurves-restricted",
        "publicSummary": summary,
        "files": {
            name: {"bytes": len(data),
                   "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(entries.items())
        },
    }
    entries["MANIFEST.json"] = _json_bytes(manifest)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(entries):
            zi = zipfile.ZipInfo(name, date_time=_FIXED_DT)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 0  # cross-platform-stable archive
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, entries[name])
    zip_bytes = buf.getvalue()
    return zip_bytes, hashlib.sha256(zip_bytes).hexdigest(), summary
