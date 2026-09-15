"""Step ``report``: the one report the owner decides from.

Under ``analysis/report/``:
- ``index.html``: the paradigm comparison (the three views per run and
  state), the level comparison (T-L1, T-L2, border excess), the sanity
  gates, the routes table, and links to the scorecards and maps; figures
  are inline PNGs (matplotlib, no new dependency).
- ``scorecards/<function>.html``: one page per function with today's class
  shares by state, the distribution flags, the variance shares, the NRSA
  agreement of the incumbent and its candidates under every run, and the
  stability of its curves.
- ``maps/<run>_eci_<view>.png``: the ECI of every scored reach (anchor
  points, rasterised) per run and view.
- ``routes.csv``: one row per function with the route the rules recommend
  and the evidence, which the owner edits before the final run.
- ``decision_sheet.md``: the one-page sheet with the recommendation rules
  applied (C15 for the level, C16 for the paradigm) and the numbers behind them.
"""
from __future__ import annotations

import base64
import csv
import html
import io
import json
from pathlib import Path
from typing import Optional

import numpy as np

from ..paths import DataRoot
from ..state import Control, Progress, digest
from . import ANALYSIS_VERSION
from .diagnostics import stats_dir
from .schemes import CLASSES, CURVE_INPUTS, RUNS, VIEWS, run_path, schemes_dir
from .stability import stability_path
from .validation import TARGETS, validation_path
from .values import target_states, values_path

PRESSURE_FUNCTIONS = ("catchment_hydrology", "reach_inflow", "streamflow_regime", "sediment_continuity",
                      "community_dynamics", "watershed_connectivity", "hyporheic_connectivity")
#: the diagnostics quantity that stands for each function's incumbent input
QUANTITY_OF = {"low_flow_baseflow_dynamics": "hyd_integrity", "bed_composition_bedform_dynamics": "sed_integrity",
               "high_flow_dynamics": "bhr", "channel_floodplain_dynamics": "bhr", "channel_evolution": "bhr",
               "floodplain_connectivity": "er", "streamflow_regime": "dor", "watershed_connectivity": "dams",
               "community_dynamics": "taxa", "surface_water_storage": "wetland", "habitat_provision": "woody_corridor",
               "light_thermal_regime": "woody_corridor", "carbon_processing": "natural_corridor",
               "catchment_hydrology": "impervious", "reach_inflow": "road_density", "sediment_continuity": "k_factor",
               "nutrient_cycling": "tn", "hyporheic_connectivity": "slope", "water_soil_quality": "tn",
               "population_support": "hyd_integrity"}
LEVEL_ORDER = ("national", "nars9", "l2", "l3")
LEVEL_LABEL = {"national": "National", "nars9": "NARS-9", "l2": "Level II", "l1": "Level I", "l3": "Level III"}
RUN_LEGEND = (
    "S0 represents the loaded values method, not the saved historical baseline. "
    "Its banded view retains the loaded ratings and can be checked against current scored outputs. "
    "Guidance lines provide diagnostic continuous indices where available; incomplete guidance composites retain class anchors. "
    "S0 function AUC and rank correlations use those diagnostic indices; class agreement uses the retained classes. "
    "SN, S9, S2 and S3 are diagnostic refits at national, NARS-9, Level II and Level III levels. "
    "In the route table, incumbent_* refers to the displayed diagnostic run, while s0_auc_poor refers to S0. "
    "These scenarios and recommendations do not change the accepted criteria.")


def quantity_of() -> dict:
    """The current input descriptors, selected at call time like scoring."""
    from easi.config import criteria_set
    quantities = dict(QUANTITY_OF)
    if criteria_set() == "regional":
        quantities.update({"low_flow_baseflow_dynamics": "flow_variability_cv",
                           "bed_composition_bedform_dynamics": "bed_agriculture",
                           "population_support": "biological_model_probability"})
    return quantities


