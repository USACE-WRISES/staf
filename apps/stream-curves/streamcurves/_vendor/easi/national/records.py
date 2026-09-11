"""The per-reach evidence record and how it becomes an ``AnalysisContext``.

A record is a plain JSON-serializable dict (one parquet row of
``evidence_<huc4>.parquet``). The keys below are the contract shared by the
builder (which writes them) and the app (which scores them):

identity
    ``comid, huc4, huc8, huc12, vpu, gnis_name, streamorde, fcode, totdasqkm,
    lengthkm, slope, sinuosity, lat, lon`` (the anchor point: the flowline's
    downstream node, a few metres upstream), ``hydroseq, dnhydroseq,
    levelpathi, tocomid`` (topology, informational).
evidence
    ``streamcat`` (``{column: value}`` exactly as ``streamcat.metrics_by_comid``
    returns, lowercase columns), ``nrsa`` (the record ``nrsa.evidence_for_reach``
    would return, or None), ``bankfull`` (``bieger.bankfull_geometry``'s dict),
    ``attains_exact`` / ``attains_nearby`` (``attains.impairment_*`` dicts, ``{}``
    for none), ``wqp_tn`` / ``wqp_tp`` (``wqp.sample_summary`` dicts, None for a
    failed query), ``nid_dams`` (``nid_barriers.barriers_near`` list, None on
    failure), ``nas_taxa`` (``nas.established_taxa`` list, None on failure) with
    ``nas_scope`` (``huc12`` or ``huc8``), ``geomorph`` (Tier 2: the
    ``threedep.reach_geomorphology`` dict slimmed by the builder: the reach
    medians and ``reach`` stats on top, the nine candidates' scalars under
    ``candidate_scalars``, and ``candidates`` holding the one drawn section with
    its simplified profile so the report can plot it; ``{}`` when the sampling
    ran and found nothing usable, the live outcome; None when the cross-section
    stages have not run for the reach, Tier 1).
"""
from __future__ import annotations

from typing import Any, Optional

from .. import bieger, routing, watershed
from ..metrics.base import AnalysisContext

IDENTITY_FIELDS = ("comid", "huc4", "huc8", "huc12", "vpu", "gnis_name",
                   "streamorde", "fcode", "totdasqkm", "lengthkm", "slope",
                   "sinuosity", "lat", "lon", "hydroseq", "dnhydroseq",
                   "levelpathi", "tocomid")
EVIDENCE_FIELDS = ("streamcat", "nrsa", "bankfull", "attains_exact",
                   "attains_nearby", "wqp_tn", "wqp_tp", "nid_dams", "nas_taxa",
                   "nas_scope", "geomorph")
POINT_SERVICE_KEYS = ("attains_exact", "attains_nearby", "wqp_tn", "wqp_tp",
                      "nid_dams", "nas_taxa", "nas_scope")
#: Variable-shaped fields travel as JSON text inside the parquet row: parquet
#: cannot hold an empty struct (an ATTAINS "queried, nothing found" is ``{}``),
#: and the shapes may grow with later builder versions.
JSON_FIELDS = ("streamcat", "nrsa", "bankfull", "attains_exact", "attains_nearby",
               "wqp_tn", "wqp_tp", "nid_dams", "nas_taxa", "geomorph")


def to_row(record: dict) -> dict:
    """The parquet row for a record (JSON fields encoded, None kept)."""
    import json
    row = dict(record)
    for key in JSON_FIELDS:
        value = row.get(key)
        row[key] = None if value is None else json.dumps(value, separators=(",", ":"))
    row.setdefault("schema_version", 1)
    return row


def from_row(row: dict) -> dict:
    """A record from a parquet row (JSON fields decoded)."""
    import json
    record = dict(row)
    for key in JSON_FIELDS:
        value = record.get(key)
        if isinstance(value, (str, bytes)):
            try:
                record[key] = json.loads(value)
            except ValueError:
                record[key] = None
    return record


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None      # NaN -> None


