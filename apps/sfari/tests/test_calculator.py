"""The SFARI Excel calculator: the blank, the filled copy, and the drift gates.

The template is out for Eco-PCX certification, so the tests that matter most are
the ones asserting the app does not alter it: the package comes back with the same
parts, the same order, the same compression, and only the input cells changed.

openpyxl reads the result here; the app itself never imports it (see the module
docstring in sfari/calculator.py for why a spreadsheet library cannot be used to
write this workbook).
"""
from __future__ import annotations

import datetime
import hashlib
import io
import pathlib
import re
import warnings
import zipfile

import pytest

openpyxl = pytest.importorskip("openpyxl")

from sfari import calculator, config      # noqa: E402

SHEET_PART = "xl/worksheets/sheet2.xml"
TOUCHED = {SHEET_PART, "xl/workbook.xml"}

# The draft as shipped. If this changes, the review board sent a new workbook and
# every mapping test below has to be re-read before the digest is updated.
# 2026-09-08: the E72 correction below moved this from 6d28f1f4...
TEMPLATE_SHA256 = "c5795f9e6e12160ae8a3377fbc96eebaa33a445006b835dbae1baaad17aa3b70"

#: The owner's worked example, test-only (tests/ is not in the deploy bundle).
EXAMPLE_PATH = pathlib.Path(__file__).with_name("fixtures") / \
    "SFARI_Calculator_Example_2026-06-29.xlsx"

DELIN = {"delineation": {"network": "nhdplus-hr", "nhdplus_id": 24000800011817,
                         "snapped_lat": 43.68582, "snapped_lon": -72.23667,
                         "reach_length_ft": 1000}}


def _template_sheet():
    with zipfile.ZipFile(io.BytesIO(calculator.blank_bytes())) as z:
        return openpyxl.load_workbook(io.BytesIO(calculator.blank_bytes()))["SFARI Score"]


def _build(**kw) -> bytes:
    kw.setdefault("today", datetime.date(2026, 9, 8))
    metric_scores = kw.pop("metric_scores", {})
    function_scores = kw.pop("function_scores", {})
    return calculator.build_calculator(kw.pop("delin", DELIN), metric_scores,
                                       function_scores, **kw)


def _sheet_of(data: bytes):
    return openpyxl.load_workbook(io.BytesIO(data))["SFARI Score"]


def _cells(sheet_xml: str) -> dict:
    """``{cell ref: its serialized element}``, parsed rather than pattern-matched:
    a regex over this sheet mis-associates cells around the array formulas."""
    import xml.etree.ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ET.fromstring(sheet_xml)
    return {c.get("r"): ET.tostring(c, encoding="unicode") for c in root.iter(ns + "c")}


# --------------------------------------------------------------------------- #
# the template itself
# --------------------------------------------------------------------------- #
def test_the_shipped_template_is_the_draft_we_mapped():
    assert hashlib.sha256(calculator.blank_bytes()).hexdigest() == TEMPLATE_SHA256


def test_the_blank_download_is_the_template_byte_for_byte():
    assert calculator.blank_bytes() == calculator.TEMPLATE_PATH.read_bytes()
    assert calculator.blank_filename().endswith(".xlsx")
    assert calculator.TEMPLATE_VERSION in calculator.blank_filename()


# --------------------------------------------------------------------------- #
# the cell map still describes the workbook (the gate that matters)
# --------------------------------------------------------------------------- #
def test_every_metric_has_exactly_one_row_and_the_labels_still_match():
    ws = _template_sheet()
    ids = [mid for mid, _row, _label in calculator.METRIC_ROWS]
    assert len(ids) == len(set(ids)) == 80
    assert set(ids) == {m["metricId"] for m in config.metrics()}
    rows = [row for _mid, row, _label in calculator.METRIC_ROWS]
    assert rows == list(range(8, 88))
    for _mid, row, label in calculator.METRIC_ROWS:
        assert ws.cell(row=row, column=9).value == label, f"row {row} label drifted"


def test_every_function_has_a_row_and_the_labels_still_match():
    ws = _template_sheet()
    ids = [fid for fid, _row, _label in calculator.FUNCTION_ROWS]
    assert ids == [f["id"] for f in sorted(config.functions(), key=lambda f: f["order"])]
    rows = [row for _fid, row, _label in calculator.FUNCTION_ROWS]
    assert rows == list(range(8, 88, 4))        # each score spans a 4-row merge
    for _fid, row, label in calculator.FUNCTION_ROWS:
        assert ws.cell(row=row, column=6).value == label, f"row {row} label drifted"


