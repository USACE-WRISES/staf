"""The round's replay guarantee: every published version's digests re-derive from what
the version stores.

``inputsDigest`` is recomputed from each version's own manifest with
``provenance.digest_payload_from_manifest`` (a manifest without ``digestSchema`` keeps the
legacy rules verbatim; the national campaign's versions, published 2026-09-29, carry schema 2
and replay under it); ``contentDigest`` from the bundle with ``library.content_digest``.
A version that stores no provenance, or one whose provenance is not a run manifest (an EASI
method version, a state SQT transcription), is skipped by name, never silently.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from streamcurves import library as lib
from streamcurves import methodology
from streamcurves import provenance as pv

APP = Path(__file__).resolve().parents[1]
LIBRARY = APP.parent / "library" / "assessments"
#: provenance documents that are not run manifests (they name their kind)
NOT_BUILT = ("easi-method", "sqt-transcription")
#: published at nBoot 200 before methodology 0.9 put the bootstrap depth in the digest:
#: their stored digests predate the rule and are never "fixed" (the same two
#: tests/test_screening_engine_pin.py skips)
PRE_DEPTH = ("eastern-corn-belt-plains/v2", "northeastern-highlands/v3")


def _versions() -> list[tuple[str, Path]]:
    if not LIBRARY.is_dir():
        return []
    return [(f"{v.parent.name}/{v.name}", v)
            for v in sorted(LIBRARY.glob("*/v*")) if v.is_dir() and v.name[1:].isdigit()]


VERSIONS = _versions()
IDS = [label for label, _ in VERSIONS]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.skipif(not VERSIONS, reason="assessment library not present")
def test_the_library_holds_the_versions_this_round_guards():
    assert len(VERSIONS) >= 40
    assert any(lbl == "interior-plateau/v6" for lbl, _ in VERSIONS)


@pytest.mark.parametrize("label,vdir", VERSIONS, ids=IDS)
def test_inputs_digest_replays_from_the_stored_manifest(label, vdir):
    p = vdir / lib.PROVENANCE_FILE
    if not p.is_file():
        pytest.skip(f"{label}: stores no provenance.json")
    doc = _read(p)
    if doc.get("kind") in NOT_BUILT:
        pytest.skip(f"{label}: a {doc['kind']} record carries no run manifest")
    manifest = doc.get("manifest") or doc
    expected = manifest.get("inputsDigest")
    assert expected, f"{label}: no inputsDigest in the stored manifest"
    if label in PRE_DEPTH:
        pytest.skip(f"{label}: published at nBoot 200 before v0.9 carried the depth")
    payload = pv.digest_payload_from_manifest(manifest)
    if "digestSchema" not in manifest:
        for key in ("digestSchema", "code", "policy", "decisions", "refit", "reviewer_files",
                    "experimental", "nrsa_value_policy"):
            assert key not in payload, f"{label}: the legacy rules gained a key"
    assert methodology.inputs_digest(payload) == expected, f"{label}: digest does not replay"
    assert doc.get("inputsDigest") == expected


@pytest.mark.parametrize("label,vdir", VERSIONS, ids=IDS)
def test_content_digest_replays_from_the_bundle(label, vdir):
    bundle_path = vdir / lib.BUNDLE_FILE
    if not bundle_path.is_file():
        pytest.skip(f"{label}: holds no DEEP bundle (an EASI method version)")
    bundle = _read(bundle_path)
    got = lib.content_digest(bundle)
    assert got == bundle.get("contentDigest"), f"{label}: the bundle's digest does not replay"
    assert got == (bundle.get("library") or {}).get("contentDigest")
    meta = vdir / lib.META_FILE
    if meta.is_file():
        assert _read(meta).get("contentDigest") == got, f"{label}: meta.json disagrees"
    manifest = vdir.parent / "manifest.json"
    if manifest.is_file():
        rows = {int(v.get("version") or 0): v for v in _read(manifest).get("versions") or []}
        row = rows.get(int(vdir.name[1:]))
        assert row is not None and row.get("contentDigest") == got, f"{label}: manifest.json disagrees"
    prov = vdir / lib.PROVENANCE_FILE
    if prov.is_file():
        recorded = _read(prov).get("contentDigest")
        assert recorded in (None, got), f"{label}: provenance.json disagrees"


@pytest.mark.skipif(not VERSIONS, reason="assessment library not present")
def test_the_catalog_names_each_default_versions_digest():
    catalog = _read(LIBRARY.parent / "catalog.json")
    for entry in catalog.get("assessments") or []:
        aid, default = entry.get("assessmentId"), entry.get("defaultVersion")
        bundle_path = LIBRARY / str(aid) / f"v{default}" / lib.BUNDLE_FILE
        if not bundle_path.is_file():
            continue
        assert entry.get("contentDigest") == lib.content_digest(_read(bundle_path)), aid


def test_a_schema_2_manifest_keeps_the_legacy_payload_and_adds_its_keys():
    """A legacy manifest and the same manifest declared under schema 2 share every legacy
    key of the payload; only the schema-2 keys differ, so the schema is the declaration."""
    manifest = {"region": {"code": "71"}, "methodology": {"methodology_version": "x"},
                "configs": [], "inputs": {"nrsa_values": None, "nrsa_sites": None,
                                          "nrsa_dataset": {"datasetId": "multi-cycle-v1",
                                                           "policy": "latest_non_null_index_visit"}},
                "stratifiers": {"registryVersion": 1}, "standingDecisions": {"sha256": "sha256:p", "policyVersion": "1.2"},
                "reviewerInputs": {"decisionsDigest": "sha256:d", "files": {"b.json": "2", "a.json": "1"}},
                "agent": {"codeFingerprint": "c" * 64}}
    legacy = pv.digest_payload_from_manifest(manifest)
    two = pv.digest_payload_from_manifest({**manifest, "digestSchema": 2,
                                           "inputs": {**manifest["inputs"], "refit": {"mode": "all"}}})
    assert {k: v for k, v in two.items() if k in legacy} == legacy
    assert two["digestSchema"] == 2 and two["code"] == "c" * 64
    assert two["policy"] == {"sha256": "sha256:p", "version": "1.2"}
    assert two["decisions"] == "sha256:d" and two["refit"] == "all"
    assert list(two["reviewer_files"]) == ["a.json", "b.json"]
    assert two["nrsa_value_policy"] == "latest_non_null_index_visit" and two["experimental"] is None
    assert methodology.inputs_digest(two) != methodology.inputs_digest(legacy)
    with pytest.raises(ValueError):
        pv.digest_payload_from_manifest({**manifest, "digestSchema": 3})
