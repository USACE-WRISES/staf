"""Explicit local Alternative 2 rollout from immutable staged evidence.

This module never enters the automatic queue or calls acquisition/publication.
Only its dedicated review directory is writable. The ordinary national root is
used for read-only geometry/state indexes and the captured Alternative 1 assets.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time
import uuid

from . import EASI_APP, REPO_ROOT
from .paths import DataRoot, atomic_write_text
from .state import Control, Progress, UnitStates, now_iso
from .stages import common, coverage, score, stats, tiles
from .units import list_chunks

BASE_METHOD = "e9f472b31fe5"
ALTERNATIVE = "alternative-2"
EXPECTED_REACHES = 1_357_265
EXPECTED_HUC8S = 1169
META = ("comid", "huc4", "huc8", "vpu", "gnis_name", "streamorde", "totdasqkm",
        "tier", "xs_status", "dem_res_m", "xs_n", "xs_er", "xs_bhr")


class RolloutError(RuntimeError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    atomic_write_text(Path(path), json.dumps(value, sort_keys=True, indent=2) + "\n")


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def asset_path(folder: Path, name: str):
    if not isinstance(name, str) or Path(name).name != name or name in ("", ".", "..") or ":" in name or "\\" in name:
        raise RolloutError("Invalid asset filename")
    result = (folder / name).resolve()
    if result.parent != folder.resolve():
        raise RolloutError("Asset escapes its directory")
    return result


def fingerprint(path: Path):
    stat = path.stat()
    result = {"path": str(path.resolve()), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
              "sha256": coverage.sha256_of(path)}
    if (path.stat().st_size, path.stat().st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        raise RolloutError(f"Input changed while hashing: {path}")
    return result


def unchanged(rows, *, full=True):
    for row in rows:
        path = Path(row["path"])
        stat = path.stat()
        if stat.st_size != row["bytes"] or stat.st_mtime_ns != row["mtime_ns"] or (full and coverage.sha256_of(path) != row["sha256"]):
            raise RolloutError(f"Captured input changed: {path}")


def asset_inventory(manifest):
    blocks = list((manifest.get("assets") or {}).values())
    blocks += list((manifest.get("scores") or {}).values()) + list((manifest.get("tiles") or {}).values())
    blocks += [unit["evidence"] for unit in (manifest.get("units") or {}).values()]
    out = {}
    for block in blocks:
        name, value = block["asset"], block["sha256"]
        if name in out and out[name] != value:
            raise RolloutError("Contradictory asset hashes")
        out[name] = value
    return out


@dataclass(frozen=True)
class RolloutRoot(DataRoot):
    source: Path

    @property
    def national(self):
        return self.source / "national"

    @property
    def chunks(self):
        return self.source / "chunks"

    def ensure(self):
        # Do not call DataRoot.ensure: the read-only source folders are external.
        for path in (self.huc8, self.tiles, self.staging, self.state, self.ledgers):
            path.mkdir(parents=True, exist_ok=True)
        return self


def current_identity(expected_method):
    from easi import config
    from easi.national import method_version
    identity = config.scoring_identity()
    if config.criteria_set() != "regional" or identity.get("alternative_id") != ALTERNATIVE or identity.get("curve_count") != 34:
        raise RolloutError("The active scorer is not the 34-curve regional Alternative 2")
    method_version.cache_clear()
    if method_version() != expected_method:
        raise RolloutError("Active scoring method differs from the explicit expected method")
    return identity


def score_record_digest():
    from easi.national.client import score_record
    return hashlib.sha256(inspect.getsource(score_record).replace("\r\n", "\n").encode()).hexdigest()


def capture_inputs(root, expected_method, expected_reaches):
    source = root.source / "staging"
    manifest_path = source / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("method_version") != BASE_METHOD or manifest.get("reaches_scored") != expected_reaches:
        raise RolloutError("Source is not the expected complete Alternative 1 cohort")
    queue = root.source / "state/queue.json"
    if read_json(queue).get("items"):
        raise RolloutError("Source automatic queue is not empty")
    identity = current_identity(expected_method)
    wanted = {manifest_path, queue}
    inventory = asset_inventory(manifest)
    for name, expected in inventory.items():
        # Old tiles need not be read to produce new tiles; preserve their stamps.
        path = asset_path(source, name)
        if not name.endswith(".pmtiles"):
            if coverage.sha256_of(path) != expected:
                raise RolloutError(f"Damaged source manifest asset: {name}")
            wanted.add(path)
    for name in ("huc8_index.json", "huc4_vpu.json", "huc4.geojson", "comid_huc4.parquet", "comid_state.parquet"):
        wanted.add(root.national / name)
    for chunk in list_chunks(root):
        wanted.add(root.chunk_dir(chunk.id) / "chunk.json")
        flowlines = root.chunk_raw(chunk.id, "flowlines")
        if flowlines.exists():
            wanted.add(flowlines)
    from easi import config
    from easi.national import _METHOD_SOURCES, _METHOD_DATA
    pkg = EASI_APP / "easi"
    wanted.update(pkg / name for name in _METHOD_SOURCES)
    wanted.update((pkg / "metrics").glob("*.py"))
    wanted.update(Path(config.DATA_DIR) / name for name in (config.screening_methods_filename(), *_METHOD_DATA))
    wanted.add(Path(config.DATA_DIR) / "scoring-identity.json")
    wanted.update((Path(__file__), Path(score.__file__), Path(stats.__file__), Path(tiles.__file__), Path(coverage.__file__)))
    inputs = [fingerprint(path) for path in sorted(wanted)]
    stamp_only = [{"path": str(asset_path(source, name)), "bytes": asset_path(source, name).stat().st_size,
                   "mtime_ns": asset_path(source, name).stat().st_mtime_ns, "sha256": expected}
                  for name, expected in inventory.items() if name.endswith(".pmtiles")]
    return {"method_version": expected_method, "scoring_identity": identity,
            "source_manifest_sha256": coverage.sha256_of(manifest_path), "inputs": inputs,
            "preserved_source_tiles": stamp_only, "expected_reaches": expected_reaches,
            "score_record_sha256": score_record_digest()}


def prepare(root, build):
    """Split only the captured staged evidence; preserve raw rows unchanged."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    checkpoint = root.root / "prepared.json"
    if checkpoint.exists():
        prepared = read_json(checkpoint)
        if prepared["build_id"] != build["build_id"]:
            raise RolloutError("Preparation belongs to another build")
        unchanged(prepared["outputs"])
        return prepared["huc8s"]
    manifest = read_json(root.source / "staging/manifest.json")
    seen, huc8s, outputs = set(), [], []
    for vpu, scores_asset in sorted(manifest["scores"].items()):
        source_scores = asset_path(root.source / "staging", scores_asset["asset"])
        table = pq.read_table(source_scores, columns=[*META, "method_version"])
        if set(table["method_version"].to_pylist()) != {BASE_METHOD}:
            raise RolloutError("Source score partition has mixed/stale methods")
        old = {int(row["comid"]): row for row in table.select(META).to_pylist()}
        if len(old) != table.num_rows:
            raise RolloutError("Source scores contain duplicate COMIDs")
        used = set()
        for huc4, unit in sorted(manifest["units"].items()):
            if unit["vpu"] != vpu:
                continue
            evidence = pq.read_table(asset_path(root.source / "staging", unit["evidence"]["asset"]))
            ids = [int(x) for x in evidence["comid"].to_pylist()]
            if len(ids) != unit["n_scored"]:
                raise RolloutError("Source unit row count differs from its manifest")
            if len(set(ids)) != len(ids) or seen.intersection(ids) or not set(ids).issubset(old):
                raise RolloutError("Evidence COMIDs are duplicate or absent from source scores")
            seen.update(ids); used.update(ids)
            for huc8 in sorted(set(evidence["huc8"].to_pylist())):
                if not isinstance(huc8, str) or len(huc8) != 8 or not huc8.isdigit() or not huc8.startswith(huc4) or huc8 in huc8s:
                    raise RolloutError("Invalid or repeated HUC8 in evidence")
                part = evidence.filter(pc.equal(evidence["huc8"], huc8))
                if any(old[int(x)]["huc8"] != huc8 or old[int(x)]["huc4"] != huc4 for x in part["comid"].to_pylist()):
                    raise RolloutError("Stored evidence/score HUC identity differs")
                metadata = pa.Table.from_pylist([old[int(x)] for x in part["comid"].to_pylist()])
                for name, payload in (("evidence", part), ("source_metadata", metadata)):
                    path = root.huc8_file(huc8, name)
                    common.write_parquet(payload, path)
                    outputs.append(fingerprint(path))
                huc8s.append(huc8)
        if used != set(old):
            raise RolloutError("Source staged scores/evidence COMID cohorts differ")
    if len(seen) != build["expected_reaches"]:
        raise RolloutError("Unexpected source cohort size")
    if build["expected_reaches"] == EXPECTED_REACHES and len(huc8s) != EXPECTED_HUC8S:
        raise RolloutError("Unexpected source HUC8 count")
    write_json(checkpoint, {"build_id": build["build_id"], "huc8s": sorted(huc8s), "reaches": len(seen), "outputs": outputs})
    return sorted(huc8s)


