"""Tests for the headless Regional Analysis Agent (pure stages, no network)."""
from __future__ import annotations

import pandas as pd
import pytest

from streamcurves import regional_agent as ra


# --------------------------------------------------------------------------- #
# Candidate selection (reimplemented nrsa_in_region)
# --------------------------------------------------------------------------- #
def test_select_candidates_l3_counts():
    nh = ra.select_candidates("58")
    ecbp = ra.select_candidates("55")
    assert len(nh) == 71          # Northeastern Highlands
    assert len(ecbp) == 18        # Eastern Corn Belt Plains (sparse)
    for col in ("site_id", "lat", "lon"):
        assert col in nh.columns


def test_select_candidates_empty_region():
    assert len(ra.select_candidates("does-not-exist")) == 0


# --------------------------------------------------------------------------- #
# Missingness dispositions (DATA-01/02/03) acting on the review map
# --------------------------------------------------------------------------- #
def test_metric_missingness_classifies_and_counts_absent_columns():
    data = pd.DataFrame({
        "clean": [1.0, 2.0, 3.0, 4.0, 5.0],
        "half": [1.0, None, 3.0, None, 5.0],
    })
    out = ra.metric_missingness(data, ["clean", "half", "absent"])
    assert out["clean"]["disposition"] == "auto"
    assert out["half"]["missing_fraction"] == 0.4
    assert out["half"]["disposition"] == "caution"
    # A column that never arrived is fully missing, not silently skipped.
    assert out["absent"]["missing_fraction"] == 1.0
    assert out["absent"]["disposition"] == "review"


def test_review_curves_routes_high_missingness_to_data_review():
    from streamcurves import run_state as rs
    row = {"curve_status": "complete", "n_reference": 30}
    review = ra.review_curves(
        {"m": dict(row)}, {"m": "Nutrient cycling"},
        missingness={"m": {"missing_fraction": 0.55, "disposition": "review"}})
    assert review["m"]["status"] == rs.CURVE_STATUS_DATA_REVIEW
    assert "55%" in review["m"]["reasons"][0]
    # The same curve with acceptable coverage auto-finalizes.
    clean = ra.review_curves(
        {"m": dict(row)}, {"m": "Nutrient cycling"},
        missingness={"m": {"missing_fraction": 0.1, "disposition": "auto"}})
    assert clean["m"]["status"] == rs.CURVE_STATUS_AUTO_OK


# --------------------------------------------------------------------------- #
# Curated direction map
# --------------------------------------------------------------------------- #
def test_build_metric_config_applies_curated_directions():
    directions = ra.load_directions()
    cols = ["chem_PTL", "bent_EPT_NTAX", "phab_PCT_SAFN", "phab_XBKA", "chem_DOC"]
    mc, flagged = ra.build_metric_config(cols, directions)
    # correct ecological directions, not the naive higher-is-better default
    assert mc["chem_PTL"]["higher_is_better"] is False       # phosphorus: lower is better
    assert mc["bent_EPT_NTAX"]["higher_is_better"] is True   # richness: higher is better
    assert mc["phab_PCT_SAFN"]["higher_is_better"] is False  # sand/fines: lower is better
    # Resolved at the NEH review gate (2026-08-21): bank angle scores two-sided.
    assert mc["phab_XBKA"]["curve_form"] == "optimum"
    assert mc["phab_XBKA"]["expected_shape"] == "optimum"
    # DOC is a recorded, human-decided exclusion: flagged as documented, never re-asked.
    doc = next(f for f in flagged if f["metric"] == "chem_DOC")
    assert doc["documented"] is True
    assert doc["decided_by"]
    assert "chem_DOC" not in mc


def test_uncurated_metric_is_flagged_never_guessed():
    mc, flagged = ra.build_metric_config(["chem_PTL"], {})
    assert mc == {}
    assert flagged[0]["metric"] == "chem_PTL"
    assert flagged[0]["reason"] == "no curated direction available"
    assert not flagged[0].get("documented")


