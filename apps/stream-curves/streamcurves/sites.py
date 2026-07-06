"""Port of app/helpers/site_model.R.

Turn a set of sites (id + lat/lon) plus pulled metrics into the ``data`` sheet the
pipeline consumes. Pure DataFrame transforms (no network). Depends on
``streamcurves.geo`` (haversine_m, bieger_division_at).

NOTE(parity): the regional-curve prediction uses ``bieger_geometry`` from
R/18_geomorph.R, which is not part of this port's module set. ``regional_curve_predict``
and ``compile_site_table`` therefore accept it as an injected callable
``bieger_geometry(da_sqkm, division) -> {"width_m","depth_m","area_m2"}``.
"""

from __future__ import annotations

import math
import os

import pandas as pd

from .geo import bieger_division_at, haversine_m

M_TO_FT = 3.280839895
M2_TO_FT2 = 10.76391042
SQKM_TO_SQMI = 0.3861021585


def _num(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return math.nan
    return f


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


# ── unify site origins (upload / NRSA / map pins) ───────────────────────────
def assemble_sites(upload=None, nrsa=None, pins=None):
    """Row-bind site tables from up to three origins, tagging each with
    ``.source``. Missing columns fill with NA. Returns None if all empty."""
    parts = []
    for df, tag in ((upload, "upload"), (nrsa, "nrsa"), (pins, "pin")):
        if df is not None and len(df) > 0:
            d = df.copy()
            d[".source"] = tag
            parts.append(d)
    if not parts:
        return None
    return pd.concat(parts, ignore_index=True)


# ── dedup near-duplicate sites ──────────────────────────────────────────────
def dedup_sites(df, lon_col, lat_col, tol_m=50, source_col=".source",
                priority=("upload", "nrsa", "pin")):
    """Drop near-duplicate sites within ``tol_m`` metres, keeping the
    highest-priority source. Rows with missing coordinates are always kept.
    Original row order is preserved in the result."""
    priority = list(priority)
    n = len(df)
    if n <= 1:
        return df
    order = list(range(n))
    if source_col is not None and source_col in df.columns:
        rank = []
        srcvals = df[source_col].tolist()
        for s in srcvals:
            rank.append(priority.index(s) if s in priority else len(priority))
        # stable order by rank (mirrors R order() radix stability)
        order = sorted(range(n), key=lambda i: (rank[i], i))
    lon = [_num(v) for v in df[lon_col].tolist()]
    lat = [_num(v) for v in df[lat_col].tolist()]
    keep = [False] * n
    kept: list[int] = []
    for i in order:
        if not _finite(lon[i]) or not _finite(lat[i]):
            keep[i] = True
            continue
        dup = False
        for j in kept:
            if (
                _finite(lon[j])
                and _finite(lat[j])
                and haversine_m(lon[i], lat[i], lon[j], lat[j]) < tol_m
            ):
                dup = True
                break
        if not dup:
            keep[i] = True
            kept.append(i)
    keep_idx = [i for i in range(n) if keep[i]]  # sorted -> original row order
    return df.iloc[keep_idx].reset_index(drop=True)


# ── joining pulled data onto the site rows ──────────────────────────────────
def attach_by_comid(base, comid_col, wide):
    """Left-join a COMID-keyed wide frame onto ``base``, preserving base row order
    and adding NA where a COMID has no match."""
    if wide is None or len(wide) == 0 or "COMID" not in wide.columns:
        return base
    base = base.copy()
    base_comid = pd.to_numeric(base[comid_col], errors="coerce")
    lookup = wide.drop_duplicates("COMID").set_index("COMID")
    add_cols = [c for c in wide.columns if c != "COMID"]
    for cn in add_cols:
        base[cn] = base_comid.map(lookup[cn]).to_numpy()
    return base


def streamcat_da_col(df):
    """Find a StreamCAT watershed drainage-area column (WsAreaSqKm) case-
    insensitively, or None."""
    lowered = [c.lower() for c in df.columns]
    targets = ("wsareasqkm", "wsareasqkmrp100", "wsareasqkmws")
    for i, name in enumerate(lowered):
        if name in targets:
            return df.columns[i]
    return None


# ── regional bankfull predictions (Bieger division curves) ──────────────────
def regional_curve_predict(da_sqkm, division=None, bieger_geometry=None):
    """Predicted bankfull width/depth/area (US units) for a drainage area +
    division. Returns ``{pred_BW_ft, pred_BD_ft, pred_BA_ft2}``.

    ``bieger_geometry`` must be supplied (see module note)."""
    if bieger_geometry is None:
        raise NotImplementedError(
            "regional_curve_predict requires a bieger_geometry callable "
            "(R/18_geomorph.R is not part of this port)."
        )
    bf = bieger_geometry(da_sqkm, division)
    return {
        "pred_BW_ft": round(bf["width_m"] * M_TO_FT, 2),
        "pred_BD_ft": round(bf["depth_m"] * M_TO_FT, 3),
        "pred_BA_ft2": round(bf["area_m2"] * M2_TO_FT2, 2),
    }


# ── compile: sites + pulled metrics -> the `data` sheet ─────────────────────
def compile_site_table(base, lat_col, lon_col, comid_col=None, streamcat_wide=None,
                       physio_path=None, da_mi2_col=None, da_sqkm=None,
                       bieger_geometry=None, division_abbr=None):
    """Assemble the per-site table: join StreamCAT metrics by COMID, derive DA_mi2,
    look up the Bieger physiographic division per site, and append predicted
    bankfull columns. ``physio_path=None`` -> national ("USA") curve."""
    base = base.copy()
    n = len(base)
    if streamcat_wide is not None and comid_col is not None and comid_col in base.columns:
        base = attach_by_comid(base, comid_col, streamcat_wide)

    lon = [_num(v) for v in base[lon_col].tolist()]
    lat = [_num(v) for v in base[lat_col].tolist()]

    # drainage area (sq km) -> DA_mi2. Priority: explicit da_sqkm > StreamCAT
    # watershed-area column > existing DA (sq mi) column.
    if da_sqkm is not None and len(da_sqkm) == n:
        da = [_num(v) for v in list(da_sqkm)]
    else:
        da = [math.nan] * n
        da_col = streamcat_da_col(base) if streamcat_wide is not None else None
        if da_col is not None:
            da = [_num(v) for v in base[da_col].tolist()]
        elif da_mi2_col is not None and da_mi2_col in base.columns:
            da = [_num(v) / SQKM_TO_SQMI for v in base[da_mi2_col].tolist()]

    base["DA_mi2"] = [round(v * SQKM_TO_SQMI, 3) if _finite(v) else math.nan for v in da]

    # division per site
    div = ["USA"] * n
    if physio_path is not None:
        if os.path.exists(physio_path):
            for i in range(n):
                if _finite(lon[i]) and _finite(lat[i]):
                    div[i] = bieger_division_at(
                        lon[i], lat[i], physio_path, division_abbr=division_abbr
                    )
    base["bieger_division"] = div

    preds = [regional_curve_predict(da[i], div[i], bieger_geometry=bieger_geometry)
             for i in range(n)]
    base["pred_BW_ft"] = [p["pred_BW_ft"] for p in preds]
    base["pred_BD_ft"] = [p["pred_BD_ft"] for p in preds]
    base["pred_BA_ft2"] = [p["pred_BA_ft2"] for p in preds]
    return base


# ── coverage ("how many streams have this metric") ──────────────────────────
def coverage_table(df, cols=None):
    """Per-column availability summary: DataFrame[metric, n_available, n_total,
    pct]."""
    if cols is None:
        cols = list(df.columns)
    cols = [c for c in cols if c in df.columns]
    n = len(df)
    avail = [int(df[c].notna().sum()) for c in cols]
    if n > 0:
        pct = [round(100 * a / n, 1) for a in avail]
    else:
        pct = [0 for _ in cols]
    return pd.DataFrame(
        {
            "metric": cols,
            "n_available": avail,
            "n_total": [n] * len(cols),
            "pct": pct,
        }
    )
