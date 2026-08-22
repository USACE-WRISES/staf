"""The end-review packet a staged batch run writes for the owner.

An unattended run replaces the mid-course looks a person took during the
pilots, so the packet has to show, per metric, the things those looks caught:
the declared curve form and expected shape beside the realized shape, the band
text DEEP will print, a two-sided core's width against the reference range, the
domain bounds and violations, the sample and its missingness, the confidence
with its caps, the influence flag with its driver, and a thumbnail of every
curve. It then lists every standing decision the policy applied with its
evidence and rationale, every item left open for the owner, the portfolio with
both SELECT-01 counts, coverage, the differences against the prior published
version when one exists, and the exact promote command.

Pure builders (dicts in, dicts out) plus two writers. The gallery needs
matplotlib and is skipped cleanly when it is absent.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

from . import curve_svg, curves, run_state

logger = logging.getLogger("streamcurves")

NARROW_CORE_FRACTION = 0.10  # a two-sided core narrower than this share of the reference range is flagged


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _fmt(v, digits: int = 3) -> str:
    f = _num(v)
    if f is None:
        return "" if v is None else str(v)
    if abs(f) >= 1000:
        return f"{f:,.0f}"
    return f"{f:.{digits}g}"


def curve_rows_for_packet(result: dict) -> list[dict]:
    """One row per built curve with everything an end review needs to see."""
    cr = result.get("curve_review") or {}
    diags = result.get("diagnostics") or {}
    confs = result.get("confidence") or {}
    domain_checks = result.get("domain_checks") or {}
    gradients = result.get("deferred_gradients") or {}
    mandatory = result.get("mandatory_review") or {}
    missing = result.get("missingness") or {}
    intended = set(result.get("intended_metrics") or [])
    rows = []
    for mk, row in (result.get("curve_rows") or {}).items():
        mc = result["metric_config"].get(mk) or {}
        entry = cr.get(mk) or {}
        form = curves.curve_form_of(mc)
        dom = curves.metric_domain_of(mc)
        bands = curves.deep_contract_bands(
            row.get("curve_points"), curve_form=form,
            higher_is_better=mc.get("higher_is_better") is True, domain=dom)
        realized = run_state.realized_curve_shape(row.get("curve_points"))
        lo, hi = _num(row.get("min_val")), _num(row.get("max_val"))
        ref_range = (hi - lo) if (lo is not None and hi is not None) else None
        core_width = None
        core_fraction = None
        if bands.get("band_semantics") == "two_sided":
            fmin, fmax = _num(bands.get("deep_functioning_min")), _num(bands.get("deep_functioning_max"))
            if fmin is not None and fmax is not None:
                core_width = fmax - fmin
                if ref_range:
                    core_fraction = core_width / ref_range
        d = diags.get(mk) or {}
        loo, boot, infl = d.get("loo") or {}, d.get("bootstrap") or {}, d.get("influence") or {}
        conf = confs.get(mk) or {}
        review = mandatory.get(mk) or {}
        flags = []
        if entry.get("status") not in (run_state.CURVE_STATUS_AUTO_OK, None):
            flags.append(f"status {entry.get('status')}")
        if mc.get("expected_shape") and realized and mc.get("expected_shape") != realized \
                and not (mc.get("expected_shape") == "optimum" and realized == "optimum"):
            flags.append(f"expected {mc.get('expected_shape')}, realized {realized}")
        if (domain_checks.get(mk) or {}).get("violations"):
            flags.append(f"{domain_checks[mk]['violations']} anchor(s) outside the domain")
        if core_fraction is not None and core_fraction < NARROW_CORE_FRACTION:
            flags.append(f"two-sided core is {core_fraction:.0%} of the reference range: "
                         "check it against the metric's measurement precision")
        if infl.get("flagged"):
            flags.append(f"influence flag, driver {infl.get('driver')}, "
                         f"decision flip {'yes' if infl.get('decision_flip') else 'no'}")
        if mk in gradients:
            flags.append(f"deferred gradient on {gradients[mk].get('stratification')}")
        rows.append({
            "metric": mk,
            "display_name": mc.get("display_name"),
            "function": result["column_functions"].get(mk) or "(unmapped)",
            "in_scope": mk in intended,
            "review_status": entry.get("status"),
            "decision": entry.get("decision") or (
                run_state.DECISION_AUTO if entry.get("status") == run_state.CURVE_STATUS_AUTO_OK
                else run_state.DECISION_PENDING),
            "n_reference": row.get("n_reference"),
            "sample_disposition": (result.get("sample_sizes") or {}).get(mk, {}).get("disposition"),
            "missing_fraction": (missing.get(mk) or {}).get("missing_fraction"),
            "curve_form": form,
            "expected_shape": mc.get("expected_shape"),
            "realized_shape": realized,
            "band_semantics": bands.get("band_semantics"),
            "functioning_text": bands.get("functioning_text"),
            "core_width": core_width,
            "core_fraction_of_range": core_fraction,
            "reference_min": lo,
            "reference_max": hi,
            "domain_min": dom[0],
            "domain_max": dom[1],
            "domain_violations": (domain_checks.get(mk) or {}).get("violations"),
            "loo_mean_abs_delta": loo.get("held_out_mean_abs_delta"),
            "bootstrap_shape_stability": boot.get("shape_stability"),
            "bootstrap_structure_stability": boot.get("structure_stability"),
            "influence_flagged": bool(infl.get("flagged")),
            "influence_driver": infl.get("driver"),
            "influence_decision_flip": infl.get("decision_flip"),
            "confidence_total": conf.get("total"),
            "confidence_label": conf.get("label"),
            "confidence_caps": list(conf.get("caps_applied") or []),
            "review_open": list(review.get("open") or []),
            "flags": flags,
        })
    rows.sort(key=lambda r: (r["function"], r["metric"]))
    return rows


def portfolio_rows(result: dict, approvals: list[dict]) -> list[dict]:
    blocks = {}
    for block in ((result.get("bundle") or {}).get("metricsByFunction") or []):
        blocks[str(block.get("functionId"))] = [str(m.get("metricId")) for m in block.get("metrics") or []]
    approved = {str(a.get("functionId")): a for a in approvals or []}
    rows = []
    for p in result.get("portfolio") or []:
        fid = str(p.get("function_id") or "")
        in_bundle = blocks.get(fid)
        n_bundle = len(in_bundle) if in_bundle is not None else 0
        rows.append({
            "function": p.get("function"), "function_id": fid or None,
            "discipline": p.get("discipline"), "coverage": p.get("coverage"),
            "compact_metrics": list(p.get("metrics") or []),
            "bundle_metrics": in_bundle or [],
            "n_compact": len(p.get("metrics") or []), "n_bundle": n_bundle,
            "needs_approval": max(len(p.get("metrics") or []), n_bundle) > 2,
            "approved_by": (approved.get(fid) or {}).get("approvedBy"),
        })
    return rows


def excluded_rows(result: dict) -> list[dict]:
    out = []
    for fd in result.get("flagged_direction") or []:
        out.append({"metric": fd.get("metric"), "display_name": fd.get("display_name"),
                    "documented": bool(fd.get("documented")), "reason": fd.get("reason"),
                    "decided_by": fd.get("decided_by")})
    for mk, note in (result.get("removed_metrics") or {}).items():
        out.append({"metric": mk, "documented": True, "reason": f"removed from scope: {note}",
                    "decided_by": "recorded reviewer decision"})
    return out


def diff_against_prior(result: dict, prior_bundle: Optional[dict]) -> Optional[dict]:
    """Metric sets and band edges against the prior published bundle."""
    if not prior_bundle:
        return None
    def _by_metric(bundle):
        out = {}
        for block in bundle.get("metricsByFunction") or []:
            for m in block.get("metrics") or []:
                out.setdefault(str(m.get("metricId")), {"functions": [], "entry": m})
                out[str(m.get("metricId"))]["functions"].append(str(block.get("functionId")))
        return out
    new, old = _by_metric(result.get("bundle") or {}), _by_metric(prior_bundle)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    band_changes = []
    for mid in sorted(set(new) & set(old)):
        a, b = new[mid]["entry"], old[mid]["entry"]
        pa = a.get("curve", {}).get("points") or []
        pb = b.get("curve", {}).get("points") or []
        if pa != pb:
            ba = curves.deep_contract_bands(pa, curve_form=a.get("curve", {}).get("form"),
                                            higher_is_better=True)
            bb = curves.deep_contract_bands(pb, curve_form=b.get("curve", {}).get("form"),
                                            higher_is_better=True)
            band_changes.append({"metric": mid, "prior": bb.get("functioning_text"),
                                 "now": ba.get("functioning_text"),
                                 "confidence_prior": b.get("confidenceLabel"),
                                 "confidence_now": a.get("confidenceLabel")})
    prior_lib = (prior_bundle.get("library") or {})
    return {"prior_version": prior_lib.get("version"),
            "prior_reference_tier": prior_bundle.get("referenceTier"),
            "metrics_added": added, "metrics_removed": removed,
            "band_changes": band_changes}


def build_packet(result: dict, doc: dict, policy_result: dict, *, policy_meta: dict,
                 enabled: list[str], staged: Optional[dict], promote_command: str,
                 prior_bundle: Optional[dict] = None, gallery: Optional[str] = None,
                 approvals: Optional[list[dict]] = None,
                 gallery_html: Optional[str] = None) -> dict:
    queue = (doc.get("reviewQueue") or {})
    counts = result.get("screening_counts") or {}
    return {
        "schemaVersion": 1,
        "region": result.get("region"),
        "reference_tier": result.get("reference_tier"),
        "ref02_triggered": bool(result.get("ref02_triggered")),
        "review_flags": list(result.get("review_flags") or []),
        "screening": {"method": result.get("screening_method"), "counts": counts,
                      "n_candidates": result.get("n_candidates"),
                      "n_retained": len(result.get("retained_site_ids") or []),
                      "pool_disposition": result.get("reference_pool_disposition")},
        "tier_evaluation": list(result.get("tier_evaluation") or []),
        "policy": {"version": policy_meta.get("policy_version"), "sha256": policy_meta.get("sha256"),
                   "enabled": list(enabled or []), "applied_ids": policy_result.get("applied_ids") or []},
        "decisions_applied": policy_result.get("decisions") or [],
        "open_items": policy_result.get("uncovered") or [],
        "hard_stops": policy_result.get("hard_stops") or [],
        "queue_counts": queue.get("counts") or {},
        "curves": curve_rows_for_packet(result),
        "excluded": excluded_rows(result),
        "portfolio": portfolio_rows(result, approvals or []),
        "coverage": result.get("coverage") or {},
        "uncovered_functions": [
            {"function": g.get("function"), "candidates": g.get("candidate_metrics") or []}
            for g in (result.get("uncovered_functions") or [])],
        "source_reports": list(result.get("source_reports") or []),
        "prior_version_diff": diff_against_prior(result, prior_bundle),
        "staged": staged,
        "gallery": gallery,
        "gallery_html": gallery_html,
        "promote_command": promote_command,
        "inputs_digest": doc.get("inputsDigest"),
        "run_seed": result.get("run_seed"),
        "n_boot": result.get("diagnostics_n_boot"),
    }


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v).replace("|", "/").replace("\n", " ")
                                     for v in r) + " |")
    return out


def packet_markdown(p: dict) -> str:
    region = p.get("region") or {}
    lines = [f"# End-review packet: {region.get('name')} (EPA Level III {region.get('code')})", ""]
    s = p["screening"]
    lines += [f"Reference tier **{p['reference_tier']}**"
              + (" (REF-02 fallback fired)" if p["ref02_triggered"] else "")
              + f". Screening {s['method']}: {s['n_retained']} of {s['n_candidates']} candidates retained "
              f"({s['pool_disposition']} pool). Run seed {p['run_seed']}, {p['n_boot']} resamples, "
              f"inputs {str(p.get('inputs_digest') or '')[:19]}.", ""]
    for f in p.get("review_flags") or []:
        lines.append(f"- {f}")
    if p.get("review_flags"):
        lines.append("")
    lines += [f"Standing-decision policy {p['policy']['version']} ({str(p['policy']['sha256'])[:19]}), "
              f"enabled beyond the defaults: {', '.join(p['policy']['enabled']) or 'none'}. "
              f"Applied entries: {', '.join(p['policy']['applied_ids']) or 'none'}. "
              f"Queue: {p['queue_counts'].get('open', 0)} open, {p['queue_counts'].get('blocking', 0)} blocking.", ""]
    if p.get("staged"):
        st = p["staged"]
        lines += [f"Staged as version {st.get('version')} at `{st.get('path')}`.", ""]

    lines += ["## 1. Items left for you (open after the policy)", ""]
    if p["open_items"]:
        lines += _table(["item", "trigger", "blocking", "question", "evidence"],
                        [[o["item_id"], o["trigger"], "yes" if o["blocking"] else "",
                          o["question"], json.dumps(o.get("evidence"), default=str)[:160]]
                         for o in p["open_items"]])
    else:
        lines.append("None. Every queue item received a standing decision.")
    lines.append("")

    lines += ["## 2. Standing decisions applied", ""]
    if p["decisions_applied"]:
        lines += _table(["item", "class", "action", "rationale"],
                        [[f"{d['rule_id']}:{d['subject']}", d["decision_class"], d["action"],
                          d["rationale"]] for d in p["decisions_applied"]])
    else:
        lines.append("None.")
    lines.append("")

    lines += ["## 3. Curves", "",
              "Flags name what an end review has to look at: a realized shape that differs from the "
              "curated expectation, an anchor outside the domain, a two-sided core narrow enough to "
              "sit inside measurement precision, an influence driver, a deferred gradient.", ""]
    lines += _table(["metric", "function", "scope", "status", "n", "missing", "form", "band", "core",
                     "conf", "flags"],
                    [[r["metric"], r["function"], "in" if r["in_scope"] else "out", r["review_status"],
                      r["n_reference"],
                      "" if r["missing_fraction"] is None else f"{100*float(r['missing_fraction']):.0f}%",
                      f"{r['curve_form']}/{r['realized_shape'] or '?'}", r["functioning_text"],
                      "" if r["core_fraction_of_range"] is None
                      else f"{_fmt(r['core_width'])} ({r['core_fraction_of_range']:.0%} of range)",
                      f"{r['confidence_total']} {r['confidence_label']}"
                      + (f" [{', '.join(r['confidence_caps'])}]" if r["confidence_caps"] else ""),
                      "; ".join(r["flags"])] for r in p["curves"]])
    lines.append("")
    if p.get("gallery"):
        lines += [f"![curve gallery]({p['gallery']})", ""]
    if p.get("gallery_html"):
        lines += [f"The same gallery as a page with hover details: [{p['gallery_html']}]({p['gallery_html']})", ""]

    lines += ["## 4. Metrics not built or removed", ""]
    if p["excluded"]:
        lines += _table(["metric", "documented", "reason", "decided by"],
                        [[e["metric"], "yes" if e["documented"] else "NO",
                          e["reason"], e.get("decided_by") or ""] for e in p["excluded"]])
    else:
        lines.append("None.")
    lines.append("")

    cov = p.get("coverage") or {}
    lines += ["## 5. Portfolio and coverage", "",
              f"Coverage {cov.get('covered', 0)} of {cov.get('total', 20)} functions"
              + (f", {cov.get('excluded')} documented exceptions" if cov.get("excluded") else "")
              + (f", **{cov.get('missing')} uncovered**" if cov.get("missing") else "") + ".", ""]
    lines += _table(["function", "coverage", "compact", "bundle", "approval"],
                    [[r["function"], r["coverage"], ", ".join(r["compact_metrics"]) or "",
                      f"{r['n_bundle']}: " + ", ".join(r["bundle_metrics"]) if r["bundle_metrics"] else "",
                      ("needed: " + (r["approved_by"] or "NONE")) if r["needs_approval"] else ""]
                     for r in p["portfolio"]])
    lines.append("")
    if p.get("uncovered_functions"):
        lines += ["Uncovered functions and the metrics that would close them:", ""]
        for g in p["uncovered_functions"]:
            lines.append(f"- {g['function']}: {', '.join(g['candidates']) or 'no candidate in the crosswalk'}")
        lines.append("")

    lines += ["## 6. Per-metric reference tier evaluation (REF-02)", ""]
    te = p.get("tier_evaluation") or []
    if te:
        lines += _table(["metric", "n functional", "n applied", "trigger", "note"],
                        [[t.get("metric"), t.get("n_functional_pool"), t.get("n_applied_pool"),
                          "yes" if t.get("ref02_metric_trigger") else "", t.get("note")] for t in te])
    lines.append("")

    d = p.get("prior_version_diff")
    lines += ["## 7. Against the prior published version", ""]
    if d:
        lines += [f"Prior version {d['prior_version']} (tier {d['prior_reference_tier']}). "
                  f"Added: {', '.join(d['metrics_added']) or 'none'}. "
                  f"Removed: {', '.join(d['metrics_removed']) or 'none'}.", ""]
        if d["band_changes"]:
            lines += _table(["metric", "prior band", "band now", "confidence prior", "confidence now"],
                            [[c["metric"], c["prior"], c["now"], c["confidence_prior"], c["confidence_now"]]
                             for c in d["band_changes"]])
            lines.append("")
    else:
        lines += ["No prior published version of this assessment.", ""]

    lines += ["## 8. Data sources", ""]
    for rep in p.get("source_reports") or []:
        lines.append(f"- {rep.get('source')}: {rep.get('status')} "
                     f"({rep.get('n_columns', 0)} columns)"
                     + (f", {rep.get('reason')}" if rep.get("reason") else ""))
    lines.append("")

    lines += ["## 9. Promote", "",
              "After your review, and with any overrides you want recorded, publish the staged "
              "version into the canonical library with:", "", "```", p["promote_command"], "```", ""]
    return "\n".join(lines)


def write_packet(packet: dict, out_dir: Path | str) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / "review_packet.json"
    mp = out / "review_packet.md"
    jp.write_text(json.dumps(packet, indent=1, default=str) + "\n", encoding="utf-8")
    mp.write_text(packet_markdown(packet), encoding="utf-8")
    return jp, mp


def packet_tiles(result: dict) -> list[dict]:
    """The packet's curve rows as gallery tiles (``curve_svg`` schema): the
    packet's own scope test, flags, and confidence label ride on each tile."""
    cr = result.get("curve_review") or {}
    tiles = []
    for r in curve_rows_for_packet(result):
        mk = r["metric"]
        row = (result.get("curve_rows") or {}).get(mk) or {}
        tile = curve_svg.tile_from_curve_rows(
            mk, [row], metric_entry=(result.get("metric_config") or {}).get(mk),
            review_entry=cr.get(mk), function_label=r["function"], in_scope=r["in_scope"])
        tile["review_status"] = r["review_status"]
        tile["decision"] = r["decision"]
        tile["needs_review"] = bool(r.get("review_open")) or r["decision"] == run_state.DECISION_PENDING
        tile["flags"] = list(r["flags"])
        tile["badge"] = r.get("confidence_label")
        tiles.append(tile)
    return tiles


