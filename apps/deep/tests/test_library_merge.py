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
    vdir.mkdir(parents=True, exist_ok=True)
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
    # v2 registry reads manifest.json (all versions) + status.json (per-version status);
    # accumulate this version into the manifest, defaulting to preliminary.
    adir = root / "assessments" / assessment_id
    mpath = adir / "manifest.json"
    manifest = json.loads(mpath.read_text("utf-8")) if mpath.is_file() else {
        "schemaVersion": 2, "assessmentId": assessment_id, "versions": []}
    versions = {int(v["version"]) for v in manifest["versions"]} | {version}
    manifest["assessmentName"] = name
    manifest["latestVersion"] = max(versions)
    manifest["versions"] = [{"version": v} for v in sorted(versions)]
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    spath = adir / "status.json"
    status = json.loads(spath.read_text("utf-8")) if spath.is_file() else {
        "schemaVersion": 2, "assessmentId": assessment_id, "history": []}
    status["history"].append({"version": version, "status": "preliminary",
                              "actor": "t", "timestamp": "2026-07-07T00:00:00Z"})
    spath.write_text(json.dumps(status), encoding="utf-8")
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
    # Visible baked ids only. Hidden assessments (the SQTs) are filtered out of the
    # registry by config._is_hidden, so the visible-baked set is what config.assessments()
    # is compared against below.
    return [a["assessmentId"] for a in config.assessments_doc()["assessments"]
            if not config._is_hidden(a)]


def test_absent_library_returns_baked(tmp_path, monkeypatch):
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(tmp_path / "absent"))
    assert library.latest_bundles() == []
    assert len(config.assessments()) == len(_baked_ids())


def test_new_library_assessment_is_appended(libroot):
    # Needs an id the baked registry will never contain (eastern-corn-belt-plains is
    # baked into data/ now, so it would replace rather than append).
    _write_library_version(
        libroot, "test-only-region", 1,
        name="Test Only Region", region_name="Test Only Region",
    )
    _write_catalog(
        libroot,
        [{"assessmentId": "test-only-region", "assessmentName": "Test Only Region",
          "region": {"kind": "ecoregion", "code": "55", "name": "Test Only Region"},
          "latestVersion": 1, "latestUpdatedAt": "2026-07-07T00:00:00Z"}],
    )
    ids = [a["assessmentId"] for a in config.assessments()]
    assert "test-only-region" in ids
    assert len(ids) == len(_baked_ids()) + 1
    assert config.assessments_by_id()["test-only-region"]["library"]["version"] == 1


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


def test_ref_api_default_and_load(libroot):
    _write_library_version(libroot, "test-only-region", 1, name="v1", region_name="X")
    _write_library_version(libroot, "test-only-region", 2, name="v2", region_name="X")
    _write_catalog(libroot, [{"assessmentId": "test-only-region",
                              "assessmentName": "v2", "latestVersion": 2}])
    assert config.default_ref_for("test-only-region") == "test-only-region@v2"
    rec = config.load_ref("test-only-region@v1")
    assert rec is not None and rec["assessmentName"] == "v1"
    by_ref = config.assessments_by_ref()
    assert {"test-only-region@v1", "test-only-region@v2"}.issubset(by_ref)
    la = assessments.load_ref("test-only-region@v2")
    assert la.assessment_name == "v2"


def test_covering_refs_groups_versions_certified_first(libroot, monkeypatch):
    # A polygon covering a known point; two versions, v2 certified.
    poly = {"type": "Polygon", "coordinates": [[[-72, 43], [-70, 43], [-70, 45], [-72, 45], [-72, 43]]]}
    for v, life in ((1, "preliminary"), (2, "certified")):
        vdir = libroot / "assessments" / "nh-test" / f"v{v}"
        vdir.mkdir(parents=True, exist_ok=True)
        region = {"kind": "ecoregion", "code": "58", "name": "NH", "polygon": poly}
        bundle = {"schemaVersion": 1, "tier": "detailed", "assessmentId": "nh-test",
                  "assessmentName": f"NH v{v}", "region": region,
                  "library": {"version": v, "region": region},
                  "metricsByFunction": [{"functionId": "catchment-hydrology",
                                         "functionName": "CH", "discipline": "Hydrology",
                                         "metrics": [{"metricId": "m1",
                                                      "curve": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}}]}]}
        (vdir / "assessment.deep.json").write_text(json.dumps(bundle), encoding="utf-8")
    (libroot / "assessments" / "nh-test" / "manifest.json").write_text(
        json.dumps({"schemaVersion": 2, "assessmentId": "nh-test", "latestVersion": 2,
                    "versions": [{"version": 1}, {"version": 2}]}), encoding="utf-8")
    (libroot / "assessments" / "nh-test" / "status.json").write_text(
        json.dumps({"schemaVersion": 2, "assessmentId": "nh-test", "history": [
            {"version": 1, "status": "preliminary"}, {"version": 2, "status": "certified"}]}),
        encoding="utf-8")
    _write_catalog(libroot, [{"assessmentId": "nh-test", "assessmentName": "NH v2",
                              "latestVersion": 2}])

    covering = assessments.covering_refs(44.0, -71.0, require_polygon=True)
    entry = next(c for c in covering if c["assessmentId"] == "nh-test")
    # certified v2 is the default and leads the ref list
    assert entry["defaultRef"] == "nh-test@v2"
    assert entry["refs"][0] == "nh-test@v2"
    assert set(entry["refs"]) == {"nh-test@v1", "nh-test@v2"}
    assert entry["lifecycleByRef"]["nh-test@v2"] == "certified"
    # a point outside the polygon does not match
    assert not any(c["assessmentId"] == "nh-test"
                   for c in assessments.covering_refs(10.0, 10.0, require_polygon=True))


def test_uploaded_library_bundle_scores_through_deep(libroot):
    # A library bundle is a valid DEEP upload bundle (contract check).
    bundle = _write_library_version(
        libroot, "eastern-corn-belt-plains", 1, name="ECBP", region_name="ECBP"
    )
    la = assessments.from_bundle(bundle)
    assert la.assessment_id == "eastern-corn-belt-plains"
    assert la.raw["library"]["version"] == 1
    assert assessments.validate_bundle(bundle) == []