def test_build_metric_config_carries_expected_shape_and_transformation():
    directions = ra.load_directions()
    cols = ["chem_PTL", "bent_EPT_NTAX", "chem_PH"]
    mc, _ = ra.build_metric_config(cols, directions)
    assert mc["chem_PTL"]["expected_shape"] == "monotone_decreasing"
    assert mc["bent_EPT_NTAX"]["expected_shape"] == "monotone_increasing"
    assert mc["chem_PH"]["expected_shape"] == "optimum"      # curve_form: optimum
    for code in ("chem_PTL", "bent_EPT_NTAX", "chem_PH"):
        assert mc[code]["transformation"] == "none"


def test_two_sided_metrics_build_instead_of_being_flagged():
    # These degrade at BOTH extremes. Before the optimum form existed they had no
    # representable shape, so they were dropped -- taking Floodplain connectivity and
    # Channel evolution out of every published assessment with them.
    directions = ra.load_directions()
    mc, flagged = ra.build_metric_config(["chem_PH", "phab_BFWD_RAT", "phab_XBKF_H"], directions)
    flagged_metrics = {f["metric"] for f in flagged}
    for code in ("chem_PH", "phab_BFWD_RAT", "phab_XBKF_H"):
        assert code in mc, f"{code} should build as a two-sided curve"
        assert code not in flagged_metrics
        assert mc[code]["curve_form"] == "optimum"
        # no "better" direction is the whole point of a two-sided response
        assert mc[code]["higher_is_better"] is None


# --------------------------------------------------------------------------- #
# Landscape (StreamCat) metrics — the 12-of-20 coverage defect
# --------------------------------------------------------------------------- #
def test_landscape_split_scores_condition_metrics_and_keeps_context_as_predictors():
    d = ra.load_landscape_directions()
    scored, predictors = ra.select_landscape_codes(d)
    # condition indicators with an unambiguous direction are scored
    for code in ("pctimp2019", "pctcrop2019", "pctwdwet2019", "bfi", "damdens", "rdcrs"):
        assert code in scored
    # climate/scaling context is never scored, but is not discarded either
    for code in ("runoff", "precip8110"):
        assert code in predictors and code not in scored


def test_landscape_metric_config_keys_by_the_suffixed_column():
    # StreamCat returns pctimp2019 as the column pctimp2019ws; the config must key by
    # the column that actually exists or nothing downstream can find it.
    d = ra.load_landscape_directions()
    mc, missing = ra.build_landscape_metric_config(["pctimp2019ws", "bfiws"], d)
    assert mc["pctimp2019ws"]["higher_is_better"] is False   # impervious: lower is better
    assert mc["pctimp2019ws"]["column_name"] == "pctimp2019ws"
    assert mc["bfiws"]["higher_is_better"] is True           # base-flow index: higher is better
    # codes with no column present are reported, never silently dropped
    assert {m["metric"] for m in missing} and "pctimp2019" not in {m["metric"] for m in missing}


def test_predictor_config_covers_the_context_variables():
    d = ra.load_landscape_directions()
    pc = ra.build_predictor_config(["runoffws", "precip8110ws", "pctimp2019ws"], d)
    assert set(pc) == {"runoffws", "precip8110ws"}   # scored metrics are not predictors here
    assert pc["runoffws"]["type"] == "continuous"


def test_streamcat_failure_is_reported_not_silently_na():
    d = ra.load_landscape_directions()
    data = pd.DataFrame({"site_id": ["a"], "comid": [123], "chem_PTL": [10.0]})

    def _boom(*a, **k):
        raise RuntimeError("service unreachable")

    out, report = ra.enrich_streamcat(data, d, fetch=_boom)
    assert report["status"] == "failed" and "unreachable" in report["reason"]
    assert list(out.columns) == list(data.columns)      # untouched


def test_streamcat_enrichment_joins_by_comid():
    d = ra.load_landscape_directions()
    data = pd.DataFrame({"site_id": ["a", "b"], "comid": [1, 2], "chem_PTL": [10.0, 20.0]})
    wide = pd.DataFrame({"COMID": [1, 2], "pctimp2019ws": [3.5, 9.0]})
    out, report = ra.enrich_streamcat(data, d, fetch=lambda *a, **k: wide)
    assert report["status"] == "ok" and report["n_columns"] == 1
    assert list(out["pctimp2019ws"]) == [3.5, 9.0]


