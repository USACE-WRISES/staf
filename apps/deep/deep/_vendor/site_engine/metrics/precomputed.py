"""Watershed values from the STAF data bundle's per-catchment tables (engine 0.5.0).

The bundle carries, for every HR catchment, its NLCD 2021 class cells and impervious sums (and
the 2001 impervious), the pieces of its 100 m riparian strip, its TIGER/Line road length, its
road-stream crossings, its NID dams and its soil K cells (``tools/hr-slim``; read with
``_hrslim.values``). A watershed's values are sums over the catchments of its walk, so the
land-cover, roads, dams and soils families take them from here when the bundle answered the
walk and holds the tables of every region the tree touches, and run live otherwise. The riparian
pieces reproduce the engine's buffer of the walked flowlines clipped to the watershed: each
catchment's strip is split by which upstream level first reaches it.

Same entry names, units and rounding as the live families; the sources say the values were
counted per catchment, and the vintages are the bundle's (``manifest.json`` ``sources``).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional

from .. import bundle

_lock = threading.Lock()
_memo: "OrderedDict[tuple, Optional[dict]]" = OrderedDict()
_MEMO_MAX = 16

SRC_NLCD = "NLCD, counted per HR catchment (STAF data bundle)"
SRC_ROADS = "TIGER/Line roads, measured per HR catchment (STAF data bundle)"
SRC_CROSS = "TIGER/Line roads x NHDPlus HR flowlines, per HR catchment (STAF data bundle)"
SRC_DAMS = "USACE NID, located per HR catchment (STAF data bundle)"
SRC_SOILS = ("gNATSGO surface-horizon kwfact by 10 m cell, retired map units from current SSURGO, "
             "per HR catchment (STAF data bundle)")


def _source(key: str) -> dict:
    ds = bundle.dataset()
    return ((ds.manifest.get("sources") or {}).get(key) or {}) if ds is not None else {}


def _day(entry: dict) -> str:
    return str(entry.get("recorded") or "")[:10]


def vintages() -> dict:
    """The bundle's vintages for the precomputed families (``manifest.json`` ``sources``)."""
    nid, patch = _source("nid_featureserver"), _source("ssurgo_patch")
    soils = [_source("gnatsgo_tables").get("vintage") or "gNATSGO"]
    if patch:
        soils.append(f"SSURGO patch {_day(patch)}")
    return {"tiger": _source("tiger").get("vintage") or "TIGER/Line roads",
            "nid": f"NID FeatureServer snapshot {_day(nid)}" if nid else "NID snapshot",
            "soils": "; ".join(soils)}


def values_for(record: dict) -> Optional[dict]:
    """The bundle's watershed values for the record's outlet under its walk budget, or None (the
    bundle is off, did not answer this tree, or lacks a region's tables)."""
    if not bundle.enabled():
        return None
    site = record.get("site") or {}
    nid = site.get("nhdplusId")
    cfg = ((record.get("input") or {}).get("config") or {})
    if not nid:
        return None
    key = (str(bundle.root()), int(nid), int(cfg.get("maxReaches") or 5000), int(cfg.get("maxHops") or 200))
    with _lock:
        if key in _memo:
            _memo.move_to_end(key)
            return _memo[key]
    out = None
    try:
        tables = bundle.values()
        if tables is not None and bundle.watershed(int(nid), max_hops=key[3], max_reaches=key[2]) is not None:
            from .._hrslim.values import watershed_values
            got = watershed_values(bundle.dataset(), tables, int(nid), max_reaches=key[2], max_hops=key[3])
            out = got if got.get("status") == "ok" else None
    except Exception:  # noqa: BLE001 - the live families answer instead
        out = None
    with _lock:
        _memo[key] = out
        while len(_memo) > _MEMO_MAX:
            _memo.popitem(last=False)
    return out
