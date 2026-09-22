"""The curves a session scores without having fitted them (methodology 0.14).

An opened agent build shows, counts and republishes every curve its bundle
scores: the curves carried forward from the published version, the curves from
a rung above the hierarchy (national, modeled, published benchmark) and the
fixed criteria. None of them sits in ``metric_config``; they live in the
session's ``reference_build`` and ``pressure_evidence.apply_reference_build``
folds them into the bundle. These tests hold the workspace to the bundle, on
the published versions in the canonical library as fixtures.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from streamcurves import carry_forward as cf
from streamcurves import deep_export as dx
from streamcurves import library as lib
from streamcurves import mapping as M
from streamcurves import pressure_evidence as pe
from streamcurves import region_build as rb
from streamcurves import run_state as rs
from streamcurves import session_io as sio
from views import curve_gallery as cg

LIBRARY = Path(__file__).resolve().parents[2] / "library" / "assessments"
#: what each version scores without its session having fitted it
CASES = {
    "northeastern-highlands/v9": {"carried": 23, "fromVersion": 8, "fixed": 5},
    "interior-plateau/v6": {"carried": 22, "fromVersion": 5, "fixed": 5},
    "eastern-corn-belt-plains/v7": {"modeled": 2, "published_benchmark": 2, "fixed": 5},
    "southeastern-plains/v1": {"fixed": 5},
}


def _load(version: str):
    vdir = LIBRARY / version
    if not (vdir / lib.SESSION_FILE).is_file():
        pytest.skip(f"{version} is not in this checkout")
    bundle = json.loads((vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    fields = sio.decode_session_fields(sio.load_session_payload(vdir / lib.SESSION_FILE))
    return bundle, fields


def _in_scope(fields) -> dict:
    """The scope rule build_bundle_from_state applies."""
    review = fields.get("curve_review") or {}
    return {mk: cm for mk, cm in (fields.get("completed_metrics") or {}).items()
            if mk not in review or rs.is_in_scope(review[mk])}


def _blocks(bundle) -> dict:
    return {str(b["functionId"]): sorted(str(m["metricId"]) for m in b.get("metrics") or [])
            for b in bundle.get("metricsByFunction") or [] if b.get("metrics")}


def _entries(bundle) -> dict:
    return {str(m["metricId"]): m for b in bundle.get("metricsByFunction") or []
            for m in b.get("metrics") or []}


@pytest.mark.parametrize("version", sorted(CASES))
def test_reference_rows_name_every_curve_the_session_did_not_fit(version):
    _bundle, fields = _load(version)
    build = fields["reference_build"]
    built = fields.get("completed_metrics") or {}
    assert pe.reference_summary(build, built=built) == CASES[version]
    rows = pe.reference_rows(build, fields["discipline_function_mapping"], built=built)
    assert set(rows) == set(pe.reference_keys(build))
    for mk, entry in rows.items():
        stored = ((build.get("carriedMetrics") or {}).get(mk)
                  or (build.get("ladderMetrics") or {}).get(mk))
        if entry["kind"] == "fixed":
            assert entry["label"] == "Fixed criterion"
            continue
        # the published points themselves, never refitted
        pts = entry["row"]["curve_points"]
        assert [[float(x), float(y)] for x, y in zip(pts["metric_value"], pts["index_score"])] \
            == [[float(p["x"]), float(p["y"])] for p in stored["points"]]
        if entry["kind"] == "carried":
            assert entry["label"] == f"Carried from v{CASES[version]['fromVersion']}"


@pytest.mark.parametrize("version", sorted(CASES))
def test_quick_coverage_and_counts_equal_the_published_bundle(version):
    """The strip, the mapping page and the step 6 checklist judge coverage and the
    SELECT-01 count without building a bundle. With the reference curves counted
    and SELECT-04's not-selected pairs left out, they equal the bundle."""
    bundle, fields = _load(version)
    build, mapping = fields["reference_build"], fields["discipline_function_mapping"]
    built = fields.get("completed_metrics") or {}
    cov = dx.function_coverage_quick(
        _in_scope(fields), mapping, fields.get("function_coverage_exceptions") or [],
        always_covered=pe.reference_function_ids(build, mapping, built=built),
        exclude_pairs=pe.not_selected_pairs(build))
    want = bundle["functionCoverage"]
    assert set(cov["coveredFunctionIds"]) == set(want["coveredFunctionIds"])
    assert cov["missing"] == want["missing"] == 0
    counts = dx.metrics_per_function_quick(
        _in_scope(fields), mapping,
        extra=pe.reference_metric_counts(build, mapping, built=built),
        exclude_pairs=pe.not_selected_pairs(build))
    assert {f: n for f, n in counts.items() if n} == {f: len(m) for f, m in _blocks(bundle).items()}


