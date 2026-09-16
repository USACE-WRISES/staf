"""Frozen A2 promotion, regional selection and full composite regression gates."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from easi import config, geo, screening_methods as sm
from easi.metrics import biology, hydraulics, physicochemistry
from easi.national import method_version

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("CPL", "NAP", "NPL", "SAP", "SPL", "TPL", "UMW", "WMT", "XER")
FAMILIES = ("corridor-natural", "corridor-woody", "flow-variability")
CURVES_SHA = "a824e2c254dea1c22af62d2a6f5fd3d0862ff0574190111655aa5b34dbce4887"
CATALOG_SHA = "64c0a49da530879ed7c0bb329ace1f12c4b386fa1f02378865c0e882c5331d6e"


@pytest.fixture(autouse=True)
def active(criteria_set):
    assert criteria_set == "regional"


def original_catalog():
    # The promoted candidate changes only these four inherited prose strings.
    raw = (config.DATA_DIR / "screening-methods.json").read_bytes()
    assert raw.count(b"NARS-9") == 4
    original = raw.replace(b"NARS-9", b"Level II")
    assert hashlib.sha256(original).hexdigest() == CATALOG_SHA
    return json.loads(original)


def spec(family):
    return {"set": family, "stratifier": "nars9" if family in FAMILIES else "slope_class",
            "fallback": ["national"], "mode": "banded"}


def test_live_assets_match_preserved_candidate_and_identity():
    original_catalog()
    raw = (config.DATA_DIR / "reference-curves.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CURVES_SHA
    curves = json.loads(raw)["sets"]
    assert set(curves) == {*FAMILIES, "entrenchment"}
    assert sum(len(value["curves"]) for value in curves.values()) == 34
    identity = config.scoring_identity()
    assert identity["alternative_id"] == config.scoring_alternative_id() == "alternative-2"
    assert identity["alternative_name"] == config.scoring_alternative_name()
    assert identity["criteria_family"] == "regional" and identity["curve_count"] == 34
    for field, name in [("catalog_sha256", "screening-methods.json"),
                        ("curves_sha256", "reference-curves.json"),
                        ("nars_geography_sha256", "nars-ecoregions-9.geojson.gz")]:
        assert identity[field] == hashlib.sha256((config.DATA_DIR / name).read_bytes()).hexdigest()
    assert sm.validate_catalog() == []


def test_only_four_permitted_functions_differ_from_alternative1_catalog():
    candidate = original_catalog()
    reconstructed = copy.deepcopy(candidate)

    def undo_curve_stratification(value):
        if isinstance(value, dict):
            curve = value.get("curve")
            if isinstance(curve, dict) and curve.get("set") in FAMILIES:
                assert curve["stratifier"] == "nars9"
                curve["stratifier"] = "l2"
            for item in value.values():
                undo_curve_stratification(item)
        elif isinstance(value, list):
            for item in value:
                undo_curve_stratification(item)

    undo_curve_stratification(reconstructed)
    changed = {a["metricId"] for a, b in zip(candidate["methods"], reconstructed["methods"])
               if a != b}
    assert changed == {hydraulics.LOW_FLOW_ID, physicochemistry.TEMPERATURE_ID,
                       physicochemistry.CPOM_ID, biology.HABITAT_ID}
    # Independently recorded from the preserved A1 catalog. Formatting is
    # canonicalized because the original A1 and candidate exporters differ.
    raw = json.dumps(reconstructed, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(raw).hexdigest() == "19256536d2e1f4df675ee90e162a11770e2ee68ff15d8e4198647b6a1dd6ce67"


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("region", REGIONS)
def test_every_nars_region_selects_its_own_curve_and_name(family, region):
    chosen, curve, depth = sm.resolve_curve(spec(family), {"nars9": region, "l2": "8.3"})
    assert (chosen, depth) == (region, 0)
    assert curve is sm.curve_sets()[family]["curves"][region]
    label = sm.curve_reference(spec(family), {"nars9": region})
    assert f"NARS-9 region {region}" in label and geo.nars9_name(region) in label
    assert "slope class" not in label and "Level II" not in label


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("region", [None, "", "missing"])
def test_missing_nars_region_falls_back_without_using_level2(family, region):
    assert sm.resolve_curve(spec(family), {"nars9": region, "l2": "8.3"})[::2] == ("national", 1)
    assert "national curve" in sm.curve_reference(spec(family), {"nars9": region})


@pytest.mark.parametrize("slope,key", [(0, "lt_0.5"), (.004999, "lt_0.5"),
                                     (.005, "0.5_to_2"), (.019999, "0.5_to_2"),
                                     (.02, "ge_2"), (None, "national")])
def test_entrenchment_retains_slope_boundaries(slope, key):
    strata = {"nars9": "TPL", "slope_class": geo.slope_class(slope)}
    assert sm.resolve_curve(spec("entrenchment"), strata)[0] == key


@pytest.mark.parametrize("family,key", [
    (family, key) for family, definition in sm.curve_sets().items()
    for key in definition["curves"]])
def test_every_frozen_curve_crosses_both_rating_boundaries(family, key):
    definition = sm.curve_sets()[family]
    curve = definition["curves"][key]
    strata = {definition["stratifier"]: key}
    points = sorted(curve["points"])
    higher = definition["higherIsBetter"]
    for edge, below_rating, above_rating in [(.39, "Poor", "Fair"), (.69, "Fair", "Good")]:
        a, b = next((a, b) for a, b in zip(points, points[1:])
                    if min(a[1], b[1]) <= edge <= max(a[1], b[1]) and a[1] != b[1])
        crossing = a[0] + (edge - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
        step = max(1, abs(crossing)) * 1e-7
        better = crossing + step if higher else crossing - step
        worse = crossing - step if higher else crossing + step
        for value, expected in [(better, above_rating), (worse, below_rating)]:
            rating, anchor, _ = sm.score_quantity(value, {"curve": spec(family)}, strata)
            assert rating == expected
            assert anchor == config.RATING_INDEX[expected]


@pytest.mark.parametrize("region", REGIONS)
def test_complete_thermal_composite_and_missingness_are_preserved(region):
    context = {"strata": {"nars9": region}}
    result = sm.evaluate(physicochemistry.TEMPERATURE_ID,
                         {"woodyRiparian": 100, "impervious": 30}, context=context)
    assert result.rating == "Poor" and result.index == .195
    assert result.trace["governingInput"] == "impervious"
    assert result.trace["curves"]["woodyRiparian"]["stratum"] == region
    for inputs in ({"woodyRiparian": 100}, {"impervious": 0}, {}):
        missing = sm.evaluate(physicochemistry.TEMPERATURE_ID, inputs, context=context)
        assert missing.rating is None and missing.trace["completeness"] == "not_assessed"


def test_full_offline_reports_equal_the_preserved_candidate(monkeypatch):
    from easi.national import client
    from test_batch_parity import _parity_view
    from test_national_preloaded import _record

    records = [_record(nars9=region) for region in (*REGIONS, None)]
    live = [_parity_view(client.score_record(record, cross_section=False)) for record in records]
    frozen = original_catalog()
    monkeypatch.setattr(sm, "catalog", lambda: frozen)
    candidate = [_parity_view(client.score_record(record, cross_section=False)) for record in records]
    assert live == candidate
    assert all(report["computedCount"] == 20 for report in live)


def test_digest_binds_nars_geography_and_scoring_identity(tmp_path, monkeypatch):
    import shutil
    for path in config.DATA_DIR.iterdir():
        if path.is_file():
            shutil.copyfile(path, tmp_path / path.name)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    config.reset_caches()
    baseline = method_version()
    for name in ("nars-ecoregions-9.geojson.gz", "scoring-identity.json"):
        target = tmp_path / name
        raw = target.read_bytes()
        target.write_bytes(raw + b" ")
        method_version.cache_clear()
        assert method_version() != baseline
        target.write_bytes(raw)
    config.reset_caches()


def test_legacy_identity_is_separate_and_frozen_metadata_is_not_mutated(monkeypatch):
    active_identity = config.scoring_identity()
    active_identity["alternative_id"] = "changed"
    assert config.scoring_alternative_id() == "alternative-2"
    monkeypatch.setenv("EASI_CRITERIA_SET", "legacy")
    identity = config.scoring_identity()
    assert identity["alternative_id"] == "legacy" and identity["curve_count"] == 0
    assert identity["alternative_name"] == "Historical legacy baseline"
    assert config.screening_methods_filename() == "screening-methods-legacy.json"


def test_unidentified_external_artifact_does_not_claim_alternative2(tmp_path, monkeypatch):
    (tmp_path / "screening-methods.json").write_bytes(b"{}")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    config.reset_caches()
    assert config.scoring_identity()["alternative_id"] is None
    config.reset_caches()
