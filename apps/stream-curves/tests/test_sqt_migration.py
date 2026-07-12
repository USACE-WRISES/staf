"""Tests for scripts/migrate_sqts_to_library.py — migrating the 8 state SQTs into the
assessment library as preliminary v1 versions with state applicability polygons.

STAF_LIBRARY_ROOT is monkeypatched to a tmp dir so these never write into the repo
apps/library/ tree. The migration resolves curves from the real STAF metric library
(docs/assets/data/metric-library), which is read-only.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from streamcurves import library as lib

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_sqts_to_library.py"

EXPECTED = {
    "ak-sqt-adapted": "AK",
    "co-sqt-adapted": "CO",
    "mi-sqt-adapted": "MI",
    "mn-sqt-adapted": "MN",
    "nc-sqt-adapted": "NC",
    "sc-sqt-adapted": "SC",
    "wi-sqt-adapted": "WI",
    "wy-sqt-adapted": "WY",
}


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migrate_sqts_to_library", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mig = _load_migration_module()


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    """A tmp library root via env; intentionally NOT created on disk so the dry-run test
    can prove it writes nothing."""
    root = tmp_path / "library"
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    return root


def test_dry_run_lists_8_sqts_with_polygons_and_writes_nothing(libroot):
    results = mig.migrate(dry_run=True)

    assert len(results) == 8
    assert {r["assessmentId"]: r["stateCode"] for r in results} == EXPECTED
    for r in results:
        assert r["action"] == "would-publish"
        assert r["polygonType"] in ("Polygon", "MultiPolygon")
        assert r["polygonVertices"] > 0
        assert r["metricsResolved"] > 0

    # writes nothing: the library root is never created
    assert not libroot.exists()
    assert lib.list_assessments() == []


def test_real_migration_produces_8_preliminary_v1_entries_with_polygons(libroot):
    results = mig.migrate(dry_run=False)

    published = [r for r in results if r["action"] == "published"]
    assert len(published) == 8
    assert all(r["version"] == 1 for r in published)
    assert {r["assessmentId"]: r["stateCode"] for r in published} == EXPECTED

    catalog = lib.read_catalog()
    assert catalog["schemaVersion"] == 2
    assert len(catalog["assessments"]) == 8

    for aid in EXPECTED:
        assert lib.latest_version(aid) == 1
        assert lib.version_status(aid, 1) == "preliminary"

        bundle = lib.load_version_bundle(aid, 1)
        polygon = (bundle.get("region") or {}).get("polygon")
        assert polygon and polygon["type"] in ("Polygon", "MultiPolygon")
        assert polygon["coordinates"]
        # region stays a state region with the 2-letter code, and the fingerprint is stamped
        assert bundle["region"]["kind"] == "state"
        assert bundle["region"]["code"] == EXPECTED[aid]
        assert bundle["contentDigest"].startswith("sha256:")
        assert bundle["library"]["contentDigest"] == bundle["contentDigest"]

        entry = next(a for a in catalog["assessments"] if a["assessmentId"] == aid)
        assert entry["latestPreliminary"] == 1
        assert entry["defaultVersion"] == 1


def test_migration_is_idempotent(libroot):
    first = mig.migrate(dry_run=False)
    assert all(r["action"] == "published" for r in first)

    second = mig.migrate(dry_run=False)
    assert len(second) == 8
    assert all(r["action"] == "skipped" for r in second)
    # still exactly one version each — no duplicate publishing
    for aid in EXPECTED:
        assert lib.latest_version(aid) == 1


def test_force_publishes_a_new_version(libroot):
    mig.migrate(dry_run=False)
    forced = mig.migrate(dry_run=False, force=True)
    assert all(r["action"] == "published" and r["version"] == 2 for r in forced)
    for aid in EXPECTED:
        assert lib.latest_version(aid) == 2
        # both versions remain preliminary
        assert lib.version_status(aid, 2) == "preliminary"
