"""The offline reach line: the app's 1,000 ft reach upstream of the anchor,
built from the local flowlines and the VAA main-path walk instead of NLDI.

The app (``easi.delineation.derive_reach``) fetches the COMID's own flowline
and NLDI's upstreamMain flowlines, then merges, orients and trims them in
``_reach_from_lines``. Here the same pure tail runs on the same geometry
taken from the national flowline file, and the walk runs on the national
network table, so a reach is never clipped at a chunk, state or region
border: it ends only at the 1,000 ft mark or at a true headwater.
"""
from __future__ import annotations

from typing import Optional

from . import config

FT_PER_M = 3.28083989501312


def nav_km(own_len_km: float, length_ft: float) -> float:
    """The navigation distance ``derive_reach`` asks NLDI for: clear the
    reach's own length so its upstream node is inside the returned set."""
    length_km = length_ft / FT_PER_M / 1000.0
    return round(max(length_km * 4, own_len_km + length_km) + 0.3, 1)


def explode(geoms) -> list:
    """LineStrings of a list of (Multi)LineStrings, empties dropped."""
    parts: list = []
    for geom in geoms:
        if geom is None or geom.is_empty:
            continue
        parts.extend(list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom])
    return parts


def own_length_km(own_parts: list) -> float:
    if not own_parts:
        return 0.0
    import geopandas as gpd
    return float(gpd.GeoSeries(own_parts, crs="EPSG:4326").to_crs("EPSG:5070").length.sum()) / 1000.0


def chain_for(network, comid: int, own_len_km: float, length_ft: float = config.REACH_LENGTH_FT) -> list[int]:
    """The COMIDs whose geometry the reach needs, the reach first."""
    chain = network.upstream_main(int(comid), max(1.0, nav_km(own_len_km, length_ft)))
    return chain or [int(comid)]


def reach_line(comid: int, lat: float, lon: float, geoms_by_comid: dict, network, *,
               length_ft: float = config.REACH_LENGTH_FT
               ) -> tuple[Optional[dict], Optional[float], list[str], list[int]]:
    """``(feature collection, reach length ft, warnings, chain)`` exactly as
    ``derive_reach`` returns them, from local geometry (EPSG:4326)."""
    from easi.delineation import _reach_from_lines
    own_parts = explode([geoms_by_comid.get(int(comid))])
    chain = chain_for(network, comid, own_length_km(own_parts), length_ft)
    geoms: list = []
    for other in chain:
        geom = geoms_by_comid.get(int(other))
        if geom is not None:
            geoms.extend(explode([geom]))
    warnings: list[str] = []
    missing = [c for c in chain if geoms_by_comid.get(int(c)) is None]
    if missing:
        warnings.append(f"no geometry for {len(missing)} upstream reach(es)")
    if not geoms:
        geoms = own_parts
    if not geoms:
        return None, None, warnings or ["no flowline geometry for reach"], chain
    feature_collection, actual_ft, warnings = _reach_from_lines(geoms, own_parts, lat, lon, length_ft, warnings)
    return feature_collection, actual_ft, warnings, chain


def missing_comids(chains: dict, geoms_by_comid: dict) -> list[int]:
    """Chain COMIDs without local geometry (outside the chunk), for one batched
    read from the national flowline file."""
    out: set[int] = set()
    for chain in chains.values():
        out.update(int(c) for c in chain if geoms_by_comid.get(int(c)) is None)
    return sorted(out)
