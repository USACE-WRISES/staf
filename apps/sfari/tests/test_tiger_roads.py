"""TIGER roads fallback: the count sums the primary + secondary + local road
layers (layer 2 alone is interstates only and reads zero in rural areas), and
a failed layer fails the whole count rather than returning a partial sum.
Also guards the revived StreamCat tiers whose ws-suffixed columns the bare
names never matched. Fully offline."""
from __future__ import annotations

from sfari import evidence
from sfari.datasources import tiger_roads
from sfari.metrics.base import AnalysisContext


def test_sums_the_three_road_layers(monkeypatch):
    counts = {2: 1, 6: 4, 8: 37}
    calls: list[int] = []

    def fake(layer_id, env, timeout):
        calls.append(layer_id)
        return counts[layer_id]
    monkeypatch.setattr(tiger_roads, "_count_layer", fake)
    assert tiger_roads.roads_near(40.0, -83.0) == 42
    assert calls == [2, 6, 8]


def test_failed_layer_fails_the_count(monkeypatch):
    monkeypatch.setattr(tiger_roads, "_count_layer",
                        lambda layer_id, env, timeout:
                        None if layer_id == 8 else 3)
    assert tiger_roads.roads_near(40.0, -83.0) is None


def _ctx(**extras) -> AnalysisContext:
    ctx = AnalysisContext(lat=40.0, lon=-83.0)
    ctx.extras.update(extras)
    return ctx


def test_road_density_streamcat_tier_reads_the_ws_column():
    # rddens comes back as rddensws; the bare name never matched, so this
    # tier was dead and every site fell to the TIGER proxy.
    res = evidence.ev_road_density(_ctx(streamcat={"rddensws": 1.59}))
    assert res.value == 1.59
    assert res.source == "EPA StreamCat rddens"
    assert res.suggested_likert is not None

    fallback = evidence.ev_road_density(_ctx(streamcat={}, tiger=42))
    assert fallback.value == 42
    assert "TIGER" in fallback.source


def test_dam_storage_tiers_read_the_ws_column():
    res = evidence.ev_impoundments(_ctx(streamcat={"damnrmstorws": 1234.0},
                                        nid=[]))
    assert "storage 1234" in res.value_text

    flow = evidence.ev_natural_flow_regime(
        _ctx(streamcat={"damnrmstorws": 1234.0}, flow=None))
    assert "dam storage 1234" in flow.value_text
