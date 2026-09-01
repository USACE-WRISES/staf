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
_SRC_DATA = _REPO / "apps" / "easi" / "data"
_DATA_SKIP = {"source", "__pycache__", ".pytest_cache"}   # mirrors vendor_easi_engine.py


def _hash_py(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*.py"))}


def _hash_data(root: Path, skip: set[str] = frozenset()) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix == ".pyc":
            continue
        rel = p.relative_to(root)
        if any(part in skip for part in rel.parts):
            continue
        out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _hash_package_data(root: Path) -> dict[str, str]:
    # Non-py files inside the package tree (the nested site engine's manifests
    # and geojson data). The package's own copied data/ folder and the top-level
    # VENDOR_INFO.json belong to the other manifests.
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix in (".py", ".pyc"):
            continue
        rel = p.relative_to(root)
        if any(part in _DATA_SKIP for part in rel.parts):
            continue
        if rel.parts[0] == "data" or rel.as_posix() == "VENDOR_INFO.json":
            continue
        out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_vendor_info_present():
    info = json.loads((_VENDOR / "VENDOR_INFO.json").read_text(encoding="utf-8"))
    assert info["engine_api_version"] >= 1
    assert info["manifest"], "empty vendor manifest"
    assert info["data_manifest"], "empty vendor data manifest"
    assert info["package_data_manifest"], "empty package data manifest"


def test_vendored_copy_matches_manifest():
    # The vendored .py and data files match the hashes recorded at vendor time.
    info = json.loads((_VENDOR / "VENDOR_INFO.json").read_text(encoding="utf-8"))
    current = _hash_py(_VENDOR)
    for rel, digest in info["manifest"].items():
        assert current.get(rel) == digest, f"vendored {rel} diverged from manifest"
    current_data = _hash_data(_VENDOR / "data", _DATA_SKIP)
    for rel, digest in info["data_manifest"].items():
        assert current_data.get(rel) == digest, f"vendored data/{rel} diverged from manifest"
    current_pkg = _hash_package_data(_VENDOR)
    for rel, digest in info["package_data_manifest"].items():
        assert current_pkg.get(rel) == digest, f"vendored {rel} diverged from manifest"


@pytest.mark.skipif(not _SRC.is_dir(), reason="EASI source not present (cloud deploy)")
def test_vendor_in_sync_with_source():
    # Drift gate: neither the source engine nor its data catalogs have changed since
    # they were last vendored (scoring criteria live in data/screening-methods.json,
    # so a data-only edit must trip this too).
    # If this fails, re-run scripts/vendor_easi_engine.py and commit the result.
    info = json.loads((_VENDOR / "VENDOR_INFO.json").read_text(encoding="utf-8"))
    assert _hash_py(_SRC) == info["manifest"], (
        "EASI engine changed; re-run vendor_easi_engine.py")
    assert _hash_data(_SRC_DATA, _DATA_SKIP) == info["data_manifest"], (
        "EASI data catalogs changed; re-run vendor_easi_engine.py")
    assert _hash_package_data(_SRC) == info["package_data_manifest"], (
        "EASI package data (the nested site engine) changed; re-run vendor_easi_engine.py")


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
        BatchConfig=lambda **kw: types.SimpleNamespace(**kw),
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
        calls["config"] = None

    async def spy_run_batch(request, *, on_event=None, cancel=None):
        calls["config"] = request.config
        return await fake_run_batch(request, on_event=on_event, cancel=cancel)
    api_mod.run_batch = spy_run_batch

    result = asyncio.run(easi_screening.screen_sites_direct_async(
        [{"site_id": "REF-1", "lat": 44.0, "lon": -71.0}], "functional",
        on_event=on_event, cancel=lambda: False))
    assert result["criteria"] == "functional"
    assert calls["cancel_is_callable"] is True
    assert ticks["n"] == 1
    # the screen is pinned to the StreamCat lookup engine's legacy policy
    assert calls["config"].watershed_engine == "streamcat-legacy"


# --- preset wiring ---------------------------------------------------------- #
def test_preset_choices_resolve_to_engine_presets():
    # An unknown preset name resolves to None in the engine, which silently marks
    # every site not_evaluable and retains nothing. Keep the UI and engine in step.
    from streamcurves._vendor.easi.batch import qualify
    assert set(easi_screening.SCREENING_PRESET_CHOICES) <= set(qualify.PRESETS)
    assert easi_screening.DEFAULT_SCREENING_PRESET in qualify.PRESETS


def test_preset_choice_labels_are_the_condition_bands():
    assert list(easi_screening.SCREENING_PRESET_CHOICES.values()) == [
        "Only Functioning", "Functioning or Functioning-at-Risk", "All sites"]


# --- condition band + failure reporting ------------------------------------- #
def _tables(*sites):
    from streamcurves._vendor.easi.batch import contracts as C
    batch = C.BatchResult(sites=list(sites), criteria="functional")
    return easi_screening.to_screening_tables(batch.to_dict())


def _scored(site_id, eci, raw=None):
    from streamcurves._vendor.easi.batch import contracts as C
    return C.SiteResult(site_id=site_id, state="succeeded", eci=eci,
                        raw_eci=eci if raw is None else raw)


def test_condition_column_labels_each_band():
    rows = _tables(_scored("HI", 0.82), _scored("MID", 0.53),
                   _scored("LO", 0.20))["easi_screening_sites"]
    assert {r["site_id"]: r["condition"] for r in rows} == {
        "HI": "Functioning", "MID": "Functioning-at-Risk", "LO": "Non-Functioning"}


def test_condition_uses_raw_eci_not_the_rounded_display():
    # Both display as 0.69, but they fall on opposite sides of the band boundary.
    # Banding on the rounded value would contradict the retained set.
    rows = _tables(_scored("UP", 0.69, raw=0.694),
                   _scored("DOWN", 0.69, raw=0.688))["easi_screening_sites"]
    by_id = {r["site_id"]: r for r in rows}
    assert by_id["UP"]["condition"] == "Functioning"
    assert by_id["DOWN"]["condition"] == "Functioning-at-Risk"
    assert by_id["UP"]["eci"] == by_id["DOWN"]["eci"] == 0.69


def _failed(site_id, message="delineation failed: No module named 'pynhd'"):
    from streamcurves._vendor.easi.batch import contracts as C
    return C.SiteResult(
        site_id=site_id, state="failed",
        issues=[C.Issue(code="engine_dependency_missing", severity="error",
                        stage="delineation", site_id=site_id, message=message)],
        qualification=C.Qualification(auto="not_evaluable", final="pending",
                                      reasons=["eci > 0.69: skip (no data)"]))


def test_failed_site_reports_the_real_cause_not_the_criteria_text():
    row = _tables(_failed("BAD"))["easi_screening_sites"][0]
    assert row["issue_code"] == "engine_dependency_missing"
    assert "pynhd" in row["issue"]
    # The reason column is what the user reads; it must not say "skip (no data)".
    assert "pynhd" in row["reason"]
    assert "skip (no data)" not in row["reason"]
    assert row["condition"] is None


def test_partial_site_is_not_treated_as_failed():
    # A partial site carries info-level metric_unavailable issues; those are not
    # the blocking cause and must not overwrite its criteria reason.
    from streamcurves._vendor.easi.batch import contracts as C
    site = C.SiteResult(
        site_id="P", state="partial", eci=0.82, raw_eci=0.82,
        issues=[C.Issue(code="metric_unavailable", severity="info", stage="metrics",
                        site_id="P", message="no data")],
        qualification=C.Qualification(auto="qualified", final="retained",
                                      reasons=["eci > 0.69: pass"]))
    row = _tables(site)["easi_screening_sites"][0]
    assert row["issue"] == ""
    assert row["reason"] == "eci > 0.69: pass"


def test_diagnostics_reach_the_criteria_table():
    from streamcurves._vendor.easi.batch import contracts as C
    batch = C.BatchResult(sites=[_scored("A", 0.82)], criteria="functional")
    batch.diagnostics = {"retries": 2, "timeouts": 1}
    tables = easi_screening.to_screening_tables(batch.to_dict())
    assert tables["easi_screening_criteria"]["diagnostics"]["retries"] == 2


# --- outcome accounting ----------------------------------------------------- #
def test_summarize_keeps_unassessed_separate_from_excluded():
    rows = [
        {"site_id": "A", "state": "succeeded", "final_decision": "retained"},
        {"site_id": "B", "state": "succeeded", "final_decision": "excluded"},
        {"site_id": "C", "state": "failed", "final_decision": "pending"},
        {"site_id": "D", "state": "cancelled", "final_decision": "pending"},
    ]
    c = easi_screening.summarize_screening_rows(rows)
    assert c["n_screened"] == 4
    assert c["n_retained"] == 1
    assert c["n_excluded"] == 1          # not 3: the engine failures are not screen-outs
    assert c["n_unresolved"] == 2
    assert c["n_failed"] == 1 and c["n_cancelled"] == 1


def test_exclusion_records_tag_provenance():
    rows = [
        {"site_id": "A", "state": "succeeded", "final_decision": "retained"},
        {"site_id": "B", "state": "succeeded", "final_decision": "excluded",
         "reason": "eci > 0.69: fail"},
        {"site_id": "C", "state": "failed", "final_decision": "pending",
         "issue": "No module named 'pynhd'", "reason": "No module named 'pynhd'"},
        {"site_id": "D", "state": "succeeded", "final_decision": "excluded",
         "reviewer": "reviewer", "reason": "manual", "reviewer_note": "off-network"},
    ]
    by_id = {r["site_id"]: r for r in easi_screening.exclusion_records(rows)}
    assert "A" not in by_id                       # retained sites are not exclusions
    assert by_id["B"]["source"] == "screening"
    assert by_id["C"]["source"] == "unresolved"   # never assessed, not screened out
    assert "pynhd" in by_id["C"]["reason"]
    assert by_id["D"]["source"] == "reviewer"


# --- seeded COMIDs ---------------------------------------------------------- #
def test_bundled_nrsa_comids_cover_the_site_that_failed_to_snap():
    # NRS18_OH_10043's live snap 502s intermittently; EPA already publishes its
    # reach, so screening should never need to snap it at all.
    from streamcurves._vendor.easi.datasources.nrsa import comid_by_site_id
    seeded = comid_by_site_id()
    assert seeded["NRS18_OH_10043"] == 18509814
    assert len(seeded) > 1800


def test_bundled_nrsa_comids_omit_synthetic_ids():
    # Some bundled records carry a HUC-derived placeholder for sites that were
    # never matched to the network. Handing one back as a reach would delineate
    # nonsense, so they must be dropped and left to snap live.
    from streamcurves._vendor.easi.datasources.nrsa import comid_by_site_id
    assert all(c < 1_000_000_000 for c in comid_by_site_id().values())
    assert "NRS18_KS_10043" not in comid_by_site_id()


# --- engine availability guard ---------------------------------------------- #
def test_engine_available_probes_the_geospatial_stack(monkeypatch):
    # Regression: the old probe only imported batch.api, which succeeds without
    # the geo stack because the engine imports it function-locally. That fail-open
    # let the cloud render Run screening and then fail every site.
    import importlib.util as ilu
    real = ilu.find_spec

    def no_pynhd(name, *a, **k):
        return None if name == "pynhd" else real(name, *a, **k)

    easi_screening.missing_engine_requirements.cache_clear()
    monkeypatch.setattr(ilu, "find_spec", no_pynhd)
    try:
        assert "pynhd" in easi_screening.missing_engine_requirements()
        assert easi_screening.engine_available() is False
    finally:
        easi_screening.missing_engine_requirements.cache_clear()
