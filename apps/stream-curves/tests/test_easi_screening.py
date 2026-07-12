"""EASI vendoring drift gate + screening mapping (ZIP-import path, offline)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from streamcurves import easi_screening

_VENDOR = Path(__file__).resolve().parents[1] / "streamcurves" / "_vendor" / "easi"
_REPO = Path(__file__).resolve().parents[3]
_SRC = _REPO / "apps" / "easi" / "easi"


def _hash_py(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*.py"))}


def test_vendor_info_present():
    info = json.loads((_VENDOR / "VENDOR_INFO.json").read_text(encoding="utf-8"))
    assert info["engine_api_version"] >= 1
    assert info["manifest"], "empty vendor manifest"


def test_vendored_copy_matches_manifest():
    # The vendored .py files match the hashes recorded at vendor time.
    info = json.loads((_VENDOR / "VENDOR_INFO.json").read_text(encoding="utf-8"))
    current = _hash_py(_VENDOR)
    for rel, digest in info["manifest"].items():
        assert current.get(rel) == digest, f"vendored {rel} diverged from manifest"


@pytest.mark.skipif(not _SRC.is_dir(), reason="EASI source not present (cloud deploy)")
def test_vendor_in_sync_with_source():
    # Drift gate: the source engine has not changed since it was last vendored.
    # If this fails, re-run scripts/vendor_easi_engine.py and commit the result.
    info = json.loads((_VENDOR / "VENDOR_INFO.json").read_text(encoding="utf-8"))
    assert _hash_py(_SRC) == info["manifest"], (
        "EASI engine changed; re-run vendor_easi_engine.py")


def _sample_zip() -> bytes:
    from streamcurves._vendor.easi.batch import contracts as C
    from streamcurves._vendor.easi.batch import exports
    retained = C.SiteResult(
        site_id="REF-1", state="succeeded", eci=0.82, raw_eci=0.82,
        sub_indices={"physical": 0.8, "chemical": 0.8, "biological": 0.86},
        function_scores={"f": 13},
        metrics=[C.MetricRecord(metric_id="m1", function_name="Impervious",
                                final_rating="Good", index=0.85, function_score=13,
                                band="F", status="ok", availability="available")],
        qualification=C.Qualification(auto="qualified", final="retained",
                                      criteria_id="functional"))
    excluded = C.SiteResult(
        site_id="REF-2", state="succeeded", eci=0.30, raw_eci=0.30,
        qualification=C.Qualification(auto="excluded", final="excluded",
                                      criteria_id="functional",
                                      reasons=["eci > 0.69: fail"]))
    batch = C.BatchResult(sites=[retained, excluded], criteria="functional")
    return exports.build_batch_zip(batch, include_pdf=False)


def test_zip_import_to_screening_tables():
    tables = easi_screening.to_screening_tables(
        easi_screening.screen_result_from_zip(_sample_zip()))
    assert {r["site_id"] for r in tables["easi_screening_sites"]} == {"REF-1", "REF-2"}
    assert easi_screening.retained_site_ids(tables) == ["REF-1"]
    # every candidate is preserved with its decision (pass and fail both kept)
    decisions = {r["site_id"]: r["auto_decision"]
                 for r in tables["easi_screening_sites"]}
    assert decisions == {"REF-1": "qualified", "REF-2": "excluded"}
    assert tables["easi_screening_criteria"]["criteria"] == "functional"


def test_bad_zip_rejected():
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("nope.txt", "x")
    with pytest.raises(ValueError):
        easi_screening.screen_result_from_zip(buf.getvalue())


def test_async_direct_entrypoint_is_coroutine():
    import inspect
    assert inspect.iscoroutinefunction(easi_screening.screen_sites_direct_async)


def test_reviewer_override_flips_retained_set():
    # A reviewer excluding a qualified site (or retaining an excluded one) is the
    # "adjust which reference sites EASI screens out" feature: retained_site_ids
    # keys off final_decision, so overrides move the retained set.
    tables = easi_screening.to_screening_tables(
        easi_screening.screen_result_from_zip(_sample_zip()))
    rows = tables["easi_screening_sites"]
    by_id = {r["site_id"]: r for r in rows}
    by_id["REF-1"]["final_decision"] = "excluded"   # reviewer excludes a qualifier
    by_id["REF-1"]["reviewer"] = "reviewer"
    by_id["REF-2"]["final_decision"] = "retained"   # reviewer retains a failure
    assert easi_screening.retained_site_ids(tables) == ["REF-2"]


def test_screen_sites_direct_async_runs_with_stubbed_engine(monkeypatch):
    import asyncio
    import sys
    import types

    calls = {}

    async def fake_run_batch(request, *, on_event=None, cancel=None):
        # Emit one site_done so a progress callback would tick, then return a
        # BatchResult-like object exposing to_dict().
        if on_event is not None:
            on_event("site_done", "REF-1", {})
        calls["cancel_is_callable"] = callable(cancel)

        class _R:
            def to_dict(self_inner):
                return {"sites": [], "criteria": request.criteria}
        return _R()

    api_mod = types.SimpleNamespace(run_batch=fake_run_batch)
    contracts_mod = types.SimpleNamespace(
        BatchConfig=lambda: object(),
        BatchRequest=lambda *, sites, config, criteria: types.SimpleNamespace(
            sites=sites, config=config, criteria=criteria),
        SiteRequest=lambda **kw: types.SimpleNamespace(**kw),
    )
    monkeypatch.setitem(sys.modules, "streamcurves._vendor.easi.batch.api", api_mod)
    monkeypatch.setitem(sys.modules, "streamcurves._vendor.easi.batch.contracts",
                        contracts_mod)

    ticks = {"n": 0}

    def on_event(stage, site_id, info):
        if stage == "site_done":
            ticks["n"] += 1

    result = asyncio.run(easi_screening.screen_sites_direct_async(
        [{"site_id": "REF-1", "lat": 44.0, "lon": -71.0}], "functional",
        on_event=on_event, cancel=lambda: False))
    assert result["criteria"] == "functional"
    assert calls["cancel_is_callable"] is True
    assert ticks["n"] == 1
