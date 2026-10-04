"""The id and hydroseq index (``hrslim.reader2.write_index``). NHDPlus V2's COMIDs and hydroseqs are
not grouped by region, so without the index a lookup opens every region whose range covers the
value, and a delivered bundle fetches each region it opens (one New Jersey point fetched 139 of
them, 2026-10-04); with it a lookup opens only the region that holds the value."""
import json

import pytest

import fixture2 as fx
from hrslim import reader2
from hrslim.reader2 import Dataset2


@pytest.fixture()
def root(tmp_path):
    """The fixture network with ranges that overlap across its two regions, as V2's do."""
    folder = fx.build(tmp_path / "data")
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for key in ("id_range", "hydroseq_range"):
        lo = min(v[key][0] for v in m["vpus"].values())
        hi = max(v[key][1] for v in m["vpus"].values())
        for v in m["vpus"].values():
            v[key] = [lo, hi]
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return folder


def _hydroseq(root, reach):
    topo = Dataset2(root)._regions[[v for v, rs in fx.REGIONS.items() if reach in rs][0]].topo()
    return int(topo["hs"][list(topo["ids"]).index(fx.ID[reach])])


def test_without_the_index_a_lookup_opens_every_region_whose_range_covers_it(root):
    asked = []
    found = Dataset2(root, ensure=asked.append)._find("by_id", [fx.ID[5]])
    assert [r.vpu for r, _ in found] == ["9902"]
    assert asked == ["9901", "9902"]


def test_with_the_index_a_lookup_opens_only_the_region_that_holds_the_value(root):
    hs1 = _hydroseq(root, 1)
    assert reader2.write_index(root)["ids"] == 6
    asked = []
    ds = Dataset2(root, ensure=asked.append)
    assert [r.vpu for r, _ in ds._find("by_id", [fx.ID[5]])] == ["9902"]
    assert asked == ["9902"]
    assert ds.reach(hydroseq=hs1).column("nhdplusid").to_pylist() == [fx.ID[1]]
    assert asked == ["9902", "9901"]
    assert ds._find("by_id", [123456]) == [] and ds.catchments_by_ids([123456]) is None
    assert asked == ["9902", "9901"]                     # a value no region holds opens nothing


def _answers(ds, ids, hydroseqs):
    ws = dict((k, v) for k, v in ds.watershed(fx.ID[1]).items() if not k.endswith("Ms"))    # not the timings
    return ([(r.vpu, rows.tolist()) for r, rows in ds._find("by_id", ids)],
            [(r.vpu, rows.tolist()) for r, rows in ds._find("by_hs", hydroseqs)],
            ds.catchments_by_ids(ids).column("nhdplusid").to_pylist(), ws)


def test_answers_are_the_same_with_and_without_the_index(root):
    ids = list(fx.ID.values())
    hydroseqs = [_hydroseq(root, n) for n in fx.ID]
    before = _answers(Dataset2(root), ids, hydroseqs)
    reader2.write_index(root)
    after = _answers(Dataset2(root), ids, hydroseqs)
    assert after == before
    assert before[3]["status"] == "ok" and before[3]["areaSqkm"] == pytest.approx(5.0, abs=1e-6)
