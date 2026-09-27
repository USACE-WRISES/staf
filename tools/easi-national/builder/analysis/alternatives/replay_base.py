"""EA1: replay the base package through ``evaluation_campaign`` on the study's case set and
compare with the study's own base scores.

    python -m builder.analysis.alternatives.replay_base --study <study folder> --package <base zip>
        --campaign <folder> [--cases <cases folder>] [--workers 2] [--out <report.json>]

The case set is ``cases.export_cases`` (written first when the cases folder has no index).
Each chunk is scored by StreamCurves' ``easi_method.campaigns.evaluation_campaign`` with the
package active in the worker (``EASI_METHOD_PACKAGE``), and every case's rating, index and
ECI per function are compared with ``scores/alternative-1.parquet``: identical, or the
differences listed per COMID and function. The report records the package digest the
campaign scored with (the run records the published zip sha and the export's; one package
digest) beside the study's.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import pyarrow.parquet as pq

from . import REFERENCE_ID
from .cases import export_cases
from .io import read_json


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def function_map(package_zip: Path) -> dict:
    """metricId -> functionId (snake_case) from the package's easi-metrics.json."""
    from easi import method_package as mp
    pkg = mp.read_package(Path(package_zip).read_bytes())
    metrics = json.loads(pkg.files["easi-metrics.json"].decode("utf-8"))
    return {m["metricId"]: str(m["functionId"]).replace("-", "_") for m in metrics.get("metrics") or []}, pkg.digest


def _same(a, b, tol=1e-9) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, float) and math.isnan(a):
        a = None
    if isinstance(b, float) and math.isnan(b):
        b = None
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


def compare(results: dict, scores, functions: dict) -> dict:
    """The campaign's results (``{case id: worker result}``) against the study's score rows
    (a DataFrame indexed by COMID): rating, index and ECI per function and COMID."""
    differences, compared, missing = [], 0, []
    for case_id, result in sorted(results.items(), key=lambda kv: int(kv[0])):
        comid = int(case_id)
        if comid not in scores.index:
            missing.append(comid)
            continue
        row = scores.loc[comid]
        compared += 1
        if not _same(result.get("eciRaw"), row.get("eci")):
            differences.append({"comid": comid, "field": "eci", "campaign": result.get("eciRaw"), "study": row.get("eci")})
        for metric_id, item in (result.get("metrics") or {}).items():
            function = functions.get(metric_id)
            if function is None:
                continue
            for name, key in (("rating", "rating"), ("index", "index")):
                column = f"{name}__{function}"
                if column not in scores.columns:
                    continue
                if not _same(item.get(key), row.get(column)):
                    differences.append({"comid": comid, "field": column, "campaign": item.get(key), "study": row.get(column)})
    return {"compared": compared, "missing_in_study": missing, "differences": differences,
            "identical": compared > 0 and not differences and not missing}


def replay(study: Path, package: Path, campaign: Path, *, cases_dir: Path | None = None, workers: int = 2,
           chunk_limit: int | None = None) -> dict:
    from streamcurves.easi_method import campaigns
    study, package, campaign = Path(study), Path(package), Path(campaign)
    cases_dir = Path(cases_dir) if cases_dir is not None else study / "cases"
    index_path = cases_dir / "index.json"
    index = read_json(index_path) if index_path.is_file() else export_cases(study, cases_dir)
    functions, package_digest = function_map(package)
    scores = pq.read_table(study / f"scores/{REFERENCE_ID}.parquet").to_pandas().set_index("comid")
    study_record = read_json(study / f"scores/{REFERENCE_ID}.json") if (study / f"scores/{REFERENCE_ID}.json").is_file() else {}
    chunks = index["chunks"][:chunk_limit] if chunk_limit else index["chunks"]
    summaries, all_differences, compared, missing = [], [], 0, []
    scored_digests = set()
    for chunk in chunks:
        path = Path(chunk["path"])
        if _sha(path) != chunk["sha256"]:
            raise RuntimeError(f"cases chunk changed since it was exported: {path}")
        results, summary = campaigns.evaluation_campaign({"base": package}, path, campaign / path.stem, workers=workers)
        doc = results.get("base") or {}
        identity = doc.get("identity") or {}
        scored_digests.add(identity.get("packageDigest"))
        got = compare(doc.get("results") or {}, scores, functions)
        compared += got["compared"]
        missing.extend(got["missing_in_study"])
        all_differences.extend(got["differences"])
        summaries.append({"chunk": chunk["name"], "rows": chunk["rows"], "compared": got["compared"],
                          "differences": len(got["differences"]), "jobs": summary.get("counts"),
                          "method_version": identity.get("methodVersion")})
    return {"study": study.name, "package": str(package), "package_zip_sha256": _sha(package),
            "package_digest": package_digest, "scored_package_digests": sorted(d for d in scored_digests if d),
            "study_base_package_digest": study_record.get("package_digest"),
            "same_package_digest": bool(scored_digests) and scored_digests == {study_record.get("package_digest")} if study_record.get("package_digest") else None,
            "cases": index["cases"], "chunks": summaries, "compared": compared,
            "missing_in_study": missing[:50], "differences": all_differences[:500],
            "differences_n": len(all_differences),
            "identical": compared > 0 and not all_differences and not missing}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--study", required=True, type=Path)
    ap.add_argument("--package", required=True, type=Path, help="the base method package zip")
    ap.add_argument("--campaign", required=True, type=Path, help="the campaign folder (jobs, resumable)")
    ap.add_argument("--cases", type=Path, help="the cases folder (default <study>/cases; exported when absent)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--chunk-limit", type=int, help="score only the first N chunks (a rehearsal)")
    ap.add_argument("--out", type=Path, help="the report to write (default <campaign>/replay_base.json)")
    a = ap.parse_args(argv)
    report = replay(a.study, a.package, a.campaign, cases_dir=a.cases, workers=a.workers, chunk_limit=a.chunk_limit)
    out = a.out or (Path(a.campaign) / "replay_base.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("differences", "chunks")}, indent=1, default=str))
    return 0 if report["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