@contextmanager
def offline():
    original = socket.socket.connect
    def denied(*_args, **_kwargs):
        raise RolloutError("Network access is forbidden during stored-evidence scoring")
    socket.socket.connect = denied
    try:
        yield
    finally:
        socket.socket.connect = original


def score_huc8(folder, source, huc8, build):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from easi.national import client, records
    root = RolloutRoot(Path(folder), Path(source))
    current_identity(build["method_version"])
    destination = root.huc8_file(huc8, "scores")
    marker = destination.with_suffix(".json")
    if marker.exists() and destination.exists():
        prior = read_json(marker)
        if prior.get("build_id") == build["build_id"] and prior.get("sha256") == coverage.sha256_of(destination):
            return {"huc8": huc8, "rows": prior["rows"], "resumed": True}
        raise RolloutError(f"Damaged/mismatched completed HUC8: {huc8}")
    evidence = pq.read_table(root.huc8_file(huc8, "evidence"))
    meta = {int(r["comid"]): r for r in pq.read_table(root.huc8_file(huc8, "source_metadata")).to_pylist()}
    rows = []
    with offline():
        for raw in evidence.to_pylist():
            comid = int(raw["comid"])
            report = client.score_record(records.from_row(raw), cross_section=False)
            rows.append({**meta[comid], **score.flatten_report(report, {}),
                         "method_version": build["method_version"], "computed_at": now_iso(),
                         "alternative_id": ALTERNATIVE, "build_id": build["build_id"],
                         "source_manifest_sha256": build["source_manifest_sha256"]})
    common.write_parquet(pa.Table.from_pylist(rows), destination)
    write_json(marker, {"build_id": build["build_id"], "rows": len(rows), "sha256": coverage.sha256_of(destination)})
    return {"huc8": huc8, "rows": len(rows), "resumed": False}


