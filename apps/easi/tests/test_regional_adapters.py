"""Regional routes use the approved evidence and retain the legacy dispatch."""
from __future__ import annotations

import math

import pytest

from easi import config, geo, screening_methods as sm, watershed
from easi.metrics import base, biology, geomorphology, hydraulics, hydrology


@pytest.fixture(autouse=True)
def regional(monkeypatch):
    monkeypatch.setenv("EASI_CRITERIA_SET", "regional")
    config.reset_caches()
    yield
    config.reset_caches()


def context(row=None, erom=None, *, l3="45", slope=.01):
    ctx = base.AnalysisContext(lat=37.9, lon=-78.5, comid=1, slope=slope, fcode=46006)
    ctx.extras.update(streamcat=row or {}, erom=erom,
                      strata=geo.strata_for(l3, slope=slope))
    return ctx


def erom(months):
    return {"qe_ma": 10.0, **{f"qe_{i:02d}": value for i, value in enumerate(months, 1)}}


@pytest.mark.parametrize("months, expected", [
    ([10.0] * 12, 0.0),
    (list(range(1, 13)), .531085),
    ([0.0] * 12, None),
    ([-1.0] * 12, None),
    ([1.0] * 11, None),
    ([1.0] * 11 + [None], None),
    ([1.0] * 11 + [math.nan], None),
    ([1.0] * 11 + [math.inf], None),
    ([1.0] * 11 + [True], None),
])
def test_monthly_flow_cv_requires_complete_finite_positive_mean(months, expected):
    assert hydraulics.monthly_flow_cv(erom(months)) == expected


def no_nrsa(*args, **kwargs):
    raise AssertionError("regional routes must not consult the retired NRSA scoring tier")


def test_regional_low_flow_uses_raw_erom_and_reference_strata(monkeypatch):
    monkeypatch.setattr(base, "nrsa_evidence", no_nrsa)
    ctx = context(erom=erom(range(1, 13)))
    result = hydraulics.low_flow_connectivity(ctx)
    assert result.value == .531085
    assert result.scoring["methodKey"] == "erom-flow-variability"
    assert result.scoring["evidenceFamily"] == "erom_flow"
    assert result.scoring["context"]["strata"]["l2"] == "8.3"
    assert result.scoring["curves"]["method"]["stratum"] == "8.3"
    assert "Unvalidated" in result.note
    assert hydraulics.low_flow_connectivity(context()).rating is None


@pytest.mark.parametrize("agriculture", [0, 30, 30.0001, 50, 50.0001, 100])
def test_substrate_shares_catchment_agriculture_bands(monkeypatch, agriculture):
    monkeypatch.setattr(base, "nrsa_evidence", no_nrsa)
    ctx = context({"pctcrop2019ws": agriculture, "pcthay2019ws": 0, "pctimp2019ws": 0})
    substrate = geomorphology.substrate(ctx)
    catchment = hydrology.impervious(ctx)
    assert substrate.rating == catchment.rating
    assert substrate.scoring["methodKey"] == "watershed-agriculture-share"
    assert "correlated pair" in substrate.note
    assert "Catchment hydrology" in substrate.note
    sub_method = sm.method_for(geomorphology.SUBSTRATE_ID)
    catch_method = sm.method_for(hydrology.IMPERVIOUS_ID)
    assert sub_method["bands"] == next(i["bands"] for i in catch_method["inputs"] if i["key"] == "agriculture")


def test_substrate_requires_both_agriculture_components(monkeypatch):
    monkeypatch.setattr(base, "nrsa_evidence", no_nrsa)
    assert geomorphology.substrate(context({"pctcrop2019ws": 10})).rating is None


@pytest.mark.parametrize("probability, expected", [
    (0, "Poor"), (.249999, "Poor"), (.25, "Fair"),
    (.499999, "Fair"), (.5, "Good"), (1, "Good"),
])
def test_biological_model_uses_other_aoi_name_and_exact_bands(monkeypatch, probability, expected):
    monkeypatch.setattr(base, "nrsa_evidence", no_nrsa)
    result = biology.biological_integrity(context({"prg_bmmi0809": probability}))
    assert result.rating == expected
    assert result.scoring["methodKey"] == "streamcat-prg-bmmi"
    assert result.scoring["usedFallback"] is False
    assert "areaOfInterest other" in result.source


@pytest.mark.parametrize("probability", [None, math.nan, math.inf, -1, 2])
def test_missing_or_invalid_model_keeps_complete_integrity_products(monkeypatch, probability):
    monkeypatch.setattr(base, "nrsa_evidence", no_nrsa)
    row = {f"{name}{aoi}": .99 for name in ("hyd", "chem", "sed", "conn", "temp", "habt")
           for aoi in ("cat", "ws")}
    row["prg_bmmi0809"] = probability
    result = biology.biological_integrity(context(row))
    assert result.rating == "Good"
    assert result.scoring["methodKey"] == "streamcat-integrity-products"
    assert result.scoring["usedFallback"] is True
    row.pop("habtws")
    assert biology.biological_integrity(context(row)).rating is None


@pytest.mark.parametrize("module, dispatcher, legacy", [
    (hydraulics, "low_flow_connectivity", "_low_flow_connectivity_legacy"),
    (geomorphology, "substrate", "_substrate_legacy"),
    (biology, "biological_integrity", "_biological_integrity_legacy"),
])
def test_legacy_switch_is_read_at_call_time(monkeypatch, module, dispatcher, legacy):
    ctx = context()
    token = object()
    monkeypatch.setattr(module, legacy, lambda supplied: token if supplied is ctx else None)
    monkeypatch.setenv("EASI_CRITERIA_SET", "legacy")
    assert getattr(module, dispatcher)(ctx) is token


def test_habitat_adapter_retains_selected_curve_context():
    row = {"pctconif2019wsrp100": 20, "pctdecid2019wsrp100": 20,
           "pctmxfst2019wsrp100": 20, "pctshrb2019wsrp100": 0,
           "pctwdwet2019wsrp100": 0}
    local = biology.habitat_complexity(context(row, l3="45"))
    fallback = biology.habitat_complexity(context(row, l3=None))
    assert local.scoring["curves"]["method"]["stratum"] == "8.3"
    assert fallback.scoring["curves"]["method"]["stratum"] == "national"
    assert fallback.scoring["curves"]["method"]["fallbackDepth"] == 1


def test_substrate_site_engine_uses_routed_watershed_evidence():
    ctx = context({"pctcrop2019ws": 100, "pcthay2019ws": 0})
    ctx.extras["watershed"] = watershed.from_engine({"metrics": {
        "cropPctWatershed": {"value": 10}, "hayPasturePctWatershed": {"value": 5}}})
    result = geomorphology.substrate(ctx)
    assert result.value == 15 and result.rating == "Good"
    assert "HR reach watershed" in result.source
