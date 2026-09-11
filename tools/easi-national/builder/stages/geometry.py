"""Flowline geometry for a chunk from the USGS fabric OGC API (CQL ``comid IN``
filter, 1,000 per request), stored as GeoParquet; also sets the chunk's
buffered bounding box, which every bbox-driven stage reads.
"""
from __future__ import annotations

import json

from .. import config, http
from ..paths import DataRoot
from ..state import Control, Ledger, Progress, UnitStates
from ..units import Chunk, chunk_comids
from . import common

STAGE = "geometry"
SOURCE = "flowlines"
URL = f"{config.FABRIC_BASE}/nhdflowline_network/items"
PROPERTIES = "comid,gnis_name,reachcode,lengthkm,fcode,streamorde,slope,totdasqkm,flowdir"


def fetch_flowlines(comids: list[int]) -> list[dict]:
    """GeoJSON features for up to ``FABRIC_IN_CHUNK`` COMIDs."""
    params = {"filter": "comid IN (" + ",".join(str(c) for c in comids) + ")",
              "properties": PROPERTIES, "f": "json",
              "limit": min(len(comids) + 5, 1000)}
    data = http.get_json(URL, params)
    return list(data.get("features") or [])


def run_geometry(root: DataRoot, chunk: Chunk, states: UnitStates, progress: Progress,
                 control: Control, *, force: bool = False, fetch=fetch_flowlines) -> None:
    inputs = common.chunk_inputs(STAGE, chunk, PROPERTIES, 1)
    out = root.chunk_raw(chunk.id, SOURCE)

    def work():
        import geopandas as gpd
        from .local_gdb import flowlines_for
        comids = sorted({int(c) for c in chunk_comids(root, chunk).column("comid").to_pylist()})
        local = flowlines_for(root, comids)
        if local is not None:
            progress.begin(chunk.id, STAGE, total=1,
                           message=f"flowlines: {len(comids):,} reaches from the national geodatabase")
            local = local[local.geometry.notna() & ~local.geometry.is_empty]
            if not len(local):
                raise RuntimeError("no flowline geometry for this chunk in the national file")
            common.write_parquet(local, out)
            west, south, east, north = [float(v) for v in local.total_bounds]
            chunk.bbox = common.buffer_bbox([west, south, east, north], config.CHUNK_BUFFER_MI)
            chunk.save(root)
            progress.tick(done=1)
            progress.say(f"flowlines.parquet: {len(local):,} lines from the national geodatabase; bbox "
                         + json.dumps([round(v, 4) for v in chunk.bbox]))
            return
        size = config.FABRIC_IN_CHUNK
        batches = [comids[i:i + size] for i in range(0, len(comids), size)]
        parts = common.parts_dir(root, chunk.id, SOURCE)
        ledger = Ledger(root, f"{SOURCE}-{chunk.id}")
        pending = [(f"b{i:05d}", batch) for i, batch in enumerate(batches) if f"b{i:05d}" not in ledger]
        done_n = len(batches) - len(pending)
        progress.begin(chunk.id, STAGE, total=len(batches),
                       message=f"flowlines: {len(comids):,} reaches, {len(pending)} of {len(batches)} "
                               f"requests to go, {config.FABRIC_CONCURRENCY} at a time")
        progress.tick(done=done_n)
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
        with ThreadPoolExecutor(max_workers=max(1, config.FABRIC_CONCURRENCY)) as pool:
            running: dict = {}
            while pending or running:
                while pending and len(running) < config.FABRIC_CONCURRENCY:
                    control.check()
                    key, batch = pending.pop(0)
                    running[pool.submit(fetch, batch)] = key
                finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for future in finished:
                    key = running.pop(future)
                    features = future.result()
                    common.write_part(parts, key, {"type": "FeatureCollection", "features": features})
                    ledger.add(key, n=len(features))
                    done_n += 1
                    progress.tick(done=done_n, message=f"flowlines {done_n}/{len(batches)}")
        features = []
        for _key, payload in common.read_parts(parts):
            features.extend(payload.get("features") or [])
        if not features:
            raise RuntimeError("no flowline geometry came back for this chunk")
        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
        gdf["comid"] = gdf["comid"].astype("int64")
        gdf = gdf.drop_duplicates("comid").sort_values("comid").reset_index(drop=True)
        common.write_parquet(gdf, out)
        west, south, east, north = [float(v) for v in gdf.total_bounds]
        chunk.bbox = common.buffer_bbox([west, south, east, north], config.CHUNK_BUFFER_MI)
        chunk.save(root)
        progress.say(f"flowlines.parquet: {len(gdf):,} lines; bbox "
                     + json.dumps([round(v, 4) for v in chunk.bbox]))
        common.drop_parts(parts)
        ledger.clear()

    common.run_stage(states, chunk.id, STAGE, inputs, work, progress, force=force)
