"""The reference screen (rule REF-04): EASI's least-disturbed-v1, mirrored and pure."""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from streamcurves import methodology
from streamcurves import reference_screen as rs
from streamcurves._vendor.easi import geo as easi_geo


def _row(**over) -> dict:
    """A watershed that passes the strict screen, from its StreamCat parts."""
    base = {"pctimp2019ws": 0.4, "pctcrop2019ws": 2.0, "pcthay2019ws": 3.0, "rddensws": 1.1,
            "damnrmstorws": 0.0, "runoffws": 500.0, "nabd_densws": 0.0, "npdesdensws": 0.0,
            "minedensws": 0.0, "coalminedensws": 0.0}
    base.update(over)
    return base


def _frame(*rows) -> pd.DataFrame:
    return rs.derive_screen_variables(pd.DataFrame(list(rows)))


# --------------------------------------------------------------------------- #
# the mirror
# --------------------------------------------------------------------------- #
def test_the_governed_screen_mirrors_the_vendored_easi_screen():
    assert rs.screen_drift() == []
    assert rs.governed_screen()["id"] == rs.SCREEN_ID == rs.vendored_screen()["id"]


def test_the_strict_screen_is_the_seven_conditions_of_the_easi_report():
    assert rs.rules("strict") == {
        "pctimp2019ws": ("<=", 1.0), "agriculture_ws": ("<=", 10.0), "rddensws": ("<=", 2.0),
        "dor": ("<", 2.0), "nabd_densws": ("==", 0.0), "npdesdensws": ("==", 0.0),
        "mines_ws": ("==", 0.0)}
    assert "nabd_densws" not in rs.rules("relaxed")      # the relaxed screen allows dams


def test_an_edited_rule_is_reported(monkeypatch):
    edited = copy.deepcopy(methodology.load_config())
    edited["reference_screen"]["strict"]["pctimp2019ws"] = ["<=", 5.0]
    monkeypatch.setattr(methodology, "load_config", lambda: edited)
    problems = rs.screen_drift()
    assert any("strict.pctimp2019ws" in p for p in problems)


def test_the_methodology_mirror_check_covers_the_screen(monkeypatch):
    edited = copy.deepcopy(methodology.load_config())
    edited["reference_screen"]["relaxed"]["agriculture_ws"] = ["<=", 40.0]
    monkeypatch.setattr(methodology, "load_config", lambda: edited)
    assert any("reference_screen.relaxed.agriculture_ws" in p
               for p in methodology.mirror_drift())


def test_an_unknown_tier_is_refused():
    with pytest.raises(ValueError):
        rs.rules("lenient")


# --------------------------------------------------------------------------- #
# derived variables (EASI's formulas)
# --------------------------------------------------------------------------- #
def test_agriculture_is_crop_plus_hay_and_a_missing_part_is_missing():
    df = _frame(_row(), _row(pcthay2019ws=np.nan))
    assert df.loc[0, "agriculture_ws"] == pytest.approx(5.0)
    assert np.isnan(df.loc[1, "agriculture_ws"])


def test_degree_of_regulation_is_storage_over_runoff_as_a_percent():
    # 5,000 m3/km2 of storage over 500 mm of runoff (500,000 m3/km2) is 1 percent
    df = _frame(_row(damnrmstorws=5000.0, runoffws=500.0), _row(runoffws=0.0))
    assert df.loc[0, "dor"] == pytest.approx(1.0)
    assert np.isnan(df.loc[1, "dor"])                    # undefined without runoff


def test_mines_are_mines_plus_coal_mines():
    df = _frame(_row(minedensws=0.01, coalminedensws=0.02))
    assert df.loc[0, "mines_ws"] == pytest.approx(0.03)


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #
def test_a_low_pressure_watershed_passes_both_tiers():
    df = _frame(_row())
    for tier in rs.TIERS:
        ok, why = rs.evaluate(df, tier)
        assert bool(ok.iloc[0]) and why.iloc[0] == ""


@pytest.mark.parametrize("over,failing", [
    ({"pctimp2019ws": 1.01}, "pctimp2019ws"),
    ({"pctcrop2019ws": 8.0, "pcthay2019ws": 2.5}, "agriculture_ws"),
    ({"rddensws": 2.01}, "rddensws"),
    ({"damnrmstorws": 10000.0}, "dor"),                  # exactly 2 percent: the rule is <
    ({"nabd_densws": 0.001}, "nabd_densws"),
    ({"npdesdensws": 0.002}, "npdesdensws"),
    ({"coalminedensws": 0.004}, "mines_ws"),
])
def test_each_condition_alone_fails_the_strict_screen(over, failing):
    ok, why = rs.evaluate(_frame(_row(**over)), "strict")
    assert not bool(ok.iloc[0])
    assert why.iloc[0] == failing


def test_the_boundary_values_pass_where_the_rule_is_inclusive():
    ok, _ = rs.evaluate(_frame(_row(pctimp2019ws=1.0, rddensws=2.0,
                                    pctcrop2019ws=10.0, pcthay2019ws=0.0)), "strict")
    assert bool(ok.iloc[0])


