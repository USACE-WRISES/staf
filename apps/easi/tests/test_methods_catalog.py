"""The display projection of the canonical method catalog (:mod:`easi.methods`).

The worksheet's "Scoring method" panel is a projection of
``data/screening-methods.json`` — the same catalog the evaluator scores from — so the
reference curve, the criteria list, and the rating a site received cannot disagree.
These tests assert the projection is complete, that a what-if recomputation routes back
through the canonical evaluator, and that the displayed ranges are the catalog's bands.
"""
from __future__ import annotations

import asyncio

from easi import assessment, config, methods, screening_methods as sm
from easi.metrics import base, biology, physicochemistry
from easi.metrics.biology import BARRIERS_ID
from easi.metrics.hydraulics import HYPORHEIC_ID
from easi.metrics.hydrology import IMPERVIOUS_ID
from easi.metrics.physicochemistry import IMPAIRMENT_ID

STREAMCAT = {
    "pctimp2019ws": 8.0, "pctwdwet2019ws": 3.0, "pcthbwet2019ws": 2.0,
    "pctcrop2019ws": 15.0, "pcthay2019ws": 10.0, "kffactws": 0.3, "rddensws": 1.2,
    "damnrmstorws": 5000.0, "runoffws": 400.0,
    "pctconif2019wsrp100": 8.0, "pctdecid2019wsrp100": 20.0,
    "pctmxfst2019wsrp100": 12.0, "pctgrs2019wsrp100": 15.0,
    "pctshrb2019wsrp100": 10.0, "pctwdwet2019wsrp100": 3.0,
    "pcthbwet2019wsrp100": 2.0,
    "hydcat": 0.82, "hydws": 0.78, "sedcat": 0.55, "sedws": 0.52,
    "chemcat": 0.74, "chemws": 0.71, "conncat": 0.90, "connws": 0.88,
    "tempcat": 0.85, "tempws": 0.83, "habtcat": 0.80, "habtws": 0.77,
    "prg_bmmiws": 0.71,
}
REACH_GEOMORPH = {"entrenchment_ratio": 2.5, "bank_height_ratio": 1.1,
                  "edge_limited": False, "dem_resolution_m": 10}
BIEGER = {"width_m": 5.0, "depth_m": 1.0, "area_m2": 5.0, "division": "USA",
          "division_name": "National curve", "regional": False}


def _stub(monkeypatch):
    monkeypatch.setattr(assessment.streamcat, "metrics_by_comid",
                        lambda *a, **k: dict(STREAMCAT))
    monkeypatch.setattr(assessment.nlcd, "watershed_landcover", lambda *a, **k: {})
    monkeypatch.setattr(assessment.wbd, "huc12_at_point", lambda *a, **k: "010203040506")
    monkeypatch.setattr(assessment.threedep, "reach_geomorphology",
                        lambda *a, **k: dict(REACH_GEOMORPH))
    monkeypatch.setattr(assessment.bieger, "bankfull_geometry", lambda *a, **k: dict(BIEGER))
    monkeypatch.setattr(assessment.nrsa, "evidence_for_reach", lambda *a, **k: None)
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point", lambda *a, **k: {})
    monkeypatch.setattr(physicochemistry.attains, "impairment_near_point", lambda *a, **k: {})
    monkeypatch.setattr(physicochemistry.wqp, "sample_summary", lambda *a, **k: None)
    monkeypatch.setattr(biology.nas, "established_taxa", lambda *a, **k: [])
    monkeypatch.setattr(biology.nid_barriers, "barriers_near", lambda *a, **k: [])


def _ctx() -> base.AnalysisContext:
    return base.AnalysisContext(lat=40.10, lon=-83.10, comid=1234567, huc8="01020304",
                                drainage_area_sqkm=50.0, slope=0.005, fcode=46006,
                                stream_order=3, sinuosity=1.2)


def test_every_metric_projects_a_renderable_method():
    projected = methods.METHODS
    assert set(config.metrics_by_id()) == set(projected)
    for mid, method in projected.items():
        assert method.mode in {"scalar", "combined", "worst", "count", "categorical"}
        assert method.equation, f"{mid} has no equation"
        assert method.method_key and method.title
        if method.mode == "categorical":
            assert method.decisions
        elif method.mode == "worst":
            assert method.per_input or mid.startswith("nutrient-")   # regional: needs a region
        else:
            assert method.bands and method.domain


def test_panel_recomputation_matches_the_recorded_rating(monkeypatch):
    """A what-if seeded with the site's own inputs reproduces the site's rating."""
    _stub(monkeypatch)
    report = asyncio.run(assessment.assess(_ctx()))
    checked = 0
    for row in report["metricRows"]:
        trace = row.get("scoring")
        if not trace or not row.get("generatedRating"):
            continue
        method = methods.resolve(row["metricId"], trace.get("methodKey"),
                                 trace.get("context"))
        if method is None or method.mode == "categorical":
            continue
        values = {item["key"]: item.get("value") for item in trace["inputs"]}
        result = methods.evaluate_method(method, values)
        assert result["rating"] == row["generatedRating"], row["metricId"]
        checked += 1
    assert checked >= 12


def test_projection_uses_the_catalog_bands():
    """The plotted regions are the evaluator's own bands.

    Only the outer open edges are closed against the plot domain so the first and last
    region have something to draw against; the interior boundaries are the catalog's.
    """
    method = methods.METHODS[HYPORHEIC_ID]
    catalog = sm.method_for(HYPORHEIC_ID)
    assert [b.rating for b in method.bands] == [b["rating"] for b in catalog["bands"]]
    for projected, raw in zip(method.bands, catalog["bands"], strict=True):
        if raw.get("min") is not None:
            assert projected.lo == raw["min"]
        if raw.get("max") is not None:
            assert projected.hi == raw["max"]
    assert method.bands[0].lo == method.domain[0]     # outer edges closed for drawing
    assert method.bands[-1].hi == method.domain[1]


def test_band_range_texts_scalar_and_worst_and_categorical():
    combined = methods.band_range_texts(methods.METHODS[HYPORHEIC_ID])
    assert combined["Good"].endswith("> 0.6") and combined["Poor"].endswith("< 0.3")

    worst = methods.band_range_texts(methods.METHODS[IMPERVIOUS_ID])
    for rating in ("Good", "Fair", "Poor"):          # both indicators, joined
        assert " · " in worst[rating]
    assert "%" in worst["Good"]

    count = methods.band_range_texts(methods.METHODS[BARRIERS_ID])
    assert (count["Good"].startswith("0") and count["Fair"].startswith("1")
            and count["Poor"].startswith(">= 2"))

    assert methods.band_range_texts(methods.METHODS[IMPAIRMENT_ID]) == {}


def test_slider_specs_expand_max_for_a_site_value_beyond_the_domain():
    method = methods.METHODS[HYPORHEIC_ID]
    specs = methods.slider_specs(method, {"slope": 0.5, "sinuosity": 1.2})
    slope = next(spec for spec in specs if spec[0].key == "slope")
    assert slope[2][1] >= 0.5


def test_legacy_trace_model_names_still_resolve():
    """Reports written before the catalog carry mode names, not method keys."""
    assert methods.resolve(HYPORHEIC_ID, "combined") is methods.METHODS[HYPORHEIC_ID]
    assert methods.resolve(HYPORHEIC_ID, None) is methods.METHODS[HYPORHEIC_ID]


def test_citations_resolve_for_every_method():
    for mid in config.metrics_by_id():
        entry = methods.catalog_entry(mid)
        assert methods.citations_for(entry), f"{mid} has no resolvable sources"
