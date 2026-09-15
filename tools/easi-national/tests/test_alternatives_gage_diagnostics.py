import calendar
from datetime import date
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder.analysis.alternatives import gage_diagnostics as g
from builder.analysis.alternatives.io import info, sha, write_json


def row(day, value, **kwargs):
    return {"time": str(day), "value": str(value), "unit_of_measure": "ft^3/s", "approval_status": "Approved",
            "qualifier": None, "monitoring_location_id": "USGS-00123456", "parameter_code": "00060",
            "statistic_id": "00003", **kwargs}


def test_monthly_cv_and_daily_weighted_annual_mean_with_leap_year():
    rows = [row(date(2000, m, d), m) for m in range(1, 13) for d in range(1, calendar.monthrange(2000, m)[1]+1)]
    rows[0]["qualifier"] = ["e"]
    rows[1]["approval_status"] = "Provisional"
    result = g.summarize_gage(rows)
    year = result["annual"][0]
    annual_mean = sum(m * calendar.monthrange(2000, m)[1] for m in range(1, 13)) / 366
    assert year["usable_days"] == year["expected_days"] == 366 and year["full_year"]
    assert year["monthly_cv"] == pytest.approx(np.std(np.arange(1., 13.), ddof=0) / 6.5)
    assert year["min_month_annual_ratio"] == pytest.approx(1 / annual_mean)
    assert year["annual_mean_cfs"] == pytest.approx(annual_mean)
    assert year["qualified_days"] == year["not_approved_days"] == 1
    assert year["approved_unqualified_days"] == 364
    assert result["full_years"] == result["years_with_data"] == 1
    assert result["annual"][-1]["expected_days"] == 366
    assert result["annual"][-1]["monthly_cv"] is None


def test_sparse_twelve_months_labeled_partial_not_invented_coverage_threshold():
    rows = [row(date(2001, m, 1), m) for m in range(1, 13)]
    result = g.summarize_gage(rows)
    year = result["annual"][1]
    assert year["monthly_cv"] is not None and year["full_year"] is False
    assert year["daily_coverage"] == pytest.approx(12 / 365)
    assert g.summarize_gage(rows[:-1])["climatology"]["monthly_cv"] is None
    assert g.summarize_gage([row(date(2001, m, 1), 0) for m in range(1, 13)])["climatology"]["min_month_annual_ratio"] is None


def test_conflicting_duplicates_invalid_values_units_and_qualifiers():
    rows = [row("2001-01-01", 1), row("2001-01-01", 1, qualifier=["e"]),
            row("2001-01-02", 1), row("2001-01-02", 2), row("2001-01-03", -9999),
            row("2001-01-04", "Ice"), row("2001-01-05", 1, unit_of_measure="unknown"),
            row("2001-01-06", 1, unit_of_measure="m^3/s"), row("2025-01-01", 1)]
    days, rejected = g.daily_records(rows)
    assert len(days) == 2 and days[0]["source_observations"] == 2 and days[0]["qualifiers"] == ["e"]
    assert days[1]["value_cfs"] == pytest.approx(35.31466672148859)
    assert rejected == {"conflicting_duplicate_days": 1, "nonfinite_or_negative_discharge": 1,
                        "missing_or_nonnumeric_date_value": 1, "unsupported_unit": 1, "outside_period": 1}


def fixture(root):
    study = root / "review/alternative-studies/test"
    folder = study / "acquisition"
    (folder / "daily").mkdir(parents=True)
    source = folder / "daily/00123456.json"
    write_json(source, {"gage_no": "00123456", "rows": [row(date(2001, m, 1), m) for m in range(1, 13)]})
    write_json(folder / "manifest.json", {"daily": [{"gage_no": "00123456", "status": "available", **info(source)}]})
    metadata = {"comid": "11", "qc_final": "OK"}
    write_json(folder / "gage-matches.json", {"rows": [{"station_key": "a", "comid": 11, "gage_no": "00123456", "gage_metadata": metadata},
                                                        {"station_key": "b", "comid": 11, "gage_no": "00123456", "gage_metadata": metadata}]})
    write_json(folder / "summary.json", {"training_independence": "unknown"})
    (root / "analysis").mkdir()
    pq.write_table(pa.Table.from_pylist([{"comid": 11, "erom__q_cv_monthly": .5, "erom__q_min_ratio": .1,
                                        "erom__qe_ma": 6., "sc__bfiws": 35.}]), root / "analysis/landscape.parquet")
    return study


def test_exact_gage_comparisons_keep_units_vintages_and_do_not_duplicate_stations(tmp_path):
    study = fixture(tmp_path)
    source = tmp_path / "analysis/landscape.parquet"
    before = sha(source)
    result = g.run(tmp_path, study)
    assert result["comparison_pairs"] == 1 and result["gage_count"] == 1
    assert len(result["rows"]) == 8  # four climatology pairs plus four annual pairs
    assert {r["year"] for r in result["rows"]} == {"climatology", 2001}
    assert all(r["n"] == 1 for r in result["signed_associations"])
    assert all(r["rho"] is None for r in result["signed_associations"])
    bfi = next(r for r in result["rows"] if r["stored_column"] == "sc__bfiws")
    assert bfi["reference"] == 35. and "different quantities" in bfi["interpretation"]
    assert sha(source) == before
    summary = json.loads((study / "acquisition/summary.json").read_text())
    assert summary["training_independence"] == "unknown"
    assert summary["gage_diagnostics"]["comparison_pairs"] == 1


def test_changed_daily_source_fails_before_diagnostics(tmp_path):
    study = fixture(tmp_path)
    (study / "acquisition/daily/00123456.json").write_text('{}')
    with pytest.raises(ValueError, match="differ from acquisition manifest"):
        g.run(tmp_path, study)
    assert not (study / "acquisition/gage-diagnostics.json").exists()


def test_unresolved_gage_qc_stays_in_sensitivity_only_and_partial_acquisition_is_visible(tmp_path):
    study = fixture(tmp_path)
    path = study / "acquisition/gage-matches.json"
    matches = json.loads(path.read_text())
    for match in matches["rows"]:
        match["gage_metadata"]["qc_notes1"] = "Drop - diversion no NHD flowline"
    write_json(path, matches)
    path = study / "acquisition/manifest.json"
    manifest = json.loads(path.read_text()); manifest["status"] = "partial"
    write_json(path, manifest)
    result = g.run(tmp_path, study)
    assert result["status"] == "partial" and result["primary_gages_with_usable_days"] == 0
    assert result["gages_with_usable_days"] == 1
    assert all(not r["primary_eligible"] for r in result["rows"])
    assert all(r["n"] == 0 for r in result["signed_associations"])
    assert all(r["n"] == 1 for r in result["sensitivity_all_exact_associations"])