def test_the_likert_vocabularies_still_agree():
    """The app's labels must be the workbook's, and its numeric ladder too."""
    ws = _template_sheet()
    listed = [ws.cell(row=r, column=18).value for r in range(1, 7)]      # R1:R6
    assert listed[:5] == list(config.LIKERT_ORDER)
    assert listed[5] == calculator.NOT_APPLICABLE_LABEL
    numeric = [ws.cell(row=r, column=19).value for r in range(1, 6)]     # S1:S5
    assert numeric == [config.LIKERT_NUMERIC[k] for k in config.LIKERT_ORDER]


def test_each_category_label_tests_its_own_range():
    """The E72 correction (2026-09-08). Biology's label tested Physicochemistry's
    range for its second threshold, so a Biology average of 0.5 read as
    Functioning whenever Physicochemistry reached 0.7. Both thresholds in all
    five labels must reference that category's own four functions."""
    ws = _template_sheet()
    for row, category, expected in ((8, "Hydrology", "H8:H23"),
                                    (24, "Hydraulics", "H24:H39"),
                                    (40, "Geomorphology", "H40:H55"),
                                    (56, "Physicochemistry", "H56:H71"),
                                    (72, "Biology", "H72:H87")):
        ranges = re.findall(r"AVERAGE\((H\d+:H\d+)\)", ws.cell(row=row, column=5).value)
        assert ranges == [expected, expected], f"{category} label at E{row}"


def test_the_writer_fills_the_cells_a_human_fills():
    """Checked against the owner's own worked example rather than against our
    reading of the workbook: the cells they typed into are the cells we write.

    The two differences are both deliberate. The assessor names (G3, G4) are the
    owner's to type, since SFARI has no assessor field. The example left the
    lat/long and date blank, and we fill them because the app knows them.
    """
    import xml.etree.ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

    def valued(data: bytes) -> set:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = ET.fromstring(z.read(SHEET_PART).decode("utf-8"))
        return {c.get("r") for c in root.iter(ns + "c")
                if c.find(ns + "f") is None                       # not a formula
                and (c.find(ns + "v") is not None or c.find(ns + "is") is not None)}

    example = EXAMPLE_PATH.read_bytes()
    typed_by_hand = valued(example) - valued(calculator.blank_bytes())
    ours = ({"B2", "B3", "B4", "G2"}
            | {f"H{r}" for _f, r, _l in calculator.FUNCTION_ROWS}
            | {f"J{r}" for _m, r, _l in calculator.METRIC_ROWS})
    assert len(typed_by_hand) == len(ours) == 104
    assert typed_by_hand - ours == {"G3", "G4"}          # assessor names, out of scope
    assert ours - typed_by_hand == {"B3", "B4"}          # blank in the example


# --------------------------------------------------------------------------- #
# the package survives
# --------------------------------------------------------------------------- #
def test_only_the_two_expected_parts_change():
    out = _build()
    with zipfile.ZipFile(io.BytesIO(calculator.blank_bytes())) as src, \
            zipfile.ZipFile(io.BytesIO(out)) as got:
        assert got.testzip() is None
        assert got.namelist() == src.namelist()
        for info in src.infolist():
            other = got.getinfo(info.filename)
            assert other.compress_type == info.compress_type, info.filename
            assert other.date_time == info.date_time, info.filename
            if info.filename not in TOUCHED:
                # decompressed payload, not raw bytes: Python deflates harder
                # than Excel did, so the archive is smaller with identical content
                assert got.read(info.filename) == src.read(info.filename), info.filename


def test_the_parts_openpyxl_would_have_destroyed_are_all_present():
    with zipfile.ZipFile(io.BytesIO(_build())) as got:
        names = set(got.namelist())
    for part in ("xl/charts/chart1.xml", "xl/charts/style1.xml", "xl/charts/colors1.xml",
                 "xl/media/image1.emf", "xl/drawings/vmlDrawing1.vml",
                 "xl/comments1.xml", "xl/persons/person.xml", "xl/calcChain.xml",
                 "xl/printerSettings/printerSettings1.bin", "customXml/item1.xml",
                 "docMetadata/LabelInfo.xml"):
        assert part in names, part


