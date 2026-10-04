"""Size and accuracy report over the converted VPUs, scaled to the nation."""
from __future__ import annotations

from pathlib import Path

from . import config
from .manifest import parts


def _gb(x: float) -> str:
    return f"{x / 1e9:5.2f}"


def report(root: Path) -> str:
    found = parts(root)
    if not found:
        return "no converted VPUs"
    rows = []
    tot = {"lines": 0, "lines_rows": 0, "attrs": 0, "cat_rows": 0}
    line_var: dict = {}
    cat_tot: dict = {}
    for p in found:
        e, s = p["entry"], p["stats"]
        n_lines = e["lines"]["rows"]
        tot["lines"] += e["lines"]["bytes"]
        tot["lines_rows"] += n_lines
        tot["attrs"] += s["lines"]["attribute_bytes"]
        for tol, b in s["lines"]["bytes_by_tolerance"].items():
            line_var[tol] = line_var.get(tol, 0) + b
        n_cat = max((c["rows"] for c in e["catchments"].values()), default=0)
        tot["cat_rows"] += n_cat
        cells = []
        for tol, c in e["catchments"].items():
            cat_tot[tol] = cat_tot.get(tol, 0) + c["bytes"]
            acc = s["catchments"]["by_tolerance"][tol]["area_change_pct"]
            cells.append(f"c{tol}m {c['bytes'] / 1e6:6.1f} MB ({c['bytes'] / max(n_cat, 1):5.0f} B, "
                         f"area chg med {acc.get('median', 0):.2f}% p99 {acc.get('p99', 0):.1f}%)")
        rows.append(f"{p['vpu']:>8}  zip {e['package']['bytes'] / 1e6:6.0f} MB  lines {n_lines:7d} "
                    f"{e['lines']['bytes'] / 1e6:6.1f} MB ({e['lines']['bytes'] / max(n_lines, 1):4.0f} B)  "
                    f"catch {n_cat:7d}  " + "  ".join(cells) + f"  [{s['seconds']['total']} s]")
    nat_l = config.NATIONAL_FLOWLINES
    nat_c = config.NATIONAL_CATCHMENTS
    out = ["Per VPU:"] + rows + ["", f"National estimate (pooled bytes per feature x {nat_l:,} lines, {nat_c:,} catchments):"]
    for tol in sorted(line_var, key=float):
        out.append(f"  lines simplified {tol:>3} m (attributes included): {_gb(line_var[tol] / tot['lines_rows'] * nat_l)} GB")
    out.append(f"  of which attributes alone: {_gb(tot['attrs'] / tot['lines_rows'] * nat_l)} GB")
    for tol in sorted(cat_tot, key=float):
        out.append(f"  catchments simplified {tol:>3} m: {_gb(cat_tot[tol] / tot['cat_rows'] * nat_c)} GB")
    best_line = min(line_var.values()) / tot["lines_rows"] * nat_l
    for tol in sorted(cat_tot, key=float):
        total = line_var.get(f"{found[0]['recipe']['line_tolerance_m']:g}", 0) / tot["lines_rows"] * nat_l \
            + cat_tot[tol] / tot["cat_rows"] * nat_c
        out.append(f"  TOTAL with the recipe's lines + catchments {tol} m: {_gb(total)} GB")
    out.append(f"  (smallest line variant measured: {_gb(best_line)} GB)")
    return "\n".join(out)