def _int(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def streamcat_row(record: dict) -> dict:
    """The StreamCat row as the adapters read it: lowercase keys, no None values."""
    row = record.get("streamcat") or {}
    out: dict = {}
    for key, value in row.items():
        if value is None:
            continue
        number = _num(value)
        out[str(key).lower()] = value if number is None else number
    return out


def bankfull_block(record: dict) -> dict:
    """The stored Bieger block, or the one ``assess`` would have computed."""
    block = record.get("bankfull")
    if isinstance(block, dict) and block.get("area_m2") is not None:
        return dict(block)
    return bieger.bankfull_geometry(_num(record.get("totdasqkm")) or 0.0,
                                    _num(record.get("lat")), _num(record.get("lon")))


def reach_geomorph(record: dict) -> dict:
    """``extras["reach_geomorph"]`` as ``assess`` builds it: the (possibly empty)
    geometry dict carrying the Bieger fit-range flags."""
    geom = dict(record.get("geomorph") or {})
    bf = bankfull_block(record)
    geom["bankfull_extrapolated"] = bool(bf.get("extrapolated"))
    geom["bankfull_fit_range_sqkm"] = bf.get("fit_range_sqkm")
    return geom


def site_anchor(record: dict) -> dict:
    """The covered-network anchor ``pipeline.delineate_only`` synthesizes for a
    direct-COMID run, with the flowline facts filled in."""
    comid = _int(record.get("comid"))
    lat, lon = _num(record.get("lat")), _num(record.get("lon"))
    anchor = routing.v2_anchor(comid, lat, lon, snap_lat=lat, snap_lon=lon)
    scored = anchor.setdefault("scoredReach", {})
    scored["gnisName"] = record.get("gnis_name")
    scored["drainageAreaSqkm"] = _num(record.get("totdasqkm"))
    return anchor


def apply_evidence(ctx: AnalysisContext, record: dict) -> AnalysisContext:
    """Overwrite ``ctx``'s attributes and ``extras`` with the record's evidence,
    exactly as the live prefetch would have populated them."""
    ctx.comid = _int(record.get("comid"))
    ctx.huc8 = record.get("huc8") or ctx.huc8
    ctx.huc12 = record.get("huc12") or ctx.huc12
    ctx.drainage_area_sqkm = _num(record.get("totdasqkm"))
    ctx.slope = _num(record.get("slope"))
    ctx.fcode = _int(record.get("fcode"))
    ctx.stream_order = _int(record.get("streamorde"))
    ctx.sinuosity = _num(record.get("sinuosity"))
    sc = streamcat_row(record)
    ctx.extras["streamcat"] = sc
    ctx.extras["landcover"] = {}
    ctx.extras["watershed"] = watershed.build(ctx, sc)
    nrsa_record = record.get("nrsa")
    ctx.extras["nrsa"] = dict(nrsa_record) if isinstance(nrsa_record, dict) else None
    ctx.extras["reach_geomorph"] = reach_geomorph(record)
    ctx.extras["source_choices"] = {}
    ctx.extras["prefetch_variants"] = False
    if not ctx.extras.get("siteAnchor"):
        ctx.extras["siteAnchor"] = site_anchor(record)
    ctx.extras["precomputed"] = {
        "comid": ctx.comid, "huc4": record.get("huc4"), "huc8": ctx.huc8,
        "schema": record.get("schema_version"),
    }
    return ctx


def build_context(record: dict, *, watershed_geojson: Optional[dict] = None,
                  reach_geojson: Optional[dict] = None) -> AnalysisContext:
    """A scoring context from a record alone (no geometry unless supplied)."""
    ctx = AnalysisContext(lat=_num(record.get("lat")), lon=_num(record.get("lon")),
                          comid=_int(record.get("comid")),
                          watershed_geojson=watershed_geojson,
                          reach_geojson=reach_geojson)
    return apply_evidence(ctx, record)
