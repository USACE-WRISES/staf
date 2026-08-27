"""The NRSA explorer's pure logic.

Fixture-driven so it runs without the archive, plus a few checks against the real
station table when it has been built.
"""
from __future__ import annotations

import pandas as pd
import pytest

from streamcurves import nrsa_dataset as nd
from streamcurves import nrsa_explorer as ex
from views import nrsa_explorer as vx

MULTI = pytest.mark.skipif(
    not nd.multi_cycle_available(),
    reason="multi-cycle archive not built (scripts/nrsa/build_values_table.py)",
)


@pytest.fixture
def stations() -> pd.DataFrame:
    return pd.DataFrame([
        {"station_key": "A", "lat": 38.0, "lon": -85.0, "comid": 1, "us_l3code": "71",
         "us_l3name": "Interior Plateau", "state": "Kentucky", "cycles_sampled": "1314,1819,2324",
         "n_cycles": 3, "n_visits": 4},
        {"station_key": "B", "lat": 38.1, "lon": -85.1, "comid": 2, "us_l3code": "71",
         "us_l3name": "Interior Plateau", "state": "Kentucky", "cycles_sampled": "2324",
         "n_cycles": 1, "n_visits": 1},
        {"station_key": "C", "lat": 44.0, "lon": -70.0, "comid": None, "us_l3code": "58",
         "us_l3name": "Northeastern Highlands", "state": "Maine", "cycles_sampled": "1314",
         "n_cycles": 1, "n_visits": 1},
        {"station_key": "D", "lat": None, "lon": None, "comid": 4, "us_l3code": "58",
         "us_l3name": "Northeastern Highlands", "state": "Maine", "cycles_sampled": "1819,2324",
         "n_cycles": 2, "n_visits": 2},
    ])


@pytest.fixture
def visits() -> pd.DataFrame:
    return pd.DataFrame([
        {"station_key": "A", "cycle": "1314", "site_id": "OLD-1", "visit_no": "1",
         "year": 2014, "date_col": "6/1/2014"},
        {"station_key": "A", "cycle": "1819", "site_id": "NRS18_KY_1", "visit_no": "1",
         "year": 2018, "date_col": "6/1/2018"},
        {"station_key": "A", "cycle": "1819", "site_id": "NRS18_KY_1", "visit_no": "2",
         "year": 2018, "date_col": "8/1/2018"},
        {"station_key": "A", "cycle": "2324", "site_id": "NRS23_KY_1", "visit_no": "1",
         "year": 2023, "date_col": "7/1/2023"},
        {"station_key": "B", "cycle": "2324", "site_id": "NRS23_KY_2", "visit_no": "1",
         "year": 2024, "date_col": "7/1/2024"},
    ])


@pytest.fixture
def values() -> pd.DataFrame:
    """Four real metric keys so the dictionary supplies real names and categories:
    one physical habitat, one chemistry, one landscape (which never varies by
    cycle), and one benthic that this station was never scored for."""
    return pd.DataFrame([
        {"station_key": "A", "cycle": "1314", "visit_no": "1", "phab_XEMBED": 25.0,
         "chem_PTL": 12.0, "land_BFIWS": 55.0, "bent_EPT_NTAX": None},
        {"station_key": "A", "cycle": "1819", "visit_no": "1", "phab_XEMBED": 35.0,
         "chem_PTL": 14.0, "land_BFIWS": 55.0, "bent_EPT_NTAX": None},
        {"station_key": "A", "cycle": "2324", "visit_no": "1", "phab_XEMBED": 43.0,
         "chem_PTL": 9.0, "land_BFIWS": 55.0, "bent_EPT_NTAX": None},
        {"station_key": "B", "cycle": "2324", "visit_no": "1", "phab_XEMBED": None,
         "chem_PTL": None, "land_BFIWS": None, "bent_EPT_NTAX": None},
    ])


# --------------------------------------------------------------------------- #
# filtering
# --------------------------------------------------------------------------- #

def test_filtering_by_ecoregion_and_state(stations):
    assert set(ex.filter_stations(stations, ecoregion="71")["station_key"]) == {"A", "B"}
    assert set(ex.filter_stations(stations, state="Maine")["station_key"]) == {"C", "D"}
    assert ex.filter_stations(stations, ecoregion="99").empty


