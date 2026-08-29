"""Port of app/helpers/data_sources.R — StreamCAT section (watershed metrics by COMID).

Endpoints (keyless):

- primary  https://api.epa.gov/StreamCat/streams/metrics  (JSON ``{"items": [...]}``)
- mirror   https://java.epa.gov/StreamCAT/metrics         (CSV; also the catalog)
"""

from __future__ import annotations

import io
import os
from typing import Any, Callable

import pandas as pd

from . import _as_int, _get_json, _get_text
from ..paths import DATA_DIR

STREAMCAT_PRIMARY = "https://api.epa.gov/StreamCat/streams/metrics"
STREAMCAT_MIRROR = "https://java.epa.gov/StreamCAT/metrics"

_STREAMCAT_AOI_SUFFIX = {
    "watershed": "ws",
    "catchment": "cat",
    "riparian_watershed": "wsrp100",
    "riparian_catchment": "catrp100",
}


def _empty_comid_frame() -> pd.DataFrame:
    """R ``data.frame(COMID = integer(0))``."""
    return pd.DataFrame({"COMID": pd.Series([], dtype="Int64")})


def _normalize_comid(df: pd.DataFrame) -> pd.DataFrame:
    """Rename the first case-insensitive 'comid' column to COMID + as.integer."""
    ci = [c for c in df.columns if str(c).lower() == "comid"]
    if ci:
        df = df.rename(columns={ci[0]: "COMID"})
        df["COMID"] = pd.array([_as_int(v) for v in df["COMID"]], dtype="Int64")
    return df


def parse_streamcat_csv(text: str | None) -> pd.DataFrame:
    """Parse a StreamCAT CSV response into a DataFrame with a normalized COMID column."""
    if text is None or not str(text).strip():
        return _empty_comid_frame()
    try:
        df = pd.read_csv(io.StringIO(str(text)))
    except Exception:
        df = None
    if df is None or not len(df):
        return _empty_comid_frame()
    return _normalize_comid(df)


def parse_streamcat_json(j: Any, aoi_suffix: str = "ws") -> pd.DataFrame:
    """Parse api.epa.gov StreamCat JSON (``{"items":[{comid, <name+suffix>...}]}``)
    into a wide DataFrame, keeping COMID + columns for the requested
    area-of-interest suffix (plus any suffix-less metrics). The API returns
    every suffix variant, so we filter."""
    items = j.get("items") if isinstance(j, dict) else None
    if not items:
        return _empty_comid_frame()
    df = pd.DataFrame(list(items))  # bind_rows: union of columns, NaN-filled
    df = _normalize_comid(df)
    metric_cols = [c for c in df.columns if c != "COMID"]
    if metric_cols:
        all_suf = tuple(_STREAMCAT_AOI_SUFFIX.values())
        keep = [
            c
            for c in metric_cols
            if str(c).lower().endswith(aoi_suffix) or not str(c).lower().endswith(all_suf)
        ]
        # R `df[, c("COMID", keep)]` errors when no comid column came back;
        # KeyError here reproduces that (callers catch it).
        df = df[["COMID"] + keep]
    return df