def test_streamcat_without_comid_reports_rather_than_raising():
    d = ra.load_landscape_directions()
    data = pd.DataFrame({"site_id": ["a"], "chem_PTL": [10.0]})
    out, report = ra.enrich_streamcat(data, d, fetch=lambda *a, **k: pd.DataFrame())
    assert report["status"] == "failed" and "comid" in report["reason"]
    assert out is data


def test_streamcat_partial_fetch_is_not_reported_ok(tmp_path):
    """A chunk that fails after retries used to vanish: the joined table came
    back short, the report said ok, and the partial table was even cached.
    The failed chunks now ride out on the frame's attrs, the status is partial
    (which the batch runner refuses like any bad source), and nothing caches."""
    d = ra.load_landscape_directions()
    data = pd.DataFrame({"site_id": ["a", "b"], "comid": [1, 2], "chem_PTL": [1.0, 2.0]})
    wide = pd.DataFrame({"COMID": [1], "pctimp2019ws": [3.5]})   # comid 2's chunk failed
    wide.attrs["n_chunks"] = 2
    wide.attrs["failed_chunks"] = [2]
    cache = tmp_path / "streamcat_cache.json"
    out, report = ra.enrich_streamcat(data, d, fetch=lambda *a, **k: wide,
                                      cache_path=cache)
    assert report["status"] == "partial"
    assert "2" in report["reason"] and report["failed_chunks"] == [2]
    assert report["n_columns"] == 1                  # what did arrive still joins
    assert "pctimp2019ws" in out.columns
    assert not cache.exists()                        # a partial table never caches


def test_streamcat_clean_fetch_with_chunk_attrs_stays_ok(tmp_path):
    d = ra.load_landscape_directions()
    data = pd.DataFrame({"site_id": ["a", "b"], "comid": [1, 2], "chem_PTL": [1.0, 2.0]})
    wide = pd.DataFrame({"COMID": [1, 2], "pctimp2019ws": [3.5, 9.0]})
    wide.attrs["n_chunks"] = 1
    wide.attrs["failed_chunks"] = []
    cache = tmp_path / "streamcat_cache.json"
    out, report = ra.enrich_streamcat(data, d, fetch=lambda *a, **k: wide,
                                      cache_path=cache)
    assert report["status"] == "ok"
    assert cache.exists()                            # a complete table still caches


# --------------------------------------------------------------------------- #
# Curve building honors direction (lower-is-better inverts)
# --------------------------------------------------------------------------- #
def _synthetic(col: str, values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"site_id": [f"s{i}" for i in range(len(values))], col: values})


