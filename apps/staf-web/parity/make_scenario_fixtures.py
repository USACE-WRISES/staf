"""Reference markup and comparisons for the scenario controls (libs/staf_workbook web.py and
model/), as a tool inside the STAF app renders them (its module namespace on every input id).

Writes ``tests/fixtures/scenarios_ref.json``: the scenario bar for a few scenario sets; the Add, Edit,
Delete and Compare dialogs (``ui.modal`` as htmltools renders it); the comparisons SFARI builds for a
few score sets (rows, deltas, the formatted cells); and sheet-name checks. Run with the repo's .venv:

    .venv\\Scripts\\python apps/staf-web/parity/make_scenario_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "scenarios_ref.json"


def main() -> int:
    sys.path.insert(0, str(REPO / "apps" / "sfari"))
    from htmltools import TagList
    from shiny._namespaces import namespace_context

    from sfari import workbook as staf_book
    from sfari._vendor.staf_workbook import web as staf_web
    from sfari._vendor.staf_workbook.model import compare as cmp
    from sfari._vendor.staf_workbook.model.scenarios import ScenarioSet
    from sfari._vendor.staf_workbook.xlsx.names import sheet_name_problem

    def html(tag) -> str:
        return TagList(tag).get_html_string()

    out: dict = {"bars": [], "dialogs": [], "comparisons": [], "names": []}
    one = ScenarioSet()
    two = ScenarioSet()
    two.baseline.description = "As surveyed in May."
    two.add("Restored buffer", "Plant 100 ft of riparian forest.", state={})
    three = ScenarioSet()
    three.add("A & B <option>", "", state={})
    three.add("Alternative 2", "", state={})
    three.select("existing")
    full = ScenarioSet()
    for i in range(9):
        full.add(f"Alt {i}", "", state={})
    for label, sset in (("one", one), ("two", two), ("three", three), ("full", full)):
        out["bars"].append({"name": label, "set": sset.to_json(include_baseline_state=False),
                            "html": html(staf_web.scenario_bar(sset))})

    with namespace_context("sfari"):
        out["dialogs"].append({"name": "add", "set": two.to_json(), "args": {"name": "Alternative 2", "description": "", "start": "existing", "hint": "A copy brings its scores and notes; photos stay with the original. Blank starts with no scores.", "error": None},
                               "html": html(staf_web.add_dialog(two, name="Alternative 2", description="", start="existing",
                                                                 hint="A copy brings its scores and notes; photos stay with the original. Blank starts with no scores."))})
        out["dialogs"].append({"name": "add-error", "set": two.to_json(), "args": {"name": "Summary", "description": "x", "start": "blank", "hint": "", "error": "That name is already used."},
                               "html": html(staf_web.add_dialog(two, name="Summary", description="x", start="blank", hint="",
                                                                 error="That name is already used."))})
        out["dialogs"].append({"name": "edit-baseline", "args": {"title": "Describe Existing Conditions", "name": "Existing Conditions", "description": "As surveyed in May.", "name_locked": True, "can_delete": False, "error": None},
                               "html": html(staf_web.scenario_dialog(title="Describe Existing Conditions", name="Existing Conditions",
                                                                      description="As surveyed in May.", name_locked=True))})
        out["dialogs"].append({"name": "edit-alt", "args": {"title": "Rename or describe", "name": "Restored buffer", "description": "Plant 100 ft.", "name_locked": False, "can_delete": True, "error": None},
                               "html": html(staf_web.scenario_dialog(title="Rename or describe", name="Restored buffer",
                                                                      description="Plant 100 ft.", name_locked=False, can_delete=True))})
        out["dialogs"].append({"name": "delete", "args": {"name": "Restored buffer"}, "html": html(staf_web.delete_dialog("Restored buffer"))})
        sets = [{}, {"catchment-hydrology": {"score": 11}, "habitat-provision": {"score": 4, "note": "x"}},
                {"catchment-hydrology": {"score": 7}, "reach-inflow": {"score": None, "note": "y"}}]
        for names, fs in ((["Existing Conditions", "Alt"], sets[:2]), (["Existing Conditions", "Alt", "B"], [sets[1], sets[2], sets[0]])):
            c = staf_book.comparison(names, fs)
            out["comparisons"].append({
                "names": names, "functionScores": fs,
                "rows": [{"key": r.key, "label": r.label, "group": r.group, "digits": r.digits,
                          "values": [cmp.fmt_measure(v, r.digits) for v in r.values],
                          "deltas": [list(d) if d else None for d in r.deltas],
                          "deltaText": [cmp.fmt_delta(d, r.digits) for d in r.deltas],
                          "signs": [cmp.delta_sign(d) for d in r.deltas]} for r in c.rows],
                "dialog": html(staf_web.compare_dialog(c)),
                "railDelta": html(staf_web.rail_delta(c, 1)) if staf_web.rail_delta(c, 1) is not None else None})
    for name, taken in (("", []), ("  ", []), ("Alternative 1", []), ("x" * 32, []), ("a:b", []), ("'quoted", []),
                        ("History", []), ("summary", ["Summary"]), ("Alt [2]?", []), ("Existing conditions", ["Existing Conditions"])):
        out["names"].append({"name": name, "taken": taken, "problem": sheet_name_problem(name, taken)})
    for x, d in ((0.125, 2), (0.135, 2), (2.675, 2), (-0.125, 2), (1.005, 2), (0.5, 0), (1.5, 0), (2.5, 0), (-2.5, 0),
                 (0.7333333333333333, 2), (1e-7, 2)):
        out.setdefault("excelRound", []).append([x, d, cmp.excel_round(x, d)])
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"{OUT}: {len(out['bars'])} bars, {len(out['dialogs'])} dialogs, {len(out['comparisons'])} comparisons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
