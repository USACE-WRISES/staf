"""Fit-only exploration grids: many candidate curves at once, as register-format candidates.

Exploration fits curves under alternative choices (a pool rung, a stratification level, a
screen) without building, deciding or publishing anything. Each grid cell is one job on
``streamcurves.jobs`` (resumable, one process per cell); every fit becomes a candidate in the
register's vocabulary (AUTHORING.md, "Candidates"): its identity says what it is, its
``basisDigest`` what it holds, ``purpose: exploration`` that no build selected it. A campaign's
``candidates.jsonl`` opens in StreamCurves beside the curves a build produced.

- DEEP (``deep_cell``): one Level III ecoregion; every scored NRSA metric fit on the reference
  stations (the fixed pressure screen, strict) of the region, its Level II and its Level I
  ecoregion, from the in-app NRSA archive. The governed build adds comparability checks and
  the basis ladder; this grid only shows what each pool would fit.
- EASI (``easi_cell``): one quantity at one stratification level, on the panels as built, or
  redrawn from the universe packages under another screen.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Optional

import numpy as np

BANDS = (0.39, 0.69)
GRID_COLUMNS = ["assessmentType", "subject", "level", "stratum", "split", "variant", "n", "status",
                "usable", "reason", "q25", "q50", "q75", "x39", "x69", "candidateKey"]


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=float).encode("utf-8")


def candidate(identity: dict, fit: dict, *, campaign: str, usable: bool, reason: str = "") -> dict:
    """A register candidate from one exploratory fit."""
    definition = {k: fit.get(k) for k in ("points", "x39", "x69", "q25", "q50", "q75", "n", "status")}
    key = "cand-" + hashlib.sha256(canonical(identity)).hexdigest()[:12]
    basis = "sha256:" + hashlib.sha256(canonical({"points": definition["points"], "x39": definition["x39"],
                                                  "x69": definition["x69"]})).hexdigest()
    built = bool(definition["points"])
    return {"candidateKey": key, "identity": identity, "basisDigest": basis, "purpose": "exploration",
            "campaign": campaign, "buildStatus": "built" if built else "failed",
            "supersededBy": None, "definition": definition,
            "eligibility": {"status": "eligible" if usable else "excluded",
                            "reasons": [reason] if reason else [], "checks": []}}


def _write_outputs(out_dir: Path, cands: list[dict]) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cands = sorted(cands, key=lambda c: c["candidateKey"])
    with (out_dir / "candidates.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for c in cands:
            fh.write(json.dumps(c, sort_keys=True, default=float) + "\n")
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=GRID_COLUMNS, lineterminator="\n")
    w.writeheader()
    for c in cands:
        ident, d = c["identity"], c["definition"]
        ref = ident.get("sourceRef") or {}
        w.writerow({"assessmentType": ident["assessmentType"], "subject": ident["subject"]["id"],
                    "level": ref.get("level"), "stratum": ref.get("stratum"), "split": ref.get("split", ""),
                    "variant": ref.get("variant", ""), "n": d.get("n"), "status": d.get("status"),
                    "usable": c["eligibility"]["status"] == "eligible",
                    "reason": "; ".join(c["eligibility"]["reasons"]), "q25": d.get("q25"),
                    "q50": d.get("q50"), "q75": d.get("q75"), "x39": d.get("x39"), "x69": d.get("x69"),
                    "candidateKey": c["candidateKey"]})
    (out_dir / "grid.csv").write_text(buf.getvalue(), encoding="utf-8", newline="")
    return {"candidates": len(cands), "built": sum(1 for c in cands if c["buildStatus"] == "built")}


def merge(campaign: Path, job_ids: list[str]) -> dict:
    """The campaign's ``candidates.jsonl`` and ``grid.csv`` from its completed cells."""
    cands = []
    for jid in job_ids:
        p = Path(campaign) / "jobs" / jid / "out" / "candidates.jsonl"
        if p.is_file():
            cands += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]
    return _write_outputs(Path(campaign), cands)


# --------------------------------------------------------------------------- #
# DEEP: metric x Level III x pool rung, from the in-app NRSA archive
# --------------------------------------------------------------------------- #
RUNGS = ("l3", "l2", "l1")