def test_a_missing_value_fails_its_rule_and_says_so():
    ok, why = rs.evaluate(_frame(_row(rddensws=np.nan)), "strict")
    assert not bool(ok.iloc[0])
    assert why.iloc[0] == "missing:rddensws"


def test_every_failing_variable_is_listed():
    ok, why = rs.evaluate(_frame(_row(pctimp2019ws=9.0, rddensws=4.0)), "strict")
    assert not bool(ok.iloc[0])
    assert set(why.iloc[0].split(";")) == {"pctimp2019ws", "rddensws"}


def test_the_relaxed_screen_is_looser_never_tighter():
    rows = [_row(pctimp2019ws=2.5), _row(pctcrop2019ws=20.0), _row(nabd_densws=0.01),
            _row(damnrmstorws=20000.0)]
    df = _frame(*rows)
    strict, _ = rs.evaluate(df, "strict")
    relaxed, _ = rs.evaluate(df, "relaxed")
    assert not strict.any() and relaxed.all()
    assert not (strict & ~relaxed).any()


def test_failures_read_in_plain_words():
    row = _frame(_row(pctimp2019ws=9.0)).iloc[0]
    text = rs.describe_failures(row, "strict")
    assert "watershed impervious cover" in text and "9" in text and "<= 1" in text


# --------------------------------------------------------------------------- #
# classes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("slope", [0.0, 0.00499, 0.005, 0.0199, 0.02, 0.3, -9998.0, None,
                                   float("nan")])
def test_slope_classes_are_easis(slope):
    assert rs.slope_class(slope) == easi_geo.slope_class(slope)


@pytest.mark.parametrize("area,label", [
    (0.5, "le_10"), (10.0, "le_10"), (10.01, "10_to_100"), (100.0, "10_to_100"),
    (100.5, "gt_100"), (0.0, None), (None, None), (float("nan"), None)])
def test_drainage_area_classes(area, label):
    assert rs.da_class(area) == label


# --------------------------------------------------------------------------- #
# the screening tables
# --------------------------------------------------------------------------- #
def _screen_table() -> pd.DataFrame:
    rows = [{"station_key": "A", "comid": 11, "eci_raw": 0.72, **_row()},
            {"station_key": "B", "comid": 22, "eci_raw": 0.41, **_row(pctimp2019ws=12.0)},
            {"station_key": "C", "comid": 33, "eci_raw": np.nan, **_row(rddensws=np.nan)}]
    table = rs.derive_screen_variables(pd.DataFrame(rows))
    for tier in rs.TIERS:
        ok, why = rs.evaluate(table, tier)
        table[f"pass_{tier}"], table[f"fail_{tier}"] = ok, why
    table["screen_evaluable"] = ~table["fail_strict"].str.contains("missing:")
    return table


def test_the_tables_have_the_shape_the_app_already_reads():
    panel = pd.DataFrame({"site_id": ["A", "B", "C", "Z"], "lat": [1.0] * 4, "lon": [2.0] * 4})
    tables = rs.to_screening_tables(panel, _screen_table())
    assert set(tables) == {"easi_screening_sites", "easi_screening_metrics",
                           "easi_screening_criteria"}
    by = {r["site_id"]: r for r in tables["easi_screening_sites"]}
    assert by["A"]["final_decision"] == "retained"
    assert by["B"]["final_decision"] == "excluded"
    assert "impervious" in by["B"]["reason"]
    assert by["C"]["final_decision"] == "not_evaluable"      # a missing value is not a pass
    assert by["Z"]["final_decision"] == "not_evaluable"      # unknown station
    assert by["A"]["eci"] == pytest.approx(0.72)             # context only, never the gate
    crit = tables["easi_screening_criteria"]
    assert crit["method"] == rs.METHOD
    assert crit["criteria"]["screen"] == rs.SCREEN_ID and crit["criteria"]["tier"] == "strict"


def test_the_eci_never_decides():
    table = _screen_table()
    table.loc[table["station_key"] == "B", "eci_raw"] = 0.99       # a high index, a failed screen
    table.loc[table["station_key"] == "A", "eci_raw"] = 0.05       # a low index, a passed screen
    panel = pd.DataFrame({"site_id": ["A", "B"], "lat": [1.0, 1.0], "lon": [2.0, 2.0]})
    by = {r["site_id"]: r["final_decision"]
          for r in rs.to_screening_tables(panel, table)["easi_screening_sites"]}
    assert by == {"A": "retained", "B": "excluded"}


def test_the_pressure_tables_are_publishable_criteria():
    """The all-sites guard must not mistake the pressure screen for it."""
    from streamcurves import easi_screening as es
    panel = pd.DataFrame({"site_id": ["A"], "lat": [1.0], "lon": [2.0]})
    crit = rs.to_screening_tables(panel, _screen_table())["easi_screening_criteria"]
    assert es.screening_publishable(crit) is True
