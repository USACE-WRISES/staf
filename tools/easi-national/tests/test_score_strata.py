"""Stored derived identities reach schema-2 scoring without redoing polygons."""
from __future__ import annotations

import numpy as np

from builder.analysis import strata
from builder.stages import score
from easi import geo
from easi.national import SCHEMA_VERSION, records
from test_analysis_values import _record


def test_score_record_preserves_the_derived_anchor_regions(monkeypatch):
    source = _record()
    expected = geo.strata_at(source["lat"], source["lon"], slope=source["slope"])
    assert expected["l3"] == source["l3_code"] and expected["nars9"] == source["nars9"]
    derived = {key: source[key] for key in records.IDENTITY_FIELDS}
    record = score.record_for(derived, {}, None, source["streamcat"])
    assert record["schema_version"] == SCHEMA_VERSION == 2
    assert record["l3_code"] == "45" and record["nars9"] == "SAP"
    calls = []
    monkeypatch.setattr(geo, "level3_at", lambda *args: calls.append("l3"))
    monkeypatch.setattr(geo, "nars9_at", lambda *args: calls.append("nars9"))
    context = records.build_context(records.from_row(records.to_row(record)))
    assert calls == [] and context.extras["strata"] == expected


def test_score_record_keeps_absent_and_null_identities_distinct():
    missing = score.record_for({"comid": 1}, {}, None, {})
    assert "l3_code" not in missing and "nars9" not in missing
    explicit = score.record_for({"comid": 1, "l3_code": None, "nars9": None}, {}, None, {})
    assert "l3_code" in explicit and "nars9" in explicit
    assert explicit["l3_code"] is None and explicit["nars9"] is None


def test_slope_classes_match_the_analysis_boundaries():
    slopes = np.asarray([0.0, 0.004999, 0.005, 0.019999, 0.02, 0.1, np.nan])
    expected = strata.classify(slopes, strata.SLOPE_BREAKS, strata.SLOPE_LABELS).tolist()
    assert [geo.slope_class(value) for value in slopes] == expected
