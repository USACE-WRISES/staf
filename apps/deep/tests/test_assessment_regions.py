"""DEEP's assessment regions (owner, 2026-10-05): a right sidebar that searches the Level III
regions and a map layer that is explorable while zoomed out. The server sends the regions once per
session (the page keeps them; each answer is ~1.2 MB) and says which assessment is in use and
whether the regions take clicks (the Identify step only)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

app = pytest.importorskip("app")
SRC = Path(app.__file__).read_text(encoding="utf-8")


class Value:
    def __init__(self, value=None):
        self.value = value

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = value


def _scope(**extra):
    from test_streamcat_readiness import function
    sent = []

    async def send(kind, payload):
        sent.append((kind, payload))
    scope = {**vars(app), "session_": SimpleNamespace(send_custom_message=send), "sent": sent,
             "_coverage": {"sent": False, "focus": False}, "loaded_assessment": Value(None),
             "current_step": Value(app.STEP_IDENTIFY), "region_statuses": Value(()), **extra}
    for name in ("_coverage_state", "_send_coverage", "_send_coverage_current"):
        function(name, scope)
    return scope


FEATURES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []},
     "properties": {"assessmentId": "flint-hills", "assessmentName": "Flint Hills reference assessment",
                    "regionName": "Flint Hills", "regionCode": "28", "version": 2, "lifecycle": "certified"}},
    {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []},
     "properties": {"assessmentId": "custom", "assessmentName": "A drawn region", "regionName": "",
                    "regionCode": "", "version": 1, "lifecycle": "preliminary"}},
]}


def test_the_regions_go_out_once_per_session_with_names_codes_and_status(monkeypatch):
    monkeypatch.setattr(app.assessments, "library_region_features", lambda: FEATURES)
    scope = _scope()
    asyncio.run(scope["_send_coverage"]())
    asyncio.run(scope["_send_coverage"]())                 # the page asked again before the answer landed
    kinds = [k for k, _p in scope["sent"]]
    assert kinds == ["deep_coverage", "deep_coverage_current"]
    features = scope["sent"][0][1]["features"]
    assert [f["name"] for f in features] == ["Flint Hills", "A drawn region"]   # the region, else the assessment
    assert features[0]["code"] == "28" and features[0]["status"] == "Final" and features[0]["certified"] is True
    assert features[1]["status"] == "Preliminary" and features[1]["certified"] is False
    # the status itself rides too (owner, 2026-10-08: Draft, Preliminary and Final, each colored),
    # and the legend keys the statuses the map shows
    assert [f["lifecycle"] for f in features] == ["certified", "preliminary"]
    assert scope["region_statuses"]() == ("certified", "preliminary")
    assert scope["sent"][1][1] == {"assessmentId": None, "identify": True, "focus": False}


def test_a_draft_region_goes_out_as_a_draft(monkeypatch):
    draft = {"type": "FeatureCollection", "features": [dict(FEATURES["features"][1], properties=dict(
        FEATURES["features"][1]["properties"], lifecycle="draft"))]}
    monkeypatch.setattr(app.assessments, "library_region_features", lambda: draft)
    scope = _scope()
    asyncio.run(scope["_send_coverage"]())
    (feature,) = scope["sent"][0][1]["features"]
    assert (feature["lifecycle"], feature["status"], feature["certified"]) == ("draft", "Draft", False)
    assert scope["region_statuses"]() == ("draft",)


def test_the_assessment_in_use_and_the_step_reach_the_page_and_a_link_shows_its_region_once():
    scope = _scope(loaded_assessment=Value(SimpleNamespace(assessment_id="flint-hills")),
                   current_step=Value(app.STEP_BASIN))
    scope["_coverage"]["focus"] = True                     # what a ?assessment= link sets
    asyncio.run(scope["_send_coverage_current"]())
    asyncio.run(scope["_send_coverage_current"]())
    assert [p for _k, p in scope["sent"]] == [
        {"assessmentId": "flint-hills", "identify": False, "focus": True},
        {"assessmentId": "flint-hills", "identify": False, "focus": False}]
    link = SRC.split("def _ingest_url_params():", 1)[1].split("\n    # ", 1)[0]
    assert '_coverage["focus"] = True' in link


def test_the_sidebar_markup_mirrors_the_left_pane_and_keeps_deeps_own_ids():
    body = str(app._tool_body())
    assert 'class="deep-cov"' in body and 'class="deep-cov-tab"' in body
    assert 'aria-controls="deep-cov-panel"' in body and 'aria-expanded="false"' in body
    # closed, a level button with the region count, in the map's controls between Layers and the
    # legend (coverage.js docks it there); never sideways text (owner, 2026-10-05)
    assert 'class="deep-cov-tab-count"' in body
    css = (Path(app.__file__).parent / "www" / "deep.css").read_text(encoding="utf-8")
    assert "writing-mode" not in css
    assert ".easi-map-wrap .leaflet-top.leaflet-right .deep-cov-tab { order: 2; }" in css
    assert ".easi-map-wrap .leaflet-top.leaflet-right .easi-legend-panel { order: 3; }" in css
    assert 'id="deep-cov-panel"' in body and 'id="deep-cov-body"' in body
    assert 'class="easi-pane-head deep-cov-head"' in body and "Assessment regions" in body
    assert f'data-flow-zoom="{app.FLOW_ZOOM}"' in body
    assert "Assessment coverage" not in body and "deep-cov-all" not in body
    assert 'class="staf-zoom-cue-slot"' in body
    help_text = SRC.split("def _help():", 1)[1].split("@reactive", 1)[0]
    assert "**Assessment regions** list" in help_text and "separate panel" not in help_text