@pytest.mark.parametrize("version", sorted(CASES))
def test_opening_a_session_keeps_the_rows_that_place_its_curves(version):
    """The realign that runs on open drops rows for metrics not in the workbook.
    The rows of the curves the session did not fit are kept, and a mapping with
    nothing to add or drop comes back unchanged (so the mapping stays
    confirmed and the revision record logs no edit)."""
    _bundle, fields = _load(version)
    mapping = fields["discipline_function_mapping"]
    keep = pe.reference_keys(fields["reference_build"])
    res = M.realign_discipline_function_mapping(mapping, list(fields["metric_config"]), keep)
    assert res["dropped"] == []
    before = set(mapping["metric_key"].astype(str)) & set(keep)
    assert before <= set(res["mapping"]["metric_key"].astype(str))
    if not res["added"]:
        assert res["mapping"] is mapping
    # without the keep list the ladder rows would go: the loss this fixes
    dropped = M.realign_discipline_function_mapping(mapping, list(fields["metric_config"]))
    assert set(dropped["dropped"]) == before


@pytest.mark.parametrize("version", sorted(CASES))
def test_an_opened_version_republishes_what_it_published(version):
    """build_bundle_from_state's path on an opened version: realign as the app
    does, scope, the reference build folded in. Every function carries the same
    metrics with the same curves as the published bundle. Before the realign
    kept the ladder rows, ECBP v7 lost its four modeled and benchmark curves."""
    bundle, fields = _load(version)
    build = fields["reference_build"]
    mapping = M.realign_discipline_function_mapping(
        fields["discipline_function_mapping"], list(fields["metric_config"]),
        pe.reference_keys(build))["mapping"]
    rows = dx.deep_collect_curve_rows(_in_scope(fields))
    meta = {"assessmentId": "t", "assessmentName": "T", "sourceCitation": "t",
            "region": fields.get("region_of_applicability"),
            "functionCoverageExceptions": fields.get("function_coverage_exceptions") or []}
    rows, mapping, config = pe.apply_reference_build(build, rows, mapping,
                                                     fields["metric_config"], meta)
    rebuilt = dx.build_deep_assessment_bundle(rows, mapping, config, meta)
    assert _blocks(rebuilt) == _blocks(bundle)
    got, want = _entries(rebuilt), _entries(bundle)
    for mid, m in want.items():
        assert got[mid]["curve"]["points"] == m["curve"]["points"], mid


