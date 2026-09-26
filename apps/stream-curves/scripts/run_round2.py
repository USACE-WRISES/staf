"""Round 2 of the national assessment campaign: the orchestrator.

One arm per candidate of Pre-registration V, each a config root of its own, staged
in fresh processes under ``STREAMCURVES_CONFIG_ROOT``, evaluated on outcomes O1 to
O6, compared with the full-refit baseline A2 on identical units, and composed into
the finalist. Every command records the protocol's sha256 and refuses to run when
the committed protocol differs from the one an existing arm recorded.

    arms      --protocol <yaml> --out <root> [--arm ID ...]
              one config root per candidate (and the baseline A2, untouched) under
              <root>/arms/<id>/config, with arm.json (protocol sha, knob, baseline)
    stage     --arm <id> --out <root> --regions <codes> --workers N --n-boot 50 [--protocol]
              run_region_batch.py stage-many under the arm's root, fresh processes,
              --refit all --maintainer "Rehearsal (not an owner decision)"
    evaluate  --arm <id> --out <root> --bio-indices <table> [--jobs N] [--skip-hierarchy]
              [--hierarchy-arg ARG ...]
              the hierarchy harness (O1, O2), the association test (O3), the
              stability test (O5), and coverage and usability (O4, O6) from the
              staged campaign
    compare   --baseline A2 --arm <id> --out <root> [--constraint-resolved NOTE]
              the protocol's margins per decision type; verdict.json and verdict.md
              under <root>/verdicts/<id>
    finalist  --arms <ids> --out <root> [--allow-unverdicted]
              the accepted arms composed into <root>/arms/finalist/config, with the
              Benjamini-Hochberg q-values of every verdict as supporting evidence

Every subprocess is a fresh interpreter (``-B``) with ``STREAMCURVES_CONFIG_ROOT`` set
to the arm's config, because the app reads its configuration once at import.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import pandas as pd

APP_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from streamcurves import round2  # noqa: E402
from streamcurves.paths import APP_CONFIG_DIR  # noqa: E402

DEFAULT_PROTOCOL = APP_CONFIG_DIR / "methodology" / "evaluation_protocol_v1.yaml"
PYTHON = sys.executable
CAMPAIGN_DIR = "campaign"
EVALUATION_DIR = "evaluation"
VERDICTS_DIR = "verdicts"
#: The independent target the O3 primary comparison reads: the benthic MMI, the one
#: index every cycle publishes with both a score and a class; O/E and the fish MMI
#: are reported beside it.
PRIMARY_TARGET = "benthic_mmi"
ECI = "eci"


def _write_json(path: Path, doc: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    return path


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _protocol(a) -> Path:
    p = Path(a.protocol).resolve()
    if not p.is_file():
        raise SystemExit(f"no protocol at {p}; pass --protocol (the committed "
                         f"config/methodology/evaluation_protocol_v1.yaml)")
    return p


def _failed_evaluate_steps(arm_dir: Path) -> list[str]:
    """The evaluation steps ``evaluate.json`` records with a non-zero exit (a skipped
    step records None and is not a failure); empty when there is no record."""
    p = Path(arm_dir) / EVALUATION_DIR / "evaluate.json"
    if not p.is_file():
        return []
    exits = (_read_json(p).get("exits") or {})
    return sorted(str(k) for k, v in exits.items() if v not in (0, None))


def _arm(out_root: Path, arm_id: str, protocol: Path) -> tuple[Path, dict]:
    """The arm folder and its record, refused when it was built under another protocol."""
    d = round2.arm_dir(out_root, arm_id)
    try:
        rec = round2.read_arm(d)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        round2.check_protocol(rec, round2.sha256_of(protocol), what=f"arm {arm_id}")
    except round2.ProtocolMismatch as exc:
        raise SystemExit(str(exc)) from exc
    return d, rec


def _env(arm_config: Path) -> dict:
    env = dict(os.environ)
    env["STREAMCURVES_CONFIG_ROOT"] = str(arm_config)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _run(argv: list[str], *, env: dict, log: Path, label: str) -> int:
    """One fresh process, its output to ``log``, its exit code returned and printed."""
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"[round2] {label}: {' '.join(str(x) for x in argv[2:5])} ... -> {log}", flush=True)
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.run([str(x) for x in argv], env=env, stdout=fh, stderr=subprocess.STDOUT,
                              cwd=str(APP_ROOT))
    print(f"[round2] {label}: exit {proc.returncode}", flush=True)
    return int(proc.returncode)


def _regions(a, proto: dict) -> list[str]:
    if getattr(a, "regions", None):
        codes = []
        for chunk in a.regions:
            codes += [c.strip() for c in str(chunk).split(",") if c.strip()]
        return codes
    dev = (proto.get("regions") or {}).get("development") or {}
    return [str(c) for c in (dev.get("published") or []) + (dev.get("added") or [])]


# --------------------------------------------------------------------------- #
# arms
# --------------------------------------------------------------------------- #
def cmd_arms(a) -> int:
    protocol = _protocol(a)
    proto = round2.load_protocol(protocol)
    out_root = Path(a.out).resolve()
    source = Path(a.source_config).resolve() if a.source_config else APP_CONFIG_DIR
    ids = list(a.arm) if a.arm else [round2.BASELINE_ARM] + round2.candidate_ids(proto)
    rows = []
    for arm_id in ids:
        try:
            d = round2.build_arm_root(protocol, arm_id, source_config=source, out_root=out_root)
        except (round2.ProtocolMismatch, round2.KnobError, KeyError) as exc:
            raise SystemExit(f"arm {arm_id}: {exc}") from exc
        rec = round2.read_arm(d)
        rows.append({"arm": arm_id, "knobs": json.dumps(rec["knobs"], sort_keys=True),
                     "changed": ", ".join(rec["changes"]["file_keys"] + rec["changes"]["stage_flags"]) or "none (baseline)",
                     "root": str(d)})
        print(f"[round2] arm {arm_id:<8} {rows[-1]['changed']}")
    _write_json(out_root / "arms.json", {"schema": "round2-arms/1", "protocol": {"path": protocol.name,
                                          "sha256": round2.sha256_of(protocol)},
                                         "campaign": proto.get("campaign"), "sourceConfig": str(source),
                                         "arms": rows})
    print(f"[round2] {len(rows)} arm root(s) under {out_root / round2.ARMS_DIR}")
    return 0


# --------------------------------------------------------------------------- #
# stage
# --------------------------------------------------------------------------- #
def stage_argv(arm_dir: Path, rec: dict, regions: list[str], *, workers: int, n_boot: int,
               names: Optional[list[str]] = None, extra: Optional[list[str]] = None) -> list[str]:
    flags = rec.get("stageFlags") or {}
    argv = [PYTHON, "-B", str(SCRIPTS / "run_region_batch.py"), "stage-many",
            "--out-root", str(arm_dir / CAMPAIGN_DIR), "--decisions-root", str(arm_dir / "decisions"),
            "--workers", str(int(workers)), "--isolated", "--n-boot", str(int(n_boot)),
            "--refit", str(flags.get("refit") or "all"),
            "--maintainer", str(flags.get("maintainer") or round2.REHEARSAL_MAINTAINER)]
    if flags.get("value_policy"):
        argv += ["--value-policy", str(flags["value_policy"])]
    for code in regions:
        argv += ["--l3", str(code)]
    for spec in names or []:
        argv += ["--name", str(spec)]
    argv += [str(x) for x in (extra or [])]
    return argv


def cmd_stage(a) -> int:
    protocol = _protocol(a)
    proto = round2.load_protocol(protocol)
    out_root = Path(a.out).resolve()
    arm_dir, rec = _arm(out_root, a.arm, protocol)
    regions = _regions(a, proto)
    (arm_dir / "decisions").mkdir(exist_ok=True)
    argv = stage_argv(arm_dir, rec, regions, workers=a.workers, n_boot=a.n_boot, names=a.name, extra=a.stage_arg)
    env = _env(arm_dir / round2.CONFIG_DIR_NAME)
    code = _run(argv, env=env, log=arm_dir / "stage.log", label=f"stage {a.arm}")
    summary_path = arm_dir / CAMPAIGN_DIR / "batch_summary.json"
    summary = _read_json(summary_path) if summary_path.is_file() else None
    _write_json(arm_dir / "stage.json", {
        "schema": "round2-stage/1", "arm": a.arm, "exit": code, "argv": argv[3:],
        "env": {"STREAMCURVES_CONFIG_ROOT": env["STREAMCURVES_CONFIG_ROOT"]},
        "regions": regions, "workers": int(a.workers), "n_boot": int(a.n_boot),
        "stamp": round2.stamp(protocol, config_root=arm_dir / round2.CONFIG_DIR_NAME),
        "batchSummary": summary})
    if summary:
        for r in summary.get("regions") or []:
            print(f"[round2]   L3-{r.get('l3')} {r.get('name') or ''}: exit {r.get('exit')} "
                  f"{r.get('error') or ''} ({r.get('curves')} curves, v{r.get('staged_version')})")
    return code


# --------------------------------------------------------------------------- #
# evaluate
# --------------------------------------------------------------------------- #
def coverage_and_usability(campaign: Path) -> tuple[dict, dict]:
    """O4 and O6 from the staged campaign: per region from its staged bundle, the
    runtime and failures from batch_summary.json."""
    sys.path.insert(0, str(SCRIPTS))
    from run_association_test import staged_bundles  # noqa: E402
    cov: dict[str, Any] = {"regions": {}, "totals": {"functionsSupported": 0, "curves": 0, "withheld": 0,
                                                       "curvesByBasis": {}}}
    use: dict[str, Any] = {"regions": {}, "totals": {"fieldMetrics": 0, "distinctProcedures": 0,
                                                       "runtimeSeconds": 0.0, "failures": 0, "staged": 0}}
    for code, path, bundle in staged_bundles(campaign):
        c = round2.coverage_of(bundle)
        u = round2.usability_of(bundle)
        cov["regions"][code] = {**c, "bundle": str(path)}
        use["regions"][code] = u
        cov["totals"]["functionsSupported"] += c["functionsSupported"]
        cov["totals"]["curves"] += c["metricListings"]
        cov["totals"]["withheld"] += c["withheld"]
        for k, v in c["curvesByBasis"].items():
            cov["totals"]["curvesByBasis"][k] = cov["totals"]["curvesByBasis"].get(k, 0) + v
        use["totals"]["fieldMetrics"] += u["fieldMetrics"]
        use["totals"]["distinctProcedures"] += u["distinctProcedures"]
        use["totals"]["staged"] += 1
    summary = campaign / "batch_summary.json"
    if summary.is_file():
        for r in _read_json(summary).get("regions") or []:
            use["totals"]["runtimeSeconds"] += float(r.get("seconds") or 0.0)
            if r.get("exit") not in (0, None):
                use["totals"]["failures"] += 1
            use["regions"].setdefault(str(r.get("l3")), {})["stage"] = {
                "exit": r.get("exit"), "seconds": r.get("seconds"), "error": r.get("error")}
    n = max(1, use["totals"]["staged"])
    use["perAssessment"] = {"fieldMetrics": use["totals"]["fieldMetrics"] / n,
                            "distinctProcedures": use["totals"]["distinctProcedures"] / n}
    cov["totals"]["functionsSupportedPerRegion"] = cov["totals"]["functionsSupported"] / n
    return cov, use


def cmd_evaluate(a) -> int:
    protocol = _protocol(a)
    out_root = Path(a.out).resolve()
    arm_dir, rec = _arm(out_root, a.arm, protocol)
    campaign = arm_dir / CAMPAIGN_DIR
    ev = arm_dir / EVALUATION_DIR
    ev.mkdir(parents=True, exist_ok=True)
    env = _env(arm_dir / round2.CONFIG_DIR_NAME)
    exits: dict[str, Optional[int]] = {}
    if not a.skip_hierarchy:
        argv = [PYTHON, "-B", str(SCRIPTS / "run_hierarchy_test.py"), "--out", str(ev / "hierarchy"),
                "--prereg", str(protocol), "--jobs", str(int(a.jobs)), "--seed", str(int(a.seed))]
        argv += [str(x) for x in (a.hierarchy_arg or [])]
        exits["hierarchy"] = _run(argv, env=env, log=ev / "hierarchy.log", label=f"hierarchy {a.arm}")
    else:
        exits["hierarchy"] = None
    if not a.skip_association:
        if not a.bio_indices:
            raise SystemExit("--bio-indices is needed for the association test (build_bio_indices.py)")
        argv = [PYTHON, "-B", str(SCRIPTS / "run_association_test.py"), "--campaign", str(campaign),
                "--bio-indices", str(Path(a.bio_indices).resolve()), "--out", str(ev / "association"),
                "--protocol", str(protocol), "--n-boot", str(int(a.n_boot)), "--seed", str(int(a.seed)),
                "--config-root", str(arm_dir / round2.CONFIG_DIR_NAME)]
        exits["association"] = _run(argv, env=env, log=ev / "association.log", label=f"association {a.arm}")
    else:
        exits["association"] = None
    if not a.skip_stability:
        argv = [PYTHON, "-B", str(SCRIPTS / "run_stability_test.py"), "--campaign", str(campaign),
                "--out", str(ev / "stability"), "--protocol", str(protocol), "--n-boot", str(int(a.n_boot)),
                "--seed", str(int(a.seed)), "--config-root", str(arm_dir / round2.CONFIG_DIR_NAME)]
        exits["stability"] = _run(argv, env=env, log=ev / "stability.log", label=f"stability {a.arm}")
    else:
        exits["stability"] = None
    cov, use = coverage_and_usability(campaign)
    _write_json(ev / "coverage.json", {"schema": "round2-coverage/1", "arm": a.arm, **cov})
    _write_json(ev / "usability.json", {"schema": "round2-usability/1", "arm": a.arm, **use})
    _write_json(ev / "evaluate.json", {"schema": "round2-evaluate/1", "arm": a.arm, "exits": exits,
                                       "stamp": round2.stamp(protocol, config_root=arm_dir / round2.CONFIG_DIR_NAME),
                                       "outputs": {"hierarchy": str(ev / "hierarchy" / "hierarchy.csv"),
                                                   "association": str(ev / "association" / "association.csv"),
                                                   "stability": str(ev / "stability" / "stability.csv"),
                                                   "coverage": str(ev / "coverage.json"),
                                                   "usability": str(ev / "usability.json")}})
    print(f"[round2] evaluate {a.arm}: coverage {cov['totals']['functionsSupported']} function blocks over "
          f"{use['totals']['staged']} staged region(s), {cov['totals']['withheld']} withheld; exits {exits}")
    return 0 if all(x in (0, None) for x in exits.values()) else 1


# --------------------------------------------------------------------------- #
# compare
# --------------------------------------------------------------------------- #
def _hierarchy(arm_dir: Path, cells: Optional[set[str]]) -> Optional[pd.DataFrame]:
    p = arm_dir / EVALUATION_DIR / "hierarchy" / "hierarchy.csv"
    if not p.is_file():
        return None
    df = pd.read_csv(p, dtype={"l3": str})
    if cells:
        df = df[df["l3"].astype(str).isin(cells)]
    return df


def _calls(arm_dir: Path) -> Optional[pd.DataFrame]:
    p = arm_dir / EVALUATION_DIR / "hierarchy" / "hierarchy_calls.csv"
    return pd.read_csv(p, dtype={"l3": str, "station_key": str}) if p.is_file() else None


def _stations(arm_dir: Path) -> Optional[pd.DataFrame]:
    p = arm_dir / EVALUATION_DIR / "association" / "association_stations.csv"
    return pd.read_csv(p, dtype={"station_key": str, "l3": str, "huc8": str, "huc12": str}) if p.is_file() else None


def _stability(arm_dir: Path) -> Optional[pd.DataFrame]:
    p = arm_dir / EVALUATION_DIR / "stability" / "stability.csv"
    return pd.read_csv(p, dtype={"l3": str}) if p.is_file() else None


def _json_or_none(path: Path) -> Optional[dict]:
    return _read_json(path) if path.is_file() else None


def o3_comparison(arm_st: pd.DataFrame, base_st: pd.DataFrame, *, n_boot: int, seed: int) -> dict:
    """Paired AUC deltas on identical stations (HUC8 clusters) for the ECI and every
    function score against each target; the ECI rows feed the O3 block."""
    from run_association_test import TARGETS, POSITIVE, NEGATIVE, fold_of  # noqa: E402
    both = arm_st.merge(base_st, on="station_key", suffixes=("_arm", "_base"))
    folds = both.rename(columns={"huc8_arm": "huc8", "l3_arm": "l3"}).apply(fold_of, axis=1).tolist() if len(both) else []
    rows = []
    subjects = [ECI] + sorted({c[len("f__"):] for c in arm_st.columns if c.startswith("f__")})
    for subject in subjects:
        col = "eci_over_scored" if subject == ECI else f"f__{subject}"
        for target, cols in TARGETS.items():
            cls = both[f"{cols['class']}_arm"].astype(object) if f"{cols['class']}_arm" in both.columns else pd.Series([None] * len(both))
            lab = cls.isin([POSITIVE, NEGATIVE]).to_numpy()
            if not lab.any():
                rows.append({"subject": subject, "target": target, "n": 0})
                continue
            got = round2.paired_auc_delta(both.loc[lab, f"{col}_arm"], both.loc[lab, f"{col}_base"],
                                          (cls[lab] == POSITIVE).to_numpy(), [f for f, k in zip(folds, lab) if k],
                                          n_boot=n_boot, seed=round2.seed_for("o3", subject, target, seed=seed))
            rows.append({"subject": subject, "target": target, **got})
    return {"unit": "station; clusters: HUC8", "n_stations": int(len(both)),
            "stations_hash": round2.station_list_hash(both["station_key"]) if len(both) else None,
            "rows": rows}


def o5_comparison(arm_tab: pd.DataFrame, base_tab: pd.DataFrame, *, n_boot: int, seed: int) -> dict:
    both = arm_tab.merge(base_tab, on=["l3", "metric"], suffixes=("_arm", "_base"))
    fa = pd.to_numeric(both["flip_median_arm"], errors="coerce")
    fb = pd.to_numeric(both["flip_median_base"], errors="coerce")
    delta = round2.paired_delta((-fa).to_numpy(dtype=float), (-fb).to_numpy(dtype=float),
                                both["l3"].astype(str).tolist(), level=round2.MARGINS["O5"]["level"],
                                n_boot=n_boot, seed=round2.seed_for("o5", seed=seed), statistic="median")
    sa = pd.to_numeric(both["acc04_max_shift_iqr_arm"], errors="coerce")
    sb = pd.to_numeric(both["acc04_max_shift_iqr_base"], errors="coerce")
    return {"unit": "cell (region x metric); clusters: Level III region; delta is minus the flip rate",
            "n_cells": int(len(both)), "cells_hash": round2.station_list_hash(both["l3"] + "|" + both["metric"]),
            "flip_median_arm": float(fa.median()) if fa.notna().any() else None,
            "flip_median_base": float(fb.median()) if fb.notna().any() else None,
            "acc04_median_arm": float(sa.median()) if sa.notna().any() else None,
            "acc04_median_base": float(sb.median()) if sb.notna().any() else None,
            "delta": delta}


def primary_from_auc(row: dict) -> dict:
    lo, hi = row.get("lo"), row.get("hi")
    return {"estimate": row.get("delta"), "lo": lo, "hi": hi, "level": row.get("level"),
            "excludes_zero": None if lo is None else bool(lo > 0 or hi < 0), "p_value": row.get("p_value"),
            "n": row.get("n"), "n_clusters": row.get("n_clusters"), "statistic": "auc delta"}


def cmd_compare(a) -> int:
    protocol = _protocol(a)
    proto = round2.load_protocol(protocol)
    out_root = Path(a.out).resolve()
    base_dir, base_rec = _arm(out_root, a.baseline, protocol)
    arm_dir, arm_rec = _arm(out_root, a.arm, protocol)
    # an evaluation that failed leaves an outcome without data; a verdict on the rest would
    # read an absent block as a passed one (C3b, 2026-09-26), so the arm is evaluated again
    failed = {label: _failed_evaluate_steps(d) for label, d in ((a.arm, arm_dir), (a.baseline, base_dir))}
    failed = {k: v for k, v in failed.items() if v}
    if failed and not getattr(a, "allow_failed_evaluate", False):
        raise SystemExit("refusing to compare: the evaluation of "
                         + "; ".join(f"{k} failed at {', '.join(v)}" for k, v in failed.items())
                         + ". Run evaluate again for the failed step(s) (or pass --allow-failed-evaluate "
                           "for an inconclusive record).")
    cand = arm_rec.get("candidate") or {}
    decision = str(cand.get("decision") or "accuracy_change")
    primary_outcome = str(cand.get("primary_outcome") or "O1")
    dev = (proto.get("regions") or {}).get("development") or {}
    cells = None if a.all_cells else {str(c) for c in (dev.get("testable_cells") or [])}
    sys.path.insert(0, str(SCRIPTS))
    n_boot, seed = int(a.n_boot), int(a.seed)
    outcomes: dict[str, dict] = {}
    limits: list[dict] = []
    subgroups: dict[str, Any] = {}

    # O1 and O2 from the hierarchy harness
    ha, hb = _hierarchy(arm_dir, cells), _hierarchy(base_dir, cells)
    o1 = None
    if ha is not None and hb is not None and len(ha) and len(hb):
        ca, cb = _calls(arm_dir), _calls(base_dir)
        if ca is not None and cb is not None:
            o1 = round2.o1_from_calls(ca, cb, n_boot=n_boot, seed=round2.seed_for("o1", "calls", seed=seed))
            o1.update({k: v for k, v in round2.o1_comparison(ha, hb, n_boot=n_boot, seed=seed).items()
                       if k in ("by_nars9", "by_family", "a1_share_arm", "a1_share_base")})
        else:
            view = round2.o1_comparison(ha, hb, n_boot=n_boot, seed=round2.seed_for("o1", seed=seed), selected_only=True)
            o1 = view if view["n_cells"] else round2.o1_comparison(ha, hb, n_boot=n_boot, seed=round2.seed_for("o1", seed=seed))
        d = o1["delta"]
        outcomes["O1"] = {"arm_value": None, "baseline_value": None, "delta_value": d.get("estimate"),
                          "lo": d.get("lo"), "hi": d.get("hi"), "limit": "adoption rule", "blocks": False,
                          "note": f"{o1.get('unit')}; {o1.get('n_cells', o1.get('n_units'))} units; A1 share "
                                  f"{round2.fmt(o1.get('a1_share_arm'), 3)} vs {round2.fmt(o1.get('a1_share_base'), 3)}",
                          "detail": o1}
        net_a, net_b = round2.o2_summary(ha), round2.o2_summary(hb)
        lim = round2.o2_limit(net_a, net_b)
        limits.append(lim)
        outcomes["O2"] = {"arm_value": net_a, "baseline_value": net_b, "delta_value": lim.get("worsening"),
                          "limit": "<= 0.05 and no worsening beyond 0.02", "blocks": lim["blocks"], "note": lim["why"]}
        sg = round2.subgroup_block(o1.get("by_nars9") or {})
        limits.append(sg)
        subgroups = {"nars9": sg, "family": o1.get("by_family") or {}}
    else:
        outcomes["O1"] = {"note": "no hierarchy output for both arms", "blocks": False}
        outcomes["O2"] = {"note": "no hierarchy output for both arms", "blocks": False}

    # O3 from the association test
    sa, sb = _stations(arm_dir), _stations(base_dir)
    o3 = None
    if sa is not None and sb is not None and len(sa) and len(sb):
        o3 = o3_comparison(sa, sb, n_boot=n_boot, seed=seed)
        eci_rows = [r for r in o3["rows"] if r.get("subject") == ECI and r.get("lo") is not None]
        blk = round2.o3_block(eci_rows)
        limits.append(blk)
        prim = next((r for r in eci_rows if r.get("target") == PRIMARY_TARGET), None)
        outcomes["O3"] = {"arm_value": None if prim is None else prim.get("auc_a"),
                          "baseline_value": None if prim is None else prim.get("auc_b"),
                          "delta_value": None if prim is None else prim.get("delta"),
                          "lo": None if prim is None else prim.get("lo"), "hi": None if prim is None else prim.get("hi"),
                          "limit": "lower 95 percent bound of the paired AUC delta >= -0.01", "blocks": blk["blocks"],
                          "note": f"ECI vs {PRIMARY_TARGET} on {o3['n_stations']} identical stations; {blk['why']}",
                          "detail": o3}
    else:
        outcomes["O3"] = {"note": "no association output for both arms", "blocks": False}

    # O4 coverage, reported separately
    ca_, cb_ = _json_or_none(arm_dir / EVALUATION_DIR / "coverage.json"), _json_or_none(base_dir / EVALUATION_DIR / "coverage.json")
    coverage_gain = None
    coverage = {}
    if ca_ and cb_:
        ta, tb = ca_["totals"], cb_["totals"]
        coverage_gain = float(ta["functionsSupported"] - tb["functionsSupported"])
        bases = sorted(set(ta["curvesByBasis"]) | set(tb["curvesByBasis"]))
        rows = [{"measure": "functions supported", "arm": ta["functionsSupported"], "baseline": tb["functionsSupported"],
                 "delta": coverage_gain},
                {"measure": "curves", "arm": ta["curves"], "baseline": tb["curves"], "delta": ta["curves"] - tb["curves"]},
                {"measure": "withheld", "arm": ta["withheld"], "baseline": tb["withheld"], "delta": ta["withheld"] - tb["withheld"]}]
        rows += [{"measure": f"curves by basis: {b}", "arm": ta["curvesByBasis"].get(b, 0),
                  "baseline": tb["curvesByBasis"].get(b, 0),
                  "delta": ta["curvesByBasis"].get(b, 0) - tb["curvesByBasis"].get(b, 0)} for b in bases]
        coverage = {"rows": rows, "columns": ["measure", "arm", "baseline", "delta"]}
        outcomes["O4"] = {"arm_value": ta["functionsSupported"], "baseline_value": tb["functionsSupported"],
                          "delta_value": coverage_gain, "limit": "reported; never traded", "blocks": False,
                          "note": f"curves {ta['curves']} vs {tb['curves']}; withheld {ta['withheld']} vs {tb['withheld']}"}
    else:
        outcomes["O4"] = {"note": "no coverage output for both arms", "blocks": False}

    # O5 from the stability test
    ta_, tb_ = _stability(arm_dir), _stability(base_dir)
    o5 = None
    if ta_ is not None and tb_ is not None and len(ta_) and len(tb_):
        o5 = o5_comparison(ta_, tb_, n_boot=n_boot, seed=seed)
        lim = round2.o5_limit(o5["flip_median_arm"], o5["flip_median_base"])
        limits.append(lim)
        outcomes["O5"] = {"arm_value": o5["flip_median_arm"], "baseline_value": o5["flip_median_base"],
                          "delta_value": lim.get("rise"), "lo": o5["delta"].get("lo"), "hi": o5["delta"].get("hi"),
                          "limit": "median flip rate may not rise by more than 0.02", "blocks": lim["blocks"],
                          "note": f"{lim['why']}; ACC-04 median shift {round2.fmt(o5['acc04_median_arm'], 3)} vs "
                                  f"{round2.fmt(o5['acc04_median_base'], 3)} ({o5['n_cells']} identical cells)",
                          "detail": o5}
    else:
        outcomes["O5"] = {"note": "no stability output for both arms", "blocks": False}

    # O6 usability, reported
    ua, ub = _json_or_none(arm_dir / EVALUATION_DIR / "usability.json"), _json_or_none(base_dir / EVALUATION_DIR / "usability.json")
    if ua and ub:
        outcomes["O6"] = {"arm_value": ua["perAssessment"]["fieldMetrics"], "baseline_value": ub["perAssessment"]["fieldMetrics"],
                          "delta_value": ua["perAssessment"]["fieldMetrics"] - ub["perAssessment"]["fieldMetrics"],
                          "limit": "reported", "blocks": False,
                          "note": (f"field metrics per assessment; procedures {ua['perAssessment']['distinctProcedures']:.1f} vs "
                                   f"{ub['perAssessment']['distinctProcedures']:.1f}; runtime {ua['totals']['runtimeSeconds']:.0f} s vs "
                                   f"{ub['totals']['runtimeSeconds']:.0f} s; failures {ua['totals']['failures']} vs {ub['totals']['failures']}")}
    else:
        outcomes["O6"] = {"note": "no usability output for both arms", "blocks": False}

    # the primary comparison per the candidate's primary outcome
    primary: dict = {"outcome": primary_outcome, "unit": "", "n": None, "n_clusters": None, "delta": {}}
    if primary_outcome == "O1" and o1 is not None:
        primary.update({"unit": o1.get("unit"), "n": o1.get("n_cells", o1.get("n_units")),
                        "n_clusters": o1["delta"].get("n_clusters"), "delta": o1["delta"]})
    elif primary_outcome == "O2" and ha is not None and hb is not None:
        both = round2.hierarchy_cells(ha).merge(round2.hierarchy_cells(hb), on="cell", suffixes=("_arm", "_base"))
        d = round2.paired_delta(-pd.to_numeric(both["net_optimism_arm"], errors="coerce").to_numpy(dtype=float),
                                -pd.to_numeric(both["net_optimism_base"], errors="coerce").to_numpy(dtype=float),
                                both["l3_arm"].tolist(), level=round2.MARGINS["O1"]["level"], n_boot=n_boot,
                                seed=round2.seed_for("o2", seed=seed))
        primary.update({"unit": "cell; clusters: Level III region; delta is minus the net optimism",
                        "n": d["n"], "n_clusters": d["n_clusters"], "delta": d})
    elif primary_outcome == "O3" and o3 is not None:
        prim = next((r for r in o3["rows"] if r.get("subject") == ECI and r.get("target") == PRIMARY_TARGET
                     and r.get("lo") is not None), None)
        if prim is not None:
            primary.update({"unit": f"{o3['unit']}; ECI vs {PRIMARY_TARGET}", "n": prim.get("n"),
                            "n_clusters": prim.get("n_clusters"), "delta": primary_from_auc(prim)})
    elif primary_outcome == "O5" and o5 is not None:
        primary.update({"unit": o5["unit"], "n": o5["n_cells"], "n_clusters": o5["delta"].get("n_clusters"),
                        "delta": o5["delta"]})
    elif primary_outcome == "O4" and coverage:
        primary.update({"unit": "coverage (reported)", "n": None, "delta": {}})

    # an outcome recorded as a note alone has no data; the primary without a delta likewise
    missing = [oid for oid in ("O1", "O2", "O3", "O5")
               if set(outcomes.get(oid) or {}) <= {"note", "blocks"}]
    if primary_outcome not in ("O4",) and not primary.get("delta") and primary_outcome not in missing:
        missing.append(primary_outcome)
    adoption = round2.adoption(decision, primary=primary.get("delta") or None, limits=limits,
                               constraint_resolved=bool(a.constraint_resolved) if a.constraint_resolved else None,
                               coverage_gain=coverage_gain, missing=missing)
    verdict = {
        "schema": "round2-verdict/1", "arm": a.arm, "baseline": a.baseline, "candidate": cand,
        "decision": decision, "primaryOutcome": primary_outcome, "primary": primary,
        "adoption": adoption, "outcomes": outcomes, "limits": limits, "subgroups": subgroups,
        "coverage": coverage, "coverageGain": coverage_gain,
        "constraint": {"resolved": bool(a.constraint_resolved), "note": a.constraint_resolved or None,
                       "text": cand.get("constraint")},
        "cells": sorted(cells) if cells else "all",
        "margins": round2.MARGINS, "n_boot": n_boot, "seed": seed,
        "armConfigRoot": str(arm_dir / round2.CONFIG_DIR_NAME), "baselineConfigRoot": str(base_dir / round2.CONFIG_DIR_NAME),
        "armFingerprint": (arm_rec.get("config") or {}).get("fingerprint"),
        "baselineFingerprint": (base_rec.get("config") or {}).get("fingerprint"),
        "stamp": round2.stamp(protocol, config_root=arm_dir / round2.CONFIG_DIR_NAME),
    }
    vdir = out_root / VERDICTS_DIR / a.arm
    _write_json(vdir / "verdict.json", verdict)
    (vdir / "verdict.md").write_text(round2.verdict_markdown(verdict), encoding="utf-8", newline="\n")
    print(f"[round2] {a.arm} vs {a.baseline}: {adoption['verdict']} ({decision} on {primary_outcome}); "
          + "; ".join(adoption["reasons"]))
    print(f"[round2] verdict -> {vdir / 'verdict.md'}")
    return 0


# --------------------------------------------------------------------------- #
# finalist
# --------------------------------------------------------------------------- #
def cmd_finalist(a) -> int:
    protocol = _protocol(a)
    out_root = Path(a.out).resolve()
    ids = []
    for chunk in a.arms:
        ids += [c.strip() for c in str(chunk).split(",") if c.strip()]
    members = []
    for arm_id in ids:
        d, _rec = _arm(out_root, arm_id, protocol)
        v = out_root / VERDICTS_DIR / arm_id / "verdict.json"
        if not a.allow_unverdicted:
            if not v.is_file():
                raise SystemExit(f"arm {arm_id} has no verdict; run compare first (or --allow-unverdicted)")
            got = str((_read_json(v).get("adoption") or {}).get("verdict"))
            if got != round2.VERDICT_ADOPT:
                raise SystemExit(f"arm {arm_id} was not adopted ({got}); a finalist composes accepted arms")
        members.append(d)
    source = Path(a.source_config).resolve() if a.source_config else APP_CONFIG_DIR
    try:
        target = round2.compose_finalist(protocol, members, source_config=source, out_root=out_root)
    except (round2.KnobError, round2.ProtocolMismatch) as exc:
        raise SystemExit(str(exc)) from exc
    pvalues = {}
    verdicts = {}
    for v in sorted((out_root / VERDICTS_DIR).glob("*/verdict.json")):
        doc = _read_json(v)
        verdicts[doc["arm"]] = (doc.get("adoption") or {}).get("verdict")
        pvalues[doc["arm"]] = ((doc.get("primary") or {}).get("delta") or {}).get("p_value")
    q = round2.bh_adjust(pvalues)
    _write_json(target / "supporting.json", {
        "schema": "round2-supporting/1", "members": ids, "verdicts": verdicts,
        "benjamini_hochberg": {"q_threshold": round2.MARGINS["multiplicity"]["bh_q"], "p_values": pvalues,
                               "q_values": q, "passing": sorted(k for k, qv in q.items()
                                                                if qv is not None and qv <= round2.MARGINS["multiplicity"]["bh_q"])},
        "stamp": round2.stamp(protocol, config_root=target / round2.CONFIG_DIR_NAME)})
    rec = round2.read_arm(target)
    print(f"[round2] finalist composed from {', '.join(ids) or 'no arm'} -> {target / round2.CONFIG_DIR_NAME}; "
          f"changed {', '.join(rec['changes']['file_keys']) or 'nothing'}")
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--out", required=True, help="the Round 2 root (arms/, verdicts/ under it)")
        p.add_argument("--protocol", default=str(DEFAULT_PROTOCOL), help="the committed evaluation protocol")

    p = sub.add_parser("arms", help="one config root per candidate, and the baseline")
    common(p)
    p.add_argument("--arm", action="append", default=None, help="only these arms (repeatable)")
    p.add_argument("--source-config", default=None, help="the config folder to copy (default: the app's)")

    p = sub.add_parser("stage", help="stage an arm's regions under its config root, fresh processes")
    common(p)
    p.add_argument("--arm", required=True)
    p.add_argument("--regions", action="append", default=None, help="codes, comma separated or repeated "
                   "(default: the protocol's development set)")
    p.add_argument("--name", action="append", default=None, metavar="CODE=NAME")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--n-boot", type=int, default=50)
    p.add_argument("--stage-arg", action="append", default=None, help="passed to stage-many verbatim (repeatable)")

    p = sub.add_parser("evaluate", help="O1 to O6 for an arm")
    common(p)
    p.add_argument("--arm", required=True)
    p.add_argument("--bio-indices", default=None)
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument("--n-boot", type=int, default=200)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--skip-hierarchy", action="store_true")
    p.add_argument("--skip-association", action="store_true")
    p.add_argument("--skip-stability", action="store_true")
    p.add_argument("--hierarchy-arg", action="append", default=None,
                   help="passed to run_hierarchy_test.py verbatim, e.g. --hierarchy-arg=--registry --hierarchy-arg=PATH")

    p = sub.add_parser("compare", help="the protocol's margins per decision type; verdict.json and verdict.md")
    common(p)
    p.add_argument("--baseline", default=round2.BASELINE_ARM)
    p.add_argument("--arm", required=True)
    p.add_argument("--n-boot", type=int, default=200)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--all-cells", action="store_true", help="every cell, not only the development testable cells")
    p.add_argument("--constraint-resolved", default=None, metavar="NOTE",
                   help="for a coverage-only candidate: the record that its constraint (D4a) is resolved")
    p.add_argument("--allow-failed-evaluate", action="store_true",
                   help="compare although a step of the evaluation failed (the verdict is then "
                        "inconclusive for the outcomes without data)")

    p = sub.add_parser("finalist", help="compose the accepted arms into one config root")
    common(p)
    p.add_argument("--arms", action="append", required=True, help="arm ids, comma separated or repeated")
    p.add_argument("--allow-unverdicted", action="store_true")
    p.add_argument("--source-config", default=None)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return {"arms": cmd_arms, "stage": cmd_stage, "evaluate": cmd_evaluate,
            "compare": cmd_compare, "finalist": cmd_finalist}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