def test_any_keeps_a_station_sampled_in_one_of_the_cycles(stations):
    got = ex.filter_stations(stations, cycles=["2324"], match="any")
    assert set(got["station_key"]) == {"A", "B", "D"}


def test_all_keeps_only_stations_with_the_whole_time_series(stations):
    got = ex.filter_stations(stations, cycles=["1314", "1819", "2324"], match="all")
    assert set(got["station_key"]) == {"A"}
    both_new = ex.filter_stations(stations, cycles=["1819", "2324"], match="all")
    assert set(both_new["station_key"]) == {"A", "D"}


def test_selecting_every_cycle_is_a_no_op_for_any_but_not_for_all(stations):
    """The trap: short-circuiting on 'all cycles selected' silently disables the
    'all' filter, which is exactly the query worth asking."""
    everything = ex.filter_stations(stations, cycles=["1314", "1819", "2324"], match="any")
    assert len(everything) == len(stations)
    only_full = ex.filter_stations(stations, cycles=["1314", "1819", "2324"], match="all")
    assert len(only_full) == 1


def test_filters_compose(stations):
    got = ex.filter_stations(stations, ecoregion="71", cycles=["1314"], match="any")
    assert set(got["station_key"]) == {"A"}


def test_filtering_an_empty_table_is_not_an_error():
    assert ex.filter_stations(pd.DataFrame()).empty
    assert ex.filter_stations(None).empty


# --------------------------------------------------------------------------- #
# map payload and legend
# --------------------------------------------------------------------------- #

def test_geojson_skips_stations_without_coordinates(stations):
    payload = ex.station_geojson(stations)
    assert payload["type"] == "FeatureCollection"
    keys = [f["properties"]["station_key"] for f in payload["features"]]
    assert keys == ["A", "B", "C"]          # D has no lat/lon
    first = payload["features"][0]
    assert first["geometry"]["coordinates"] == [-85.0, 38.0]   # lon, lat order
    assert first["properties"]["style"]["color"] == ex.cycle_set_color("1314,1819,2324")


def test_each_cycle_set_gets_its_own_colour(stations):
    colors = {ex.cycle_set_color(c) for c in stations["cycles_sampled"]}
    assert len(colors) == len(set(stations["cycles_sampled"]))
    assert ex.cycle_set_color("") == ex.UNKNOWN_COLOR
    assert ex.cycle_set_color(None) == ex.UNKNOWN_COLOR
    # order within the set does not change the colour
    assert ex.cycle_set_color("2324,1819") == ex.cycle_set_color("1819,2324")


def test_cycle_labels_read_as_survey_years(stations):
    assert ex.cycle_set_label("1819,2324") == "NRSA 2018-19, NRSA 2023-24"
    assert ex.cycle_set_label("") == "unknown"


def test_the_legend_counts_every_station(stations):
    rows = ex.legend_rows(stations)
    assert sum(r["n"] for r in rows) == len(stations)
    assert [r["n"] for r in rows] == sorted([r["n"] for r in rows], reverse=True)


def test_ecoregion_choices_are_labelled_and_counted(stations):
    choices = ex.ecoregion_choices(stations)
    assert choices["71"] == "71 Interior Plateau (2)"
    assert set(choices) == {"71", "58"}


def test_ecoregion_choices_are_ordered_by_number_not_by_string():
    """us_l3code is a string column holding numeric codes 1 to 85, so a plain
    string sort reads 10 before 2. The count is deliberately inverted against
    the code order here, so the old most-stations-first sort fails too."""
    frame = pd.DataFrame(
        [{"us_l3code": "85", "us_l3name": "Eighty-five"}] * 9
        + [{"us_l3code": "10", "us_l3name": "Ten"}] * 4
        + [{"us_l3code": "9", "us_l3name": "Nine"}] * 2
        + [{"us_l3code": "2", "us_l3name": "Two"}]
    )
    choices = ex.ecoregion_choices(frame)
    assert list(choices) == ["2", "9", "10", "85"]
    assert choices["2"] == "2 Two (1)"          # the label keeps its count
    assert choices["85"] == "85 Eighty-five (9)"