@pytest.mark.parametrize("version", sorted(CASES))
def test_the_workspace_draws_every_curve_it_did_not_fit_read_only(version):
    bundle, fields = _load(version)
    build, mapping = fields["reference_build"], fields["discipline_function_mapping"]
    built = fields.get("completed_metrics") or {}
    tiles = cg.reference_tiles_for(build, mapping, built=built)
    assert {t["metric"] for t in tiles} == set(pe.reference_keys(build))
    ids = {"spring-" + dx.deep_slug(t["metric"]) for t in tiles if t["in_scope"] is not False}
    assert ids <= set(_entries(bundle))
    rows = pe.reference_rows(build, mapping, built=built)
    for t in tiles:
        ann = rows[t["metric"]]["annotations"]
        stated = ann.get("referenceN")
        if stated is None:
            stated = (ann.get("referenceSupport") or {}).get("nUsable")
        if t["source_kind"] in ("published_benchmark", "fixed") \
                or ann.get("basis") == "published-benchmark":
            assert t["reference_n"] is None, t["metric"]      # a criterion has no sample
        elif stated is not None:
            # the count the bundle states, never a raw count off the session row
            assert t["reference_n"] == float(stated), t["metric"]
        assert t["read_only"] and t["badge"] and cs_status(t) == t["status_text"]
        assert t.get("function_name"), t["metric"]
        html = _unescape(str(cg.tile_ui(t, channel_id="ch")))
        assert '"action": "open"' not in html and "curve-tile-recompute" not in html
        assert ("remove_carried" in html) == bool(t.get("removable"))
        assert ("curve-tile-remove" in html) == (t["source_kind"] == "carried")


def cs_status(tile) -> str:
    from streamcurves import curve_svg as cs
    return cs.status_label(tile)


def _unescape(html: str) -> str:
    return html.replace("&apos;", "'").replace("&quot;", '"')


def test_a_pending_removal_shows_on_the_tile():
    _bundle, fields = _load("interior-plateau/v6")
    build = fields["reference_build"]
    mk = sorted(build["carriedMetrics"])[0]
    tiles = {t["metric"]: t for t in cg.reference_tiles_for(
        build, fields["discipline_function_mapping"], pending=[mk])}
    assert tiles[mk]["pending_removal"] and tiles[mk]["status_text"] == "Removal pending"
    html = _unescape(str(cg.tile_ui(tiles[mk], channel_id="ch")))
    assert "undo_remove" in html and "is-removal-pending" in html


def test_the_counts_read_as_one_sentence():
    assert pe.reference_summary_text({"carried": 19, "fromVersion": 7, "fixed": 5}) \
        == "19 carried from v7 and 5 fixed criteria"
    assert pe.reference_summary_text({"national": 2, "modeled": 1, "published_benchmark": 2,
                                      "fixed": 5}) \
        == "2 national, 1 modeled, 2 published benchmark and 5 fixed criteria"
    assert pe.reference_summary_text({"fixed": 1}) == "1 fixed criterion"
    assert pe.reference_summary_text({}) == ""


def test_the_curves_stage_names_the_curves_it_did_not_fit():
    snap = {"enriched": True, "reference_text": "19 carried from v7 and 5 fixed criteria",
            "curve_review": {"m": {"status": rs.CURVE_STATUS_AUTO_OK,
                                   "decision": rs.DECISION_AUTO}}}
    detail = rs.derive_stage_status(snap)["curve_review"]["detail"]
    assert detail == "1 curve(s) in scope. Plus 19 carried from v7 and 5 fixed criteria."


def test_a_reset_of_the_mapping_keeps_the_reference_rows():
    from views.discipline_map import with_reference_rows
    old = pd.DataFrame({"metric_key": ["a", "chem_PTL", "lib:x"],
                        "discipline": ["Hydrology", "Physicochemistry", None],
                        "function_label": ["Streamflow regime", "Nutrient cycling", None],
                        "sort_order": [1, 2, 3]})
    new = pd.DataFrame({"metric_key": ["a"], "discipline": ["Hydrology"],
                        "function_label": [None], "sort_order": [1]})
    out = with_reference_rows(new, old, ["chem_PTL"])
    assert out["metric_key"].tolist() == ["a", "chem_PTL"]
    assert out["sort_order"].tolist() == [1, 2]
    assert with_reference_rows(new, old, []) is new


