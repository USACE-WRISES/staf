"""``config["metricFamilies"]`` selects Layer B families deterministically;
the interactive budget is a tighter copy of the default config."""
from __future__ import annotations

import site_engine.metrics as metrics_pkg
from site_engine import engine, provenance
from test_engine_determinism import _wire


def test_families_filter_skips_xsection(monkeypatch):
    _wire(monkeypatch)

    def boom(record, tree_geoms):
        raise AssertionError("xsection must not run")
    monkeypatch.setitem(metrics_pkg._REGISTRY, "xsection", boom)
    rec = engine.compute_site(40.3112, -83.0561,
                              {"metricFamilies": ["roads", "landcover"]})
    assert rec["status"] == "ok"
    assert rec["input"]["config"]["metricFamilies"] == ["landcover", "roads"]
    assert "imperviousPctWatershed" in rec["metrics"]
    assert "roadDensity" in rec["metrics"]
    assert "entrenchmentRatio" not in rec["metrics"]
    assert "damCount" not in rec["metrics"]


def test_unknown_family_warns(monkeypatch):
    _wire(monkeypatch)
    rec = engine.compute_site(40.3112, -83.0561,
                              {"metricFamilies": ["roads", "nope"]})
    assert rec["status"] == "ok"
    assert any("nope" in w for w in rec["warnings"])
    assert "roadDensity" in rec["metrics"]


def test_none_runs_every_family(monkeypatch):
    _wire(monkeypatch)
    rec = engine.compute_site(40.3112, -83.0561)
    assert rec["input"]["config"]["metricFamilies"] is None
    assert "entrenchmentRatio" in rec["metrics"]
    assert set(metrics_pkg.families()) >= {"dams", "landcover", "roads",
                                           "runoff", "soils", "xsection"}


def test_interactive_config_is_a_tighter_budget():
    assert (provenance.INTERACTIVE_CONFIG["maxReaches"]
            < provenance.DEFAULT_CONFIG["maxReaches"])
    assert (provenance.INTERACTIVE_CONFIG["maxHops"]
            < provenance.DEFAULT_CONFIG["maxHops"])
    cfg = provenance.resolve_config(provenance.INTERACTIVE_CONFIG)
    assert cfg["maxReaches"] == provenance.INTERACTIVE_CONFIG["maxReaches"]
    assert cfg["landcoverBaseline"] is False
