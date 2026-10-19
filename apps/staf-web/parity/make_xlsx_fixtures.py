"""Goldens for the static app's workbook writers (tests/xlsx.test.ts), from the tools' own Python.

- ``repack``: SFARI's calculator rewritten with every entry carried through (``calculator._repack``) and
  as ``Package(...).write()`` with nothing changed: their sha256.
- ``formulas``: the formula scanner on a corpus (every formula of SFARI's template, plus edge cases):
  spans, identifiers, a rewrite and the errors.
- ``quote``: ``quote_sheet`` on sheet names.
- ``metrics``: the Summary tab's text metrics (``_text_px``, ``_lines``) and ``page_fit``.
- ``sfari``: a three-scenario SFARI workbook built with ``sfari.workbook.build`` on a fixed day: its
  sha256 and each part's.

    .venv\\Scripts\\python apps/staf-web/parity/make_xlsx_fixtures.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE.parent / "tests" / "fixtures" / "xlsx_ref.json"
sys.path.insert(0, str(ROOT / "apps" / "sfari"))

from sfari import calculator, workbook  # noqa: E402
from sfari._vendor.staf_workbook import sheets  # noqa: E402
from sfari._vendor.staf_workbook.model.scenarios import Scenario  # noqa: E402
from sfari._vendor.staf_workbook.xlsx import formula as F  # noqa: E402
from sfari._vendor.staf_workbook.xlsx.package import Package  # noqa: E402

sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731


def formula_corpus(template: bytes) -> list[str]:
    out = []
    with zipfile.ZipFile(io.BytesIO(template)) as z:
        for name in z.namelist():
            if name.endswith(".xml") and ("worksheets/" in name or "charts/" in name or name == "xl/workbook.xml"):
                text = z.read(name).decode("utf-8")
                for tag in ("f", "c:f", "formula", "formula1", "definedName"):
                    out += re.findall(rf"<{re.escape(tag)}\b[^>]*?(?<!/)>(.*?)</{re.escape(tag)}>", text, re.S)
    from xml.sax.saxutils import unescape
    out = sorted(set(unescape(t, {"&quot;": '"', "&apos;": "'"}) for t in out if t.strip()))
    out += ["'SFARI Score'!A1+'It''s here'!B2", "\"a 'quoted' Sheet!A1\"&Score!C3", "#N/A+#DIV/0!", "SUM(Data:Other!A1)",
            "[1]Sheet!A1", "Table1[Col]", "1.5E+3*Sheet1!$A$1", "myName*2+IF(x>1,Sheet2!B$4,\"\")", "'unclosed",
            "Score!A1:B2+Score!C3", "R1C1+TRUE", "é_name!A1"]
    return out


def main() -> None:
    template = calculator.template_bytes()
    ref: dict = {}
    ref["repack"] = {"repack": sha(calculator._repack(template, {})), "package": sha(Package(template).write())}

    cases = []
    for f in formula_corpus(template):
        case = {"formula": f}
        try:
            case["spans"] = [[s.start, s.end, s.sheet, s.quoted] for s in F.sheet_spans(f)]
            case["rewrite"] = F.rewrite(f, {"sfari score": "Existing Conditions", "score": "S2 Score's", "sheet1": "Data"})
        except F.UnsupportedFormula as exc:
            case["error"] = str(exc)
        try:
            case["identifiers"] = sorted(F.identifiers(f))
        except Exception as exc:  # noqa: BLE001
            case["identifiersError"] = type(exc).__name__
        cases.append(case)
    ref["formulas"] = cases
    ref["quote"] = {n: F.quote_sheet(n) for n in ["Summary", "Existing Conditions", "A1", "R1C1", "TRUE", "x_1.y", "it's", "S2 Score", "abc123",
                                                    "ABCD1", "R", "c12", "_x", "1abc", "Données"]}

    texts = ["Reach name", "EPA Level III ecoregion", "Existing Conditions", "Restored 'riffle' & pool", "mmmWWW@%", "",
             "A long description of an alternative that wraps over several lines of the Summary tab.\nA second paragraph.",
             "Supercalifragilisticexpialidocious-and-more-words-without-spaces-to-break"]
    ref["metrics"] = {
        "textPx": [[t, sheets._text_px(t), sheets._text_px(t, True)] for t in texts],
        "lines": [[t, px, sheets._lines(t, px), sheets._lines(t, px, True)] for t in texts for px in (91.0, 168.0, 500.5)],
        "pageFit": [[w, h, list(sheets.page_fit(w, h))] for w, h in [(400, 300), (700, 400), (900, 900), (523, 705), (2000, 300),
                                                                      (720, 1500), (600, 2000), (697.3, 940.0)]],
    }

    # a three-scenario SFARI workbook on a fixed day
    delin = {"delineation": {"gnis_name": "Mink Brook", "network": "nhdplus-hr", "nhdplus_id": 1000123456789,
                             "snapped_lat": 43.68581234, "snapped_lon": -72.23671234, "reach_length_ft": 1000.5,
                             "drainage_area_sqkm": 12.3456, "stream_order": 3},
             "siteEngine": {"site": {"ecoregionL3": {"code": "59"}}}}
    ec = {"metric_scores": {"catchment-hydrology-road-density": {"likert": "Agree"},
                            "surface-water-storage-wetland-coverage": {"likert": "Not Applicable"}},
          "function_scores": {"catchment-hydrology": {"score": 11}, "surface-water-storage": {"score": 4, "note": "n"}}}
    alt = {"metric_scores": {"catchment-hydrology-road-density": {"likert": "Strongly Agree"}},
           "function_scores": {"catchment-hydrology": {"score": 14}, "reach-inflow": {"score": 9}}}
    scenarios = [(Scenario("existing", "Existing Conditions", "", ec), ec),
                 (Scenario("s2", "Alt one", "Two scores changed.", alt), alt),
                 (Scenario("s3", "Restored 'riffle' & pool", "A <test> & more", {}), {})]
    data = workbook.build(delin, scenarios, today=dt.date(2026, 10, 12))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        parts = {i.filename: sha(z.read(i.filename)) for i in z.infolist()}
    ref["sfari"] = {"delin": delin, "scenarios": [{"id": s.id, "name": s.name, "description": s.description, "entries": e} for s, e in scenarios],
                    "today": "2026-10-12", "sha256": sha(data), "size": len(data), "parts": parts}
    OUT.write_text(json.dumps(ref, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(cases)} formulas)")


if __name__ == "__main__":
    main()
