"""EASI work that fans out: refits and many-method evaluations as resumable campaigns.

Both run on ``streamcurves.jobs``: one job per unit of work, a few processes at a time, each
job's result kept under its content-derived id, so a campaign run twice (or resumed after an
interruption) gives the same outputs and redoes nothing already done.

- ``refit_campaign``: one job per fitted quantity (all its levels and strata), merged in the
  registry's order: the same rows ``refit.fit_registry`` gives in one process.
- ``evaluation_campaign``: one job per method package, each scoring the same case set in its
  own interpreter with that package active (``EASI_METHOD_PACKAGE``), so any number of
  method variants can be compared at once without ever loading two in one process.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Iterable, Optional

from .. import jobs


def _key(r: dict) -> tuple:
    return (r["quantity"], r["level"], r["stratum"], r.get("split") or "")


@contextlib.contextmanager
def _locked(campaign: Path, kw: dict):
    """``run``'s keywords with the campaign's lock held for the run and for reading what its jobs
    wrote (another run could otherwise clean an output folder while it is read); the caller's
    lock when it passes ``lock_held``."""
    if kw.get("lock_held") is not None:
        yield kw
        return
    with jobs.lock(Path(campaign)) as held:
        yield {**kw, "lock_held": held}


# --------------------------------------------------------------------------- #
# refits
# --------------------------------------------------------------------------- #
def fit_quantity(spec: dict, out_dir: Path) -> dict:
    """Job target: every fit of one quantity from a members package folder (grouped by
    stratum only when the spec says ``byStratumOnly``, the Round 4 candidate sets)."""
    from . import refit
    members, values, panels = refit.load_members(Path(spec["members"]))
    plain = [spec["quantity"]] if spec.get("byStratumOnly") else ()
    rows = refit.fit_registry(members, values, panels, quantities=[spec["quantity"]],
                              by_stratum_only=plain)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "rows.json").write_text(json.dumps(rows, sort_keys=True, default=float) + "\n",
                                             encoding="utf-8")
    return {"fits": len(rows), "quantity": spec["quantity"],
            "recipe": refit.recipe_check(Path(spec["members"]))}


def refit_jobs(members_dir: Path, *, members_digest: str,
               quantities: Optional[Iterable[str]] = None,
               by_stratum_only: Iterable[str] = ()) -> list[jobs.Job]:
    """One job per quantity. A quantity in ``by_stratum_only`` is fitted per stratum at every
    level (its spec says so, and so its job id differs from the registry fit of the same
    quantity); every other spec is exactly what it was."""
    from .. import evidence_store as evs
    from . import fit_recipe as fr
    got = evs.verify_folder(Path(members_dir))
    if not got["ok"] or got["dataDigest"] != members_digest:
        raise ValueError("the members package does not verify as the one named")
    wanted = list(quantities) if quantities is not None else list(fr.QUANTITIES)
    plain = set(by_stratum_only or ())
    code = jobs.tree_fingerprint(jobs.APP_ROOT, ("streamcurves",))
    out = []
    for q in wanted:
        spec = {"task": "easi-refit", "quantity": q, "members": str(members_dir),
                "membersDigest": got["dataDigest"], "membersPackageDigest": got["packageDigest"],
                "code": code}
        if q in plain:
            spec["byStratumOnly"] = True
        out.append(jobs.Job(kind="python", target="streamcurves.easi_method.campaigns:fit_quantity",
                            spec=spec, label=f"refit {q}" + (" by stratum" if q in plain else "")))
    return out


def refit_campaign(members_dir: Path, campaign: Path, *, members_digest: str, workers: int = 2,
                   quantities: Optional[Iterable[str]] = None,
                   by_stratum_only: Iterable[str] = (), **kw) -> tuple[list[dict], dict]:
    """``(rows, summary)``: the registry rows of every quantity, in the registry's order."""
    from . import fit_recipe as fr
    js = refit_jobs(members_dir, members_digest=members_digest, quantities=quantities,
                    by_stratum_only=by_stratum_only)
    rows: list[dict] = []
    with _locked(campaign, kw) as kw:
        summary = jobs.run(js, campaign, workers=workers,
                           meta={"task": "easi-refit", "members": members_digest}, **kw)
        order = {q: i for i, q in enumerate(fr.QUANTITIES)}
        for job in sorted(js, key=lambda j: order.get(j.spec["quantity"], 999)):
            rec = jobs.completed(campaign, job)
            if rec is None:
                continue
            rows.extend(json.loads((Path(campaign) / "jobs" / job.id / "out" / "rows.json")
                                   .read_text(encoding="utf-8")))
    return rows, summary


