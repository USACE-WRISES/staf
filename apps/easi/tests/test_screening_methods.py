"""Canonical EASI method catalog, evaluator, and SVG viewer tests."""
from __future__ import annotations

import copy

import pytest

from easi import config, method_plot, methods as easi_methods, screening_methods as sm
from easi.metrics import biology, geomorphology, hydraulics, hydrology, physicochemistry


def test_catalog_contract_is_complete_and_valid():
    catalog = config.screening_methods()
    assert len(catalog["methods"]) == 20
    assert {m["metricId"] for m in catalog["methods"]} == set(config.metrics_by_id())
    assert sm.validate_catalog() == []
    assert {m["operator"] for m in catalog["methods"]} <= sm.VALID_OPERATORS


@pytest.mark.parametrize(
    "value,expected",
    [(9.999, "Good"), (10, "Fair"), (25, "Fair"), (25.001, "Poor")],
)
def test_impervious_exact_boundaries(value, expected):
    result = sm.evaluate(hydrology.IMPERVIOUS_ID,
                         {"impervious": value, "agriculture": None})
    assert result.rating == expected
    assert result.trace["completeness"] == "partial"


@pytest.mark.parametrize(
    "value,expected",
    [(24.999, "Good"), (25, "Fair"), (50, "Fair"), (50.001, "Poor")],
)
def test_agriculture_exact_boundaries(value, expected):
    result = sm.evaluate(hydrology.IMPERVIOUS_ID,
                         {"impervious": None, "agriculture": value})
    assert result.rating == expected


def test_worst_input_uses_fixed_index_and_governing_input():
    result = sm.evaluate(hydrology.IMPERVIOUS_ID,
                         {"impervious": 2, "agriculture": 61})
    assert result.rating == "Poor"
    assert result.combined_value == config.RATING_INDEX["Poor"]
    assert result.trace["governingInput"] == "agriculture"


@pytest.mark.parametrize(
    "value,expected",
    [(0.9999, "Poor"), (1, "Fair"), (5, "Fair"), (5.0001, "Good")],
)
def test_wetland_extent_exact_boundaries(value, expected):
    result = sm.evaluate(
        hydrology.WETLANDS_ID,
        {"woodyWetland": value, "herbaceousWetland": 0})
    assert result.rating == expected


@pytest.mark.parametrize(
    "value,expected",
    [(0.9999, "Good"), (1, "Fair"), (2.9999, "Fair"), (3, "Poor")],
)
def test_road_density_exact_boundaries(value, expected):
    assert sm.evaluate(
        hydrology.REACH_INFLOW_ID, {"roadDensity": value}).rating == expected


def test_missing_required_inputs_are_not_zero():
    result = sm.evaluate(geomorphology.SEDIMENT_ID, {
        "agriculture": 60, "kFactor": None, "roadDensity": 8,
    })
    assert result.rating is None
    assert result.trace["completeness"] == "not_assessed"
    assert result.trace["inputs"][1]["value"] is None


@pytest.mark.parametrize(
    "storage,runoff,expected",
    [
        (19990, 1000, "Good"),
        (20000, 1000, "Fair"),
        (300000, 1000, "Fair"),
        (300010, 1000, "Poor"),
    ],
)
def test_degree_of_regulation_conversion_and_boundaries(storage, runoff, expected):
    result = sm.evaluate(hydrology.FLOW_ALTERATION_ID,
                         {"storage": storage, "runoff": runoff})
    assert result.rating == expected
    assert result.combined_value == pytest.approx(0.1 * storage / runoff)


@pytest.mark.parametrize("runoff", [None, 0, -1])
def test_degree_of_regulation_requires_positive_runoff(runoff):
    result = sm.evaluate(hydrology.FLOW_ALTERATION_ID,
                         {"storage": 1000, "runoff": runoff})
    assert result.rating is None


@pytest.mark.parametrize(
    "bhr,expected",
    [(1.2999, "Good"), (1.3, "Good"), (1.3001, "Fair"),
     (1.5, "Fair"), (1.5001, "Poor")],
)
def test_bhr_boundaries(bhr, expected):
    assert sm.evaluate(hydraulics.FLOODPLAIN_ENGAGEMENT_ID, {"bhr": bhr}).rating == expected