def test_an_ecoregion_code_that_is_not_a_number_sorts_last():
    frame = pd.DataFrame([
        {"us_l3code": "7", "us_l3name": "Seven"},
        {"us_l3code": "XX", "us_l3name": "Unknown"},
        {"us_l3code": "3", "us_l3name": "Three"},
    ])
    assert list(ex.ecoregion_choices(frame)) == ["3", "7", "XX"]


def test_coverage_summary_reports_what_the_header_shows(stations):
    summary = ex.coverage_summary(stations)
    assert summary == {"stations": 4, "with_comid": 3, "multi_cycle": 2, "ecoregions": 2}
    assert ex.coverage_summary(pd.DataFrame())["stations"] == 0


# --------------------------------------------------------------------------- #
# one station
# --------------------------------------------------------------------------- #

def test_station_detail_lists_every_visit_and_the_metric_by_cycle(stations, visits, values):
    detail = ex.station_detail("A", stations=stations, visits=visits, values=values,
                               metrics=["phab_XEMBED"])
    assert detail["station_key"] == "A"
    assert detail["n_cycles"] == 3
    assert [v["cycle"] for v in detail["visits"]] == ["1314", "1819", "1819", "2324"]
    # the site id changes every cycle, which is the whole reason stations exist
    assert len({v["site_id"] for v in detail["visits"]}) == 3
    metric = detail["metrics"][0]
    assert metric["name"] == "Embeddedness"
    assert metric["units"] == "%"
    assert metric["by_cycle"] == {"1314": 25.0, "1819": 35.0, "2324": 43.0}


def test_station_detail_omits_a_metric_with_no_value(stations, visits, values):
    detail = ex.station_detail("B", stations=stations, visits=visits, values=values,
                               metrics=["phab_XEMBED"])
    assert detail["metrics"][0]["by_cycle"] == {}


def test_station_detail_for_an_unknown_key_is_empty(stations, visits):
    assert ex.station_detail("nope", stations=stations, visits=visits) == {}


# --------------------------------------------------------------------------- #
# every metric, grouped by category
# --------------------------------------------------------------------------- #

def _by_category(groups) -> dict[str, dict]:
    return {g["category"]: g for g in groups}


def test_groups_follow_the_declared_category_order(values):
    groups = ex.station_metric_groups("A", values=values)
    assert [g["category"] for g in groups] == [
        "Water chemistry", "Physical habitat", "Landscape"]
    # measured first, watershed attributes last
    assert groups[-1]["category"] in ex.STATIC_CATEGORIES


def test_a_metric_with_no_value_is_absent_and_the_count_matches(values):
    groups = ex.station_metric_groups("A", values=values)
    # bent_EPT_NTAX is null in every cycle, so Benthic never appears at all
    assert "Benthic macroinvertebrates" not in _by_category(groups)
    for group in groups:
        assert group["n"] == len(group["metrics"])
    assert sum(g["n"] for g in groups) == 3


def test_a_measured_category_carries_a_value_per_cycle(values):
    habitat = _by_category(ex.station_metric_groups("A", values=values))["Physical habitat"]
    assert habitat["varies_by_cycle"] is True
    metric = habitat["metrics"][0]
    assert metric["metric"] == "phab_XEMBED"
    assert metric["name"] == "Embeddedness"
    assert metric["units"] == "%"
    assert metric["by_cycle"] == {"1314": 25.0, "1819": 35.0, "2324": 43.0}
    assert metric["value"] is None


def test_a_landscape_metric_is_shown_once_not_per_cycle(values):
    """It is a watershed attribute keyed to the flowline, identical in every
    cycle, so a per-cycle row would imply a change that never happened."""
    land = _by_category(ex.station_metric_groups("A", values=values))["Landscape"]
    assert land["varies_by_cycle"] is False
    metric = land["metrics"][0]
    assert metric["metric"] == "land_BFIWS"
    assert metric["value"] == 55.0
    assert metric["by_cycle"] == {}


def test_search_matches_the_name_or_the_code_case_insensitively(values):
    by_name = ex.station_metric_groups("A", values=values, search="EMBED")
    by_code = ex.station_metric_groups("A", values=values, search="xembed")
    for groups in (by_name, by_code):
        assert [g["category"] for g in groups] == ["Physical habitat"]
        assert [m["metric"] for m in groups[0]["metrics"]] == ["phab_XEMBED"]
        assert groups[0]["n"] == 1


