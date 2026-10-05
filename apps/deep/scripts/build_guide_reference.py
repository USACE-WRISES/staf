"""Generate the DEEP guide's metric reference from the published regional assessments.

Writes the section between the BEGIN/END GENERATED markers in
``docs/walkthroughs/deep/index.md`` (repo root): one block per metric of the current
regional assessments, grouped by discipline, with how it is measured (the concise
wording DEEP shows), whether DEEP fills it in from desktop data, the functions it
serves, what its curve rests on across the assessments, and, for a metric scored on
fixed criteria, the criteria and their sources. The rest of the page stays
hand-authored. The detail the Assessment page leaves out (2026-10-04) lives here and
in the report.

Deterministic: the same bundles in, a byte-identical section out.

    python scripts/build_guide_reference.py           # write the section
    python scripts/build_guide_reference.py --check   # exit 1 when the page is stale
"""
from __future__ import annotations

import collections
import html
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

from deep import assessments, config, field_form, method_text  # noqa: E402
from deep import reference_support as rs  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.dirname(APP)),
                    "docs", "walkthroughs", "deep", "index.md")
BEGIN = "<!-- BEGIN GENERATED METRIC REFERENCE -->"
END = "<!-- END GENERATED METRIC REFERENCE -->"

#: what a curve rests on, in the order the reference lists them
BASIS_ORDER = ("fixed criteria", "own reference stations", "borrowed reference stations",
               "national reference", "modeled reference", "published benchmark",
               "EASI screening method", "owner-entered", "withheld")


def esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def regional_ids() -> list[str]:
    """The regional assessments DEEP offers by default (the adapted state SQT
    bundles are hidden from the registry)."""
    cat = config.assessments_doc().get("libraryCatalog") or {}
    return sorted(aid for aid in cat if not aid.endswith("-sqt-adapted"))


def basis_word(m: dict) -> str:
    if rs.is_fixed(m) and not rs.is_ladder(m):
        return "fixed criteria"
    basis = rs.basis_of(m)
    if basis == rs.BASIS_NATIONAL:
        return "national reference"
    if basis == rs.BASIS_MODELED:
        return "modeled reference"
    if basis == rs.BASIS_PUBLISHED:
        return "published benchmark"
    if basis == rs.BASIS_OWNER:
        return "owner-entered"
    if str(m.get("basis") or "") == rs.BASIS_EASI_SCREENING:
        return "EASI screening method"
    return "borrowed reference stations" if rs.is_borrowed(m) else "own reference stations"


def common(values):
    """The most frequent non-empty value, ties broken alphabetically."""
    counts = collections.Counter(v for v in values if v)
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def collect():
    desk = field_form._desktop_ids()
    metrics: dict[str, dict] = {}
    ids = regional_ids()
    for aid in ids:
        la = assessments.load_predefined(aid)
        seen = set()
        for fn in la.metrics_by_function:
            for m in fn.get("metrics") or []:
                mid = m["metricId"]
                rec = metrics.setdefault(mid, {
                    "names": [], "units": [], "disciplines": [], "methods": [],
                    "notes": [], "functions": set(), "codes": [], "basis": collections.Counter(),
                    "fixed": None})
                rec["functions"].add(fn.get("functionName") or fn.get("functionId"))
                if mid in seen:
                    continue            # one count per assessment for a metric serving two functions
                seen.add(mid)
                rec["names"].append(m.get("metricName") or mid)
                rec["units"].append(field_form.units_of(m))
                rec["disciplines"].append(m.get("discipline") or fn.get("discipline") or "")
                rec["methods"].append(field_form.method_text(m))
                rec["notes"].append(method_text.concise(m.get("howToMeasure") or ""))
                rec["codes"].append(field_form.measure_code(m, desk))
                word = basis_word(m)
                rec["basis"][word] += 1
                if word == "fixed criteria" and rec["fixed"] is None:
                    rec["fixed"] = m.get("criteriaSource") or {}
        for w in rs.withheld(la):
            mid = w.get("metricId")
            if mid in metrics:
                metrics[mid]["basis"]["withheld"] += 1
            else:
                metrics[mid] = {"names": [w.get("metricName") or mid], "units": [w.get("units") or ""],
                                "disciplines": [""], "methods": [""], "notes": [""],
                                "functions": {str(f.get("functionName") or f.get("functionId"))
                                              for f in w.get("functions") or []},
                                "codes": [], "basis": collections.Counter(withheld=1),
                                "fixed": None}
    return metrics, len(ids)


