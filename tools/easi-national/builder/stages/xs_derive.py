"""Cross-section derivation: the archive's raw transects turned into the
reach geometry dict the four cross-section metrics read, with exactly the
app's steps (``easi.datasources.threedep.reach_geomorphology`` after its
sampling loop): drop transects with fewer than seven finite samples,
``geomorph.balanced_profile``, ``geomorph.simplify_profile``, then
``geomorph.candidates_from_transects`` for the nine candidates, the reach
medians and the drawn section. Pure and fast; re-run when the method changes.

``huc8/<huc8>/xsections.parquet``: one row per reach with the dict as JSON
(every profile stripped) plus the drawn section's simplified profile as two
float32 arrays. ``to_published`` rebuilds the slim dict the evidence file
carries: the medians, the reach stats, the candidates' scalars, and the one
drawable candidate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from .. import config
from ..paths import DataRoot
from ..state import Control, Progress, UnitStates, digest
from ..units import Chunk
from . import common

STAGE = "xs_derive"
MIN_FINITE = 7


def xs_method_version() -> str:
    """Hash of the derivation rules: the app's geomorph and threedep modules
    and this file. A change marks every derived HUC8 stale (the app's own
    ``method_version`` leaves these files out on purpose)."""
    import easi.geomorph
    from easi.datasources import threedep
    h = hashlib.sha256()
    for path in (Path(easi.geomorph.__file__), Path(threedep.__file__), Path(__file__)):
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def stations_for(profile: dict):
    import numpy as np
    return np.linspace(-float(profile["wide_m"]), float(profile["wide_m"]), int(profile["n_pts"]))


def derive_reach(profiles: list[dict], line_length_m: float, da_sqkm: float, bankfull: dict,
                 dem_res_m: float) -> dict:
    """``reach_geomorphology``'s result from stored samples ({} when nothing
    usable, exactly as live)."""
    import numpy as np
    from easi import geomorph
    usable = []
    for profile in sorted(profiles, key=lambda p: int(p["k"])):
        ts = stations_for(profile)
        z = np.asarray(profile["z"], dtype=float)
        ok = np.isfinite(z)
        if ok.sum() < MIN_FINITE:
            continue
        balanced = geomorph.balanced_profile(ts[ok].tolist(), z[ok].tolist())
        if balanced is not None:
            st, el = geomorph.simplify_profile(balanced[0], balanced[1])
            usable.append((float(profile["frac"]), st, el))
    if not usable:
        return {}
    width = bankfull.get("width_m")
    depth = bankfull.get("depth_m")
    out = geomorph.candidates_from_transects(
        usable, float(line_length_m), da_sqkm or 1.0,
        bankfull=(width, depth) if width and depth else None,
        bankfull_area_m2=bankfull.get("area_m2"), division=bankfull.get("division_name"),
        dem_res_m=dem_res_m)
    if not out:
        return {}
    # the app stores 1 or 10 (its captions print the value), never 1.0
    out["dem_resolution_m"] = int(dem_res_m) if float(dem_res_m).is_integer() else dem_res_m
    return out


def strip_profiles(full: dict) -> dict:
    """The dict without any profile (JSON for ``xsections.parquet``)."""
    out = {k: v for k, v in full.items() if k != "profile"}
    out["candidates"] = [{k: v for k, v in c.items() if k != "profile"} for c in (full.get("candidates") or [])]
    return out


def selected_profile(full: dict) -> tuple[list[float], list[float]]:
    cands = full.get("candidates") or []
    sel = int(full.get("selected") or 0)
    if not cands or not 0 <= sel < len(cands):
        return [], []
    profile = cands[sel].get("profile") or {}
    return ([round(float(s), 2) for s in profile.get("stations") or []],
            [round(float(e), 3) for e in profile.get("elevs") or []])


def to_published(geomorph_json: Optional[str], stations: Optional[list], elevs: Optional[list]) -> Optional[dict]:
    """The slim dict the evidence file carries: the medians, ``reach``, the
    candidates' scalars are kept in ``candidate_scalars``, and ``candidates``
    holds the one drawable section (its scalars plus the simplified profile)
    so the report draws it; ``selected`` points at it."""
    if geomorph_json is None:
        return None
    full = json.loads(geomorph_json)
    if not full:
        return {}
    cands = full.get("candidates") or []
    sel = int(full.get("selected") or 0)
    out = {k: v for k, v in full.items() if k != "candidates"}
    out["candidate_scalars"] = cands
    if cands and 0 <= sel < len(cands) and stations:
        drawn = dict(cands[sel])
        drawn["profile"] = {"stations": list(stations), "elevs": list(elevs or [])}
        out["candidates"] = [drawn]
        out["selected"] = 0
        out["selected_transect"] = sel
    else:
        out["candidates"] = []
    return out


def _schema():
    import pyarrow as pa
    return pa.schema([
        ("comid", pa.int64()), ("status", pa.string()), ("dem_res_m", pa.float64()),
        ("n_transects", pa.int32()), ("selected", pa.int32()), ("geomorph", pa.string()),
        ("profile_stations", pa.list_(pa.float32())), ("profile_elevs", pa.list_(pa.float32()))])


def sample_stamp(root: DataRoot, huc8: str):
    path = root.huc8_file(huc8, "xs_sample")
    if not path.exists():
        return "none"
    stat = path.stat()
    return [int(stat.st_mtime), stat.st_size]


def xsections_stamp(root: DataRoot, huc8: str):
    """What the score stage keys on: the derived file's mtime and size, or "none"."""
    path = root.huc8_file(huc8, "xsections")
    if not path.exists():
        return "none"
    stat = path.stat()
    return [int(stat.st_mtime), stat.st_size]


