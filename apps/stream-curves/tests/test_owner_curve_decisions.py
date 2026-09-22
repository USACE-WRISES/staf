"""The owner's decisions on the curves a build did not fit (REF-15, 2026-09-22).

The owner can remove a curve from the assessment, take it out of a function, or
put back a fitted curve the two-per-function rule left out. A decision is a
standing decision of its region: the workspace applies it at once, the session
keeps it beside the build's own reference build (so it can be undone), and every
later build applies it the same way through one transform after SELECT-04, with
nothing refilled. These tests hold the display to the bundle, the workspace to
the build, and every decision to its record.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from shiny import reactive

from streamcurves import deep_export as dx
from streamcurves import library as lib
from streamcurves import methodology
from streamcurves import owner_curves as oc
from streamcurves import pressure_evidence as pe
from streamcurves import region_build as rb
from streamcurves import run_state as rs
from streamcurves import session_io as sio
from views import assessment_publish as ap
from views import curve_gallery as cg
from views import source_panel as sp
from views.state import AppState

APP = Path(__file__).resolve().parents[1]
LIBRARY = APP.parent / "library" / "assessments"
FIELDS = ("completed_metrics", "curve_review", "discipline_function_mapping", "metric_config",
          "region_of_applicability", "function_coverage_exceptions", "predictor_config",
          "reference_build", "session_name")
WHY = "A reason long enough to be recorded."


def _opened(version: str):
    vdir = LIBRARY / version
    if not (vdir / lib.SESSION_FILE).is_file():
        pytest.skip(f"{version} is not in this checkout")
    bundle = json.loads((vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    fields = sio.decode_session_fields(sio.load_session_payload(vdir / lib.SESSION_FILE))
    state = AppState.fresh()
    for name in FIELDS:
        getattr(state, name).set(fields.get(name))
    aid, ver = version.split("/v")
    state.assessment_source.set(ap.build_origin(state, kind="library", library_id=aid,
                                                version=int(ver)))
    return state, bundle, fields


def _blocks(bundle) -> dict:
    return {str(b["functionId"]): sorted(str(m["metricId"]) for m in b.get("metrics") or [])
            for b in bundle.get("metricsByFunction") or [] if b.get("metrics")}


def _republish(state, bundle):
    return ap.build_bundle_from_state(state, meta={"sourceCitation": bundle.get("sourceCitation")})


def _decision(metric, action, **kw):
    return oc.new_decision(metric, action, rationale=kw.pop("rationale", WHY),
                           recorded_by=kw.pop("recorded_by", "owner"), **kw)


# --------------------------------------------------------------------------- #
# the record
# --------------------------------------------------------------------------- #
def test_a_decision_needs_a_named_owner_and_a_reason():
    with pytest.raises(ValueError, match="rationale"):
        oc.new_decision("chem_TURB", oc.REMOVE, rationale="too short", recorded_by="owner")
    with pytest.raises(ValueError, match="named owner"):
        oc.new_decision("chem_TURB", oc.REMOVE, rationale=WHY, recorded_by="")
    with pytest.raises(ValueError, match="function"):
        oc.new_decision("chem_TURB", oc.UNMAP, rationale=WHY, recorded_by="owner")
    with pytest.raises(ValueError, match="unassessed"):
        oc.new_decision("chem_TURB", oc.REMOVE, rationale=WHY, recorded_by="owner",
                        coverage_exceptions=[{"functionId": "f", "justification": "short"}])
    assert oc.min_rationale() == methodology.threshold("owner_decisions.min_rationale") == 20


def test_ref_15_is_in_the_catalog_with_its_threshold():
    rule = methodology.rule(oc.RULE)
    assert rule["name"] == "Owner curve decision" and rule["family"] == "REF"
    from streamcurves import rules_view as rv
    assert rv.RULE_THRESHOLD_PATHS[oc.RULE] == ["owner_decisions.min_rationale"]


def test_the_session_has_a_field_for_the_decisions():
    assert "owner_curve_decisions" in sio.SESSION_FIELDS
    d = _decision("chem_TURB", oc.REMOVE)
    back = sio.decode_session_fields(json.loads(sio.dumps_session(sio.dump_session_fields(
        {"owner_curve_decisions": [d]}))))
    assert back["owner_curve_decisions"] == [d]
    assert sio.decode_session_fields(json.loads(sio.dumps_session(
        sio.dump_session_fields({}))))["owner_curve_decisions"] is None


def test_a_session_cannot_take_a_decision_on_a_curve_it_built_or_does_not_hold():
    _state, _bundle, fields = _opened("eastern-corn-belt-plains/v7")
    build, built = fields["reference_build"], fields["completed_metrics"]
    fitted = sorted(built)[0]
    with pytest.raises(ValueError, match="built here"):
        oc.validate(_decision(fitted, oc.REMOVE), build=build, built=built)
    with pytest.raises(ValueError, match="not a curve from another source"):
        oc.validate(_decision("no_such_metric", oc.REMOVE), build=build, built=built)
    with pytest.raises(ValueError, match="CURVE-11"):
        oc.validate(_decision("pctimp2019ws", oc.SOURCE), build=build, built=built)
    with pytest.raises(ValueError, match="left out"):
        oc.validate(_decision(fitted, oc.INCLUDE, functions=["population-support"]),
                    build=build, built=built)
    # a fixed criterion can be removed: a portfolio choice, not a new criterion
    oc.validate(_decision("pctimp2019ws", oc.REMOVE), build=build, built=built)


# --------------------------------------------------------------------------- #
# the region's file
# --------------------------------------------------------------------------- #
def test_the_region_keeps_its_decisions_and_one_supersedes_another(tmp_path):
    first = _decision("chem_TURB", oc.UNMAP, functions=["water-soil-quality"])
    oc.save(tmp_path, first)
    assert [d["id"] for d in oc.load(tmp_path)] == [first["id"]]
    # removing the curve replaces the older decision on it
    gone = _decision("chem_TURB", oc.REMOVE)
    assert [d["id"] for d in oc.save(tmp_path, gone)] == [gone["id"]]
    assert oc.path_of(tmp_path) == tmp_path / oc.DECISIONS_FILE
    assert oc.undo(tmp_path, gone["id"]) == []
    assert not (tmp_path / oc.DECISIONS_FILE).exists() and oc.path_of(tmp_path) is None


def test_the_removals_of_2026_09_21_are_folded_in(tmp_path):
    (tmp_path / oc.LEGACY_REMOVALS_FILE).write_text(json.dumps([
        {"metric": "phab_XFC_NAT", "rationale": WHY, "recordedBy": "owner",
         "recordedAt": "2026-09-21T00:00:00+00:00"}]), encoding="utf-8")
    items = oc.load(tmp_path)
    assert [(d["metric"], d["action"], d["recordedBy"]) for d in items] == [
        ("phab_XFC_NAT", oc.REMOVE, "owner")]
    assert not (tmp_path / oc.LEGACY_REMOVALS_FILE).exists()


def test_the_session_and_the_region_combine_by_id():
    a = _decision("chem_TURB", oc.REMOVE)
    b = _decision("chem_NTL", oc.UNMAP, functions=["nutrient-cycling"])
    assert [d["id"] for d in oc.combine([a], [a, b])] == [a["id"], b["id"]]


def test_a_flag_removal_is_the_same_decision_every_time():
    one = oc.from_removals({"chem_TURB": WHY}, recorded_by="owner", keys={"chem_TURB"})
    two = oc.from_removals({"chem_TURB": WHY}, recorded_by="owner", keys={"chem_TURB"})
    assert one[0]["id"] == two[0]["id"] and one[0]["action"] == oc.REMOVE
    assert oc.from_removals({"fitted": WHY}, recorded_by="owner", keys={"chem_TURB"}) == []


def test_the_build_command_passes_the_region_decisions(tmp_path):
    argv = rb.stage_command("55", "Eastern Corn Belt Plains", tmp_path, maintainer="owner",
                            curve_decisions=tmp_path / oc.DECISIONS_FILE)
    i = argv.index("--curve-decisions")
    assert argv[i + 1] == str(tmp_path / oc.DECISIONS_FILE)
    assert "--curve-decisions" not in rb.stage_command("55", "E", tmp_path, maintainer="owner")
    assert rb.region_run_dir({"kind": "ecoregion", "code": "27"}) == rb.run_folder(
        rb.default_runs_root(), "27")
    assert rb.region_run_dir({"kind": "state", "code": "OH"}) is None


def test_the_runner_takes_a_decisions_file_in_stage_and_stage_many():
    src = (APP / "scripts" / "run_region_batch.py").read_text(encoding="utf-8")
    assert '"--curve-decisions"' in src and "curve_decisions=curve_decisions or None" in src
    assert "curve_decisions=None," in src                  # stage-many's explicit Namespace


# --------------------------------------------------------------------------- #
# what the decisions do, the same way in the workspace and in a build
# --------------------------------------------------------------------------- #
def test_no_decision_changes_nothing():
    state, bundle, _fields = _opened("interior-plateau/v6")
    state.owner_curve_decisions.set([])
    assert lib.content_digest(_republish(state, bundle)) == bundle["contentDigest"]


def test_each_action_changes_exactly_its_function_blocks():
    state, bundle, fields = _opened("eastern-corn-belt-plains/v7")
    rows = pe.reference_rows(fields["reference_build"], fields["discipline_function_mapping"],
                             built=fields["completed_metrics"])
    modeled = next(k for k, e in rows.items() if e["kind"] == "modeled")
    fixed, fixed_fns = next((k, e["functions"]) for k, e in rows.items()
                            if e["kind"] == "fixed" and len(e["functions"]) > 1)
    state.owner_curve_decisions.set([
        _decision(modeled, oc.REMOVE),
        _decision(fixed, oc.UNMAP, functions=[fixed_fns[-1]])])
    before, after = _blocks(bundle), _blocks(_republish(state, bundle))
    from streamcurves.deep_export import deep_slug
    mid, fid = "spring-" + deep_slug(modeled), "spring-" + deep_slug(fixed)
    assert all(mid not in ids for ids in after.values())
    assert fid not in after.get(fixed_fns[-1], []) and fid in after[fixed_fns[0]]
    changed = {f for f in set(before) | set(after) if before.get(f) != after.get(f)}
    assert changed <= set(rows[modeled]["functions"]) | {fixed_fns[-1]}


def test_the_display_counts_what_the_bundle_scores():
    state, bundle, fields = _opened("eastern-corn-belt-plains/v7")
    rows = pe.reference_rows(fields["reference_build"], fields["discipline_function_mapping"],
                             built=fields["completed_metrics"])
    published = next(k for k, e in rows.items() if e["kind"] == "published_benchmark")
    state.owner_curve_decisions.set([_decision(published, oc.REMOVE)])
    rebuilt = _republish(state, bundle)
    with reactive.isolate():
        eff = ap.effective_reference_build(state)
    completed, review = fields["completed_metrics"], fields["curve_review"] or {}
    in_scope = {mk: cm for mk, cm in completed.items()
                if mk not in review or rs.is_in_scope(review[mk])}
    mapping = fields["discipline_function_mapping"]
    counts = dx.metrics_per_function_quick(
        in_scope, mapping, extra=pe.reference_metric_counts(eff, mapping, built=completed),
        exclude_pairs=pe.not_selected_pairs(eff))
    assert {f: n for f, n in counts.items() if n} == {f: len(m) for f, m in _blocks(rebuilt).items()}
    cov = ap.coverage_from_state(state)
    assert set(cov["coveredFunctionIds"]) == set(rebuilt["functionCoverage"]["coveredFunctionIds"])
    assert [d["metric"] for d in rebuilt["ownerCurveDecisions"]] == [published]


def test_undo_restores_the_build_and_twice_is_once():
    state, bundle, fields = _opened("northeastern-highlands/v9")
    carried = sorted(fields["reference_build"]["carriedMetrics"])[0]
    d = _decision(carried, oc.REMOVE)
    state.owner_curve_decisions.set([d])
    once = lib.content_digest(_republish(state, bundle))
    assert once != bundle["contentDigest"]
    state.owner_curve_decisions.set(oc.combine([d], [d]))
    assert lib.content_digest(_republish(state, bundle)) == once
    state.owner_curve_decisions.set([])
    assert lib.content_digest(_republish(state, bundle)) == bundle["contentDigest"]


def test_the_workspace_and_a_build_run_the_same_transform():
    """apply_to_inputs on the pre-drop inputs equals SELECT-04's own drop when the
    owner decided nothing, and the build and the workspace both call it."""
    _state, _bundle, fields = _opened("eastern-corn-belt-plains/v7")
    build = fields["reference_build"]
    rows = dx.deep_collect_curve_rows(fields["completed_metrics"])
    meta: dict = {}
    r1, m1, _c = pe.apply_reference_build(build, dict(rows), fields["discipline_function_mapping"],
                                          dict(fields["metric_config"]), dict(meta))
    meta2: dict = {}
    r2, m2, c2 = pe.apply_reference_build(build, dict(rows), fields["discipline_function_mapping"],
                                          dict(fields["metric_config"]), meta2,
                                          apply_selection=False)
    r2, m2, _c2 = oc.apply_to_inputs(r2, m2, c2, meta2, [],
                                     keep=pe.fixed_in_order(build.get("fixedMetrics")))
    assert set(r1) == set(r2) and m1.reset_index(drop=True).equals(m2.reset_index(drop=True))
    agent = (APP / "streamcurves" / "regional_agent.py").read_text(encoding="utf-8")
    assert "owner_curves.apply_to_inputs(" in agent
    assert "_oc.apply_to_inputs(" in (APP / "views" / "assessment_publish.py").read_text(
        encoding="utf-8")


def test_an_include_puts_back_a_pair_select_04_left_out():
    selection = {"population-support": {"selected": ["bent_TOTLNTAX"], "notSelected": [
        {"metric": "fish_NAT_NTOLPTAX", "source": "regional"}]}}
    d = _decision("fish_NAT_NTOLPTAX", oc.INCLUDE, functions=["population-support"])
    eff = oc.effective_selection(selection, [d])
    assert eff["population-support"]["notSelected"] == []
    assert eff["population-support"]["ownerIncluded"] == ["fish_NAT_NTOLPTAX"]
    unmap = _decision("bent_TOTLNTAX", oc.UNMAP, functions=["population-support"])
    eff = oc.effective_selection(selection, [unmap])
    assert {"metric": "bent_TOTLNTAX", "owner": True, "decision": unmap["id"]} in \
        eff["population-support"]["notSelected"]
    # the build's own record is never touched
    assert selection["population-support"]["notSelected"] == [
        {"metric": "fish_NAT_NTOLPTAX", "source": "regional"}]


def test_a_gap_the_owner_gave_rides_with_the_decision_and_goes_with_it():
    d = _decision("chem_TURB", oc.REMOVE, coverage_exceptions=[
        {"functionId": "water-soil-quality", "justification": "Nothing else measures it here."}])
    gaps = oc.with_exceptions([{"functionId": "water-soil-quality", "reason": "x"},
                               {"functionId": "carbon-processing", "reason": "y"}], [d])
    assert [g["functionId"] for g in gaps] == ["carbon-processing", "water-soil-quality"]
    assert gaps[-1]["recordedBy"] == "owner" and gaps[-1]["decision"] == d["id"]
    assert oc.with_exceptions([], []) == []


def test_stale_decisions_are_named():
    build = {"method": pe.METHOD, "ladderMetrics": {"chem_TURB": {"points": [{"x": 0, "y": 1}]}},
             "portfolioSelection": {"f": {"notSelected": [{"metric": "fish_X"}]}}}
    fine = _decision("chem_TURB", oc.REMOVE)
    gone = _decision("chem_NTL", oc.REMOVE)
    moved = _decision("fish_Y", oc.INCLUDE, functions=["f"])
    names = [d["metric"] for d, _why in oc.stale([fine, gone, moved], build)]
    assert names == ["chem_NTL", "fish_Y"]


# --------------------------------------------------------------------------- #
# the build: records and packet
# --------------------------------------------------------------------------- #
def test_the_build_records_each_decision_under_ref_15():
    from streamcurves import provenance as pv
    d = _decision("chem_TURB", oc.REMOVE)
    records = []

    def add(rule, kind, subject, **kw):
        records.append({"rule": rule, "kind": kind, "subject": subject, **kw})

    pv._hierarchy_records({"curve_decisions": [d], "carried_from": {}}, add)
    mine = [r for r in records if r["rule"] == "REF-15"]
    assert len(mine) == 1 and mine[0]["kind"] == "owner_decision" and mine[0]["subject"] == d["id"]
    assert "chem_TURB" in mine[0]["recommendation"] and WHY in mine[0]["recommendation"]


def test_the_packet_lists_the_decisions():
    from streamcurves import review_packet as rp
    d = _decision("chem_TURB", oc.REMOVE)
    h = rp.hierarchy_block({"curve_decisions": [d], "removed_carried": {}})
    text = "\n".join(rp._hierarchy_section(h))
    assert "Curve decisions of the owner (REF-15)" in text and "chem_TURB" in text


# --------------------------------------------------------------------------- #
# the workspace
# --------------------------------------------------------------------------- #
def test_every_curve_from_another_source_can_be_removed_and_a_removed_one_undone():
    _state, _bundle, fields = _opened("interior-plateau/v6")
    build, mapping = fields["reference_build"], fields["discipline_function_mapping"]
    built = fields["completed_metrics"]
    tiles = cg.reference_tiles_for(build, mapping, built=built)
    assert tiles and all(t["removable"] for t in tiles)
    victim = tiles[0]["metric"]
    d = _decision(victim, oc.REMOVE)
    tiles = cg.reference_tiles_for(build, mapping, built=built, decisions=[d])
    mine = [t for t in tiles if t["metric"] == victim]
    assert len(mine) == 1 and mine[0]["removed_decision"] == d["id"]
    html = _unescape(str(cg.tile_ui(mine[0], channel_id="c")))
    assert sp.UNDO_INPUT in html and "is-owner-removed" in html and sp.ACT_INPUT not in html
    others = _unescape(str(cg.tile_ui(next(t for t in tiles if t["metric"] != victim),
                                      channel_id="c")))
    assert sp.ACT_INPUT in others and '"action": "remove"' in others


def _unescape(html: str) -> str:
    return html.replace("&apos;", "'").replace("&quot;", '"').replace("&#x27;", "'")


def test_a_curve_the_owner_put_back_says_so_with_its_undo():
    build = {"method": pe.METHOD, "portfolioSelection": {"population-support": {
        "selected": ["bent_TOTLNTAX"],
        "notSelected": [{"metric": "fish_NAT_NTOLPTAX", "source": "regional"}]}}}
    d = _decision("fish_NAT_NTOLPTAX", oc.INCLUDE, functions=["population-support"])
    eff = oc.effective_build(build, [d])
    tile = {"metric": "fish_NAT_NTOLPTAX", "display_name": "Fish", "strata": [],
            "function_id": "population-support", "also_function_refs": [], "also_functions": []}
    cg.mark_not_selected([tile], eff)
    assert tile["not_selected_fids"] == [] and tile["owner_included"] == {
        "population-support": d["id"]}
    html = _unescape(str(cg.tile_ui(tile, channel_id="c")))
    assert "Used by the owner" in html and "is-owner-included" in html
    assert sp.UNDO_INPUT in html and d["id"] in html


def test_the_form_asks_for_the_gap_a_decision_would_leave():
    html = str(sp.decision_form("chem_TURB", "Turbidity", oc.REMOVE, ["water-soil-quality"],
                                ["water-soil-quality"], ns=lambda x: x, others=[]))
    assert "would have no curve left" in html and 'id="dec_gap_0"' in html
    assert 'id="dec_rationale"' in html and chr(8212) not in html
    use = str(sp.decision_form("fish_X", "Fish", oc.INCLUDE, ["population-support"], [],
                               ns=lambda x: x, others=[]))
    assert "Use in Population support" in use and "would have no curve left" not in use