def deep_cell(spec: dict, out_dir: Path) -> dict:
    """Job target: every scored NRSA metric of one Level III region at each pool rung."""
    import pandas as pd
    from . import curves as engine
    from . import nrsa_dataset as nds
    from . import reference_screen as rsc
    from . import regional_agent as ra
    code = str(spec["l3"])
    dataset = spec.get("dataset") or nds.MULTI_CYCLE_DATASET_ID
    panel, _ = nds.resolve_site_panel(None, dataset=dataset, max_stream_order=spec.get("maxStreamOrder", 5),
                                      protocols=("WADEABLE",))
    screen = rsc.load_station_screen()[["station_key", "l3", "l2", "l1", "pass_strict"]]
    keep = set(screen.loc[screen["pass_strict"] == True, "station_key"].astype(str))  # noqa: E712
    keep |= set(screen.loc[screen["l3"].astype(str) == code, "station_key"].astype(str))
    keys = [k for k in panel["station_key"].astype(str) if k in keep]
    # DATA-11: each station's newest non-null value per metric, as the pooled builds read it
    values, _ = nds.latest_values(keys, dataset=dataset)
    values = values.rename(columns={"site_id": "station_key"})
    data = values.merge(screen, on="station_key", how="inner")
    data["l3"] = data["l3"].astype(str)
    in_region = data[data["l3"] == code]
    if not len(in_region):
        return _write_outputs(out_dir, [])
    region = {"l3": code, "l2": str(in_region["l2"].mode().iloc[0]), "l1": str(in_region["l1"].mode().iloc[0])}
    ref = data[data["pass_strict"] == True]  # noqa: E712
    metric_cols = [c for c in values.columns if c != "station_key"
                   and pd.api.types.is_numeric_dtype(values[c])]
    config, _ = ra.build_metric_config(metric_cols, ra.load_directions())
    wanted = set(spec.get("metrics") or config)
    cands = []
    for rung in spec.get("rungs") or RUNGS:
        pool = ref[ref[rung].astype(str) == region[rung]]
        for metric in sorted(m for m in config if m in wanted):
            col = config[metric].get("column_name") or metric
            if col not in pool.columns:
                continue
            res = engine.build_reference_curve(pool[[col]].copy(), metric, config,
                                               stratum_label=f"{rung}:{region[rung]}", build_plots=False)
            row, pts = res["curve_row"], res["curve_points"]
            points = [[float(x), float(y)] for x, y in zip(pts["metric_value"], pts["index_score"])] \
                if len(pts) else []
            fit = {"points": points, "status": str(row["curve_status"].iloc[0]),
                   "n": int(pd.to_numeric(pool[col], errors="coerce").notna().sum()),
                   "q25": _f(row, "q25"), "q50": _f(row, "median_val"), "q75": _f(row, "q75")}
            for name, t in (("x39", BANDS[0]), ("x69", BANDS[1])):
                hits = engine.reference_curve_threshold_crossings(pts, t) if len(points) >= 2 else []
                fit[name] = hits[0] if len(hits) == 1 else None
            usable = fit["status"] == "complete" and fit["x39"] is not None and fit["x69"] is not None
            identity = {"assessmentType": "deep", "subject": {"kind": "metric", "id": metric},
                        "sourceKind": "fitted",
                        "sourceRef": {"dataset": spec.get("dataset"), "level": rung, "stratum": region[rung],
                                      "screen": "least-disturbed-v1/strict", "variant": f"pool-{rung}"},
                        "applicability": {"geography": {"kind": "ecoregion", "code": code}}}
            cands.append(candidate(identity, fit, campaign=spec["campaign"], usable=usable,
                                   reason="" if usable else f"engine status {fit['status']}"))
    return _write_outputs(out_dir, cands)


def _f(row, col) -> Optional[float]:
    try:
        v = float(row[col].iloc[0])
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return v if np.isfinite(v) else None


# --------------------------------------------------------------------------- #
# EASI: quantity x stratification level x screen, from the evidence packages
# --------------------------------------------------------------------------- #
SCREEN_VARIANTS = ("as-built", "relaxed-first")


def _members_for(spec: dict):
    """(members, values, panels) as built, or redrawn from the universe under a variant."""
    import pandas as pd
    import pyarrow.parquet as pq
    from .easi_method import fit_recipe as fr
    from .easi_method import refit
    if spec["variant"] == "as-built":
        return refit.load_members(Path(spec["members"]))
    frame = pq.read_table(Path(spec["universe"]) / "data" / "universe.parquet").to_pandas()
    for name in frame.columns:
        if str(frame[name].dtype) == "float32":
            frame[name] = frame[name].astype("float64")
    if spec["variant"] == "relaxed-first":
        # this job's own process: the relaxed screen draws the panels first
        fr.screens.STRICT = fr.RELAXED
    panels, members = fr.select_panels(frame, spec["level"])
    vals = pq.read_table(Path(spec["universeValues"]) / "data" / "universe_values.parquet").to_pandas()
    raw = {v: frame[v].to_numpy() for v in fr.PRESSURE_VARIABLES if v in frame.columns}
    in_members = frame["comid"].isin(members["comid"]).to_numpy()
    pressure, _ = fr.composite_pressure({k: v[in_members] for k, v in raw.items()})
    pres = pd.DataFrame({"comid": frame["comid"].to_numpy()[in_members], "composite_pressure": pressure})
    values = vals[vals["comid"].isin(members["comid"])].merge(pres, on="comid", how="left")
    return members, values, panels


def easi_cell(spec: dict, out_dir: Path) -> dict:
    """Job target: one quantity at one level (every stratum and split), as built or redrawn."""
    from .easi_method import fit_recipe as fr
    from .easi_method import refit
    members, values, panels = _members_for(spec)
    rows = refit.fit_registry(members, values, panels, quantities=[spec["quantity"]],
                              levels=[spec["level"]])
    q = fr.QUANTITIES[spec["quantity"]]
    cands = []
    for r in rows:
        identity = {"assessmentType": "easi", "subject": {"kind": "quantity", "id": q.key},
                    "sourceKind": "fitted",
                    "sourceRef": {"level": r["level"], "stratum": r["stratum"], "split": r.get("split") or "",
                                  "variant": spec["variant"], "evidence": spec.get("evidenceDigest")},
                    "applicability": {"geography": {"kind": r["level"], "code": r["stratum"]}}}
        cands.append(candidate(identity, r, campaign=spec["campaign"], usable=bool(r.get("usable")),
                               reason=r.get("reason") or ""))
    return _write_outputs(out_dir, cands)


__all__ = ["candidate", "merge", "deep_cell", "easi_cell", "RUNGS", "SCREEN_VARIANTS", "GRID_COLUMNS"]
