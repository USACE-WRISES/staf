"""The reference screen keys on each NRSA site's own reach (2026-09-07).

The NRSA sample frame is NHDPlus V2, so a candidate's archive COMID names the
reach the crew sampled and the screen reads StreamCat there with no routing at
all. Before this the batch path passed lat/lon only: 27 of 186 NEH candidates
routed to a reach downstream, 12 of those were retained, and 9 archive COMIDs
were silently replaced by the routed one, which then keyed the curve values.

Covered here: the candidate rows, the provenance columns every screening row
now carries, the summary, the COMID precedence, every screening-cache verdict,
the chunked-screen diagnostics merge, and the vendored site limit. Offline.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from streamcurves import easi_screening as es
from streamcurves import regional_agent as ra
from streamcurves import run_state


# --------------------------------------------------------------------------- #
# candidate COMIDs
# --------------------------------------------------------------------------- #
def test_candidate_comid_reads_the_archive_value_and_the_zero_sentinel():
    assert es.candidate_comid({"comid": 5214461}) == 5214461
    assert es.candidate_comid({"comid": "5214461"}) == 5214461
    assert es.candidate_comid({"comid": 0}) is None       # EPA published none
    assert es.candidate_comid({"comid": None}) is None
    assert es.candidate_comid({}) is None and es.candidate_comid({"comid": "x"}) is None


def test_comid_mode_of_a_panel():
    assert es.comid_mode_of([{"comid": 1}, {}]) == es.COMID_MODE_ARCHIVE_FIRST
    assert es.comid_mode_of([{"comid": 0}, {}]) == es.COMID_MODE_COORDINATE
    assert es.comid_mode_of([]) == es.COMID_MODE_COORDINATE


def test_run_evidence_passes_the_archive_comid(monkeypatch):
    seen = {}

    def fake_tier(rows, preset, **kw):
        seen["rows"] = rows
        seen["mode"] = kw.get("comid_mode")
        raise RuntimeError("stop after the screen call")

    panel = pd.DataFrame({"site_id": ["A", "B", "C"], "lat": [40.0, 41.0, 42.0],
                          "lon": [-83.0, -84.0, -85.0],
                          "comid": [5214461, 0, None],
                          "comid_source": ["epa_published", "", None]})
    monkeypatch.setattr(ra, "select_candidates_detailed",
                        lambda *a, **k: (panel, pd.DataFrame()))
    monkeypatch.setattr(ra, "choose_reference_tier", fake_tier)
    with pytest.raises(RuntimeError):
        ra.run_evidence("55", "Eastern Corn Belt Plains", nrsa_dataset_id="multi-cycle-v1")
    rows = seen["rows"]
    assert rows[0] == {"site_id": "A", "lat": 40.0, "lon": -83.0,
                       "comid": 5214461, "comid_source": "epa_published"}
    assert "comid" not in rows[1] and "comid" not in rows[2]   # 0 and None route
    assert seen["mode"] == es.COMID_MODE_ARCHIVE_FIRST


# --------------------------------------------------------------------------- #
# the provenance columns
# --------------------------------------------------------------------------- #
def _batch(site: dict) -> dict:
    base = {"site_id": "A", "state": "succeeded", "qualification": {"final": "retained"},
            "delineation": {"comid": 5214461}, "input": {"lat": 40.0, "lon": -83.0}}
    base.update(site)
    return {"sites": [base]}


def _row(site: dict) -> dict:
    return es.to_screening_tables(_batch(site))["easi_screening_sites"][0]


def test_an_archive_comid_is_recorded_as_such():
    row = _row({"input": {"lat": 40.0, "lon": -83.0, "comid": 5214461,
                          "metadata": {"comid_source": "epa_published"}}})
    assert row["comid_source"] == "archive" and row["comid_input"] == 5214461
    assert row["archive_comid_source"] == "epa_published"
    assert row["comid_differs"] is False and row["routed_comid"] is None


def test_a_routed_site_carries_the_reach_the_distance_and_the_ratio():
    row = _row({"delineation": {"comid": 9327042},
                "anchor": {"anchorKind": "hrSurrogate",
                           "clickedStream": {"nhdplusId": 24000800011817},
                           "scoredReach": {"comid": 9327042},
                           "routing": {"routedDistanceFt": 1688.0, "daRatio": 32.0,
                                       "declined": True, "declineCode": "da-ratio"}}})
    assert row["comid_source"] == "routed" and row["anchor_kind"] == "hrSurrogate"
    assert row["routed_comid"] == 9327042 and row["routed_distance_ft"] == 1688.0
    assert row["da_ratio"] == 32.0 and row["routing_declined"] is True
    assert row["clicked_nhdplusid"] == 24000800011817


def test_a_routed_site_that_disagrees_with_the_archive_is_flagged():
    row = _row({"input": {"lat": 40.0, "lon": -83.0, "comid": 87396037},
                "delineation": {"comid": 4594211},
                "anchor": {"anchorKind": "v2Direct", "scoredReach": {"comid": 4594211}}})
    assert row["comid_differs"] is True and row["comid_input"] == 87396037


def test_a_refusal_and_an_unresolved_site_are_told_apart():
    refused = _row({"state": "failed", "delineation": {},
                    "qualification": {"final": ""},
                    "issues": [{"severity": "error", "code": "surrogate_da_ratio_exceeded",
                                "message": "past the bound"}]})
    assert refused["comid_source"] == "refused"
    unresolved = _row({"state": "failed", "delineation": {}, "qualification": {"final": ""},
                       "issues": [{"severity": "error", "code": "snap_service_error",
                                   "message": "NLDI down"}]})
    assert unresolved["comid_source"] == "unresolved"
    snapped = _row({"anchor": {"anchorKind": "v2Direct",
                               "scoredReach": {"comid": 5214461}}})
    assert snapped["comid_source"] == "snapped"


def test_the_criteria_table_names_the_mode_and_the_vendored_engine():
    tables = es.to_screening_tables(_batch(
        {"input": {"lat": 40.0, "lon": -83.0, "comid": 5214461}}))
    crit = tables["easi_screening_criteria"]
    assert crit["comid_mode"] == es.COMID_MODE_ARCHIVE_FIRST
    assert crit["easi_vendor_sha"] == es.easi_vendor_identity()["vendorSha"]
    assert len(crit["easi_vendor_sha"]) == 64


def test_the_summary_counts_and_lists_what_needs_reading():
    rows = [
        {"site_id": "A", "comid_source": "archive"},
        {"site_id": "B", "comid_source": "routed", "routed_comid": 9327042,
         "routed_distance_ft": 1688.0, "da_ratio": 32.0, "final_decision": "retained"},
        {"site_id": "C", "comid_source": "refused", "da_ratio": 111.6,
         "issue_code": "surrogate_da_ratio_exceeded"},
        {"site_id": "D", "comid_source": "snapped", "comid_differs": True,
         "comid_input": 87396037, "comid": 4594211, "final_decision": "retained"},
    ]
    s = es.screening_comid_summary(rows)
    assert s["counts"] == {"archive": 1, "routed": 1, "refused": 1, "snapped": 1}
    assert [r["site_id"] for r in s["routed_sites"]] == ["B"]
    assert [r["site_id"] for r in s["refused_sites"]] == ["C"]
    assert s["differing_sites"][0] == {"site_id": "D", "archive_comid": 87396037,
                                       "scored_comid": 4594211, "routed_distance_ft": None,
                                       "da_ratio": None, "final_decision": "retained"}


# --------------------------------------------------------------------------- #
# the archive COMID keys the curve values
# --------------------------------------------------------------------------- #
def test_attach_comids_never_overwrites_the_archive_comid():
    data = pd.DataFrame({"site_id": ["A", "B", "C"], "comid": [87396037, 0, None]})
    tables = {"easi_screening_sites": [
        {"site_id": "A", "comid": 4594211},        # the screen routed downstream
        {"site_id": "B", "comid": 111},            # the archive published none
        {"site_id": "C", "comid": 222}]}
    out = ra.attach_comids(data, tables)
    assert list(out["comid"]) == [87396037, 111, 222]


# --------------------------------------------------------------------------- #
# the screening cache is keyed on what produced it
# --------------------------------------------------------------------------- #
def _cache_doc(**over) -> dict:
    doc = {"sites": [{"site_id": "A", "input": {"comid": 5214461}},
                     {"site_id": "B", "input": {"comid": None}}],
           "screening_method_version": run_state.SCREENING_METHOD_VERSION,
           "screening_watershed_engine": es.SCREENING_WATERSHED_ENGINE,
           "comid_mode": es.COMID_MODE_ARCHIVE_FIRST}
    doc.update(over)
    return doc


ROWS = [{"site_id": "A", "lat": 40.0, "lon": -83.0, "comid": 5214461},
        {"site_id": "B", "lat": 41.0, "lon": -84.0}]


def test_a_matching_cache_is_reused():
    assert ra._screen_cache_verdict(_cache_doc(), ROWS,
                                    es.COMID_MODE_ARCHIVE_FIRST) is None


@pytest.mark.parametrize("over,rows,mode,fragment", [
    ({"sites": [{"site_id": "A", "input": {"comid": 5214461}}]}, ROWS,
     es.COMID_MODE_ARCHIVE_FIRST, "is not this panel"),
    ({"screening_method_version": None}, ROWS, es.COMID_MODE_ARCHIVE_FIRST,
     "screening method unstamped"),
    ({"screening_method_version": "easi-batch-1"}, ROWS, es.COMID_MODE_ARCHIVE_FIRST,
     "screening method easi-batch-1"),
    ({"screening_watershed_engine": None}, ROWS, es.COMID_MODE_ARCHIVE_FIRST,
     "watershed engine unstamped"),
    ({"config": {"watershed_engine": "auto"}}, ROWS, es.COMID_MODE_ARCHIVE_FIRST,
     "engine config echo auto"),
    ({}, ROWS, es.COMID_MODE_COORDINATE, "comid mode"),
    ({"sites": [{"site_id": "A", "input": {"comid": None}},
                {"site_id": "B", "input": {"comid": None}}]}, ROWS,
     es.COMID_MODE_ARCHIVE_FIRST, "archive COMIDs differ"),
])
def test_every_reason_a_cache_is_ignored(over, rows, mode, fragment):
    verdict = ra._screen_cache_verdict(_cache_doc(**over), rows, mode)
    assert verdict is not None and fragment in verdict


def test_screen_pool_rejects_a_pre_routing_cache_and_rescreens(tmp_path, monkeypatch):
    # 34 of the 39 caches in the repo were written by an EASI that had no
    # routing at all: they carry no stamps and must never be screened again as-is
    calls = []

    def fake_direct(rows, preset, on_event=None):
        calls.append([r["site_id"] for r in rows])
        return {"sites": [{"site_id": r["site_id"], "input": {"comid": r.get("comid")},
                           "qualification": {"final": "retained"}} for r in rows]}

    monkeypatch.setattr(ra.easi_screening, "screen_sites_direct", fake_direct)
    cache = tmp_path / "screening_cache_functional.json"
    cache.write_text(json.dumps({"sites": [{"site_id": "A"}, {"site_id": "B"}]}),
                     encoding="utf-8")
    out = ra.screen_pool(ROWS, "functional", cache_path=cache)
    assert calls == [["A", "B"]] and out["from_cache"] is False
    assert "screening method unstamped" in out["cache"]["ignored"]
    # the rewritten cache carries the stamps, so the next run reuses it
    doc = json.loads(cache.read_text(encoding="utf-8"))
    assert doc["screening_method_version"] == run_state.SCREENING_METHOD_VERSION
    assert doc["screening_watershed_engine"] == es.SCREENING_WATERSHED_ENGINE
    assert doc["comid_mode"] == es.COMID_MODE_ARCHIVE_FIRST and doc["written_at"]
    again = ra.screen_pool(ROWS, "functional", cache_path=cache)
    assert calls == [["A", "B"]] and again["from_cache"] is True
    assert again["cache"]["ignored"] is None


# --------------------------------------------------------------------------- #
# the chunked screen
# --------------------------------------------------------------------------- #
def test_the_vendored_site_limit_is_importable():
    from streamcurves._vendor.easi.batch.api import MAX_SITES
    assert ra._engine_max_sites() == int(MAX_SITES)


def test_chunked_diagnostics_are_the_runs_not_chunk_ones(monkeypatch):
    # NEH's cache reported "150 sites, 17 failed" for a 186-site screen because
    # only chunk 1's diagnostics survived the merge
    monkeypatch.setattr(ra, "_engine_max_sites", lambda: 2)

    def fake_direct(rows, preset, on_event=None):
        return {"sites": [{"site_id": r["site_id"]} for r in rows],
                "diagnostics": {"total_sites": len(rows), "failed": 1, "elapsed_s": 2.5},
                "generated_ids": [r["site_id"] for r in rows],
                "config": {"watershed_engine": "streamcat-legacy"}}

    monkeypatch.setattr(ra.easi_screening, "screen_sites_direct", fake_direct)
    rows = [{"site_id": f"S{i}", "lat": 40.0, "lon": -83.0} for i in range(5)]
    batch = ra._screen_live(rows, "functional", None)
    assert len(batch["sites"]) == 5
    assert batch["diagnostics"]["total_sites"] == 5 and batch["diagnostics"]["failed"] == 3
    assert batch["diagnostics"]["elapsed_s"] == 7.5 and batch["diagnostics"]["n_chunks"] == 3
    assert len(batch["generated_ids"]) == 5
    assert batch["config"] == {"watershed_engine": "streamcat-legacy"}
