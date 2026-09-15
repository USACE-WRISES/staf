"""The optional review surface stays read-only, local and scoped to report assets."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import unquote

import pytest
from starlette.applications import Starlette

import local_review as review


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.delenv(review.ENV_ROOT, raising=False)
    monkeypatch.delenv(review.ENV_BASELINE, raising=False)
    root, data = tmp_path / "national", tmp_path / "app-data"
    root.mkdir()
    curve = {"points": [[0, 0], [50, .7], [100, 1]], "n": 40, "nMembers": 45,
             "q25": 50, "q50": 60, "q75": 100, "x39": 27.857143, "x69": 49.285714,
             "status": "usable", "panelTier": "strict", "screen": "least-disturbed-v1"}
    write_json(data / "reference-curves.json", {"schemaVersion": 1, "provenance": {"method": "frozen"},
        "sets": {"woody": {"quantity": "woody_wsrp100", "stratifier": "l2", "higherIsBetter": True,
                            "curves": {"national": curve, "8.3": curve}}}})
    return root, data


def test_no_routes_or_reads_without_explicit_review_root(monkeypatch):
    monkeypatch.delenv(review.ENV_ROOT, raising=False)
    assert review.review_root() is None
    assert review.routes(None) == []


def test_root_must_exist(tmp_path, monkeypatch):
    monkeypatch.setenv(review.ENV_ROOT, str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="existing local"):
        review.review_root()


def test_pending_outputs_render_frozen_fit_and_leave_files_unchanged(fixture):
    root, data = fixture
    before = {p: p.read_bytes() for folder in (root, data) for p in folder.rglob('*') if p.is_file()}
    page = review.render_page(root, data, "regional", "current", "woody|8.3")
    assert "Frozen scoring fit" in page and "Regenerated diagnostic fit" in page
    assert "Pending or unavailable" in page and "rebuild pending" in page
    assert "Reference panel q25: 50" in page and "Fitted knot: 50, 0.7" in page
    assert "0.39" in page and "0.69" in page and "Finite observations" in page
    assert "0.85 / 0.545 / 0.195" in page and "rounds to 13/8/3 scores" in page
    assert "Reference stratum: 8.3" in page
    after = {p: p.read_bytes() for folder in (root, data) for p in folder.rglob('*') if p.is_file()}
    assert before == after


@pytest.mark.parametrize("criteria", ["regional", "legacy"])
def test_comparison_contract_baseline_and_untrusted_text(fixture, monkeypatch, criteria):
    root, data = fixture
    baseline = root / "baseline"
    write_json(baseline / "staging/manifest.json", {"method_version": "old", "updated": "yesterday"})
    monkeypatch.setenv(review.ENV_BASELINE, str(baseline))
    write_json(root / "staging/manifest.json", {"criteria_set": criteria, "method_version": "current"})
    write_json(root / "staging/stats.json", {"groups": {"US": {"indices": {
        "eci": {"n": 5, "p50": .65, "sd": .1, "bands": [1, 1, 3]}}}}})
    write_json(root / "analysis/local-review/comparison.json", {
        "summary_rows": [{"measure": "Matched reaches", "legacy": 5, "current": 5, "change": 0},
                         {"measure": "Class changes", "unit": "share", "current": .25}],
        "states": [{"name": "Virginia", "reaches": 5, "legacy_functioning_share": .1, "current_functioning_share": .3}],
        "functions": [{"name": "<script>alert(1)</script>", "rating_changed_share": .4}],
        "provenance": {"method_version": "current"}})
    page = review.render_page(root, data, criteria, "current")
    assert "method old; build yesterday" in page
    assert "Matched reaches" in page and "Virginia" in page and "30.0%" in page and "40.0%" in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "<td>Yes</td>" in page
    assert "<td>0.650</td>" in page and "<td>60.0%</td>" in page
    assert "<td>25.0%</td>" in page
    assert "Saved baseline and rebuilt results" in page
    assert "saved baseline and both current criteria sets use 13/8/3" in page
    assert "changes in criteria and input evidence" in page
    assert "14/8/2" not in page and "anchor changes" not in page


def test_field_statistics_use_readable_names_and_precision(fixture):
    root, data = fixture
    write_json(data / "functions.json", [{"id": "catchment-hydrology", "name": "Catchment hydrology"}])
    validation = root / "analysis/validation/nrsa_validation.csv"
    validation.parent.mkdir(parents=True)
    validation.write_text("run,subject,region,target,n,n_class,auc_poor,rho,kappa\n"
                          "S0,catchment_hydrology,US,bent_mmi,1357265,1200000,0.5814983898875019,0.2165195640212163,0.08647677164274548\n")
    page = review.render_page(root, data, "regional", "current")
    assert "Catchment hydrology" in page and "Benthic macroinvertebrate MMI" in page
    assert "1,357,265" in page and "<td>0.581</td>" in page and "<td>0.217</td>" in page
    assert "0.5814983898875019" not in page and "0.5 is chance" in page
    assert "Desktop sites" in page and "Class-matched sites" in page and "1,200,000" in page


def test_partial_analysis_refresh_keeps_old_outputs_stale(fixture):
    import os
    root, data = fixture
    meta = root / "analysis/values_meta.json"
    registry = root / "analysis/curves/curve_registry.csv"
    registry.parent.mkdir(parents=True)
    registry.write_text("quantity,level,stratum,split\n")
    write_json(meta, {"method_version": "old"})
    assert not review._fresh_output(registry, meta, review._json(meta), "current")
    write_json(meta, {"method_version": "current"})
    os.utime(registry, (100, 100))
    os.utime(meta, (200, 200))
    assert "Refreshed diagnostic fits are pending" in review.render_page(root, data, "regional", "current")
    os.utime(registry, (300, 300))
    assert "Refreshed diagnostic fits are pending" not in review.render_page(root, data, "regional", "current")


def test_completion_must_match_current_staging_and_frozen_artifact(fixture):
    import hashlib
    root, data = fixture
    write_json(root / "staging/manifest.json", {"criteria_set": "regional", "method_version": "current", "updated": "new"})
    completion = {"status": "complete", "criteria_set": "regional", "method_version": "current",
                  "staging_updated": "old", "completed_at": "today",
                  "frozen_artifact_sha256": hashlib.sha256((data / "reference-curves.json").read_bytes()).hexdigest(),
                  "checks": {k: "passed" for k in ("analysis_audit", "comparison", "landscape_parity")}}
    write_json(root / "analysis/local-review/completion.json", completion)
    assert "Checks pending" in review.render_page(root, data, "regional", "current")
    completion["staging_updated"] = "new"
    write_json(root / "analysis/local-review/completion.json", completion)
    assert "Verified complete at today" in review.render_page(root, data, "regional", "current")
    completion["frozen_artifact_sha256"] = "different"
    write_json(root / "analysis/local-review/completion.json", completion)
    assert "Checks pending" in review.render_page(root, data, "regional", "current")


def test_diagnostic_selection_keeps_region_slope_and_national_distinct():
    rows = [{"quantity": "er_median", "level": "national", "stratum": "national:national", "split": "lt_0.5"},
            {"quantity": "woody", "level": "l2", "stratum": "l2:8.3", "split": ""}]
    assert review._diagnostic_fit(rows, {"quantity": "er_median", "stratifier": "slope_class"}, "lt_0.5") == rows[0]
    assert review._diagnostic_fit(rows, {"quantity": "er_median", "stratifier": "slope_class"}, "national") == {}
    assert review._diagnostic_fit(rows, {"quantity": "woody", "stratifier": "l2"}, "8.3") == rows[1]


def test_legacy_session_does_not_claim_regional_fits_are_active(fixture):
    root, data = fixture
    assert "regional fits below are not used" in review.render_page(root, data, "legacy", "old")


def test_report_routes_allow_only_report_assets_and_loopback(fixture, monkeypatch):
    root, data = fixture
    report = root / "analysis/report"
    report.mkdir(parents=True)
    (report / "index.html").write_text("<h1>Local report</h1>")
    (report / "private.json").write_text('{"secret":1}')
    (root / "analysis/private.html").write_text("outside report")
    from easi import config
    from easi import national
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "criteria_set", lambda: "regional")
    monkeypatch.setattr(national, "method_version", lambda: "current")
    app = Starlette(routes=review.routes(root))

    async def get(path, host="127.0.0.1"):
        messages = []
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "GET", "scheme": "http", "path": unquote(path),
                 "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [], "client": (host, 123), "server": ("127.0.0.1", 8000)}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages).decode()
        return status, body

    async def check():
        status, body = await get("/local-review/")
        assert status == 200 and "Frozen scoring fit" in body
        status, body = await get("/local-review/report/index.html")
        assert status == 200 and "Local report" in body
        for name in ("private.json", "%2e%2e%2fprivate.html", "%2e%2e%5cprivate.html", "missing.png"):
            assert (await get("/local-review/report/" + name))[0] == 404
        assert (await get("/local-review/", "192.0.2.1"))[0] == 403
        assert (await get("/local-review/report/index.html", "192.0.2.1"))[0] == 403
    asyncio.run(check())
