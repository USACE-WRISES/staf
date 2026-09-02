"""Science Support Document — self-contained HTML builder.

Port of reports/science_support.qmd. The R version renders via Quarto with
embedded R code chunks (reads a report_context.rds, sources the OH parameter
map); that path is impossible from a Python app, so this builds the same
document as a single self-contained HTML string: static SFPF prose carried
verbatim, plus dynamic chapters per SFPF category -> function-based parameter
-> metric section, driven by the summary-export context. Curve figures embed as
base64 data URIs so the file needs no external assets. Cosmetic drift from the
Quarto cosmo theme is accepted.
"""

from __future__ import annotations

import base64
from . import engine_names
import html

import pandas as pd

from streamcurves.mapping import (
    oh_category_order,
    oh_covered_metrics,
    oh_data_sources,
    oh_function_parameter,
    oh_functional_category,
    oh_metrics_for_category,
    oh_metrics_for_parameter,
    oh_reference_notes,
    oh_units_display,
)

_CSS = """
:root { --accent: #1f77b4; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       color: #1a1a1a; margin: 0; line-height: 1.55; }
.sqt-layout { display: flex; align-items: flex-start; max-width: 1200px; margin: 0 auto; }
#TOC { position: sticky; top: 0; max-height: 100vh; overflow-y: auto; width: 260px;
       flex: 0 0 260px; padding: 1.25rem 1rem; font-size: 0.85rem; border-right: 1px solid #e6e6e6; }
#TOC .toc-title { font-weight: 700; margin-bottom: 0.5rem; }
#TOC a { display: block; color: #444; text-decoration: none; padding: 2px 0; }
#TOC a:hover { color: var(--accent); }
#TOC .toc-h2 { padding-left: 0.9rem; }
#TOC .toc-h3 { padding-left: 1.8rem; color: #666; }
.sqt-main { flex: 1 1 auto; padding: 1.5rem 2rem; min-width: 0; }
.quarto-title-block { margin-bottom: 1.5rem; border-bottom: 2px solid var(--accent); padding-bottom: 0.75rem; }
.quarto-title-block h1 { margin: 0; }
.quarto-title-block .subtitle { color: #666; font-size: 1.1rem; }
h1 { font-size: 1.7rem; margin-top: 2rem; }
h2 { font-size: 1.35rem; margin-top: 1.5rem; }
h3 { font-size: 1.1rem; margin-top: 1.25rem; }
.sqt-functional-statement { font-style: italic; color: #4a4a4a; margin: 0.5rem 0 1rem; }
.sqt-section-note { background: #f5f7fa; border-left: 3px solid var(--accent); padding: 0.75rem 1rem; margin: 1rem 0; }
.sqt-category-empty { color: #6c757d; font-style: italic; }
table.sqt-table { border-collapse: collapse; width: 100%; font-size: 0.82rem; margin: 0.75rem 0; }
table.sqt-table th, table.sqt-table td { border: 1px solid #dcdcdc; padding: 4px 8px; text-align: right; }
table.sqt-table th { background: #f0f3f7; }
table.sqt-table td:first-child, table.sqt-table th:first-child { text-align: left; }
img.sqt-curve { width: 90%; max-width: 720px; margin: 0.5rem 0; }
.text-muted { color: #6c757d; }
"""

_CATEGORY_INTROS = {
    "Hydrology": "Metrics in the Hydrology category describe the amount and timing of "
    "water delivery to the stream reach.",
    "Hydraulics": "Hydraulic metrics describe the interaction between flowing water and "
    "channel boundary — chiefly floodplain connectivity and channel cross-section geometry.",
    "Geomorphology": "Geomorphology parameters describe the channel form, bed material, "
    "lateral migration, large woody debris, and riparian vegetation extent and composition.",
    "Physicochemistry": "Physicochemistry parameters describe temperature, dissolved oxygen, "
    "and chemical water-quality indicators.",
    "Biology": "Biology parameters describe the condition of macroinvertebrate and fish "
    "communities that integrate stream function over time.",
}