def test_build_curves_lower_is_better_inverts():
    data = _synthetic("chem_PTL", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    mc = {"chem_PTL": {"column_name": "chem_PTL", "higher_is_better": False,
                       "metric_family": "continuous", "display_name": "TP"}}
    rows = ra.build_curves(data, mc)
    pts = rows["chem_PTL"]["curve_points"]
    # index score must fall as the (lower-is-better) metric rises
    assert float(pts["index_score"].iloc[0]) > float(pts["index_score"].iloc[-1])
    assert rows["chem_PTL"]["curve_status"] == "complete"


def test_review_flags_degenerate_and_scopes_clean():
    # A clean higher-is-better metric + a degenerate one: a NONNEGATIVE scale
    # collapsed at zero (q25 == 0). Signed scales no longer trip the guard
    # (iqr-seed-2), so the fixture uses the guard's real domain.
    data = pd.DataFrame({
        "site_id": [f"s{i}" for i in range(10)],
        "good": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "bad": [0, 0, 0, 0, 0, 0, 0, 1, 2, 3],
    })
    mc = {
        "good": {"column_name": "good", "higher_is_better": True, "metric_family": "continuous"},
        "bad": {"column_name": "bad", "higher_is_better": True, "metric_family": "continuous"},
    }
    rows = ra.build_curves(data, mc)
    review = ra.review_curves(rows, {"good": "Biology: Habitat provision", "bad": "Biology: Habitat provision"})
    from streamcurves import run_state as rs
    intended = rs.intended_metrics_for_publish(review)
    flagged = rs.flagged_metrics(review)
    assert "good" in intended
    assert "bad" in flagged            # degenerate_q25 -> flagged, not published


def test_review_flags_unmapped():
    data = _synthetic("m", list(range(1, 11)))
    mc = {"m": {"column_name": "m", "higher_is_better": True, "metric_family": "continuous"}}
    rows = ra.build_curves(data, mc)
    review = ra.review_curves(rows, {"m": ""})   # no function assigned
    assert review["m"]["status"] == "unmapped"


# --------------------------------------------------------------------------- #
# Redundancy (RED-01 Spearman-primary)
# --------------------------------------------------------------------------- #
def test_redundancy_red01_spearman_flag():
    # a and b are a perfect monotone transform -> Spearman 1.0 -> RED-01 flag
    data = pd.DataFrame({"a": [1, 2, 3, 4, 5, 6, 7, 8], "b": [1, 4, 9, 16, 25, 36, 49, 64]})
    mc = {"a": {"column_name": "a"}, "b": {"column_name": "b"}}
    red = ra.redundancy_matrix(data, mc, {"a": "F1", "b": "F2"})
    assert len(red) == 1
    assert bool(red.iloc[0]["red01_spearman_flag"]) is True
    assert abs(red.iloc[0]["spearman"]) >= 0.99


# --------------------------------------------------------------------------- #
# Portfolio (SELECT-01)
# --------------------------------------------------------------------------- #
def test_portfolio_select01_flag_over_two():
    cf = {"m1": "Biology: Community dynamics", "m2": "Biology: Community dynamics",
          "m3": "Biology: Community dynamics", "m4": "Biology: Habitat provision"}
    port = ra.compact_portfolio(["m1", "m2", "m3", "m4"], cf, {})
    by_fn = {p["function"]: p for p in port}
    assert by_fn["Community dynamics"]["select01_flag"] is True
    assert by_fn["Habitat provision"]["select01_flag"] is False


def test_portfolio_enumerates_all_twenty_functions_including_gaps():
    """A metric-less function used to be structurally invisible here, so a document
    titled "Compact Metric Portfolio" read as complete when it was partial."""
    port = ra.compact_portfolio(["m1"], {"m1": "Biology: Community dynamics"}, {})
    assert len(port) == 20
    by_fn = {p["function"]: p for p in port}
    assert by_fn["Community dynamics"]["coverage"] == "covered"
    assert by_fn["Reach inflow"]["coverage"] == "GAP"
    assert by_fn["Reach inflow"]["n_metrics"] == 0
    assert by_fn["Reach inflow"]["primary_metric"] is None
    assert by_fn["Reach inflow"]["discipline"] == "Hydrology"


def test_portfolio_resolves_both_label_shapes():
    """column_functions carries "Discipline: Function"; the crosswalk is keyed on the
    bare name. Getting this wrong yields 20 empty rows plus the covered ones."""
    prefixed = ra.compact_portfolio(["m1"], {"m1": "Hydrology: Reach inflow"}, {})
    bare = ra.compact_portfolio(["m1"], {"m1": "Reach inflow"}, {})
    for port in (prefixed, bare):
        assert len(port) == 20
        assert {p["function"]: p for p in port}["Reach inflow"]["coverage"] == "covered"


def test_portfolio_keeps_unmapped_metrics_visible():
    port = ra.compact_portfolio(["m1"], {"m1": "Not A Real Function"}, {})
    assert len(port) == 21
    unmapped = [p for p in port if p["coverage"] == "unmapped"]
    assert unmapped and unmapped[0]["metrics"] == ["m1"]


def test_uncovered_functions_pairs_each_gap_with_its_candidate_metrics():
    port = ra.compact_portfolio(["m1"], {"m1": "Biology: Community dynamics"}, {})
    gaps = ra.uncovered_functions(port)
    assert len(gaps) == 19
    reach = next(g for g in gaps if g["function"] == "Reach inflow")
    # metric_map.yaml maps road density here; a reviewer needs the way out, not
    # just the news that something is missing.
    assert any(c.startswith("rddens") for c in reach["candidate_metrics"])


def test_uncovered_functions_is_empty_when_everything_is_covered():
    from streamcurves import deep_export as de
    cf = {f"m{i}": str(f["name"]) for i, f in enumerate(de.deep_read_staf_crosswalk())}
    port = ra.compact_portfolio(list(cf), cf, {})
    assert all(p["coverage"] == "covered" for p in port)
    assert ra.uncovered_functions(port) == []


# --------------------------------------------------------------------------- #
# Reference-tier ladder (REF-01/02/03), screening mocked
# --------------------------------------------------------------------------- #
def test_choose_reference_tier_ref01_when_pool_adequate(monkeypatch):
    def fake(rows, preset, on_event=None, cache_path=None):
        ids = [f"s{i}" for i in range(40)]  # 40 functioning >= floor
        return {"tables": {}, "sites": [], "retained_ids": ids,
                "counts": {"n_retained": 40}, "preset": preset}
    monkeypatch.setattr(ra, "screen_pool", fake)
    tier = ra.choose_reference_tier([], "functional")
    assert tier["reference_tier"] == ra.TIER_LEAST_DISTURBED
    assert tier["ref02_triggered"] is False


def test_choose_reference_tier_ref02_fallback(monkeypatch):
    calls = []

    def fake(rows, preset, on_event=None, cache_path=None):
        calls.append(preset)
        n = 5 if preset == "functional" else 25   # too few functioning -> fallback
        return {"tables": {}, "sites": [], "retained_ids": [f"s{i}" for i in range(n)],
                "counts": {"n_retained": n}, "preset": preset}
    monkeypatch.setattr(ra, "screen_pool", fake)
    tier = ra.choose_reference_tier([], "functional")
    assert tier["reference_tier"] == ra.TIER_BEST_AVAILABLE
    assert tier["ref02_triggered"] is True
    assert calls == ["functional", "at_risk_or_better"]   # explicit, ordered fallback
    assert tier["review_flags"]                            # mandatory review recorded


def test_choose_reference_tier_rejects_below_floor():
    with pytest.raises(ValueError):
        ra.choose_reference_tier([], "all_sites")   # REF-03: never below at_risk_or_better


def test_screen_pool_cache_is_keyed_to_the_candidate_panel(tmp_path, monkeypatch):
    """The cache file carries no key, so screen_pool recovers the cached panel
    from its own rows. A reused out dir whose candidate panel changed (e.g. a
    different --nrsa-dataset) must refetch, never silently screen the old list."""
    calls = []

    def fake_direct(rows, preset, on_event=None):
        calls.append([r["site_id"] for r in rows])
        return {"sites": [{"site_id": r["site_id"]} for r in rows]}

    def fake_tables(batch):
        return {"easi_screening_sites": [
            {"site_id": s["site_id"], "final_decision": "retained"}
            for s in batch.get("sites", [])]}

    monkeypatch.setattr(ra.easi_screening, "screen_sites_direct", fake_direct)
    monkeypatch.setattr(ra.easi_screening, "to_screening_tables", fake_tables)
    cache = tmp_path / "screening_cache_functional.json"
    panel_a = [{"site_id": "A1"}, {"site_id": "A2"}]

    first = ra.screen_pool(panel_a, "functional", cache_path=cache)
    assert first["from_cache"] is False and cache.exists()
    second = ra.screen_pool(panel_a, "functional", cache_path=cache)
    assert second["from_cache"] is True
    assert calls == [["A1", "A2"]]                 # the cache absorbed the rerun

    events = []
    panel_b = [{"site_id": "B1"}]
    third = ra.screen_pool(panel_b, "functional", cache_path=cache,
                           on_event=lambda stage, sid, info: events.append(stage))
    assert third["from_cache"] is False
    assert third["cache_stale_refetched"] is True
    assert calls[-1] == [["B1"]][0]                # refetched the NEW panel
    assert "screening_cache_stale" in events

    fourth = ra.screen_pool(panel_b, "functional", cache_path=cache)
    assert fourth["from_cache"] is True            # rewritten cache now matches
    assert fourth["retained_ids"] == ["B1"]


def test_screen_pool_chunks_panels_beyond_the_engine_limit(tmp_path, monkeypatch):
    """The vendored EASI batch runner refuses requests over 150 sites; the
    pooled NRSA panels exceed that for four ecoregions (NEH at 186 failed
    live). screen_pool now splits the panel into engine-sized requests and
    merges the site lists; the merged batch caches whole."""
    calls = []

    def fake_direct(rows, preset, on_event=None):
        calls.append(len(rows))
        return {"sites": [{"site_id": r["site_id"]} for r in rows],
                "criteria": preset}

    def fake_tables(batch):
        return {"easi_screening_sites": [
            {"site_id": s["site_id"], "final_decision": "retained"}
            for s in batch.get("sites", [])]}

    monkeypatch.setattr(ra.easi_screening, "screen_sites_direct", fake_direct)
    monkeypatch.setattr(ra.easi_screening, "to_screening_tables", fake_tables)
    cache = tmp_path / "screening_cache_functional.json"
    panel = [{"site_id": f"s{i}"} for i in range(186)]

    res = ra.screen_pool(panel, "functional", cache_path=cache)
    assert calls == [150, 36]                     # engine-sized requests
    assert len(res["retained_ids"]) == 186        # nothing lost in the merge
    again = ra.screen_pool(panel, "functional", cache_path=cache)
    assert again["from_cache"] is True and calls == [150, 36]


# --------------------------------------------------------------------------- #
# Sample-size gate (DATA-04/05/06, calibrated v0.3)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("n,expected", [
    (25, "adequate"), (20, "adequate"),          # DATA-04: n >= 20
    (19, "exploratory"), (10, "exploratory"),    # DATA-05: 10 <= n < 20
    (9, "insufficient"), (5, "insufficient"),    # DATA-06: 5 <= n < 10
    (4, "too_few"), (None, "unknown"),           # engine hard floor / no data
])
def test_sample_size_disposition_calibrated_bands(n, expected):
    assert ra.sample_size_disposition(n) == expected


