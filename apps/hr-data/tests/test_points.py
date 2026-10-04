"""Point lookups from the bundled national tables (hrslim.points), each app's rule on small tables.

Everything sits around (40 N, 100 W). One degree of latitude is 69.093 miles on EASI's WQP sphere
and 111,195 m on the NID sphere.
"""
from datetime import date

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from hrslim import points

LAT, LON = 40.0, -100.0
MI_DEG = 1 / 69.0933                          # degrees of latitude per mile (EASI's WQP radius)
M_DEG = 1 / 111_195.08                        # degrees of latitude per metre (NID radius)


@pytest.fixture(scope="module")
def tables(tmp_path_factory):
    folder = tmp_path_factory.mktemp("tables")
    # stations: A at the point, B 3 miles north, C 6 miles north (outside the 5 mile radius)
    pq.write_table(pa.table({"station": ["A", "B", "C"],
                             "lat": [LAT, LAT + 3 * MI_DEG, LAT + 6 * MI_DEG], "lon": [LON] * 3,
                             "name": ["a", "b", "c"], "org": ["o", "o", "o"]}), folder / "wqp_stations.parquet")
    # station, param, date, raw, reason, milli
    rows = [(0, 0, "2020-05-01", 1.0, 0, False),
            (0, 0, "2021-05-01", 3.0, 0, False),
            (0, 0, "2022-05-01", 2000.0, 0, True),        # ug/L: EASI reads 2.0, SFARI the raw 2000
            (0, 0, "2023-05-01", 0.5, 3, False),          # censored: EASI drops it, SFARI keeps it
            (0, 0, "2010-05-01", 50.0, 0, False),         # before both windows
            (0, 1, "2020-05-01", 0.2, 0, False),          # TP
            (1, 0, "2020-06-01", 5.0, 0, False),
            (1, 0, "2020-07-01", 7.0, 2, False),          # rejected
            (1, 0, "2020-08-01", float("nan"), 1, False),  # blank
            (2, 0, "2020-05-01", 100.0, 0, False)]
    pq.write_table(pa.table({
        "station": pa.array([r[0] for r in rows], type=pa.int32()),
        "param": pa.array([r[1] for r in rows], type=pa.int8()),
        "date": pa.array(np.array([r[2] for r in rows], dtype="datetime64[D]")),
        "raw": pa.array([r[3] for r in rows], type=pa.float64()),
        "reason": pa.array([r[4] for r in rows], type=pa.int8()),
        "milli": pa.array([r[5] for r in rows]),
        "rid_num": pa.array([1000 + i if i % 2 == 0 else None for i in range(len(rows))], type=pa.int64()),
        "rid_uuid": pa.array([None if i % 2 == 0 else bytes(range(i, i + 16)) for i in range(len(rows))],
                             type=pa.binary(16)),
        "rid_text": pa.array([None] * len(rows), type=pa.string())}), folder / "wqp_results.parquet")
    # temperature in hundredths of a degree C: station A reports Celsius (and once Fahrenheit, which
    # is then not needed, plus a December 36 the screen set apart as out of season and a 55 outside
    # -1 to 40 C), B reports Fahrenheit only (one conversion implausible), C is 6 miles away
    temp = [(0, "2020-07-01", 20.0, 0), (0, "2021-07-01", 22.0, 0), (0, "2022-07-01", 25.0, 7),
            (0, "2022-12-01", 36.0, 10), (0, "2023-07-01", 55.0, 9),
            (1, "2020-07-02", 18.5, 7), (1, "2020-08-02", float("nan"), 8), (2, "2020-07-03", 30.0, 0)]
    v = np.array([r[2] for r in temp])
    pq.write_table(pa.table({
        "station": pa.array([r[0] for r in temp], type=pa.int32()),
        "date": pa.array(np.array([r[1] for r in temp], dtype="datetime64[D]")),
        "value_c100": pa.array(np.round(np.nan_to_num(v) * 100).astype(np.int16), mask=np.isnan(v)),
        "reason": pa.array([r[3] for r in temp], type=pa.int8()),
        "rid_num": pa.array([5000 + i for i in range(len(temp))], type=pa.int64()),
        "rid_uuid": pa.array([None] * len(temp), type=pa.binary(16)),
        "rid_text": pa.array([None] * len(temp), type=pa.string())}), folder / "wqp_temperature.parquet")
    # dams 0.5 and 0.99 miles north, 1.01 miles north, and one at a corner of SFARI's box
    mile_m = 1609.344
    dam_lat = [LAT + 0.5 * mile_m * M_DEG, LAT + 0.99 * mile_m * M_DEG, LAT + 1.01 * mile_m * M_DEG, LAT + 0.014]
    pq.write_table(pa.table({
        "nid_id": ["D1", "D2", "D3", "D4"], "name": ["Half", "Edge", "Out", "Corner"],
        "lon": [LON, LON, LON, LON + 0.014], "lat": dam_lat,
        "nid_storage_acft": [10.0, None, 30.0, 40.0], "normal_storage_acft": [5.0, None, 15.0, 20.0],
        "dam_height_ft": [12.0, 8.0, None, 20.0], "nid_height_ft": [12.0, 8.0, None, 20.0],
        "river": ["r"] * 4, "state": ["NE"] * 4}), folder / "nid_points.parquet")
    # gages: G1 comparable with a record, G2 more comparable but too short, G3 far too large
    pq.write_table(pa.table({
        "site": ["G1", "G2", "G3"], "name": ["one", "two", "three"],
        "lat": [LAT + 0.05, LAT + 0.08, LAT + 0.01], "lon": [LON, LON, LON],
        "da_sqmi": [110.0, 100.0, 1000.0], "n_days": [4000, 30, 4000],
        "zero_frac": [0.0, None, 0.0], "q10": [500.0, None, 900.0], "q50": [100.0, None, 300.0],
        "q90": [20.5, None, 80.0], "baseflow_ratio": [0.205, None, 0.267],
        "as_of": ["2026-09-30"] * 3}), folder / "nwis_gages.parquet")
    pq.write_table(pa.table({
        "scientificName": ["Cyprinus carpio", "Cyprinus carpio", "Corbicula fluminea", "Dreissena polymorpha", None],
        "huc8": ["10200101"] * 4 + ["10200102"], "huc12": ["102001010101", "102001010101", "102001010102", "", ""],
        "status": ["established"] * 5}), folder / "nas_taxa.parquet")
    return points.PointTables(folder)


