"""Worker-local connection reuse and national transport compatibility."""
from __future__ import annotations

import gc
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from easi.national import client, http
from test_national_client import COMID, _FakeResponse, _write_dataset


@pytest.fixture
def sessions(monkeypatch):
    http.close_sessions()
    created = []

    class FakeSession:
        def __init__(self):
            self.adapters = {}
            self.closed = 0
            created.append(self)

        def mount(self, scheme, adapter):
            self.adapters[scheme] = adapter

        def close(self):
            self.closed += 1

    monkeypatch.setattr(http.requests, "Session", FakeSession)
    yield created
    http.close_sessions()


def test_one_session_per_worker_and_no_added_adapter_retries(sessions):
    first = http.session()
    assert http.session() is first
    assert len(sessions) == 1
    for adapter in first.adapters.values():
        assert adapter._pool_connections == 4
        assert adapter._pool_maxsize == 1
        assert adapter._pool_block is True
        assert adapter.max_retries.total == 0
    barrier = threading.Barrier(2)

    def worker():
        own = http.session()
        barrier.wait(timeout=5)
        assert http.session() is own
        return own

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(worker) for _ in range(2)]
        others = [future.result(timeout=5) for future in futures]
        assert others[0] is not others[1]
        assert all(item is not first and item.closed == 0 for item in others)
    assert all(item.closed == 1 for item in others)
    assert first.closed == 0


def test_completed_thread_owner_is_not_retained(sessions):
    thread = threading.Thread(target=http.session)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    gc.collect()
    assert len(sessions) == 1 and sessions[0].closed == 1
    assert not list(http._OWNERS)


def test_quiescent_cleanup_is_idempotent_and_next_request_reopens(sessions):
    first = http.session()
    http.close_sessions()
    http.close_sessions()
    assert first.closed == 1
    second = http.session()
    assert second is not first and second.closed == 0


def _transport(monkeypatch, responses, *, head_responses=None):
    calls = []
    heads = iter(head_responses or [_FakeResponse(302, headers={"Location": "https://objects.test/a?sig=1"}),
                                   _FakeResponse(302, headers={"Location": "https://objects.test/a?sig=2"})])
    gets = iter(responses)

    def head(url, **kwargs):
        calls.append(("head", url, kwargs))
        return next(heads)

    def get(url, **kwargs):
        calls.append(("get", url, kwargs))
        response = next(gets)
        if isinstance(response, Exception):
            raise response
        return response

    transport = SimpleNamespace(head=head, get=get)
    monkeypatch.setattr(client, "_http_session", lambda: transport)
    return calls


@pytest.mark.parametrize("expired", [403, 404])
@pytest.mark.parametrize("operation", ["range", "download"])
def test_expired_signed_url_retries_once_and_closes_every_response(tmp_path, monkeypatch, expired, operation):
    first = _FakeResponse(expired)
    second = _FakeResponse(206 if operation == "range" else 200, b"payload")
    heads = [_FakeResponse(302, headers={"Location": "https://objects.test/a?sig=1"}),
             _FakeResponse(302, headers={"Location": "https://objects.test/a?sig=2"})]
    calls = _transport(monkeypatch, [first, second], head_responses=heads)
    ds = client.Dataset(base="https://example.test/", cache_dir=tmp_path, timeout=17)
    if operation == "range":
        assert ds.range_reader("asset")(0, 7) == b"payload"
    else:
        assert ds._download("asset", tmp_path / "asset", tmp_path / "asset.sha256", None).read_bytes() == b"payload"
    assert [kind for kind, _, _ in calls] == ["head", "get", "head", "get"]
    assert [url for kind, url, _ in calls if kind == "get"] == [
        "https://objects.test/a?sig=1", "https://objects.test/a?sig=2"]
    assert all(kwargs["timeout"] == 17 for _, _, kwargs in calls)
    assert all(response.closed for response in [first, second, *heads])


@pytest.mark.parametrize("status", [403, 404, 500])
def test_range_failure_is_not_retried_beyond_existing_policy(monkeypatch, status):
    responses = [_FakeResponse(status), _FakeResponse(status)]
    calls = _transport(monkeypatch, responses)
    ds = client.Dataset(base="https://example.test/")
    with pytest.raises(client.DatasetError):
        ds.range_reader("asset")(0, 7)
    count = 2 if status in (403, 404) else 1
    assert len([call for call in calls if call[0] == "get"]) == count
    assert all(response.closed for response in responses[:count])


def test_range_ignored_keeps_existing_slice_and_closes_response(monkeypatch):
    response = _FakeResponse(200, b"0123456789")
    calls = _transport(monkeypatch, [response])
    ds = client.Dataset(base="https://example.test/")
    assert ds.range_reader("asset")(3, 4) == b"3456"
    assert calls[-1][2]["headers"] == {"Range": "bytes=3-6"}
    assert response.closed


def test_hash_failure_closes_download_and_does_not_install_bad_bytes(tmp_path, monkeypatch):
    response = _FakeResponse(200, b"bad asset")
    _transport(monkeypatch, [response])
    ds = client.Dataset(base="https://example.test/", cache_dir=tmp_path)
    target, sidecar = tmp_path / "asset", tmp_path / "asset.sha256"
    assert ds._download("asset", target, sidecar, "0" * 64) is None
    assert response.closed
    assert not target.exists() and not sidecar.exists()
    assert not target.with_suffix(".part").exists()


def test_failed_download_stream_closes_response_without_transport_retry(tmp_path, monkeypatch):
    class BrokenResponse(_FakeResponse):
        def iter_content(self, n):
            yield b"partial"
            raise client.requests.ConnectionError("stream interrupted")

    response = BrokenResponse(200)
    calls = _transport(monkeypatch, [response])
    ds = client.Dataset(base="https://example.test/", cache_dir=tmp_path)
    target = tmp_path / "asset"
    assert ds._download("asset", target, tmp_path / "asset.sha256", None) is None
    assert response.closed and not target.exists()
    assert len([call for call in calls if call[0] == "get"]) == 1


@pytest.mark.parametrize("response", [_FakeResponse(200, b'{"schema_version": 2}'),
                                     _FakeResponse(200, b"bad-json"), _FakeResponse(500)])
def test_manifest_closes_success_invalid_json_and_failure(monkeypatch, response):
    _transport(monkeypatch, [response])
    ds = client.Dataset(base="https://example.test/")
    ds.manifest()
    assert response.closed


def test_local_dataset_never_creates_http_session(tmp_path, monkeypatch):
    _write_dataset(tmp_path)
    monkeypatch.setattr(client, "_http_session", lambda: pytest.fail("local data opened HTTP"))
    ds = client.Dataset(base=str(tmp_path))
    assert ds.huc4_of([COMID]) == {COMID: "0102"}
    assert COMID in ds.records([COMID])
    assert COMID in ds.scores([COMID])
    assert ds.range_reader(client.INDEX)(0, 4) == b"PAR1"
