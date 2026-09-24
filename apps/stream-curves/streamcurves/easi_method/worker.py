"""EASI evaluation worker: scores a case set with the method its environment names.

Run as ``python -m streamcurves.easi_method.worker <cases.json> <out.json>`` by
``streamcurves.easi_method.evaluate``. ``EASI_METHOD_PACKAGE`` (set by the parent)
is read by the vendored EASI package when it is first imported, so this process
scores with exactly one method, and the identity it reports is recomputed from the
files it actually loaded.
"""
from __future__ import annotations

import json
import sys
import time


def _flatten(report: dict) -> dict:
    metrics = {}
    for row in report.get("metricRows") or []:
        sc = row.get("scoring") or {}
        metrics[row["metricId"]] = {
            "rating": row.get("rating"), "index": row.get("index"),
            "functionScore": row.get("functionScore"), "status": row.get("status"),
            "methodKey": sc.get("methodKey"), "completeness": sc.get("completeness"),
        }
    cov = report.get("coverage") or {}
    return {"metrics": metrics, "functionScores": report.get("functionScores"),
            "subIndicesRaw": report.get("subIndicesRaw"),
            "eciRaw": report.get("ecosystemConditionIndexRaw"),
            "eci": report.get("ecosystemConditionIndex"),
            "rated": (cov.get("overall") or {}).get("rated"),
            "provisional": bool(cov.get("provisional"))}


def main(argv: list[str]) -> int:
    cases_path, out_path = argv[1], argv[2]
    t0 = time.perf_counter()
    from streamcurves._vendor.easi import method_package as mp
    identity = mp.verify_active()
    from streamcurves._vendor.easi import assessment
    from streamcurves._vendor.easi.national import client
    t_load = time.perf_counter() - t0
    with open(cases_path, encoding="utf-8") as fh:
        cases = json.load(fh)
    results = {}
    t1 = time.perf_counter()
    for case in cases.get("cases") or []:
        report = client.score_record(case["record"], cross_section=False)
        if case.get("ratings"):
            report = assessment.rescore(report, case["ratings"])
        if case.get("observed"):
            report = assessment.apply_observed_evidence(report, case["observed"])
        results[case["id"]] = _flatten(report)
    out = {"identity": identity, "results": results, "loadSeconds": round(t_load, 3),
           "scoreSeconds": round(time.perf_counter() - t1, 3)}
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, sort_keys=True, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
