"""Candidate scoring in processes with isolated EASI data directories."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import ALTERNATIVES, BASE_METHOD
from .io import fingerprint, read_json, sha, write_json, write_parquet
from builder import EASI_APP


def flatten(report, comid):
    out = {"comid": comid, "eci": report.get("ecosystemConditionIndexRaw"),
           **{key: (report.get("subIndicesRaw") or {}).get(key) for key in ("physical", "chemical", "biological")}}
    for row in report.get("metricRows") or []:
        key = row["functionId"].replace("-", "_")
        trace = row.get("scoring") or {}
        for prefix, field in (("rating", "rating"), ("score", "functionScore"), ("index", "index")):
            out[f"{prefix}__{key}"] = row.get(field)
        out[f"route__{key}"] = trace.get("methodKey")
        out[f"fallback__{key}"] = bool(trace.get("usedFallback"))
        out[f"curves__{key}"] = json.dumps(trace.get("curves") or {}, sort_keys=True, separators=(",", ":"))
        out[f"observed__{key}"] = bool(trace.get("observedOverridesProxy"))
    return out


def worker(study: Path, data_dir: Path, destination: Path, comids=None):
    """Called only in an isolated child, after EASI_DATA_DIR is set."""
    from easi import config
    from easi.national import client, records, method_version
    if config.DATA_DIR.resolve() != data_dir.resolve() or config.criteria_set() != "regional":
        raise RuntimeError("Candidate process loaded the wrong data directory")
    sources = sorted((study / "evidence").rglob("*.parquet"))
    assets = sorted(data_dir.glob("*.json"))
    engine = sorted((EASI_APP / "easi").rglob("*.py"))
    digest, _ = fingerprint([Path(__file__), *assets, *engine], {"comids": sorted(comids) if comids is not None else None})
    parts = destination.with_suffix("").with_name(destination.stem + "-parts")
    output = []
    traces = []
    for index, source in enumerate(sources):
        tag = source.parent.name + "-" + source.stem
        target, marker = parts / (tag + ".parquet"), parts / (tag + ".json")
        key, _ = fingerprint([source], {"assets": digest})
        receipt = read_json(marker) if marker.exists() else {}
        if not target.exists() or receipt.get("input_digest") != key or receipt.get("sha256") != sha(target):
            table = pq.read_table(source)
            if comids is not None:
                import pyarrow.compute as pc
                table = table.filter(pc.is_in(table["comid"], value_set=pa.array(sorted(comids), pa.int64())))
            rows, examples = [], []
            for raw in table.to_pylist():
                report = client.score_record(records.from_row(raw), cross_section=False)
                rows.append(flatten(report, int(raw["comid"])))
                if not examples:
                    examples.append({"comid": int(raw["comid"]), "functions": [
                        {"function": r["functionId"], "rating": r.get("rating"), "index": r.get("index"),
                         "route": (r.get("scoring") or {}).get("methodKey"),
                         "curves": (r.get("scoring") or {}).get("curves", {}),
                         "inputs": (r.get("scoring") or {}).get("inputs", []),
                         "strata": ((r.get("scoring") or {}).get("context") or {}).get("strata", {})}
                        for r in report.get("metricRows") or [] if (r.get("scoring") or {}).get("curves")]})
            if rows:
                write_parquet(target, pa.Table.from_pylist(rows))
            else:
                write_parquet(target, pa.table({"comid": pa.array([], pa.int64())}))
            write_json(marker, {"input_digest": key, "sha256": sha(target), "rows": len(rows), "traces": examples})
            receipt = read_json(marker)
        if receipt["rows"]:
            output.append(target)
            if len(traces) < 48:
                traces.extend(receipt.get("traces") or [])
        if index % 200 == 0:
            print(f"{destination.stem}: evidence parts {index + 1:,}/{len(sources):,}", flush=True)
    frame = pd.concat([pq.read_table(path).to_pandas() for path in output], ignore_index=True).sort_values("comid")
    if frame.comid.duplicated().any():
        raise RuntimeError("Candidate contains duplicate COMIDs")
    write_parquet(destination, frame)
    write_json(destination.with_suffix(".json"), {"rows": len(frame), "method_version": method_version(),
        "input_digest": digest, "data_directory": str(data_dir), "output_sha256": sha(destination)})
    if destination.parent.name == "scores":
        write_json(study / "traces" / (destination.stem + ".json"), {"alternative": destination.stem, "examples": traces})
    return len(frame)


def launch(study, data_dir, destination, *, comids_file=None):
    env = dict(os.environ, EASI_DATA_DIR=str(data_dir), EASI_CRITERIA_SET="regional",
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    command = [sys.executable, "-m", __name__, "--study", str(study), "--data-dir", str(data_dir), "--out", str(destination)]
    if comids_file:
        command += ["--comids", str(comids_file)]
    subprocess.run(command, env=env, check=True)


def verify(study):
    reaches = pq.read_table(study / "cohorts/reaches.parquet").to_pandas().set_index("comid")
    baseline = pq.read_table(study / "scores/alternative-1.parquet").to_pandas().set_index("comid")
    prior = pq.read_table(study / "snapshot/analysis/values.parquet",
        columns=["comid", "eci_raw", "phys_raw", "chem_raw", "bio_raw"] +
        [prefix + key.split("__", 1)[1] for key in baseline.columns if key.startswith("rating__") for prefix in ("rating_", "fs_", "index_")]).to_pandas().set_index("comid")
    original = baseline.loc[baseline.index.intersection(prior.index)]
    fields = {"eci": "eci_raw", "physical": "phys_raw", "chemical": "chem_raw", "biological": "bio_raw"}
    for name in baseline.columns:
        prefix, _, function = name.partition("__")
        if prefix in ("rating", "score", "index"):
            fields[name] = {"rating": "rating_", "score": "fs_", "index": "index_"}[prefix] + function
    mismatches = {}
    for new, old in fields.items():
        left, right = original[new], prior.loc[original.index, old]
        mismatch = ~(left.eq(right) | (left.isna() & right.isna()))
        if mismatch.any():
            mismatches[new] = int(mismatch.sum())
    if mismatches:
        raise RuntimeError(f"Alternative 1 replay mismatch: {mismatches}")
    expected = set(reaches.index[reaches.evidence_available])
    if set(baseline.index) != expected:
        raise RuntimeError("Alternative 1 output does not cover exactly the eligible evidence cohort")
    station_path = study / "snapshot/analysis/nrsa/nrsa_desktop.parquet"
    station_fields = ["comid", "desktop_source", *fields.values()]
    stations = pq.read_table(station_path, columns=station_fields).to_pandas()
    stations = stations[stations.desktop_source.ne("missing")]
    station_mismatches = {}
    for new, old in fields.items():
        expected_values = stations[old].reset_index(drop=True)
        actual_values = baseline.reindex(stations.comid)[new].reset_index(drop=True)
        mismatch = ~(actual_values.eq(expected_values) | (actual_values.isna() & expected_values.isna()))
        if mismatch.any():
            station_mismatches[new] = {"n": int(mismatch.sum()), "comids": stations.loc[mismatch.to_numpy(), "comid"].tolist()[:20]}
    if station_mismatches:
        raise RuntimeError(f"Alternative 1 NRSA replay mismatch: {station_mismatches}")
    allowed = {"light_thermal_regime", "habitat_provision", "carbon_processing", "low_flow_baseflow_dynamics"}
    checks = []
    for number in (2, 3, 4):
        other = pq.read_table(study / f"scores/alternative-{number}.parquet").to_pandas().set_index("comid")
        if not other.index.equals(baseline.index):
            raise RuntimeError("Candidate cohorts differ")
        changes = {}
        for name in fields:
            if "__" not in name:
                continue
            function = name.split("__", 1)[1]
            left, right = baseline[name], other[name]
            changed = ~(left.eq(right) | (left.isna() & right.isna()))
            if number == 4:
                permitted = function in {"light_thermal_regime", "habitat_provision"}
                if changed.any():
                    # NRSA stations outside the stored population may also have Level II 8.2.
                    if not reaches.reindex(changed.index[changed]).l2.astype(str).eq("8.2").all():
                        raise RuntimeError("Woody override escaped Level II 8.2")
            else:
                permitted = function in allowed
            if changed.any() and not permitted:
                raise RuntimeError(f"Unpermitted change: alternative-{number} {function}")
            if not left.isna().equals(right.isna()):
                raise RuntimeError(f"Rating availability changed: alternative-{number} {name}")
            changes[name] = int(changed.sum())
        checks.append({"alternative": f"alternative-{number}", "changed": changes})
    result = {"status": "passed", "replayed_stored_reaches": len(original), "fields_per_reach": len(fields),
              "replayed_nrsa_stations": len(stations), "nrsa_mismatches": station_mismatches,
              "mismatches": mismatches, "candidate_scope": checks}
    write_json(study / "scores/verification.json", result)
    return result


def run(root, study, workers=4):
    with ThreadPoolExecutor(max_workers=min(4, workers)) as pool:
        tasks = [pool.submit(launch, study, study / "candidates" / a["id"] / "app-data",
                             study / "scores" / (a["id"] + ".parquet")) for a in ALTERNATIVES]
        for task in tasks:
            task.result()
    return verify(study)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--comids", type=Path)
    args = parser.parse_args()
    worker(args.study, args.data_dir, args.out, set(read_json(args.comids)) if args.comids else None)