_CATEGORY_EMPTY_NOTES = {
    "Physicochemistry": "This SQT version does not yet publish reference curves for "
    "physicochemistry parameters (temperature, dissolved oxygen, nutrients, total suspended "
    "solids). Reference data were not part of the current synthesis. Future revisions will add "
    "these parameters once suitable reference data are compiled.",
    "Biology": "This SQT version does not yet publish reference curves for biology parameters "
    "(macroinvertebrate IBI, fish IBI). Biological-monitoring data will be incorporated in a "
    "future revision.",
}


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _anchor(key: str) -> str:
    import re

    return "sec-" + re.sub(r"[^A-Za-z0-9_-]", "_", str(key))


def _threshold_table_html(tbl) -> str:
    if tbl is None or not isinstance(tbl, pd.DataFrame) or len(tbl) == 0:
        return (
            '<p class="sqt-section-note">Threshold table not available for this metric '
            "in the current session.</p>"
        )
    head = "".join(f"<th>{_esc(c)}</th>" for c in tbl.columns)
    rows = "".join(
        "<tr>" + "".join(f"<td>{_esc(r[c])}</td>" for c in tbl.columns) + "</tr>"
        for _, r in tbl.iterrows()
    )
    return f'<table class="sqt-table"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'


def _plot_data_uri(entry: dict) -> str | None:
    png = entry.get("plot_png")
    if callable(png):
        try:
            png = png()
        except Exception:  # noqa: BLE001
            png = None
    if not png:
        return None
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _metric_info(key: str, entry: dict, metric_config) -> dict:
    return {
        "display_name": entry.get("display_name")
        or ((metric_config or {}).get(key) or {}).get("display_name")
        or key,
        "units": oh_units_display(key, metric_config),
        "category": oh_functional_category(key),
        "parameter": oh_function_parameter(key),
        "functional_statement": oh_reference_notes(key),
        "n_obs": entry.get("n_obs"),
        "status_label": entry.get("status_label"),
        "selected_strat_label": entry.get("selected_curve_stratification_label") or "None",
        "threshold_table": entry.get("threshold_table"),
        "warning_summary": entry.get("warning_summary"),
        "data_sources": oh_data_sources(key),
        "plot_uri": _plot_data_uri(entry),
    }


