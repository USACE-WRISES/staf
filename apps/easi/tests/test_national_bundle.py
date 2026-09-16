"""Current application results may only use one completed, matching build."""
import asyncio
import hashlib
import json

import pytest

from easi.national import bundle, client
from test_national_client import _complete_dataset, _write_dataset, COMID


def test_matching_complete_bundle_and_generation(tmp_path):
    manifest = _complete_dataset(tmp_path)
    ds = client.Dataset(str(tmp_path))
    assert ds.require_current() == manifest
    summary = ds.summary()
    assert summary["available"] and summary["vpus"] == ["01"]
    assert summary["dataset_key"] == bundle.dataset_key(manifest)
    assert ds.current_stats(summary["dataset_key"])["build_id"] == "fixture-build"
    with pytest.raises(client.DatasetError, match="changed"):
        ds.require_current("previous-build")


@pytest.mark.parametrize("key,value", [
    ("alternative_id", "alternative-1"), ("method_version", "stale"),
    ("build_status", "running"), ("build_id", None), ("scoring_identity", {}),
    ("criteria_set", "legacy"), ("scores", {}), ("tiles", {}),
])
def test_mismatched_or_incomplete_bundle_never_serves_results(tmp_path, key, value):
    manifest = _complete_dataset(tmp_path)
    manifest[key] = value
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    ds = client.Dataset(str(tmp_path))
    assert not ds.summary()["available"]
    assert ds.summary()["vpus"] == []
    result = asyncio.run(client.open_precomputed_async(COMID, dataset=ds, geometry=False))
    assert result["code"] == "outdated_dataset"


@pytest.mark.parametrize("asset", ["completion.json", "stats.json", "tiles_01.pmtiles",
                                   "scores_01.parquet", "evidence_0102.parquet"])
def test_modified_asset_rejected_after_initial_success(tmp_path, asset):
    _complete_dataset(tmp_path)
    ds = client.Dataset(str(tmp_path))
    assert ds.summary()["available"]
    with (tmp_path / asset).open("ab") as handle:
        handle.write(b"damaged")
    assert not ds.summary()["available"]
    assert ds.asset_path(asset) is None


def test_manifest_refresh_invalidates_row_and_stat_caches(tmp_path):
    manifest = _complete_dataset(tmp_path)
    ds = client.Dataset(str(tmp_path))
    ds.records([COMID])
    ds.stats()
    assert ds._positions and ds._records and ds._index is not None and ds._stats is not None
    manifest["build_id"] = "replacement"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert not ds.summary_refreshed()["available"]
    assert not ds._positions and not ds._records and ds._index is None and ds._stats is None
    assert ds._position_bytes == ds._record_bytes == 0


def test_completion_inventory_cannot_omit_or_relabel_an_asset(tmp_path):
    manifest = _complete_dataset(tmp_path)
    manifest["scores"]["01"]["sha256"] = "2" * 64
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    ds = client.Dataset(str(tmp_path))
    with pytest.raises(client.DatasetError, match="missing or damaged"):
        ds.require_current()


def test_historical_evidence_readable_but_interactive_reports_rejected(tmp_path):
    _write_dataset(tmp_path)
    ds = client.Dataset(str(tmp_path))
    assert COMID in ds.records([COMID])
    result = asyncio.run(client.open_precomputed_async(COMID, dataset=ds, geometry=False))
    assert result["code"] == "outdated_dataset"


@pytest.mark.parametrize("key,value", [("status", "running"), ("scoring_identity", {}),
                                       ("validation", {"passed": True, "reaches": 999})])
def test_hash_consistent_receipt_still_requires_complete_matching_cohort(tmp_path, key, value):
    manifest = _complete_dataset(tmp_path)
    path = tmp_path / "completion.json"
    receipt = json.loads(path.read_text())
    receipt[key] = value
    path.write_text(json.dumps(receipt))
    manifest["assets"]["completion.json"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(client.DatasetError):
        client.Dataset(str(tmp_path)).require_current()


def test_nonobject_manifest_is_unavailable_even_after_a_valid_read(tmp_path):
    _complete_dataset(tmp_path)
    ds = client.Dataset(str(tmp_path))
    assert ds.summary()["available"]
    (tmp_path / "manifest.json").write_text("[]")
    assert not ds.summary()["available"]
