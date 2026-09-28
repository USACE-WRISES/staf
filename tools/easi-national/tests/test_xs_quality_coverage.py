"""The K2b quality table (``builder.analysis.xs_quality``) and the coverage table by stream
class (``builder.analysis.xs_coverage``) on synthetic evidence and a synthetic weighted sample
(WP-R6c): the quality row is the evaluator's own record, the scan reads an evidence parquet,
the reliability masks and the coverage arithmetic are plain weighted shares."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from builder.analysis import xs_coverage as cov
from builder.analysis import xs_quality as xq


def _geom(bhr=1.1, er=2.5, *, n=9, capped=0, depth=0.5, dem_res=10, extrapolated=False):
    scalars = [{"bank_height_ratio": bhr, "entrenchment_ratio": er, "bankfull_width_m": 10.0 + i,
                "bankfull_depth_m": depth, "low_bank_capped": i < capped, "edge_limited": False}
               for i in range(n)]
    median_capped = bool(capped and bhr >= 2.0 - 1e-9)
    geom = {"entrenchment_ratio": er, "bank_height_ratio": bhr, "edge_limited": False, "dem_resolution_m": dem_res,
            "n_transects": n, "selected": 0, "candidate_scalars": scalars, "candidates": [],
            "reach": {"n": n, "entrenchment_ratio": {"median": er, "min": er, "max": er, "n": n},
                      "bank_height_ratio": {"median": bhr, "min": bhr, "max": bhr, "n": n, "capped": capped,
                                            "median_capped": median_capped, "max_capped": median_capped}}}
    bankfull = {"width_m": 5.0, "depth_m": 0.5, "area_m2": 2.0, "division": "AHI", "division_name": "Appalachian Highlands",
                "regional": True, "extrapolated": extrapolated, "fit_range_sqkm": [0.2, 2435.0]}
    return json.dumps(geom), json.dumps(bankfull)


def test_quality_row_is_the_evaluators_record_per_reach():
    from easi import geomorph
    g, b = _geom()
    row = xq.quality_row(1, "0104", g, b, 50.0, 40.0, -83.0)
    assert row["xs_status"] == "ok" and row["quality"] == geomorph.QUALITY_RULES == "K2b"
    assert row["flags"] == "" and row["low_quality_tokens"] == "" and row["sections"] == 9
    assert row["cap_unreachable_depth_m"] == 0.15 and row["depth_median_m"] == 0.5 and row["shallow_sections"] == 0
    g, b = _geom(bhr=2.0, capped=6, depth=0.5)
    row = xq.quality_row(2, "0104", g, b)
    assert row["flags"] == "" and row["bhr_at_cap"] and row["median_capped"] and row["capped_sections"] == 6
    g, b = _geom(bhr=2.0, capped=6, depth=0.12)
    row = xq.quality_row(3, "0104", g, b)
    assert row["flags"] == "low_quality" and row["low_quality_tokens"] == "cap_unreachable" and row["cap_is_floor"]
    assert row["cap_unreachable_sections"] == 6 and row["shallow_sections"] == 9
    g, b = _geom(extrapolated=True, n=2)
    row = xq.quality_row(4, "0104", g, b)
    assert row["low_quality_tokens"] == "few_sections:bhr,few_sections:er,bankfull_extrapolated"
    assert json.loads(row["reasons"])["low_quality"].startswith("only 2 of 2")
    assert xq.quality_row(5, "0104", None, b)["xs_status"] == "none"
    assert xq.quality_row(6, "0104", "{}", b)["xs_status"] == "empty"
    assert set(row) == set(xq.COLUMNS)


def test_the_scan_reads_an_evidence_parquet_and_the_masks_read_the_rows(tmp_path):
    rows = []
    for i, (kw) in enumerate([dict(), dict(bhr=2.0, capped=6), dict(bhr=2.0, capped=6, depth=0.12), dict(extrapolated=True),
                              dict(er=0.8), dict(n=2)]):
        g, b = _geom(**kw)
        rows.append({"comid": 100 + i, "huc4": "0104", "geomorph": g, "bankfull": b, "totdasqkm": 5.0, "lat": 40.0, "lon": -83.0})
    rows.append({"comid": 200, "huc4": "0104", "geomorph": None, "bankfull": None, "totdasqkm": 5.0, "lat": 40.0, "lon": -83.0})
    staging = tmp_path / "staging"
    staging.mkdir()
    pq.write_table(pa.Table.from_pylist(rows), staging / "evidence_0104.parquet")
    frame = xq.scan(staging, workers=1)
    assert list(frame["comid"]) == [100, 101, 102, 103, 104, 105, 200]
    assert list(frame["xs_status"]) == ["ok"] * 6 + ["none"]
    er_ok = xq.reliable_for(frame, "er_median")
    bhr_ok = xq.reliable_for(frame, "bhr_median")
    assert list(er_ok) == [True, True, False, False, False, False, False]
    assert list(bhr_ok) == [True, True, False, False, True, False, False]     # an ER below 1 withholds the ER only
    three = xq.reliable_three_rules(frame, "er_median")
    assert list(three) == [True, True, True, False, True, False, False]
    doc = xq.summary(frame, staging=staging, files=xq.evidence_files(staging), seconds=1.0)
    assert doc["counts"]["status"] == {"ok": 6, "none": 1} and doc["counts"]["flags"] == {"low_quality": 3, "out_of_range_er": 1}
    assert doc["counts"]["low_quality_tokens"] == {"cap_unreachable": 1, "bankfull_extrapolated": 1,
                                                    "few_sections:bhr": 1, "few_sections:er": 1}
    assert doc["counts"]["reliable_er_median"] == 2 and doc["counts"]["reliable_bhr_median"] == 3
    written = xq.write(frame, tmp_path / "out", doc)
    back = pq.read_table(tmp_path / "out" / "xs_quality.parquet").to_pandas()
    assert len(back) == 7 and written["parquet"]["file"] == "xs_quality.parquet"
    assert json.loads((tmp_path / "out" / "xs_quality.json").read_text(encoding="utf-8"))["quality"] == "K2b"


def test_the_coverage_table_is_weighted_shares_on_a_synthetic_sample():
    comids = list(range(1, 11))
    weights = [1.0, 1.0, 2.0, 2.0, 1.0, 1.0, 3.0, 3.0, 1.0, 1.0]              # population 16
    sample = pd.DataFrame({"comid": comids, "sample_weight": weights,
                           "nars9": ["CPL"] * 5 + ["WMT"] * 5, "slope_class": ["lt_0.5", "ge_2"] * 5,
                           "da_class": ["le_10"] * 10}).set_index("comid")
    quality = pd.DataFrame({
        "comid": comids, "xs_status": ["ok"] * 10, "er": [2.0] * 10, "bhr": [1.0] * 10,
        # 1-2 clean; 3-4 capped only (E5 withheld, K2b rates); 5 extrapolated; 6 cap floor;
        # 7 few sections; 8 extrapolated and cap floor; 9 clean; 10 an ER below 1
        "flags": ["", "", "", "", "low_quality", "low_quality", "low_quality", "low_quality", "", "out_of_range_er"],
        "low_quality_tokens": ["", "", "", "", "bankfull_extrapolated", "cap_unreachable", "few_sections:bhr,few_sections:er",
                               "bankfull_extrapolated,cap_unreachable", "", ""]}).set_index("comid")
    rated = pd.Series(["rated"] * 10, index=sample.index)
    base = {fn: rated for fn in cov.FUNCTIONS}
    e5_comp = pd.Series(["rated", "rated", "withheld", "withheld", "withheld", "withheld", "withheld", "withheld", "rated", "withheld"],
                        index=sample.index)
    e5 = {fn: e5_comp for fn in cov.FUNCTIONS}
    table = cov.coverage_table(sample, quality, base, e5)
    assert table["n_sample"] == 10 and table["population_n"] == 16.0
    hf = table["functions"]["high_flow_dynamics"]
    # K2b rates 1, 2, 3, 4, 9 and 10 (the ER flag never withholds a BHR method): weights 1+1+2+2+1+1 = 8 of 16
    assert hf["availability_base"] == 1.0 and hf["availability_k2b"] == 0.5
    assert hf["availability_e5"] == (1 + 1 + 1) / 16 and hf["n_k2b_rated"] == 6
    assert hf["regained_share"] == (2 + 2 + 1) / 16 and hf["n_regained"] == 3          # 3, 4 and 10
    fc = table["functions"]["floodplain_connectivity"]
    assert fc["availability_k2b"] == (1 + 1 + 2 + 2 + 1) / 16 and fc["n_regained"] == 2      # 10 stays withheld for the ER
    comp = table["withheld_composition"]["floodplain_connectivity"]
    withheld_weight = 2 + 2 + 1 + 1 + 3 + 3 + 1                                        # comids 3 to 8 and 10
    assert comp["regained (capped median only)"]["n"] == 2 and comp["regained (capped median only)"]["weighted"] == 4.0
    assert comp["extrapolated bankfull"]["n"] == 1 and comp["cap is the detector's floor"]["n"] == 1
    assert comp["fewer than three sections"]["n"] == 1 and comp["several K2b reasons"]["n"] == 1
    assert comp["impossible ratio"]["n"] == 1 and comp["impossible ratio"]["share_of_withheld"] == 1 / withheld_weight
    assert sum(v["n"] for v in comp.values()) == 7
    by_region = table["by_class"]["high_flow_dynamics"]["nars9"]
    assert by_region["CPL"]["weighted"] == 7.0 and by_region["CPL"]["availability_k2b"] == (1 + 1 + 2 + 2) / 7
    assert by_region["WMT"]["availability_k2b"] == (1 + 1) / 9 and by_region["WMT"]["still_withheld_share"] == 7 / 9
    # CPL withheld 3, 4 and 5 (weights 2, 2, 1); K2b regains 3 and 4: 4 of 5. WMT withheld 6, 7, 8
    # and 10 (weights 1, 3, 3, 1); the BHR methods regain 10 only: 1 of 8
    assert by_region["CPL"]["regained_share_of_withheld"] == 0.8 and by_region["WMT"]["regained_share_of_withheld"] == 1 / 8
    # the E5b study's own completeness is read beside the table's reading when given
    e5b = {fn: pd.Series(["rated", "rated", "rated", "rated", "withheld", "withheld", "withheld", "withheld", "rated", "rated"],
                         index=sample.index) for fn in cov.FUNCTIONS}
    table = cov.coverage_table(sample, quality, base, e5, e5b)
    assert table["functions"]["high_flow_dynamics"]["k2b_reading_matches_study"] is True
    assert table["functions"]["floodplain_connectivity"]["k2b_reading_matches_study"] is False
    assert table["functions"]["floodplain_connectivity"]["n_k2b_reading_differs"] == 1
    assert cov.weighted_share([True, False], [1.0, 3.0]) == 0.25 and cov.weighted_share([], []) is None
    assert cov.completeness_columns(pd.DataFrame({"rating__high_flow_dynamics": ["Good", None]}),
                                    {"high_flow_dynamics": ("bhr",)})["high_flow_dynamics"].tolist() == ["rated", "missing"]