def test_bhr_below_one_adds_geometry_warning():
    result = sm.evaluate(hydraulics.FLOODPLAIN_ENGAGEMENT_ID, {"bhr": 0.9})
    assert result.rating == "Good"
    assert result.trace["warnings"]


@pytest.mark.parametrize(
    "er,expected",
    [(1.3999, "Poor"), (1.4, "Fair"), (2.1999, "Fair"), (2.2, "Good")],
)
def test_entrenchment_boundaries(er, expected):
    assert sm.evaluate(hydraulics.ENTRENCHMENT_ID, {"er": er}).rating == expected


def test_provisional_composite_formula_reproduction():
    hyp = sm.evaluate(hydraulics.HYPORHEIC_ID, {"slope": 0.005, "sinuosity": 1.25})
    assert hyp.combined_value == pytest.approx(0.5)
    sed = sm.evaluate(geomorphology.SEDIMENT_ID, {
        "agriculture": 25, "kFactor": 0.2, "roadDensity": 2.5,
    })
    assert sed.combined_value == pytest.approx(0.5)
    habitat = sm.evaluate(biology.HABITAT_ID, {
        "woodyRiparian": 30, "sinuosity": 1.25,
    })
    assert habitat.combined_value == pytest.approx(0.5)


@pytest.mark.parametrize(
    "target,expected",
    [(0.299999, "Poor"), (0.3, "Fair"), (0.599999, "Fair"), (0.6, "Good")],
)
def test_hyporheic_combined_breakpoints(target, expected):
    result = sm.evaluate(
        hydraulics.HYPORHEIC_ID,
        {"slope": target / 0.6 * 0.01, "sinuosity": 1.0})
    assert result.combined_value == pytest.approx(target)
    assert result.rating == expected


@pytest.mark.parametrize(
    "target,expected",
    [(0.329999, "Good"), (0.33, "Fair"), (0.659999, "Fair"), (0.66, "Poor")],
)
def test_sediment_combined_breakpoints(target, expected):
    result = sm.evaluate(geomorphology.SEDIMENT_ID, {
        "agriculture": 50 * target,
        "kFactor": 0.4 * target,
        "roadDensity": 5 * target,
    })
    assert result.combined_value == pytest.approx(target)
    assert result.rating == expected


@pytest.mark.parametrize(
    "target,expected",
    [(0.299999, "Poor"), (0.3, "Fair"), (0.549999, "Fair"), (0.55, "Good")],
)
def test_habitat_combined_breakpoints(target, expected):
    result = sm.evaluate(biology.HABITAT_ID, {
        "woodyRiparian": 60 * target,
        "sinuosity": 1 + 0.5 * target,
    })
    assert result.combined_value == pytest.approx(target)
    assert result.rating == expected


def test_cpom_requires_all_components_and_caps_sum():
    missing = sm.evaluate(physicochemistry.CPOM_ID, {
        "forest": 60, "shrub": 10, "grassland": None, "wetland": 10,
    })
    assert missing.rating is None
    complete = sm.evaluate(physicochemistry.CPOM_ID, {
        "forest": 60, "shrub": 20, "grassland": 20, "wetland": 20,
    })
    assert complete.combined_value == 100
    assert complete.rating == "Good"


@pytest.mark.parametrize(
    "total,expected",
    [(19.999, "Poor"), (20, "Poor"), (20.001, "Fair"),
     (50, "Fair"), (50.001, "Good")],
)
def test_cpom_exact_boundaries(total, expected):
    result = sm.evaluate(physicochemistry.CPOM_ID, {
        "forest": total, "shrub": 0, "grassland": 0, "wetland": 0,
    })
    assert result.rating == expected