def test_wqp_easi_balances_stations(tables):
    s = points.wqp_easi(tables, "tn", LAT, LON, start=date(2016, 10, 2))
    assert s["station_medians"] == {"A": 2.0, "B": 5.0}              # A: 1.0, 3.0 and 2000 ug/L
    assert s["value"] == 3.5 and s["station_count"] == 2 and s["observation_count"] == 4
    assert s["excluded"]["censored"] == 1 and s["excluded"]["rejected"] == 1 and s["excluded"]["blank"] == 1
    assert s["excluded_count"] == 3
    assert s["date_start"] == "2020-05-01" and s["date_end"] == "2022-05-01"
    assert s["nearest_distance_mi"] == 0.0
    tp = points.wqp_easi(tables, "tp", LAT, LON, start=date(2016, 10, 2))
    assert tp["value"] == 0.2 and tp["station_count"] == 1


def test_wqp_easi_window_and_radius(tables):
    assert points.wqp_easi(tables, "tn", LAT, LON, start=date(2009, 1, 1))["station_medians"]["A"] == 2.5
    far = points.wqp_easi(tables, "tn", LAT + 20 * MI_DEG, LON, start=date(2016, 10, 2))
    assert far["value"] is None and far["station_count"] == 0
    assert points.wqp_easi(tables, "ph", LAT, LON) is None


def test_wqp_sfari_reads_every_number(tables):
    # since 2015: A 1.0, 3.0, 2000 (raw), 0.5 (censored); B 5.0, 7.0 (rejected); the blank is skipped
    assert points.wqp_sfari(tables, "tn", LAT, LON) == 4.0
    assert points.wqp_sfari(tables, "tn", LAT + 20 * MI_DEG, LON) is None


def test_nid_radius_and_box(tables):
    near = points.nid_radius(tables, LAT, LON, 1.0)
    assert [d["name"] for d in near] == ["Half", "Edge"]
    assert near[0]["distance_m"] == pytest.approx(0.5 * 1609.344, abs=0.2) and near[1]["storage"] is None
    assert sorted(d["name"] for d in points.nid_box(tables, LAT, LON, 1.0)) == ["Corner", "Edge", "Half"]


