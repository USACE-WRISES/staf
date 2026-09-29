"""Candidate scoring in isolated processes: one method per process.

Two routes, one worker. A 1.0.0 or 1.1.0 study (and every fold refit of the spatial step)
points a child at an app-data folder with ``EASI_DATA_DIR``. A 1.2.0 study scores each arm
through its method package (``EASI_METHOD_PACKAGE=<the arm's .easi-method.zip>``, the
addendum's words: each arm scores an isolated package) with ``EASI_DATA_DIR`` unset: the
vendored-free EASI of ``apps/easi`` materializes the package before ``easi.config`` reads its
data folder, the worker asserts that ``method_package.active()`` reports the expected package
digest before it scores a row, and records it in the destination's ``.json``. A non-method
data file the evaluator needs that the package route cannot serve as the snapshot preserved
it (missing from the materialized folder, or not the snapshot's bytes) is read from the
study's app-data copy for that file only, and recorded.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import ALTERNATIVES, BASE_METHOD, REFERENCE_ID, is_round4, study_candidates  # noqa: F401
from .io import fingerprint, read_json, sha, write_json, write_parquet
from builder import EASI_APP

#: the per-function completeness a score table carries (K5 reporting of the knobs' outputs)
COMPLETENESS = ("rated", "withheld", "missing")


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
    # the knobs' outputs, kept beside the unchanged columns: per function whether the rating is
    # there, withheld as a documented gap (K1, the unscored statement) or missing, and the gap
    # (the rule or the K2 flags that matched, as the worker's gap block spells them); per report
    # the K5 rollup fields, None where the report has none (the base and every family but E8)
    for row in report.get("metricRows") or []:
        key = row["functionId"].replace("-", "_")
        trace = row.get("scoring") or {}
        completeness = row.get("completeness") or trace.get("completeness")
        rated = row.get("rating") is not None
        out[f"completeness__{key}"] = "rated" if rated else ("withheld" if completeness == "withheld" else "missing")
        gap = None
        if not rated and completeness == "withheld":
            applicability = trace.get("applicability") or {}
            gap = {"statement": trace.get("statement"), "rule": applicability.get("rule"),
                   "matched": applicability.get("matched")}
        out[f"gap__{key}"] = (json.dumps(gap, sort_keys=True, separators=(",", ":"), default=str)
                              if gap is not None else None)
    out["functions_rated"] = report.get("functionsRated")
    interval = report.get("ecosystemConditionIndexInterval")
    if isinstance(interval, (list, tuple)) and len(interval) == 2:
        out["eci_interval_lower"], out["eci_interval_upper"] = interval[0], interval[1]
    else:
        out["eci_interval_lower"] = out["eci_interval_upper"] = None
    return out


#: the module attribute each evaluator asset is read through (``method_package._point_modules_at``)
_ASSET_POINTERS = {"ecoregions_l3.geojson": ("geo", "ECOREGIONS_PATH"),
                   "physio_divisions.geojson": ("bieger", "_GEOJSON"),
                   "nrsa-2018-19-evidence.json.gz": ("datasources.nrsa", "DATA_PATH")}


def asset_fallbacks(materialized: Path, app_data: Path) -> list[dict]:
    """The evaluator assets (the non-method data files EASI reads) the package route cannot
    serve as the study preserved them: missing from the materialized folder, or not the
    snapshot app-data's bytes. Each is repointed at the study's copy for that file only, the
    caches cleared, and the file named with the reason. Empty when the route serves them all."""
    import importlib
    from easi import method_package as mp
    out = []
    for name in mp.EVALUATOR_ASSETS:
        preserved = Path(app_data) / name
        if not preserved.is_file():
            continue
        served = Path(materialized) / name
        reason = None
        if not served.is_file():
            reason = "missing from the materialized package folder"
        elif sha(served) != sha(preserved):
            reason = "the materialized copy is not the snapshot's bytes"
        if reason is None:
            continue
        pointer = _ASSET_POINTERS.get(name)
        if pointer is None:
            out.append({"file": name, "reason": reason, "fallback": None,
                        "note": "no evaluator module reads this file in the regional criteria set"})
            continue
        module = importlib.import_module("easi." + pointer[0])
        setattr(module, pointer[1], preserved)
        out.append({"file": name, "reason": reason, "fallback": str(preserved.resolve())})
    if out:
        mp._reset_all_caches()
    return out


def worker(study: Path, data_dir: Path, destination: Path, comids=None, *, package=None, expected_digest=None):
    """Called only in an isolated child: after EASI_DATA_DIR is set (the data-dir route), or
    after EASI_METHOD_PACKAGE named ``package`` and EASI_DATA_DIR was unset (the package route,
    ``data_dir`` then being the study's app-data copy the asset fallbacks read)."""
    from easi import config
    from easi import method_package as mp
    from easi.national import client, records, method_version
    started = time.monotonic()
    route = {"route": "data-dir", "data_directory": str(data_dir)}
    if package is not None:
        active = mp.active()
        if active.get("source") != "package":
            raise RuntimeError("Candidate process did not activate a method package")
        if expected_digest and active.get("packageDigest") != expected_digest:
            raise RuntimeError(f"Candidate process activated package {active.get('packageDigest')}, "
                               f"expected {expected_digest}")
        materialized = Path(active["dataDir"])
        if config.DATA_DIR.resolve() != materialized.resolve() or config.criteria_set() != "regional":
            raise RuntimeError("Candidate process loaded the wrong data directory")
        identity = mp.verify_active()
        fallbacks = asset_fallbacks(materialized, data_dir)
        route = {"route": "package", "package": str(package), "package_digest": active["packageDigest"],
                 "data_directory": str(materialized), "app_data": str(data_dir),
                 "active": {k: active.get(k) for k in ("methodId", "version", "label", "recordedMethodVersion",
                                                       "recordedEvaluatorDigest")},
                 "same_evaluator": identity.get("sameEvaluator"), "asset_fallbacks": fallbacks}
    elif config.DATA_DIR.resolve() != data_dir.resolve() or config.criteria_set() != "regional":
        raise RuntimeError("Candidate process loaded the wrong data directory")
    sources = sorted((study / "evidence").rglob("*.parquet"))
    assets = sorted(data_dir.glob("*.json"))
    engine = sorted((EASI_APP / "easi").rglob("*.py"))
    extra = {"comids": sorted(comids) if comids is not None else None}
    if package is not None:
        extra["package_digest"] = expected_digest or route["package_digest"]
        assets = [*assets, Path(package)]
    digest, _ = fingerprint([Path(__file__), *assets, *engine], extra)
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
        "evaluator_digest": mp.evaluator_digest(), "input_digest": digest, **route,
        "output_sha256": sha(destination), "seconds": round(time.monotonic() - started, 2),
        "unrated_reaches": int(frame["eci"].isna().sum()) if "eci" in frame else None})
    if destination.parent.name == "scores":
        write_json(study / "traces" / (destination.stem + ".json"), {"alternative": destination.stem, "examples": traces})
    return len(frame)


