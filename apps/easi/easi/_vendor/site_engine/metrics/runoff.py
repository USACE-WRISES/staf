"""Mean-annual flow and derived runoff depth (NHDPlus HR EROM).

Feasibility verdict (G6): BUILD from the HR VAA ``qama`` already carried on
the anchor reach (EROM mean-annual flow, cfs) — zero extra network. The
derived runoff depth (flow volume over drainage area, mm/yr) is provenance-
labeled as EROM-derived; it is NOT claimed equivalent to StreamCat
``runoffws`` (McCabe-Wolock grid runoff), and the covered-reach comparison
quantifies how the two relate before anything consumes it for normalization.
"""
from __future__ import annotations

from ..provenance import metric_entry
from . import register

_SRC = "NHDPlus HR EROM mean-annual flow"
_VINTAGE = "NHDPlus HR VAA (live service)"
_CFS_TO_M3YR = 0.0283168466 * 3600 * 24 * 365.25


@register("runoff")
def compute(record: dict, tree_geoms: list) -> dict:
    anchor_qama = (record.get("site") or {}).get("eromQamaCfs")
    da = (record.get("site") or {}).get("drainageAreaSqkm")
    if anchor_qama is None:
        return {"meanAnnualFlowCfs": metric_entry(
            None, "cfs", _SRC, _VINTAGE, "reach",
            ["EROM qama unavailable on the anchor reach"])}
    out = {"meanAnnualFlowCfs": metric_entry(
        round(float(anchor_qama), 3), "cfs", _SRC, _VINTAGE, "reach")}
    if da:
        depth_mm = (float(anchor_qama) * _CFS_TO_M3YR) / (float(da) * 1e6) * 1000
        out["runoffDepthMm"] = metric_entry(
            round(depth_mm, 1), "mm/yr", f"{_SRC} over drainage area",
            _VINTAGE, "pointWatershed",
            ["EROM-derived; not equivalent to StreamCat runoffws"])
    return out