def test_the_workbook_stays_protected_and_validated():
    src = openpyxl.load_workbook(io.BytesIO(calculator.blank_bytes()))
    got = openpyxl.load_workbook(io.BytesIO(_build()))
    assert got.security.workbookHashValue == src.security.workbookHashValue
    assert got["SFARI Score"].protection.sheet == src["SFARI Score"].protection.sheet
    got_dv = {(dv.type, str(dv.sqref)): str(dv.formula1)
              for dv in got["SFARI Score"].data_validations.dataValidation}
    src_dv = {(dv.type, str(dv.sqref)): str(dv.formula1)
              for dv in src["SFARI Score"].data_validations.dataValidation}
    assert got_dv == src_dv
    assert ("list", "J8:J87") in {k for k in got_dv}


def test_formulas_and_array_formulas_survive():
    ws = _sheet_of(_build())
    assert ws["A8"].value == "=AVERAGE(C8:C87)"
    assert ws["G8"].value == '=AVERAGEIF(P8:P11,"<>#N/A")'
    assert getattr(ws["P8"].value, "text", None) == "=VLOOKUP($J8:$J8,$R$1:$S$5,2,FALSE)"


def test_only_the_input_cells_differ_in_the_sheet_xml():
    """The surgical-edit guarantee: nothing but <c> elements we meant to fill."""
    with zipfile.ZipFile(io.BytesIO(calculator.blank_bytes())) as src:
        before = src.read(SHEET_PART).decode("utf-8")
    fs = {fid: {"score": 7} for fid, _r, _l in calculator.FUNCTION_ROWS}
    ms = {mid: {"likert": "Agree"} for mid, _r, _l in calculator.METRIC_ROWS}
    with zipfile.ZipFile(io.BytesIO(_build(metric_scores=ms, function_scores=fs))) as got:
        after = got.read(SHEET_PART).decode("utf-8")
    a, b = _cells(before), _cells(after)
    assert set(a) == set(b)                       # no cell added or removed
    changed = {ref for ref in a if a[ref] != b[ref]}
    expected = ({"B2", "B3", "B4", "G2"}
                | {f"H{r}" for _f, r, _l in calculator.FUNCTION_ROWS}
                | {f"J{r}" for _m, r, _l in calculator.METRIC_ROWS})
    assert changed == expected


def test_recalculation_is_forced():
    with zipfile.ZipFile(io.BytesIO(_build())) as got:
        book = got.read("xl/workbook.xml").decode("utf-8")
    calc = re.search(r"<calcPr[^>]*>", book).group(0)
    assert 'fullCalcOnLoad="1"' in calc and 'calcId="0"' in calc


def test_the_build_is_deterministic_and_does_not_poison_the_template():
    first, second = _build(), _build()
    assert first == second
    # the ZipInfo-mutation trap: the source must still be readable afterwards
    with zipfile.ZipFile(io.BytesIO(calculator.blank_bytes())) as src:
        assert src.testzip() is None


def test_no_new_openpyxl_warning_appears():
    """The template already warns once about a WMF image. Anything else is damage."""
    def warns(data):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            openpyxl.load_workbook(io.BytesIO(data))
        return {str(w.message) for w in caught}
    assert warns(_build()) == warns(calculator.blank_bytes())


# --------------------------------------------------------------------------- #
# the values
# --------------------------------------------------------------------------- #
def test_the_header_cells_carry_the_site():
    ws = _sheet_of(_build())
    assert ws["B2"].value == "NHDPlusID 24000800011817"
    assert ws["B3"].value == "43.68582, -72.23667"
    assert ws["B4"].value == datetime.datetime(2026, 9, 8)
    assert ws["B4"].number_format == "mm-dd-yy"
    assert ws["G2"].value == 1000


def test_function_scores_land_on_the_merge_anchors():
    fs = {fid: {"score": i} for i, (fid, _r, _l) in enumerate(calculator.FUNCTION_ROWS)}
    ws = _sheet_of(_build(function_scores=fs))
    for i, (_fid, row, _l) in enumerate(calculator.FUNCTION_ROWS):
        cell = ws.cell(row=row, column=8)
        assert cell.value == i and cell.data_type == "n"


def test_an_unscored_function_leaves_its_cell_empty():
    fid, row, _l = calculator.FUNCTION_ROWS[3]
    fs = {fid: {"score": None}}
    assert _sheet_of(_build(function_scores=fs)).cell(row=row, column=8).value is None


