"""The reference screen is pinned to the StreamCat lookup engine's legacy
behavior: a fixed policy that reaches the vendored batch config, is recorded
in the manifest, and never enters the digest. The published digests must
still reproduce from their own manifests."""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

from streamcurves import easi_screening
from streamcurves import provenance as pv
from streamcurves import methodology

_REPO = Path(__file__).resolve().parents[3]
_LIBRARY = _REPO / "apps" / "library" / "assessments"
# Published at nBoot 200 before methodology 0.9 put the bootstrap depth in the
# digest; their stored digests predate the rule and are never "fixed".
_PRE_V09_DEPTH = ("eastern-corn-belt-plains/v2", "northeastern-highlands/v3")


def _result(**over) -> dict:
    base = {
        "region": {"l3_code": "71", "name": "Interior Plateau"},
        "screening_method": "direct_engine",
        "screening_counts": {"n_screened": 71, "n_retained": 33},
        "source_reports": [None],
    }
    base.update(over)
    return base


def test_the_pin_is_the_legacy_policy():
    assert easi_screening.SCREENING_WATERSHED_ENGINE == "streamcat-legacy"


def test_screen_sites_direct_pins_the_legacy_watershed_engine(monkeypatch):
    captured: dict = {}

    def fake_run_batch_sync(request, *, on_event=None, cancel=None):
        captured["config"] = request.config

        class _R:
            def to_dict(self_inner):
                return {"sites": []}
        return _R()

    api_mod = types.SimpleNamespace(run_batch_sync=fake_run_batch_sync)
    contracts_mod = types.SimpleNamespace(
        BatchConfig=lambda **kw: types.SimpleNamespace(**kw),
        BatchRequest=lambda *, sites, config, criteria: types.SimpleNamespace(
            sites=sites, config=config, criteria=criteria),
        SiteRequest=lambda **kw: types.SimpleNamespace(**kw))
    # Stub both the sys.modules entry and the package attribute a real import
    # binds: `from ...batch import api` reads the attribute first, so patching
    # sys.modules alone leaves the real engine running whenever the package has
    # already been imported (2026-09-07).
    batch_pkg = importlib.import_module("streamcurves._vendor.easi.batch")
    monkeypatch.setitem(sys.modules, "streamcurves._vendor.easi.batch.api", api_mod)
    monkeypatch.setitem(sys.modules, "streamcurves._vendor.easi.batch.contracts",
                        contracts_mod)
    monkeypatch.setattr(batch_pkg, "api", api_mod, raising=False)
    monkeypatch.setattr(batch_pkg, "contracts", contracts_mod, raising=False)
    easi_screening.screen_sites_direct(
        [{"site_id": "A", "lat": 44.0, "lon": -71.0}], "functional")
    assert captured["config"].watershed_engine == "streamcat-legacy"


def test_the_pin_is_a_vendored_batchconfig_value():
    from streamcurves._vendor.easi.batch.contracts import BatchConfig
    cfg = BatchConfig(watershed_engine=easi_screening.SCREENING_WATERSHED_ENGINE)
    assert cfg.to_dict()["watershed_engine"] == "streamcat-legacy"
    assert BatchConfig.from_dict(cfg.to_dict()).watershed_engine == "streamcat-legacy"
    assert BatchConfig().watershed_engine == "auto"          # EASI's own default
    assert easi_screening.SCREENING_WATERSHED_ENGINE != "auto"
    with pytest.raises(ValueError):
        BatchConfig(watershed_engine="nope")


def test_the_manifest_records_the_pin_inside_the_digest():
    # 2026-09-07: the pin and the COMID mode join the digest under absence
    # semantics. A run that records neither (every version published before
    # either existed) adds no key and reproduces byte for byte; a run that
    # records one must move the digest, because both decide pool membership.
    plain = pv.build_run_manifest(_result(), argv=[])
    pinned = pv.build_run_manifest(_result(
        screening_watershed_engine="streamcat-legacy",
        screening_comid_mode="archive-first",
        screening={"tables": {"easi_screening_criteria": {
            "config": {"watershed_engine": "streamcat-legacy"}}}}), argv=[])
    assert pinned["inputsDigest"] != plain["inputsDigest"]
    assert pinned["inputs"]["easi"]["watershed_engine"] == "streamcat-legacy"
    assert pinned["inputs"]["easi"]["watershed_engine_echo"] == "streamcat-legacy"
    assert pinned["inputs"]["easi"]["comid_mode"] == "archive-first"
    assert plain["inputs"]["easi"]["watershed_engine"] is None
    assert plain["inputs"]["easi"]["comid_mode"] is None
    payload = pv.digest_payload_from_manifest(pinned)
    assert payload["easi_watershed_engine"] == "streamcat-legacy"
    assert payload["easi_comid_mode"] == "archive-first"
    # the engine's own echo is the engine's report, not an input
    assert "watershed_engine_echo" not in json.dumps(payload)
    # an unrecorded policy adds no key at all
    assert "easi_watershed_engine" not in json.dumps(
        pv.digest_payload_from_manifest(plain))


def _published_manifests() -> list[tuple[str, Path]]:
    out = []
    for p in sorted(_LIBRARY.glob("*/v*/provenance.json")):
        out.append((f"{p.parents[1].name}/{p.parent.name}", p))
    return out


@pytest.mark.skipif(not _LIBRARY.is_dir(), reason="assessment library not present")
@pytest.mark.parametrize("label,path", _published_manifests(),
                         ids=[lbl for lbl, _ in _published_manifests()])
def test_published_digests_reproduce_from_their_manifests(label, path):
    if label in _PRE_V09_DEPTH:
        pytest.skip("published at nBoot 200 before v0.9 carried the depth")
    doc = json.loads(path.read_text(encoding="utf-8"))
    manifest = doc.get("manifest") or doc
    expected = manifest.get("inputsDigest")
    assert expected, f"{label}: no inputsDigest in the stored manifest"
    rebuilt = methodology.inputs_digest(pv.digest_payload_from_manifest(manifest))
    assert rebuilt == expected, f"{label}: stored digest no longer reproduces"