def basis_text(basis: collections.Counter, n_regions: int) -> str:
    """What the metric's curves rest on, named but not counted, so a new library
    version changes this page only when a metric gains or loses a kind of basis."""
    kinds = [w for w in BASIS_ORDER if w != "withheld" and basis.get(w)]
    if kinds == ["fixed criteria"] and basis["fixed criteria"] == n_regions:
        return "Curve basis: the fixed criteria below, in every regional assessment."
    if not kinds:
        text = "Not scored in any regional assessment."
    elif len(kinds) == 1:
        text = f"Curve basis: {kinds[0]}."
    else:
        text = f"Curve basis, by assessment: {', '.join(kinds[:-1])} or {kinds[-1]}."
    if basis.get("withheld"):
        text += " Some assessments withhold it for insufficient reference support."
    return text


def fixed_section(src: dict) -> str:
    bands = {b.get("rating"): b.get("label") for b in src.get("bands") or []}
    segs = "".join(f'<div class="gfp-seg {r.lower()}"><b>{r}</b><span>{esc(bands[r])}</span></div>'
                   for r in ("Good", "Fair", "Poor") if bands.get(r))
    cites = "".join(f"<li>{esc(c.get('text'))}</li>" for c in src.get("citations") or []
                    if c.get("text"))
    note = (' EASI lists these criteria as provisional, so the breakpoints may be revised.'
            if src.get("provisional") else "")
    return ('<div class="metric-ref-sec"><div class="metric-ref-label">Fixed criteria, the same '
            'in every region</div>\n'
            f'<p class="metric-ref-note">DEEP scores the value on a continuous curve through '
            f'these bands (index 0.69 and 0.39 at the band edges).{note}</p>\n'
            f'<div class="gfp-charts"><figure class="gfp"><div class="gfp-strip">{segs}</div>'
            '</figure></div>\n'
            + (f'<ul class="metric-ref-sources">{cites}</ul>\n' if cites else "")
            + '</div>')


def block(rec: dict, n_regions: int) -> str:
    name = common(rec["names"])
    units = common(rec["units"])
    title = name + (f" ({units})" if units and f"({units})" not in name else "")
    method = common(rec["methods"])
    note = common(rec["notes"])
    desktop = "D" in rec["codes"]
    fns = ", ".join(sorted(f for f in rec["functions"] if f))
    parts = [f'<details class="metric-ref">\n<summary>{esc(title)}</summary>\n'
             '<div class="metric-ref-body">']
    if method:
        parts.append(f'<p class="metric-ref-def"><b>How to measure.</b> {esc(method)}</p>')
    if note and note != method and not note.startswith(name + " (") and not rec["fixed"]:
        parts.append(f'<p class="metric-ref-def"><b>What it indicates.</b> {esc(note)}</p>')
    where = ("Desktop: DEEP fills it in from desktop data, and the value stays editable."
             if desktop else "Field: measured at the site.")
    parts.append('<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div>'
                 f'<p class="metric-ref-meta">{esc(where)}</p>'
                 + (f'<p class="metric-ref-meta">Serves: {esc(fns)}</p>' if fns else "")
                 + f'<p class="metric-ref-meta">{esc(basis_text(rec["basis"], n_regions))}</p>'
                 '</div>')
    if rec["fixed"]:
        parts.append(fixed_section(rec["fixed"]))
    parts.append("</div></details>")
    return "\n".join(parts)


def generated() -> tuple[str, int]:
    metrics, n_regions = collect()
    order = {d: i for i, d in enumerate(config.CATEGORY_ORDER)}
    by_disc: dict[str, list] = collections.defaultdict(list)
    for mid, rec in metrics.items():
        by_disc[common(rec["disciplines"]) or "Other"].append((common(rec["names"]).lower(), mid))
    parts = []
    n = 0
    for disc in sorted(by_disc, key=lambda d: (order.get(d, 99), d)):
        parts.append(f"### {disc}\n")
        for _key, mid in sorted(by_disc[disc]):
            parts.append(block(metrics[mid], n_regions))
            parts.append("")
            n += 1
    return "\n".join(parts).rstrip() + "\n", n


def main(argv=None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    section, n = generated()
    text = io.open(PAGE, encoding="utf-8").read()
    if BEGIN not in text or END not in text:
        print(f"markers not found in {PAGE}")
        return 1
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    out = f"{head}{BEGIN}\n{section}{END}{tail}"
    if check:
        if out != text:
            print(f"{PAGE} is stale: run scripts/build_guide_reference.py")
            return 1
        print(f"current ({n} metric blocks)")
        return 0
    io.open(PAGE, "w", encoding="utf-8", newline="\n").write(out)
    print(f"wrote {os.path.relpath(PAGE, os.getcwd())} ({n} metric blocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