# --------------------------------------------------------------------------- #
# Removing a carried curve (owner decision 2026-09-21)
# --------------------------------------------------------------------------- #
def test_removals_are_saved_loaded_and_undone(tmp_path):
    with pytest.raises(ValueError, match="at least"):
        rb.save_removal(tmp_path, "bfiws", "too short", recorded_by="owner")
    with pytest.raises(ValueError, match="named owner"):
        rb.save_removal(tmp_path, "bfiws", "x" * rb.MIN_REMOVAL_RATIONALE, recorded_by="")
    rb.save_removal(tmp_path, "bfiws", "The carried curve rests on a pool we no longer trust.",
                    recorded_by="owner", recorded_at="2026-09-21T12:00:00+00:00")
    rb.save_removal(tmp_path, "chem_PH", "A second removal, recorded for the next build.",
                    recorded_by="owner")
    got = rb.load_removals(tmp_path)
    assert [d["metric"] for d in got] == ["bfiws", "chem_PH"]
    assert got[0] == {"metric": "bfiws", "recordedBy": "owner",
                      "rationale": "The carried curve rests on a pool we no longer trust.",
                      "recordedAt": "2026-09-21T12:00:00+00:00"}
    rb.clear_removal(tmp_path, "bfiws")
    rb.clear_removal(tmp_path, "chem_PH")
    assert rb.load_removals(tmp_path) == []
    assert not (tmp_path / rb.REMOVALS_FILE).exists()


def test_a_removal_is_a_build_input_until_the_curve_is_gone(tmp_path):
    argv = rb.stage_command("55", "Eastern Corn Belt Plains", tmp_path, maintainer="owner",
                            remove_metrics={"bfiws": "why it goes"})
    i = argv.index("--remove-metric")
    assert argv[i + 1] == "bfiws=why it goes"
    keep, stale = rb.removal_inputs(
        [{"metric": "bfiws", "rationale": "why"}, {"metric": "gone", "rationale": "old"}],
        {"spring-bfiws", "spring-other"})
    assert keep == {"bfiws": "why"} and stale == ["gone"]
    assert rb.removal_inputs([{"metric": "gone", "rationale": "old"}], None) == ({"gone": "old"}, [])


def test_assemble_takes_a_carried_curve_out_on_the_owners_removal():
    from streamcurves import regional_agent as ra
    evidence = {"carried": {"bfiws": {"row": {}}, "chem_PH": {"row": {}}},
                "reference_support": {"bfiws": {"status": "carried"}, "x": {}}}
    out, removed = ra.without_removed_carried(evidence, {"bfiws": "why", "built_one": "n"},
                                              curve_review={"built_one": {}}, actor="owner")
    assert removed == {"bfiws": "why"}
    assert set(out["carried"]) == {"chem_PH"} and set(out["reference_support"]) == {"x"}
    assert set(evidence["carried"]) == {"bfiws", "chem_PH"}      # the evidence is not mutated
    with pytest.raises(ValueError, match="named finalize_actor"):
        ra.without_removed_carried(evidence, {"bfiws": "why"}, curve_review={}, actor="")
    same, none = ra.without_removed_carried(evidence, None, curve_review={}, actor="")
    assert same is evidence and none == {}


# --------------------------------------------------------------------------- #
# A SELECT-01 approval carries with an unchanged set (owner decision 2026-09-21)
# --------------------------------------------------------------------------- #
def _block(fid, metrics):
    return {"functionId": fid, "metrics": [
        {"metricId": mid, "curve": {"points": [{"x": x, "y": y} for x, y in pts]}}
        for mid, pts in metrics]}


