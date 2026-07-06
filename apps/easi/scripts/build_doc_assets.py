"""Build figures and generated tables for the EASI V&V document.

Reads EASI results from the cached per-site JSON (data/easi_site_reports/, written by
run_sfari_sites.py) and SFARI results fresh from SFARI Summary.xlsx (the source of truth,
via sfari_data.read_sites), joined by site id. Editing the xlsx therefore updates every
figure and table on the next build with no EASI re-run. Produces:
  figures/val_eci_scatter.png       EASI vs SFARI Ecosystem Condition Index
  figures/val_subindex.png          EASI vs SFARI physical/chemical/biological
  figures/val_function_agreement.png mean abs diff per function (0-15 scale)
  figures/xs_<id>.png               EASI cross-section for each case study
  figures/photo_*.{png,jpg}         copied field photos for the case studies
  _generated/site_coverage.md       coverage table (all sites)         {#tbl-coverage}
  _generated/case_<id>.md           per-case function comparison
  _generated/val_stats.md           correlation + mean abs diff summary {#tbl-val-stats}
  _generated/val_classification.md  ECI class agreement matrix         {#tbl-val-class}
  _generated/appendix_comparison.md full per-site comparison           {#tbl-appendix}

Quantitative comparisons exclude SFARI duplicate rows; coverage and the appendix
list every site and mark duplicates.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from easi import config  # noqa: E402
from easi import scoring  # noqa: E402
import sfari_data  # noqa: E402

DOC = os.path.join(ROOT, "docs", "EASI_Documentation")
DATA = os.path.join(DOC, "data")
REPDIR = os.path.join(DATA, "easi_site_reports")
FIG = os.path.join(DOC, "figures")
GEN = os.path.join(DOC, "_generated")
VSITES = os.path.join(ROOT, "docs", "Verification Sites")

ACCENT = "#2f4b7c"
ACCENT_DK = "#243a61"
GRID = "#dfe5ee"

CLASSES = ["Non-Functioning", "Functioning-at-Risk", "Functioning"]
CLS_SHORT = {"Non-Functioning": "Non-Func.",
             "Functioning-at-Risk": "At-Risk", "Functioning": "Func."}

_META = config.metrics_by_id()
# function order (metric order), names, disciplines; matched = 19 (drop bed-comp)
ORDER = [(m["functionId"], m["functionName"], m["discipline"]) for m in _META.values()]
MATCHED = [t for t in ORDER if t[0] != "bed-composition-bedform-dynamics"]

CASE_IDS = ["MB", "CC", "MC"]
PHOTOS = {  # source filename in docs/Verification Sites -> figures/ name
    "MinkBrook.png": "photo_minkbrook.jpg",
    "Cowart Creek Upstream.jpg": "photo_cowart_up.jpg",
    "Mary's Creek Upstream 1.jpg": "photo_marys_up.jpg",
}

# Example report summaries for the Technical Note: a high-condition stream and a
# low-condition stream, one PNG each (site id, display name, output file).
EXAMPLE_REPORTS = [
    ("MB", "Mink Brook", "example_high.png"),
    ("CC", "Cowart Creek", "example_low.png"),
]


def cls(v):
    if v is None:
        return None
    if v <= 0.39:
        return CLASSES[0]
    if v <= 0.69:
        return CLASSES[1]
    return CLASSES[2]


def load_records():
    """Merge EASI (cached JSON) with SFARI (fresh from the xlsx, the source of truth).

    SFARI scores, coordinates, and duplicate flags come from SFARI Summary.xlsx via
    sfari_data.read_sites(), so editing the xlsx updates every figure and table on the
    next build with no EASI re-run. EASI results come from data/easi_site_reports/<id>.json
    (status is "missing" for a site that has an xlsx row but has not been run yet).
    """
    recs = {}
    for s in sfari_data.read_sites():
        sid = s["site_id"]
        easi = report = deline = None
        status = "missing"
        jpath = os.path.join(REPDIR, f"{sid}.json")
        if os.path.exists(jpath):
            with open(jpath, encoding="utf-8") as f:
                j = json.load(f)
            status = j.get("status", "missing")
            easi, report = j.get("easi"), j.get("report")
            deline = j.get("delineation")
        recs[sid] = {
            "site": {k: s[k] for k in ("site_id", "name", "state", "lat", "lon",
                                       "sfari_duplicate", "duplicate_of")},
            "sfari": {"functions": s["sfari_functions"],
                      "planform_change": s["sfari_planform_change"],
                      "sub": s["sfari_sub"], "eci": s["sfari_eci"]},
            "easi": easi, "report": report, "delineation": deline, "status": status,
        }
    return recs


def ok_nondup(recs):
    """Sites that ran and are not SFARI duplicates (for quantitative stats)."""
    out = [r for r in recs.values()
           if r.get("status") == "ok" and not r["site"]["sfari_duplicate"]]
    out.sort(key=lambda r: (r["sfari"]["eci"] or 0), reverse=True)
    return out


def _pearson(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 2 or np.std(x) == 0 or np.std(y) == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def _mae(x, y):
    return float(np.mean(np.abs(np.asarray(x, float) - np.asarray(y, float))))


def _scatter(ax, sx, ey, labels, title, lim=(0, 1)):
    ax.plot(lim, lim, "--", color="#9aa7bd", lw=1, zorder=1)
    ax.scatter(sx, ey, s=46, color=ACCENT, edgecolor="white", lw=0.8, zorder=3)
    for x, y, lab in zip(sx, ey, labels):
        ax.annotate(lab, (x, y), fontsize=7, color=ACCENT_DK,
                    xytext=(3, 3), textcoords="offset points", zorder=4)
    ax.set_xlim(*lim)
    ax.set_ylim(*lim)
    ax.set_aspect("equal", "box")
    ax.set_title(title, fontsize=11, color=ACCENT_DK)
    ax.grid(True, color=GRID, lw=0.6)
    for s in ax.spines.values():
        s.set_color("#c7cfdb")


def fig_eci(sites):
    sx = [s["sfari"]["eci"] for s in sites]
    ey = [s["easi"]["eci"] for s in sites]
    labs = [s["site"]["site_id"] for s in sites]
    fig, ax = plt.subplots(figsize=(5.4, 5.4))
    _scatter(ax, sx, ey, labs, "Ecosystem Condition Index")
    ax.set_xlabel("SFARI (field)")
    ax.set_ylabel("EASI (desktop)")
    r, mae = _pearson(sx, ey), _mae(sx, ey)
    ax.text(0.03, 0.97, f"n = {len(sites)}\nr = {r:.2f}\nMAE = {mae:.2f}",
            transform=ax.transAxes, va="top", fontsize=9, color=ACCENT_DK,
            bbox=dict(boxstyle="round,pad=0.3", fc="#f6f8fb", ec="#c7cfdb"))
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "val_eci_scatter.png"), dpi=150)
    plt.close(fig)
    return r, mae


def fig_subindex(sites):
    keys = [("physical", "Physical"), ("chemical", "Chemical"),
            ("biological", "Biological")]
    fig, axes = plt.subplots(1, 3, figsize=(12.6, 4.5))
    out = {}
    for ax, (k, title) in zip(axes, keys):
        sx = [s["sfari"]["sub"][k] for s in sites]
        ey = [s["easi"]["subIndices"][k] for s in sites]
        labs = [s["site"]["site_id"] for s in sites]
        _scatter(ax, sx, ey, labs, title)
        ax.set_xlabel("SFARI (field)")
        out[k] = (_pearson(sx, ey), _mae(sx, ey))
        ax.text(0.03, 0.97, f"r = {out[k][0]:.2f}\nMAE = {out[k][1]:.2f}",
                transform=ax.transAxes, va="top", fontsize=8.5, color=ACCENT_DK,
                bbox=dict(boxstyle="round,pad=0.3", fc="#f6f8fb", ec="#c7cfdb"))
    axes[0].set_ylabel("EASI (desktop)")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "val_subindex.png"), dpi=150)
    plt.close(fig)
    return out


def fig_function_agreement(sites):
    rows = []
    for fid, name, disc in MATCHED:
        diffs = []
        for s in sites:
            e = s["easi"]["functions"].get(fid, {}).get("functionScore")
            sf = s["sfari"]["functions"].get(fid)
            if e is not None and sf is not None:
                diffs.append(abs(e - sf))
        if diffs:
            rows.append((name, float(np.mean(diffs))))
    rows.sort(key=lambda t: t[1])
    names = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    fig, ax = plt.subplots(figsize=(8.6, 6.6))
    ax.barh(names, vals, color=ACCENT, edgecolor="white")
    ax.set_xlabel("Mean absolute difference (function score, 0 to 15)")
    ax.set_title("EASI vs SFARI agreement by function", fontsize=11, color=ACCENT_DK)
    ax.grid(True, axis="x", color=GRID, lw=0.6)
    ax.invert_yaxis()
    for s in ax.spines.values():
        s.set_color("#c7cfdb")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "val_function_agreement.png"), dpi=150)
    plt.close(fig)
    return rows


def fig_example_reports():
    """Two example report summaries for the Technical Note Figure 1 and Figure 2:
    a high-condition stream (Mink Brook) and a low-condition stream (Cowart Creek).

    Reads the cached EASI report directly from the per-site JSON (no SFARI xlsx
    needed) and renders one horizontal-bar panel per stream, mirroring the app's
    report summary. Bars are colored by the same condition bands as the live app
    via scoring.index_band_color, so the figure matches what a user sees.
    """
    labels = ["Ecosystem", "Physical", "Chemical", "Biological"]
    for sid, name, outfile in EXAMPLE_REPORTS:
        with open(os.path.join(REPDIR, f"{sid}.json"), encoding="utf-8") as f:
            rep = json.load(f)["report"]
        sub = rep["subIndices"]
        vals = [rep["ecosystemConditionIndex"],
                sub["physical"], sub["chemical"], sub["biological"]]
        colors = [scoring.index_band_color(v) for v in vals]
        fig, ax = plt.subplots(figsize=(6.5, 1.9))
        ax.barh(labels[::-1], vals[::-1], color=colors[::-1], edgecolor="#888")
        ax.set_xlim(0, 1)
        for i, v in enumerate(vals[::-1]):
            ax.text(min(v + 0.02, 0.92), i, f"{v:.2f}", va="center", fontsize=9)
        ax.set_title(f"{name} ({cls(vals[0])})", fontsize=11, color=ACCENT_DK)
        ax.set_xlabel("Condition index (0 to 1)", fontsize=9)
        ax.tick_params(labelsize=9)
        ax.grid(True, axis="x", color=GRID, lw=0.6)
        for s in ax.spines.values():
            s.set_color("#c7cfdb")
        fig.tight_layout()
        fig.savefig(os.path.join(FIG, outfile), dpi=150)
        plt.close(fig)


def extract_cross_sections(recs):
    for sid in CASE_IDS:
        rec = recs.get(sid)
        if not rec or rec.get("status") != "ok":
            continue
        xs = (rec.get("report") or {}).get("crossSection") or {}
        b64 = xs.get("png_b64")
        if b64:
            with open(os.path.join(FIG, f"xs_{sid}.png"), "wb") as f:
                f.write(base64.b64decode(b64))


def copy_photos(max_px=1100, quality=82):
    """Downsize field photos for the web (a self-contained HTML embeds them)."""
    from PIL import Image
    for src, dst in PHOTOS.items():
        sp = os.path.join(VSITES, src)
        if not os.path.exists(sp):
            continue
        im = Image.open(sp)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_px / max(w, h))
        if scale < 1.0:
            im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        im.save(os.path.join(FIG, dst), "JPEG", quality=quality, optimize=True)


# --------------------------------------------------------------------------- #
# Markdown tables
# --------------------------------------------------------------------------- #
def w(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def table_coverage(recs):
    order = sorted(recs.values(),
                   key=lambda r: (r["sfari"]["eci"] or 0), reverse=True)
    lines = ["| Site | State | Lat | Lon | Drainage area (km^2) | EASI ECI | "
             "EASI class | SFARI ECI | SFARI class | Note |",
             "|---|:--:|--:|--:|--:|--:|:--:|--:|:--:|---|"]
    for r in order:
        s = r["site"]
        e = r.get("easi") or {}
        da = (r.get("delineation") or {}).get("drainage_area_sqkm")
        da_txt = f"{da:.1f}" if da is not None else "n/a"
        lat = f"{s['lat']:.4f}" if s.get("lat") is not None else "n/a"
        lon = f"{s['lon']:.4f}" if s.get("lon") is not None else "n/a"
        note = f"SFARI dup of {s['duplicate_of']}" if s["sfari_duplicate"] else ""
        st = r.get("status")
        if st != "ok":
            extra = "EASI not run" if st == "missing" else "EASI run failed"
            note = (note + ", " if note else "") + extra
        eeci = e.get("eci")
        lines.append(
            f"| {s['name']} | {s['state']} | {lat} | {lon} | "
            f"{da_txt} | {eeci if eeci is not None else 'n/a'} | "
            f"{CLS_SHORT.get(cls(eeci), 'n/a')} | {r['sfari']['eci']:.2f} | "
            f"{CLS_SHORT.get(cls(r['sfari']['eci']), 'n/a')} | {note} |")
    cap = (": Verification sites with EASI and SFARI Ecosystem Condition Index. "
           "All twenty EASI functions computed at every site. {#tbl-coverage}")
    w(os.path.join(GEN, "site_coverage.md"), "\n".join(lines) + "\n\n" + cap + "\n")


def table_case(recs, sid):
    rec = recs.get(sid)
    if not rec or rec.get("status") != "ok":
        w(os.path.join(GEN, f"case_{sid}.md"), "")
        return
    e, sf = rec["easi"], rec["sfari"]
    name = rec["site"]["name"]
    # scorecard line
    sc = (f"**EASI** ECI {e['eci']} (Physical {e['subIndices']['physical']}, "
          f"Chemical {e['subIndices']['chemical']}, "
          f"Biological {e['subIndices']['biological']}). "
          f"**SFARI** ECI {sf['eci']:.2f} "
          f"(Physical {sf['sub']['physical']:.2f}, "
          f"Chemical {sf['sub']['chemical']:.2f}, "
          f"Biological {sf['sub']['biological']:.2f}).\n\n")
    lines = ["| Function | EASI rating | EASI score | SFARI score |",
             "|---|:--:|--:|--:|"]
    for fid, fname, disc in MATCHED:
        ef = e["functions"].get(fid, {})
        rating = ef.get("rating") or "n/a"
        star = " *" if ef.get("status") == "override" else ""
        es = ef.get("functionScore")
        ss = sf["functions"].get(fid)
        lines.append(f"| {fname} | {rating}{star} | "
                     f"{es if es is not None else 'n/a'} | "
                     f"{ss if ss is not None else 'n/a'} |")
    cap = (f": EASI and SFARI function scores at {name}, on the 0 to 15 scale. "
           f"An asterisk marks an expert override. {{#tbl-case-{sid.lower()}}}")
    note = ("\n\n*Functions marked with an asterisk were set by expert override "
            "to reflect field condition.*\n" if any(
                e["functions"].get(fid, {}).get("status") == "override"
                for fid, _, _ in MATCHED) else "\n")
    w(os.path.join(GEN, f"case_{sid}.md"),
      sc + "\n".join(lines) + "\n\n" + cap + note)


def table_val_stats(eci_stats, sub_stats):
    r, mae = eci_stats
    lines = ["| Comparison | Pearson r | Mean abs diff |",
             "|---|:--:|:--:|",
             f"| Ecosystem Condition Index | {r:.2f} | {mae:.2f} |"]
    for k, title in [("physical", "Physical sub-index"),
                     ("chemical", "Chemical sub-index"),
                     ("biological", "Biological sub-index")]:
        rr, mm = sub_stats[k]
        lines.append(f"| {title} | {rr:.2f} | {mm:.2f} |")
    cap = (": Agreement between EASI and SFARI across the non-duplicate sites. "
           "Index comparisons use the 0 to 1 scale. {#tbl-val-stats}")
    w(os.path.join(GEN, "val_stats.md"), "\n".join(lines) + "\n\n" + cap + "\n")


def table_classification(sites):
    # confusion matrix of ECI class: rows SFARI, cols EASI
    idx = {c: i for i, c in enumerate(CLASSES)}
    m = [[0, 0, 0] for _ in range(3)]
    exact = within1 = 0
    for s in sites:
        ec, sc = cls(s["easi"]["eci"]), cls(s["sfari"]["eci"])
        m[idx[sc]][idx[ec]] += 1
        if ec == sc:
            exact += 1
        if abs(idx[ec] - idx[sc]) <= 1:
            within1 += 1
    n = len(sites)
    head = "| SFARI \\\\ EASI | " + " | ".join(CLS_SHORT[c] for c in CLASSES) + " |"
    sep = "|---|:--:|:--:|:--:|"
    body = []
    for c in CLASSES:
        body.append(f"| {CLS_SHORT[c]} | " +
                    " | ".join(str(m[idx[c]][idx[d]]) for d in CLASSES) + " |")
    cap = (f": Agreement on Ecosystem Condition Index class across {n} sites. "
           f"Exact agreement {exact} of {n}. Within one class {within1} of {n}. "
           f"{{#tbl-val-class}}")
    w(os.path.join(GEN, "val_classification.md"),
      "\n".join([head, sep] + body) + "\n\n" + cap + "\n")
    return exact, within1, n


def table_appendix(recs):
    order = sorted(recs.values(),
                   key=lambda r: (r["sfari"]["eci"] or 0), reverse=True)
    lines = ["| Site | State | EASI ECI | SFARI ECI | EASI P/C/B | SFARI P/C/B | Note |",
             "|---|:--:|--:|--:|:--:|:--:|---|"]
    for r in order:
        s = r["site"]
        e = r.get("easi") or {}
        esi = e.get("subIndices") or {}
        sf = r["sfari"]
        note = f"SFARI dup of {s['duplicate_of']}" if s["sfari_duplicate"] else ""
        epcb = (f"{esi.get('physical')}/{esi.get('chemical')}/{esi.get('biological')}"
                if esi else "n/a")
        spcb = (f"{sf['sub']['physical']:.2f}/{sf['sub']['chemical']:.2f}/"
                f"{sf['sub']['biological']:.2f}")
        lines.append(f"| {s['name']} | {s['state']} | "
                     f"{e.get('eci', 'n/a')} | {sf['eci']:.2f} | {epcb} | "
                     f"{spcb} | {note} |")
    cap = (": Full EASI and SFARI comparison for every verification site "
           "(P/C/B = physical/chemical/biological sub-index). {#tbl-appendix}")
    w(os.path.join(GEN, "appendix_comparison.md"), "\n".join(lines) + "\n\n" + cap + "\n")


def main():
    os.makedirs(FIG, exist_ok=True)
    os.makedirs(GEN, exist_ok=True)
    recs = load_records()
    sites = ok_nondup(recs)
    print(f"loaded {len(recs)} records; {len(sites)} non-duplicate ok sites")

    eci_stats = fig_eci(sites)
    sub_stats = fig_subindex(sites)
    fig_function_agreement(sites)
    fig_example_reports()
    extract_cross_sections(recs)
    copy_photos()

    table_coverage(recs)
    for sid in CASE_IDS:
        table_case(recs, sid)
    table_val_stats(eci_stats, sub_stats)
    exact, within1, n = table_classification(sites)
    table_appendix(recs)

    print(f"ECI r={eci_stats[0]:.2f} MAE={eci_stats[1]:.2f}; "
          f"class exact {exact}/{n}, within-one {within1}/{n}")
    print("figures ->", FIG)
    print("tables  ->", GEN)


if __name__ == "__main__":
    main()
