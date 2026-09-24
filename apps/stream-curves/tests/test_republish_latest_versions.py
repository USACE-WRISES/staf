"""Every latest DEEP version republishes to its own content digest (2026-09-24).

Opened the way the Open dialog opens it (``data_overview.restore_session``) and published
again untouched from the workspace, a version states what it stated: the same content digest
and the same applicability. NLF v1 and CBR v1 did not. Each has one class split (STRAT-10)
whose pooled curve is degenerate: the build published that pooled curve with the complete
classes as layers, and the workspace read the same rows as the first complete class. Both
paths now share one rule, ``deep_export.pooled_layers``.

A state SQT is a transcription and is never revised in the app, so its sessions are refused.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from shiny import reactive, ui

from streamcurves import deep_export as dx
from streamcurves import gallery
from streamcurves import library as lib
from streamcurves import pressure_evidence as pe
from streamcurves import session_io as sio
from views import assessment_publish as ap
from views import data_overview as do
from views.state import AppState

APP = Path(__file__).resolve().parents[1]
LIBRARY = APP.parent / "library" / "assessments"
SQT_SUFFIX = "-sqt-adapted"


def _latest_deep() -> list[str]:
    if not LIBRARY.is_dir():
        return []
    return sorted(f"{e.id}/v{e.latest_version}" for e in gallery.entries_from_library()
                  if e.type == "deep")


LATEST = _latest_deep()
REGIONAL = [v for v in LATEST if SQT_SUFFIX not in v]
STATE_SQT = [v for v in LATEST if SQT_SUFFIX in v]


def _opened(version: str, monkeypatch):
    vdir = LIBRARY / version
    if not (vdir / lib.SESSION_FILE).is_file():
        pytest.skip(f"{version} has no session in this checkout")
    published = json.loads((vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    monkeypatch.setattr(ui, "notification_show", lambda *a, **k: None)
    aid, ver = version.split("/v")
    state = AppState.fresh()
    with reactive.isolate():
        do.restore_session(state, sio.load_session_payload(vdir / lib.SESSION_FILE), aid)
        state.assessment_source.set(ap.build_origin(state, kind="library", library_id=aid,
                                                    version=int(ver)))
    return state, published


def test_the_library_holds_the_regional_versions_this_checks():
    assert {"northern-lakes-and-forests/v1", "central-basin-and-range/v1"} <= set(REGIONAL)


@pytest.mark.parametrize("version", REGIONAL)
def test_an_untouched_republish_states_what_the_version_stated(version, monkeypatch):
    state, published = _opened(version, monkeypatch)
    assert ap.transcription_refusal(state) is None
    rebuilt = ap.build_bundle_from_state(state, meta={
        "sourceCitation": published.get("sourceCitation"),
        "assessmentName": published.get("assessmentName")})
    assert lib.content_digest(rebuilt) == published["contentDigest"]
    assert rebuilt.get("applicability") == published.get("applicability")


@pytest.mark.parametrize("version", STATE_SQT)
def test_a_state_sqt_transcription_is_refused_instead(version, monkeypatch):
    state, _published = _opened(version, monkeypatch)
    assert ap.transcription_refusal(state) == ap.TRANSCRIPTION_REFUSAL


def test_the_publish_page_refuses_a_transcription_on_the_page_the_form_and_the_click():
    src = (APP / "views" / "publish.py").read_text(encoding="utf-8")
    assert 'return not_ready_panel("Not revised in the app", refusal' in src
    assert "blocked = _publish_block_reason() or ap.transcription_refusal(state)" in src
    assert src.count("refusal = ap.transcription_refusal(state)") == 2


def _pts(*pairs) -> pd.DataFrame:
    return pd.DataFrame({"point_order": list(range(1, len(pairs) + 1)),
                         "metric_value": [float(x) for x, _ in pairs],
                         "index_score": [float(y) for _, y in pairs]})


def test_a_degenerate_pooled_curve_stays_the_default_with_the_complete_classes():
    from streamcurves import regional_agent as ra
    pooled = {"metric": "m", "curve_status": "degenerate_q25", "n_reference": 56,
              "curve_points": _pts((0, 0), (35.6, 1))}
    classes = [{"metric": "m", "stratum": "lt_0.5", "curve_status": "degenerate_q25",
                "n_reference": 36, "curve_points": _pts((0, 0), (20, 1))},
               {"metric": "m", "stratum": "0.5_to_2", "curve_status": "complete",
                "n_reference": 16, "curve_points": _pts((0, 0), (3.2, .3), (7.5, .7), (40.8, 1))}]
    entry = ra._stratified_metric_entry("m", pooled, classes, "NhdSlopeClass")
    # the workspace's own rule is unchanged: a degenerate pooled curve gives way
    assert dx.deep_collect_curve_rows({"m": entry})["m"]["stratum"] == "0.5_to_2"
    # a build's split reads as the build published it, through the build's own rule
    row = dx.deep_collect_curve_rows({"m": entry}, pooled_default=True)["m"]
    assert row["stratum"] == "" and row["curve_status"] == "degenerate_q25"
    assert [L["stratum"] for L in row["all_strata"]] == ["", "0.5_to_2"]
    assert [L["stratum"] for L in pe.layered_row(pooled, classes, {})["all_strata"]] == ["", "0.5_to_2"]
    # and an unsplit metric reads the same either way
    single = {"m": ra._completed_metric_entry("m", pooled)}

    def read(row):
        return (row.get("stratum"), row.get("curve_status"),
                [L["stratum"] for L in row.get("all_strata") or []])
    assert read(dx.deep_collect_curve_rows(single, pooled_default=True)["m"]) == \
        read(dx.deep_collect_curve_rows(single)["m"])
