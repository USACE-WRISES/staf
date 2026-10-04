"""The screen of unrealistic Celsius stream temperatures (hrbuild.wqp_temperature.screen).

Eleven stations share one 2 degree cell (Wisconsin-like): eight report ordinary readings (about 1
to 3 C in December, 9 to 10 C in October, 18 to 21 C in July) for six years, F1 also logs one
December 36 (Fahrenheit labelled Celsius), H1 is a hot spring at 34 to 36 C in December and July,
Z1 reads at or below freezing in July and in December, C1 is a cold creek (12 to 13 C summers) that
logs one October 36 (a Fahrenheit deployment outside winter, which does not fit as Fahrenheit).
"""
from datetime import date

import pandas as pd

from hrbuild import wqp_temperature as wt

OK, F_OK = wt.REASONS.index("ok"), wt.REASONS.index("fahrenheit")
SEASONAL, IMPLAUSIBLE = wt.REASONS.index("celsius_seasonal"), wt.REASONS.index("celsius_implausible")


def _rows():
    rows = []
    for k in range(8):
        for y in range(2016, 2022):
            rows.append((f"N{k}", date(y, 12, 5), 1.0 + 0.25 * k, OK))
            rows.append((f"N{k}", date(y, 10, 5), 9.0 + 0.2 * k, OK))
            rows.append((f"N{k}", date(y, 7, 5), 18.0 + 0.4 * k, OK))
    for y, jul in ((2017, 12.0), (2018, 12.5), (2019, 13.0)):
        rows.append(("C1", date(y, 7, 9), jul, OK))
        rows.append(("C1", date(y, 10, 9), 9.5, OK))
    rows.append(("C1", date(2020, 10, 9), 36.0, OK))           # far above the area's October and C1's summers
    for y in range(2016, 2022):
        rows.append(("F1", date(y, 12, 6), 1.5, OK))
        rows.append(("F1", date(y, 7, 6), 19.0, OK))
    rows.append(("F1", date(2020, 12, 20), 36.0, OK))           # 36 F labelled Celsius
    for y, dec, jul in ((2017, 34.0, 35.0), (2018, 35.0, 36.0), (2019, 36.0, 34.5)):
        rows.append(("H1", date(y, 12, 7), dec, OK))
        rows.append(("H1", date(y, 7, 7), jul, OK))
    rows += [("Z1", date(2019, 7, 8), -0.5, OK), ("Z1", date(2019, 12, 8), -0.5, OK),
             ("Z1", date(2020, 7, 8), 19.5, OK), ("Z1", date(2020, 12, 8), 1.2, OK),
             ("N0", date(2021, 7, 20), 55.0, OK),                 # outside -1 to 40 C
             ("N1", date(2021, 7, 21), 50.0, F_OK)]               # a Fahrenheit row: left alone
    t = pd.DataFrame(rows, columns=["station", "date", "value", "reason"])
    t["lat"], t["lon"] = 44.5, -89.5
    return t


def test_screen_sets_apart_what_is_not_a_stream_temperature():
    t = _rows()
    reasons, stats = wt.screen(t)
    got = t.assign(reason=reasons)

    def reason_of(station, day, value):
        row = got[(got["station"] == station) & (got["date"] == day) & (got["value"] == value)]
        return int(row["reason"].iat[0])

    assert reason_of("F1", date(2020, 12, 20), 36.0) == SEASONAL          # fits only as Fahrenheit
    assert reason_of("Z1", date(2019, 7, 8), -0.5) == SEASONAL            # freezing in July
    assert reason_of("Z1", date(2019, 12, 8), -0.5) == OK                 # freezing in December is real
    assert reason_of("C1", date(2020, 10, 9), 36.0) == SEASONAL           # far above the area and its summers
    assert reason_of("C1", date(2019, 7, 9), 13.0) == OK
    assert reason_of("N0", date(2021, 7, 20), 55.0) == IMPLAUSIBLE
    assert reason_of("N1", date(2021, 7, 21), 50.0) == F_OK
    assert (got.loc[got["station"] == "H1", "reason"] == OK).all()        # a hot spring keeps its readings
    assert stats["fahrenheit_like"] == 1 and stats["freezing_in_a_warm_month"] == 1
    assert stats["far_above_the_area_and_own_summer"] == 1
    assert stats["celsius_outside_bounds"] == 1 and stats["hot_spring_readings_kept"] == 3
    assert (got["value"] == t["value"]).all()                             # values are never changed


def test_without_enough_data_only_the_bounds_apply():
    t = _rows()
    thin = t[t["station"].isin(["F1", "Z1", "N0"])].copy()                # three stations: no area climate
    reasons, stats = wt.screen(thin)
    got = thin.assign(reason=reasons)
    assert int(got.loc[(got["station"] == "F1") & (got["value"] == 36.0), "reason"].iat[0]) == OK
    assert int(got.loc[got["value"] == 55.0, "reason"].iat[0]) == IMPLAUSIBLE
    assert stats["celsius_without_an_area_month"] > 0
