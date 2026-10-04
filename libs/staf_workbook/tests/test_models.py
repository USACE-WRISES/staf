"""Scenarios, comparisons, the summary block and the ecoregion table."""
from __future__ import annotations

import json

import pytest

from staf_workbook.model import compare as C
from staf_workbook.model.scenarios import BASELINE_ID, BASELINE_NAME, MAX_SCENARIOS, ScenarioSet
from staf_workbook.model.summary import DATA, SummaryInfo, regions_for_l3


def test_scenarios_add_copy_rename_delete():
    s = ScenarioSet()
    s.baseline.state = {"m1": 3}
    alt = s.add()
    assert alt.name == "Alternative 1" and s.active == alt.id and alt.state == {"m1": 3}
    alt.state["m1"] = 9
    assert s.baseline.state == {"m1": 3}                         # a copy, not a shared object
    s.rename(alt.id, "Restore riparian")
    with pytest.raises(ValueError):
        s.rename(BASELINE_ID, "Today")                           # the baseline keeps its name
    with pytest.raises(ValueError):
        s.add("Restore riparian")                                # names are unique
    with pytest.raises(ValueError):
        s.add("Summary")                                         # reserved by the workbook
    with pytest.raises(ValueError):
        s.add("a/b")                                             # Excel's forbidden characters
    s.describe(BASELINE_ID, "Before the project")
    s.delete(alt.id)
    assert s.active == BASELINE_ID and len(s.items) == 1
    with pytest.raises(ValueError):
        s.delete(BASELINE_ID)


def test_scenario_cap_and_ids_never_reused():
    s = ScenarioSet()
    ids = [s.add().id for _ in range(MAX_SCENARIOS - 1)]
    assert not s.can_add()
    with pytest.raises(ValueError):
        s.add()
    s.delete(ids[-1])
    assert s.add().id not in ids


def test_scenarios_round_trip_and_tolerate_bad_input():
    s = ScenarioSet()
    s.baseline.state = {"a": 1}
    alt = s.add("Dam removal", "Take out the weir")
    alt.state = {"a": 2}
    s.describe(BASELINE_ID, "Today")
    data = json.loads(json.dumps(s.to_json()))
    assert "state" not in data["items"][0]                       # the baseline lives at top level
    back = ScenarioSet.from_json(data, baseline_state={"a": 1})
    assert [x.name for x in back.items] == [BASELINE_NAME, "Dam removal"]
    assert back.baseline.description == "Today" and back.get(alt.id).state == {"a": 2}
    assert back.active == alt.id and back.next_number == s.next_number
    for junk in (None, [], {"items": "x"}, {"items": [{"id": "s2", "name": ""}, 5]}):
        assert [x.id for x in ScenarioSet.from_json(junk).items] == [BASELINE_ID]


def test_comparison_points_and_intervals():
    scores = [C.ScenarioScores(C.Measure(0.524), {"physical": C.Measure(0.6)}, {"f1": C.Measure(9)}),
              C.ScenarioScores(C.Measure(0.586), {"physical": C.Measure(0.5)}, {"f1": C.Measure(11)}),
              C.ScenarioScores(C.Measure(None, 0.40, 0.70), {}, {})]
    cmp = C.build(["Existing Conditions", "A", "B"], scores, [("f1", "Catchment hydrology", "Hydrology")])
    eci = cmp.rows[0]
    assert eci.deltas[0] == (0.07, 0.07)                         # 0.59 - 0.52 as displayed
    assert eci.deltas[1] == (round(0.40 - 0.52, 2), round(0.70 - 0.52, 2))
    assert C.fmt_delta(eci.deltas[0], 2) == "+0.07"
    assert C.fmt_delta(eci.deltas[1], 2) == "-0.12 to +0.18"
    assert C.fmt_measure(scores[2].eci, 2) == "0.40 to 0.70"
    assert C.delta_sign(eci.deltas[0]) == 1 and C.delta_sign(eci.deltas[1]) == 0
    phys = cmp.rows[1]
    assert C.fmt_delta(phys.deltas[0], 2) == "-0.10" and phys.deltas[1] is None
    assert cmp.rows[-1].label == "Catchment hydrology" and cmp.rows[-1].deltas[0] == (2, 2)


def test_excel_round_is_half_away_from_zero():
    assert C.excel_round(0.125, 2) == 0.13 and C.excel_round(-0.125, 2) == -0.13 and C.excel_round(2.5, 0) == 3


def test_summary_rows_and_regions():
    regions = regions_for_l3("59")
    assert regions["l3_name"] == "Northeastern Coastal Zone" and regions["l1_name"] == "Eastern Temperate Forests"
    assert regions["nars9"] == "NAP" and regions["nars9_name"] == "Northern Appalachians"
    rows = dict(SummaryInfo("SFARI", "Mink Brook", 1000, 12.3456, 3, regions).rows())
    assert rows["Assessment tier"] == "Rapid" and rows["Reach length"] == "1,000 ft"
    assert rows["Drainage area"] == "12.35 km²" and rows["Stream order (Strahler)"] == "3"
    assert rows["EPA Level III ecoregion"] == "Northeastern Coastal Zone (59)"
    empty = dict(SummaryInfo("DEEP").rows())
    assert empty["Reach name"] == "Unnamed" and empty["NARS-9 region"] == "Not available"


def test_ecoregion_table_covers_every_level_iii_code():
    doc = json.loads((DATA / "ecoregions.json").read_text(encoding="utf-8"))
    assert len(doc["l3"]) == 85 and len(doc["sources"]) == 3
    assert all(v.get("nars9") and v.get("l1") and v.get("l2_name") for v in doc["l3"].values())
    assert min(v["nars9_share"] for v in doc["l3"].values()) > 0.97