def test_a_search_that_matches_nothing_returns_no_groups(values):
    """Not a group with zero metrics: the view would draw an empty section."""
    assert ex.station_metric_groups("A", values=values, search="zzzz") == []


def test_a_cycle_with_several_visits_reports_its_first_non_null_value(stations, visits):
    """Both halves of the rule the one-pass slice has to preserve: a null first
    visit does not win, and a later differing value does not overwrite an
    earlier real one."""
    frame = pd.DataFrame([
        {"station_key": "A", "cycle": "1819", "visit_no": "1", "phab_XEMBED": None},
        {"station_key": "A", "cycle": "1819", "visit_no": "2", "phab_XEMBED": 31.0},
        {"station_key": "A", "cycle": "2324", "visit_no": "1", "phab_XEMBED": 12.0},
        {"station_key": "A", "cycle": "2324", "visit_no": "2", "phab_XEMBED": 99.0},
    ])
    groups = ex.station_metric_groups("A", values=frame)
    assert groups[0]["metrics"][0]["by_cycle"] == {"1819": 31.0, "2324": 12.0}
    # station_detail reads the same slice, so it must agree
    detail = ex.station_detail("A", stations=stations, visits=visits, values=frame,
                               metrics=["phab_XEMBED"])
    assert detail["metrics"][0]["by_cycle"] == {"1819": 31.0, "2324": 12.0}


def test_grouping_does_not_re_scan_the_rows_for_every_column(monkeypatch):
    """Clicking a station froze the app for 40 seconds because the row scan sat
    inside the loop over metric columns, rebuilding a namedtuple as wide as the
    whole 792-column table once per column.

    A wall-clock assertion would be flaky, so this pins the invariant instead:
    the row scan must not scale with the number of columns. The old shape calls
    itertuples once per column, so it fails this by 700 calls.
    """
    def wide(n_columns: int) -> pd.DataFrame:
        return pd.DataFrame([{
            "station_key": "A", "cycle": "1819", "visit_no": "1",
            **{f"phab_M{i}": float(i) for i in range(n_columns)},
        }])

    # Warm the metric dictionary first. Its one-time lazy load scans a frame of
    # its own, and with pytest-randomly deciding the order it would otherwise
    # land inside whichever measurement happens to run first and count as a
    # phantom extra scan.
    ex.station_metric_groups("A", values=wide(4))

    calls: list[int] = []
    real = pd.DataFrame.itertuples

    def counting(self, *args, **kwargs):
        calls.append(self.shape[1])
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "itertuples", counting)

    def scans_for(n_columns: int) -> int:
        calls.clear()
        groups = ex.station_metric_groups("A", values=wide(n_columns))
        # guards the count from passing on a function that does nothing
        assert sum(g["n"] for g in groups) == n_columns
        return len(calls)

    assert scans_for(800) == scans_for(100) <= 1


def test_grouping_an_unknown_station_or_no_values_is_empty(values):
    assert ex.station_metric_groups("nope", values=values) == []
    assert ex.station_metric_groups("A", values=None) == []
    assert ex.station_metric_groups("A", values=pd.DataFrame()) == []
    # B is a real station whose every value is null
    assert ex.station_metric_groups("B", values=values) == []


# --------------------------------------------------------------------------- #
# the panel markup
#
# These live at module level in views/nrsa_explorer.py rather than inside the
# server closure precisely so this file can reach them: the first cut kept them
# in the closure and an accordion_panel arity mistake ("If `title` is not a
# string, `value` must be provided") shipped past the whole suite, surfacing only
# on a live click in the app.
# --------------------------------------------------------------------------- #

def _cycle_group() -> dict:
    return {"category": "Physical habitat", "n": 2, "varies_by_cycle": True,
            "metrics": [
                {"metric": "phab_XEMBED", "name": "Embeddedness", "units": "%",
                 "description": "How buried the substrate is.",
                 "by_cycle": {"1314": 25.0, "1819": 35.0}, "value": None},
                {"metric": "phab_SINU", "name": "Sinuosity", "units": "",
                 "description": "", "by_cycle": {"1819": 1.4}, "value": None},
            ]}