def streamcat_metrics(
    comids,
    metric_names,
    area: str = "watershed",
    batch: int = 200,
    progress: Callable[[int, int], Any] | None = None,
) -> pd.DataFrame:
    """StreamCAT metrics for COMIDs. GET to api.epa.gov (JSON), falling back to
    the java mirror (CSV). ``metric_names`` are StreamCAT base names
    (lower-cased here, e.g. "pctimp2019"); columns come back as base+suffix
    (e.g. "pctimp2019ws"). Never raises; empty frame on total failure."""
    if isinstance(metric_names, str):
        metric_names = [metric_names]
    # unique(as.integer(comids)) keeping first-occurrence order, NAs dropped
    ids: list[int] = []
    seen: set[int] = set()
    for c in comids:
        ci = _as_int(c)
        if ci is not None and ci not in seen:
            seen.add(ci)
            ids.append(ci)
    if not ids or not len(metric_names):
        return _empty_comid_frame()
    name_param = ",".join(str(m).lower() for m in metric_names)
    # NOTE(parity): R indexes a named *atomic* vector with [[area]], which errors
    # on an unknown area (its `%||% "ws"` fallback is unreachable); we honour the
    # evident intent and default to "ws".
    aoi_suffix = _STREAMCAT_AOI_SUFFIX.get(area, "ws")
    chunks = [ids[i : i + int(batch)] for i in range(0, len(ids), int(batch))]
    frames: list[pd.DataFrame] = []
    failed_chunks: list[int] = []
    for k, chunk in enumerate(chunks, start=1):
        ch = ",".join(str(c) for c in chunk)
        params = {"name": name_param, "areaOfInterest": area, "comid": ch}
        try:
            fr = parse_streamcat_json(_get_json(STREAMCAT_PRIMARY, params=params), aoi_suffix)
        except Exception:
            fr = None
        if fr is None or not len(fr):
            try:
                fr = parse_streamcat_csv(_get_text(STREAMCAT_MIRROR, params=params))
            except Exception:
                fr = None
        if fr is not None and len(fr):
            frames.append(fr)
        else:
            # Both endpoints came back empty for this chunk. A partial outage
            # must be distinguishable from success, so the chunk is recorded
            # and rides out on the frame's attrs for the caller's source report.
            failed_chunks.append(k)
        if callable(progress):
            progress(k, len(chunks))
    if not frames:
        out = _empty_comid_frame()
    else:
        out = pd.concat(frames, ignore_index=True, sort=False)
    out.attrs["n_chunks"] = len(chunks)
    out.attrs["failed_chunks"] = failed_chunks
    return out


def parse_streamcat_catalog(j: Any) -> pd.DataFrame:
    """Best-effort catalog parse. The parameterless endpoint returns JSON whose
    shape varies, so probe a few (items | metrics | parameters | name)."""
    j = j if isinstance(j, dict) else {}
    items = _first_not_none(j.get("items"), j.get("metrics"), j.get("parameters"))
    names_vec: list[str | None] = []
    if items:
        if isinstance(items[0], dict):
            for it in items:
                v = it.get("name")
                v = v if v is not None else it.get("metric")
                names_vec.append(str(v) if v is not None else None)
        else:
            names_vec = [str(x) for x in items]
    elif j.get("name") is not None:
        nm = j["name"]
        names_vec = [str(x) for x in nm] if isinstance(nm, (list, tuple)) else [str(nm)]
    names_vec = [n for n in names_vec if n is not None and n != ""]
    if not names_vec:
        return pd.DataFrame({"name": pd.Series([], dtype="object"),
                             "domain": pd.Series([], dtype="object")})
    return pd.DataFrame({"name": names_vec, "domain": [None] * len(names_vec)})


def _first_not_none(*vals: Any) -> Any:
    for v in vals:
        if v is not None:
            return v
    return None


def streamcat_catalog(fallback_csv: str | os.PathLike | None = None) -> pd.DataFrame:
    """Catalog of available StreamCAT metric names; falls back to a bundled CSV.

    NOTE(parity): R's default is ``fallback_csv = NULL`` (no fallback) and the
    app passes the bundled file; here None defaults to
    ``DATA_DIR / "streamcat_metrics.csv"`` so the port is self-contained.
    """
    if fallback_csv is None:
        fallback_csv = DATA_DIR / "streamcat_metrics.csv"
    try:
        out = parse_streamcat_catalog(_get_json(STREAMCAT_MIRROR))
    except Exception:
        out = None
    if (out is None or not len(out)) and fallback_csv is not None and os.path.exists(fallback_csv):
        try:
            out = pd.read_csv(fallback_csv)
        except Exception:
            out = None
    if out is None:
        return pd.DataFrame({"name": pd.Series([], dtype="object"),
                             "domain": pd.Series([], dtype="object")})
    return out