def build_science_support_html(context: dict, metric_config=None) -> str:
    """Build the Science Support Document as a self-contained HTML string."""
    metrics_ctx = context.get("metrics") or {}
    session_meta = context.get("session_meta") or {}
    covered = [m for m in metrics_ctx if m in oh_covered_metrics()]

    body_parts: list[str] = []
    toc_parts: list[str] = []

    def add_toc(level: int, text: str, anchor: str):
        cls = {1: "toc-h1", 2: "toc-h2", 3: "toc-h3"}[level]
        toc_parts.append(f'<a class="{cls}" href="#{anchor}">{_esc(text)}</a>')

    # ── Background + Introduction (static prose from the qmd) ─────────────────
    body_parts.append('<h1 id="background">Background and Introduction</h1>')
    add_toc(1, "Background and Introduction", "background")
    body_parts.append(
        "<p>This Stream Quantification Tool (SQT) applies the Stream Functions Pyramid "
        "Framework (SFPF) to assess stream condition and quantify functional change through "
        "restoration. This Science Support Document describes the development of the reference "
        "curves derived from the project's reference-stream dataset.</p>"
        "<p>The SFPF organizes stream function into five hierarchical categories: "
        "<strong>Hydrology</strong>, <strong>Hydraulics</strong>, <strong>Geomorphology</strong>, "
        "<strong>Physicochemistry</strong>, and <strong>Biology</strong>. Each lower category "
        "supports the ones above it. The SQT translates measurable field metrics into index "
        "values between 0 and 1, binned into three condition categories:</p>"
        "<ul><li><strong>Functioning</strong> (index &ge; 0.70)</li>"
        "<li><strong>Functioning-At-Risk</strong> (0.30 &le; index &lt; 0.70)</li>"
        "<li><strong>Not Functioning</strong> (index &lt; 0.30)</li></ul>"
    )
    body_parts.append('<h2 id="scope">Scope and Version</h2>')
    add_toc(2, "Scope and Version", "scope")
    body_parts.append(
        '<div class="sqt-section-note">This v0.1 document covers the reference curves currently '
        "supported by the reference-stream dataset: geomorphology, hydraulics, and "
        "riparian-vegetation parameters. Physicochemistry and biology are flagged as data gaps "
        "and will be developed in future revisions once suitable reference data are compiled.</div>"
    )
    if session_meta:
        # Provenance: which source computed the curve predictors. The stamp is
        # derived from the configured predictor columns, never user-chosen.
        psrc = session_meta.get("predictor_source")
        psrc_display = engine_names.predictor_source_display(psrc)
        # The scored landscape metrics an engine-sourced build recomputed.
        resourced = [str(c) for c in (session_meta.get("resourced_metrics") or [])]
        body_parts.append(
            "<p><strong>Session snapshot:</strong></p><ul>"
            f"<li><strong>Generated:</strong> {_esc(session_meta.get('generated_at') or 'Unknown')}</li>"
            f"<li><strong>Summary metrics:</strong> {_esc(session_meta.get('metric_count') or 0)}</li>"
            f"<li><strong>Curves ready:</strong> {_esc(session_meta.get('complete_metrics') or 0)}</li>"
            f"<li><strong>Needs review:</strong> {_esc(session_meta.get('review_metrics') or 0)}</li>"
            + (f"<li><strong>Predictor source:</strong> {_esc(psrc_display)}</li>" if psrc else "")
            + (f"<li><strong>Recomputed by the {_esc(engine_names.SITE_ENGINE)}:</strong> "
               f"{_esc(', '.join(resourced))}</li>" if resourced else "")
            + "</ul>"
        )

    # ── Methodology (static prose) ────────────────────────────────────────────
    body_parts.append('<h1 id="methodology">Reference Curve Development Methodology</h1>')
    add_toc(1, "Reference Curve Development Methodology", "methodology")
    body_parts.append(
        "<p>Reference curves are fit from the reference-stream dataset using the StreamCurves "
        "analytical workflow:</p><ol>"
        "<li><strong>Screen</strong> each metric for adequate sample size and data coverage "
        "across candidate stratifications.</li>"
        "<li><strong>Compare</strong> metric distributions across stratifications using "
        "Kruskal–Wallis / Wilcoxon tests with effect-size estimates.</li>"
        "<li><strong>Model</strong> each metric against allowed predictors using best-subsets "
        "regression with AICc-based model selection.</li>"
        "<li><strong>Fit</strong> a three-point linear reference curve between the 25th "
        "percentile (&asymp; 0.30 breakpoint), the 75th percentile (&asymp; 0.70 breakpoint), "
        "and the observed extreme of the reference distribution.</li>"
        "<li><strong>Convert</strong> any field measurement into an index value via the fitted "
        "linear segments, and bin it into one of the three condition categories.</li></ol>"
        "<p>Curves are adjusted for direction: for metrics where lower values indicate better "
        "condition, the mapping is inverted. When a metric's curve fails diagnostic checks it is "
        "flagged for manual review rather than published as a reference curve.</p>"
    )

    # ── Dynamic chapters per SFPF category ────────────────────────────────────
    for category in oh_category_order():
        anchor = _anchor(f"cat-{category}")
        body_parts.append(f'<h1 id="{anchor}">{_esc(category)}</h1>')
        add_toc(1, category, anchor)
        intro = _CATEGORY_INTROS.get(category)
        if intro:
            body_parts.append(f"<p>{_esc(intro)}</p>")

        all_cat_metrics = oh_metrics_for_category(category)
        if not all_cat_metrics:
            note = _CATEGORY_EMPTY_NOTES.get(category) or (
                f"No {category.lower()} parameters are developed in this SQT version. Future "
                "revisions will add reference curves as suitable data become available."
            )
            body_parts.append(f'<div class="sqt-section-note">{_esc(note)}</div>')
            continue

        parameters = list(
            dict.fromkeys(
                p for p in (oh_function_parameter(m) for m in all_cat_metrics) if p
            )
        )
        present_cat = [m for m in all_cat_metrics if m in covered]
        for param in parameters:
            p_anchor = _anchor(f"param-{category}-{param}")
            body_parts.append(f'<h2 id="{p_anchor}">{_esc(param)}</h2>')
            add_toc(2, param, p_anchor)
            metrics_in_param = [
                m for m in oh_metrics_for_parameter(category, param) if m in covered
            ]
            if not metrics_in_param:
                body_parts.append(
                    '<p class="sqt-category-empty">No metrics are available for this parameter '
                    "in the current session.</p>"
                )
                continue
            for key in metrics_in_param:
                info = _metric_info(key, metrics_ctx.get(key) or {}, metric_config)
                m_anchor = _anchor(key)
                body_parts.append(f'<h3 id="{m_anchor}">{_esc(info["display_name"])}</h3>')
                add_toc(3, info["display_name"], m_anchor)
                if info["functional_statement"]:
                    body_parts.append(
                        f'<p class="sqt-functional-statement">{_esc(info["functional_statement"])}</p>'
                    )
                bullets = [
                    f"<li><strong>Units:</strong> {_esc(info['units'] or '—')}</li>",
                    f"<li><strong>Function-Based Parameter:</strong> "
                    f"{_esc(info['category'])} / {_esc(info['parameter'])}</li>",
                ]
                if info["n_obs"] not in (None, "", "N/A"):
                    bullets.append(
                        f"<li><strong>Reference observations:</strong> {_esc(info['n_obs'])}</li>"
                    )
                bullets.append(
                    f"<li><strong>Selected stratification:</strong> {_esc(info['selected_strat_label'])}</li>"
                )
                if info["warning_summary"]:
                    bullets.append(
                        f"<li><strong>Review notes:</strong> {_esc(info['warning_summary'])}</li>"
                    )
                body_parts.append("<ul>" + "".join(bullets) + "</ul>")
                if info["plot_uri"]:
                    body_parts.append(
                        f'<img class="sqt-curve" src="{info["plot_uri"]}" '
                        f'alt="{_esc(info["display_name"])} reference curve">'
                    )
                body_parts.append(_threshold_table_html(info["threshold_table"]))

        if not present_cat and parameters:
            body_parts.append(
                '<div class="sqt-section-note">No reference curves were finalized in the current '
                "session for this category.</div>"
            )

    # ── Limitations + References (static) ─────────────────────────────────────
    body_parts.append('<h1 id="limitations">Limitations and Data Gaps</h1>')
    add_toc(1, "Limitations and Data Gaps", "limitations")
    body_parts.append(
        "<p>This v0.1 release does not yet publish reference curves for several parameters "
        "present in comparable state SQTs:</p><ul>"
        "<li><strong>Hydrology — Reach Runoff coefficient curves.</strong></li>"
        "<li><strong>Physicochemistry — Temperature, Dissolved Oxygen, TSS, Nutrients.</strong></li>"
        "<li><strong>Biology — Macroinvertebrate IBI, Fish IBI.</strong></li>"
        "<li><strong>Bed Material — percent fines.</strong></li></ul>"
        "<p>Reference-stream sample sizes are uneven across stratifications; curves should be "
        "treated as draft pending expanded reference data and steering-committee review.</p>"
    )
    body_parts.append('<h1 id="references">References</h1>')
    add_toc(1, "References", "references")
    body_parts.append(
        "<ul>"
        "<li>U.S. EPA (2012). <em>A Function-Based Framework for Stream Assessment and "
        "Restoration Projects</em>. EPA 843-K-12-006.</li>"
        "<li>Rosgen, D. L. (2014). <em>River Stability Field Guide</em> (2nd ed.).</li>"
        "<li>Harman, W. A., et al. (2012). <em>A Function-Based Framework for Stream Assessment "
        "and Restoration Projects</em>. U.S. EPA.</li>"
        "<li>Minnesota SQT Steering Committee (2022). <em>Minnesota SQT — Science Document, v1.0</em>.</li>"
        "<li>Wisconsin SQT Steering Committee (2023). <em>Wisconsin SQT — Science Support Document</em>.</li>"
        "</ul>"
    )
    generated = session_meta.get("generated_at") or ""
    body_parts.append(
        f'<hr><p class="text-muted" style="font-size:0.8rem;">Generated at {_esc(generated)}</p>'
    )

    toc_html = (
        '<nav id="TOC"><div class="toc-title">Contents</div>' + "".join(toc_parts) + "</nav>"
    )
    title_block = (
        '<div class="quarto-title-block"><h1>Stream Quantification Tool — Science Support '
        'Document</h1><div class="subtitle">v0.1 Draft</div></div>'
    )
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>SQT Science Support Document</title>"
        f"<style>{_CSS}</style></head><body>"
        f'<div class="sqt-layout">{toc_html}<main class="sqt-main">'
        f"{title_block}{''.join(body_parts)}</main></div></body></html>"
    )