def run_xs_derive(root: DataRoot, chunk: Chunk, huc8: str, states: UnitStates, progress: Progress,
                  control: Control, *, force: bool = False) -> None:
    sample_path = root.huc8_file(huc8, "xs_sample")
    profiles_path = root.huc8_file(huc8, "xs_profiles")
    if not sample_path.exists() or not profiles_path.exists():
        raise RuntimeError("the xs_sample stage must run first")
    inputs = digest(STAGE, huc8, chunk.id, sample_stamp(root, huc8), xs_method_version(), 1)

    def work():
        import pyarrow as pa
        import pyarrow.parquet as pq
        derived = pq.read_table(root.huc8_file(huc8, "derived"), columns=["comid", "totdasqkm", "bankfull"]).to_pylist()
        summary = {int(r["comid"]): r for r in pq.read_table(sample_path).to_pylist()}
        by_comid: dict[int, list[dict]] = {}
        for row in pq.read_table(profiles_path).to_pylist():
            by_comid.setdefault(int(row["comid"]), []).append(row)
        progress.begin(huc8, STAGE, total=len(derived), message=f"cross-section derivation {huc8}")
        rows = []
        counts = {"ok": 0, "empty": 0, "not_sampled": 0}
        for i, d in enumerate(sorted(derived, key=lambda r: int(r["comid"]))):
            if i % 200 == 0:
                control.check()
            comid = int(d["comid"])
            info = summary.get(comid)
            if not info or info.get("status") != "ok":
                counts["not_sampled"] += 1
                rows.append({"comid": comid, "status": "not_sampled", "dem_res_m": None, "n_transects": 0,
                             "selected": 0, "geomorph": None, "profile_stations": [], "profile_elevs": []})
                continue
            block = json.loads(d.get("bankfull") or "{}") if isinstance(d.get("bankfull"), str) else (d.get("bankfull") or {})
            full = derive_reach(by_comid.get(comid, []), float(info.get("line_length_m") or 0.0),
                                float(d.get("totdasqkm") or 0.0), block, float(info.get("dem_res_m") or 10.0))
            if not full:
                counts["empty"] += 1
                rows.append({"comid": comid, "status": "empty", "dem_res_m": info.get("dem_res_m"), "n_transects": 0,
                             "selected": 0, "geomorph": "{}", "profile_stations": [], "profile_elevs": []})
                continue
            stations, elevs = selected_profile(full)
            counts["ok"] += 1
            rows.append({"comid": comid, "status": "ok", "dem_res_m": info.get("dem_res_m"),
                         "n_transects": int(full.get("n_transects") or 0), "selected": int(full.get("selected") or 0),
                         "geomorph": json.dumps(strip_profiles(full), separators=(",", ":")),
                         "profile_stations": stations, "profile_elevs": elevs})
            if i % 200 == 0:
                progress.tick(done=i + 1)
        common.write_parquet(pa.Table.from_pylist(rows, schema=_schema()), root.huc8_file(huc8, "xsections"))
        progress.tick(done=len(derived))
        progress.say(f"xsections.parquet {huc8}: {counts['ok']:,} reaches with cross-sections, "
                     f"{counts['empty']} empty, {counts['not_sampled']} not sampled")

    common.run_stage(states, huc8, STAGE, inputs, work, progress, force=force)