def input_distribution_html(function: str, rows: list[dict]) -> str:
    primary = quantity_of().get(function, "")
    quantities = [primary]
    note = ""
    if primary == "biological_model_probability":
        quantities.append("biological_integrity_fallback")
        note = ("<p class='note'>Published model probability and the landscape integrity fallback "
                "are separate source routes. Each distribution includes only its route; share_missing "
                "also counts reaches on another route. Neither quantity is a measured MMI.</p>")
    return (f"<h2>Distribution of the incumbent input ({html.escape(primary)}), national and per state</h2>"
            + note + table_html([r for r in rows if r["level"] in ("national", "state") and r["quantity"] in quantities],
                                ["quantity", "level", "stratum", "n", "share_missing", "p05", "p25", "p50", "p75", "p95",
                                 "iqr", "robust_cv", "share_zero", "share_cap", "c1_constant", "c2_censored", "c3_zero_inflated"]))


def report_dir(root: DataRoot) -> Path:
    return root.analysis / "report"


def routes_path(root: DataRoot) -> Path:
    return report_dir(root) / "routes.csv"


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _num(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if np.isfinite(out) else None


def _fmt(value, digits: int = 3) -> str:
    number = _num(value)
    if number is None:
        return "" if value in (None, "", "None", "nan") else html.escape(str(value))
    return f"{number:.{digits}f}"


# ------------------------------------------------------------ html helpers
STYLE = """
body { font-family: Georgia, serif; max-width: 1180px; margin: 24px auto; padding: 0 16px; color: #1f2937; }
h1, h2, h3 { font-family: Helvetica, Arial, sans-serif; }
table { border-collapse: collapse; font-size: 12px; margin: 8px 0 18px; }
th, td { border: 1px solid #d1d5db; padding: 3px 6px; text-align: right; }
th { background: #f3f4f6; } td:first-child, th:first-child { text-align: left; }
.note { color: #6b7280; font-size: 13px; } .warn { color: #b45309; } .good { color: #047857; }
img { max-width: 100%; }
"""


def table_html(rows: list[dict], columns: Optional[list[str]] = None, digits: int = 3, limit: int = 400) -> str:
    if not rows:
        return "<p class='note'>no rows</p>"
    columns = columns or list(rows[0].keys())
    head = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
    body = []
    for row in rows[:limit]:
        body.append("<tr>" + "".join(f"<td>{_fmt(row.get(c), digits)}</td>" for c in columns) + "</tr>")
    more = f"<p class='note'>{len(rows) - limit} more rows in the CSV</p>" if len(rows) > limit else ""
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>{more}"


def figure_html(png_bytes: bytes, caption: str) -> str:
    data = base64.b64encode(png_bytes).decode("ascii")
    return f"<figure><img src='data:image/png;base64,{data}' alt='{html.escape(caption)}'/><figcaption class='note'>{html.escape(caption)}</figcaption></figure>"


def _png(fig) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return buffer.getvalue()


# ------------------------------------------------------------ figures
def figure_paradigm(comparison: list[dict]):
    """Per run: the ECI P10 / P50 / P90 per state under the three views."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    runs = [r for r in RUNS if any(row["run"] == r for row in comparison)]
    states = sorted({row["group"] for row in comparison if row["group"] != "US"})
    if not runs or not states:
        return None
    fig, axes = plt.subplots(len(runs), 1, figsize=(10, 2.2 * len(runs) + 1), sharex=True, squeeze=False)
    colors = {"banded": "#1d4ed8", "continuous": "#b91c1c", "mix": "#047857"}
    for ax, run in zip(axes[:, 0], runs):
        for k, view in enumerate(VIEWS):
            for i, state in enumerate(states):
                row = next((r for r in comparison if r["run"] == run and r["view"] == view and r["group"] == state), None)
                if row is None:
                    continue
                x = i + (k - 1) * 0.25
                ax.plot([x, x], [_num(row["eci_p10"]), _num(row["eci_p90"])], color=colors[view], lw=2)
                ax.plot(x, _num(row["eci_p50"]), "o", color=colors[view], ms=4)
        for edge in (0.39, 0.69):
            ax.axhline(edge, color="#9ca3af", lw=0.8, ls="--")
        ax.set_ylim(0, 1)
        ax.set_ylabel(f"{run}\nECI")
        ax.set_xticks(range(len(states)))
        ax.set_xticklabels(states)
    handles = [plt.Line2D([], [], color=c, lw=2, label=v) for v, c in colors.items()]
    axes[0, 0].legend(handles=handles, loc="upper right", fontsize=8, ncol=3)
    fig.suptitle("ECI P10 to P90 (bar) and P50 (dot) per state: banded, continuous and mix views", fontsize=10)
    return _png(fig)


def figure_levels(t_l2: list[dict]):
    """Per state: the banded ECI spread and the pinned cells per run."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    states = sorted({r["group"] for r in t_l2})
    runs = [r for r in RUNS if any(row["run"] == r for row in t_l2)]
    if not states or not runs:
        return None
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 3.5))
    width = 0.8 / max(len(runs), 1)
    for k, run in enumerate(runs):
        spread, pinned = [], []
        for state in states:
            row = next((r for r in t_l2 if r["run"] == run and r["group"] == state), None)
            spread.append((_num(row["eci_p90"]) or 0) - (_num(row["eci_p10"]) or 0) if row else 0)
            pinned.append(_num(row.get("pinned_cells_state")) or 0 if row else 0)
        xs = np.arange(len(states)) + (k - len(runs) / 2 + 0.5) * width
        ax1.bar(xs, spread, width=width, label=run)
        ax2.bar(xs, pinned, width=width, label=run)
    for ax, title in ((ax1, "within-state ECI P10 to P90 spread (banded view)"), (ax2, "pinned function cells per state (one class at 90 %+)")):
        ax.set_xticks(range(len(states)))
        ax.set_xticklabels(states)
        ax.set_title(title, fontsize=9)
    ax1.legend(fontsize=7, ncol=len(runs))
    return _png(fig)