def test_nwis_skips_short_records(tables):
    got = points.nwis_flow_stats(tables, LAT, LON, 100.0 / points.SQMI_PER_SQKM)
    assert got["site"] == "G1" and got["q50"] == 100.0 and got["n_days"] == 4000
    assert points.nwis_flow_stats(tables, LAT + 5.0, LON, 250.0) is None


def test_nas_by_huc12_then_huc8(tables):
    assert points.nas_established(tables, huc12="102001010101") == ["Cyprinus carpio"]
    assert points.nas_established(tables, huc12="102001019999") == []
    assert points.nas_established(tables, huc8="10200101") == ["Corbicula fluminea", "Cyprinus carpio",
                                                                "Dreissena polymorpha"]
    assert points.nas_established(tables) is None


def test_wqp_temperature_summary_and_its_results(tables):
    t = points.wqp_easi(tables, "temp", LAT, LON, start=date(2016, 10, 2), details=True)
    assert t["units"] == "°C" and t["station_medians"] == {"A": 21.0, "B": 18.5}   # B from Fahrenheit
    assert t["value"] == 19.75 and t["observation_count"] == 3 and t["fahrenheit_converted"] == 1
    assert t["excluded"]["unsupported_unit"] == 1           # A's Fahrenheit, not needed
    assert t["excluded"]["implausible"] == 3                # A's two screened results, B's implausible conversion
    assert [r["result_id"] for r in t["results"]] == [f"STORET-{n}" for n in range(5000, 5007)]
    assert [r["used"] for r in t["results"]] == [True, True, False, False, False, True, False]
    assert t["results"][2]["value"] == 25.0 and t["results"][2]["reason"] == "fahrenheit"
    assert t["results"][3]["value"] == 36.0 and t["results"][3]["reason"] == "celsius_seasonal"
    raw = points.wqp_easi(tables, "temp", LAT, LON, start=date(2016, 10, 2), screen=False)
    assert raw["station_medians"]["A"] == 29.0 and raw["excluded"]["implausible"] == 1   # 20, 22, 36, 55
    easi = points.wqp_easi(tables, "temp", LAT, LON, start=date(2016, 10, 2), fahrenheit=False, screen=False)
    assert easi["station_medians"] == {"A": 29.0} and easi["excluded"]["unsupported_unit"] == 3   # EASI's own rule
    assert easi["excluded"]["implausible"] == 0


def test_temperature_beyond_int16_reads_back(tmp_path):
    # a Celsius sentinel EASI's rule still counts does not fit int16 hundredths; the builder widens the
    # column to int32 and the reader takes either
    pq.write_table(pa.table({"station": ["A"], "lat": [LAT], "lon": [LON], "name": ["a"], "org": ["o"]}),
                   tmp_path / "wqp_stations.parquet")
    v = np.array([20.0, 9999.0, 22.0])
    pq.write_table(pa.table({
        "station": pa.array([0, 0, 0], type=pa.int32()),
        "date": pa.array(np.array(["2020-07-01", "2021-07-01", "2022-07-01"], dtype="datetime64[D]")),
        "value_c100": pa.array(np.round(v * 100).astype(np.int32)),
        "reason": pa.array([0, 0, 0], type=pa.int8()),
        "rid_num": pa.array([1, 2, 3], type=pa.int64()),
        "rid_uuid": pa.array([None] * 3, type=pa.binary(16)),
        "rid_text": pa.array([None] * 3, type=pa.string())}), tmp_path / "wqp_temperature.parquet")
    t = points.wqp_easi(points.PointTables(tmp_path), "temp", LAT, LON, start=date(2016, 10, 2), details=True)
    assert t["station_medians"] == {"A": 22.0} and t["observation_count"] == 3
    assert max(r["value"] for r in t["results"]) == 9999.0


def test_wqp_results_carry_the_identifiers(tables):
    got = points.wqp_results(tables, "tn", LAT, LON, start=date(2016, 10, 2))
    assert len(got) == 7 and {r["station"] for r in got} == {"A", "B"}
    assert got[0]["result_id"] == "STORET-1000" and got[1]["result_id"].count("-") == 4      # a UUID
    assert got[0]["value"] == 1.0 and got[0]["distance_mi"] == 0.0
