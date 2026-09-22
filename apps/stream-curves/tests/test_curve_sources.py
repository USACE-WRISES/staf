"""Where every curve an opened version scores comes from (the source panel).

The workspace draws the curves a session did not fit (carried forward, from a
rung above the station pools, fixed criteria), and every one of them opens a
panel naming its source, the facts that identify it and the sources the build
tried before it. These tests read the published versions in the canonical
library, the same fixtures ``test_reference_curves_workspace`` uses.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from streamcurves import curve_sources as src
from streamcurves import curve_svg as cs
from streamcurves import library as lib
from streamcurves import pressure_evidence as pe
from streamcurves import session_io as sio
from views import curve_gallery as cg
from views import source_panel as sp

LIBRARY = Path(__file__).resolve().parents[2] / "library" / "assessments"
VERSIONS = ("northeastern-highlands/v9", "interior-plateau/v6",
            "eastern-corn-belt-plains/v7", "southeastern-plains/v1")
EM_DASH = chr(8212)


def _load(version: str):
    vdir = LIBRARY / version
    if not (vdir / lib.SESSION_FILE).is_file():
        pytest.skip(f"{version} is not in this checkout")
    fields = sio.decode_session_fields(sio.load_session_payload(vdir / lib.SESSION_FILE))
    prov_path = vdir / "provenance.json"
    prov = json.loads(prov_path.read_text(encoding="utf-8")) if prov_path.is_file() else None
    return fields, prov


def _rows(fields):
    return pe.reference_rows(fields["reference_build"], fields["discipline_function_mapping"],
                             built=fields.get("completed_metrics") or {})


def test_every_kind_has_a_label_an_icon_and_a_sentence_without_an_em_dash():
    import faicons
    for kind, meta in src.KINDS.items():
        assert meta["label"] and meta["sentence"], kind
        assert EM_DASH not in meta["label"] + meta["sentence"], kind
        if meta["icon"]:
            faicons.icon_svg(meta["icon"])        # raises on an unknown icon
    assert set(src.KIND_ORDER) == set(src.KINDS)
    # every kind the reference rows produce has its own entry
    assert set(pe.REFERENCE_KIND_LABELS) | {"carried"} <= set(src.KINDS)


@pytest.mark.parametrize("version", VERSIONS)
def test_every_curve_from_another_source_names_its_source(version):
    fields, prov = _load(version)
    build = fields["reference_build"]
    for mk, entry in _rows(fields).items():
        title = src.source_title(mk, entry, build=build)
        facts = dict(src.source_facts(mk, entry, provenance=prov, build=build))
        assert title and facts, mk
        text = title + " ".join(f"{k} {v}" for k, v in facts.items())
        assert EM_DASH not in text, mk
        assert src.chosen_by(mk, entry).startswith("The build, under "), mk
        pts = src.breakpoints(entry)
        assert len(pts) >= 2 and pts == sorted(pts), mk
        if entry["kind"] in ("published_benchmark", "fixed"):
            assert facts["Confidence"].startswith("Not scored"), mk
            assert facts.get("Thresholds"), mk
        if entry["kind"] == "carried":
            assert facts["From"] == f"Version {src.from_version_of(entry, build)} of this assessment"
            assert facts.get("Original source"), mk


def _prov(*records):
    """A provenance document holding ``records`` built by the real constructor."""
    from streamcurves import provenance as pv
    return {"records": [pv._record("run", "55", rule, "metric", mk, computed=computed,
                                   verdict=verdict, recommendation=rec)
                        for rule, mk, computed, verdict, rec in records]}


def test_a_published_benchmark_names_its_catalog_entry_and_the_rungs_before_it():
    fields, _prov_doc = _load("eastern-corn-belt-plains/v7")
    rows = _rows(fields)
    mk = next(k for k, e in rows.items() if e["kind"] == "published_benchmark")
    # the records a 0.14 build writes (provenance._hierarchy_records and
    # _pressure_records); v7 predates them
    doc = _prov(("REF-12", mk, {"why": "42 matched national donors, but the recovery test refused."},
                 "fail", None),
                ("REF-13", mk, {"why": "The model registry holds no approved specification."},
                 "fail", None),
                ("REF-14", mk, {"status": "published", "screen_detail": {
                    "catalogEntry": "nrsa-2018-19-total-nitrogen", "edition": "2018-19"}},
                 "pass", "Scored against a published criterion."))
    facts = dict(src.source_facts(mk, rows[mk], provenance=doc))
    assert facts["Catalog entry"] == "nrsa-2018-19-total-nitrogen, edition 2018-19"
    trail = src.build_trail(mk, rows[mk], provenance=doc)
    assert [s["step"] for s in trail] == ["Station pools", "National reference",
                                          "Modeled reference", "Published benchmark"]
    assert trail[-1]["verdict"] == src.USED
    # every source before it was tried and refused, each with its reason
    assert all(s["verdict"] == src.REFUSED and s["why"] for s in trail[:-1])


def test_a_modeled_curve_names_its_approved_model_and_stops_the_trail_there():
    fields, _prov_doc = _load("eastern-corn-belt-plains/v7")
    rows = _rows(fields)
    mk = next(k for k, e in rows.items() if e["kind"] == "modeled")
    facts = dict(src.source_facts(mk, rows[mk]))
    # the registry's approval, including its date (a bare YAML ``on:`` key)
    assert facts["Model approved"].startswith("by ") and " on 20" in facts["Model approved"]
    assert "stations nationally" in facts["Fitted on"]
    assert facts["Model"].endswith(".")
    doc = _prov(("REF-12", mk, {"why": "The donor pool is not stable."}, "fail", None),
                ("REF-13", mk, {"status": "modeled", "screen_detail": {"coverage": 0.2581}},
                 "pass", "Only 2 stations pass the screen."))
    trail = src.build_trail(mk, rows[mk], provenance=doc)
    assert [s["verdict"] for s in trail] == [src.REFUSED, src.REFUSED, src.USED]
    covered = dict(src.source_facts(mk, rows[mk], provenance=doc))["Natural setting covered"]
    assert covered == "26 percent of this ecoregion's streams"


def test_a_version_older_than_the_records_shows_no_trail():
    fields, prov = _load("eastern-corn-belt-plains/v7")
    for mk, entry in _rows(fields).items():
        if entry["kind"] in ("national", "modeled", "published_benchmark"):
            assert src.build_trail(mk, entry, provenance=prov) == []


def test_no_provenance_means_no_trail_but_the_facts_stand():
    fields, _prov = _load("eastern-corn-belt-plains/v7")
    for mk, entry in _rows(fields).items():
        if entry["kind"] in ("national", "modeled", "published_benchmark"):
            assert src.build_trail(mk, entry, provenance=None) == []
            assert src.source_facts(mk, entry, provenance=None)


def test_a_fixed_criterion_has_no_trail():
    fields, prov = _load("southeastern-plains/v1")
    for mk, entry in _rows(fields).items():
        assert entry["kind"] == "fixed"
        assert src.build_trail(mk, entry, provenance=prov) == []
        assert src.source_title(mk, entry).startswith("EASI fixed criteria")


@pytest.mark.parametrize("version", VERSIONS)
def test_the_panel_renders_for_every_curve(version):
    fields, prov = _load(version)
    build = fields["reference_build"]
    for mk, entry in _rows(fields).items():
        html = str(sp.panel_modal(mk, entry, provenance=prov, build=build))
        assert mk in html and EM_DASH not in html
        assert "curve-tile-point-label" in html          # the breakpoints are labeled
        assert src.kind_label(entry["kind"]) in html or entry["label"] in html


def test_reference_tiles_open_the_panel_and_never_the_analysis():
    fields, _prov = _load("interior-plateau/v6")
    tiles = cg.reference_tiles_for(fields["reference_build"], fields["discipline_function_mapping"],
                                   built=fields.get("completed_metrics") or {})
    assert tiles
    for t in tiles:
        html = str(cg.tile_ui(t, channel_id="summary-curve_gallery_action"))
        assert sp.OPEN_INPUT in html and 'role="button"' in html
        assert '"action": "open"' not in html
        assert t["source_title"]
        assert f"src-{t['source_kind'].replace('_', '-')}" in cs.tile_state_classes(t)


def test_the_open_payload_survives_a_quote():
    click = sp.open_onclick("it's")
    assert sp.OPEN_INPUT in click and "it\\'s" in click


def test_a_fitted_curve_left_out_of_a_function_reads_so_there_only():
    # SELECT-04's record, as a 0.14 build writes it (Central Great Plains left two
    # fitted fish curves out of Population support)
    build = {"method": pe.METHOD, "portfolioSelection": {"population-support": {
        "selected": ["bent_TOTLNTAX"],
        "notSelected": [{"metric": "fish_NAT_NTOLPTAX", "source": "regional"}]}}}
    tile = {"metric": "fish_NAT_NTOLPTAX", "display_name": "Native non-tolerant fish",
            "strata": [], "function_id": "population-support", "also_function_refs": [],
            "also_functions": []}
    cg.mark_not_selected([tile], build)
    assert tile["not_selected_fids"] == ["population-support"]
    html = str(cg.tile_ui(tile, channel_id="c"))
    assert "Not selected here" in html and "is-not-selected" in html
    # the same curve drawn under a function it does score reads as usual
    elsewhere = dict(tile, function_id="community-dynamics")
    assert "Not selected here" not in str(cg.tile_ui(elsewhere, channel_id="c"))
    # and a cross-listed copy is read against the function it sits under
    cross = {"primary_function_name": "Community dynamics"}
    assert "Not selected here" in str(cg.tile_ui(elsewhere, channel_id="c", cross=cross,
                                                  under="population-support"))


def test_the_legend_counts_each_kind_once():
    fields, _prov = _load("northeastern-highlands/v9")
    tiles = cg.reference_tiles_for(fields["reference_build"], fields["discipline_function_mapping"],
                                   built=fields.get("completed_metrics") or {})
    built = [{"metric": "phab_X", "read_only": False}, {"metric": "phab_X", "read_only": False}]
    counts = src.kind_counts(tiles + tiles[:3] + built)
    assert counts["built"] == 1
    assert counts["carried"] == 23 and counts["fixed"] == 5
    assert list(counts) == [k for k in src.KIND_ORDER if k in counts]
    html = str(cg.sources_popover(tiles))
    assert "Where these curves come from" in html and EM_DASH not in html


def test_the_panel_svg_labels_breakpoints_and_keeps_the_default_drawing():
    tile = {"metric": "m", "strata": [{"points": [(0.0, 1.0), (1.0, 0.69), (2.0, 0.39),
                                                    (3.0, 0.0)]}]}
    plain = cs.tile_svg(tile)
    assert "curve-tile-point-label" not in plain and "curve-tile-axis-label" not in plain
    labeled = cs.tile_svg(tile, x_label="mg/L", point_labels=True)
    assert labeled.count("curve-tile-point-label") == 4 and ">mg/L<" in labeled
