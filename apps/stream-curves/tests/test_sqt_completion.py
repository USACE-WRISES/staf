"""Completing the state SQT assessments (ADOPTION step 8, the owner's decisions of 2026-09-24).

Each ``*-sqt-adapted`` library assessment was migrated on 2026-07-12 with a stub session. Its next
version carries every v1 curve and stratum as a State SQT criterion under the function the metric
library gives it, documents every gap, approves the SQT's own metric sets and scores exactly as
v1 (the same content digest). Defects and open ends are stated, never fixed here.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from streamcurves import curve_basis
from streamcurves import curve_sources as cs
from streamcurves import deep_export as dx
from streamcurves import library as lib
from streamcurves import pressure_evidence as pe
from streamcurves import session_io as sio
from streamcurves import sqt_transcription as tr

APP = Path(__file__).resolve().parents[1]
REAL_LIBRARY = APP.parent / "library"
IDS = sorted(p.name for p in (REAL_LIBRARY / "assessments").glob("*" + tr.SUFFIX))


def _script():
    spec = importlib.util.spec_from_file_location("complete_sqt_under_test",
                                                  APP / "scripts" / "complete_sqt_assessments.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)
    return mod


def _v1(aid: str) -> dict:
    return json.loads((REAL_LIBRARY / "assessments" / aid / "v1" / "assessment.deep.json").read_text(
        encoding="utf-8"))


@pytest.fixture(scope="module")
def completed():
    records = json.loads(tr.REGISTRY.read_text(encoding="utf-8"))["records"]
    adapted = {a["assessmentId"]: a for a in json.loads(tr.ADAPTED.read_text(encoding="utf-8"))["assessments"]}
    return {aid: (_v1(aid), tr.complete(aid, _v1(aid), records=records,
                                        citation=adapted[aid]["sourceCitation"], by="ZZ", at="2026-09-24"))
            for aid in IDS}


def test_the_eight_state_sqts_are_there():
    assert IDS == [f"{s}-sqt-adapted" for s in ("ak", "co", "mi", "mn", "nc", "sc", "wi", "wy")]


def test_each_completion_scores_exactly_as_v1_and_passes_todays_gates(completed):
    for aid, (v1, done) in completed.items():
        assert lib.content_digest(done["bundle"]) == v1["contentDigest"], aid
        assert done["bundle"]["metricsByFunction"] == v1["metricsByFunction"], aid
        cov = done["bundle"]["functionCoverage"]
        assert cov["missing"] == 0 and cov["covered"] + cov["excluded"] == 20, aid
        lib._require_documented_coverage(aid, done["bundle"])
        lib._require_portfolio_approval(aid, done["bundle"], {"portfolioApprovals": done["approvals"]})
        with pytest.raises(ValueError, match="SELECT-01"):
            lib._require_portfolio_approval(aid, done["bundle"], {})


def test_every_curve_and_stratum_is_carried_where_v1_placed_it(completed):
    for aid, (v1, done) in completed.items():
        carried = done["fields"]["reference_build"]["carriedMetrics"]
        placed: dict = {}
        for block in v1["metricsByFunction"]:
            for m in block["metrics"]:
                placed.setdefault(m["metricId"], []).append(block["functionId"])
                row = carried[m["metricId"]]
                assert row["points"] == [{"x": float(p["x"]), "y": float(p["y"])}
                                         for p in (m.get("curve") or {}).get("points") or []]
                layers = m.get("curveLayers") or []
                assert [(L["stratum"], L["points"]) for L in row["layers"]] == \
                       [(L.get("stratum") or "", [{"x": float(p["x"]), "y": float(p["y"])} for p in L["points"]])
                        for L in layers]
                assert row["transcribed"]["bundleEntry"] == m
        assert {mk: c["transcribed"]["functions"] for mk, c in carried.items()} == placed, aid


def test_the_mapping_is_the_metric_librarys(completed):
    index = {m["metricId"]: m for m in json.loads((tr.METRIC_LIBRARY / "index.json").read_text(
        encoding="utf-8"))["metrics"]}
    names = tr.function_ids_by_name()
    for aid, (v1, done) in completed.items():
        for block in v1["metricsByFunction"]:
            for m in block["metrics"]:
                assert names[re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", index[m["metricId"]]["function"]
                                                         .lower().replace("&", " and "))).strip()] == \
                       block["functionId"], (aid, m["metricId"])


def test_the_counts_match_the_design_review(completed):
    layers = sum(len(c["annotations"]["sqtTranscription"]["layers"]) for _, done in completed.values()
                 for c in done["fields"]["reference_build"]["carriedMetrics"].values())
    defects = sum(len(done["provenance"]["defects"]) for _, done in completed.values())
    open_ended = sum(len(done["provenance"]["openEnds"]) for _, done in completed.values())
    reasons: dict = {}
    for _, done in completed.values():
        for e in done["fields"]["function_coverage_exceptions"]:
            reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    approvals = sum(len(done["approvals"]) for _, done in completed.values())
    assert (layers, defects, open_ended, approvals) == (386, 40, 92, 29)
    assert reasons == {"no-suitable-metric": 43, "deferred-to-other-tier": 23}


def test_a_defect_is_stated_on_its_curve_and_it_still_scores_as_v1(completed):
    _, done = completed["sc-sqt-adapted"]
    ecoli = done["fields"]["reference_build"]["carriedMetrics"]["water-and-soil-quality-e-coli"]
    assert any(c.startswith("The metric library row this curve was transcribed from is defective: The "
                            "field-value cells hold the SQT index levels")
               for c in ecoli["annotations"]["curveCaveats"])
    assert all(p["y"] == 1.0 for p in ecoli["points"])        # v1's scoring, kept on purpose
    layer = ecoli["annotations"]["sqtTranscription"]["layers"][0]
    assert layer["registryKey"] == "sqt:sc:e-coli:default" and layer["verification"] == "defective"


def test_the_completed_session_opens_on_its_curves_as_state_sqt_criteria(completed):
    script = _script()
    v1, done = completed["mn-sqt-adapted"]
    payload = script.session_payload(done["fields"], "MN SQT Adapted")
    back = sio.decode_session_fields(json.loads(sio.dumps_session(payload)))
    build = back["reference_build"]
    assert back["region_of_applicability"]["kind"] == "state" and back["discipline_function_mapping_confirmed"]
    rows = pe.reference_rows(build, back["discipline_function_mapping"])
    assert len(rows) == 22 and {r["kind"] for r in rows.values()} == {"carried"}
    assert {r["basis"] for r in rows.values()} == {curve_basis.PUBLISHED}
    mk = next(iter(rows))
    facts = dict(cs.source_facts(mk, rows[mk], build=build))
    assert facts["Original source"] == tr.SOURCE_LABEL
    assert facts["Criterion"] == "Minnesota Stream Quantification Tool"
    assert facts["Confidence"].startswith("Not scored")
    assert cs.chosen_by(mk, rows[mk]).startswith("Transcribed from the Minnesota Stream Quantification Tool in v1")
    assert tr.BASIS_LIMIT in cs.limits(rows[mk])


def test_a_project_with_curves_and_no_data_opens_on_its_curves():
    src = (APP / "views" / "project.py").read_text(encoding="utf-8")
    body = src[src.index("def _land(meta: dict):"):]
    body = body[:body.index("\n    async def ")]
    assert "reference_keys(state.reference_build())" in body
    assert body.index('_request_nav("curves")') < body.index('_request_nav("data", wizard_step')


def test_publishing_writes_the_next_version_scored_as_v1(tmp_path, monkeypatch):
    root = tmp_path / "library"
    shutil.copytree(REAL_LIBRARY / "assessments" / "mn-sqt-adapted", root / "assessments" / "mn-sqt-adapted")
    for extra in [p for p in (root / "assessments" / "mn-sqt-adapted").iterdir() if p.name.startswith("v")
                  and p.name != "v1"]:
        shutil.rmtree(extra)                              # the real one may hold a v2 already
    man_path = root / "assessments" / "mn-sqt-adapted" / "manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    man["versions"] = [v for v in man["versions"] if int(v["version"]) == 1]
    man["latestVersion"] = 1
    man_path.write_text(json.dumps(man, indent=2), encoding="utf-8")
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    script = _script()
    records = json.loads(tr.REGISTRY.read_text(encoding="utf-8"))["records"]
    got = script.prepare("mn-sqt-adapted", records=records,
                         adapted={"mn-sqt-adapted": {"sourceCitation": "Minnesota SQT"}}, by="ZZ",
                         at="2026-09-24")
    version = lib.publish_version("mn-sqt-adapted", got["meta"], got["payload"], got["bundle"],
                                  provenance=got["provenance"], status="preliminary")
    assert version == 2
    assert lib.load_version_bundle("mn-sqt-adapted", 2)["contentDigest"] == _v1("mn-sqt-adapted")["contentDigest"]
    assert lib.version_status("mn-sqt-adapted", 2) == "preliminary"
    meta = json.loads((root / "assessments" / "mn-sqt-adapted" / "v2" / "meta.json").read_text(encoding="utf-8"))
    assert {a["functionId"] for a in meta["portfolioApprovals"]} == {
        "carbon-processing", "channel-floodplain-dynamics", "habitat-provision"}
    session = sio.decode_session_fields(json.loads(
        (root / "assessments" / "mn-sqt-adapted" / "v2" / "session.streamcurves.json").read_text(encoding="utf-8")))
    assert len(session["reference_build"]["carriedMetrics"]) == 22
    prov = json.loads((root / "assessments" / "mn-sqt-adapted" / "v2" / "provenance.json").read_text(encoding="utf-8"))
    assert prov["kind"] == "sqt-transcription" and "pending" not in json.dumps(prov).lower()
