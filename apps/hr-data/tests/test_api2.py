"""The API on a version 2 folder: same routes, exact catchments, no tolerance."""
import importlib
import json
import time

import pytest

import fixture2

ID = fixture2.ID
NEAR_REACH_2 = fixture2.point_deg(52, 130)          # 20 m east of reach 2
BOX_1 = fixture2.box_deg(10, 10, 40, 40)             # inside reach 1's catchment


@pytest.fixture(scope="module")
def client(data_dir2, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("HR_DATA_DIR", str(data_dir2))
    mp.setenv("HR_DATA_JOBS", str(tmp_path_factory.mktemp("jobs2")))
    import app as app_module
    app_module = importlib.reload(app_module)
    yield app_module.server.test_client()
    app_module._stop_workers()
    mp.undo()


def _get(client, url):
    resp = client.get(url)
    return resp, json.loads(resp.get_data())


def _pick(client, **body):
    resp = client.post("/api/pick", json=body)
    assert resp.status_code == 200, resp.get_data()
    job_id = json.loads(resp.get_data())["job"]
    for _ in range(1200):
        _, job = _get(client, f"/api/job/{job_id}")
        if job.get("status") != "running":
            return job
        time.sleep(0.025)
    raise AssertionError("the job did not finish")


def test_health_reports_format_2(client):
    _, body = _get(client, "/api/health")
    assert body["ok"] is True and body["dataset"]["format"] == 2
    assert body["dataset"]["tolerances"] == [] and [v["vpu"] for v in body["dataset"]["vpus"]] == ["9901", "9902"]


def test_catchments_answer_whatever_tolerance_is_asked(client):
    for tol in ("", "&tol=10", "&tol=15"):
        resp, body = _get(client, "/api/catchments?bbox={},{},{},{}{}".format(*BOX_1, tol))
        assert resp.status_code == 200 and [f["properties"]["nhdplusid"] for f in body["features"]] == [ID[1]]
    resp = client.post("/api/catchments", json={"ids": [ID[1], ID[5]], "tol": 15})
    assert len(json.loads(resp.get_data())["features"]) == 2


def test_tree_and_watershed(client):
    _, tree = _get(client, f"/api/tree?nhdplusid={ID[1]}")
    assert tree["status"] == "ok" and tree["nReaches"] == 5
    _, ws = _get(client, f"/api/watershed?nhdplusid={ID[1]}&tol=20")
    assert ws["status"] == "ok" and ws["areaSqkm"] == 5.0 and ws["tolerance_m"] == 0.0
    assert ws["geometry"]["type"] == "Polygon"


def test_pick_slim_catchment_and_watershed(client):
    job = _pick(client, lon=NEAR_REACH_2[0], lat=NEAR_REACH_2[1], method="slim", scope="catchment", tol=15)
    res = job["result"]
    assert job["status"] == "done" and res["status"] == "ok"
    assert res["reach"]["properties"]["nhdplusid"] == ID[2]
    assert res["reach"]["properties"]["snap_m"] == pytest.approx(20, abs=1.5)
    assert res["tolerance_m"] == 0.0 and res["areaSqkm"] == pytest.approx(1.0, abs=1e-4)
    ws = _pick(client, lon=NEAR_REACH_2[0], lat=NEAR_REACH_2[1], method="slim", scope="watershed")["result"]
    assert ws["status"] == "ok" and ws["nReaches"] == 3            # 2, 4 and 5 across the region line
    assert ws["publishedSqkm"] == 3.0 and ws["areaSqkm"] == pytest.approx(3.0, abs=1e-4)
    assert ws["agreement"] == pytest.approx(1.0, abs=1e-4)