def _static_group() -> dict:
    return {"category": "Landscape", "n": 1, "varies_by_cycle": False,
            "metrics": [{"metric": "land_BFIWS", "name": "Base-flow index",
                         "units": "%", "description": "",
                         "by_cycle": {}, "value": 55.0}]}


def test_the_section_renders_a_collapsed_panel_per_category():
    html = str(vx.metric_groups_ui([_cycle_group(), _static_group()], ["1314", "1819"]))
    assert "All metrics by category" in html
    assert "Physical habitat" in html and "Landscape" in html
    # both closed, so opening 300 landscape rows is a choice the reader makes
    assert html.count("accordion-button collapsed") == 2
    assert "3 metrics with a value for this station." in html


def test_a_panel_header_carries_its_count():
    html = str(vx.metric_groups_ui([_cycle_group()], ["1314", "1819"]))
    assert 'class="metric-count-badge">2<' in html


def test_a_per_cycle_group_gets_one_column_per_cycle():
    html = str(vx.metric_group_table(_cycle_group(), ["1314", "1819"]))
    assert "<th>NRSA 2013-14</th>" in html and "<th>NRSA 2018-19</th>" in html
    assert "<th>Value</th>" not in html
    # a cycle the metric was not measured in is blank, not a repeat
    assert html.count("<td></td>") == 1


def test_a_static_group_gets_one_value_column_and_says_why():
    html = str(vx.metric_groups_ui([_static_group()], ["1314", "1819"]))
    assert "<th>Value</th>" in html
    assert "<th>NRSA 2013-14</th>" not in html
    assert "Watershed attributes from the flowline" in html


def test_the_name_cell_shows_units_and_the_code():
    html = str(vx.metric_name_cell(_cycle_group()["metrics"][0]))
    assert "Embeddedness" in html
    assert "(%)" in html
    assert "<code>phab_XEMBED</code>" in html
    assert "How buried the substrate is." in html      # the hover description
    # a metric with no units renders no empty parenthesis
    assert "()" not in str(vx.metric_name_cell(_cycle_group()["metrics"][1]))


def test_no_groups_explains_itself_differently_with_and_without_a_search():
    searched = str(vx.metric_groups_ui([], ["1819"], search="  zzz "))
    assert 'No metric name or code matches "zzz".' in searched
    assert "accordion" not in searched
    bare = str(vx.metric_groups_ui([], ["1819"]))
    assert "holds no metric values for this station" in bare


@MULTI
def test_the_panel_renders_for_a_real_station():
    """The end-to-end shape, which is what the closure hid."""
    ds = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID)
    groups = ex.station_metric_groups("GARO-1032", values=ds.values)
    html = str(vx.metric_groups_ui(groups, ["1314", "1819"]))
    assert "760 metrics with a value for this station." in html
    assert html.count("accordion-button collapsed") == len(groups) == 5
    assert "<th>Value</th>" in html            # Landscape
    assert "<th>NRSA 2013-14</th>" in html     # the measured categories


@MULTI
def test_a_real_station_groups_every_metric_it_has():
    ds = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID)
    groups = ex.station_metric_groups("GARO-1032", values=ds.values)
    counts = {g["category"]: g["n"] for g in groups}
    # 760 of the 788 metric columns carry a value for this station
    assert sum(counts.values()) == 760
    assert counts["Landscape"] == 303
    assert _by_category(groups)["Landscape"]["varies_by_cycle"] is False
    # a name is never the bare code, and never the truncated tile-header form
    for group in groups:
        for metric in group["metrics"]:
            assert metric["name"] != metric["metric"]
            assert not str(metric["name"]).endswith("...")


# --------------------------------------------------------------------------- #
# against the real archive
# --------------------------------------------------------------------------- #

@MULTI
def test_the_real_table_has_the_expected_shape():
    ds = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID)
    summary = ex.coverage_summary(ds.stations)
    assert summary["stations"] == 4378
    assert summary["ecoregions"] > 80
    # only eleven places were sampled in all three cycles
    all_three = ex.filter_stations(
        ds.stations, cycles=["1314", "1819", "2324"], match="all")
    assert len(all_three) == 11
    payload = ex.station_geojson(ex.filter_stations(ds.stations, ecoregion="71"))
    assert len(payload["features"]) == 64