def validate_scores(root, huc8s, build):
    import pyarrow.parquet as pq
    from easi import config
    seen, count = set(), 0
    fids = [f["id"].replace("-", "_") for f in config.functions()]
    expected = {"method_version": build["method_version"], "alternative_id": ALTERNATIVE,
                "build_id": build["build_id"], "source_manifest_sha256": build["source_manifest_sha256"]}
    for huc8 in huc8s:
        evidence_ids = pq.read_table(root.huc8_file(huc8, "evidence"), columns=["comid"])["comid"].to_pylist()
        columns = ["comid", *expected]
        columns += [prefix + fid for fid in fids for prefix in ("rating_", "index_", "fs_")]
        table = pq.read_table(root.huc8_file(huc8, "scores"), columns=columns)
        ids = table["comid"].to_pylist()
        if len(set(ids)) != len(ids) or seen.intersection(ids) or set(ids) != set(evidence_ids):
            raise RolloutError(f"Score/evidence COMID mismatch: {huc8}")
        seen.update(ids); count += len(ids)
        for key, value in expected.items():
            if set(table[key].to_pylist()) != {value}:
                raise RolloutError(f"Mixed/stale score identity: {huc8} {key}")
        for fid in fids:
            for rating,index,fs in zip(*(table[prefix+fid].to_pylist() for prefix in ("rating_","index_","fs_"))):
                if rating is None:
                    valid = index is None and fs is None
                else:
                    anchor = config.RATING_INDEX.get(rating)
                    valid = anchor is not None and index == anchor and fs == round(anchor*15)
                if not valid:
                    raise RolloutError(f"Invalid rating/index/score: {huc8} {fid}")
    if count != build["expected_reaches"]:
        raise RolloutError("Score cohort is incomplete")
    return {"passed": True, "reaches": count, "huc8s": len(huc8s), "functions": len(fids),
            "rating_anchor_pairs_checked": count*len(fids), "comid_mismatches": 0}


