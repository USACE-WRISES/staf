"""Tests for the batch workspace parsing helpers."""
from __future__ import annotations

from easi.batch_ui import parse_sites_text, result_summary


def test_csv_with_header():
    sites, errors = parse_sites_text("id,lat,lon\nMB,43.7,-72.2\nCC,40.1,-83.1\n")
    assert errors == []
    assert [s["site_id"] for s in sites] == ["MB", "CC"]
    assert sites[0]["lat"] == 43.7 and sites[0]["lon"] == -72.2


def test_tsv_and_header_synonyms():
    sites, errors = parse_sites_text("name\tlatitude\tlongitude\nA\t40.0\t-83.0")
    assert errors == [] and sites[0]["site_id"] == "A"


def test_whitespace_no_header_three_cols():
    sites, errors = parse_sites_text("MB 43.7 -72.2\nCC 40.1 -83.1")
    assert errors == [] and len(sites) == 2 and sites[1]["site_id"] == "CC"


def test_two_cols_no_id():
    sites, errors = parse_sites_text("43.7,-72.2\n40.1,-83.1")
    assert errors == [] and all(s["site_id"] == "" for s in sites)


def test_comid_column():
    sites, _ = parse_sites_text("id,lat,lon,comid\nA,40.0,-83.0,12345")
    assert sites[0]["comid"] == 12345


def test_out_of_range_row_is_error():
    sites, errors = parse_sites_text("id,lat,lon\nA,40.0,-83.0\nB,10.0,10.0")
    assert len(sites) == 1 and len(errors) == 1 and "out of CONUS" in errors[0]


def test_over_limit_warns():
    body = "\n".join(f"S{i},40.0,{-83.0 - i*0.001}" for i in range(151))
    sites, errors = parse_sites_text("id,lat,lon\n" + body)
    assert len(sites) == 151
    assert any("exceeds" in e for e in errors)


def test_result_summary():
    batch = {"sites": [{"qualification": {"final": "retained"}},
                       {"qualification": {"final": "excluded"}}],
             "diagnostics": {"succeeded": 2, "qualified": 1, "elapsed_s": 3.2}}
    s = result_summary(batch)
    assert s["total"] == 2 and s["retained"] == 1 and s["qualified"] == 1