def launch(study, data_dir, destination, *, comids_file=None, package=None, expected_digest=None):
    """One child per arm. The data-dir route sets ``EASI_DATA_DIR``; the package route sets
    ``EASI_METHOD_PACKAGE`` (and the study's own method cache) with ``EASI_DATA_DIR`` unset."""
    env = dict(os.environ, EASI_CRITERIA_SET="regional",
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    command = [sys.executable, "-m", __name__, "--study", str(study), "--data-dir", str(data_dir), "--out", str(destination)]
    if package is not None:
        env.pop("EASI_DATA_DIR", None)
        env["EASI_METHOD_PACKAGE"] = str(package)
        env["EASI_METHOD_CACHE"] = str(Path(study) / "method-cache")
        command += ["--package", str(package)]
        if expected_digest:
            command += ["--expect", str(expected_digest)]
    else:
        env.pop("EASI_METHOD_PACKAGE", None)
        env["EASI_DATA_DIR"] = str(data_dir)
    if comids_file:
        command += ["--comids", str(comids_file)]
    subprocess.run(command, env=env, check=True)


def _replay_base(study, reaches, baseline):
    """Alternative 1 against the snapshot analysis: the stored reaches and the NRSA stations."""
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
    return fields, original, stations, mismatches, station_mismatches


def _changes(fields, baseline, other):
    """Per scored column, how many reaches changed and whether availability changed."""
    changes, availability = {}, {}
    for name in fields:
        if "__" not in name:
            continue
        left, right = baseline[name], other[name]
        changed = ~(left.eq(right) | (left.isna() & right.isna()))
        changes[name] = int(changed.sum())
        availability[name] = not left.isna().equals(right.isna())
    return changes, availability


def verify(study):
    reaches = pq.read_table(study / "cohorts/reaches.parquet").to_pandas().set_index("comid")
    baseline = pq.read_table(study / "scores/alternative-1.parquet").to_pandas().set_index("comid")
    manifest = read_json(study / "manifest.json") if (study / "manifest.json").is_file() else {}
    replayed = None
    if manifest.get("base_scores"):
        # the stored analysis is another base's: the base arm is compared with the base scores
        # (a national build of the study base), never with the stored analysis's scores
        from . import base_scores as bs
        replayed = bs.replay(study, reaches, baseline, manifest["base_scores"])
        fields, original, stations = bs.score_fields(baseline), None, None
        mismatches, station_mismatches = replayed["mismatches"], {}
    else:
        fields, original, stations, mismatches, station_mismatches = _replay_base(study, reaches, baseline)
    checks = []
    if is_round4(manifest):
        # a candidate package's changes are the family's to explain: the cohort must be the
        # base's, and the changed functions and availability are recorded per candidate, with
        # the functions the family declares beside them (never enforced: E7 and E8 change
        # composites and reporting, a documented gap changes availability by design)
        for row in study_candidates(manifest):
            if row["id"] == REFERENCE_ID:
                continue
            other = pq.read_table(study / f"scores/{row['id']}.parquet").to_pandas().set_index("comid")
            if not other.index.equals(baseline.index):
                raise RuntimeError(f"Candidate cohorts differ: {row['id']}")
            changes, availability = _changes(fields, baseline, other)
            declared = row.get("function")
            declared = None if declared in (None, "all") else [str(f).replace("-", "_") for f in (declared if isinstance(declared, list) else [declared])]
            changed_functions = sorted({name.split("__", 1)[1] for name, n in changes.items() if n})
            checks.append({"alternative": row["id"], "family": row.get("family"), "changed": changes,
                           "availability_changed": sorted(name for name, flag in availability.items() if flag),
                           "functions_changed": changed_functions, "functions_declared": declared,
                           "functions_changed_outside_declared": (
                               sorted(set(changed_functions) - set(declared)) if declared is not None else [])})
    else:
        allowed = {"light_thermal_regime", "habitat_provision", "carbon_processing", "low_flow_baseflow_dynamics"}
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
    if replayed is not None:
        result = {"status": "passed", "replay": "base-scores", "fields_per_reach": len(fields),
                  "replayed_stored_reaches": replayed["replayed_stored_reaches"],
                  "replayed_nrsa_stations": replayed["replayed_nrsa_stations"],
                  "compared": replayed["compared"], "absent_from_base_scores": replayed["absent_from_base_scores"],
                  "base_scores": {k: manifest["base_scores"].get(k) for k in ("build_id", "method_version", "alternative_id", "folder")},
                  "nrsa_mismatches": {}, "mismatches": mismatches, "candidate_scope": checks}
    else:
        # the legacy result keeps exactly its 1.0.0 shape: verify rewrites verification.json only
        # when the content differs, and the 2026-09-15 study's must stay the bytes its
        # completion record hashes
        result = {"status": "passed", "replayed_stored_reaches": len(original), "fields_per_reach": len(fields),
                  "replayed_nrsa_stations": len(stations), "nrsa_mismatches": station_mismatches,
                  "mismatches": mismatches, "candidate_scope": checks}
    if is_round4(manifest):
        result["replay"] = "base-scores" if replayed is not None else "stored-analysis"
        result["routes"] = {row["id"]: {k: v for k, v in read_json(study / f"scores/{row['id']}.json").items()
                                        if k in ("route", "package_digest", "method_version", "evaluator_digest",
                                                 "asset_fallbacks", "seconds", "rows", "unrated_reaches")}
                            for row in study_candidates(manifest) if (study / f"scores/{row['id']}.json").is_file()}
    write_json(study / "scores/verification.json", result)
    return result


def run(root, study, workers=4):
    manifest = read_json(study / "manifest.json") if (study / "manifest.json").is_file() else {}
    if is_round4(manifest):
        arms = read_json(study / "candidates/manifest.json")["candidates"]
        with ThreadPoolExecutor(max_workers=max(1, min(len(arms), workers))) as pool:
            tasks = [pool.submit(launch, study, study / "candidates" / a["id"] / "app-data",
                                 study / "scores" / (a["id"] + ".parquet"),
                                 package=a["zip"], expected_digest=a["package_digest"]) for a in arms]
            for task in tasks:
                task.result()
        return verify(study)
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
    parser.add_argument("--package", type=Path, help="the method package zip this process activated (EASI_METHOD_PACKAGE)")
    parser.add_argument("--expect", help="the package digest the process must have activated")
    args = parser.parse_args()
    worker(args.study, args.data_dir, args.out, set(read_json(args.comids)) if args.comids else None,
           package=args.package, expected_digest=args.expect)
