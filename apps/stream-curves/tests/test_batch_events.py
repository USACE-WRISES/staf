"""The batch runners' event callback.

``run_evidence`` emits three shapes: a plain string, a StreamCat dict, and the
site-engine tuple ``("enrich_site_engine", info)``. The CLI runners once passed a
one-argument lambda, so the tuple raised ``TypeError`` the first time a stage ran
with ``--predictor-source site-engine`` (2026-09-02, NEH). The narrator accepts
every shape and never raises; the emit is guarded as well.
"""
from __future__ import annotations

from pathlib import Path

from streamcurves import regional_agent as ra

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_event_narrator_accepts_every_emitted_shape():
    lines: list[str] = []
    n = ra.event_narrator(write=lines.append)
    n("screening 71 sites")
    n({"event": "streamcat_start", "n_codes": 11})
    n({"event": "streamcat_done", "n_columns": 11})
    n({"event": "streamcat_partial", "n_columns": 4, "failed_chunks": [2]})
    n("enrich_site_engine", {"n_sites": 33, "note": "usually under a minute"})
    n("site_engine_site", {"i": 12, "n": 33, "site_id": "NRS18_NH_10016",
                           "status": "ok", "seconds": 41.2, "cached": False})
    n("site_engine_site", {"i": 13, "n": 33, "site_id": "NRS18_NH_10017",
                           "status": "refused", "seconds": 130.0,
                           "reason": "watershed exceeds the budget"})
    n("site_engine_site", {"i": 14, "n": 33, "site_id": "NRS18_NH_10018",
                           "status": "ok", "seconds": 0.0, "cached": True})
    n("screening_retry", {"pass": 1, "n": 23, "recovered": 21})
    n("screening_cache_stale", "", {"cache_path": "x", "cached_n": 3, "candidate_n": 4})
    n("site_done", "NRS18_NH_10016", {"state": "succeeded"})
    n("delineation", "NRS18_NH_10016", {})          # silent EASI stage
    n(object(), 1, 2, 3)                              # garbage never raises
    text = "\n".join(lines)
    assert "[screen] screening 71 sites" in text
    assert "[streamcat] start 11" in text and "[streamcat] done 11" in text
    assert "[streamcat] partial" in text
    assert "[engine] computing exact-watershed values at 33 retained site" in text
    assert "[engine] 12/33 NRS18_NH_10016 ok 41 s" in text
    assert "[engine] 13/33 NRS18_NH_10017 refused 130 s: watershed exceeds the budget" in text
    assert "[engine] 14/33 NRS18_NH_10018 cached" in text
    assert "[screen] retry pass 1: 23 site(s), 21 recovered" in text
    assert "screening cache stale" in text
    assert "[screen] site_done NRS18_NH_10016 succeeded" in text
    assert "delineation" not in text
    for line in lines:
        assert "—" not in line and ";" not in line


def test_safe_emit_survives_any_callback_shape():
    seen = []
    ra._safe_emit(lambda ev: seen.append(ev), "only", {"two": 2})   # TypeError swallowed
    ra._safe_emit(lambda *a: seen.append(a), "a", {"b": 1})
    ra._safe_emit(None, "nothing")
    assert seen == [("a", {"b": 1})]


def test_both_runners_pass_the_narrator():
    for script in ("run_region_batch.py", "run_regional_analysis.py"):
        text = (_SCRIPTS / script).read_text(encoding="utf-8")
        assert "on_event=ra.event_narrator(" in text, script
        assert "lambda ev: print" not in text, script


def test_run_evidence_engine_emit_survives_a_one_arg_on_event(monkeypatch):
    """The exact failure of 2026-09-02: a one-argument callback and the
    site-engine branch. Offline: the engine enrichment is stubbed."""
    from streamcurves import site_engine_source as ses

    monkeypatch.setattr(ses, "enrich_site_engine",
                        lambda rows, **kw: ({}, {"source": "site_engine", "status": "ok",
                                                 "n_columns": 0, "reason": None,
                                                 "engine": ses.engine_identity()}))
    evidence = ra.run_evidence(
        "55", "Eastern Corn Belt Plains", do_screen=False, use_streamcat=False,
        diagnostics_enabled=False, nrsa_dataset_id="legacy-1819",
        predictor_source="site-engine",
        on_event=lambda ev: None)
    assert evidence["resourced_metrics"] == []       # StreamCat was off: nothing to re-source
    assert evidence["source_reports"][1]["source"] == "site_engine"
