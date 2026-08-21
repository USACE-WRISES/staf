"""Behavior adopted at the 2026-08-21 adversarial-review gate (methodology 0.6).

Seed geometry (domain clamps, declared signed scales, the flat low tail), the
per-region removal door, the DEEP-contract bands, the reviewer-decision
consistency check, the bundle annotations, and the published-library domain
regression. Each test names the review finding it answers.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from streamcurves import curves as cv
from streamcurves import deep_export
from streamcurves import methodology
from streamcurves import provenance as pv
from streamcurves import regional_agent as ra
from streamcurves import workbook

LIBRARY = Path(__file__).resolve().parents[2] / "library" / "assessments"


def _build(vals, entry):
    frame = pd.DataFrame({"m": vals})
    cfg = {"m": {**entry, "column_name": "m"}}
    res = cv.build_reference_curve(frame, "m", cfg, build_plots=False)
    pts = res["curve_points"]
    status = str(res["curve_row"].iloc[0]["curve_status"])
    plist = [{"x": float(r.metric_value), "y": float(r.index_score)}
             for r in pts.itertuples(index=False)]
    return plist, status


# --------------------------------------------------------------------------- #
# ECO-1: domain clamps
# --------------------------------------------------------------------------- #
def test_sinuosity_seed_clamps_to_its_physical_floor():
    """A straightened channel (sinuosity 1.0) must score Non-Functioning; the
    pre-clamp seed put the zero anchor below 1.0 where no stream exists."""
    vals = np.array([1.05, 1.08, 1.1, 1.12, 1.15, 1.2, 1.25, 1.3, 1.4, 1.5, 1.6, 1.9])
    pts, status = _build(vals, {"higher_is_better": True, "domain_min": 1.0})
    assert status == "complete"
    assert min(p["x"] for p in pts) >= 1.0
    assert cv.interp_curve(pts, 1.0) < 0.39
    assert cv.count_domain_violations(pd.DataFrame(
        {"point_order": range(1, len(pts) + 1), "metric_value": [p["x"] for p in pts],
         "index_score": [p["y"] for p in pts]}), 1.0, None) == 0


def test_percent_seed_clamps_to_100_and_keeps_the_tail_score():
    """Embeddedness cannot exceed 100 percent; a fully embedded bed scores 0."""
    vals = np.array([30, 40, 45, 50, 55, 60, 65, 70, 72, 75, 80, 85, 90, 95], dtype=float)
    pts, status = _build(vals, {"higher_is_better": False, "metric_family": "proportion",
                                "domain_min": 0, "domain_max": 100})
    assert status == "complete"
    assert max(p["x"] for p in pts) <= 100.0
    assert cv.interp_curve(pts, 100.0) <= 0.01


def test_clamp_collapses_duplicate_edge_anchors_and_renumbers():
    pts = pd.DataFrame({"point_order": [1, 2, 3, 4, 5],
                        "metric_value": [-2.0, -1.0, 0.5, 1.0, 1.5],
                        "index_score": [0.0, 0.3, 0.7, 1.0, 1.0]})
    out = cv.clamp_points_to_domain(pts, 0.0, None)
    assert out["metric_value"].tolist() == [0.0, 0.5, 1.0, 1.5]
    assert out["point_order"].tolist() == [1, 2, 3, 4]
    assert out["index_score"].tolist()[0] == 0.0  # the outermost anchor's score survives


def test_manual_points_outside_the_domain_are_rejected():
    pts = pd.DataFrame({"point_order": [1, 2, 3], "metric_value": [0.4, 1.1, 1.5],
                        "index_score": [0.0, 0.7, 1.0]})
    res = cv.validate_reference_curve_points(pts, True, domain=(1.0, None))
    assert not res["valid"]
    assert any("physical domain" in e for e in res["errors"])
    ok = cv.validate_reference_curve_points(pts, True, domain=(0.0, None))
    assert ok["valid"]


def test_metric_domain_of_reads_the_registry_fields():
    assert cv.metric_domain_of({"domain_min": 1.0}) == (1.0, None)
    assert cv.metric_domain_of({"domain_min": "0", "domain_max": 100}) == (0.0, 100.0)
    assert cv.metric_domain_of({}) == (None, None)
    assert cv.metric_domain_of(None) == (None, None)


# --------------------------------------------------------------------------- #
# STAT-9: the declared scale wins over the sample minimum
# --------------------------------------------------------------------------- #
def test_declared_signed_scale_keeps_the_form_when_the_sample_minimum_flips_sign():
    base = np.array([-1.2, -0.9, -0.6, -0.4, -0.2, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    shifted = base + 1.3  # no negative value left
    pts_a, _ = _build(base, {"higher_is_better": True, "signed_scale": True})
    pts_b, _ = _build(shifted, {"higher_is_better": True, "signed_scale": True})
    # Same seed geometry (the scale-free ladder), just translated.
    assert len(pts_a) == len(pts_b)
    dx = [b["x"] - a["x"] for a, b in zip(pts_a, pts_b)]
    assert max(dx) - min(dx) < 1e-9
    # Without the declaration the inference from the sample minimum changes the form.
    pts_c, _ = _build(shifted, {"higher_is_better": True})
    assert len(pts_c) != len(pts_b) or any(abs(c["x"] - b["x"]) > 1e-9 for c, b in zip(pts_c, pts_b))


# --------------------------------------------------------------------------- #
# ECO-7: the flat low tail
# --------------------------------------------------------------------------- #
def test_optimum_flat_low_tail_holds_the_functioning_edge():
    vals = np.array([12, 15, 18, 19, 20, 22, 24, 26, 28, 30, 32, 35, 40, 45], dtype=float)
    pts, status = _build(vals, {"higher_is_better": None, "curve_form": "optimum",
                                "low_tail": "flat", "domain_min": 0, "domain_max": 180})
    assert status == "complete"
    assert [p["y"] for p in pts] == [0.70, 0.70, 1.0, 1.0, 0.70, 0.30, 0.00]
    assert cv.interp_curve(pts, 5.0) >= 0.69          # a regraded gentle bank stays Functioning
    assert cv.interp_curve(pts, 120.0) < 0.39         # a steep, incising bank still fails
    assert max(p["x"] for p in pts) <= 180.0


# --------------------------------------------------------------------------- #
# ECO-8 / RPT-8: DEEP-contract bands are one-sided for monotone curves
# --------------------------------------------------------------------------- #
def test_deep_contract_bands_are_one_sided():
    rising = [{"x": 0.0, "y": 0.0}, {"x": 3.0, "y": 0.3}, {"x": 7.0, "y": 0.7},
              {"x": 14.0, "y": 1.0}, {"x": 16.1, "y": 1.0}]
    b = cv.deep_contract_bands(rising, curve_form="monotone", higher_is_better=True,
                               domain=(0.0, None))
    assert b["band_semantics"] == "one_sided_rising"
    assert b["deep_functioning_max"] is None                # open above
    assert abs(b["deep_functioning_min"] - 6.9) < 1e-9       # the 0.69 crossing
    assert b["deep_not_functioning_min"] == 0.0             # the domain edge closes it
    assert b["functioning_text"].endswith("or more")
    falling = [{"x": 0.0, "y": 1.0}, {"x": 10.0, "y": 1.0}, {"x": 20.0, "y": 0.7},
               {"x": 30.0, "y": 0.3}, {"x": 40.0, "y": 0.0}]
    f = cv.deep_contract_bands(falling, curve_form="monotone", higher_is_better=False,
                               domain=(0.0, 100.0))
    assert f["band_semantics"] == "one_sided_falling"
    assert f["deep_functioning_min"] == 0.0
    assert f["functioning_text"].endswith("or less")
    assert f["deep_not_functioning_max"] == 100.0


def test_deep_contract_bands_two_sided_core():
    pts = [{"x": 0.0, "y": 0.0}, {"x": 2.0, "y": 0.3}, {"x": 4.0, "y": 0.7}, {"x": 5.0, "y": 1.0},
           {"x": 7.0, "y": 1.0}, {"x": 8.0, "y": 0.7}, {"x": 10.0, "y": 0.3}, {"x": 12.0, "y": 0.0}]
    b = cv.deep_contract_bands(pts, curve_form="optimum", higher_is_better=None)
    assert b["band_semantics"] == "two_sided"
    assert b["functioning_text"].startswith("between")
    assert b["deep_functioning_min"] < 5.0 < 7.0 < b["deep_functioning_max"]
    assert b["deep_not_functioning_high_min"] is not None


# --------------------------------------------------------------------------- #
# STAT-8: the exploratory band stays at tier
# --------------------------------------------------------------------------- #
def test_choose_reference_tier_keeps_an_exploratory_pool_at_tier(monkeypatch):
    calls = []

    def fake_screen(rows, preset, on_event=None, cache_path=None):
        calls.append(preset)
        n = 15 if preset == "functional" else 40
        return {"retained_ids": [f"s{i}" for i in range(n)],
                "counts": {"n_screened": 60, "n_retained": n}, "preset": preset,
                "tables": {}}

    monkeypatch.setattr(ra, "screen_pool", fake_screen)
    res = ra.choose_reference_tier([{"site_id": f"s{i}", "lat": 0.0, "lon": 0.0}
                                    for i in range(60)], "functional")
    assert calls == ["functional"]                     # no fallback screen ran
    assert res["reference_tier"] == ra.TIER_LEAST_DISTURBED
    assert res["ref02_triggered"] is False
    assert any("exploratory" in f for f in res["review_flags"])


# --------------------------------------------------------------------------- #
# VAL-6: a reviewer rationale cannot contradict its record
# --------------------------------------------------------------------------- #
def _doc_with_curve04():
    rec = pv._record("run", "55", "CURVE-04", "metric", "phab_PCT_FAST",
                     computed={"max_param_change_frac": 0.15, "decision_flip": True,
                               "driver": "NRS18_OH_10050"},
                     verdict=pv.VERDICT_REVIEW, review_required=True,
                     review_triggers=["influential_site"])
    queue = pv.build_review_queue([rec], {"inputsDigest": "x"})
    return {"records": [rec], "reviewQueue": queue}


def test_templated_phrase_contradicting_the_record_raises():
    doc = _doc_with_curve04()
    with pytest.raises(ValueError, match="contradict"):
        pv.apply_reviewer_decisions(doc, [{
            "rule_id": "CURVE-04", "subject": "phab_PCT_FAST", "action": "accept",
            "rationale": "Accepted with the flag: no decision flip.",
        }])


def test_explicit_asserts_are_checked_and_fields_pass_through():
    doc = _doc_with_curve04()
    with pytest.raises(ValueError, match="asserts decision_flip=False"):
        pv.apply_reviewer_decisions(doc, [{
            "rule_id": "CURVE-04", "subject": "phab_PCT_FAST", "action": "accept",
            "rationale": "Accepted.", "asserts": {"decision_flip": False},
        }])
    doc = _doc_with_curve04()
    out = pv.apply_reviewer_decisions(doc, [{
        "rule_id": "CURVE-04", "subject": "phab_PCT_FAST", "action": "accept",
        "rationale": "Accepted with the flag. Decision flip: yes, the drop of "
                     "NRS18_OH_10050 changes the build.",
        "asserts": {"decision_flip": True, "driver": "NRS18_OH_10050"},
        "decision_class": "curve04-accept-with-flag",
        "rationale_origin": "ai_drafted_owner_approved",
    }], default_reviewer="tester")
    rec = out["records"][0]
    assert rec["reviewer_action"] == "accept"
    assert rec["reviewer_decision_class"] == "curve04-accept-with-flag"
    assert rec["reviewer_rationale_origin"] == "ai_drafted_owner_approved"
    assert rec["reviewer_asserts"] == {"decision_flip": True, "driver": "NRS18_OH_10050"}
    assert out["reviewQueue"]["counts"]["open"] == 0


def test_every_record_carries_the_new_reviewer_fields():
    rec = pv._record("run", "55", "CURVE-01", "metric", "m")
    for field in ("reviewer_decision_class", "reviewer_rationale_origin", "reviewer_asserts"):
        assert field in rec and field in pv.RULE_RECORD_FIELDS


# --------------------------------------------------------------------------- #
# Bundle annotations (ECO-5, ECO-10, STAT-6, ECO-14)
# --------------------------------------------------------------------------- #
def test_metric_annotations_carry_role_sample_and_caveats():
    intended = ["thin", "land"]
    curve_rows = {"thin": {"n_reference": 9}, "land": {"n_reference": 33}}
    metric_config = {"thin": {"metric_role": "response"},
                     "land": {"metric_role": "stressor_surrogate", "caveat": "Units caution."}}
    sample_sizes = {"thin": {"disposition": "insufficient"}, "land": {"disposition": "adequate"}}
    confidence = {"thin": {"label": "Low", "total": 40.0}, "land": {"label": "High", "total": 90.0}}
    gradients = {"land": {"stratification": "DrainageAreaClass",
                          "cv_error_improvement": 0.36, "resample_support": 0.96}}
    ann = ra.metric_annotations(intended=intended, curve_rows=curve_rows,
                                metric_config=metric_config, sample_sizes=sample_sizes,
                                confidence_map=confidence, deferred_gradients=gradients)
    assert ann["thin"]["referenceN"] == 9 and ann["thin"]["sampleDisposition"] == "insufficient"
    assert any("read the condition band" in c for c in ann["thin"]["curveCaveats"])
    assert ann["land"]["metricRole"] == "stressor_surrogate"
    texts = " ".join(ann["land"]["curveCaveats"])
    assert "stressor surrogate" in texts and "DrainageAreaClass" in texts and "Units caution." in texts
    assert ann["land"]["confidenceLabel"] == "High"


def test_bundle_exporter_writes_the_annotations():
    row = {"metric": "m", "curve_status": "complete",
           "curve_points": pd.DataFrame({"point_order": [1, 2, 3],
                                         "metric_value": [0.0, 5.0, 10.0],
                                         "index_score": [0.0, 0.7, 1.0]})}
    mapping = pd.DataFrame([{"metric_key": "m", "discipline": "Hydrology",
                             "function_label": "Catchment hydrology"}])
    cfg = {"m": {"display_name": "M", "units": "%", "metric_family": "proportion",
                 "higher_is_better": True}}
    meta = {"assessmentId": "t", "assessmentName": "T", "sourceCitation": "x",
            "referenceTier": "best_available",
            "metricAnnotations": {"m": {"referenceN": 9, "sampleDisposition": "insufficient",
                                        "metricRole": "stressor_surrogate",
                                        "curveCaveats": ["read the band"],
                                        "confidenceLabel": "Low", "confidenceTotal": 50.0}}}
    bundle = deep_export.build_deep_assessment_bundle({"m": row}, mapping, cfg, meta)
    entry = bundle["metricsByFunction"][0]["metrics"][0]
    assert entry["referenceN"] == 9 and entry["sampleDisposition"] == "insufficient"
    assert entry["metricRole"] == "stressor_surrogate"
    assert entry["curveCaveats"] == ["read the band"]
    assert entry["confidenceLabel"] == "Low" and entry["referenceTier"] == "best_available"


# --------------------------------------------------------------------------- #
# A7: the interactive path carries the curated fields through the workbook
# --------------------------------------------------------------------------- #
def test_workbook_round_trip_keeps_the_curated_geometry_fields():
    cfg = {"phab_SINU": {"display_name": "Sinuosity", "column_name": "phab_SINU",
                         "units": "", "metric_family": "continuous", "higher_is_better": True,
                         "expected_shape": "monotone_increasing", "transformation": "none",
                         "domain_min": 1.0, "direction_source": "expert",
                         "direction_confidence": "moderate", "notes": ""},
           "phab_LRBS_use": {"display_name": "LRBS", "column_name": "phab_LRBS_use",
                             "units": "", "metric_family": "continuous",
                             "higher_is_better": None, "curve_form": "optimum",
                             "signed_scale": True, "expected_shape": "optimum",
                             "transformation": "none", "notes": ""},
           "phab_XBKA": {"display_name": "Bank angle", "column_name": "phab_XBKA",
                         "units": "deg", "metric_family": "continuous",
                         "higher_is_better": None, "curve_form": "optimum",
                         "low_tail": "flat", "domain_min": 0.0, "domain_max": 180.0,
                         "expected_shape": "optimum", "transformation": "none", "notes": ""}}
    data = pd.DataFrame({"phab_SINU": [1.1, 1.2], "phab_LRBS_use": [-0.5, 0.2],
                         "phab_XBKA": [20.0, 30.0]})
    tables = workbook.tables_from_configs(data, cfg, {}, {})
    back = workbook.build_metric_config_from_workbook(
        tables["metrics"], tables["metric_predictors"], tables["metric_stratifications"])
    assert back["phab_SINU"]["domain_min"] == 1.0 and "domain_max" not in back["phab_SINU"]
    assert back["phab_SINU"]["expected_shape"] == "monotone_increasing"
    assert back["phab_LRBS_use"]["signed_scale"] is True
    assert back["phab_LRBS_use"]["curve_form"] == "optimum"
    assert back["phab_XBKA"]["low_tail"] == "flat"
    assert back["phab_XBKA"]["domain_max"] == 180.0
    # The overlay whitelist restores them on a role rebuild too.
    regenerated = {"metrics": tables["metrics"].drop(
        columns=["domain_min", "domain_max", "low_tail", "signed_scale", "expected_shape"])}
    restored = workbook.overlay_metric_settings(regenerated, cfg)["metrics"]
    assert "low_tail" in restored.columns and "signed_scale" in restored.columns


# --------------------------------------------------------------------------- #
# Published-library regression: no anchor outside a declared domain
# --------------------------------------------------------------------------- #
def _declared_domains():
    out = {}
    for entry_map in (ra.load_directions(), ra.load_landscape_directions()):
        for code, d in (entry_map or {}).items():
            if not isinstance(d, dict):
                continue
            dom = cv.metric_domain_of(d)
            if dom != (None, None):
                out[code.lower()] = dom
    return out


@pytest.mark.parametrize("assessment", ["northeastern-highlands", "eastern-corn-belt-plains"])
def test_latest_published_bundle_has_no_anchor_outside_its_domain(assessment):
    """ECO-1 acceptance criterion, over the library's default version of each
    pilot. Versions published before the clamps (NEH v3, ECBP v2) are expected
    to fail this and are superseded; only the default version is checked."""
    catalog_path = LIBRARY.parent / "catalog.json"
    if not catalog_path.exists():
        pytest.skip("assessment library not present")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    entry = next((a for a in catalog["assessments"] if a["assessmentId"] == assessment), None)
    if not entry or not entry.get("defaultVersion"):
        pytest.skip(f"{assessment} not in the catalog")
    version = int(entry["defaultVersion"])
    if (assessment, version) in {("northeastern-highlands", 3), ("eastern-corn-belt-plains", 2)}:
        pytest.skip("pre-clamp version still the default (republish pending)")
    bundle = json.loads((LIBRARY / assessment / f"v{version}" / "assessment.deep.json")
                        .read_text(encoding="utf-8"))
    domains = _declared_domains()
    checked = 0
    for block in bundle["metricsByFunction"]:
        for m in block["metrics"]:
            code = m["metricId"].replace("spring-", "").replace("-", "_").lower()
            dom = next((v for k, v in domains.items() if k.replace("-", "_") == code
                        or code.endswith(k.replace("-", "_"))), None)
            if dom is None:
                continue
            checked += 1
            pts = pd.DataFrame({"point_order": range(1, len(m["curve"]["points"]) + 1),
                                "metric_value": [p["x"] for p in m["curve"]["points"]],
                                "index_score": [p["y"] for p in m["curve"]["points"]]})
            assert cv.count_domain_violations(pts, dom[0], dom[1]) == 0, m["metricId"]
    assert checked > 0


def test_prose_documents_agree_on_the_methodology_version():
    """The three governed prose documents live in the notes folder (not tracked);
    when present they must carry the config's version."""
    notes = Path(__file__).resolve().parents[3] / "notes" / "2026-07-23_StreamCurves_Methodology"
    if not notes.is_dir():
        pytest.skip("methodology notes folder not present")
    version = methodology.methodology_version()
    for name in ("methodology.md", "README.md", "regional_analysis_prompt.md"):
        text = (notes / name).read_text(encoding="utf-8")
        assert version in text, f"{name} does not state methodology {version}"
