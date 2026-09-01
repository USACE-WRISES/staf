"""HR feature parsing: typed conversion, sentinel guards, and parity with the
EASI copy when the source tree is present."""
from __future__ import annotations

from pathlib import Path

import pytest

from site_engine import hr

_EASI = Path(__file__).resolve().parents[3] / "apps" / "easi"


def _feat(**props) -> dict:
    base = {
        "nhdplusid": 24000800021917.0, "gnis_name": "Rush Run",
        "reachcode": "05060001001737", "lengthkm": 1.2, "totdasqkm": 2.7176,
        "slope": 0.01767, "fcode": 46003, "ftype": 460, "streamorde": 1,
        "hydroseq": 24000800000444.0, "uphydroseq": 24000800000455.0,
        "dnhydroseq": 24000800000440.0, "vpuid": "0506", "innetwork": 1,
    }
    base.update(props)
    return {"type": "Feature", "properties": base,
            "geometry": {"type": "LineString",
                         "coordinates": [[-83.02, 40.09], [-83.01, 40.10]]}}


def test_parse_types_and_sentinels():
    rec = hr.parse_feature(_feat())
    assert rec["nhdplusid"] == 24000800021917 and isinstance(rec["nhdplusid"], int)
    assert rec["totdasqkm"] == 2.7176 and rec["stream_order"] == 1
    assert hr.parse_feature(_feat(slope=-9998))["slope"] is None
    assert hr.parse_feature(_feat(totdasqkm=0))["totdasqkm"] is None
    assert hr.parse_feature(_feat(nhdplusid=None)) is None
    assert hr.parse_feature(None) is None


def test_chunk_query_escalates_before_failing(monkeypatch):
    # First pass (base timeout, retried) fails; the second pass at the
    # escalated timeout succeeds — a chunk gets real patience before it can
    # fail a whole delineation.
    calls: list[tuple] = []

    def fake_request(url, params, timeout, retries=1):
        calls.append((timeout, retries))
        return {"features": []} if timeout == 120.0 else None
    monkeypatch.setattr(hr, "_request", fake_request)
    data = hr._chunk_query("u", {}, 60.0, 120.0)
    assert data == {"features": []}
    assert calls == [(60.0, 2), (120.0, 1)]


def test_chunk_query_failure_fails_the_call(monkeypatch):
    monkeypatch.setattr(hr, "_request", lambda *a, **k: None)
    assert hr._chunk_query("u", {}, 60.0, 120.0) is None
    # and the level-callers keep the never-partial invariant
    monkeypatch.setattr(hr, "_chunk_query", lambda *a, **k: None)
    assert hr.parents_by_dnhydroseq([1, 2]) is None
    assert hr.catchments_by_ids([1, 2]) is None


def test_post_chunking_sizes(monkeypatch):
    # The walk is id-only in POST chunks of 250; geometry-bearing fetches use
    # POST chunks of 100. Ids are counted from the IN clause.
    calls: list[tuple] = []

    def fake_chunk(url, params, timeout, escalated, **k):
        calls.append((params["where"].count(",") + 1,
                      params["returnGeometry"], k.get("post")))
        return {"features": []}
    monkeypatch.setattr(hr, "_chunk_query", fake_chunk)
    assert hr.parents_by_dnhydroseq(list(range(1, 252))) == []
    assert [c[0] for c in calls] == [250, 1]
    assert all(c[1] == "false" and c[2] is True for c in calls)
    calls.clear()
    assert hr.flowlines_by_ids(list(range(1, 151))) == []
    assert [c[0] for c in calls] == [100, 50]
    assert all(c[1] == "true" for c in calls)
    calls.clear()
    assert hr.catchments_by_ids(list(range(1, 151))) == []
    assert [c[0] for c in calls] == [100, 50]
    monkeypatch.setattr(hr, "_chunk_query", lambda *a, **k: None)
    assert hr.flowlines_by_ids([1, 2]) is None


@pytest.mark.skipif(not _EASI.is_dir(), reason="EASI source not present")
def test_parse_parity_with_easi():
    # The engine's record is a superset (it adds EROM qama); every field the
    # EASI parser produces must match exactly.
    import sys
    sys.path.insert(0, str(_EASI))
    from easi.datasources import nhd_hr as easi_hr

    for f in (_feat(), _feat(slope=-9998), _feat(totdasqkm=0),
              _feat(gnis_name="  "), _feat(uphydroseq=0)):
        ours = hr.parse_feature(f)
        theirs = easi_hr.parse_feature(f)
        assert {k: ours[k] for k in theirs} == theirs