def finish_staging(root, build, huc8s, progress):
    import pyogrio
    from easi.national import method_version
    validation = validate_scores(root, huc8s, build)
    validation["tile_inputs"] = validate_tile_inputs(root, huc8s, build)
    manifest = coverage.run_staging(root, UnitStates(root), progress)
    if manifest["method_version"] != build["method_version"] or method_version() != build["method_version"]:
        raise RolloutError("Scoring method changed before staging")
    source = read_json(root.source / "staging/manifest.json")
    binding = {"alternative_id": ALTERNATIVE, "method_version": build["method_version"],
               "build_id": build["build_id"], "source_manifest_sha256": build["source_manifest_sha256"]}
    # Preserve original evidence bytes, including row order and source metadata.
    for huc4, unit in manifest["units"].items():
        original = source["units"][huc4]["evidence"]
        shutil.copyfile(asset_path(root.source/"staging", original["asset"]), root.staging/original["asset"])
        unit["evidence"] = coverage._asset(root.staging/original["asset"])
        if unit["evidence"]["sha256"] != original["sha256"]:
            raise RolloutError("Staged evidence bytes differ from captured source")
    stat_path = root.staging / "stats.json"
    data = read_json(stat_path)
    if data["reaches"] != build["expected_reaches"] or data["method_version"] != build["method_version"]:
        raise RolloutError("Statistics identity/cohort mismatch")
    data.update(binding)
    stats.write_stats(root.staging, data)
    manifest["assets"]["stats.json"] = coverage._asset(stat_path)
    geo_path = root.staging / "coverage.geojson"
    geo = read_json(geo_path); geo.update(binding); write_json(geo_path, geo)
    manifest["assets"]["coverage.geojson"] = coverage._asset(geo_path)
    for vpu, block in manifest["tiles"].items():
        bounds = list(pyogrio.read_info(root.tiles_dir(vpu)/"lines.fgb")["total_bounds"])
        block.update(binding, bounds=bounds)
    if set(manifest["tiles"]) != set(source["scores"]):
        raise RolloutError("Missing tile archives for scored regions")
    for block in manifest["scores"].values():
        block.update(binding)
    manifest.update(binding, build_status="complete", scoring_identity=build["scoring_identity"])
    unchanged(build["inputs"])
    unchanged(build["preserved_source_tiles"], full=False)
    if score_record_digest() != build["score_record_sha256"]:
        raise RolloutError("Shared score_record adapter entry point changed")
    artifacts = asset_inventory(manifest)
    receipt = {"schema_version":1, "status":"complete", **binding, "completed_at":now_iso(),
               "scoring_identity":build["scoring_identity"], "validation":validation,
               "source_inputs_digest":canonical_digest(build["inputs"]), "artifacts":artifacts,
               "artifact_inventory_sha256":canonical_digest(artifacts)}
    write_json(root.staging/"completion.json", receipt)
    manifest["assets"]["completion.json"] = coverage._asset(root.staging/"completion.json")
    # The only consumer-visible completion point is this final atomic replacement.
    write_json(root.staging/"manifest.json", manifest)
    verify_completed(root.staging, build["method_version"])
    return receipt


