"""Step ``panels``: the least-disturbed reference panels per ecoregion level.

For each level (Level III, II, I, NARS-9, national) and each stratum with at
least ``MIN_STRATUM`` reaches, the panel is every reach of the wadeable,
non-canal frame that passes the strict desktop screen (``screens.STRICT``),
thinned to one reach per HUC12 (seeded). Under 30 reaches the relaxed
screen is tried and the panel is labelled best-available; still under 30,
the stratum has no panel at this level and its reaches fall back to the
parent level when a curve is resolved. Never a percentile rule: in a
converted region a "least-disturbed 20 %" would make converted watersheds
the reference.

Outputs under ``analysis/panels/``: ``reference_panels.parquet`` (one row per
level x stratum), ``panel_members.parquet`` (the member reaches with the
columns the curve fitting groups on) and ``panel_summary.json``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np

from ..paths import DataRoot
from ..state import Control, Progress, digest
from ..stages import common
from . import ANALYSIS_VERSION, screens
from .values import landscape_path

LEVELS = ("l3", "l2", "l1", "nars9", "national")
#: a stratum needs this many reaches to be a stratum at all
MIN_STRATUM = 1000
FLOOR_COMPLETE = 100
FLOOR_EXPLORATORY = 30
SEED = 7
#: columns the members table keeps (the curve fitting groups on the splits)
MEMBER_COLUMNS = ("comid", "huc12", "state", "slope_class", "fcode_class", "da_class", "in_scored_set",
                  "totdasqkm", "streamorde")
SCREEN_COLUMNS = tuple(sorted({*screens.STRICT, *screens.RELAXED, *screens.FRAME_RULES, *screens.PRESSURE_VARIABLES}))


def panels_dir(root: DataRoot) -> Path:
    return root.analysis / "panels"


def panels_path(root: DataRoot) -> Path:
    return panels_dir(root) / "reference_panels.parquet"


def members_path(root: DataRoot) -> Path:
    return panels_dir(root) / "panel_members.parquet"


def summary_path(root: DataRoot) -> Path:
    return panels_dir(root) / "panel_summary.json"


def stratum_of(frame, level: str):
    """The stratum code per row for a level (``national`` is one stratum)."""
    import pandas as pd
    if level == "national":
        return pd.Series(["national"] * len(frame), index=frame.index, dtype="object")
    column = level
    if column not in frame.columns:
        raise KeyError(f"level {level!r} has no column in the landscape table")
    return frame[column].astype("object").where(frame[column].notna(), None)


def _thin(candidates, stratum, seed: int):
    """One reach per (stratum, HUC12) among the candidate rows, chosen by a
    seeded random key so the choice is reproducible."""
    import pandas as pd
    if len(candidates) == 0:
        return candidates
    rng = np.random.default_rng(seed)
    keyed = candidates.assign(_stratum=stratum.loc[candidates.index].to_numpy(),
                              _key=rng.random(len(candidates)))
    keyed = keyed.sort_values(["_stratum", "huc12", "_key"], kind="stable")
    thinned = keyed.drop_duplicates(["_stratum", "huc12"], keep="first")
    return thinned.drop(columns=["_key"])


def select_panels(frame, level: str, *, seed: int = SEED, min_stratum: int = MIN_STRATUM):
    """``(panels, members)`` for one level over a landscape frame (pandas)."""
    import pandas as pd
    stratum = stratum_of(frame, level)
    has = stratum.notna()
    columns = {name: frame[name].to_numpy() for name in SCREEN_COLUMNS if name in frame.columns}
    in_frame, skipped_frame = screens.evaluate(columns, screens.FRAME_RULES)
    strict, skipped_strict = screens.evaluate(columns, screens.STRICT)
    relaxed, skipped_relaxed = screens.evaluate(columns, screens.RELAXED)
    strict = in_frame & strict & has.to_numpy()
    relaxed = in_frame & relaxed & has.to_numpy()
    keep = [c for c in MEMBER_COLUMNS if c in frame.columns]
    thinned_strict = _thin(frame.loc[strict, keep], stratum, seed)
    thinned_relaxed = _thin(frame.loc[relaxed, keep], stratum, seed + 1)
    totals = stratum[has].value_counts()
    frame_counts = stratum[has & pd.Series(in_frame, index=frame.index)].value_counts()
    strict_counts = stratum[pd.Series(strict, index=frame.index)].value_counts()
    relaxed_counts = stratum[pd.Series(relaxed, index=frame.index)].value_counts()
    thinned_strict_counts = thinned_strict["_stratum"].value_counts() if len(thinned_strict) else pd.Series(dtype=int)
    thinned_relaxed_counts = thinned_relaxed["_stratum"].value_counts() if len(thinned_relaxed) else pd.Series(dtype=int)
    rows = []
    member_frames = []
    for code in sorted(totals.index, key=lambda c: (len(str(c)), str(c))):
        n_total = int(totals.get(code, 0))
        n_strict = int(thinned_strict_counts.get(code, 0))
        n_relaxed = int(thinned_relaxed_counts.get(code, 0))
        if n_total < min_stratum:
            screen_used, tier, reason = "none", "none", f"stratum under {min_stratum} reaches"
        elif n_strict >= FLOOR_EXPLORATORY:
            screen_used = "strict"
            tier = "complete" if n_strict >= FLOOR_COMPLETE else "exploratory"
            reason = ""
        elif n_relaxed >= FLOOR_EXPLORATORY:
            screen_used, tier, reason = "relaxed", "best_available", "strict panel under 30 reaches"
        else:
            screen_used, tier, reason = "none", "none", "no panel of 30 reaches under either screen"
        members = None
        if screen_used == "strict":
            members = thinned_strict[thinned_strict["_stratum"] == code]
        elif screen_used == "relaxed":
            members = thinned_relaxed[thinned_relaxed["_stratum"] == code]
        n_ref = int(len(members)) if members is not None else 0
        da = members["totdasqkm"].to_numpy(dtype=float) if members is not None and "totdasqkm" in members else np.zeros(0)
        da = da[np.isfinite(da)]
        states = (members["state"].value_counts().head(5).to_dict() if members is not None and "state" in members else {})
        rows.append({
            "level": level, "stratum": f"{level}:{code}", "code": str(code), "n_total": n_total,
            "n_frame": int(frame_counts.get(code, 0)), "n_pass_strict": int(strict_counts.get(code, 0)),
            "n_pass_relaxed": int(relaxed_counts.get(code, 0)), "n_strict_thinned": n_strict,
            "n_relaxed_thinned": n_relaxed, "n_ref": n_ref, "screen": screen_used, "panel_tier": tier,
            "reason": reason, "skipped_rules": ";".join(sorted(set(skipped_frame + skipped_strict + skipped_relaxed))),
            "n_ref_scored": int(members["in_scored_set"].sum()) if members is not None and "in_scored_set" in members else 0,
            "da_p25": float(np.quantile(da, 0.25)) if da.size else None,
            "da_p50": float(np.quantile(da, 0.5)) if da.size else None,
            "da_p75": float(np.quantile(da, 0.75)) if da.size else None,
            "states": json.dumps({str(k): int(v) for k, v in states.items()}),
        })
        if members is not None and len(members):
            member_frames.append(members.assign(level=level, stratum=f"{level}:{code}", screen=screen_used,
                                                panel_tier=tier).drop(columns=["_stratum"]))
    panels = pd.DataFrame(rows)
    members_out = (pd.concat(member_frames, ignore_index=True) if member_frames
                   else pd.DataFrame(columns=[*keep, "level", "stratum", "screen", "panel_tier"]))
    return panels, members_out


def inputs(root: DataRoot, options: Optional[dict] = None) -> str:
    path = landscape_path(root)
    stamp = (path.stat().st_size, int(path.stat().st_mtime)) if path.exists() else None
    levels = tuple((options or {}).get("levels") or LEVELS)
    return digest("panels", ANALYSIS_VERSION, stamp, levels, sorted(screens.STRICT.items()),
                  sorted(screens.RELAXED.items()), MIN_STRATUM, FLOOR_COMPLETE, FLOOR_EXPLORATORY, SEED, 1)


def load_landscape(root: DataRoot, columns: Optional[list[str]] = None):
    """The landscape table (or the named columns) as a pandas frame with
    float32 values widened to float64."""
    import pyarrow.parquet as pq
    schema = pq.read_schema(landscape_path(root)).names
    wanted = [c for c in (columns or schema) if c in schema]
    table = pq.read_table(landscape_path(root), columns=wanted)
    frame = table.to_pandas(types_mapper=None)
    for name in frame.columns:
        if str(frame[name].dtype) == "float32":
            frame[name] = frame[name].astype("float64")
    return frame


def run(root: DataRoot, progress: Progress, control: Control, options: Optional[dict] = None) -> Path:
    import pandas as pd
    if not landscape_path(root).exists():
        raise RuntimeError("landscape.parquet not found: run the landscape step first")
    levels = list((options or {}).get("levels") or LEVELS)
    panels_dir(root).mkdir(parents=True, exist_ok=True)
    needed = sorted({"comid", "huc12", "state", "l3", "l2", "l1", "nars9", "slope_class", "fcode_class", "da_class",
                     "wadeable", "totdasqkm", "streamorde", "in_scored_set", *SCREEN_COLUMNS})
    progress.begin("analysis", "panels", total=len(levels), message="panels: loading the landscape table")
    frame = load_landscape(root, needed)
    all_panels, all_members, summary = [], [], {}
    for i, level in enumerate(levels):
        control.check()
        panels, members = select_panels(frame, level)
        all_panels.append(panels)
        all_members.append(members)
        covered = panels.loc[panels["panel_tier"] != "none", "n_total"].sum()
        summary[level] = {
            "strata": int(len(panels)),
            "tiers": {tier: int((panels["panel_tier"] == tier).sum()) for tier in ("complete", "exploratory", "best_available", "none")},
            "reaches_with_panel_share": float(covered / max(panels["n_total"].sum(), 1)),
            "members": int(len(members)),
        }
        progress.tick(done=i + 1, message=f"panels {level}: {len(panels)} strata, {len(members):,} members")
        progress.say(f"panels {level}: {summary[level]['tiers']} ({summary[level]['reaches_with_panel_share']:.1%} of reaches covered)")
    panels = pd.concat(all_panels, ignore_index=True)
    members = pd.concat(all_members, ignore_index=True)
    common.write_parquet(panels, panels_path(root))
    common.write_parquet(members, members_path(root))
    summary_path(root).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return panels_path(root)
