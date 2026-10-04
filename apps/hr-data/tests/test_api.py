import gzip
import importlib
import json
import time

import pytest

import fixture

X0, Y0, ID, D = fixture.X0, fixture.Y0, fixture.ID, fixture.D
NEAR_REACH_2 = (X0 + 0.52 * D, Y0 + 1.3 * D)     # about 17 m from reach 2


@pytest.fixture(scope="module")
def client(data_dir, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("HR_DATA_DIR", str(data_dir))
    mp.setenv("HR_DATA_JOBS", str(tmp_path_factory.mktemp("jobs")))
    import app as app_module
    app_module = importlib.reload(app_module)
    yield app_module.server.test_client()
    mp.undo()


def _get(client, url, **kw):
    resp = client.get(url, **kw)
    return resp, json.loads(resp.get_data())


def test_health(client):
    resp, body = _get(client, "/api/health")
    assert resp.status_code == 200 and body["ok"] is True
    assert [v["vpu"] for v in body["dataset"]["vpus"]] == ["9901", "9902"]


def test_lines_in_a_box(client):
    resp, body = _get(client, f"/api/lines?bbox={X0},{Y0},{X0 + 0.02},{Y0 + 0.029}")
    assert body["type"] == "FeatureCollection" and body["status"] == "ok"
    assert len(body["features"]) == 4
    assert resp.headers["Access-Control-Allow-Origin"] == "*"


def test_lines_box_limits_and_errors(client):
    _, big = _get(client, "/api/lines?bbox=-80,35,-75,40")
    assert big["status"] == "too-large" and big["features"] == []
    resp, bad = _get(client, "/api/lines?bbox=nope")
    assert resp.status_code == 400 and bad["status"] == "bad-request"
    _, empty = _get(client, "/api/lines?bbox=10,10,10.01,10.01")
    assert empty["status"] == "empty"


def test_reach_and_flowlines(client):
    _, by_id = _get(client, f"/api/reach?nhdplusid={ID[2]}")
    assert by_id["features"][0]["properties"]["hydroseq"] == 1002
    _, by_hs = _get(client, "/api/reach?hydroseq=2005")
    assert by_hs["features"][0]["properties"]["nhdplusid"] == ID[5]
    resp = client.post("/api/flowlines", json={"ids": [ID[1], ID[5]]})
    body = json.loads(resp.get_data())
    assert sorted(f["properties"]["nhdplusid"] for f in body["features"]) == sorted([ID[1], ID[5]])
    assert set(body["features"][0]["properties"]) == {"nhdplusid"}


def test_catchments_by_box_and_by_ids(client):
    _, box = _get(client, f"/api/catchments?bbox={X0},{Y0},{X0 + 0.005},{Y0 + 0.005}&tol=10")
    assert [f["properties"]["nhdplusid"] for f in box["features"]] == [ID[1]]
    resp = client.post("/api/catchments", json={"ids": [ID[1], ID[2]], "tol": 20})
    assert len(json.loads(resp.get_data())["features"]) == 2
    resp, bad = _get(client, f"/api/catchments?bbox={X0},{Y0},{X0 + 0.005},{Y0 + 0.005}&tol=15")
    assert resp.status_code == 400


def test_tree_and_watershed(client):
    _, tree = _get(client, f"/api/tree?nhdplusid={ID[1]}")
    assert tree["status"] == "ok" and tree["nReaches"] == 5 and len(tree["ids"]) == 5
    _, refused = _get(client, f"/api/tree?nhdplusid={ID[1]}&max_reaches=2")
    assert refused["status"] == "refused"
    _, ws = _get(client, f"/api/watershed?nhdplusid={ID[1]}&tol=10")
    assert ws["status"] == "ok" and ws["geometry"]["type"] in ("Polygon", "MultiPolygon")
    resp, _ = _get(client, "/api/tree")
    assert resp.status_code == 400


def test_qa(client):
    _, qa = _get(client, f"/api/qa?bbox={X0},{Y0},{X0 + 0.01},{Y0 + 0.01}")
    assert len(qa["features"]) == 2


def test_gzip_when_asked(client):
    resp = client.get(f"/api/lines?bbox={X0},{Y0},{X0 + 0.02},{Y0 + 0.029}",
                      headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("Content-Encoding") == "gzip"
    body = json.loads(gzip.decompress(resp.get_data()))
    assert len(body["features"]) == 4


def test_home_page_serves_the_viewer(client):
    resp = client.get("/")
    assert resp.status_code == 200 and b"NHDPlus HR data" in resp.get_data()


# ------------------------------------------------------------------ fetch jobs
def _wait(client, job_id):
    for _ in range(1200):
        _, body = _get(client, f"/api/job/{job_id}")
        if body.get("status") != "running":
            return body
        time.sleep(0.025)
    raise AssertionError("the job did not finish")


def _pick(client, **body):
    resp = client.post("/api/pick", json=body)
    assert resp.status_code == 200, resp.get_data()
    return _wait(client, json.loads(resp.get_data())["job"])


@pytest.fixture
def usgs(client, tmp_path, monkeypatch):
    """A local USGS-style package stands in for S3: the worker processes read the
    listing from HR_DIRECT_PACKAGES and download into HR_DIRECT_CACHE."""
    import app as app_module
    import gdb_fixture
    from hrslim import direct
    vpu = direct.vpus_at(*NEAR_REACH_2)[0]
    pkg = dict(gdb_fixture.build(tmp_path / "usgs"), vpu=vpu)
    listing = tmp_path / "packages.json"
    listing.write_text(json.dumps(dict([(vpu, pkg)])), encoding="utf-8")
    monkeypatch.setenv("HR_DIRECT_PACKAGES", str(listing))
    monkeypatch.setenv("HR_DIRECT_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(direct, "CACHE_DIR", tmp_path / "cache")
    app_module._stop_workers()                 # the next job starts them with this environment
    yield pkg
    app_module._stop_workers()


def test_pick_slim_catchment_and_watershed(client):
    job = _pick(client, lon=NEAR_REACH_2[0], lat=NEAR_REACH_2[1], method="slim", scope="catchment", tol=10)
    res = job["result"]
    assert job["status"] == "done" and res["status"] == "ok"
    assert res["reach"]["properties"]["nhdplusid"] == ID[2] and res["reach"]["properties"]["snap_m"] < 30
    assert res["polygon"]["properties"]["nhdplusid"] == ID[2] and res["tolerance_m"] == 10
    assert res["areaSqkm"] == pytest.approx(0.96, rel=0.03) and res["vertices"] >= 4
    assert {"find_s", "catchment_s", "total_s"} <= set(job["timings"])
    ws = _pick(client, lon=NEAR_REACH_2[0], lat=NEAR_REACH_2[1], method="slim", scope="watershed")["result"]
    assert ws["status"] == "ok" and ws["nReaches"] == 3          # 2, 4 and 5 across the VPU line
    assert ws["publishedSqkm"] == pytest.approx(2.3) and ws["tolerance_m"] == 20
    assert ws["agreement"] == pytest.approx(3 * 0.96 / 2.3, rel=0.03)


def test_pick_slim_with_no_stream_near(client):
    res = _pick(client, lon=X0 + 0.3, lat=Y0 + 0.3, method="slim", scope="catchment")["result"]
    assert res["status"] == "failed" and "No network stream" in res["reason"]


def test_pick_validation_and_unknown_jobs(client):
    assert client.post("/api/pick", json={"lat": 39}).status_code == 400
    assert client.post("/api/pick", json={"lon": -77, "lat": 39, "method": "ftp"}).status_code == 400
    assert client.post("/api/pick", json={"lon": -77, "lat": 39, "scope": "basin"}).status_code == 400
    assert client.post("/api/pick", json={"lon": -77, "lat": 39, "method": "slim", "tol": 15}).status_code == 400
    resp, body = _get(client, "/api/job/not-a-job")
    assert resp.status_code == 404 and body["status"] == "unknown"
    resp, body = _get(client, "/api/job/0123456789ab")
    assert resp.status_code == 404 and body["status"] == "unknown"


@pytest.mark.parametrize("mode", ["remote", "download"])
def test_pick_usgs_modes(client, usgs, mode):
    job = _pick(client, lon=NEAR_REACH_2[0], lat=NEAR_REACH_2[1], method=mode, scope="watershed")
    res = job["result"]
    assert job["status"] == "done" and res["status"] == "ok", job
    assert res["vpu"] == usgs["vpu"] and res["package"] == usgs["name"]
    assert res["nReaches"] == 2                       # 2 and 4; reach 5 is in the next package
    assert res["publishedSqkm"] == pytest.approx(2.3)
    assert "open_s" in job["timings"] and ("download_s" in job["timings"]) == (mode == "download")
    cat = _pick(client, lon=NEAR_REACH_2[0], lat=NEAR_REACH_2[1], method=mode, scope="catchment")["result"]
    assert cat["status"] == "ok" and cat["polygon"]["properties"]["nhdplusid"] == ID[2]
    assert cat["publishedSqkm"] == pytest.approx(0.95)
    assert cat["agreement"] == pytest.approx(0.96 / 0.95, rel=0.03)


def test_where(client, usgs):
    _, w = _get(client, f"/api/where?lon={NEAR_REACH_2[0]}&lat={NEAR_REACH_2[1]}")
    assert w["vpus"] == [usgs["vpu"]] and w["slim"] == []
    assert w["package"]["name"] == usgs["name"] and w["downloaded"] is False
    resp, _ = _get(client, "/api/where?lon=x")
    assert resp.status_code == 400
