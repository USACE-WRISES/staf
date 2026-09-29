"""The candidate register and the rebuild ledger from a result, headless, agree with the
register the Reference Curves page computes from an open session and with the bundle.

On the seven latest published versions: the register built from the version's session
fields (``candidates.register_for_result``) equals the one built from the same fields
through ``views.curve_gallery.gallery_rows`` on an AppState, and its selected pairs equal
the bundle's ``metricsByFunction``; the ledger (``provenance.build_ledger_from_version``)
keeps invariants 1 to 5 of the rebuild-ledger schema.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from streamcurves import candidates as C
from streamcurves import curve_tiles as ct
from streamcurves import library as lib
from streamcurves import provenance as pv
from streamcurves import session_io as sio
from streamcurves.deep_export import deep_slug

APP = Path(__file__).resolve().parents[1]
LIBRARY = APP.parent / "library" / "assessments"
LATEST = ("interior-plateau/v6", "northeastern-highlands/v9", "eastern-corn-belt-plains/v8",
          "southeastern-plains/v2", "northern-lakes-and-forests/v1", "central-basin-and-range/v1",
          "central-great-plains/v1")
FIELDS = ("completed_metrics", "curve_review", "discipline_function_mapping", "metric_config",
          "region_of_applicability", "function_coverage_exceptions", "predictor_config",
          "reference_build", "session_name", "owner_curve_decisions", "candidate_register",
          "column_functions", "curve_stratification", "metric_phase_cache")


def _version(label: str):
    vdir = LIBRARY / label
    if not (vdir / lib.SESSION_FILE).is_file():
        pytest.skip(f"{label} is not in this checkout")
    fields = sio.decode_session_fields(sio.load_session_payload(vdir / lib.SESSION_FILE))
    bundle = json.loads((vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    return vdir, fields, bundle


def _bundle_pairs(bundle) -> set:
    return {(m["metricId"], f["functionId"]) for f in bundle["metricsByFunction"]
            for m in f.get("metrics") or []}


def _selected_pairs(reg) -> set:
    cands = {c["candidateKey"]: c for c in reg["candidates"]}
    return {("spring-" + deep_slug(cands[r["candidateKey"]]["identity"]["subject"]["id"]),
             r["functionId"]) for r in reg["rows"] if r["status"] == C.SELECTED}


@pytest.mark.parametrize("label", LATEST)
def test_the_headless_register_equals_the_pages_register(label):
    from shiny import reactive
    from views import curve_gallery as cg
    from views.state import AppState
    vdir, fields, bundle = _version(label)
    state = AppState.fresh()
    for name in FIELDS:
        if fields.get(name) is not None:
            getattr(state, name).set(fields[name])
    code = (fields.get("region_of_applicability") or {}).get("code")
    with reactive.isolate():
        from_state = C.deep_register(
            tiles=cg.gallery_rows(state, include_reference=True), build=state.reference_build(),
            decisions=state.owner_curve_decisions() or [], metric_config=state.metric_config() or {},
            register=state.candidate_register(),
            coverage_exceptions=state.function_coverage_exceptions() or [], region={"code": code})
    from_fields = C.register_for_result(fields)
    assert C.export_rows(from_fields) == C.export_rows(from_state)
    assert json.dumps(from_fields["candidates"], sort_keys=True, default=str) == \
        json.dumps(from_state["candidates"], sort_keys=True, default=str)
    assert from_fields["functions"] == from_state["functions"]
    assert _selected_pairs(from_fields) == _bundle_pairs(bundle)
    doc = C.register_document(from_fields)
    assert doc["schema"] == 1 and doc["rows"] == C.export_rows(from_fields)
    assert doc["counts"]["selected"] == len(_selected_pairs(from_fields)) == len(_bundle_pairs(bundle))


@pytest.mark.parametrize("label", LATEST)
def test_the_version_ledger_keeps_the_schemas_invariants(label):
    vdir, fields, bundle = _version(label)
    ledger = pv.build_ledger_from_version(vdir)
    assert ledger["schema"] == pv.LEDGER_SCHEMA
    assert ledger["region"]["code"] == fields["region_of_applicability"]["code"]
    doc = json.loads((vdir / lib.PROVENANCE_FILE).read_text(encoding="utf-8"))
    assert ledger["build"]["inputsDigest"] == doc["manifest"]["inputsDigest"]
    assert ledger["build"]["refit"] in ("missing", "all")
    rows = ledger["rows"]
    # 1: exactly one row per metric and function it concerns
    pairs = collections.Counter((r["metric"], r["functionId"]) for r in rows)
    assert all(n == 1 for n in pairs.values()), [k for k, n in pairs.items() if n > 1]
    for w in (fields["reference_build"].get("insufficientReferenceSupport") or []):
        for f in w.get("functions") or []:
            assert (w["metricKey"], f["functionId"]) in pairs, (label, w["metricKey"])
    for d in fields.get("owner_curve_decisions") or []:
        assert any(r["metric"] == d["metric"] for r in rows), (label, d["metric"])
    # 2: refitted means the engine ran; carried means it did not
    for r in rows:
        assert r["disposition"] in pv.LEDGER_DISPOSITIONS
        if r["disposition"] == pv.REFITTED:
            assert r["engine"]["executed"] and r["engine"]["seed"] is not None
            assert r["pool"]["nStations"] and r["pool"]["evidence"]
        if r["disposition"] == pv.CARRIED:
            assert not r["engine"]["executed"] and r["previousBasisDigest"] is not None
            assert r["fromVersion"] == fields["reference_build"]["carriedFrom"]["fromVersion"]
        # 3: changed is the digest comparison
        assert r["changed"] == (r["basisDigest"] != r["previousBasisDigest"])
        assert r["decidedBy"] in ("automated", "imported", "person")
    # 4: sorted by function then metric; no absolute path
    assert [(r["functionId"] or "", r["metric"]) for r in rows] == \
        sorted((r["functionId"] or "", r["metric"]) for r in rows)
    text = json.dumps(ledger)
    assert "D:\\\\" not in text and "D:/" not in text and "C:\\\\" not in text
    # 5: the selected set is the bundle's and the register's
    got = pv.ledger_rows_for_bundle(bundle, ledger)
    assert got["equal"], (label, got["missing"], got["extra"])
    assert set(got["selected"]) == _selected_pairs(C.register_for_result(fields))


def test_a_carried_curve_keeps_its_digest_across_versions():
    """The register digests a curve the same way whether the version fitted it or carries
    it (the direction from the curve's own config, each stratum once), so a carried
    row reads unchanged and only what the build refitted reads changed."""
    vdir, fields, bundle = _version("interior-plateau/v6")
    ledger = pv.build_ledger_from_version(vdir)
    carried = [r for r in ledger["rows"] if r["disposition"] == pv.CARRIED]
    assert carried and not any(r["changed"] for r in carried)
    previous = pv.version_basis_digests(vdir.parent / "v5")
    for r in carried:
        assert previous.get(r["metric"]) == r["basisDigest"], r["metric"]
    assert any(r["changed"] for r in ledger["rows"] if r["disposition"] == pv.REFITTED)


def test_tiles_for_fields_reads_a_result_and_a_session_alike():
    vdir, fields, bundle = _version("interior-plateau/v6")
    assert ct.is_session_shaped(fields)
    tiles = ct.tiles_for_fields(fields)
    fitted = [t for t in tiles if not t.get("read_only")]
    reference = [t for t in tiles if t.get("read_only")]
    assert {t["metric"] for t in fitted} == set(ct.eligible_metrics(fields["metric_config"]))
    assert {t["metric"] for t in reference} >= set(fields["reference_build"]["carriedMetrics"])
    for t in reference:
        seen = {(s["label"], tuple(map(tuple, s["points"]))) for s in t["strata"]}
        assert len(seen) == len(t["strata"]), t["metric"]
    from views import summary_state as ss
    assert ct.eligible_metrics(fields["metric_config"]) == ss.eligible_summary_metrics(fields["metric_config"])


def test_register_for_result_carries_considered_candidates():
    vdir, fields, bundle = _version("interior-plateau/v6")
    cand = {"candidateKey": "cand-000000000077", "identity": {"assessmentType": "deep",
            "subject": {"kind": "metric", "id": "sqt_x"}, "sourceKind": "sqt", "functionId": "habitat-provision"},
            "functions": ["habitat-provision"], "label": "A considered curve", "basisDigest": None,
            "eligibility": {"status": "eligible", "reasons": [], "checks": []}, "addedBy": "GM"}
    reg = C.register_for_result(fields, considered=[cand])
    keys = {c["candidateKey"] for c in reg["candidates"]}
    assert "cand-000000000077" in keys
    row = next(r for r in reg["rows"] if r["candidateKey"] == "cand-000000000077")
    assert row["status"] == C.NOT_EVALUATED and row["unresolved"] == "undecided"
    assert _selected_pairs(reg) == _bundle_pairs(bundle)
