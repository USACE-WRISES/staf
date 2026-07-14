"""Tests for streamcurves/library.py — the assessment-library reader/writer.

STAF_LIBRARY_ROOT points the library at a tmp dir so tests never touch the real
apps/library/ tree.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from streamcurves import library as lib
from streamcurves import session_io as sio
from streamcurves.deep_export import build_deep_assessment_bundle

REGION = {"kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains"}


def _bundle():
    rows = {
        "perImperv": {
            "metric": "perImperv",
            "curve_status": "complete",
            "stratum": np.nan,
            "curve_points": pd.DataFrame(
                {"metric_value": [0, 9, 25, 75], "index_score": [1, 0.7, 0.3, 0]}
            ),
        }
    }
    mapping = pd.DataFrame(
        {
            "metric_key": ["perImperv"],
            "discipline": ["Hydrology"],
            "function_label": ["Catchment hydrology"],
            "sort_order": [1],
        }
    )
    return build_deep_assessment_bundle(rows, mapping, {}, {"region": REGION})


def _session_payload():
    fields = {
        "data": pd.DataFrame({"site": ["a", "b"], "value": [1.0, 2.0]}),
        "session_name": "ecbp-draft",
        "region_of_applicability": REGION,
        "app_data_loaded": True,
    }
    return sio.dump_session_fields(fields, session_name="ecbp-draft")


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    return root


def test_root_exists_and_writable(libroot):
    assert lib.library_root() == libroot
    assert lib.exists() is True
    assert lib.writable() is True


def test_publish_writes_v1_and_updates_catalog(libroot):
    meta = {
        "assessmentName": "Eastern Corn Belt Plains",
        "region": REGION,
        "author": "publisher",
        "revisionNotes": "initial",
    }
    v = lib.publish_version("Eastern Corn Belt Plains", meta, _session_payload(), _bundle())
    assert v == 1

    vdir = libroot / "assessments" / "eastern-corn-belt-plains" / "v1"
    assert (vdir / "assessment.deep.json").is_file()
    assert (vdir / "session.streamcurves.json").is_file()
    assert (vdir / "meta.json").is_file()

    bundle = json.loads((vdir / "assessment.deep.json").read_text(encoding="utf-8"))
    assert bundle["assessmentId"] == "eastern-corn-belt-plains"
    assert bundle["library"]["version"] == 1
    assert bundle["library"]["libraryId"] == "eastern-corn-belt-plains"
    assert bundle["library"]["region"] == REGION
    # The bundle's region also carries the outline polygon (for DEEP's map layer); its
    # lean {kind,code,name} fields still match, and the library block stays lean above.
    assert {k: bundle["region"][k] for k in REGION} == REGION
    assert bundle["region"].get("polygon", {}).get("type") in ("Polygon", "MultiPolygon")

    manifest = json.loads(
        (libroot / "assessments" / "eastern-corn-belt-plains" / "manifest.json").read_text("utf-8")
    )
    assert manifest["latestVersion"] == 1
    assert len(manifest["versions"]) == 1
    assert manifest["versions"][0]["revisionNotes"] == "initial"

    catalog = json.loads((libroot / "catalog.json").read_text("utf-8"))
    entry = next(a for a in catalog["assessments"] if a["assessmentId"] == "eastern-corn-belt-plains")
    assert entry["latestVersion"] == 1
    assert entry["region"] == REGION
    assert entry["latestUpdatedAt"] == manifest["versions"][0]["updatedAt"]


def test_publish_second_version_advances_latest_and_keeps_old(libroot):
    meta = {"assessmentName": "Eastern Corn Belt Plains", "region": REGION}
    lib.publish_version("eastern-corn-belt-plains", meta, _session_payload(), _bundle())
    v2 = lib.publish_version(
        "eastern-corn-belt-plains",
        dict(meta, revisionNotes="v2 tweak"),
        _session_payload(),
        _bundle(),
    )
    assert v2 == 2
    assert lib.latest_version("eastern-corn-belt-plains") == 2

    adir = libroot / "assessments" / "eastern-corn-belt-plains"
    assert (adir / "v1" / "assessment.deep.json").is_file()  # old kept for reference
    assert (adir / "v2" / "assessment.deep.json").is_file()

    bundle2 = lib.load_version_bundle("eastern-corn-belt-plains", 2)
    assert bundle2["library"]["version"] == 2
    assert bundle2["library"]["revisionNotes"] == "v2 tweak"


def test_load_version_session_roundtrips(libroot):
    meta = {"assessmentName": "ECBP", "region": REGION}
    lib.publish_version("eastern-corn-belt-plains", meta, _session_payload(), _bundle())
    payload = lib.load_version_session("eastern-corn-belt-plains", 1)
    fields = sio.decode_session_fields(payload)
    assert fields["session_name"] == "ecbp-draft"
    assert fields["region_of_applicability"] == REGION
    pd.testing.assert_frame_equal(
        fields["data"], pd.DataFrame({"site": ["a", "b"], "value": [1.0, 2.0]})
    )


def test_publish_stores_full_session_roundtrip(libroot):
    """The library stores the FULL session (screening tables, curve state, stage
    status) so a published version reopens ready to revise. Pins the store-full
    contract the Open dialog depends on."""
    screening = pd.DataFrame(
        {
            "site_id": ["NRS1", "NRS2"],
            "lat": [44.1, 44.2],
            "lon": [-72.1, -72.2],
            "final_decision": ["retained", "excluded"],
        }
    )
    fields = {
        "data": pd.DataFrame({"site": ["a", "b"], "value": [1.0, 2.0]}),
        "session_name": "ecbp-full",
        "region_of_applicability": REGION,
        "app_data_loaded": True,
        "easi_screening_sites": screening,
        "completed_metrics": {"perImperv": {"curve_status": "complete"}},
        "run_stage_status": {"publish": {"status": "done", "label": "Published ECBP v1."}},
        "screening_run": {"n_screened": 2, "n_retained": 1, "method": "direct_engine"},
        "site_exclusions": [{"site_id": "NRS2", "reason": "reviewer"}],
    }
    payload = sio.dump_session_fields(fields, session_name="ecbp-full")
    meta = {"assessmentName": "ECBP", "region": REGION}
    lib.publish_version("eastern-corn-belt-plains", meta, payload, _bundle())

    restored = sio.decode_session_fields(
        lib.load_version_session("eastern-corn-belt-plains", 1)
    )
    pd.testing.assert_frame_equal(restored["easi_screening_sites"], screening)
    assert restored["completed_metrics"] == {"perImperv": {"curve_status": "complete"}}
    assert restored["run_stage_status"]["publish"]["status"] == "done"
    assert restored["screening_run"]["n_retained"] == 1
    assert restored["site_exclusions"] == [{"site_id": "NRS2", "reason": "reviewer"}]
    assert not (restored.get("run_meta") or {}).get("redacted")


def test_publish_requires_writable(tmp_path, monkeypatch):
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(tmp_path / "does-not-exist"))
    assert lib.writable() is False
    with pytest.raises(RuntimeError, match="not writable"):
        lib.publish_version("x", {"assessmentName": "X"}, _session_payload(), _bundle())


def test_read_catalog_empty_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(tmp_path / "nope"))
    assert lib.read_catalog()["assessments"] == []
    assert lib.list_assessments() == []


def test_region_polygon_geometry_resolves_ecoregion():
    from streamcurves import geo

    geom = geo.region_polygon_geometry("ecoregion", "55")
    assert geom is not None
    assert geom["type"] in ("Polygon", "MultiPolygon")
    assert geom["coordinates"]
    # unknown code / non-geographic kind -> None
    assert geo.region_polygon_geometry("ecoregion", "does-not-exist") is None
    assert geo.region_polygon_geometry("none", "55") is None


def test_publish_attaches_region_polygon_for_ecoregion(libroot):
    """The stored bundle carries the region outline (for DEEP's map layer); the lean
    {kind,code,name} region stays on the catalog, manifest, and library block."""
    meta = {"assessmentName": "ECBP", "region": REGION}
    lib.publish_version("eastern-corn-belt-plains", meta, _session_payload(), _bundle())

    bundle = lib.load_version_bundle("eastern-corn-belt-plains", 1)
    poly = (bundle["region"] or {}).get("polygon")
    assert poly and poly["type"] in ("Polygon", "MultiPolygon") and poly["coordinates"]
    assert bundle["region"]["code"] == "55"

    assert "polygon" not in bundle["library"]["region"]
    assert "polygon" not in (lib.read_manifest("eastern-corn-belt-plains")["region"] or {})
    entry = next(a for a in lib.list_assessments() if a["assessmentId"] == "eastern-corn-belt-plains")
    assert "polygon" not in (entry["region"] or {})
