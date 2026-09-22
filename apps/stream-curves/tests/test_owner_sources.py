"""The owner's choice of where a curve comes from (REF-15, owner decision 2026-09-22).

For a metric the build did not fit, the owner may choose its curve: a criterion
of the verified catalog fit for the ecoregion, an earlier version's curve,
another assessment's curve, or thresholds or breakpoints entered by hand, cited
or on professional judgment. The choice is resolved when it is made and applied
by the same transform in the workspace and in a build. These tests hold each
source to what it states, the display to the bundle, and undo to the build.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from shiny import reactive

from streamcurves import carry_forward as cf
from streamcurves import curve_basis as cb
from streamcurves import curve_sources as src
from streamcurves import curves
from streamcurves import deep_export as dx
from streamcurves import library as lib
from streamcurves import owner_curves as oc
from streamcurves import owner_sources as osrc
from streamcurves import pressure_evidence as pe
from streamcurves import published_benchmark as pb
from streamcurves import run_state as rs
from streamcurves import session_io as sio
from streamcurves.deep_export import deep_slug
from views import assessment_publish as ap
from views import curve_gallery as cg
from views import source_dialog as sd
from views import source_panel as sp
from views.state import AppState

APP = Path(__file__).resolve().parents[1]
LIBRARY = APP.parent / "library" / "assessments"
FIELDS = ("completed_metrics", "curve_review", "discipline_function_mapping", "metric_config",
          "region_of_applicability", "function_coverage_exceptions", "predictor_config",
          "reference_build", "session_name")
WHY = "A reason long enough to be recorded."
EM_DASH = chr(8212)


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


def _republish(state, bundle):
    return ap.build_bundle_from_state(state, meta={"sourceCitation": bundle.get("sourceCitation")})


def _blocks(bundle) -> dict:
    return {str(b["functionId"]): [str(m["metricId"]) for m in b.get("metrics") or []]
            for b in bundle.get("metricsByFunction") or [] if b.get("metrics")}


def _entries(bundle, metric):
    mid = "spring-" + deep_slug(metric)
    return [(b["functionId"], m) for b in bundle.get("metricsByFunction") or []
            for m in b.get("metrics") or [] if m.get("metricId") == mid]


def _source_decision(metric, option, config, functions, **kw):
    return oc.new_decision(metric, oc.SOURCE, rationale=kw.pop("rationale", WHY),
                           recorded_by=kw.pop("recorded_by", "owner"), functions=functions,
                           source=osrc.decision_source(metric, option, config=config), **kw)


def _display_equals_bundle(state, fields, rebuilt) -> None:
    with reactive.isolate():
        eff = ap.effective_reference_build(state)
    completed, review = fields["completed_metrics"], fields["curve_review"] or {}
    in_scope = {mk: cm for mk, cm in completed.items()
                if mk not in review or rs.is_in_scope(review[mk])}
    mapping = fields["discipline_function_mapping"]
    counts = dx.metrics_per_function_quick(
        in_scope, mapping, extra=pe.reference_metric_counts(eff, mapping, built=completed),
        exclude_pairs=pe.not_selected_pairs(eff))
    assert {f: n for f, n in counts.items() if n} == {f: len(m) for f, m in
                                                      _blocks(rebuilt).items()}
    cov = ap.coverage_from_state(state)
    assert set(cov["coveredFunctionIds"]) == set(rebuilt["functionCoverage"]["coveredFunctionIds"])


# --------------------------------------------------------------------------- #
# a curve the owner enters
# --------------------------------------------------------------------------- #
def test_two_thresholds_make_the_curve_every_criterion_makes():
    lower = osrc.agent_config("chem_PTL")               # lower is better, ug/L
    points, errors, labels = osrc.threshold_points(20, 60, lower)
    assert not errors
    assert [b["rating"] for b in labels] == ["Good", "Fair", "Poor"]
    assert labels[0]["label"] == "\u226420 ug/L" and labels[2]["label"] == ">60 ug/L"
    # the threshold itself scores in its better class, and the line runs 1 to 0
    assert curves.interp_curve(points, 20.0) > 0.69 and curves.interp_curve(points, 60.0) > 0.39
    assert curves.interp_curve(points, 20.1) < 0.69 and curves.interp_curve(points, 60.1) < 0.39
    assert points[0]["y"] == 1.0 and points[-1]["y"] == 0.0
    higher = osrc.agent_config("fish_NAT_NTOLPTAX")     # higher is better, percent
    points, errors, _ = osrc.threshold_points(40, 10, higher)
    assert not errors and curves.interp_curve(points, 40.0) > 0.69
    assert curves.interp_curve(points, 39.9) < 0.69


def test_thresholds_that_make_no_sense_are_refused_in_words():
    lower, higher = osrc.agent_config("chem_PTL"), osrc.agent_config("fish_NAT_NTOLPTAX")
    assert "below the Poor one" in osrc.threshold_points(60, 20, lower)[1][0]
    assert "above the Poor one" in osrc.threshold_points(10, 40, higher)[1][0]
    assert "differ" in osrc.threshold_points(5, 5, lower)[1][0]
    assert "range" in osrc.threshold_points(150, 20, higher)[1][0]
    assert "numbers" in osrc.threshold_points(None, 20, lower)[1][0]
    assert "point by point" in osrc.threshold_points(6, 8, osrc.agent_config("chem_PH"))[1][0]


def test_breakpoints_are_checked_like_a_curve_drawn_in_an_analysis():
    cfg = osrc.agent_config("chem_PTL")
    points, errors = osrc.breakpoint_points("0, 1\n20; 0.7\n60\t0.3\n120 0", cfg)
    assert not errors and [p["x"] for p in points] == [0, 20, 60, 120]
    assert "Line 2" in osrc.breakpoint_points("0, 1\nnot a point", cfg)[1][0]
    assert osrc.breakpoint_points("0, 1", cfg)[1]
    assert any("0.30 and 0.70" in e for e in osrc.breakpoint_points("0, 1\n10, 0.9", cfg)[1])


def test_an_entered_curve_states_what_it_rests_on_and_nothing_more():
    cfg = osrc.agent_config("chem_PTL")
    opt = osrc.entered_option("chem_PTL", method=osrc.THRESHOLDS, config=cfg,
                              title="State nutrient rule", good=20, poor=60)
    assert not opt["errors"]
    ann = opt["annotations"]
    assert ann["basis"] == cb.OWNER and ann["basisLabel"] == "Owner-entered"
    assert "referenceSupport" not in ann and "confidenceLabel" not in ann
    assert "criteriaBasis" not in ann               # DEEP must not read "the same everywhere"
    assert ann["sourceCitation"].startswith(osrc.JUDGMENT)
    assert ann["criteriaSource"]["citations"] == []
    cited = osrc.entered_option("chem_PTL", method=osrc.THRESHOLDS, config=cfg,
                                title="State nutrient rule", citation="State rule 7",
                                good=20, poor=60)
    assert cited["annotations"]["sourceCitation"] == "State rule 7"
    assert osrc.outcome_of({"kind": osrc.ENTERED, "citation": "x"}) == "entered_cited"
    assert osrc.outcome_of({"kind": osrc.ENTERED}) == "entered_judgment"
    untitled = osrc.entered_option("chem_PTL", method=osrc.THRESHOLDS, config=cfg, title=" ",
                                   good=20, poor=60)
    assert any("title" in e for e in untitled["errors"])


# --------------------------------------------------------------------------- #
# the pool
# --------------------------------------------------------------------------- #
def test_the_catalog_offers_its_criterion_where_it_is_fit():
    got = osrc.catalog_option("chem_PTL", region_code="27")
    assert got["available"] and got["ref"]["region"] == "SPL"
    assert got["points"] == pb.curve_points("chem_PTL", "SPL")
    ann = got["annotations"]
    assert ann["basis"] == cb.PUBLISHED and ann["criteriaBasis"] == "fixed"
    assert ann["referenceSupport"]["status"] == "published"
    assert ann["criteriaSource"]["title"].endswith("NARS-9 region SPL")
    assert osrc.catalog_option("phab_XCMGW", region_code="27") is None


def test_the_library_offers_earlier_versions_and_other_assessments(tmp_path):
    opts = osrc.library_options("chem_PTL", region_code="55")
    kinds = [o["kind"] for o in opts]
    assert kinds[0] == osrc.EARLIER and osrc.OTHER in kinds
    earlier = [o for o in opts if o["kind"] == osrc.EARLIER]
    assert len(earlier) <= osrc.MAX_EARLIER
    # each distinct curve once
    assert len({tuple((p["x"], p["y"]) for p in o["points"]) for o in earlier}) == len(earlier)
    assert all(o["annotations"]["carriedForward"]["assessmentId"] == "eastern-corn-belt-plains"
               for o in earlier)
    other = next(o for o in opts if o["kind"] == osrc.OTHER)
    ann = other["annotations"]
    assert ann["borrowedFrom"]["assessmentId"] != "eastern-corn-belt-plains"
    assert "referenceN" not in ann and "confidenceLabel" not in ann
    assert ann["basisLimit"] == osrc.BORROWED_LIMIT
    # the curve the metric scores now is not offered again
    now = earlier[0]["points"]
    assert all(o["points"] != now for o in osrc.library_options("chem_PTL", region_code="55",
                                                                current=now))


def test_a_borrowed_curve_keeps_a_published_criterions_record_and_no_pool_record():
    regional = {"basis": cb.REGIONAL, "basisLabel": "Regional reference", "referenceN": 17,
                "referenceSupport": {"status": "local", "nUsable": 17},
                "curve": {"points": [{"x": 0, "y": 1}, {"x": 1, "y": 0}]}}
    ann = osrc.borrowed_annotations(regional, assessment_id="a", name="A", version=2,
                                    region={"code": "71", "name": "Interior Plateau"},
                                    content_digest=None)
    assert "referenceSupport" not in ann and ann["basisLabel"].endswith("from Interior Plateau")
    published = dict(regional, basis=cb.PUBLISHED, criteriaBasis="fixed",
                     referenceSupport={"status": "published", "nUsable": 0})
    ann = osrc.borrowed_annotations(published, assessment_id="a", name="A", version=2,
                                    region={"code": "55"}, content_digest=None)
    assert ann["referenceSupport"]["status"] == "published"


def test_the_refusals_are_listed_with_their_reasons():
    _state, _bundle, fields = _opened("interior-plateau/v6")
    build = fields["reference_build"]
    opts = osrc.refused_options("chem_CHLA", build=build)
    # the two national options can be computed at a build; a metric the registry
    # and the catalog hold nothing for cannot
    assert [(o["ref"]["rule"], o["available"]) for o in opts] == [
        ("REF-12", True), ("REF-12", True), ("REF-13", False), ("REF-14", False)]
    assert all(o["why_not"] for o in opts)
    # the provenance names every station pool the build tried, with its count
    pools = osrc.refused_options("chem_CHLA", build=build,
                                 provenance=lib.load_version_provenance("interior-plateau", 6))
    assert pools[0]["key"] == "refused_source:REF-04:local" and not pools[0]["available"]
    assert any(o["key"].startswith("refused_source:REF-11:") and o["available"]
               for o in pools)


# --------------------------------------------------------------------------- #
# the decision
# --------------------------------------------------------------------------- #
def test_a_source_decision_needs_a_curve_and_where_it_scores():
    cfg = osrc.agent_config("chem_PTL")
    opt = osrc.catalog_option("chem_PTL", region_code="27")
    with pytest.raises(ValueError, match="function"):
        _source_decision("chem_PTL", opt, cfg, [])
    empty = dict(opt, points=[])
    with pytest.raises(ValueError, match="no curve"):
        _source_decision("chem_PTL", empty, cfg, ["nutrient-cycling"])
    d = _source_decision("chem_PTL", opt, cfg, ["nutrient-cycling"])
    assert oc.summary(d)["source"] == {"kind": osrc.CATALOG, "ref": opt["ref"],
                                        "title": opt["title"], "citation": opt["citation"]}
    assert "curve" not in json.dumps(oc.summary(d))


def test_a_fixed_criterion_or_a_curve_built_here_cannot_take_a_source():
    _state, _bundle, fields = _opened("eastern-corn-belt-plains/v7")
    build, built = fields["reference_build"], fields["completed_metrics"]
    cfg = osrc.agent_config("chem_PTL")
    opt = osrc.entered_option("x", method=osrc.BREAKPOINTS, config=cfg, title="T",
                              points_text="0, 1\n20, 0.7\n60, 0.3\n120, 0")
    with pytest.raises(ValueError, match="CURVE-11"):
        oc.validate(_source_decision("pctimp2019ws", opt, cfg, ["catchment-hydrology"]),
                    build=build, built=built)
    fitted = sorted(built)[0]
    with pytest.raises(ValueError, match="built here"):
        oc.validate(_source_decision(fitted, opt, cfg, ["nutrient-cycling"]),
                    build=build, built=built)
    assert osrc.sourceable(fitted, built=built) and osrc.sourceable("pctimp2019ws")


def test_taking_a_chosen_curve_out_of_a_function_keeps_the_choice():
    cfg = osrc.agent_config("chem_PTL")
    opt = osrc.catalog_option("chem_PTL", region_code="27")
    chosen = _source_decision("chem_PTL", opt, cfg, ["nutrient-cycling", "water-soil-quality"])
    unmap = oc.new_decision("chem_PTL", oc.UNMAP, rationale=WHY, recorded_by="owner",
                            functions=["water-soil-quality"])
    assert [d["action"] for d in oc.merge([chosen], unmap)] == [oc.SOURCE, oc.UNMAP]
    gone = oc.new_decision("chem_PTL", oc.REMOVE, rationale=WHY, recorded_by="owner")
    assert [d["action"] for d in oc.merge([chosen, unmap], gone)] == [oc.REMOVE]
    again = _source_decision("chem_PTL", opt, cfg, ["nutrient-cycling"])
    assert [d["id"] for d in oc.merge([chosen, unmap], again)] == [again["id"]]


def test_a_choice_on_a_curve_the_build_now_fits_is_stale():
    cfg = osrc.agent_config("chem_PTL")
    d = _source_decision("chem_PTL", osrc.catalog_option("chem_PTL", region_code="27"), cfg,
                         ["nutrient-cycling"])
    build = {"method": pe.METHOD}
    assert [why for _d, why in oc.stale([d], build, built={"chem_PTL"})] == [
        "This build fits the metric itself, so its own curve scores."]
    assert oc.stale([d], build, built=set()) == []
    assert oc.effective_build(build, [d], built={"chem_PTL"}).get("ownerMetrics") is None


# --------------------------------------------------------------------------- #
# the same in the workspace and in the bundle
# --------------------------------------------------------------------------- #
def test_another_assessments_curve_replaces_the_builds_and_undo_gives_it_back():
    state, bundle, fields = _opened("eastern-corn-belt-plains/v7")
    build, mapping, built = (fields["reference_build"], fields["discipline_function_mapping"],
                             fields["completed_metrics"])
    rows = pe.reference_rows(build, mapping, built=built)
    modeled = next(k for k, e in rows.items() if e["kind"] == "modeled")
    opt = next(o for o in osrc.library_options(modeled, region_code="55")
               if o["kind"] == osrc.OTHER)
    cfg = osrc.config_for(modeled, metric_config=fields["metric_config"], build=build)
    d = _source_decision(modeled, opt, cfg, rows[modeled]["functions"])
    oc.validate(d, build=build, built=built)
    state.owner_curve_decisions.set([d])
    rebuilt = _republish(state, bundle)
    placed = _entries(rebuilt, modeled)
    assert [f for f, _m in placed] == rows[modeled]["functions"]
    entry = placed[0][1]
    assert entry["curve"]["points"] == [{"x": p["x"], "y": p["y"]} for p in opt["points"]]
    assert entry["ownerDecision"]["kind"] == osrc.OTHER and entry["ownerDecision"]["rationale"] == WHY
    assert entry["borrowedFrom"]["assessmentId"] == opt["ref"]["assessmentId"]
    assert "referenceSupport" not in entry and entry["basisLimit"] == osrc.BORROWED_LIMIT
    assert rebuilt["ownerCurveDecisions"][0]["source"]["kind"] == osrc.OTHER
    _display_equals_bundle(state, fields, rebuilt)
    state.owner_curve_decisions.set([])
    assert lib.content_digest(_republish(state, bundle)) == bundle["contentDigest"]


def test_an_entered_curve_scores_a_withheld_metric_and_leaves_the_withheld_list():
    state, bundle, fields = _opened("interior-plateau/v6")
    build, built = fields["reference_build"], fields["completed_metrics"]
    cfg = osrc.config_for("chem_CHLA", metric_config=fields["metric_config"], build=build)
    assert cfg.get("display_name")                     # from the agent's config builder
    opt = osrc.entered_option("chem_CHLA", method=osrc.THRESHOLDS, config=cfg,
                              title="Chlorophyll by judgment", good=5, poor=20)
    d = _source_decision("chem_CHLA", opt, cfg, ["light-thermal-regime"])
    oc.validate(d, build=build, built=built)
    state.owner_curve_decisions.set([d])
    rebuilt = _republish(state, bundle)
    assert [f for f, _m in _entries(rebuilt, "chem_CHLA")] == ["light-thermal-regime"]
    assert "chem_CHLA" not in [w["metricKey"] for w in rebuilt.get("insufficientReferenceSupport")
                               or []]
    entry = _entries(rebuilt, "chem_CHLA")[0][1]
    assert entry["basis"] == cb.OWNER and "criteriaBasis" not in entry
    _display_equals_bundle(state, fields, rebuilt)
    # twice is once, and undo gives the published version back
    state.owner_curve_decisions.set(oc.combine([d], [d]))
    assert lib.content_digest(_republish(state, bundle)) == lib.content_digest(rebuilt)
    state.owner_curve_decisions.set([])
    assert lib.content_digest(_republish(state, bundle)) == bundle["contentDigest"]


def test_the_workspace_reads_a_chosen_curve_as_its_kind_with_its_undo():
    _state, _bundle, fields = _opened("interior-plateau/v6")
    build, mapping, built = (fields["reference_build"], fields["discipline_function_mapping"],
                             fields["completed_metrics"])
    cfg = osrc.config_for("chem_CHLA", metric_config=fields["metric_config"], build=build)
    opt = osrc.entered_option("chem_CHLA", method=osrc.THRESHOLDS, config=cfg,
                              title="Chlorophyll by judgment", good=5, poor=20)
    d = _source_decision("chem_CHLA", opt, cfg, ["light-thermal-regime"])
    eff = oc.effective_build(build, [d], built=built)
    assert "chem_CHLA" in pe.reference_keys(eff)
    entry = pe.reference_rows(eff, mapping, built=built)["chem_CHLA"]
    assert entry["kind"] == "owner_entered" and entry["label"] == "Owner-entered"
    assert entry["owner"]["id"] == d["id"] and entry["functions"] == ["light-thermal-regime"]
    assert pe.reference_summary(eff, built=built).get("owner") == 1
    assert "1 chosen by the owner" in pe.reference_summary_text(pe.reference_summary(eff, built=built))
    tile = next(t for t in cg.reference_tiles_for(build, mapping, built=built, decisions=[d])
                if t["metric"] == "chem_CHLA")
    assert tile["owner_decision"] == d["id"] and "is-owner-chosen" in " ".join(
        __import__("streamcurves.curve_svg", fromlist=["x"]).tile_state_classes(tile))
    html = str(cg.tile_ui(tile, channel_id="c")).replace("&quot;", '"')
    assert sp.UNDO_INPUT in html and d["id"] in html
    # the panel: what it is, who chose it and why, and the build's own verdict first
    facts = dict(src.source_facts("chem_CHLA", entry, build=build))
    assert facts["Source"] == "Chlorophyll by judgment"
    assert facts["Citation"].startswith("None")
    assert "REF-15" in src.chosen_by("chem_CHLA", entry)
    trail = src.build_trail("chem_CHLA", entry, build=build)
    assert [s["verdict"] for s in trail][-1] == src.USED
    assert trail[0]["step"] == "Station pools" and trail[-1]["step"] == "The owner"
    body = str(sp.panel_modal("chem_CHLA", entry, build=build, decisions=[d]))
    assert "Undo this choice" in body and "Change source" in body
    assert "Remove from assessment" not in body        # nothing of the build's to remove
    assert EM_DASH not in body


# --------------------------------------------------------------------------- #
# builds, the record and the region's file
# --------------------------------------------------------------------------- #
def test_a_build_never_carries_a_curve_the_owner_chose(tmp_path):
    src_dir = LIBRARY / "interior-plateau"
    if not (src_dir / "v6" / lib.SESSION_FILE).is_file():
        pytest.skip("interior-plateau/v6 is not in this checkout")
    root = tmp_path / "library"
    dest = root / "assessments" / "interior-plateau"
    (dest / "v6").mkdir(parents=True)
    shutil.copy(src_dir / "manifest.json", dest / "manifest.json")
    for name in (lib.BUNDLE_FILE, lib.SESSION_FILE, lib.META_FILE):
        shutil.copy(src_dir / "v6" / name, dest / "v6" / name)
    before = set(cf.prepare("71", root=root)["carried"])
    assert "fish_NAT_TOTLNTAX" in before
    bundle = json.loads((dest / "v6" / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    for _f, m in _entries(bundle, "fish_NAT_TOTLNTAX"):
        m["ownerDecision"] = {"kind": osrc.ENTERED, "recordedBy": "owner"}
    (dest / "v6" / lib.BUNDLE_FILE).write_text(json.dumps(bundle), encoding="utf-8")
    assert set(cf.prepare("71", root=root)["carried"]) == before - {"fish_NAT_TOTLNTAX"}


def test_the_record_names_the_source_and_the_rule_it_overrides():
    from streamcurves import provenance as pv
    from streamcurves import review_packet as rpk
    cfg = osrc.agent_config("chem_CHLA")
    opt = osrc.entered_option("chem_CHLA", method=osrc.THRESHOLDS, config=cfg,
                              title="Chlorophyll by judgment", good=5, poor=20)
    d = _source_decision("chem_CHLA", opt, cfg, ["light-thermal-regime"])
    records = []

    def add(rule, kind, subject, **kw):
        records.append({"rule": rule, "kind": kind, "subject": subject, **kw})

    pv._hierarchy_records({"curve_decisions": [d], "carried_from": {},
                           "insufficient_support": {"chem_CHLA": {}}}, add)
    rec = next(r for r in records if r["rule"] == "REF-15")
    assert rec["computed"]["outcome"] == "entered_judgment"
    assert rec["computed"]["overrides"] == "REF-06"
    assert rec["computed"]["source"]["title"] == "Chlorophyll by judgment"
    assert "Source: Chlorophyll by judgment" in rec["recommendation"]
    h = rpk.hierarchy_block({"curve_decisions": [d], "removed_carried": {}})
    text = "\n".join(rpk._hierarchy_section(h))
    assert "Source: Chlorophyll by judgment (Enter a curve), professional judgment." in text


def test_a_region_without_a_record_takes_the_published_one_and_an_emptied_one_stays(tmp_path):
    d = oc.new_decision("chem_TURB", oc.REMOVE, rationale=WHY, recorded_by="owner")
    assert oc.seed(tmp_path, [d]) == [d] and oc.path_of(tmp_path) is not None
    oc.undo(tmp_path, d["id"])
    assert json.loads((tmp_path / oc.DECISIONS_FILE).read_text(encoding="utf-8"))["decisions"] == []
    assert oc.path_of(tmp_path) is None
    # the owner's undo stands: an emptied record is never seeded again
    assert oc.seed(tmp_path, [d]) == [] and oc.path_of(tmp_path) is None


def test_the_calculator_states_a_chosen_curve():
    from streamcurves import deep_calculator as dc
    text = dc.support_text({"basis": cb.OWNER, "basisStatement": "Scored against thresholds.",
                            "ownerDecision": {"recordedBy": "owner"}})
    assert text == "Scored against thresholds. Chosen by owner."
    borrowed = dc.support_text({"basis": cb.REGIONAL, "borrowedFrom": {"assessmentId": "a"},
                                "basisStatement": "Curve of another STAF assessment.",
                                "referenceSupport": {"status": "local", "nUsable": 9}})
    assert borrowed == "Curve of another STAF assessment." and "stations" not in borrowed


# --------------------------------------------------------------------------- #
# the dialog
# --------------------------------------------------------------------------- #
def test_the_dialog_lists_what_the_owner_can_source_in_a_function():
    state, _bundle, _fields = _opened("interior-plateau/v6")
    state.region_of_applicability.set({"kind": "ecoregion", "code": "71",
                                       "name": "Interior Plateau"})
    view = sd.session_view(state)
    listed = dict(sd.function_metrics("light-thermal-regime", view))
    assert "chem_CHLA" in listed and "withheld" in listed["chem_CHLA"]
    for mk in listed:
        assert osrc.sourceable(mk, built=view["built"]) is None
    assert sd.scoring_functions("chem_CHLA", "light-thermal-regime", view) == [
        "light-thermal-regime"]
    opts = sd.pool("chem_CHLA", view)
    assert opts and all(o["kind"] in osrc.SOURCE_KINDS for o in opts)
    html = str(sd.sources_list(opts, ns=lambda x: x))
    assert sd.ENTERED_KEY in html and "Not available here" in html and EM_DASH not in html
    form = str(sd.entered_form(osrc.agent_config("chem_CHLA"), ns=lambda x: x))
    assert "input.ent_method" in form and EM_DASH not in form
    modal = str(sd.dialog_modal(ns=lambda x: x, title="Add a source to Light", head=None,
                                has_function_pick=True, functions={"f": "F"}, function_id="f"))
    assert 'id="rationale"' in modal and "Use this source" in modal and EM_DASH not in modal


def test_the_new_kinds_have_icons_and_sentences_without_em_dashes():
    import faicons
    for kind in ("owner_entered", "borrowed", "owner_exception"):
        meta = src.kind_meta(kind)
        assert meta["label"] and meta["sentence"] and EM_DASH not in meta["sentence"]
        faicons.icon_svg(meta["icon"])
    for text in (osrc.ENTERED_LIMIT, osrc.BORROWED_LIMIT, *osrc.ENTERED_STATEMENT.values()):
        assert EM_DASH not in text


# --------------------------------------------------------------------------- #
# review of 2026-09-22: what the dialog offers and what a chosen curve states
# --------------------------------------------------------------------------- #
def test_thresholds_on_a_metric_with_no_lower_bound_run_on_below_zero():
    log_scale = {"higher_is_better": True, "units": "log10 mm", "display_name": "Substrate"}
    pts, errors, _labels = osrc.threshold_points(2.0, 0.5, log_scale)
    assert not errors and pts[0]["y"] == 0.0 and pts[0]["x"] < 0

    def score(x):
        for a, b in zip(pts, pts[1:]):
            if a["x"] <= x <= b["x"]:
                return a["y"] + (b["y"] - a["y"]) * (x - a["x"]) / (b["x"] - a["x"])
    # the line itself is unchanged where the fixed criteria draw it: 0.29 at zero,
    # and it now runs on down to 0 instead of holding every lower value at 0.29
    assert score(0.0) == pytest.approx(0.29, abs=0.01) and score(-1.0) < 0.29
    pts, errors, _labels = osrc.threshold_points(1.0, -1.0, log_scale)
    assert not errors and pts[0]["x"] < -1.0
    # a metric that starts at zero keeps the fixed criteria's shape
    fines = {"higher_is_better": False, "units": "percent", "domain_min": 0, "domain_max": 100}
    pts, errors, _labels = osrc.threshold_points(10, 30, fines)
    assert not errors and (pts[0]["x"], pts[0]["y"]) == (0.0, 1.0)
    chla = {"higher_is_better": False, "units": "ug/L"}
    pts, errors, _labels = osrc.threshold_points(5, 20, chla)
    assert not errors and (pts[0]["x"], pts[0]["y"]) == (0.0, 1.0)
    pts, errors, _labels = osrc.threshold_points(-2.0, 1.0, {"higher_is_better": False})
    assert not errors and pts[0]["y"] == 1.0 and pts[0]["x"] < -2.0


def test_a_point_by_point_curve_against_the_metrics_direction_is_said():
    hib = {"higher_is_better": True}
    falling = [{"x": 0.0, "y": 1.0}, {"x": 40.0, "y": 0.0}]
    rising = [{"x": 0.0, "y": 0.0}, {"x": 40.0, "y": 1.0}]
    assert "higher is better" in osrc.direction_warning(falling, hib)
    assert "lower is better" in osrc.direction_warning(rising, {"higher_is_better": False})
    assert osrc.direction_warning(rising, hib) is None
    assert osrc.direction_warning(falling, {"higher_is_better": False}) is None
    assert osrc.direction_warning(falling, {"higher_is_better": None}) is None


def test_another_assessment_offers_its_latest_eligible_version_only():
    for metric in ("chem_CHLA", "bent_TOLRPIND"):
        for o in osrc.library_options(metric, region_code="27"):
            if o["kind"] != osrc.OTHER:
                continue
            aid = o["ref"]["assessmentId"]
            eligible = [v for v in osrc._versions(aid)
                        if lib.version_status(aid, v) in osrc.ELIGIBLE_STATUSES]
            assert o["ref"]["version"] == eligible[0], (metric, aid, o["ref"]["version"])


def test_a_pool_the_build_could_not_form_is_not_offered():
    prov = {"records": [{"rule_id": "REF-06", "subject": "chem_CHLA", "subject_kind": "metric",
                         "verdict": "fail", "computed": {"options_tried": [
                             {"option": "regional_l2", "why": "The target has no Level II region."},
                             {"option": "regional_l1", "n_usable": 40, "region_code": "9",
                              "why": "The recovery test refused it."}]}}]}
    opts = {o["ref"]["option"]: o for o in osrc.refused_options("chem_CHLA", provenance=prov)}
    assert not opts["regional_l2"]["available"]
    assert opts["regional_l2"]["why_not"] == "The target has no Level II region."
    assert opts["regional_l1"]["available"]


def test_a_borrowed_curve_does_not_speak_for_this_ecoregion():
    entry = {"basis": "modeled-reference", "basisLabel": "Modeled reference",
             "referenceSupport": {"status": "modeled", "nUsable": 3150}}
    ann = osrc.borrowed_annotations(entry, assessment_id="eastern-corn-belt-plains",
                                    name="Eastern Corn Belt Plains", version=7,
                                    region={"code": "55", "name": "Eastern Corn Belt Plains"},
                                    content_digest=None)
    assert "not on stations of this ecoregion" not in ann["basisStatement"]
    assert ann["curveCaveats"][1].startswith("In Eastern Corn Belt Plains: too few streams")
    assert "referenceSupport" not in ann


def test_an_owners_curve_carries_no_reference_tier_and_an_exception_says_so():
    state, bundle, fields = _opened("interior-plateau/v6")
    build = fields["reference_build"]
    cfg = osrc.config_for("chem_CHLA", metric_config=fields["metric_config"], build=build)
    opt = osrc.entered_option("chem_CHLA", method=osrc.THRESHOLDS, config=cfg,
                              title="Chlorophyll by judgment", good=5, poor=20)
    state.owner_curve_decisions.set([_source_decision("chem_CHLA", opt, cfg,
                                                      ["light-thermal-regime"])])
    rebuilt = _republish(state, bundle)
    entry = _entries(rebuilt, "chem_CHLA")[0][1]
    assert "referenceTier" not in entry
    if rebuilt.get("referenceTier"):
        assert any(m.get("referenceTier") for b in rebuilt["metricsByFunction"]
                   for m in b["metrics"] if m.get("metricId") != entry["metricId"])
    from streamcurves import deep_calculator as dc
    caveat = "The build refused this source (ACC-05/06: the recovery test refused it)."
    text = dc.support_text({"referenceSupport": {"status": "borrowed_l1", "nUsable": 40,
                                                 "level": "l1", "regionCode": "9"},
                            "ownerException": {"rule": "REF-11"}, "basisLimit": caveat})
    assert text.startswith("40 least-disturbed stations") and text.endswith(caveat)


def test_the_dialog_names_a_place_as_it_is_written():
    view = {"built": set(), "not_selected": set(), "withheld": set(),
            "entries": {"bent_HPRIME": {"label": "From Interior Plateau", "kind": "borrowed",
                                        "owner": {"id": "cd-1"}, "functions": []},
                        "bent_EPT_NTAX": {"label": "Owner exception", "kind": "owner_exception",
                                          "owner": {"id": "cd-2"}, "functions": []}}}
    assert sd.metric_state("bent_HPRIME", None, view)[0] == (
        "from Interior Plateau, chosen by the owner")
    assert sd.metric_state("bent_EPT_NTAX", None, view)[0] == "owner exception"