def test_run_offline_end_to_end_no_screen():
    """Full pipeline offline (do_screen=False): curves + a valid bundle, no network."""
    res = ra.run("58", "Northeastern Highlands", do_screen=False,
                 diagnostics_n_boot=20)
    assert res["n_candidates"] == 71
    assert res["screening_method"] == "unscreened_test"
    assert res["bundle"] is not None
    assert len(res["intended_metrics"]) > 0
    assert res["reference_pool_disposition"] == "adequate"   # n=71 under calibrated floors
    for mk in res["intended_metrics"]:
        assert res["sample_sizes"][mk]["disposition"] in (
            "adequate", "exploratory", "insufficient", "too_few", "unknown")
    # Wave 3: the run carries diagnostics, confidence, scores, and the
    # per-metric tier evaluation, all keyed consistently.
    assert set(res["confidence"]) == set(res["metric_config"])
    assert set(res["metric_scores"]) == set(res["metric_config"])
    assert res["diagnostics"]
    assert res["tier_evaluation"]
    for mk, c in res["confidence"].items():
        assert c["label"] in ("High", "Moderate", "Low"), mk


def test_portfolio_credits_every_function_a_metric_informs():
    """A metric listed under two functions must cover both.

    column_functions holds metric_map_function_label's FIRST match only; the
    bundle is built from the full set. Reading the label here made the run report
    name High flow dynamics as a gap in the very run whose bundle covered it.
    """
    cf = {"pctimp2019ws": "Hydrology: Catchment hydrology"}
    port = ra.compact_portfolio(["pctimp2019ws"], cf, {})
    by_fn = {p["function"]: p for p in port}
    assert by_fn["Catchment hydrology"]["coverage"] == "covered"
    assert by_fn["High flow dynamics"]["coverage"] == "covered", (
        "impervious cover informs High flow dynamics too")


def test_portfolio_agrees_with_the_bundles_coverage():
    """The report's two coverage numbers come from different code paths; they
    must not be able to contradict each other."""
    from streamcurves import deep_export as de

    codes = ["pctimp2019ws", "rddensws", "damdensws", "phab_BFWD_RAT"]
    cf = {c: ra.metric_map.metric_map_function_label(c) for c in codes}
    port = ra.compact_portfolio(codes, cf, {})
    from_portfolio = {p["function_id"] for p in port if p["coverage"] == "covered"}

    from_map = set()
    for c in codes:
        for f in ra.metric_map.metric_map_functions_for(c):
            fid = ra._canonical_function_id(f.get("function_name"))
            if fid:
                from_map.add(fid)
    assert from_portfolio == from_map
    assert len(de.deep_read_staf_crosswalk()) == len(port)