def figure_map(root: DataRoot, run: str, view: str):
    """Every scored reach's anchor coloured by its ECI under the run and view."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pyarrow.parquet as pq
    if not run_path(root, run).exists():
        return None
    scores = pq.read_table(run_path(root, run), columns=["comid", f"eci_{view}"])
    points = pq.read_table(values_path(root), columns=["comid", "lat", "lon"])
    eci = np.asarray(scores.column(f"eci_{view}").to_pandas(), dtype=float)
    lat = np.asarray(points.column("lat").to_pandas(), dtype=float)
    lon = np.asarray(points.column("lon").to_pandas(), dtype=float)
    a = np.asarray(scores.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
    b = np.asarray(points.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
    order = np.argsort(b, kind="stable")
    pos = np.searchsorted(b[order], a)
    pos = np.minimum(pos, max(len(b) - 1, 0))
    ok = (b[order][pos] == a) & np.isfinite(eci)
    fig, ax = plt.subplots(figsize=(12, 7))
    from matplotlib.colors import ListedColormap, BoundaryNorm
    cmap = ListedColormap(["#f5b5b5", "#f5e7a6", "#c8d9f2"])
    norm = BoundaryNorm([0, 0.39, 0.69, 1.0], cmap.N)
    ax.scatter(lon[order][pos[ok]], lat[order][pos[ok]], c=eci[ok], s=0.15, cmap=cmap, norm=norm, rasterized=True, linewidths=0)
    ax.set_aspect(1.25)
    ax.set_title(f"ECI class per reach: run {run}, {view} view", fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])
    png = _png(fig)
    (report_dir(root) / "maps").mkdir(parents=True, exist_ok=True)
    (report_dir(root) / "maps" / f"{run}_eci_{view}.png").write_bytes(png)
    return png


# ------------------------------------------------------------ routes
def route_rows(root: DataRoot, validation_rows: list[dict], diag_rows: list[dict], pinned_rows: list[dict],
               stability_rows: list[dict], level: str) -> list[dict]:
    """One row per function: the route the rules recommend at the given
    level, with the deciding evidence (the owner edits the CSV)."""
    run = next((r for r, lv in RUNS.items() if lv == level), "SN")
    functions = sorted({r["function"] for r in pinned_rows}) or list(TARGETS)
    diag_national = {r["quantity"]: r for r in diag_rows if r["level"] == "national"}
    rows = []
    for fk in functions:
        diag = diag_national.get(quantity_of().get(fk, ""), {})
        flags = [name for name, key in (("constant", "c1_constant"), ("censored", "c2_censored"), ("zero-inflated", "c3_zero_inflated"))
                 if str(diag.get(key, "")).lower() == "true"]
        states = [r for r in pinned_rows if r["run"] == "S0" and r["function"] == fk]
        pinned_today = sum(1 for r in states if str(r.get("pinned")).lower() == "true")
        pinned_run = sum(1 for r in pinned_rows if r["run"] == run and r["function"] == fk and str(r.get("pinned")).lower() == "true")
        incumbent = next((r for r in validation_rows if r["run"] == run and r["subject"] == fk and r["region"] == "US"), {})
        s0 = next((r for r in validation_rows if r["run"] == "S0" and r["subject"] == fk and r["region"] == "US"), {})
        candidates = [r for r in validation_rows if r["run"] == run and r["region"] == "US"
                      and r["subject"].startswith(f"cand__{fk}__")]
        best = None
        for cand in candidates:
            score = max(_num(cand.get("auc_poor")) or 0.5, 0.5 + abs(_num(cand.get("rho")) or 0) / 2)
            if best is None or score > best[0]:
                best = (score, cand)
        beats = False
        if best is not None:
            gain_auc = (_num(best[1].get("auc_poor")) or 0) - (_num(incumbent.get("auc_poor")) or 0.5)
            gain_rho = abs(_num(best[1].get("rho")) or 0) - abs(_num(incumbent.get("rho")) or 0)
            beats = best[1].get("verdict") == "validated" and (gain_auc >= 0.05 or gain_rho >= 0.10)
        curved = fk in CURVE_INPUTS
        curve_quantities = [q for q, _c, _m in CURVE_INPUTS.get(fk, [])]
        stab = [r for r in stability_rows if r["quantity"] in curve_quantities and r["level"] == level]
        flip_ok = (float(np.mean([r["verdict"] == "accept" for r in stab])) if stab else None)
        if fk in PRESSURE_FUNCTIONS:
            route, rule = "keep (guidance bands)", "pressure or count metric: absolute bands; candidates reported"
            if beats:
                route, rule = "substitute", "a validated candidate beats the incumbent (C6)"
        elif beats:
            route, rule = "substitute", ((f"incumbent {', '.join(flags)} (C1 to C3) and " if flags else "")
                                         + "a validated candidate beats the incumbent (C6)")
        elif flags:
            route, rule = "substitute (unvalidated candidate)", f"incumbent {', '.join(flags)}; no candidate passes C6 yet"
        elif curved and incumbent.get("verdict") == "validated":
            route, rule = "re-criteria (regional curve)", "metric tracks the field truth; criteria regionalised (C5)"
        elif curved:
            route, rule = "re-criteria (regional curve, unvalidated)", "no field target or not validated; distribution evidence only (C7)"
        else:
            route, rule = "keep", "no change indicated"
        rows.append({
            "function": fk, "route": route, "rule": rule, "level": level, "run": run,
            "flags": ";".join(flags), "pinned_states_today": pinned_today, "pinned_states_run": pinned_run,
            "incumbent_auc_poor": incumbent.get("auc_poor"), "incumbent_rho": incumbent.get("rho"),
            "incumbent_verdict": incumbent.get("verdict"), "s0_auc_poor": s0.get("auc_poor"),
            "best_candidate": best[1]["subject"] if best else "", "candidate_auc_poor": best[1].get("auc_poor") if best else "",
            "candidate_rho": best[1].get("rho") if best else "", "candidate_verdict": best[1].get("verdict") if best else "",
            "candidate_beats_incumbent": beats, "flip_pass_share": flip_ok,
        })
    return rows


# ------------------------------------------------------------ recommendations
AUC_LOSS_MAX = 0.03
RHO_LOSS_MAX = 0.05
PINNED_EXCESS_MAX = 3


def recommend_level(t_l1: list[dict], t_p3: Optional[list[dict]] = None) -> tuple[str, list[str]]:
    """C15: the coarsest level such that at most a third of the curved
    quantities are materially shifted at it (half or more of the reaches in
    Level III panels at least 0.5 IQR from the level's panel), the pooled
    NRSA loss against Level III is under 0.03 ECI AUC and 0.05 rho, and the
    pinned cells are within 3 of Level III; ties go coarser."""
    notes = []
    quantities = sorted({r["quantity"] for r in t_l1})
    if not quantities:
        return "national", ["no curved quantity yet"]
    by = {(r["quantity"], r["level"]): r for r in t_l1}
    run_of = {level: run for run, level in RUNS.items() if level}

    def material(level: str) -> Optional[float]:
        shares = [_num(by.get((q, level), {}).get("share_reaches_material")) for q in quantities]
        shares = [x for x in shares if x is not None]
        return float(np.mean([x >= 0.5 for x in shares])) if shares else None

    def rho(level: str) -> Optional[float]:
        values = [_num(by.get((q, level), {}).get("nrsa_rho_pooled")) for q in quantities]
        values = [v for v in values if v is not None]
        return float(np.mean(values)) if values else None

    def auc(level: str) -> Optional[float]:
        row = next((r for r in (t_p3 or []) if r.get("run") == run_of.get(level) and r.get("view") == "banded"
                    and r.get("region") == "US"), None)
        return _num(row.get("auc_rt_r_vs_im")) if row else None

    def pinned(level: str) -> Optional[int]:
        values = [_num(by.get((q, level), {}).get("pinned_cells")) for q in quantities]
        values = [v for v in values if v is not None]
        return int(sum(values)) if values else None

    rho_l3, auc_l3, pinned_l3 = rho("l3"), auc("l3"), pinned("l3")
    for level in ("national", "nars9", "l2"):
        share_material = material(level)
        if share_material is None:                  # no curved quantity evaluated at this level: not selectable
            notes.append(f"{LEVEL_LABEL[level]}: no curved quantity evaluated at this level")
            continue
        rho_loss = None if rho_l3 is None or rho(level) is None else rho_l3 - rho(level)
        auc_loss = None if auc_l3 is None or auc(level) is None else auc_l3 - auc(level)
        pinned_excess = None if pinned_l3 is None or pinned(level) is None else pinned(level) - pinned_l3
        fmt = lambda v, d=3: "n/a" if v is None else round(v, d)  # noqa: E731
        notes.append(f"{LEVEL_LABEL[level]}: share of curved quantities materially shifted {share_material:.2f}; "
                     f"loss vs Level III: ECI AUC {fmt(auc_loss)}, pooled rho {fmt(rho_loss)}; "
                     f"pinned cells {fmt(pinned_excess, 0)} more than Level III")
        if (share_material <= 1 / 3 and (rho_loss is None or rho_loss < RHO_LOSS_MAX)
                and (auc_loss is None or auc_loss < AUC_LOSS_MAX)
                and (pinned_excess is None or pinned_excess <= PINNED_EXCESS_MAX)):
            return level, notes
    notes.append("Level III: every coarser level fails a clause")
    return "l3", notes


def recommend_paradigm(t_p1: list[dict], t_p3: list[dict], level: str) -> tuple[str, list[str]]:
    """C16 in brief: continuous if its ECI AUC for reference against most
    disturbed gains at least 0.02 over banded, or the within-state spread
    widens by 0.05 in at least three quarters of the states without loss."""
    run = next((r for r, lv in RUNS.items() if lv == level), "SN")
    notes = []
    aucs = {r["view"]: _num(r.get("auc_rt_r_vs_im")) for r in t_p3 if r["run"] == run and r["region"] == "US"}
    states = sorted({r["group"] for r in t_p1 if r["run"] == run and r["group"] != "US"})
    widened = 0
    for state in states:
        rows = {r["view"]: r for r in t_p1 if r["run"] == run and r["group"] == state}
        if "banded" in rows and "continuous" in rows:
            spread = lambda r: (_num(r["eci_p90"]) or 0) - (_num(r["eci_p10"]) or 0)  # noqa: E731
            widened += int(spread(rows["continuous"]) - spread(rows["banded"]) >= 0.05)
    gain = None if aucs.get("continuous") is None or aucs.get("banded") is None else aucs["continuous"] - aucs["banded"]
    notes.append(f"run {run}: ECI AUC (reference vs most disturbed) banded {aucs.get('banded')}, continuous {aucs.get('continuous')}, mix {aucs.get('mix')}")
    notes.append(f"within-state P10 to P90 widened by 0.05 or more in {widened} of {len(states)} states under the continuous view")
    no_loss = gain is None or gain >= 0
    if (gain is not None and gain >= 0.02) or (states and widened >= 0.75 * len(states) and no_loss):
        return "continuous", notes
    return "banded", notes


# ------------------------------------------------------------ the step
def inputs(root: DataRoot, options: Optional[dict] = None) -> str:
    stamps = [(p.name, p.stat().st_size, int(p.stat().st_mtime)) if p.exists() else None
              for p in (schemes_dir(root) / "scheme_comparison.csv", stats_dir(root) / "level_T_L1.csv",
                        validation_path(root), stability_path(root))]
    return digest("report", ANALYSIS_VERSION, stamps, (options or {}).get("phase"), quantity_of(), 2)


def run(root: DataRoot, progress: Progress, control: Control, options: Optional[dict] = None) -> Path:
    options = dict(options or {})
    out = report_dir(root)
    (out / "scorecards").mkdir(parents=True, exist_ok=True)
    progress.begin("analysis", "report", total=6, message="report: reading the tables")
    comparison = _read_csv(schemes_dir(root) / "scheme_comparison.csv")
    pinned = _read_csv(schemes_dir(root) / "pinned_cells.csv")
    gradients = _read_csv(schemes_dir(root) / "sanity_gradients.csv")
    t_l1 = _read_csv(stats_dir(root) / "level_T_L1.csv")
    t_l2 = _read_csv(stats_dir(root) / "level_T_L2.csv")
    t_p1 = _read_csv(stats_dir(root) / "paradigm_T_P1.csv")
    t_p2 = _read_csv(stats_dir(root) / "paradigm_T_P2.csv")
    t_p3 = _read_csv(stats_dir(root) / "paradigm_T_P3.csv")
    border = _read_csv(stats_dir(root) / "border_excess.csv")
    diag = _read_csv(stats_dir(root) / "diag_metric_stratum.csv")
    variance = _read_csv(stats_dir(root) / "variance_decomp.csv")
    validation_rows = _read_csv(validation_path(root))
    stability_rows = _read_csv(stability_path(root))
    notes = json.loads((schemes_dir(root) / "candidate_notes.json").read_text(encoding="utf-8")) if (schemes_dir(root) / "candidate_notes.json").exists() else {}
    targets = set(target_states(root))
    if targets:                       # the per-state tables cover the fully scored states only (plus the national row)
        keep = lambda row, key: row.get(key) == "US" or row.get(key) in targets  # noqa: E731
        comparison = [r for r in comparison if keep(r, "group")]
        pinned = [r for r in pinned if r.get("state") in targets]
        t_l2 = [r for r in t_l2 if keep(r, "group")]
        t_p1 = [r for r in t_p1 if keep(r, "group")]
        t_p2 = [r for r in t_p2 if keep(r, "group")]
    level, level_notes = recommend_level(t_l1, t_p3)
    if options.get("level") in LEVEL_LABEL:
        level = options["level"]
    paradigm, paradigm_notes = recommend_paradigm(t_p1, t_p3, level)
    progress.tick(done=1, message="report: routes")
    routes = route_rows(root, validation_rows, diag, pinned, stability_rows, level)
    with open(routes_path(root), "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(routes[0].keys()) if routes else ["function"])
        writer.writeheader()
        writer.writerows(routes)
    progress.tick(done=2, message="report: figures")
    control.check()
    figures = []
    png = figure_paradigm(comparison)
    if png:
        figures.append(figure_html(png, "Paradigm views per run and state"))
    png = figure_levels(t_l2)
    if png:
        figures.append(figure_html(png, "Level comparison per state (banded view)"))
    maps = []
    for run_name in RUNS:
        for view in ("banded", "continuous"):
            png = figure_map(root, run_name, view)
            if png and run_name in ("S0", next((r for r, lv in RUNS.items() if lv == level), "SN")):
                maps.append(figure_html(png, f"ECI class per reach, run {run_name}, {view} view"))
    progress.tick(done=4, message="report: scorecards")
    for route in routes:
        fk = route["function"]
        card = [f"<h1>{fk}</h1>", "<p class='note'>Scorecard of the stress test.</p>",
                "<h2>Route</h2>", table_html([route]),
                "<h2>Class shares by state and run</h2>",
                table_html([r for r in pinned if r["function"] == fk], ["run", "state", "n", "share_poor", "share_fair", "share_good", "top_class", "pinned", "index_sd", "curve_share"]),
                input_distribution_html(fk, diag),
                "<h2>NRSA agreement (national rows, every run)</h2>",
                table_html([r for r in validation_rows if r["region"] == "US" and (r["subject"] == fk or r["subject"].startswith(f"cand__{fk}__"))],
                           ["run", "subject", "target", "n", "n_poor", "n_good", "auc_poor", "auc_poor_lo", "auc_poor_hi", "auc_good", "rho", "rho_lo", "rho_hi", "kappa", "verdict"]),
                "<h2>Curve stability</h2>",
                table_html([r for r in stability_rows if r["quantity"] in [q for q, _c, _m in CURVE_INPUTS.get(fk, [])]],
                           ["quantity", "level", "stratum", "split", "n_members", "n_population", "x39", "x69", "flip_mean", "flip_p90", "verdict"]),
                "<h2>Candidate rules</h2>",
                "<ul>" + "".join(f"<li><b>{html.escape(k)}</b>: {html.escape(v)}</li>" for k, v in notes.items() if k.startswith(f"cand__{fk}__")) + "</ul>"]
        (out / "scorecards" / f"{fk}.html").write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>{fk}</title><style>{STYLE}</style></head><body>{''.join(card)}</body></html>", encoding="utf-8")
    progress.tick(done=5, message="report: index")
    sheet = decision_sheet(level, level_notes, paradigm, paradigm_notes, routes, gradients, t_p3, t_l1)
    (out / "decision_sheet.md").write_text(sheet, encoding="utf-8")
    index = [
        "<h1>EASI sensitivity stress test</h1>",
        f"<p class='note'>Analysis version {ANALYSIS_VERSION}. Recommendation rules applied: level <b>{LEVEL_LABEL[level]}</b>, paradigm <b>{paradigm}</b>. The owner decides; the decision sheet and routes.csv carry the numbers.</p>",
        f"<p class='note'>Target states (fully scored): {html.escape(', '.join(sorted(targets)) if targets else 'every state in the values table')}. National rows pool every scored reach, border spill included.</p>",
        f"<p class='note'>{html.escape(RUN_LEGEND)}</p>",
        "<h2>1. Paradigm views</h2>",
        *[f"<p class='note'>{html.escape(n)}</p>" for n in paradigm_notes],
        *figures[:1],
        "<h3>T-P1: ECI spread and class shares per state and view</h3>", table_html(t_p1, ["run", "view", "group", "n", "eci_p10", "eci_p50", "eci_p90", "eci_sd", "share_nf", "share_ar", "share_f", "pinned_cells"]),
        "<h3>T-P2: share of reaches whose ECI class changes against the banded view</h3>", table_html(t_p2, ["run", "view", "group", "n", "share_band_changes"]),
        "<h3>T-P3: ECI-level NRSA agreement per view</h3>", table_html(t_p3),
        "<h2>2. Level comparison</h2>",
        *[f"<p class='note'>{html.escape(n)}</p>" for n in level_notes],
        *figures[1:2],
        "<h3>T-L1: per curved quantity and level</h3>", table_html(t_l1),
        "<h3>T-L2: per state and run (banded view)</h3>", table_html(t_l2, ["run", "group", "n", "eci_p10", "eci_p50", "eci_p90", "eci_sd", "share_nf", "share_ar", "share_f", "pinned_cells_state"]),
        "<h3>Border excess (cross-border minus within-region, adjacent reaches)</h3>", table_html(border),
        "<h2>3. Gates: sanity gradients</h2>", table_html(gradients),
        "<h2>4. Routes per function</h2>", table_html(routes, digits=3),
        "<p class='note'>Scorecards: " + ", ".join(f"<a href='scorecards/{r['function']}.html'>{r['function']}</a>" for r in routes) + "</p>",
        "<h2>5. Maps</h2>", *maps,
        "<h2>6. Variance shares of the raw quantities</h2>", table_html(variance),
    ]
    (out / "index.html").write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>EASI stress test</title><style>{STYLE}</style></head><body>{''.join(index)}</body></html>", encoding="utf-8")
    progress.tick(done=6)
    progress.say(f"report: index.html, {len(routes)} scorecards, {len(maps)} maps, level {level}, paradigm {paradigm}")
    return out / "index.html"


def decision_sheet(level: str, level_notes: list[str], paradigm: str, paradigm_notes: list[str], routes: list[dict],
                   gradients: list[dict], t_p3: list[dict], t_l1: list[dict]) -> str:
    lines = ["# Decision sheet: EASI sensitivity stress test", "", RUN_LEGEND, "",
             f"Recommended level (rule C15): **{LEVEL_LABEL[level]}**", *[f"- {n}" for n in level_notes], "",
             f"Recommended paradigm (rule C16): **{paradigm}**", *[f"- {n}" for n in paradigm_notes], "",
             "## Routes per function", "", "| function | route | rule | best candidate | beats incumbent |", "|---|---|---|---|---|"]
    for r in routes:
        lines.append(f"| {r['function']} | {r['route']} | {r['rule']} | {r['best_candidate']} | {r['candidate_beats_incumbent']} |")
    lines += ["", "## Gates", "", "| run | view | gradient | Cliff's delta | reversed |", "|---|---|---|---|---|"]
    for g in gradients:
        lines.append(f"| {g['run']} | {g['view']} | {g['gradient']} | {g['cliffs_delta']} | {g['reversed']} |")
    lines += ["", "## ECI-level NRSA agreement", "", "| run | view | region | AUC R vs Im | AUC benthic Good vs Poor |", "|---|---|---|---|---|"]
    for r in t_p3:
        if r["region"] == "US":
            lines.append(f"| {r['run']} | {r['view']} | {r['region']} | {r['auc_rt_r_vs_im']} | {r['auc_bent_good_vs_poor']} |")
    lines += ["", "## Criteria sets per level", ""]
    for lv in LEVEL_ORDER:
        sets = sum(int(_num(r.get("criteria_sets")) or 0) for r in t_l1 if r["level"] == lv)
        lines.append(f"- {LEVEL_LABEL[lv]}: {sets} usable curves across the curved quantities")
    return "\n".join(lines) + "\n"
