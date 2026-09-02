"""Threading the predictor-source choice through the pipeline.

Clone of the NRSA-dataset wiring guards for the site-engine predictor source:
the digest distinguishes engine-sourced builds, the StreamCat default adds no
digest key (every published version keeps its byte-identical inputsDigest),
the CLI flag reaches the agent by direct attribute read, and the bundle stamp
follows the referenceTier pattern (bundle-level + per-metric, omitted for the
default).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from streamcurves import provenance as pv
from streamcurves import region_build as rb
from streamcurves import site_engine_source as ses
from streamcurves.deep_export import build_deep_assessment_bundle

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

_ENGINE_PS = {"source": "site-engine v0.1.0",
              "requestedFlag": "site-engine",
              "engine": {"id": "site-engine", "version": "0.1.0",
                         "vendorSha": "abc"}}


def _result(**over) -> dict:
    base = {
        "region": {"l3_code": "71", "name": "Interior Plateau"},
        "screening_method": "functional",
        "screening_counts": {"n_screened": 71, "n_retained": 33},
        "source_reports": [None],
    }
    base.update(over)
    return base


def _engine_result() -> dict:
    return _result(
        predictor_source="site-engine v0.1.0",
        predictor_source_flag="site-engine",
        source_reports=[None, {"source": "site_engine", "status": "ok",
                               "engine": _ENGINE_PS["engine"]}])


# --------------------------------------------------------------------------- #
# provenance + digest
# --------------------------------------------------------------------------- #
def test_the_digest_distinguishes_predictor_sources():
    default = pv.build_run_manifest(_result(), argv=[])
    engine = pv.build_run_manifest(_engine_result(), argv=[])
    assert default["inputsDigest"] != engine["inputsDigest"]


def test_the_streamcat_default_adds_no_digest_key():
    """Every published version was built on StreamCat predictors before this
    field existed, so the default must not contribute to the digest at all."""
    plain = pv.build_run_manifest(_result(), argv=[])
    named = pv.build_run_manifest(_result(predictor_source="streamcat"), argv=[])
    assert plain["inputsDigest"] == named["inputsDigest"]
    assert "predictor_source" not in plain["inputs"]


def test_the_manifest_records_the_engine_source():
    engine = pv.build_run_manifest(_engine_result(), argv=[])
    record = engine["inputs"]["predictor_source"]
    assert record["source"] == "site-engine v0.1.0"
    assert record["requestedFlag"] == "site-engine"
    assert record["engine"]["id"] == "site-engine"


# --------------------------------------------------------------------------- #
# CLI threading (source-level guards, the dataset-flag pattern)
# --------------------------------------------------------------------------- #
def test_the_flag_is_passed_through_not_just_parsed():
    text = (_SCRIPTS / "run_region_batch.py").read_text(encoding="utf-8")
    assert 'add_argument("--predictor-source"' in text
    # direct attribute read into run_evidence (a getattr fallback could let a
    # hand-built namespace silently revert to the default)
    assert "predictor_source=a.predictor_source," in text


def test_stage_many_hands_the_flag_to_each_stage():
    text = (_SCRIPTS / "run_region_batch.py").read_text(encoding="utf-8")
    # The hand-built stage-many Namespace ends with the flag; without it each
    # per-region stage would silently revert to the default.
    assert "predictor_source=a.predictor_source)" in text


def test_stage_command_carries_and_omits_the_flag():
    argv = rb.stage_command("71", "Interior Plateau", "out", maintainer="t",
                            predictor_source="site-engine")
    assert "--predictor-source" in argv
    assert argv[argv.index("--predictor-source") + 1] == "site-engine"
    default = rb.stage_command("71", "Interior Plateau", "out", maintainer="t")
    assert "--predictor-source" not in default


def test_restage_recovers_the_flag_from_the_manifest():
    manifest = {"inputs": {"predictor_source": _ENGINE_PS}}
    kwargs = rb.restage_args({}, manifest)
    assert kwargs["predictor_source"] == "site-engine"
    assert "predictor_source" not in rb.restage_args({}, {})


# --------------------------------------------------------------------------- #
# derivation + predictor swap
# --------------------------------------------------------------------------- #
def test_predictor_source_is_derived_from_columns():
    assert ses.predictor_source_of(["pctimp2019ws"]) == "streamcat"
    assert ses.predictor_source_of([]) == "streamcat"
    engine_only = ses.predictor_source_of(["se_pctimpws", "se_rddensws"])
    assert engine_only.startswith("site-engine v")
    mixed = ses.predictor_source_of(["se_pctimpws", "bfiws"])
    assert mixed.startswith("mixed (site-engine v")


def test_replace_predictors_swaps_analogs_and_keeps_the_rest():
    base = {"pctimp2019ws": {"display_name": "Impervious"},
            "bfiws": {"display_name": "Base-flow index"}}
    out = ses.replace_predictors(base, ["se_pctimpws", "se_wsareasqkm"])
    assert "pctimp2019ws" not in out           # analog swapped out
    assert "se_pctimpws" in out
    assert "se_wsareasqkm" in out              # engine-only addition
    assert "bfiws" in out                      # no analog: stays
    assert out["se_pctimpws"]["column_name"] == "se_pctimpws"


# --------------------------------------------------------------------------- #
# bundle stamp (the referenceTier pattern)
# --------------------------------------------------------------------------- #
def _one_metric_case():
    row = {
        "metric": "m", "display_name": np.nan, "higher_is_better": True,
        "curve_status": "complete", "stratum": np.nan,
        "curve_points": pd.DataFrame({"point_order": [1, 2],
                                      "metric_value": [0, 1],
                                      "index_score": [0, 1]}),
    }
    mapping = pd.DataFrame(
        {"metric_key": ["m"], "discipline": ["Hydrology"],
         "function_label": ["Catchment hydrology"], "sort_order": [1]})
    return {"m": row}, mapping


def test_bundle_stamps_engine_predictor_source():
    """Bundle-level from the build's derived source; per-metric only where the
    curve's own value_source says the engine computed its values (2026-09-02),
    so DEEP's pairing rule never refuses StreamCat values on a StreamCat-fitted
    curve inside an engine-sourced build."""
    rows, mapping = _one_metric_case()
    b = build_deep_assessment_bundle(
        rows, mapping, {}, {"predictorSource": "site-engine v0.1.0"})
    assert b["predictorSource"] == "site-engine v0.1.0"
    m = b["metricsByFunction"][0]["metrics"][0]
    assert "predictorSource" not in m
    b = build_deep_assessment_bundle(
        rows, mapping, {"m": {"value_source": "site-engine v0.1.0"}},
        {"predictorSource": "site-engine v0.1.0"})
    m = b["metricsByFunction"][0]["metrics"][0]
    assert m["predictorSource"] == "site-engine v0.1.0"


def test_bundle_omits_the_streamcat_default():
    rows, mapping = _one_metric_case()
    for meta in ({}, {"predictorSource": "streamcat"}, {"predictorSource": None}):
        b = build_deep_assessment_bundle(rows, mapping, {}, meta)
        assert "predictorSource" not in b
        m = b["metricsByFunction"][0]["metrics"][0]
        assert "predictorSource" not in m
