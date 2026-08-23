"""Gallery tiles grouped by discipline and function: curve_svg.assign_functions
and group_tiles (pure), the packet page's sections, and the app's section
markup from views.curve_gallery."""
from __future__ import annotations

import pandas as pd

from streamcurves import curve_svg as cs
from streamcurves import run_state as rs
from views import curve_gallery as cg

CHANNEL = "summary-curve_gallery_action"
FILTER = "summary_gallery_filter"


def _tile(metric: str, function: str | None = None, **over) -> dict:
    base = {
        "metric": metric, "display_name": metric, "function": function, "units": None,
        "in_scope": True, "needs_review": False, "review_status": rs.CURVE_STATUS_AUTO_OK,
        "decision": rs.DECISION_AUTO, "reference_range": (20.0, 80.0), "domain": (0.0, 100.0),
        "strata": [{"label": None, "points": [(10.0, 1.0), (40.0, 0.7), (70.0, 0.3), (100.0, 0.0)],
                    "curve_status": "complete", "n_reference": 23, "curve_source": "auto"}],
        "flags": [], "badge": None,
    }
    base.update(over)
    return base


# the session's discipline_function_mapping shape: one row per metric and
# function, the primary first by sort_order, planned lib: rows and blanks mixed in
MAPPING = pd.DataFrame([
    {"metric_key": "pctimp2019ws", "discipline": "Hydrology", "function_label": "Catchment hydrology", "sort_order": 1},
    {"metric_key": "pctimp2019ws", "discipline": "Hydraulics", "function_label": "High flow dynamics", "sort_order": 2},
    {"metric_key": "phab_BFWD_RAT", "discipline": "Geomorphology", "function_label": "Channel evolution", "sort_order": 3},
    {"metric_key": "phab_BFWD_RAT", "discipline": "Hydraulics", "function_label": "Floodplain connectivity", "sort_order": 4},
    {"metric_key": "phab_BFWD_RAT", "discipline": "Hydraulics", "function_label": "Low flow and baseflow dynamics", "sort_order": 5},
    {"metric_key": "bent_EPT_NTAX", "discipline": "Biology", "function_label": "Community dynamics", "sort_order": 6},
    {"metric_key": "chem_CHLA", "discipline": "Physicochemistry", "function_label": "Light and thermal regime", "sort_order": 7},
    {"metric_key": "lib:planned", "discipline": "Biology", "function_label": "Habitat provision", "sort_order": 8},
    {"metric_key": None, "discipline": "Hydrology", "function_label": "Reach inflow", "sort_order": 9},
])


def _tiles() -> list[dict]:
    return cs.assign_functions([
        _tile("pctimp2019ws", "Hydrology: Catchment hydrology"),
        _tile("phab_BFWD_RAT", "Hydraulics: Floodplain connectivity"),
        _tile("bent_EPT_NTAX", "Biology: Community dynamics", needs_review=True, decision=rs.DECISION_PENDING),
        _tile("chem_CHLA", "Physicochemistry: Light and thermal regime"),
        _tile("zzz", "Hydraulics: Hyporheic connectivity"),   # no mapping row: the label decides
        _tile("qqq", None),                                   # nothing at all: Unmapped
    ], MAPPING)


def _n_tiles(html: str) -> int:
    return html.count('class="curve-tile ') + html.count('class="curve-tile"')


def test_assign_functions_reads_every_mapping_row_primary_first():
    by = {t["metric"]: t for t in _tiles()}
    assert (by["pctimp2019ws"]["discipline"], by["pctimp2019ws"]["function_name"],
            by["pctimp2019ws"]["function_id"]) == ("Hydrology", "Catchment hydrology", "catchment-hydrology")
    assert by["pctimp2019ws"]["also_functions"] == ["High flow dynamics"]
    # the primary is the lowest sort_order, not the column_functions label; the
    # others follow in framework order
    assert by["phab_BFWD_RAT"]["function_name"] == "Channel evolution"
    assert by["phab_BFWD_RAT"]["discipline"] == "Geomorphology"
    assert by["phab_BFWD_RAT"]["also_functions"] == ["Low flow and baseflow dynamics", "Floodplain connectivity"]
    # an alias spelling resolves to the canonical name
    assert by["chem_CHLA"]["function_name"] == "Light & thermal regime"
    assert by["chem_CHLA"]["discipline"] == "Physicochemistry"
    # no row: the "Discipline: Function" label is enough
    assert (by["zzz"]["discipline"], by["zzz"]["function_id"]) == ("Hydraulics", "hyporheic-connectivity")
    assert by["zzz"]["also_functions"] == []
    # nothing: Unmapped, sorted last
    assert by["qqq"]["discipline"] == cs.UNMAPPED_DISCIPLINE and by["qqq"]["function_name"] is None
    assert by["qqq"]["function_order"] == 999
    # a planned lib: row and a blank key never become tiles' functions
    assert all("planned" not in str(t.get("function_name")) for t in by.values())


def test_resolve_function_handles_prefixes_aliases_and_unknown_text():
    assert cs.resolve_function("Physicochemistry: Water and soil quality")["id"] == "water-soil-quality"
    assert cs.resolve_function("community dynamics")["discipline"] == "Biology"
    custom = cs.resolve_function("Custom: Bank stability")
    assert custom == {"id": None, "name": "Bank stability", "discipline": "Custom", "order": 998}
    assert cs.resolve_function("")["discipline"] == cs.UNMAPPED_DISCIPLINE
    assert cs.resolve_function(float("nan"))["name"] is None


