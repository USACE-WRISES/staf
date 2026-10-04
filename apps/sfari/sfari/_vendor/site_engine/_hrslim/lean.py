"""Lean encoding of the precomputed value files (bundle v2, owner decision "lean values").

The builder writes exact files (``lc2_``, ``rip2_``, ``roads2_``, ``dams2_``, ``soils2_``,
``extras2_``: cell counts, metres, float64) and checks them; ``encode_*`` turns each into its lean
form for the bundle, and ``ValueTables`` reads either. Lean, per table (rows stay aligned with the
region's catchment or flowline rows, so the repeated ``nhdplusid`` column is dropped):

- land cover and riparian pieces: the cell count of the catchment or piece, each NLCD class in
  tenths of a percent of it by largest remainder (``p11``..``p95``, ``p_none`` for cells outside
  NLCD; a row sums to 1000, each class within 0.1 point), impervious 2021 and 2001 in tenths of a
  percent (65535: no value). Decoding scales the shares back to cells summing to the count, so
  totals stay exact and a watershed share moves by rounding only. Tenths rather than the whole
  percents first planned: whole percents moved a catchment's class by up to 0.8 point and doubled
  the median difference from the live engine, for 27% less in these two files;
- roads: centimetres (``road_cm``; decimetres moved a watershed's km in the third decimal);
  crossings unchanged;
- dams: the catchments holding dams only (``row``), storage kept in float64;
- soils: mean K per catchment (float32, exact to the engine's 3 decimals) and the cell counts;
- flowline extras: sinuosity in thousandths (the engine rounds to 3 decimals), the nearest unit's
  distance in decimetres, wetland and strip areas in whole square metres; the unit indices and
  HUC12s unchanged.

``ENCODING`` rides in each file's schema metadata (``hrslim_values``).
"""
from __future__ import annotations

import numpy as np
import pyarrow as pa

ENCODING = "lean-1"
SHARE = 1000          # per mille
IMP_SCALE = 10        # impervious in tenths of a percent
NLCD_CODES = (11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95)
LC_COLUMNS = tuple(f"lc{c}" for c in NLCD_CODES) + ("lc_none",)
LEAN_LC = tuple(f"p{c}" for c in NLCD_CODES) + ("p_none",)
IMP_COLUMNS = ("imp21_sum", "imp21_n", "imp01_sum", "imp01_n")
NO_IMP = 65535
NO_DM = 65535


def is_lean(table: pa.Table) -> bool:
    meta = table.schema.metadata or {}
    return meta.get(b"hrslim_values", b"").decode() == ENCODING


def _meta(table: pa.Table, **extra) -> pa.Table:
    meta = dict(table.schema.metadata or {})
    meta[b"hrslim_values"] = ENCODING.encode()
    for k, v in extra.items():
        meta[k.encode()] = str(v).encode()
    return table.replace_schema_metadata(meta)


# ---------------------------------------------------------------------------- land cover
def _percents(counts: np.ndarray, cells: np.ndarray) -> np.ndarray:
    """Shares per mille by largest remainder: each row sums to exactly 1000, every class within
    0.1 point of its exact share (independent rounding lets a row's shares miss the total, and
    scaling back to the cell count would then move every class)."""
    out = np.zeros(counts.shape, dtype=np.uint16)
    nz = np.nonzero(cells > 0)[0]
    if not len(nz):
        return out
    exact = float(SHARE) * counts[nz] / cells[nz, None]
    floor = np.floor(exact)
    short = (SHARE - floor.sum(axis=1)).round().astype(np.int64)
    order = np.argsort(-(exact - floor), axis=1, kind="stable")
    rank = np.empty_like(order)
    np.put_along_axis(rank, order, np.arange(counts.shape[1])[None, :].repeat(len(nz), axis=0), axis=1)
    out[nz] = (floor + (rank < short[:, None])).astype(np.uint16)
    return out


def _imp_pct(total: np.ndarray, n: np.ndarray) -> np.ndarray:
    out = np.full(len(total), NO_IMP, dtype=np.uint16)
    ok = n > 0
    out[ok] = np.round(IMP_SCALE * total[ok] / n[ok]).astype(np.uint16)
    return out


def _lc_columns(t: pa.Table) -> dict:
    counts = np.column_stack([t.column(c).to_numpy() for c in LC_COLUMNS]).astype(np.int64)
    imp = np.column_stack([t.column(c).to_numpy() for c in IMP_COLUMNS]).astype(np.int64)
    cells = counts.sum(axis=1)
    p = _percents(counts, cells)
    cols = {"cells": pa.array(cells.astype(np.int32))}
    for i, c in enumerate(LEAN_LC):
        cols[c] = pa.array(p[:, i])
    cols["imp21"] = pa.array(_imp_pct(imp[:, 0], imp[:, 1]))
    cols["imp01"] = pa.array(_imp_pct(imp[:, 2], imp[:, 3]))
    return cols


def encode_lc(t: pa.Table) -> pa.Table:
    return _meta(pa.table(_lc_columns(t)))


def encode_rip(t: pa.Table) -> pa.Table:
    cols = {"row": pa.array(t.column("row").to_numpy().astype(np.int32)),
            "k": pa.array(t.column("k").to_numpy().astype(np.int16))}
    cols.update(_lc_columns(t))
    return _meta(pa.table(cols))


