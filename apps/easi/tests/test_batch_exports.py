"""E6 tests: batch ZIP export layout + compactness."""
from __future__ import annotations

import io
import json
import zipfile

from easi.batch import api, exports
from easi.batch import contracts as C
from tests.test_batch_engine import _stub_pipeline   # reuse the offline stubs


def _run(monkeypatch):
    _stub_pipeline(monkeypatch)
    req = C.BatchRequest(sites=[
        C.SiteRequest("HI", 41.0, -83.0),
        C.SiteRequest("LO", 40.0, -83.0),
        C.SiteRequest("BAD", -1.0, -83.0)])        # lat<0 -> failed
    monkeypatch.setattr("easi.batch.runner._RETRY_BACKOFF_S", 0.0)
    return api.run_batch_sync(req)


def test_zip_layout(monkeypatch):
    batch = _run(monkeypatch)
    data = exports.build_batch_zip(batch, include_pdf=False)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())
    for expected in ("manifest.json", "batch-results.json", "run-diagnostics.json",
                     "summary.csv", "metrics.csv", "exclusions.csv"):
        assert expected in names
    # every submitted site has a result.json (including the failed one)
    for sid in ("HI", "LO", "BAD"):
        assert f"sites/{sid}/result.json" in names
    # succeeded sites get report artifacts
    assert "sites/HI/report.csv" in names


def test_batch_results_json_is_compact(monkeypatch):
    batch = _run(monkeypatch)
    data = exports.build_batch_zip(batch, include_pdf=False)
    zf = zipfile.ZipFile(io.BytesIO(data))
    doc = json.loads(zf.read("batch-results.json"))
    assert len(doc["sites"]) == 3
    for s in doc["sites"]:
        assert "_artifacts" not in s.get("metadata", {})   # no heavy source in compact


def test_summary_and_exclusions(monkeypatch):
    batch = _run(monkeypatch)
    data = exports.build_batch_zip(batch, include_pdf=False)
    zf = zipfile.ZipFile(io.BytesIO(data))
    summary = zf.read("summary.csv").decode()
    assert summary.count("\n") >= 4          # header + 3 sites (+ trailing)
    assert "HI" in summary and "BAD" in summary
    exclusions = zf.read("exclusions.csv").decode()
    assert "BAD" in exclusions               # the failed site is listed
    assert "LO" in exclusions                # excluded (ECI below threshold)


def test_pdf_best_effort_single_site(monkeypatch):
    # include_pdf=True must not raise even if a per-site report can't render.
    _stub_pipeline(monkeypatch)
    batch = api.run_batch_sync(C.BatchRequest(sites=[C.SiteRequest("HI", 41.0, -83.0)]))
    data = exports.build_batch_zip(batch, include_pdf=True)
    assert data[:2] == b"PK"                 # a valid ZIP