def write_curve_gallery_html(result: dict, path: Path | str) -> Optional[Path]:
    """The gallery as a self-contained page (inline SVG, no scripts): one tile
    per built curve with its status, flags, and confidence label on hover."""
    tiles = packet_tiles(result)
    if not tiles:
        return None
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(curve_svg.gallery_html(
        tiles, title=f"{result.get('name')}: {len(tiles)} curves"), encoding="utf-8")
    return out


def write_curve_gallery(result: dict, path: Path | str) -> Optional[Path]:
    """A grid of every built curve (seed points) with the reference range shaded
    and the condition-band breaks drawn. Skipped cleanly without matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        logger.warning("curve gallery skipped: %s", exc)
        return None
    rows = curve_rows_for_packet(result)
    if not rows:
        return None
    n = len(rows)
    ncol = 5
    nrow = math.ceil(n / ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(3.2 * ncol, 2.6 * nrow), squeeze=False)
    for ax in axes.flat:
        ax.set_visible(False)
    for ax, r in zip(axes.flat, rows):
        ax.set_visible(True)
        row = result["curve_rows"].get(r["metric"]) or {}
        pts = curves.deep_points_from_row(row) if hasattr(curves, "deep_points_from_row") else None
        if pts is None:
            from . import deep_export
            pts = deep_export.deep_points_from_row(row)
        xs = [p["x"] for p in (pts or [])]
        ys = [p["y"] for p in (pts or [])]
        if r["reference_min"] is not None and r["reference_max"] is not None:
            ax.axvspan(r["reference_min"], r["reference_max"], color="#d9e8d3", alpha=0.6, lw=0)
        for y in (0.39, 0.69):
            ax.axhline(y, color="#999999", lw=0.6, ls="--")
        style = "-" if r["in_scope"] else ":"
        ax.plot(xs, ys, style, color="#1f4e79" if r["in_scope"] else "#b04040", lw=1.4, marker="o", ms=2.5)
        title = f"{r['metric']} ({r['confidence_label'] or '?'})"
        if r["flags"]:
            title += " *"
        ax.set_title(title, fontsize=7.5)
        ax.set_ylim(-0.05, 1.05)
        ax.tick_params(labelsize=6)
    fig.suptitle(f"{result.get('name')}: {n} curves (dotted = not in scope, * = flagged)", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out
