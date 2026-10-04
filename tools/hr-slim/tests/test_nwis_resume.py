"""The NWIS gage pull resumes where NWIS stopped it (``hrbuild.nwis.gages``), offline.

Two regions with three gages each; daily values come two gages to a batch and the service fails
on the second batch the first time. The second run asks only for what is left, writes the same
table a clean run writes, and removes its progress files.
"""
import pyarrow.parquet as pq
import pytest
import shapely

from hrbuild import nwis


@pytest.fixture()
def fake(monkeypatch):
    boxes = {"0101": shapely.box(-70, 44, -69, 45), "0102": shapely.box(-71, 44, -70, 45)}
    monkeypatch.setattr(nwis.sources, "region_outlines", lambda vpus: dict((v, boxes[v]) for v in vpus))
    sites = {"0101": ["0100001", "0100002", "0100003"], "0102": ["0200001", "0200002", "0300003"]}
    asked = {"boxes": [], "daily": []}

    def sites_in_box(w, s, e, n):
        vpu = "0101" if w > -70.5 else "0102"
        asked["boxes"].append(vpu)
        return [{"site": x, "name": f"gage {x}", "lat": 44.5, "lon": -70.0, "da_sqmi": 10.0} for x in sites[vpu]]

    state = {"fail": True}

    def daily_stats(batch):
        asked["daily"].append(list(batch))
        if state["fail"] and len(asked["daily"]) == 2:
            state["fail"] = False
            raise RuntimeError("NWIS did not answer")
        return dict((x, {"n_days": 100, "q50": 1.0}) for x in batch if x != "0300003")
    monkeypatch.setattr(nwis, "sites_in_box", sites_in_box)
    monkeypatch.setattr(nwis, "daily_stats", daily_stats)
    monkeypatch.setattr(nwis, "BATCH", 2)
    monkeypatch.setattr(nwis.time, "sleep", lambda s: None)
    return asked


def test_a_stopped_pull_starts_again_where_it_stopped(fake, tmp_path):
    with pytest.raises(RuntimeError):
        nwis.gages(["0101", "0102"], tmp_path, log=lambda m: None)
    assert (tmp_path / nwis.SITES_CACHE).exists() and (tmp_path / nwis.DAILY_CACHE).exists()
    assert not (tmp_path / "nwis_gages.parquet").exists()
    first = list(fake["daily"])
    result = nwis.gages(["0101", "0102"], tmp_path, log=lambda m: None)
    assert fake["boxes"] == ["0101", "0102"]                       # each region box asked once
    assert fake["daily"][len(first):] == [["0100003", "0200001"], ["0200002", "0300003"]]   # only what was left
    assert result["gages"] == 6 and result["usable"] == 5
    table = pq.read_table(tmp_path / "nwis_gages.parquet").to_pylist()
    assert [r["site"] for r in table] == ["0100001", "0100002", "0100003", "0200001", "0200002", "0300003"]
    assert table[-1]["n_days"] == 0                               # no daily values: kept, unusable
    assert not (tmp_path / nwis.SITES_CACHE).exists() and not (tmp_path / nwis.DAILY_CACHE).exists()