def test_group_tiles_framework_order_with_cross_listed_curves():
    sections = cs.group_tiles(_tiles())
    assert [s["discipline"] for s in sections] == [
        "Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology", cs.UNMAPPED_DISCIPLINE]
    # six primary tiles in all, three cross listings (pctimp once, BFWD_RAT twice)
    assert sum(len(f["tiles"]) for s in sections for f in s["functions"]) == 6
    assert sum(f["n_cross"] for s in sections for f in s["functions"]) == 3
    hyd = {s["discipline"]: s for s in sections}["Hydraulics"]
    # functions in framework order; a function served only from elsewhere is
    # kept, its curves drawn as cross-listed copies, and counted
    assert [f["function_name"] for f in hyd["functions"]] == [
        "Low flow and baseflow dynamics", "High flow dynamics", "Floodplain connectivity", "Hyporheic connectivity"]
    assert [f["n"] for f in hyd["functions"]] == [1, 1, 1, 1]
    assert [f["n_cross"] for f in hyd["functions"]] == [1, 1, 1, 0]
    cross = {f["function_name"]: [c["metric"] for c in f["cross"]] for f in hyd["functions"]}
    assert cross["High flow dynamics"] == ["pctimp2019ws"]
    assert cross["Floodplain connectivity"] == ["phab_BFWD_RAT"]
    assert cross["Low flow and baseflow dynamics"] == ["phab_BFWD_RAT"]
    assert hyd["functions"][0]["cross"][0]["primary_function_name"] == "Channel evolution"
    assert hyd["functions"][0]["cross"][0]["tile"]["metric"] == "phab_BFWD_RAT"
    # the section counts its distinct curves, and its cross listings
    assert hyd["n"] == 3 and hyd["n_cross"] == 3
    geo = {s["discipline"]: s for s in sections}["Geomorphology"]
    assert [t["metric"] for t in geo["functions"][0]["tiles"]] == ["phab_BFWD_RAT"]
    assert geo["n"] == 1 and geo["n_cross"] == 0
    # tiles without the keys are assigned on the way in
    bare = cs.group_tiles([_tile("a", "Biology: Habitat provision")])
    assert bare[0]["discipline"] == "Biology" and bare[0]["functions"][0]["function_name"] == "Habitat provision"


def test_gallery_html_groups_tiles_with_cross_listed_copies():
    page = cs.gallery_html(_tiles(), title="t")
    assert page.count('data-role="primary"') == 6 and page.count('data-role="cross"') == 3
    assert _n_tiles(page) == 9 and page.count("<svg ") == 9
    assert page.index("discipline-hydrology") < page.index("discipline-hydraulics") < page.index("discipline-biology")
    assert 'class="curve-gallery-section-head discipline-geomorphology"' in page
    assert "Channel evolution" in page and "High flow dynamics" in page
    # the copy links back to the primary tile and carries its own id
    assert 'href="#curve-tile-pctimp2019ws"' in page and 'id="curve-tile-pctimp2019ws"' in page
    assert 'id="curve-tile-pctimp2019ws--in-high-flow-dynamics"' in page
    assert "also under " in page and "is-cross-listed" in page
    assert "also: Low flow and baseflow dynamics, Floodplain connectivity" in page
    assert "1 cross-listed" in page and "<script" not in page


def test_gallery_ui_groups_with_cross_listed_copies_and_also_lines():
    html = str(cg.gallery_ui(_tiles(), channel_id=CHANNEL, filter_input_id=FILTER))
    html = html.replace("&apos;", "'").replace("&quot;", '"')
    # the toolbar counts distinct curves; the grid draws the copies as well
    assert "6 curves" in html
    assert html.count('data-role="primary"') == 6 and html.count('data-role="cross"') == 3
    assert _n_tiles(html) == 9
    assert 'class="curve-gallery-section-head discipline-geomorphology"' in html
    assert html.index("discipline-hydrology") < html.index("discipline-geomorphology") < html.index("discipline-biology")
    assert "also: Low flow and baseflow dynamics, Floodplain connectivity" in html
    assert "also under " in html and "cross-listed" in html
    assert 'id="curve-tile-phab_BFWD_RAT--in-low-flow-baseflow-dynamics"' in html
    assert "getElementById('curve-tile-phab_BFWD_RAT')" in html and 'id="curve-tile-phab_BFWD_RAT"' in html
    assert "curve-gallery-fn-name" in html and "Hyporheic connectivity" in html
    # a copy's back-link must not also open the tile it sits in
    assert "event.stopPropagation();var el=document.getElementById('curve-tile-phab_BFWD_RAT')" in html
    # the tile root class string is unchanged for the flagged primary tile
    assert 'class="curve-tile is-flagged"' in html


def test_gallery_ui_filter_drops_empty_sections_and_copies():
    html = str(cg.gallery_ui(_tiles(), channel_id=CHANNEL, filter_input_id=FILTER, filter_mode="flagged"))
    assert _n_tiles(html) == 1
    assert "discipline-biology" in html and "discipline-hydrology" not in html
    assert 'data-role="cross"' not in html
    empty = str(cg.gallery_ui(_tiles(), channel_id=CHANNEL, filter_input_id=FILTER, filter_mode="stratified"))
    assert _n_tiles(empty) == 0 and "No curves match this filter." in empty
    assert "curve-gallery-section" not in empty
