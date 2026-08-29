"""Catchment-aggregation delineation: tree walk, union + validation, budgets,
and honest failure paths. Fully offline (hr client mocked)."""
from __future__ import annotations

from site_engine import delineate, hr


def _rec(nid, hs, dn, geom=None):
    return {"nhdplusid": nid, "hydroseq": hs, "dnhydroseq": dn,
            "uphydroseq": None, "totdasqkm": 3.0, "lengthkm": 0.5,
            "gnis_name": None, "reachcode": None, "slope": 0.01, "fcode": 46003,
            "ftype": 460, "stream_order": 1, "vpuid": "0506",
            "geometry": geom or {"type": "LineString",
                                 "coordinates": [[-83.0, 40.0], [-83.0, 40.01]]}}


def _sq(nid, x0, y0, d=0.01):
    # ~1.11 km x ~0.85 km squares near 40N; area ~0.94 km2 each
    return {"nhdplusid": nid, "areasqkm": None,
            "geometry": {"type": "Polygon", "coordinates": [[
                [x0, y0], [x0 + d, y0], [x0 + d, y0 + d], [x0, y0 + d],
                [x0, y0]]]}}


def test_union_and_validation(monkeypatch):
    # Anchor 1 has parents 2 and 3 (a confluence); no grandparents.
    parents = {(11,): [_rec(2, 12, 11), _rec(3, 13, 11)], (12, 13): []}
    monkeypatch.setattr(hr, "parents_by_dnhydroseq",
                        lambda hs, **k: parents.get(tuple(sorted(hs)), []))
    cats = [_sq(1, -83.00, 40.00), _sq(2, -83.01, 40.00), _sq(3, -83.00, 40.01)]
    monkeypatch.setattr(hr, "catchments_by_ids", lambda ids, **k: cats)
    anchor = _rec(1, 11, 10)
    anchor["totdasqkm"] = 2.83   # close to the true union of the three squares
    out = delineate.delineate_watershed(anchor)
    assert out["status"] == "ok"
    assert out["nReaches"] == 3
    assert out["polygon"] is not None
    assert 2.5 < out["areaSqkm"] < 3.2
    assert abs(out["areaAgreement"] - 1.0) < delineate.AREA_AGREEMENT_WARN
    assert out["warnings"] == []
    assert len(out["treeFlowlines"]) == 3


def test_disagreement_warns(monkeypatch):
    monkeypatch.setattr(hr, "parents_by_dnhydroseq", lambda hs, **k: [])
    monkeypatch.setattr(hr, "catchments_by_ids",
                        lambda ids, **k: [_sq(1, -83.0, 40.0)])
    anchor = _rec(1, 11, 10)
    anchor["totdasqkm"] = 10.0   # union will be ~0.94
    out = delineate.delineate_watershed(anchor)
    assert out["status"] == "ok"
    assert any("disagrees" in w for w in out["warnings"])


def test_budget_refusal(monkeypatch):
    # An endless ladder of parents must hit the hop budget and refuse.
    def endless(hs, **k):
        base = max(hs) + 1
        return [_rec(base, base, max(hs))]
    monkeypatch.setattr(hr, "parents_by_dnhydroseq", endless)
    out = delineate.delineate_watershed(_rec(1, 11, 10), max_hops=5)
    assert out["status"] == "refused"
    assert "budget" in out["reason"]
    assert out["polygon"] is None


def test_tree_query_failure_is_failed_not_partial(monkeypatch):
    monkeypatch.setattr(hr, "parents_by_dnhydroseq", lambda hs, **k: None)
    out = delineate.delineate_watershed(_rec(1, 11, 10))
    assert out["status"] == "failed"
    assert "tree query failed" in out["reason"]


def test_catchment_failure_is_failed(monkeypatch):
    monkeypatch.setattr(hr, "parents_by_dnhydroseq", lambda hs, **k: [])
    monkeypatch.setattr(hr, "catchments_by_ids", lambda ids, **k: None)
    out = delineate.delineate_watershed(_rec(1, 11, 10))
    assert out["status"] == "failed"
    assert "catchment query failed" in out["reason"]
