"""Per-HUC8 derived inputs: the anchor point, sinuosity, the VAA attributes
as the app reads them, HUC12 by point-in-polygon, the NARS 9 region, the
physiographic division and Bieger bankfull, the L3 ecoregion, and the NRSA
evidence record (exact tier, plus the connected-nearby tier walked on the
VAA network instead of NLDI)."""
from __future__ import annotations

import json
import math
from datetime import date
from typing import Optional

from .. import config
from ..network import connected_comids_factory
from ..paths import DataRoot
from ..state import Control, Progress, UnitStates, digest
from ..units import Chunk
from . import common

STAGE = "derive"
BACK_M = 10.0          # anchor sits this far upstream of the downstream node


def _anchor_and_sinuosity(geom, flowdir: Optional[str]):
    """``(lat, lon, sinuosity)`` for a flowline: the point ``BACK_M`` upstream of
    the downstream node, and length over the straight distance (EPSG:5070)."""
    import geopandas as gpd
    from shapely.geometry import Point
    from shapely.ops import linemerge, substring
    line = geom
    if line.geom_type == "MultiLineString":
        merged = linemerge(line)
        line = merged if merged.geom_type == "LineString" else max(merged.geoms, key=lambda g: g.length)
    projected = gpd.GeoSeries([line], crs=4326).to_crs(5070).iloc[0]
    if str(flowdir or "").lower().startswith("against"):
        projected = projected.reverse()
    length = projected.length
    straight = Point(projected.coords[0]).distance(Point(projected.coords[-1]))
    sinuosity = round(length / straight, 3) if straight > 0 else None
    anchor = projected.interpolate(max(0.0, length - BACK_M))
    back = gpd.GeoSeries([anchor], crs=5070).to_crs(4326).iloc[0]
    return float(back.y), float(back.x), sinuosity


def _slope(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v >= 0 else None


def _num(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def run_derive(root: DataRoot, chunk: Chunk, huc8: str, states: UnitStates,
               progress: Progress, control: Control, *, force: bool = False,
               as_of: date = config.NRSA_AS_OF) -> None:
    inputs = digest(STAGE, huc8, chunk.id, str(as_of), 1)
    out = root.huc8_file(huc8, "derived")

    def work():
        import geopandas as gpd
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
        from shapely import STRtree
        from shapely.geometry import Point
        from easi import bieger, geo
        from easi.datasources import nrsa

        vaa = pq.read_table(root.vaa, filters=[("huc8", "=", huc8)])
        attrs = {int(r["comid"]): r for r in vaa.to_pylist()}
        lines = gpd.read_parquet(root.chunk_raw(chunk.id, "flowlines"))
        lines = lines[lines["comid"].isin(list(attrs))]
        hucs = gpd.read_parquet(root.chunk_raw(chunk.id, "huc12"))
        tree = STRtree(hucs.geometry.values) if len(hucs) else None
        huc12_codes = hucs["huc_12"].tolist() if len(hucs) else []
        nrsa._connected_comids = connected_comids_factory(root)      # VAA walk, no NLDI
        huc4_vpu = json.loads(root.huc4_vpu.read_text(encoding="utf-8"))
        rows = []
        total = len(lines)
        progress.begin(huc8, STAGE, total=total, message=f"derive {huc8}: {total:,} reaches")
        for i, line in enumerate(lines.itertuples(index=False)):
            if i % 200 == 0:
                control.check()
                progress.tick(done=i)
            comid = int(line.comid)
            a = attrs[comid]
            lat, lon, sinuosity = _anchor_and_sinuosity(line.geometry, getattr(line, "flowdir", None))
            huc12 = None
            if tree is not None:
                hits = tree.query(Point(lon, lat), predicate="within")
                if len(hits):
                    huc12 = huc12_codes[int(hits[0])]
            da = _num(a.get("totdasqkm"))
            bankfull = bieger.bankfull_geometry(da or 0.0, lat, lon)
            nars = geo.nars9_at(lat, lon) or {}
            l3 = geo.level3_at(lat, lon) or {}
            record = nrsa.evidence_for_reach(comid, lat, lon, as_of=as_of)
            rows.append({
                "comid": comid, "huc4": huc8[:4], "huc8": huc8, "huc12": huc12,
                "vpu": huc4_vpu.get(huc8[:4]) or a.get("vpuid"),
                "gnis_name": a.get("gnis_name") or None,
                "streamorde": _int(a.get("streamorde")), "fcode": _int(a.get("fcode")),
                "totdasqkm": da, "lengthkm": _num(a.get("lengthkm")),
                "slope": _slope(a.get("slope")), "sinuosity": sinuosity,
                "lat": round(lat, 6), "lon": round(lon, 6),
                "hydroseq": _int(a.get("hydroseq")), "dnhydroseq": _int(a.get("dnhydroseq")),
                "levelpathi": _int(a.get("levelpathi")), "tocomid": _int(a.get("tocomid")),
                "nars9": nars.get("code"), "l3_code": l3.get("code"), "l3_name": l3.get("name"),
                "physio_division": bankfull.get("division"),
                "bankfull": json.dumps(bankfull, separators=(",", ":")),
                "nrsa": None if record is None else json.dumps(record, separators=(",", ":")),
            })
        table = pa.Table.from_pylist(rows, schema=pa.schema([
            ("comid", pa.int64()), ("huc4", pa.string()), ("huc8", pa.string()),
            ("huc12", pa.string()), ("vpu", pa.string()), ("gnis_name", pa.string()),
            ("streamorde", pa.int32()), ("fcode", pa.int32()), ("totdasqkm", pa.float64()),
            ("lengthkm", pa.float64()), ("slope", pa.float64()), ("sinuosity", pa.float64()),
            ("lat", pa.float64()), ("lon", pa.float64()), ("hydroseq", pa.int64()),
            ("dnhydroseq", pa.int64()), ("levelpathi", pa.int64()), ("tocomid", pa.int64()),
            ("nars9", pa.string()), ("l3_code", pa.string()), ("l3_name", pa.string()),
            ("physio_division", pa.string()), ("bankfull", pa.string()), ("nrsa", pa.string())]))
        common.write_parquet(table, out)
        n_nrsa = sum(1 for r in rows if r["nrsa"])
        progress.say(f"{huc8} derived.parquet: {len(rows):,} reaches, {n_nrsa} with NRSA evidence")

    common.run_stage(states, huc8, STAGE, inputs, work, progress, force=force)