def test_an_approval_carries_only_with_its_exact_set():
    fid = "low-flow-baseflow-dynamics"
    prior_bundle = {"metricsByFunction": [_block(fid, [
        ("spring-a", [(0, 0), (1, 1)]), ("spring-b", [(0, 0), (2, 1)]),
        ("spring-c", [(0, 1), (1, 0)])])]}
    prior = [{"functionId": fid, "approvedBy": "owner", "note": "Kept as a set.",
              "metrics": cf.block_signature(prior_bundle["metricsByFunction"][0])}]
    carried = {"a": {}, "b": {}, "c": {}}
    assert cf.carried_approvals(prior, prior_bundle, carried, from_version=7) == [
        {"functionId": fid, "approvedBy": "owner", "carriedFrom": 7,
         "note": "Carried from v7 with its approved metric set unchanged. Kept as a set."}]
    # a fixed criterion counts as unchanged; a rebuilt metric does not
    assert cf.carried_approvals(prior, prior_bundle, {"a": {}, "b": {}}, ["c"])
    assert cf.carried_approvals(prior, prior_bundle, {"a": {}, "b": {}}) == []
    # a changed curve, or a fourth metric, is a new set
    moved = copy.deepcopy(prior_bundle)
    moved["metricsByFunction"][0]["metrics"][2]["curve"]["points"][1]["y"] = 0.5
    assert cf.carried_approvals(prior, moved, carried) == []
    grown = copy.deepcopy(prior_bundle)
    grown["metricsByFunction"][0]["metrics"].append(
        {"metricId": "spring-d", "curve": {"points": [{"x": 0, "y": 0}]}})
    assert cf.carried_approvals(prior, grown, {**carried, "d": {}}) == []
    # an approval the owner gave for this build wins
    assert cf.carried_approvals(prior, prior_bundle, carried, have=[fid]) == []


def test_the_published_approvals_carry_where_their_blocks_are_all_carried():
    """On the canonical library: every approval the latest ECBP version records
    comes back from carry_forward.prepare, and it carries exactly when every
    metric of its block is carried forward or a fixed criterion."""
    prior = cf.prepare("55")
    if not prior or prior.get("legacy"):
        pytest.skip("no pressure-screen ECBP version in this checkout")
    aid, ver = prior["assessmentId"], prior["fromVersion"]
    vdir = lib.canonical_root() / "assessments" / aid / f"v{ver}"
    meta = json.loads((vdir / lib.META_FILE).read_text(encoding="utf-8"))
    bundle = json.loads((vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    fields = sio.decode_session_fields(sio.load_session_payload(vdir / lib.SESSION_FILE))
    fixed = fields["reference_build"]["fixedMetrics"]
    recorded = {a["functionId"] for a in meta.get("portfolioApprovals") or [] if a.get("approvedBy")}
    assert {a["functionId"] for a in prior["approvals"]} == recorded
    ok = {"spring-" + dx.deep_slug(k) for k in list(prior["carried"]) + list(fixed)}
    carried = {a["functionId"] for a in cf.carried_approvals(
        prior["approvals"], bundle, prior["carried"], fixed, from_version=ver)}
    for a in prior["approvals"]:
        assert (a["functionId"] in carried) == (set(a["metrics"]) <= ok), a["functionId"]
    if ver == 7:
        # v7's low-flow set is carried whole; bed composition's large wood is rebuilt
        assert carried == {"low-flow-baseflow-dynamics"}


def test_the_carry_forward_record_names_the_removal_and_who_made_it():
    from streamcurves import provenance as pv
    calls = []

    def add(rule, level, subject, **kw):
        calls.append((rule, subject, kw))

    result = {"carried_from": {"assessmentId": "x", "fromVersion": 7,
                               "contentDigest": "sha256:0"},
              "carried": {"a": {}, "b": {}}, "carry_rebuilt": {},
              "removed_carried": {"c": "The owner's reason for removing it."},
              "removed_carried_by": "owner"}
    pv._hierarchy_records(result, add)
    rec = next(kw for rule, subject, kw in calls
               if rule == "REF-05" and subject == "carry_forward")
    assert rec["computed"]["n_carried"] == 2
    assert rec["computed"]["removed"] == {"c": "The owner's reason for removing it."}
    assert rec["computed"]["removed_by"] == "owner"
    assert "the owner removed c from this version" in rec["recommendation"]
