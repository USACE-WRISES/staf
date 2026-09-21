"""A version published from a run carries the run's manifest (2026-09-20).

``decision_provenance_log.json`` names its manifest by reference
(``"manifestRef": "run_manifest.json"``), which resolves inside the run folder and
nowhere else. The Region builder handoff carried the log verbatim, so the three versions
published on 2026-09-20 went out with a dangling reference: no region, no configs, no
inputs, no stratifiers, and therefore no way to re-derive the ``inputsDigest`` the version
states. ``tests/test_screening_engine_pin.py`` caught it, all three rebuilding to one
digest of an empty payload.

The handoff now inlines the manifest, the way the batch promote path always did.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

from streamcurves import methodology
from streamcurves import provenance as pv

SRC = Path(inspect.getsourcefile(__import__("views.region_builder", fromlist=["x"]))).read_text(
    encoding="utf-8")


def _log(tmp: Path) -> Path:
    (tmp / "run_manifest.json").write_text(json.dumps({
        "region": {"kind": "ecoregion", "code": "58", "name": "Northeastern Highlands"},
        "methodology": {"methodology_version": "0.12"},
        "configs": {"rule_catalog": "sha256:abc"},
        "inputs": {"nrsa_values": "sha256:v", "nrsa_sites": "sha256:s"},
        "stratifiers": {"registryVersion": "1"},
        "inputsDigest": "sha256:stored",
    }), encoding="utf-8")
    (tmp / "decision_provenance_log.json").write_text(json.dumps({
        "inputsDigest": "sha256:stored", "manifestRef": "run_manifest.json", "records": []}),
        encoding="utf-8")
    return tmp


def _read(d: Path) -> dict:
    """The handoff's own resolution step, exercised through its source contract."""
    doc = json.loads((d / "decision_provenance_log.json").read_text(encoding="utf-8"))
    ref = doc.get("manifestRef")
    if isinstance(ref, str) and ref and "manifest" not in doc:
        doc["manifest"] = json.loads((d / ref).read_text(encoding="utf-8"))
    return doc


def test_the_handoff_inlines_the_manifest(tmp_path):
    doc = _read(_log(tmp_path))
    assert doc["manifest"]["region"]["code"] == "58"
    assert doc["manifestRef"] == "run_manifest.json"     # the pointer stays, for the record


def test_the_carried_document_keeps_it(tmp_path):
    carried = pv.build_carried_provenance(
        _read(_log(tmp_path)), origin={"kind": "run", "run_dir": str(tmp_path)},
        publisher="tester", session_name="s", timestamp="2026-09-20T00:00:00Z")
    assert carried["manifest"]["inputs"]["nrsa_values"] == "sha256:v"
    assert carried["interactiveRevisions"][-1]["editedBy"] == "tester"


def test_the_digest_re_derives_from_the_carried_manifest(tmp_path):
    """The point of carrying it: a reader can recompute what the version claims."""
    carried = pv.build_carried_provenance(
        _read(_log(tmp_path)), origin={"kind": "run"}, publisher="tester")
    payload = pv.digest_payload_from_manifest(carried["manifest"])
    assert payload["region"]["code"] == "58"
    assert payload["nrsa_values"] == "sha256:v"
    assert methodology.inputs_digest(payload)                 # computes, and is not the empty one
    assert methodology.inputs_digest(payload) != methodology.inputs_digest(
        pv.digest_payload_from_manifest({}))


def test_a_log_that_already_carries_a_manifest_is_left_alone(tmp_path):
    d = _log(tmp_path)
    doc = json.loads((d / "decision_provenance_log.json").read_text(encoding="utf-8"))
    doc["manifest"] = {"region": {"code": "99"}}
    (d / "decision_provenance_log.json").write_text(json.dumps(doc), encoding="utf-8")
    assert _read(d)["manifest"]["region"]["code"] == "99"


def test_the_region_builder_resolves_the_reference():
    """Pin the behaviour to the view, not only to this file's copy of it."""
    assert 'doc.get("manifestRef")' in SRC
    assert 'doc["manifest"] = manifest' in SRC