def test_thermal_requires_both_inputs_and_min_index():
    assert sm.evaluate(physicochemistry.TEMPERATURE_ID, {
        "woodyRiparian": 80, "impervious": None,
    }).rating is None
    result = sm.evaluate(physicochemistry.TEMPERATURE_ID, {
        "woodyRiparian": 80, "impervious": 30,
    })
    assert result.rating == "Poor"
    assert result.trace["governingInput"] == "impervious"


@pytest.mark.parametrize(
    "woody,expected",
    [(24.999, "Poor"), (25, "Fair"), (74.999, "Fair"), (75, "Good")],
)
def test_thermal_woody_cover_exact_boundaries(woody, expected):
    assert sm.evaluate(physicochemistry.TEMPERATURE_ID, {
        "woodyRiparian": woody, "impervious": 0,
    }).rating == expected


@pytest.mark.parametrize(
    "region", ["CPL", "NAP", "SAP", "UMW", "TPL", "NPL", "SPL", "WMT", "XER"])
@pytest.mark.parametrize("analyte", ["tn", "tp"])
def test_all_nars_regions_and_analytes_have_exact_nutrient_boundaries(
        region, analyte):
    method = sm.method_for(physicochemistry.NUTRIENTS_ID)
    input_def = next(i for i in method["inputs"] if i["key"] == analyte)
    good_fair, fair_poor = input_def["regionalBands"][region]

    def evaluate(value):
        values = {"tn": None, "tp": None}
        values[analyte] = value
        return sm.evaluate(
            physicochemistry.NUTRIENTS_ID, values,
            context={"region": region}).rating

    assert evaluate(good_fair) == "Good"
    assert evaluate(good_fair + 1e-7) == "Fair"
    assert evaluate(fair_poor - 1e-7) == "Fair"
    assert evaluate(fair_poor) == "Poor"


@pytest.mark.parametrize(
    "count,expected",
    [(0, "Good"), (1, "Fair"), (2, "Fair"), (3, "Poor"), (9, "Poor")],
)
def test_invasive_taxa_count_exact_boundaries(count, expected):
    assert sm.evaluate(
        biology.INVASIVES_ID, {"taxaCount": count}).rating == expected


def test_barrier_count_bands():
    assert sm.evaluate(biology.BARRIERS_ID, {"damCount": 0}).rating == "Good"
    assert sm.evaluate(biology.BARRIERS_ID, {"damCount": 1}).rating == "Fair"
    assert sm.evaluate(biology.BARRIERS_ID, {"damCount": 2}).rating == "Poor"
    assert sm.evaluate(biology.BARRIERS_ID, {"damCount": 20}).rating == "Poor"


def test_channel_proxy_and_canal_variant_are_explicit():
    channel = sm.evaluate(
        geomorphology.CHANNEL_EVOL_ID,
        {"bhr": 1.2, "er": 2.3, "fcodeContext": 46006})
    assert channel.rating == "Good"
    assert channel.trace["evidenceFamily"] == "incision_geometry"
    canal = sm.evaluate(
        geomorphology.CHANNEL_EVOL_ID, {"fcode": 33600},
        variant_key="channelized-fcode")
    assert canal.rating == "Poor" and canal.trace["usedFallback"] is True
    biological = sm.evaluate(biology.BIOINTEGRITY_ID, {})
    assert biological.rating is None


def _values(trace):
    return {item["key"]: item.get("value") for item in trace["inputs"]}


def test_reference_curve_shows_breakpoints_and_both_markers():
    """The worksheet's reference curve is drawn from the catalog bands the evaluator used."""
    method = easi_methods.resolve(geomorphology.SEDIMENT_ID)
    site = sm.evaluate(geomorphology.SEDIMENT_ID, {
        "agriculture": 25, "kFactor": 0.2, "roadDensity": 2.5,
    }).trace
    explored = easi_methods.evaluate_method(
        method, {"agriculture": 80, "kFactor": 0.5, "roadDensity": 8})
    svg = method_plot.scalar_svg(method, site["combinedValue"], site["generatedRating"],
                                 explored["value"], explored["rating"])
    assert svg.startswith("<svg")
    for color in ("#c8d9f2", "#f5e7a6", "#f5b5b5"):     # Good / Fair / Poor regions
        assert color in svg
    assert "V 0.33" in svg and "V 0.66" in svg          # authored breakpoint labels
    assert "Site" in svg and "Explore" in svg
    assert explored["rating"] == "Poor"


