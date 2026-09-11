"""The dataset client: local directories, remote release assets, records,
scores, the COMID index, the summary, and opening a precomputed reach."""
from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from easi import pipeline
from easi.national import client, providers, records
from test_batch_parity import BIEGER, REACH_GEOMORPH, STREAMCAT

COMID = 1234567
OTHER = 7654321


def _record(comid=COMID, huc4="0102"):
    return {
        "comid": comid, "huc4": huc4, "huc8": huc4 + "0304", "huc12": huc4 + "03040506",
        "vpu": huc4[:2], "gnis_name": "Test Creek", "streamorde": 3, "fcode": 46006,
        "totdasqkm": 50.0, "lengthkm": 2.0, "slope": 0.005, "sinuosity": 1.2,
        "lat": 40.10, "lon": -83.10,
        "streamcat": dict(STREAMCAT), "nrsa": None,
        "bankfull": {**BIEGER, "extrapolated": False, "fit_range_sqkm": [1.0, 100.0]},
        "attains_exact": {}, "attains_nearby": {}, "wqp_tn": None, "wqp_tp": None,
        "nid_dams": [], "nas_taxa": [], "nas_scope": "huc12",
        "geomorph": dict(REACH_GEOMORPH),
    }


def _write_dataset(root, *, vintage="2026.09"):
    root.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"comid": [COMID, OTHER], "huc4": ["0102", "0102"]}),
                   root / client.INDEX)
    pq.write_table(pa.Table.from_pylist([records.to_row(_record()),
                                         records.to_row(_record(OTHER))]),
                   root / client.evidence_asset("0102"))
    pq.write_table(pa.table({"comid": [COMID], "eci": [0.72], "band": ["Functioning"],
                             "phys": [0.8], "chem": [0.7], "bio": [0.66]}),
                   root / client.scores_asset("01"))
    (root / client.COVERAGE).write_text(json.dumps(
        {"type": "FeatureCollection", "features": []}), encoding="utf-8")
    manifest = {
        "schema_version": 1, "vintage": vintage, "tier": 1, "updated": "2026-09-11T00:00:00Z",
        "method_version": "abc", "reach_length_ft": 1000, "units_total": 222,
        "assets": {client.INDEX: {"sha256": _sha(root / client.INDEX)},
                   client.COVERAGE: {"sha256": _sha(root / client.COVERAGE)}},
        "units": {"0102": {"vpu": "01", "status": "partial", "n_comids": 3, "n_scored": 2,
                           "evidence": {"asset": client.evidence_asset("0102"),
                                        "sha256": _sha(root / client.evidence_asset("0102"))}}},
        "scores": {"01": {"asset": client.scores_asset("01"),
                          "sha256": _sha(root / client.scores_asset("01"))}},
        "tiles": {},
    }
    (root / client.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_local_dataset_reads_records_scores_and_summary(tmp_path):
    _write_dataset(tmp_path / "ds")
    ds = client.Dataset(base=str(tmp_path / "ds"))
    assert ds.local is not None and not ds.remote
    assert ds.huc4_of([COMID, 999]) == {COMID: "0102"}
    recs = ds.records([COMID, 999])
    assert set(recs) == {COMID}
    assert recs[COMID]["streamcat"]["pctimp2019ws"] == 8.0
    assert recs[COMID]["nid_dams"] == [] and recs[COMID]["nrsa"] is None
    scores = ds.scores([COMID, OTHER])
    assert scores[COMID]["band"] == "Functioning" and OTHER not in scores
    s = ds.summary()
    assert s["available"] and s["units_published"] == 1 and s["reaches_scored"] == 2
    assert s["vintage"] == "2026.09" and s["method_current"] is False
    assert ds.coverage()["type"] == "FeatureCollection"


def test_env_override_picks_a_local_directory(tmp_path, monkeypatch):
    _write_dataset(tmp_path / "ds")
    monkeypatch.setenv(client.ENV_BASE, str(tmp_path / "ds"))
    ds = client.Dataset()
    assert ds.local == tmp_path / "ds"


class _FakeResponse:
    def __init__(self, status, content=b"", headers=None, text=""):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
        self.text = text or content.decode("utf-8", "replace")
        self.is_redirect = status in (301, 302)

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_remote_assets_download_once_and_refresh_on_a_new_hash(tmp_path, monkeypatch):
    src = tmp_path / "src"
    manifest = _write_dataset(src)
    base = "https://example.test/releases/download/easi-national-current/"
    calls = {"head": 0, "get": []}

    def head(url, allow_redirects=False, timeout=None):
        calls["head"] += 1
        name = url.rsplit("/", 1)[1]
        return _FakeResponse(302, headers={"Location": f"https://objects.test/{name}?sig=1"})

    def get(url, stream=False, timeout=None, headers=None):
        name = url.rsplit("/", 1)[1].split("?")[0]
        calls["get"].append((name, headers))
        path = src / name
        if not path.exists():
            return _FakeResponse(404)
        data = path.read_bytes()
        if headers and "Range" in headers:
            lo, hi = headers["Range"].split("=")[1].split("-")
            return _FakeResponse(206, data[int(lo):int(hi) + 1])
        return _FakeResponse(200, data)

    monkeypatch.setattr(client.requests, "head", head)
    monkeypatch.setattr(client.requests, "get", get)
    ds = client.Dataset(base=base, cache_dir=tmp_path / "cache", manifest_ttl_s=0)
    assert ds.remote and ds.manifest()["vintage"] == "2026.09"
    recs = ds.records([COMID])
    assert recs[COMID]["comid"] == COMID
    downloaded = [n for n, h in calls["get"] if n.endswith(".parquet")]
    assert downloaded == [client.INDEX, client.evidence_asset("0102")]
    # a second dataset over the same cache reuses the files (hashes match)
    ds2 = client.Dataset(base=base, cache_dir=tmp_path / "cache", manifest_ttl_s=0)
    ds2.records([COMID])
    assert [n for n, h in calls["get"] if n.endswith(".parquet")] == downloaded
    # a changed hash in the manifest forces a re-download
    manifest["units"]["0102"]["evidence"]["sha256"] = "0" * 64
    (src / client.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    ds3 = client.Dataset(base=base, cache_dir=tmp_path / "cache", manifest_ttl_s=0)
    assert ds3.records([COMID]) == {}          # the hash check refuses the stale bytes
    # range reads go through the resolved (signed) url with a Range header
    reader = ds.range_reader(client.INDEX)
    assert reader(0, 4) == (src / client.INDEX).read_bytes()[:4]
    assert any(h and "Range" in h for n, h in calls["get"] if n == client.INDEX)


def test_open_precomputed_scores_the_record_with_live_geometry_only(tmp_path, monkeypatch):
    _write_dataset(tmp_path / "ds")
    ds = client.Dataset(base=str(tmp_path / "ds"))
    seen = {}

    async def fake_delineate_only(lat, lon, reach_ft, comid=None, **kw):
        seen.update(lat=lat, lon=lon, reach_ft=reach_ft, comid=comid)
        anchor = {"anchorKind": "v2Direct", "scoredReach": {"comid": comid, "gnisName": None,
                                                              "drainageAreaSqkm": None,
                                                              "snapLat": lat, "snapLon": lon}}
        ws = {"type": "FeatureCollection", "features": []}
        return {"status": "ok", "siteAnchor": anchor,
                "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_ft},
                "delineation": {"comid": comid, "gnis_name": "(unnamed reach)", "huc8": "01020304",
                                "huc12": None, "drainage_area_sqkm": 49.0,
                                "snapped_lat": lat, "snapped_lon": lon,
                                "watershed_area_sqkm": 50.0, "watershed_source": "nhdplus-v2-basin",
                                "reach_length_ft": reach_ft, "warnings": []},
                "watershed_geojson": ws, "reach_geojson": ws,
                "ctx_inputs": {"lat": lat, "lon": lon, "comid": comid, "huc8": "01020304",
                               "watershed_geojson": ws, "reach_geojson": ws,
                               "drainage_area_sqkm": 49.0, "slope": 0.001, "fcode": 46006,
                               "stream_order": 3, "sinuosity": 1.0, "siteAnchor": anchor,
                               "watershedPolicy": "auto"}}

    monkeypatch.setattr(pipeline, "delineate_only", fake_delineate_only)
    providers.uninstall()
    try:
        base = client.open_precomputed(COMID, dataset=ds, cross_section=False)
    finally:
        providers.uninstall()
    assert base["status"] == "ok"
    assert seen == {"lat": 40.10, "lon": -83.10, "reach_ft": 1000.0, "comid": COMID}
    assert base["delineation"]["huc12"] == "010203040506"
    assert base["delineation"]["gnis_name"] == "Test Creek"
    assert base["report"]["precomputed"]["vintage"] == "2026.09"
    assert base["report"]["ecosystemConditionIndex"] is not None
    assert base["siteAnchor"]["anchorKind"] == "v2Direct"
    missing = client.open_precomputed(999, dataset=ds)
    assert missing["status"] == "error" and missing["code"] == "no_record"


def test_default_dataset_is_a_singleton():
    assert client.default_dataset() is client.default_dataset()


def test_open_precomputed_without_geometry_needs_no_network_and_the_geometry_follows(tmp_path, monkeypatch):
    import asyncio
    _write_dataset(tmp_path / "ds")
    ds = client.Dataset(base=str(tmp_path / "ds"))
    calls = []

    async def fake_delineate_only(lat, lon, reach_ft, comid=None, **kw):
        calls.append(comid)
        ws = {"type": "FeatureCollection", "features": []}
        anchor = {"anchorKind": "v2Direct"}
        return {"status": "ok", "siteAnchor": anchor,
                "delineation": {"comid": comid, "gnis_name": "(unnamed reach)", "huc8": "01020304",
                                "huc12": None, "drainage_area_sqkm": 49.0, "snapped_lat": lat,
                                "snapped_lon": lon, "watershed_area_sqkm": 50.0,
                                "watershed_source": "nhdplus-v2-basin", "reach_length_ft": reach_ft,
                                "warnings": []},
                "watershed_geojson": ws, "reach_geojson": ws,
                "ctx_inputs": {"siteAnchor": anchor, "watershed_geojson": ws, "reach_geojson": ws}}

    monkeypatch.setattr(pipeline, "delineate_only", fake_delineate_only)
    providers.uninstall()
    try:
        base = client.open_precomputed(COMID, dataset=ds, cross_section=False, geometry=False)
    finally:
        providers.uninstall()
    assert base["status"] == "ok" and base["geometry_pending"] is True
    assert calls == []                                        # phase one never delineates
    assert base["watershed_geojson"] is None and base["reach_geojson"] is None and base["siteAnchor"] is None
    d = base["delineation"]
    assert d["comid"] == COMID and d["huc12"] == "010203040506" and d["gnis_name"] == "Test Creek"
    assert d["reach_length_ft"] == 1000.0 and d["snapped_lat"] == 40.10 and d["watershed_area_sqkm"] is None
    assert base["report"]["ecosystemConditionIndex"] is not None
    geo = asyncio.run(client.precomputed_geometry_async(COMID, dataset=ds))
    assert calls == [COMID] and geo["status"] == "ok"
    assert geo["watershed_geojson"]["type"] == "FeatureCollection" and geo["siteAnchor"]["anchorKind"] == "v2Direct"
    assert geo["delineation"]["watershed_area_sqkm"] == 50.0 and geo["delineation"]["huc12"] == "010203040506"
    assert "ctx_inputs" not in geo
    assert asyncio.run(client.precomputed_geometry_async(999, dataset=ds))["code"] == "no_record"
