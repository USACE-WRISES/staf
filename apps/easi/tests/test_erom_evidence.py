"""Raw EROM evidence follows the same contract on live and preloaded paths."""
from __future__ import annotations

import asyncio
import json

import pytest

from easi import delineation, pipeline, routing
from easi.datasources import fabric
from easi.national import records


def _erom():
    # COMID 1086969, read-only sample of the existing seamless geodatabase.
    months = [589.619, 704.108, 811.322, 726.441, 546.837, 297.317,
              175.612, 137.997, 92.747, 145.582, 222.141, 369.55]
    return {"qe_ma": 402.379, **{f"qe_{i:02d}": value for i, value in enumerate(months, 1)}}


def test_fabric_requests_and_preserves_all_raw_flow_estimates(monkeypatch):
    erom = _erom()
    requested = []

    def get(params, **kwargs):
        requested.append(params)
        return {"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {"comid": 1086969, **erom}, "geometry": None,
        }]}

    monkeypatch.setattr(fabric, "_get", get)
    fabric.clear_feature_memo()
    feature = fabric.feature_by_comid(1086969)
    assert set(erom) <= set(requested[0]["properties"].split(","))
    assert fabric.attrs_from_feature(feature)["erom"] == erom
    assert delineation.flowline_attrs(1086969)["erom"] == erom


@pytest.mark.parametrize("key", ["qe_ma", "qe_01", "qe_12"])
@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "unknown", True, False])
def test_one_invalid_erom_value_means_unknown_complete_block(key, bad):
    erom = _erom()
    erom[key] = bad
    assert fabric.erom_from_properties(erom) is None
    assert fabric.attrs_from_feature({"properties": erom})["erom"] is None


def test_erom_missing_field_zero_and_string_flows():
    missing = _erom()
    del missing["qe_09"]
    assert fabric.erom_from_properties(missing) is None
    assert fabric.erom_from_properties(None) is None
    assert fabric.erom_from_properties({k: "0" for k in fabric.EROM_PROPERTIES}) == {
        k: 0.0 for k in fabric.EROM_PROPERTIES}
    assert fabric.erom_from_properties({k: str(v) for k, v in _erom().items()}) == _erom()


@pytest.mark.parametrize("direct", [True, False])
def test_delineation_carries_erom_for_direct_and_snapped_comid(monkeypatch, direct):
    attrs = {"comid": 1086969, "gnis_name": "Sample", "sinuosity": 1.2, "erom": _erom()}
    monkeypatch.setattr(delineation, "flowline_attrs", lambda *a, **k: attrs)
    monkeypatch.setattr(delineation, "snap_point", lambda *a, **k: attrs)
    monkeypatch.setattr(delineation, "delineate_watershed", lambda *a, **k: (None, 0.0, []))
    monkeypatch.setattr(delineation, "derive_reach", lambda *a, **k: (None, 0.0, []))
    result = delineation.run_delineation(38.0, -78.0, comid=1086969 if direct else None)
    assert result.erom == _erom()


def test_routed_context_keeps_surrogate_erom(monkeypatch):
    erom = _erom()
    d = delineation.Delineation(lat=38.0, lon=-78.0, comid=1086969, erom=erom,
                                drainage_area_sqkm=500.0, slope=0.001)
    anchor = routing.v2_anchor(1086969, 38.0, -78.0)
    anchor["anchorKind"] = "hrSurrogate"
    monkeypatch.setattr(delineation, "run_delineation", lambda *a, **k: d)
    monkeypatch.setattr(routing, "reanchor_inputs", lambda *a, **k: {
        "lat": 38.1, "lon": -78.1, "slope": 0.04, "erom": {"qe_ma": 999.0},
    })
    result = asyncio.run(pipeline.delineate_only(
        38.0, -78.0, comid=1086969, anchor=anchor,
        watershed_engine=routing.POLICY_STREAMCAT_LEGACY))
    ci = result["ctx_inputs"]
    assert ci["slope"] == 0.04
    assert ci["erom"] == erom
    assert pipeline._ctx_from_inputs(ci).extras["erom"] == erom


def test_preloaded_erom_roundtrips_and_uses_same_normalizer(monkeypatch):
    monkeypatch.setattr(records.watershed, "build", lambda *a, **k: {})
    monkeypatch.setattr(records, "reach_geomorph", lambda *a, **k: {})
    erom = _erom()
    record = {"comid": 1086969, "lat": 38.0, "lon": -78.0,
              "l3_code": "45", "nars9": "SAP", "slope": 0.005, "erom": erom}
    row = records.to_row(record)
    assert isinstance(row["erom"], str)
    restored = records.from_row(row)
    ctx = records.build_context(restored)
    live = fabric.attrs_from_feature({"properties": erom})["erom"]
    assert json.dumps(ctx.extras["erom"], separators=(",", ":")) == json.dumps(live, separators=(",", ":"))
    del record["erom"]
    assert records.build_context(record).extras["erom"] is None
    assert "erom" in records.EVIDENCE_FIELDS and "erom" in records.JSON_FIELDS