# --------------------------------------------------------------------------- #
# many method packages on one case set
# --------------------------------------------------------------------------- #
def score_cases(spec: dict, out_dir: Path) -> dict:
    """Job target: the case set scored with whatever method this interpreter's environment
    activates (the job sets ``EASI_METHOD_PACKAGE``); the identity scored is recorded."""
    from ..easi_method import worker
    cases = json.loads(Path(spec["cases"]).read_text(encoding="utf-8"))
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    tmp_cases = Path(out_dir) / "cases.json"
    tmp_cases.write_text(json.dumps(cases), encoding="utf-8")
    out = Path(out_dir) / "results.json"
    worker.main(["worker", str(tmp_cases), str(out)])
    tmp_cases.unlink()
    doc = json.loads(out.read_text(encoding="utf-8"))
    ident = doc.get("identity") or {}
    for key in ("loadSeconds", "scoreSeconds"):
        doc.pop(key, None)                      # timings are not results
    out.write_text(json.dumps(doc, sort_keys=True) + "\n", encoding="utf-8")
    if spec.get("packageDigest") and ident.get("packageDigest") != spec["packageDigest"]:
        raise RuntimeError("the job scored with a different method than it names")
    return {"methodVersion": ident.get("methodVersion"), "packageDigest": ident.get("packageDigest"),
            "cases": len(doc.get("results") or {})}


def evaluation_jobs(packages: dict[str, Path], cases_path: Path, *, cache_dir: Path) -> list[jobs.Job]:
    """One job per method package (``label -> package zip``), each with its package active."""
    from .._vendor.easi import method_package as mp
    cases_sha = jobs.sha_file(cases_path)
    code = jobs.tree_fingerprint(jobs.APP_ROOT, ("streamcurves",))
    out = []
    for label, pkg in sorted(packages.items()):
        digest = mp.read_package(Path(pkg).read_bytes()).digest
        out.append(jobs.Job(
            kind="python", target="streamcurves.easi_method.campaigns:score_cases",
            spec={"task": "easi-evaluate", "packageDigest": digest, "casesSha256": cases_sha,
                  "cases": str(cases_path), "package": str(pkg),
                  "evaluator": mp.evaluator_digest(), "acquisition": mp.acquisition_digest(),
                  "code": code},
            env={"EASI_METHOD_PACKAGE": str(pkg), mp.WORKER_ENV: "1", "EASI_CRITERIA_SET": "regional",
                 "EASI_METHOD_CACHE": str(cache_dir), "PYTHONDONTWRITEBYTECODE": "1"},
            label=label))
    return out


def evaluation_campaign(packages: dict[str, Path], cases_path: Path, campaign: Path, *,
                        workers: int = 2, **kw) -> tuple[dict, dict]:
    """``(results by label, summary)``."""
    js = evaluation_jobs(packages, cases_path, cache_dir=Path(campaign) / "method-cache")
    results = {}
    with _locked(campaign, kw) as kw:
        summary = jobs.run(js, campaign, workers=workers, meta={"task": "easi-evaluate"}, **kw)
        for job in js:
            if jobs.completed(campaign, job) is not None:
                results[job.label] = json.loads((Path(campaign) / "jobs" / job.id / "out" / "results.json")
                                                .read_text(encoding="utf-8"))
    return results, summary


__all__ = ["fit_quantity", "refit_jobs", "refit_campaign", "score_cases", "evaluation_jobs",
           "evaluation_campaign"]
