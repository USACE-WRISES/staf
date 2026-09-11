"""Per-region map tiles: every fetched flowline of the region with its score
(or ``band="pending"`` when unscored), simplified, written as FlatGeobuf and
turned into a PMTiles archive by tippecanoe in Docker."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..paths import DataRoot, atomic_write_text
from ..state import Control, Progress, UnitStates, digest
from ..units import list_chunks, load_huc8_index
from . import common

STAGE = "tiles"
IMAGE = "staf-tippecanoe"
DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "tippecanoe"
TILE_ATTRIBUTES = ("comid", "name", "order", "da", "eci", "band", "phys", "chem", "bio",
                   "prov", "n_rated", "huc4")
MIN_ZOOM, MAX_ZOOM = 4, 12


def docker_ready() -> tuple[bool, str]:
    try:
        out = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"docker not available: {exc}"
    if out.returncode != 0:
        return False, (out.stderr or out.stdout).strip()[:200] or "docker daemon not running"
    return True, f"docker {out.stdout.strip()}"


def image_ready() -> bool:
    out = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, text=True)
    return out.returncode == 0


def build_image(progress: Progress) -> None:
    progress.say(f"building the {IMAGE} image (a few minutes, once) ...")
    subprocess.run(["docker", "build", "-t", IMAGE, str(DOCKERFILE)], check=True)


def scored_huc8s(root: DataRoot, vpu: str) -> list[str]:
    index = load_huc8_index(root)
    out = []
    for huc8, info in index.items():
        if info.get("vpu") == vpu and root.huc8_file(huc8, "scores").exists():
            out.append(huc8)
    return sorted(out)


def _lines_for_vpu(root: DataRoot, vpu: str):
    """Every flowline fetched so far whose HUC8 belongs to the region."""
    import geopandas as gpd
    import pandas as pd
    index = load_huc8_index(root)
    huc8s = {h for h, info in index.items() if info.get("vpu") == vpu}
    frames = []
    for chunk in list_chunks(root):
        if not (set(chunk.huc8s) & huc8s):
            continue
        path = root.chunk_raw(chunk.id, "flowlines")
        if not path.exists():
            continue
        gdf = gpd.read_parquet(path)
        if "reachcode" in gdf.columns:
            gdf = gdf[gdf["reachcode"].astype(str).str[:8].isin(huc8s)]
        frames.append(gdf)
    if not frames:
        return None
    lines = pd.concat(frames, ignore_index=True)
    lines = gpd.GeoDataFrame(lines, geometry="geometry", crs="EPSG:4326")
    return lines.drop_duplicates("comid").reset_index(drop=True)


def _scores_for_vpu(root: DataRoot, vpu: str):
    import pandas as pd
    frames = []
    for huc8 in scored_huc8s(root, vpu):
        frames.append(pd.read_parquet(root.huc8_file(huc8, "scores"),
                                      columns=["comid", "eci", "band", "phys", "chem", "bio",
                                               "provisional", "n_rated"]))
    return pd.concat(frames, ignore_index=True) if frames else None


def run_tiles(root: DataRoot, vpu: str, states: UnitStates, progress: Progress,
              control: Control, *, force: bool = False) -> None:
    huc8s = scored_huc8s(root, vpu)
    stamps = [(h, os.path.getmtime(root.huc8_file(h, "scores"))) for h in huc8s]
    inputs = digest(STAGE, vpu, stamps, TILE_ATTRIBUTES, MIN_ZOOM, MAX_ZOOM, 2)
    folder = root.tiles_dir(vpu)
    out = folder / f"tiles_{vpu}.pmtiles"

    def work():
        import geopandas as gpd
        ok, note = docker_ready()
        if not ok:
            raise RuntimeError(note)
        if not image_ready():
            build_image(progress)
        control.check()
        folder.mkdir(parents=True, exist_ok=True)
        progress.begin(vpu, STAGE, message=f"tiles {vpu}: assembling flowlines")
        lines = _lines_for_vpu(root, vpu)
        if lines is None or not len(lines):
            raise RuntimeError(f"no fetched flowlines for region {vpu} yet")
        scores = _scores_for_vpu(root, vpu)
        if scores is not None:
            lines = lines.merge(scores, on="comid", how="left")
        else:
            for col in ("eci", "band", "phys", "chem", "bio", "provisional", "n_rated"):
                lines[col] = None
        lines["band"] = lines["band"].fillna("pending")
        lines["prov"] = lines["provisional"].fillna(False).astype(bool)
        lines["huc4"] = lines["reachcode"].astype(str).str[:4] if "reachcode" in lines.columns else None
        lines = lines.rename(columns={"gnis_name": "name", "streamorde": "order", "totdasqkm": "da"})
        keep = [c for c in TILE_ATTRIBUTES if c in lines.columns] + ["geometry"]
        lines = lines[keep].copy()
        for col in ("eci", "phys", "chem", "bio", "da"):
            if col in lines.columns:
                lines[col] = lines[col].astype("float64").round(3)
        projected = lines.to_crs(5070)
        projected["geometry"] = projected.geometry.simplify(5.0, preserve_topology=True)
        lines = projected.to_crs(4326)
        fgb = folder / "lines.fgb"
        if fgb.exists():
            fgb.unlink()
        lines.to_file(fgb, driver="FlatGeobuf")
        progress.say(f"tiles {vpu}: {len(lines):,} lines -> lines.fgb; running tippecanoe")
        control.check()
        cmd = ["docker", "run", "--rm", "-v", f"{folder.resolve()}:/data", IMAGE,
               "tippecanoe", "-o", f"/data/{out.name}", "-l", "flowlines",
               f"-Z{MIN_ZOOM}", f"-z{MAX_ZOOM}", "--drop-densest-as-needed",
               "--coalesce-smallest-as-needed", "--simplification=8",
               "--extend-zooms-if-still-dropping", "--force", "--quiet",
               "-n", f"EASI national screening, region {vpu}",
               "/data/lines.fgb"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not out.exists():
            raise RuntimeError(f"tippecanoe failed: {(result.stderr or result.stdout)[-800:]}")
        info = {"vpu": vpu, "huc8s": huc8s, "n_lines": int(len(lines)),
                "n_scored": int((lines["band"] != "pending").sum()),
                "bytes": out.stat().st_size, "minzoom": MIN_ZOOM, "maxzoom": MAX_ZOOM}
        atomic_write_text(folder / "tiles.json", json.dumps(info, indent=1))
        progress.say(f"tiles_{vpu}.pmtiles: {out.stat().st_size / 1e6:.1f} MB, "
                     f"{info['n_scored']:,} scored of {info['n_lines']:,} lines")

    common.run_stage(states, f"vpu-{vpu}", STAGE, inputs, work, progress, force=force)
