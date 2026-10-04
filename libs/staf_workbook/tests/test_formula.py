"""The sheet-reference scanner, unit cases and a sweep of every formula in the real templates
checked against openpyxl's formula tokenizer (a test-only dependency)."""
from __future__ import annotations

import re
import zipfile
from xml.sax.saxutils import unescape

import pytest

from staf_workbook.xlsx import formula as F
from templates import all_templates


@pytest.mark.parametrize("text, sheets", [
    ("='EASI Score'!$J$8", ["EASI Score"]),
    ("=SUM(Metrics!C5:C9)+Results!A1", ["Metrics", "Results"]),
    ('=IF(A1="Sheet1!B2","x",Ref!B2)', ["Ref"]),
    ("='It''s here'!A1", ["It's here"]),
    ("=#REF!+1", []),
    ("=IFERROR(A1,#N/A)", []),
    ("=_xlfn.STDEV.P(Data!A1:A9)", ["Data"]),
    ("=curve_x1+strata_m01", []),
    ("=1E+5*Sheet1!A1", ["Sheet1"]),
    ("=INDEX(Reference!$B$4:$B$40,MATCH(A1,curve_key,0))", ["Reference"]),
])
def test_spans(text, sheets):
    assert [s.sheet for s in F.sheet_spans(text)] == sheets


@pytest.mark.parametrize("text", ["=[1]Sheet1!A1", "=Sheet1:Sheet3!A1", "='Jan:Mar'!A1", "=Table1[Col]"])
def test_unsupported_constructs_are_refused(text):
    with pytest.raises(F.UnsupportedFormula):
        F.sheet_spans(text)


def test_rewrite_touches_only_the_prefixes():
    f = "=IF('EASI Score'!$J$8=\"EASI Score!x\",Metrics!C5,'EASI Score'!A1)"
    got = F.rewrite(f, {"easi score": "Existing Conditions", "metrics": "S2 Metrics"})
    assert got == "=IF('Existing Conditions'!$J$8=\"EASI Score!x\",'S2 Metrics'!C5,'Existing Conditions'!A1)"
    assert F.rewrite(f, {}) == f
    # a quoted reference stays quoted, so the way back quotes Metrics
    back = F.rewrite(got, {"existing conditions": "EASI Score", "s2 metrics": "Metrics"})
    assert back == f.replace("Metrics!C5", "'Metrics'!C5")


@pytest.mark.parametrize("name, quoted", [
    ("Metrics", "Metrics"), ("Existing Conditions", "'Existing Conditions'"), ("A1", "'A1'"),
    ("R1C1", "'R1C1'"), ("O'Neil", "'O''Neil'"), ("2024", "'2024'"), ("ChartData", "ChartData"),
])
def test_quote_sheet(name, quoted):
    assert F.quote_sheet(name) == quoted


def _formulas(path):
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.endswith(".xml"):
                continue
            xml = z.read(name).decode("utf-8", "replace")
            for tag in ("f", "formula", "formula1", "formula2", "xm:f", "c:f", "definedName"):
                for m in re.finditer(rf"<{tag}\b[^>]*?(?<!/)>(.*?)</{tag}>", xml, flags=re.S):
                    text = unescape(m.group(1), {"&quot;": '"', "&apos;": "'"})
                    if text.strip():
                        yield text


def _tokenizer_sheets(text):
    from openpyxl.formula.tokenizer import Token, Tokenizer
    sheets = set()
    tok = Tokenizer(text if text.startswith("=") else "=" + text)
    for t in tok.items:
        if t.type == Token.OPERAND and t.subtype == Token.RANGE and "!" in t.value:
            head = t.value.rsplit("!", 1)[0]
            if head.startswith("'") and head.endswith("'"):
                head = head[1:-1].replace("''", "'")
            sheets.add(head.lower())
    return sheets


def test_every_template_formula_agrees_with_openpyxl():
    pytest.importorskip("openpyxl")
    paths = all_templates()
    if not paths:
        pytest.skip("templates not present")
    n = 0
    for path in paths:
        for text in _formulas(path):
            ours = F.referenced_sheets(text)
            assert ours == _tokenizer_sheets(text), (path.name, text)
            assert F.rewrite(text, {}) == text
            n += 1
    assert n > 10_000
