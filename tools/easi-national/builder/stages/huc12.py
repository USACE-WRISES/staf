"""HUC12 polygons for a chunk (fabric ``nhdplusv2-huc12`` by bbox), kept for
the chunk's HUC8s: the point-in-polygon HUC12 lookup the app performs
(``wbd.huc12_at_point``) and the NAS HUC12 list."""
from __future__ import annotations

from .. import config, http
from ..paths import DataRoot
from ..state import Control, Ledger, Progress, UnitStates
from ..units import Chunk
from . import common

STAGE = "huc12"
SOURCE = "huc12"
URL = f"{config.FABRIC_BASE}/nhdplusv2-huc12/items"
PROPERTIES = "huc_12,huc_8,hu_12_name,states"


def fetch_huc12_page(bbox: list[float], offset: int, limit: int = 1000) -> list[dict]:
    params = {"f": "json", "limit": limit, "offset": offset, "properties": PROPERTIES,
              "bbox": ",".join(f"{v:.6f}" for v in bbox)}
    data = http.get_json(URL, params)
    return list(data.get("features") or [])


def run_huc12(root: DataRoot, chunk: Chunk, states: UnitStates, progress: Progress,
              control: Control, *, force: bool = False, fetch=fetch_huc12_page) -> None:
    if not chunk.bbox:
        raise RuntimeError("the geometry stage must run first (no chunk bbox)")
    inputs = common.chunk_inputs(STAGE, chunk, [round(v, 3) for v in chunk.bbox], 1)
    out = root.chunk_raw(chunk.id, SOURCE)

    def work():
        import geopandas as gpd
        from .local_gdb import huc12_for
        local = huc12_for(root, chunk.huc8s)
        if local is not None:
            progress.begin(chunk.id, STAGE, total=1, message="HUC12 polygons from the national geodatabase")
            common.write_parquet(local, out)
            progress.tick(done=1)
            progress.say(f"huc12.parquet: {len(local):,} subwatersheds from the national geodatabase")
            return
        parts = common.parts_dir(root, chunk.id, SOURCE)
        ledger = Ledger(root, f"{SOURCE}-{chunk.id}")
        progress.begin(chunk.id, STAGE, message="HUC12 polygons")
        offset, page = 0, 0
        while True:
            key = f"p{page:04d}"
            if key in ledger:
                payload = dict(common.read_parts(parts)).get(key) or {}
                n = len(payload.get("features") or [])
            else:
                control.check()
                features = fetch(chunk.bbox, offset)
                common.write_part(parts, key, {"type": "FeatureCollection", "features": features})
                ledger.add(key, n=len(features))
                n = len(features)
            progress.tick(done=page + 1, message=f"HUC12 page {page + 1}: {n} polygons")
            if n < 1000:
                break
            offset += n
            page += 1
        features = []
        for _key, payload in common.read_parts(parts):
            features.extend(payload.get("features") or [])
        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326") if features else \
            gpd.GeoDataFrame({"huc_12": [], "huc_8": []}, geometry=[], crs="EPSG:4326")
        if len(gdf):
            gdf["huc_12"] = gdf["huc_12"].astype(str)
            gdf["huc_8"] = gdf["huc_8"].astype(str)
            keep = gdf["huc_8"].isin(set(chunk.huc8s)) | gdf["huc_12"].str[:8].isin(set(chunk.huc8s))
            gdf = gdf[keep].drop_duplicates("huc_12").sort_values("huc_12").reset_index(drop=True)
        common.write_parquet(gdf, out)
        progress.say(f"huc12.parquet: {len(gdf):,} subwatersheds")
        common.drop_parts(parts)
        ledger.clear()

    common.run_stage(states, chunk.id, STAGE, inputs, work, progress, force=force)