def test_exploration_is_scored_by_the_same_evaluator_and_does_not_mutate_the_trace():
    method = easi_methods.resolve(hydraulics.HYPORHEIC_ID)
    trace = sm.evaluate(hydraulics.HYPORHEIC_ID, {
        "slope": 0.001, "sinuosity": 1.05,
    }).trace
    original = copy.deepcopy(trace)
    explored = easi_methods.evaluate_method(method, {"slope": 0.02, "sinuosity": 2.0})
    assert trace == original
    # the same inputs through the canonical evaluator agree with the panel
    assert explored["rating"] == sm.evaluate(
        hydraulics.HYPORHEIC_ID, {"slope": 0.02, "sinuosity": 2.0}).rating


def test_slider_max_expands_for_site_value():
    method = easi_methods.resolve(hydrology.FLOW_ALTERATION_ID)
    trace = sm.evaluate(hydrology.FLOW_ALTERATION_ID, {
        "storage": 750000, "runoff": 1000,
    }).trace
    storage = next(spec for spec in easi_methods.slider_specs(method, _values(trace))
                   if spec[0].key == "storage")
    assert storage[2][1] >= 750000          # (min, max, step) expanded past the site value


def test_categorical_decision_table_highlights_the_site_rating():
    trace = sm.evaluate(
        geomorphology.CHANNEL_EVOL_ID,
        {"stageClass": "Fair", "indicators": "bars, mid-channel deposition"},
        variant_key="observed-channel-adjustment").trace
    assert trace["generatedRating"] == "Fair"
    method = easi_methods.resolve(geomorphology.CHANNEL_EVOL_ID, trace["methodKey"])
    html = method_plot.decision_html(method, trace["generatedRating"])
    assert html.count("easi-method-decide-row") == len(method.decisions)
    assert "easi-method-decide-row on" in html      # exactly the site's category
    assert "this reach" in html


@pytest.mark.parametrize("value,expected", [
    (24.999, "Poor"), (25, "Fair"), (75, "Fair"), (75.001, "Good")])
def test_nrsa_wetted_channel_boundaries(value, expected):
    result = sm.evaluate(hydraulics.LOW_FLOW_ID, {"wettedPct": value})
    assert result.rating == expected


@pytest.mark.parametrize("value,expected", [
    (24.999, "Good"), (25, "Fair"), (75, "Fair"), (75.001, "Poor")])
def test_nrsa_embeddedness_boundaries(value, expected):
    result = sm.evaluate(geomorphology.SUBSTRATE_ID, {"embeddednessPct": value})
    assert result.rating == expected


@pytest.mark.parametrize("value,expected", [
    (0.3999, "Poor"), (0.4, "Fair"), (0.6999, "Fair"), (0.7, "Good")])
def test_streamcat_integrity_fallback_boundaries(value, expected):
    result = sm.evaluate(
        hydraulics.LOW_FLOW_ID,
        {"hydCatchment": value, "hydWatershed": 1.0},
        variant_key="streamcat-hyd-integrity")
    assert result.rating == expected
    assert result.combined_value == pytest.approx(value)
    assert result.trace["usedFallback"] is True


def test_integrity_product_formula_and_trace_provenance():
    values = {f"{component}{scale}": 0.9
              for component in ("hyd", "chem", "sed", "conn", "temp", "habt")
              for scale in ("Cat", "Ws")}
    result = sm.evaluate(
        biology.BIOINTEGRITY_ID, values,
        variant_key="streamcat-integrity-products")
    assert result.combined_value == pytest.approx(0.9 ** 6)
    assert result.rating == "Fair"
    assert result.trace["sourceTier"] == "published-model"
    assert result.trace["evidenceFamily"] == "iwi_landscape"
    assert result.trace["context"]["products"]["ICI"] == pytest.approx(0.9 ** 6)