def decode_lc(t: pa.Table) -> tuple[np.ndarray, np.ndarray]:
    """``(counts 17 float64, impervious sums 4 float64)`` per row, like the exact tables'."""
    cells = t.column("cells").to_numpy().astype(np.float64)
    p = np.column_stack([t.column(c).to_numpy() for c in LEAN_LC]).astype(np.float64)
    s = p.sum(axis=1)
    scale = np.divide(cells, s, out=np.zeros_like(cells), where=s > 0)
    counts = p * scale[:, None]
    valid = counts[:, :16].sum(axis=1)
    imp = np.zeros((len(cells), 4))
    for j, col in ((0, "imp21"), (2, "imp01")):
        v = t.column(col).to_numpy().astype(np.float64)
        has = v != NO_IMP
        imp[:, j] = np.where(has, v / IMP_SCALE * valid, 0.0)
        imp[:, j + 1] = np.where(has, valid, 0.0)
    return counts, imp


# ---------------------------------------------------------------------------- vectors, soils
def encode_roads(t: pa.Table) -> pa.Table:
    m = t.column("road_m").to_numpy().astype(np.float64)
    return _meta(pa.table({"road_cm": pa.array(np.round(m * 100).astype(np.uint32))}))


def decode_roads(t: pa.Table) -> np.ndarray:
    return t.column("road_cm").to_numpy().astype(np.float64) / 100.0


def encode_dams(t: pa.Table) -> pa.Table:
    d = t.column("dams").to_numpy()
    rows = np.nonzero(d > 0)[0]
    tab = pa.table({"row": pa.array(rows.astype(np.int32)), "dams": pa.array(d[rows].astype(np.int16)),
                    "normal_acft": pa.array(t.column("normal_acft").to_numpy().astype(np.float64)[rows]),
                    "normal_missing": pa.array(t.column("normal_missing").to_numpy().astype(np.int16)[rows]),
                    "nid_acft": pa.array(t.column("nid_acft").to_numpy().astype(np.float64)[rows])})
    return _meta(tab, rows=t.num_rows)


def decode_dams(t: pa.Table) -> dict:
    n = int((t.schema.metadata or {})[b"rows"])
    rows = t.column("row").to_numpy().astype(np.int64)
    out = {}
    for c, dtype in (("dams", np.int64), ("normal_acft", np.float64), ("normal_missing", np.int64),
                     ("nid_acft", np.float64)):
        a = np.zeros(n, dtype=dtype)
        a[rows] = t.column(c).to_numpy()
        out[c] = a
    return out


def encode_soils(t: pa.Table) -> pa.Table:
    ks = t.column("k_sum").to_numpy().astype(np.float64)
    kc = t.column("k_cells").to_numpy().astype(np.int64)
    mean = np.full(len(ks), np.nan)
    mean[kc > 0] = ks[kc > 0] / kc[kc > 0]
    return _meta(pa.table({"k_mean": pa.array(mean.astype(np.float32), mask=np.isnan(mean)),
                           "k_cells": pa.array(kc.astype(np.int32)),
                           "ssurgo_cells": pa.array(t.column("ssurgo_cells").to_numpy().astype(np.int32)),
                           "statsgo_cells": pa.array(t.column("statsgo_cells").to_numpy().astype(np.int32))}))


def decode_soils(t: pa.Table) -> dict:
    kc = t.column("k_cells").to_numpy().astype(np.int64)
    mean = np.nan_to_num(t.column("k_mean").to_numpy(zero_copy_only=False).astype(np.float64))
    return {"k_sum": mean * kc, "k_cells": kc, "ssurgo_cells": t.column("ssurgo_cells").to_numpy(),
            "statsgo_cells": t.column("statsgo_cells").to_numpy()}


# ---------------------------------------------------------------------------- flowline extras
_AREA_COLUMNS = ("nwi_r_m2", "nwi_p_m2", "nwi_l_m2", "nwi_e_m2", "nwi_m_m2", "strip_m2")


def encode_extras(t: pa.Table) -> pa.Table:
    cols = {}
    for c in t.column_names:
        if c == "nhdplusid":
            continue
        v = t.column(c)
        if c == "sinuosity":
            a = v.to_numpy(zero_copy_only=False).astype(np.float64)
            cols["sinuosity_milli"] = pa.array(np.where(np.isnan(a), 0, np.round(a * 1000)).astype(np.uint32))
        elif c.startswith("au_near_m_"):
            a = v.to_numpy(zero_copy_only=False).astype(np.float64)
            dm = np.where(np.isnan(a), NO_DM, np.minimum(np.round(a * 10), NO_DM - 1)).astype(np.uint16)
            cols["au_near_dm_" + c[len("au_near_m_"):]] = pa.array(dm)
        elif c in _AREA_COLUMNS:
            a = v.to_numpy(zero_copy_only=False).astype(np.float64)
            cols[c] = pa.array(np.round(np.nan_to_num(a)).astype(np.uint32))
        else:
            cols[c] = v
    return _meta(pa.table(cols))


def decode_extras(t: pa.Table) -> pa.Table:
    """The exact table's columns and units (without ``nhdplusid``)."""
    cols = {}
    for c in t.column_names:
        v = t.column(c)
        if c == "sinuosity_milli":
            a = v.to_numpy().astype(np.float64) / 1000.0
            a[a == 0] = np.nan
            cols["sinuosity"] = pa.array(a, mask=np.isnan(a))
        elif c.startswith("au_near_dm_"):
            a = v.to_numpy().astype(np.float64)
            miss = a == NO_DM
            cols["au_near_m_" + c[len("au_near_dm_"):]] = pa.array(np.where(miss, np.nan, a / 10.0), mask=miss)
        elif c in _AREA_COLUMNS:
            cols[c] = pa.array(v.to_numpy().astype(np.float64))
        else:
            cols[c] = v
    return pa.table(cols)


ENCODERS = {"lc": encode_lc, "rip": encode_rip, "roads": encode_roads, "dams": encode_dams,
            "soils": encode_soils, "extras": encode_extras}
