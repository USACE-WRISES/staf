"""Generate the V&V document's metric-method tables from the canonical catalog.

``data/screening-methods.json`` is the single source of truth for what EASI actually
computes. Writing these tables by hand let the documentation describe a different
quantity than the code binned (the road-density metric was documented as counting
stormwater outfalls). Generating them keeps the report, the app, and the evaluator
describing one method.

Writes ``docs/EASI_Documentation/_generated/methods_<discipline>.md``, included from
``easi-vnv.qmd``. Deterministic: same catalog in, byte-identical output.

    python scripts/build_method_tables.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

from easi import config, screening_methods as sm  # noqa: E402

GEN = os.path.join(APP, "docs", "EASI_Documentation", "_generated")

DISCIPLINES = ("Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology")
_SLUG = {d: d.lower() for d in DISCIPLINES}


def _escape(text: str) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _bands_text(method: dict) -> str:
    """Good/Fair/Poor breakpoints exactly as the evaluator bands them."""
    blocks = sm.criteria_for(method).get("automated") or []
    parts = []
    for block in blocks:
        bands = block.get("bands") or {}
        rendered = "; ".join(f"{rating} {bands[rating]}"
                             for rating in ("Good", "Fair", "Poor") if bands.get(rating))
        if not rendered:
            continue
        parts.append(f"*{_escape(block['label'])}* — {_escape(rendered)}"
                     if len(blocks) > 1 else _escape(rendered))
    return "<br>".join(parts) or "Categorical; see the source hierarchy."


def _inputs_text(method: dict) -> str:
    items = []
    for inp in method.get("inputs", []):
        if inp.get("contextOnly"):
            items.append(f"{_escape(inp['label'])} (context only)")
        else:
            units = f" ({_escape(inp['units'])})" if inp.get("units") else ""
            items.append(f"{_escape(inp['label'])}{units}")
    return "; ".join(items)


def _hierarchy_text(method: dict) -> str:
    tiers = method.get("sourceHierarchy") or []
    if not tiers:
        return "Single source; no fallback."
    return "<br>".join(f"{i}. {_escape(t.get('label', t.get('methodKey', '')))} — "
                       f"{_escape(t.get('description', ''))}"
                       for i, t in enumerate(tiers, start=1))


def _table(discipline: str) -> str:
    metrics = [m for m in config.easi_metrics()["metrics"]
               if m["discipline"] == discipline]
    lines = [
        "| Metric | Attribute | Detail |",
        "|:--------|:------------------|:----------------------------------------------------|",
    ]
    for meta in metrics:
        method = sm.method_for(meta["metricId"])
        rows = [
            ("Function", _escape(meta["functionName"])),
            ("Automated method", _escape(method.get("title", ""))),
            ("Inputs", _inputs_text(method)),
            ("Equation", _escape(sm.equation_for(method))),
            ("Scoring", _bands_text(method)),
            ("Basis", f"{_escape(method.get('basisClass'))}; confidence "
                      f"{_escape(method.get('confidence'))}"
                      + ("; provisional screening transitions"
                         if method.get("provisional") else "")),
            ("Source hierarchy", _hierarchy_text(method)),
            ("Known limitations",
             "<br>".join(_escape(x) for x in method.get("limitations") or [])
             or "None recorded."),
        ]
        first = f"**{_escape(meta['name'])}**"
        for label, value in rows:
            lines.append(f"| {first} | {label} | {value} |")
            first = ""
    slug = _SLUG[discipline]
    lines.append("")
    lines.append(f": {discipline} metrics {{#tbl-metrics-{slug}}}")
    return "\n".join(lines) + "\n"


def main() -> int:
    os.makedirs(GEN, exist_ok=True)
    problems = sm.validate_catalog()
    if problems:
        print("catalog validation failed:", *problems, sep="\n  ")
        return 1
    for discipline in DISCIPLINES:
        path = os.path.join(GEN, f"methods_{_SLUG[discipline]}.md")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_table(discipline))
        print("wrote", os.path.relpath(path, APP))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