def validate_tile_inputs(root, huc8s, build):
    """Compare the global FGB input cohort and all four displayed indices."""
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq
    import pyogrio
    columns = ["comid", "eci", "phys", "chem", "bio", "band"]
    expected = pd.concat([pq.read_table(root.huc8_file(h,"scores"),columns=columns).to_pandas()
                          for h in huc8s],ignore_index=True).set_index("comid")
    seen = set()
    for folder in sorted(root.tiles.iterdir()):
        if not folder.is_dir():
            continue
        data = pyogrio.read_dataframe(folder/"lines.fgb",columns=columns,read_geometry=False)
        data = data.loc[data.band.ne("pending")].set_index("comid")
        if data.index.duplicated().any() or seen.intersection(data.index) or not set(data.index).issubset(expected.index):
            raise RolloutError("Tile input COMIDs duplicated or outside the score cohort")
        seen.update(data.index)
        scores = expected.loc[data.index]
        if not data.band.eq(scores.band).all():
            raise RolloutError("Tile input rating bands differ from scores")
        for name in ("eci","phys","chem","bio"):
            target=scores[name].astype("float64").round(3).to_numpy()
            if not np.isclose(data[name].to_numpy(dtype="float64"),target,atol=1e-12,rtol=0,equal_nan=True).all():
                raise RolloutError(f"Tile input {name} differs from scores")
    if seen != set(expected.index) or len(seen) != build["expected_reaches"]:
        raise RolloutError("Tile input global scored COMID coverage is incomplete")
    return {"passed":True,"scored_comids":len(seen),"indices_checked":4*len(seen),
            "band_mismatches":0,"index_mismatches":0,"absolute_tolerance":1e-12,
            "comparison":"canonical pre-tippecanoe FGB, displayed indices rounded to 3 places"}


def verify_completed(staging, expected_method):
    manifest = read_json(staging/"manifest.json")
    if manifest.get("build_status") != "complete" or manifest.get("alternative_id") != ALTERNATIVE or manifest.get("method_version") != expected_method:
        raise RolloutError("Dataset is incomplete or belongs to another method/alternative")
    assets = asset_inventory(manifest)
    if "completion.json" not in assets:
        raise RolloutError("Completion asset missing")
    for name, expected in assets.items():
        if coverage.sha256_of(asset_path(staging,name)) != expected:
            raise RolloutError(f"Damaged output artifact: {name}")
    receipt = read_json(staging/"completion.json")
    for key in ("alternative_id", "method_version", "build_id", "source_manifest_sha256", "scoring_identity"):
        if receipt.get(key) != manifest.get(key):
            raise RolloutError(f"Completion provenance mismatch: {key}")
    artifacts = {k:v for k,v in assets.items() if k != "completion.json"}
    if receipt.get("status") != "complete" or receipt.get("validation",{}).get("passed") is not True or receipt.get("artifacts") != artifacts or receipt.get("artifact_inventory_sha256") != canonical_digest(artifacts):
        raise RolloutError("Completion validation/artifact binding failed")
    if manifest.get("reaches_scored") != receipt["validation"].get("reaches"):
        raise RolloutError("Completion cohort differs from manifest")
    return receipt


@contextmanager
def exclusive_run(folder):
    path = folder/"run.lock"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        os.write(fd, json.dumps({"pid":os.getpid(), "started_at":now_iso()}).encode())
        yield
    finally:
        os.close(fd)
        path.unlink()