def test_likert_values_land_as_text_including_the_not_applicable_rename():
    m0, m1, m2 = calculator.METRIC_ROWS[0], calculator.METRIC_ROWS[1], calculator.METRIC_ROWS[2]
    ms = {m0[0]: {"likert": "Strongly Agree"},
          m1[0]: {"likert": config.LIKERT_NA},
          m2[0]: {"likert": None}}
    ws = _sheet_of(_build(metric_scores=ms))
    assert ws.cell(row=m0[1], column=10).value == "Strongly Agree"
    assert ws.cell(row=m0[1], column=10).data_type == "s"
    assert ws.cell(row=m1[1], column=10).value == calculator.NOT_APPLICABLE_LABEL
    assert ws.cell(row=m2[1], column=10).value is None


def test_every_written_likert_is_one_the_workbook_accepts():
    ws_src = _template_sheet()
    allowed = {ws_src.cell(row=r, column=18).value for r in range(1, 7)}
    ms = {mid: {"likert": config.LIKERT_NA if i % 6 == 5
                else config.LIKERT_ORDER[i % 5]}
          for i, (mid, _r, _l) in enumerate(calculator.METRIC_ROWS)}
    ws = _sheet_of(_build(metric_scores=ms))
    for _mid, row, _l in calculator.METRIC_ROWS:
        assert ws.cell(row=row, column=10).value in allowed


def test_a_site_with_no_reach_length_or_id_still_builds():
    ws = _sheet_of(_build(delin={"delineation": {"snapped_lat": 1.5, "snapped_lon": -2.5}}))
    assert ws["B2"].value == "1.50000, -2.50000"
    assert ws["G2"].value is None


def test_text_is_escaped_for_xml():
    dl = {"delineation": {"network": "nhdplus-hr", "snapped_lat": 1.0, "snapped_lon": 2.0,
                          "nhdplus_id": 'Cat & "Trib" <RM 1.2>\x07'}}
    ws = _sheet_of(_build(delin=dl))
    # the control character is dropped; the markup characters survive as text
    assert ws["B2"].value == 'NHDPlusID Cat & "Trib" <RM 1.2>'


def test_a_template_without_the_expected_cells_raises(monkeypatch):
    """A revised worksheet must fail loudly, never ship a silently blank column."""
    with pytest.raises(ValueError, match="expected empty cells"):
        calculator._fill_cells("<sheetData/>", {"H8": 3})


# --------------------------------------------------------------------------- #
# filenames
# --------------------------------------------------------------------------- #
def test_filenames():
    assert calculator.calculator_filename(DELIN) == \
        "sfari-calculator-nhdplusid-24000800011817.xlsx"
    assert calculator.calculator_filename({}) == "sfari-calculator.xlsx"
    assert calculator.blank_filename() == "SFARI_Calculator_Draft_2026-06-29.xlsx"


# --------------------------------------------------------------------------- #
# the report modal offers both, and a stale session still opens
# --------------------------------------------------------------------------- #
def test_the_report_modal_offers_both_downloads():
    """Source-text assertions, as elsewhere: the modal is built inside server()."""
    app = pytest.importorskip("app")
    from pathlib import Path

    src = Path(app.__file__).read_text(encoding="utf-8")
    footer = src.split('title="SFARI Report"', 1)[1].split("))", 1)[0]
    assert 'ui.download_button("dl_calc_filled"' in footer
    assert 'ui.download_button("dl_calc_blank"' in footer
    assert "def dl_calc_filled():" in src and "def dl_calc_blank():" in src
    assert "calculator.build_calculator(" in src and "calculator.blank_bytes()" in src


def test_a_session_saved_before_the_two_metrics_were_dropped_still_loads():
    """Old saves carry Likert values for metrics the app no longer scores. They
    must open, and the retired ids must simply go unread."""
    import json

    from sfari import session

    retired = "high-flow-dynamics-high-flow-velocity-shear-observed"
    kept = calculator.METRIC_ROWS[0][0]
    saved = json.dumps({"schemaVersion": 1, "delineation": {},
                        "metric_scores": {retired: {"likert": "Agree", "note": ""},
                                          kept: {"likert": "Neutral", "note": ""}},
                        "function_scores": {}, "evidence": {}})
    state = session.load(saved)
    assert state["metric_scores"][kept]["likert"] == "Neutral"
    assert retired not in {m["metricId"] for m in config.metrics()}
    # and it never reaches the worksheet, because it has no row
    assert retired not in {mid for mid, _r, _l in calculator.METRIC_ROWS}
    out = calculator.build_calculator({}, state["metric_scores"], {},
                                      today=datetime.date(2026, 9, 8))
    ws = _sheet_of(out)
    assert ws.cell(row=calculator.METRIC_ROWS[0][1], column=10).value == "Neutral"
