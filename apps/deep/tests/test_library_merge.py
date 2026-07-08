"""Tests for the shared-library merge into DEEP's assessment registry.

STAF_LIBRARY_ROOT points the library at a tmp dir so these never depend on the real
apps/library/ tree.
"""

from __future__ import annotations

import json

import pytest

from deep import assessments, config, library


def _write_library_version(root, assessment_id, version, *, name, region_name):
    vdir = root / "assessments" / assessment_id / f"v{version}"
    vdir.mkdir(parents=True)
    region = {"kind": "ecoregion", "code": "55", "name": region_name}
    bundle = {
        "schemaVersion": 1,
        "tier": "detailed",
        "assessmentId": assessment_id,
        "assessmentName": name,
        "sourceCitation": "Library test",
        "region": region,
        "library": {
            "libraryId": assessment_id,
            "version": version,
            "updatedAt": "2026-07-07T00:00:00Z",
            "region": region,
        },
        "metricsByFunction": [
            {
                "functionId": "catchment-hydrology",
                "functionName": "Catchment hydrology",
                "discipline": "Hydrology",
                "metrics": [
                    {
                        "metricId": "m1",
                        "metricName": "M1",
                        "curve": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]},
                    }
                ],
            }
        ],
    }
    (vdir / "assessment.deep.json").write_text(json.dumps(bundle), encoding="utf-8")
    return bundle


def _write_catalog(root, entries):
    (root / "catalog.json").write_text(
        json.dumps({"schemaVersion": 1, "generatedAt": None, "assessments": entries}),
        encoding="utf-8",
    )


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    return root


def _baked_ids():
    return [a["assessmentId"] for a in config.assessments_doc()["assessments"]]


def test_absent_library_returns_baked(tmp_path, monkeypatch):
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(tmp_path / "absent"))
    assert library.latest_bundles() == []
    assert len(config.assessments()) == len(_baked_ids())


def test_new_library_assessment_is_appended(libroot):
    _write_library_version(
        libroot, "eastern-corn-belt-plains", 1,
        name="ECBP Adapted", region_name="Eastern Corn Belt Plains",
    )
    _write_catalog(
        libroot,
        [{"assessmentId": "eastern-corn-belt-plains", "assessmentName": "ECBP Adapted",
          "region": {"kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains"},
          "latestVersion": 1, "latestUpdatedAt": "2026-07-07T00:00:00Z"}],
    )
    ids = [a["assessmentId"] for a in config.assessments()]
    assert "eastern-corn-belt-plains" in ids
    assert len(ids) == len(_baked_ids()) + 1
    assert config.assessments_by_id()["eastern-corn-belt-plains"]["library"]["version"] == 1


def test_library_wins_over_baked_same_id_no_duplicate(libroot):
    target = _baked_ids()[0]
    _write_library_version(libroot, target, 3, name="Overridden by library", region_name="X")
    _write_catalog(
        libroot,
        [{"assessmentId": target, "assessmentName": "Overridden by library",
          "region": {"kind": "ecoregion", "code": "55", "name": "X"},
          "latestVersion": 3, "latestUpdatedAt": "2026-07-07T00:00:00Z"}],
    )
    by_id = config.assessments_by_id()
    assert by_id[target]["assessmentName"] == "Overridden by library"
    assert by_id[target]["library"]["version"] == 3
    assert len(config.assessments()) == len(_baked_ids())  # replaced, not appended


def test_only_latest_version_is_used(libroot):
    _write_library_version(libroot, "eastern-corn-belt-plains", 1, name="v1", region_name="ECBP")
    _write_library_version(libroot, "eastern-corn-belt-plains", 2, name="v2", region_name="ECBP")
    _write_catalog(
        libroot,
        [{"assessmentId": "eastern-corn-belt-plains", "assessmentName": "v2",
          "region": {"kind": "ecoregion", "code": "55", "name": "ECBP"},
          "latestVersion": 2, "latestUpdatedAt": "2026-07-07T00:00:00Z"}],
    )
    la = assessments.load_predefined("eastern-corn-belt-plains")
    assert la.assessment_name == "v2"
    assert la.raw["library"]["version"] == 2


def test_placeholder_version_zero_is_skipped(libroot):
    _write_catalog(
        libroot,
        [{"assessmentId": "northeastern-highlands", "assessmentName": "NEH",
          "region": {"kind": "ecoregion", "code": "58", "name": "Northeastern Highlands"},
          "latestVersion": 0, "latestUpdatedAt": None}],
    )
    assert library.latest_bundles() == []
    assert len(config.assessments()) == len(_baked_ids())


def test_list_predefined_surfaces_region_and_version(libroot):
    _write_library_version(
        libroot, "eastern-corn-belt-plains", 2,
        name="ECBP Adapted", region_name="Eastern Corn Belt Plains",
    )
    _write_catalog(
        libroot,
        [{"assessmentId": "eastern-corn-belt-plains", "assessmentName": "ECBP Adapted",
          "region": {"kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains"},
          "latestVersion": 2, "latestUpdatedAt": "2026-07-07T00:00:00Z"}],
    )
    entry = next(
        a for a in assessments.list_predefined()
        if a["assessmentId"] == "eastern-corn-belt-plains"
    )
    assert entry["version"] == 2
    assert entry["regionName"] == "Eastern Corn Belt Plains"


def test_uploaded_library_bundle_scores_through_deep(libroot):
    # A library bundle is a valid DEEP upload bundle (contract check).
    bundle = _write_library_version(
        libroot, "eastern-corn-belt-plains", 1, name="ECBP", region_name="ECBP"
    )
    la = assessments.from_bundle(bundle)
    assert la.assessment_id == "eastern-corn-belt-plains"
    assert la.raw["library"]["version"] == 1
    assert assessments.validate_bundle(bundle) == []
