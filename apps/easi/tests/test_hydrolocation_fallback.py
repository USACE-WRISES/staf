"""EASI uses its vendored shared raindrop client, preserving its wrappers."""
from easi import routing
from easi._vendor.site_engine import anchor


SNAP = {"comid": 5214461, "snap_lon": -83.0563, "snap_lat": 40.3101}


def test_parser_delegates_to_the_vendored_engine(monkeypatch):
    data = {"type": "FeatureCollection", "features": []}
    seen = []
    monkeypatch.setattr(anchor, "parse_flowtrace", lambda reply: seen.append(reply) or SNAP)
    assert routing._parse_flowtrace(data) == SNAP
    assert seen == [data]


def test_current_flowtrace_intersection_and_malformed_response():
    data = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "id": "nhdFlowline",
        "properties": {"comid": 5214461, "intersection_point": [-83.0563, 40.3101]},
        "geometry": {"type": "LineString", "coordinates": [[-83.06, 40.31], [-83.05, 40.32]]}}]}
    assert routing._parse_flowtrace(data) == SNAP
    assert "error" in routing._parse_flowtrace(None)
    assert "error" in routing._parse_flowtrace({"features": []})
    assert routing._parse_flowtrace({"type": "FeatureCollection", "features": []}) == {}


def test_flowtrace_wrapper_preserves_coordinates_and_timeout(monkeypatch):
    seen = []
    monkeypatch.setattr(anchor, "flowtrace_snap",
                        lambda lat, lon, **k: seen.append((lat, lon, k)) or SNAP)
    assert routing._flowtrace_snap(43.68583, -72.23669, timeout=12) == SNAP
    assert seen == [(43.68583, -72.23669, {"timeout": 12})]
    assert routing.FLOWTRACE_URL == anchor.NLDI_FLOWTRACE_URL


def test_hydrolocation_wrapper_forwards_progress_and_exact_result(monkeypatch):
    progress, seen = [], []

    def shared_client(lat, lon, *, progress=None):
        seen.append((lat, lon))
        progress({"status": "retrying", "attempt": 4})
        return SNAP

    monkeypatch.setattr(anchor, "hydrolocation_snap", shared_client)
    assert routing._hydrolocation_snap(43.68576, -72.23658, progress=progress.append) == SNAP
    assert seen == [(43.68576, -72.23658)]
    assert progress == [{"status": "retrying", "attempt": 4}]


def test_hydrolocation_wrapper_preserves_empty_and_error(monkeypatch):
    for result in ({}, {"error": "flowtrace: HTTP 503"}):
        monkeypatch.setattr(anchor, "hydrolocation_snap", lambda lat, lon: result)
        assert routing._hydrolocation_snap(40, -83) == result
