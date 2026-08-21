"""Generate the site walkthrough's metric reference from the scoring catalog.

Writes the section between the BEGIN/END GENERATED markers in
``docs/walkthroughs/easi/index.md`` (repo root) from
``data/screening-methods.json`` plus ``easi.config.METRIC_DEFINITIONS``, so the
public metric reference cannot drift from what the app actually scores. The
rest of the page stays hand-authored.

Deterministic: same catalog in, byte-identical section out.

    python scripts/build_walkthrough_reference.py
"""
from __future__ import annotations

import html
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

from easi import config, screening_methods as sm  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.dirname(APP)),
                    "docs", "walkthroughs", "easi", "index.md")
BEGIN = "<!-- BEGIN GENERATED METRIC REFERENCE -->"
END = "<!-- END GENERATED METRIC REFERENCE -->"

DISCIPLINES = ("Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry",
               "Biology")
CONFIDENCE_WORD = {"H": "High", "M": "Moderate", "M/L": "Moderate-low",
                   "L": "Low"}


def esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def gfp_figures(method: dict) -> str:
    blocks = sm.criteria_for(method).get("automated") or []
    figures = []
    for block in blocks:
        units = f" ({block['units']})" if block.get("units") else ""
        caption = f"{block['label']}{units}"
        segs = "".join(
            f'<div class="gfp-seg {rating.lower()}"><b>{rating}</b>'
            f'<span>{esc(block["bands"][rating])}</span></div>'
            for rating in ("Good", "Fair", "Poor")
            if block["bands"].get(rating))
        figures.append(f'<figure class="gfp"><figcaption>{esc(caption)}'
                       f'</figcaption><div class="gfp-strip">{segs}</div>'
                       f'</figure>')
    if figures:
        return ('<div class="metric-ref-sec"><div class="metric-ref-label">'
                'Good / Fair / Poor</div>\n<div class="gfp-charts">'
                + "".join(figures) + '</div>\n</div>')
    return regional_bands_section(method)


def regional_bands_section(method: dict) -> str:
    """Inputs banded by NRSA region render as a per-region table."""
    regional = [inp for inp in method.get("inputs", [])
                if inp.get("regionalBands")]
    if not regional:
        return ""
    regions = list(regional[0]["regionalBands"].keys())
    head = "".join(f"<th>{esc(inp['label'])}"
                   + (f" ({esc(inp['units'])})" if inp.get("units") else "")
                   + "</th>" for inp in regional)
    rows = []
    for region in regions:
        cells = "".join(
            "<td>{} / {}</td>".format(*inp["regionalBands"][region])
            for inp in regional)
        rows.append(f"<tr><td>{esc(region)}</td>{cells}</tr>")
    return (
        '<div class="metric-ref-sec"><div class="metric-ref-label">'
        'Good / Fair / Poor</div>'
        '<p class="metric-ref-note">Rated against the NRSA regional benchmarks '
        'for the site’s aggregate ecoregion. Each cell gives the Good '
        'boundary (at or below) and the Poor boundary (at or above). The worse '
        'available analyte governs.</p>'
        '<table class="metric-ref-table"><thead><tr><th>Region</th>'
        f"{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>")


