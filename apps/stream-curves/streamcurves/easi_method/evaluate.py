"""Score EASI method versions on a case set, each in its own worker process.

StreamCurves' own copy of EASI (``_vendor/easi``) is the operational method: DEEP
builds read its pressure screen, catalog and CWA mapping. A draft or any other
method version is therefore never loaded in the app's process. Each evaluation
starts ``python -m streamcurves.easi_method.worker`` with ``EASI_METHOD_PACKAGE``
pointing at the version's method package (or unset for the built-in method), and
the result carries the identity the worker actually scored with, which is checked
against the one requested.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from .._vendor.easi import method_package as mp
from .model import EasiProject

APP_ROOT = Path(__file__).resolve().parents[2]
THREAD_CAPS = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
               "NUMEXPR_NUM_THREADS": "1"}


class EvaluationError(RuntimeError):
    pass


def method_cache_root() -> Path:
    """Where workers keep materialized method folders (the app's data root when set)."""
    base = os.environ.get("STREAMCURVES_DATA_ROOT")
    root = Path(base) / "cache" / "easi-methods" if base else Path(tempfile.gettempdir()) / "easi-methods"
    root.mkdir(parents=True, exist_ok=True)
    return root


def worker_env(package_path: Optional[Path]) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("EASI_DATA_DIR", "EASI_METHOD_PACKAGE", "EASI_CRITERIA_SET")}
    env.update(THREAD_CAPS)
    env["EASI_CRITERIA_SET"] = "regional"
    env[mp.WORKER_ENV] = "1"   # the vendored EASI honors EASI_METHOD_PACKAGE only in a worker
    env["EASI_METHOD_CACHE"] = str(method_cache_root())
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if package_path is not None:
        env["EASI_METHOD_PACKAGE"] = str(package_path)
    return env


def run_cases(package: Optional[mp.MethodPackage], cases: dict, *, timeout: float = 900.0) -> dict:
    """Results of ``cases`` under ``package`` (None = the built-in method), with identity."""
    with tempfile.TemporaryDirectory(prefix="sc-easi-eval-") as tmp:
        tmp = Path(tmp)
        pkg_path = None
        if package is not None:
            pkg_path = tmp / "method.zip"
            pkg_path.write_bytes(mp.to_zip(package))
        cases_path, out_path = tmp / "cases.json", tmp / "out.json"
        cases_path.write_text(json.dumps(cases), encoding="utf-8")
        t0 = time.perf_counter()
        proc = subprocess.run([sys.executable, "-B", "-m", "streamcurves.easi_method.worker",
                               str(cases_path), str(out_path)],
                              cwd=str(APP_ROOT), env=worker_env(pkg_path), capture_output=True,
                              text=True, timeout=timeout)
        if proc.returncode != 0 or not out_path.is_file():
            tail = (proc.stderr or proc.stdout or "")[-1500:]
            raise EvaluationError(f"the EASI worker failed (exit {proc.returncode}): {tail}")
        out = json.loads(out_path.read_text(encoding="utf-8"))
        out["wallSeconds"] = round(time.perf_counter() - t0, 2)
    ident = out.get("identity") or {}
    if package is not None:
        if ident.get("source") != "package" or ident.get("packageDigest") != package.digest:
            raise EvaluationError("the worker scored with a different method than requested")
    elif ident.get("source") != "built-in":
        raise EvaluationError("the worker did not score with the built-in method")
    return out


def run_project(project: EasiProject, cases: Optional[dict] = None, **kw) -> dict:
    from .io import consumer_package
    return run_cases(consumer_package(project), cases or project.cases or {"cases": []}, **kw)


def compare(base: dict, draft: dict) -> dict:
    """Case-by-case consequences of a draft against its base: which functions change
    rating, how many cases move, and the ECI shift."""
    b, d = base.get("results") or {}, draft.get("results") or {}
    changed_cases, by_metric, eci = [], {}, []
    for cid in sorted(set(b) & set(d)):
        rb, rd = b[cid], d[cid]
        moved = []
        for mid in sorted(set(rb.get("metrics", {})) | set(rd.get("metrics", {}))):
            x = (rb.get("metrics") or {}).get(mid) or {}
            y = (rd.get("metrics") or {}).get(mid) or {}
            if (x.get("rating"), x.get("status")) != (y.get("rating"), y.get("status")):
                moved.append({"metricId": mid, "before": x.get("rating"), "after": y.get("rating")})
                by_metric.setdefault(mid, {"cases": 0, "transitions": {}})
                by_metric[mid]["cases"] += 1
                key = f"{x.get('rating')} -> {y.get('rating')}"
                by_metric[mid]["transitions"][key] = by_metric[mid]["transitions"].get(key, 0) + 1
        if rb.get("eciRaw") is not None and rd.get("eciRaw") is not None:
            eci.append(rd["eciRaw"] - rb["eciRaw"])
        if moved:
            changed_cases.append({"case": cid, "changes": moved})
    return {"cases": len(set(b) & set(d)), "casesChanged": len(changed_cases),
            "byMetric": by_metric, "changedCases": changed_cases[:200],
            "eciShift": {"n": len(eci), "min": min(eci) if eci else None,
                         "max": max(eci) if eci else None,
                         "meanAbs": (sum(abs(x) for x in eci) / len(eci)) if eci else None},
            "identity": {"base": base.get("identity"), "draft": draft.get("identity")}}
