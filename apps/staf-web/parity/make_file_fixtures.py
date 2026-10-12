"""Reference outputs for the pieces every download is made of, from Python's own code.

Writes ``tests/fixtures/files_ref.json``: digests (hashlib's sha256, sha1, md5; uuid5; base64) of a few
inputs; ``json.dumps`` of values read with ``json.loads`` (floats, NaN, key order) under the options the
tools use; ``csv.writer`` rows; the ``UnicodeDecodeError`` texts of bytes that are not UTF-8; the py-shiny
controls the dialogs use (``input_numeric``, ``row``/``column``, a null-label ``input_select``,
``output_ui``) and staf_workbook's shared pieces (``forms_downloads`` in a pill strip, ``report_footer``,
``new_dialog``, ``scenario_suffix``, the summary rows). Run with the repo's .venv:

    .venv\\Scripts\\python apps/staf-web/parity/make_file_fixtures.py
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "files_ref.json"

TEXTS = ["", "abc", "STAF ✓ km²", "x" * 55, "y" * 56, "z" * 64, "w" * 1000, "\x00\x01\xff"]
#: JSON texts read with json.loads, then written back with each option set
JSONS = [
    '{"a": 1.0, "b": 2, "c": [1.5, 1e5, -0.0, 0.0, 3, 1E-7, 12345678901234.0], "d": "km²"}',
    '{"5": "five", "1": "one", "4A": "x", "nested": {"10": [], "2": {}}, "z": null, "t": true, "f": false}',
    '{"nan": NaN, "inf": Infinity, "ninf": -Infinity, "big": 1e400, "tiny": 5e-324}',
    '[1, 2.5, "a\\u00e9\\ud83d\\ude00\\n\\t\\"\\\\", [], {}, [[]], {"k": [1, {"m": 2.0}]}]',
    '{"b": 1, "a": 2, "\\u00e9": 3, "Z": 4, "_": 5}',
    '{"dup": 1, "other": 2, "dup": 3.0}',
    '"just a string\\u0007"',
    '-0',
]
OPTIONS = [
    {},
    {"indent": 2},
    {"separators": [",", ":"], "ensure_ascii": False},
    {"sort_keys": True},
    {"indent": 1, "ensure_ascii": False},
    {"indent": 2, "sort_keys": True, "separators": [", ", ": "]},
]
CSV_ROWS = [
    [], [""], [None], ["", ""], ["a", "b,c", 'say "hi"', "line\nbreak", "cr\rx", " lead", "trail "],
    [1, 2.5, -0.0, 1e16, 1e-5, 3.0, True, False, None, float("nan"), float("inf")],
    ["km²", "✓", 1234567890123],
]
BAD_UTF8 = [b"\xff", b"ok\x80", b"\xe2\x82", b"\xe2\x28\xa1", b"\xf0\x9f\x98", b"a\xc0\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80",
            b"abc\xe2\x82\xac\xe2"]


def _tool_modules(key):
    """(session, assessment_file, extras) of one tool, imported from its own folder."""
    import importlib
    app = REPO / "apps" / key
    sys.path.insert(0, str(app))
    try:
        session = importlib.import_module(f"{key}.session")
        assessment_file = importlib.import_module(f"{key}._vendor.staf_workbook.assessment_file")
        extras = {}
        if key == "sfari":
            extras["comid_anchor"] = importlib.import_module("sfari.comid_anchor")
        if key == "deep":
            extras["app_helpers"] = None
        return session, assessment_file, extras
    finally:
        sys.path.remove(str(app))


def _drop_tool(key):
    for name in list(sys.modules):
        if name == key or name.startswith(key + "."):
            del sys.modules[name]


def _sessions() -> dict:
    """Each tool's Open on files it must refuse (the message), files from before the shared format (what
    it reads), the file its Save writes for fixed blocks, and its saved-site readers."""
    out: dict = {"refusals": [], "legacy": [], "dumps": [], "points": [], "deep": {}}
    sfari_file = json.dumps({"format": "staf-assessment", "formatVersion": 1, "tool": "SFARI", "savedAt": "x",
                             "delineation": {}, "toolData": {}, "scenarios": None})
    bad = [
        "not json", "[1, 2]", "true", '{"format": "other", "tool": "SFARI"}', '{"format": "staf-assessment", "tool": "X"}',
        '{"format": "staf-assessment", "tool": "SFARI"}', '{"format": "staf-assessment", "tool": "SFARI", "formatVersion": 0}',
        '{"format": "staf-assessment", "tool": "SFARI", "formatVersion": 1.0}',
        '{"format": "staf-assessment", "tool": "SFARI", "formatVersion": true}',
        '{"format": "staf-assessment", "tool": "SFARI", "formatVersion": 2}',
        '{"format": "staf-assessment", "tool": "EASI", "formatVersion": 1}', '{"format": "staf-assessment", "tool": "DEEP", "formatVersion": 1}',
        '{"format": "staf-assessment", "tool": "SFARI", "formatVersion": 1, "delineation": []}',
        '{"format": "staf-assessment", "tool": "SFARI", "formatVersion": 1, "delineation": {"siteAnchor": 5}}',
        '{"format": "staf-assessment", "tool": "SFARI", "formatVersion": 1, "toolData": "x"}',
        '{"format": "staf-assessment", "tool": "SFARI", "formatVersion": 1, "scenarios": []}',
        '{"metric_scores": {}}', '{"measured_values": {}}', '{"method": "easi"}', '{"method": " deep "}', '{}', "﻿" + sfari_file,
        '{"schemaVersion": "x", "assessment": {}}',
        '{"format": "staf-assessment", "tool": "EASI", "formatVersion": 1, "delineation": {}, "toolData": {"screening": {"report": {}}}}',
        '{"format": "staf-assessment", "tool": "EASI", "formatVersion": 1, "delineation": {}, "toolData": {"screening": {"report": {"metricRows": []}}}}',
        '{"format": "staf-assessment", "tool": "EASI", "formatVersion": 1, "delineation": {"a": 1}, "toolData": {"screening": 5}}',
    ]
    for key in ("sfari", "deep", "easi"):
        session, assessment_file, extras = _tool_modules(key)
        try:
            for text in bad + [sfari_file]:
                try:
                    session.load(text)
                    message = None
                except assessment_file.AssessmentFileError as exc:
                    message = str(exc)
                except Exception as exc:  # noqa: BLE001 - recorded as the app's catch-all would show it
                    message = f"!{type(exc).__name__}: {exc}"
                out["refusals"].append({"tool": key, "text": text, "message": message})
            scen = {"version": 1, "active": "s2", "next": 3, "items": [
                {"id": "existing", "name": "Existing Conditions", "description": "As found.", "state": {"a": 1.0, "b": [2, 3.5]}},
                {"id": "s2", "name": "Plan ✓", "description": "", "state": {"a": 2}}]}
            out["dumps"].append({"tool": key, "delineation": {"delineation": {"snapped_lat": 43.5, "reach_length_ft": 1000.0}},
                                 "toolData": {"x": [1.0, 2, None, True, "km²"]}, "scenarios": scen,
                                 "text": assessment_file.dump(key.upper(), {"delineation": {"snapped_lat": 43.5, "reach_length_ft": 1000.0}},
                                                              {"x": [1.0, 2, None, True, "km²"]}, scen, saved_at="2026-10-12T00:00:00Z")})
            out["dumps"].append({"tool": key, "delineation": None, "toolData": None, "scenarios": None,
                                 "text": assessment_file.dump(key.upper(), None, None, None, saved_at="2026-10-12T00:00:00Z")})
            if key == "sfari":
                legacy = {"schemaVersion": 1, "method": "SFARI", "delineation": {"delineation": {"comid": 5}},
                          "metric_scores": {"m": {"likert": "Agree"}}, "function_scores": {"f": {"score": 7, "na": True}},
                          "evidence": {"e": {"value": 1.0}}, "cross_section": None,
                          "scenarios": {"version": 1, "active": "s2", "next": 3, "items": [
                              {"id": "s2", "name": "Alt", "description": "d", "state": {"metric_scores": {}, "function_scores": {"g": {"score": 1, "na": False}}}}]}}
                text = json.dumps(legacy)
                out["legacy"].append({"tool": key, "text": text, "loaded": session.load(text)})
                ca = extras["comid_anchor"]
                for d in ({}, {"siteAnchor": {"scoredReach": {"comid": 9327042}}, "delineation": {"snapped_lat": 43.6, "snapped_lon": -72.2, "nhdplus_id": 1}},
                          {"ctx_inputs": {"comid": "123", "lat": 30.0, "lon": -90.0}},
                          {"delineation": {"comid": 12.0, "snapped_lat": "44.1", "snapped_lon": "-71.5"}},
                          {"delineation": {"comid": "012", "snapped_lat": 60.0, "snapped_lon": -72.0},
                           "siteAnchor": {"scoredReach": {"comid": 0}, "clickedStream": {"snapLat": 43.0, "snapLon": -72.0, "nhdplusId": 7}}}):
                    out["points"].append({"tool": key, "d": d, "anchor": ca.saved_anchor(d), "point": ca.saved_point(d)})
            if key == "deep":
                v1 = {"assessment": {"assessmentId": "x-y", "library": {"version": 3, "status": "Draft"}, "curves": [1.0, 2.5]},
                      "measured_values": {"m": {"value": 2.0, "origin": "field"}}, "delineation": {"a": 1}}
                v2 = dict(v1, schemaVersion=2, provenance={"assessmentId": "x-y", "version": 3})
                for raw in (v1, dict(v1, schemaVersion=1), v2, dict(v1, schemaVersion="2")):
                    text = json.dumps(raw)
                    out["legacy"].append({"tool": key, "text": text, "loaded": session.load(text)})
                bundle = json.loads('{"assessmentId": "a", "library": {"version": 2}, "z": 1.0, "a": [3, 4.5, "é"], "lifecycle": "Certified"}')
                out["deep"] = {
                    "bundle": json.dumps(bundle), "contentDigest": session.content_digest(bundle),
                    "bundleDigest": session.bundle_digest(bundle), "lifecycle": session.lifecycle_status(bundle),
                    "provenance": session._provenance(bundle, {"level3": {"code": "58"}, "state": None}, None, None),
                    "digestPreferred": session.bundle_digest({"library": {"digest": "sha256:abc"}}),
                    "statusLabels": [[s, session.status_label(s)] for s in ("draft", "certified", "under_review", "weird_one", "", None)],
                    "dump": session.dump({"delineation": {"snapped_lat": 1.0}}, bundle, {"version": 1, "active": "existing", "next": 2,
                                         "items": [{"id": "existing", "name": "Existing Conditions", "description": "", "state": {"measured_values": {}}}]},
                                         region={"level3": None, "state": {"code": "NH", "abbr": "NH", "name": "New Hampshire"}}),
                }
        finally:
            _drop_tool(key)
    return out


def main() -> int:
    out: dict = {}
    out["digests"] = []
    for text in TEXTS:
        data = text.encode("utf-8")
        out["digests"].append({"text": text, "sha256": hashlib.sha256(data).hexdigest(), "sha1": hashlib.sha1(data).hexdigest(),
                               "md5": hashlib.md5(data).hexdigest(), "b64": base64.b64encode(data).decode("ascii"),
                               "uuid5_url": str(uuid.uuid5(uuid.NAMESPACE_URL, text)),
                               "uuid5_dns": str(uuid.uuid5(uuid.NAMESPACE_DNS, text))})
    out["json"] = []
    for text in JSONS:
        value = json.loads(text)
        dumps = []
        for opt in OPTIONS:
            kw = dict(opt)
            if "separators" in kw:
                kw["separators"] = tuple(kw["separators"])
            dumps.append({"options": opt, "text": json.dumps(value, **kw)})
        out["json"].append({"source": text, "dumps": dumps})
    out["jsonRefused"] = ["﻿{}", "{'a': 1}", "[1,]", '{"a" 1}', "01", "[1] x", '"\x01"', '"\\x"', "nan", "-NaN", ""]
    for t in out["jsonRefused"]:
        try:
            json.loads(t)
        except ValueError:
            continue
        raise SystemExit(f"json.loads read {t!r}")
    out["csv"] = []
    for row in CSV_ROWS:
        buf = io.StringIO()
        csv.writer(buf).writerow(row)
        out["csv"].append({"row": [None if v is None else (repr(v) if isinstance(v, float) else v) for v in row],
                           "kinds": [type(v).__name__ for v in row], "text": buf.getvalue()})
    out["utf8"] = []
    for data in BAD_UTF8:
        try:
            data.decode("utf-8")
            message = None
        except UnicodeDecodeError as exc:
            message = str(exc)
        out["utf8"].append({"bytes": list(data), "message": message})

    sys.path.insert(0, str(REPO / "apps" / "sfari"))
    from htmltools import TagList
    from shiny import ui
    from shiny._namespaces import namespace_context

    from sfari._vendor.staf_workbook import web as staf_web
    from sfari._vendor.staf_workbook.model import summary as model_summary
    from sfari._vendor.staf_workbook.model.scenarios import ScenarioSet

    def html(tag) -> str:
        text = TagList(tag).tagify().get_html_string()
        # a pill strip's id is random: one stands in for it
        return re.sub(r"(data-tabsetid=\"|#tab-|id=\"tab-)\d+", r"\g<1>TABSET", text)

    controls = {}
    with namespace_context("sfari"):
        controls["numeric"] = [
            {"args": ["sfari-xs_slope", "Channel slope (m/m)", round(float(0.0012345), 5), {"min": 0.00001, "step": 0.0005}],
             "html": html(ui.input_numeric("xs_slope", "Channel slope (m/m)", value=round(float(0.0012345), 5), min=0.00001, step=0.0005))},
            {"args": ["sfari-xs_nchan", "Manning n (channel)", 0.035, {"step": 0.005}],
             "html": html(ui.input_numeric("xs_nchan", "Manning n (channel)", value=0.035, step=0.005))},
            {"args": ["sfari-xs_targetq", "Target discharge Q (cfs)", 100, {"min": 0, "step": 1}],
             "html": html(ui.input_numeric("xs_targetq", "Target discharge Q (cfs)", value=100, min=0, step=1))},
            {"args": ["sfari-xs_d50", "Bed D50 (mm)", "30.0", {"min": 0, "step": 1}],
             "html": html(ui.input_numeric("xs_d50", "Bed D50 (mm)", value=30.0, min=0, step=1))},
        ]
        controls["row"] = html(ui.row(ui.column(4, ui.div("a")), ui.column(8, ui.div("b"))))
        controls["selectNull"] = html(ui.input_select("assess_pick_0", None, {"a": "A", "b": "B & C"}, selected="b"))
        controls["pills"] = html(ui.navset_pill(
            ui.nav_panel("Field forms", ui.div("x"), value="ff"), ui.nav_panel("Desktop", "y"), ui.nav_spacer(),
            *staf_web.forms_downloads(("dl_metrics", "Desktop metrics PDF"), field_forms="dl_forms", workbooks=("dl_wb", "dl_blank")),
            id="ff_tabs"))
        controls["pillsNoWorkbook"] = html(ui.navset_pill(
            ui.nav_panel("Metrics", ui.div("x")), ui.nav_spacer(),
            *staf_web.forms_downloads(("dl_metrics", "Desktop metrics PDF", "The metrics list")), id="ff_tabs"))
        controls["footer"] = html(staf_web.report_footer("dl_pdf", "dl_csv", "dl_geojson", workbook="dl_workbook"))
        controls["footerNoWorkbook"] = html(staf_web.report_footer("dl_pdf", "dl_csv", "dl_geojson"))
        controls["newDialog"] = html(staf_web.new_dialog("the delineation and every score, note and photo"))
    out["controls"] = controls

    suffixes = []
    for name in ("Alternative 1", "Restored  buffer!!", "---", "Ünïcode Plan"):
        s = ScenarioSet()
        s.add(name, "", state={})
        suffixes.append({"name": name, "id": s.active, "suffix": staf_web.scenario_suffix(s)})
    suffixes.append({"name": None, "id": "existing", "suffix": staf_web.scenario_suffix(ScenarioSet())})
    out["scenarioSuffix"] = suffixes

    summaries = []
    for app, kw in (("SFARI", {"reach_name": " Mink Brook ", "reach_length_ft": 1000.0, "drainage_area_sqkm": 12.345, "stream_order": 3,
                               "regions": model_summary.regions_for_l3("58")}),
                    ("DEEP", {"reach_name": None, "reach_length_ft": None, "drainage_area_sqkm": 1234.5, "stream_order": 0,
                              "regions": model_summary.regions_for_l3(66.0)}),
                    ("EASI", {"reach_name": "", "reach_length_ft": "2500", "drainage_area_sqkm": float("nan"), "stream_order": 2.7,
                              "regions": model_summary.regions_for_l3("999")})):
        info = model_summary.SummaryInfo(app, **kw)
        summaries.append({"app": app, "info": {"reachName": kw["reach_name"], "reachLengthFt": kw["reach_length_ft"],
                                               "drainageAreaSqkm": None if kw["drainage_area_sqkm"] != kw["drainage_area_sqkm"] else kw["drainage_area_sqkm"],
                                               "nan": kw["drainage_area_sqkm"] != kw["drainage_area_sqkm"], "streamOrder": kw["stream_order"],
                                               "regions": kw["regions"]},
                          "rows": [list(r) for r in info.rows()]})
    out["summaries"] = summaries
    out["regions"] = [{"l3": code, "regions": model_summary.regions_for_l3(code)} for code in ("58", "66.0", " 8 ", None, "", "999")]

    out["sessions"] = _sessions()
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