def breakpoints_section(method: dict) -> str:
    rows = "".join(
        f"<tr><td>{esc(bp.get('label'))}</td>"
        f"<td>{esc(bp.get('description'))}</td></tr>"
        for bp in method.get("breakpoints") or [])
    if not rows:
        return ""
    return ('<div class="metric-ref-sec"><div class="metric-ref-label">'
            'Breakpoints</div><table class="metric-ref-table"><thead><tr>'
            '<th>Boundary</th><th>What it marks</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def inputs_section(method: dict) -> str:
    items = []
    for inp in method.get("inputs", []):
        flag = (' <span class="metric-ref-flag">context only</span>'
                if inp.get("contextOnly") else "")
        items.append(f"<li><b>{esc(inp['label'])}:</b>{flag} "
                     f"{esc(inp.get('rationale'))}</li>")
    if not items:
        return ""
    return ('<div class="metric-ref-sec"><div class="metric-ref-label">'
            'Input rationale</div><ul class="metric-ref-list">'
            + "".join(items) + '</ul></div>')


def hierarchy_section(method: dict) -> str:
    tiers = method.get("sourceHierarchy") or []
    if not tiers:
        return ""
    items = "".join(f"<li><b>{esc(t.get('label'))}:</b> "
                    f"{esc(t.get('description'))}</li>" for t in tiers)
    return ('<div class="metric-ref-sec"><div class="metric-ref-label">'
            'Automatic source hierarchy</div>'
            f'<ul class="metric-ref-list metric-ref-hierarchy">{items}</ul>'
            '</div>')


def limitations_section(method: dict) -> str:
    items = "".join(f"<li>{esc(x)}</li>"
                    for x in method.get("limitations") or [])
    if not items:
        return ""
    return ('<div class="metric-ref-sec"><div class="metric-ref-label">'
            'Known limitations</div><ul class="metric-ref-list">'
            + items + '</ul></div>')


def basis_section(method: dict, citations: dict) -> str:
    confidence = CONFIDENCE_WORD.get(method.get("confidence"),
                                     esc(method.get("confidence")))
    meta = (f"Basis: {esc(method.get('basisClass'))} &middot; "
            f"Data confidence: {confidence}")
    if method.get("provisional"):
        meta += " &middot; Provisional screening thresholds"
    seen = []
    for cid in method.get("citations") or []:
        if cid not in seen:
            seen.append(cid)
    links = "".join(
        f'<li><a href="{esc(citations[cid]["url"])}" target="_blank" '
        f'rel="noopener noreferrer">{esc(citations[cid]["title"])}</a></li>'
        for cid in seen if cid in citations)
    sources = (f'<ul class="metric-ref-sources">{links}</ul>' if links else "")
    return ('<div class="metric-ref-sec">\n'
            '<div class="metric-ref-label">Basis and sources</div>\n'
            f'<p class="metric-ref-meta">{meta}</p>\n'
            f'{sources}\n</div>')


def block(meta: dict, method: dict, citations: dict) -> str:
    definition = config.METRIC_DEFINITIONS.get(meta["metricId"], "")
    sections = [s for s in (
        gfp_figures(method),
        breakpoints_section(method),
        inputs_section(method),
        hierarchy_section(method),
        limitations_section(method),
        basis_section(method, citations),
    ) if s]
    return (
        '<details class="metric-ref">\n'
        f'<summary>{esc(meta["name"])}</summary>\n'
        '<div class="metric-ref-body">\n'
        f'<p class="metric-ref-fn"><span>Stream function</span> '
        f'{esc(meta.get("functionStatement"))}</p>\n'
        f'<p class="metric-ref-def">{esc(definition)}</p>\n'
        + "\n".join(sections)
        + '\n</div></details>')


def main() -> int:
    problems = sm.validate_catalog()
    if problems:
        print("catalog validation failed:", *problems, sep="\n  ")
        return 1
    catalog = config.screening_methods()
    citations = catalog.get("citations") or {}
    metrics = config.easi_metrics()["metrics"]

    parts = []
    for discipline in DISCIPLINES:
        parts.append(f"### {discipline}\n")
        for meta in [m for m in metrics if m["discipline"] == discipline]:
            method = sm.method_for(meta["metricId"])
            parts.append(block(meta, method, citations))
            parts.append("")
    generated = "\n".join(parts).rstrip() + "\n"

    text = io.open(PAGE, encoding="utf-8").read()
    if BEGIN not in text or END not in text:
        print(f"markers not found in {PAGE}")
        return 1
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    out = f"{head}{BEGIN}\n{generated}{END}{tail}"
    io.open(PAGE, "w", encoding="utf-8", newline="").write(out)
    n = generated.count('<details class="metric-ref">')
    print(f"wrote {os.path.relpath(PAGE, os.getcwd())} ({n} metric blocks)")
    return 0 if n == 20 else 1


if __name__ == "__main__":
    raise SystemExit(main())
