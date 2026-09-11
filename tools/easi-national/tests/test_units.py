"""Chunk creation from the national HUC8 index."""
from __future__ import annotations

import json

import pytest

from builder import units
from builder.paths import DataRoot


@pytest.fixture
def root(tmp_path):
    r = DataRoot(tmp_path / "data").ensure()
    index = {"02080204": {"n": 900, "vpu": "02", "huc4": "0208"},
             "02080203": {"n": 1200, "vpu": "02", "huc4": "0208"},
             "05050001": {"n": 700, "vpu": "05", "huc4": "0505"}}
    r.huc8_index.write_text(json.dumps(index), encoding="utf-8")
    r.huc4_vpu.write_text(json.dumps({"0208": "02", "0505": "05"}), encoding="utf-8")
    return r


def test_huc8_and_huc4_chunks(root):
    c = units.make_chunk(root, "huc8", "02080204")
    assert c.id == "huc8-02080204" and c.huc8s == ["02080204"] and c.n_comids == 900
    assert c.vpus == ["02"]
    c4 = units.make_chunk(root, "huc4", "0208")
    assert c4.huc8s == ["02080203", "02080204"] and c4.n_comids == 2100
    assert units.Chunk.load(root, "huc4-0208").label == "HUC4 0208"
    assert [c.id for c in units.list_chunks(root)] == ["huc4-0208", "huc8-02080204"]


def test_vpu_chunk_and_unknown_kind(root):
    c = units.make_chunk(root, "vpu", "05")
    assert c.huc8s == ["05050001"] and c.label == "Region 05"
    with pytest.raises(ValueError):
        units.make_chunk(root, "county", "x")
    with pytest.raises(ValueError):
        units.make_chunk(root, "huc4", "9999")


def test_state_chunk_keeps_only_intersecting_known_huc8s(root):
    def fake_fetch(bbox):
        # two HUC8 polygons: one over Virginia, one in the ocean far away
        return [{"type": "Feature", "properties": {"08": "02080204"},
                 "geometry": {"type": "Polygon", "coordinates": [[[-79, 37.5], [-78, 37.5], [-78, 38.5], [-79, 38.5], [-79, 37.5]]]}},
                {"type": "Feature", "properties": {"08": "02080203"},
                 "geometry": {"type": "Polygon", "coordinates": [[[-60, 30], [-59, 30], [-59, 31], [-60, 31], [-60, 30]]]}},
                {"type": "Feature", "properties": {"08": "99999999"},
                 "geometry": {"type": "Polygon", "coordinates": [[[-79, 37.5], [-78, 37.5], [-78, 38.5], [-79, 38.5], [-79, 37.5]]]}}]
    c = units.make_chunk(root, "state", "VA", fetch=fake_fetch)
    assert c.huc8s == ["02080204"] and c.states == ["VA"] and c.label == "Virginia"