def run(source, destination, expected_method, workers=8, *, expected_reaches=EXPECTED_REACHES):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination != source/"review/alternative-2-rollout":
        raise RolloutError("Output must be the dedicated source/review/alternative-2-rollout directory")
    root = RolloutRoot(destination,source).ensure()
    with exclusive_run(destination):
        build_path = destination/"build.json"
        captured = capture_inputs(root,expected_method,expected_reaches)
        if build_path.exists():
            build = read_json(build_path)
            if any(build.get(k)!=v for k,v in captured.items()):
                raise RolloutError("Resume inputs/identity differ from the captured build")
        else:
            build = {**captured,"schema_version":1,"build_id":uuid.uuid4().hex,"created_at":now_iso()}
            write_json(build_path,build)
        if (root.staging/"completion.json").exists() and (root.staging/"manifest.json").exists() and read_json(root.staging/"manifest.json").get("build_status") == "complete":
            return verify_completed(root.staging,expected_method)
        ok,note = tiles.docker_ready()
        if not ok or not tiles.image_ready():
            raise RolloutError("Prepared canonical tippecanoe Docker image required: "+note)
        progress = Progress(root)
        started = time.monotonic()
        huc8s = prepare(root,build)
        progress.begin("alternative-2","score",total=len(huc8s),message="stored-evidence rollout")
        import pyarrow.parquet as pq
        ordered = sorted(huc8s,key=lambda h:-pq.read_metadata(root.huc8_file(h,"evidence")).num_rows)
        with ProcessPoolExecutor(max_workers=max(1,min(8,workers))) as pool:
            worker_binding = {k: build[k] for k in ("method_version", "build_id", "source_manifest_sha256")}
            futures=[pool.submit(score_huc8,str(destination),str(source),h,worker_binding) for h in ordered]
            for number,future in enumerate(as_completed(futures),1):
                result=future.result()
                progress.tick(done=number,message=f"{number}/{len(huc8s)} HUC8s scored; {result['huc8']}")
                if number%20==0 or number==len(huc8s): progress.say(progress.message)
        validation = validate_scores(root,huc8s,build)
        write_json(destination/"score_validation.json",validation)
        unchanged(build["inputs"])
        states = UnitStates(root)
        source_manifest=read_json(source/"staging/manifest.json")
        for vpu in sorted(source_manifest["scores"]):
            marker=root.tiles_dir(vpu)/"rollout.json"
            archive=root.tiles_dir(vpu)/f"tiles_{vpu}.pmtiles"
            if marker.exists():
                saved=read_json(marker)
                if saved.get("build_id")!=build["build_id"] or not archive.exists() or saved.get("sha256")!=coverage.sha256_of(archive):
                    raise RolloutError("Damaged/mismatched completed tile archive")
                unchanged(saved["inputs"])
            else:
                tiles.run_tiles(root,vpu,states,progress,Control(root),force=True)
                write_json(marker,{"build_id":build["build_id"],"sha256":coverage.sha256_of(archive),
                    "inputs":[fingerprint(root.tiles_dir(vpu)/name) for name in ("lines.fgb","tiles.json")]})
        result=finish_staging(root,build,huc8s,progress)
        write_json(destination/"timing.json",{"seconds":round(time.monotonic()-started,2),"completed_at":now_iso(),"build_id":build["build_id"]})
        progress.finish("Alternative 2 local rollout verified complete")
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=Path("D:/Data/easi-national"))
    parser.add_argument("--output",type=Path)
    parser.add_argument("--expected-method",required=True)
    parser.add_argument("--workers",type=int,default=8)
    parser.add_argument("--verify-only",action="store_true")
    args=parser.parse_args()
    if Path(sys.executable).resolve() != (REPO_ROOT/".venv/Scripts/python.exe").resolve():
        raise RolloutError("Use the workspace .venv interpreter")
    for key in ("OPENBLAS_NUM_THREADS","OMP_NUM_THREADS","MKL_NUM_THREADS"):
        os.environ[key]="1"
    output=args.output or args.source/"review/alternative-2-rollout"
    result=verify_completed(output/"staging",args.expected_method) if args.verify_only else run(args.source,output,args.expected_method,args.workers)
    print(json.dumps({k:v for k,v in result.items() if k!="artifacts"},indent=2),flush=True)


if __name__=="__main__":
    main()
