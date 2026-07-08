"""Tests for streamcurves.deep_export (port of R/20_deep_export.R).

Mirrors the R repo's tests/deep_export_roundtrip.R (synthetic finalized curves ->
bundle structure, alias mapping, curveLayers) and adds the DEEP contract test:
the exported bundle must pass deep.assessments.validate_bundle and score through
deep.curves (D:\\Code\\Work\\deep).
"""

import json
import logging
import sys

import numpy as np
import pandas as pd
import pytest

from streamcurves.deep_export import (
    build_deep_assessment_bundle,
    deep_collect_curve_rows,
    deep_norm_label,
    deep_points_finalize,
    deep_points_from_row,
    deep_read_staf_crosswalk,
    deep_slug,
    write_deep_assessment_bundle,
)

DEEP_ROOT = r"D:\Code\Work\deep"


# --------------------------------------------------------------------------- #
# synthetic finalized curves (mirror tests/deep_export_roundtrip.R)
# --------------------------------------------------------------------------- #
def mk_row(metric, values, indices, higher_is_better, stratum=np.nan):
    return {
        "metric": metric,
        "display_name": np.nan,
        "higher_is_better": higher_is_better,
        "curve_status": "complete",
        "stratum": stratum,
        "curve_points": pd.DataFrame(
            {
                "point_order": range(1, len(values) + 1),
                "metric_value": values,
                "index_score": indices,
            }
        ),
    }


def roundtrip_curve_rows():
    curve_rows = {
        "perImperv": mk_row("perImperv", [0, 9, 25, 75], [1, 0.7, 0.3, 0], False),
        "entrenchRatio": mk_row("entrenchRatio", [1, 1.4, 2.2, 3], [0, 0.3, 0.7, 1], True),
        "d50": mk_row("d50", [1, 1.2, 1.5, 2], [1, 0.7, 0.3, 0], False),
        "streamTemp": mk_row("streamTemp", [12, 18, 22, 26], [1, 0.7, 0.3, 0], False),
        "perRiffle": mk_row("perRiffle", [0, 20, 40, 60], [0, 0.3, 0.7, 1], True),
    }
    # a stratified metric (two stream-type strata) to exercise curveLayers export
    curve_rows["wdrStrat"] = {
        "metric": "wdrStrat",
        "display_name": np.nan,
        "higher_is_better": False,
        "curve_status": "complete",
        "stratum": "",
        "curve_points": pd.DataFrame(
            {"point_order": [1, 2, 3], "metric_value": [10, 20, 40], "index_score": [1, 0.7, 0]}
        ),
        "all_strata": [
            {
                "stratum": "C Streams",
                "curve_points": pd.DataFrame(
                    {"point_order": [1, 2, 3], "metric_value": [10, 20, 40], "index_score": [1, 0.7, 0]}
                ),
            },
            {
                "stratum": "E Streams",
                "curve_points": pd.DataFrame(
                    {"point_order": [1, 2, 3], "metric_value": [8, 16, 30], "index_score": [1, 0.7, 0]}
                ),
            },
        ],
    }
    return curve_rows


# function_label uses metric_map.yaml labels, incl. the two that differ from
# staf_functions.json ("Bed composition and bedform dynamics", "Light and
# thermal regime") — the exporter must still map them to canonical ids via aliases.
def roundtrip_mapping():
    return pd.DataFrame(
        {
            "metric_key": ["perImperv", "entrenchRatio", "d50", "streamTemp", "perRiffle", "wdrStrat"],
            "discipline": [
                "Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology",
                "Geomorphology",
            ],
            "function_label": [
                "Catchment hydrology", "Floodplain connectivity",
                "Bed composition and bedform dynamics", "Light and thermal regime",
                "Habitat provision", "Sediment continuity",
            ],
            "sort_order": [1, 2, 3, 4, 5, 6],
        }
    )


ROUNDTRIP_METRIC_CONFIG = {
    "perImperv": {"display_name": "Percent impervious cover", "units": "%", "metric_family": "proportion"},
    "entrenchRatio": {"display_name": "Entrenchment ratio", "units": "", "metric_family": "continuous"},
    "d50": {"display_name": "Median particle size (D50)", "units": "mm", "metric_family": "continuous"},
    "streamTemp": {"display_name": "Daily maximum temperature", "units": "degC", "metric_family": "continuous"},
    "perRiffle": {"display_name": "Percent riffle", "units": "%", "metric_family": "proportion"},
    "wdrStrat": {"display_name": "Width/Depth by stream type", "units": "", "metric_family": "continuous"},
}

ROUNDTRIP_META = {
    "assessmentId": "spring-demo-willamette-rt",
    "assessmentName": "SPRING Demo — Willamette (round-trip)",
    "stateCode": "OR",
    "stateName": "Oregon",
    "sourceCitation": "SPRING round-trip demo",
    "applicability": "Round-trip contract test",
}

# crosswalk file order for the six mapped functions (config/staf_functions.json)
EXPECTED_FIDS = [
    "catchment-hydrology",
    "floodplain-connectivity",
    "sediment-continuity",
    "bed-composition-bedform-dynamics",
    "light-thermal-regime",
    "habitat-provision",
]


@pytest.fixture(scope="module")
def bundle():
    return build_deep_assessment_bundle(
        roundtrip_curve_rows(), roundtrip_mapping(), ROUNDTRIP_METRIC_CONFIG, ROUNDTRIP_META
    )


def find_metric(bundle, metric_id):
    for b in bundle["metricsByFunction"]:
        for m in b["metrics"]:
            if m["metricId"] == metric_id:
                return m
    return None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def test_deep_slug():
    assert deep_slug(" Bed Composition & Bedform Dynamics! ") == "bed-composition-bedform-dynamics"
    assert deep_slug("wdrStrat") == "wdrstrat"
    assert deep_slug("--a--b--") == "a-b"


def test_deep_norm_label():
    assert deep_norm_label("Light & Thermal  Regime") == "light and thermal regime"
    assert deep_norm_label("  Bed composition and bedform dynamics ") == "bed composition and bedform dynamics"
    assert deep_norm_label("Water/soil quality") == "water soil quality"


def test_deep_points_finalize_drops_sorts_clamps():
    pts = pd.DataFrame({"x": [5.0, 1.0, 3.0, np.nan], "y": [1.2, -0.1, 0.5, 0.5]})
    out = deep_points_finalize(pts)
    assert out == [{"x": 1.0, "y": 0.0}, {"x": 3.0, "y": 0.5}, {"x": 5.0, "y": 1.0}]
    assert deep_points_finalize(None) is None
    assert deep_points_finalize(pd.DataFrame({"x": [np.nan], "y": [np.nan]})) is None


def test_deep_points_from_row_flat_columns():
    row = {
        "metric": "m",
        "curve_point1_x": 10.0, "curve_point1_y": 0.2,
        "curve_point2_x": 5.0, "curve_point2_y": 1.5,   # y clamps to 1
        "curve_point3_x": np.nan, "curve_point3_y": 0.3,  # incomplete pair dropped
    }
    assert deep_points_from_row(row) == [{"x": 5.0, "y": 1.0}, {"x": 10.0, "y": 0.2}]
    assert deep_points_from_row({"metric": "m"}) is None


def test_deep_points_from_row_prefers_nested_table():
    row = {
        "curve_points": pd.DataFrame({"metric_value": [2, 1], "index_score": [0.5, 1.0]}),
        "curve_point1_x": 99.0, "curve_point1_y": 0.0,
    }
    assert deep_points_from_row(row) == [{"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 0.5}]
    # R list-column wrapping (length-1 list holding the table) also accepted
    row_wrapped = {"curve_points": [pd.DataFrame({"metric_value": [1], "index_score": [0.7]})]}
    assert deep_points_from_row(row_wrapped) == [{"x": 1.0, "y": 0.7}]


def test_crosswalk_reads_20_functions():
    fns = deep_read_staf_crosswalk()
    assert len(fns) == 20
    assert fns[0]["id"] == "catchment-hydrology"
    assert fns[-1]["id"] == "watershed-connectivity"


# --------------------------------------------------------------------------- #
# bundle structure (mirror of tests/deep_export_roundtrip.R stopifnot block)
# --------------------------------------------------------------------------- #
def test_bundle_structure(bundle):
    assert bundle["schemaVersion"] == 1
    assert bundle["tier"] == "detailed"
    assert bundle["assessmentId"] == "spring-demo-willamette-rt"
    assert bundle["stateCode"] == "OR"
    assert bundle["sourceCitation"] == "SPRING round-trip demo"
    assert len(bundle["metricsByFunction"]) == 6

    fids = [b["functionId"] for b in bundle["metricsByFunction"]]
    assert set(EXPECTED_FIDS) <= set(fids)
    # function order = crosswalk file order
    assert fids == EXPECTED_FIDS

    # first metric of the first function carries inlined ascending points
    first = bundle["metricsByFunction"][0]["metrics"][0]
    assert first["metricId"] == "spring-perimperv"
    pts = first["curve"]["points"]
    assert len(pts) == 4
    assert [p["x"] for p in pts] == sorted(p["x"] for p in pts)
    assert all(0.0 <= p["y"] <= 1.0 for p in pts)


def test_metric_entry_fields(bundle):
    m = find_metric(bundle, "spring-perimperv")
    assert m["metricName"] == "Percent impervious cover"
    assert m["inputType"] == "proportion"
    assert m["xLabel"] == "Percent impervious cover (%)"
    assert m["howToMeasure"] == ""
    assert m["methodContext"] == ""
    assert m["sourceCitation"] == "SPRING round-trip demo"
    assert m["assignmentOrigin"] == "canonical"
    assert m["discipline"] == "Hydrology"  # crosswalk category, not the mapping's
    assert m["curve"]["layerName"] == "SPRING round-trip demo"
    assert m["curve"]["stratification"] == ""
    # no-units metric: xLabel is the bare display name
    m2 = find_metric(bundle, "spring-entrenchratio")
    assert m2["xLabel"] == "Entrenchment ratio"


def test_alias_labels_map_to_canonical_ids(bundle):
    by_fid = {b["functionId"]: b for b in bundle["metricsByFunction"]}
    assert [m["metricId"] for m in by_fid["bed-composition-bedform-dynamics"]["metrics"]] == ["spring-d50"]
    assert [m["metricId"] for m in by_fid["light-thermal-regime"]["metrics"]] == ["spring-streamtemp"]
    assert by_fid["light-thermal-regime"]["functionName"] == "Light & thermal regime"
    assert by_fid["light-thermal-regime"]["discipline"] == "Physicochemistry"


def test_stratified_metric_exports_curve_layers(bundle):
    m = find_metric(bundle, "spring-wdrstrat")
    assert m is not None
    assert "curveLayers" in m
    assert len(m["curveLayers"]) == 2
    assert [L["stratum"] for L in m["curveLayers"]] == ["C Streams", "E Streams"]
    # no unstratified layer -> active defaults to the first layer
    assert m["activeStratum"] == "C Streams"
    assert m["curveLayers"][1]["points"][0] == {"x": 8.0, "y": 1.0}
    # single-layer default curve is still present
    assert len(m["curve"]["points"]) == 3


def test_metric_reuse_across_functions_and_block_dedupe():
    rows = {"perRiffle": mk_row("perRiffle", [0, 20, 40], [0, 0.5, 1], True)}
    mapping = pd.DataFrame(
        {
            "metric_key": ["perRiffle", "perRiffle", "perRiffle"],
            "discipline": ["Biology", "Biology", "Biology"],
            "function_label": ["Habitat provision", "Population support", "Habitat provision"],
            "sort_order": [1, 2, 3],
        }
    )
    b = build_deep_assessment_bundle(rows, mapping, ROUNDTRIP_METRIC_CONFIG, ROUNDTRIP_META)
    fids = [blk["functionId"] for blk in b["metricsByFunction"]]
    assert fids == ["habitat-provision", "population-support"]
    hab = b["metricsByFunction"][0]["metrics"]
    pop = b["metricsByFunction"][1]["metrics"]
    assert len(hab) == 1 and len(pop) == 1  # duplicate assignment deduped per block
    assert hab[0]["assignmentOrigin"] == "canonical"
    assert pop[0]["assignmentOrigin"] == "additional-function"


def test_unmapped_and_incomplete_metrics_are_skipped(caplog):
    rows = {
        "good": mk_row("good", [0, 1], [0, 1], True),
        "nolabel": mk_row("nolabel", [0, 1], [0, 1], True),
        "badlabel": mk_row("badlabel", [0, 1], [0, 1], True),
        "notdone": dict(mk_row("notdone", [0, 1], [0, 1], True), curve_status="insufficient_data"),
        "nopoints": dict(mk_row("nopoints", [0, 1], [0, 1], True), curve_points=None),
    }
    mapping = pd.DataFrame(
        {
            "metric_key": ["good", "badlabel", "notdone", "nopoints"],
            "discipline": ["Hydrology"] * 4,
            "function_label": ["Catchment hydrology", "Bogus function", "Catchment hydrology",
                               "Catchment hydrology"],
            "sort_order": [1, 2, 3, 4],
        }
    )
    with caplog.at_level(logging.WARNING, logger="streamcurves"):
        b = build_deep_assessment_bundle(rows, mapping, {}, {})
    ids = [m["metricId"] for blk in b["metricsByFunction"] for m in blk["metrics"]]
    assert ids == ["spring-good"]
    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "not a canonical STAF function" in warned
    assert "nolabel" in warned
    assert "badlabel (Bogus function)" in warned


def test_no_exportable_curves_raises():
    rows = {"m": dict(mk_row("m", [0, 1], [0, 1], True), curve_status="insufficient_data")}
    mapping = pd.DataFrame(
        {"metric_key": ["m"], "discipline": ["Hydrology"],
         "function_label": ["Catchment hydrology"], "sort_order": [1]}
    )
    with pytest.raises(ValueError, match="no complete, mappable curves to export"):
        build_deep_assessment_bundle(rows, mapping, {}, {})


def test_meta_defaults():
    rows = {"m": mk_row("m", [0, 1], [0, 1], True)}
    mapping = pd.DataFrame(
        {"metric_key": ["m"], "discipline": ["Hydrology"],
         "function_label": ["Catchment hydrology"], "sort_order": [1]}
    )
    b = build_deep_assessment_bundle(rows, mapping)
    assert b["assessmentId"] == "spring-assessment"
    assert b["assessmentName"] == "SPRING detailed assessment"
    assert b["stateCode"] == "" and b["stateName"] == ""
    assert b["sourceCitation"] == "Developed in SPRING (stream-curves)"
    m = b["metricsByFunction"][0]["metrics"][0]
    assert m["metricName"] == "m"  # falls back to the metric key
    assert m["curve"]["layerName"] == "Developed in SPRING (stream-curves)"


def _one_metric_case():
    rows = {"m": mk_row("m", [0, 1], [0, 1], True)}
    mapping = pd.DataFrame(
        {"metric_key": ["m"], "discipline": ["Hydrology"],
         "function_label": ["Catchment hydrology"], "sort_order": [1]}
    )
    return rows, mapping


def test_bundle_carries_region_and_library_when_present():
    rows, mapping = _one_metric_case()
    region = {"kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains"}
    library = {"libraryId": "eastern-corn-belt-plains", "version": 2,
               "updatedAt": "2026-07-07T00:00:00Z"}
    b = build_deep_assessment_bundle(rows, mapping, {}, {"region": region, "library": library})
    assert b["region"] == region
    assert b["library"] == library


def test_bundle_omits_region_and_library_when_absent():
    rows, mapping = _one_metric_case()
    b = build_deep_assessment_bundle(rows, mapping, {}, {})
    assert "region" not in b
    assert "library" not in b


# --------------------------------------------------------------------------- #
# DataFrame input path (threshold_rows shape from streamcurves/curves.py)
# --------------------------------------------------------------------------- #
def threshold_rows_frame():
    df = pd.DataFrame(
        {
            "metric": ["perImperv", "flatMetric"],
            "display_name": [np.nan, "Flat metric"],
            "higher_is_better": [False, True],
            "curve_status": ["complete", "complete"],
            "stratum": [np.nan, "Valley"],
            "curve_source": ["quantile_regression", "manual"],
            "curve_point1_x": [np.nan, 0.0],
            "curve_point1_y": [np.nan, 0.0],
            "curve_point2_x": [np.nan, 10.0],
            "curve_point2_y": [np.nan, 0.5],
            "curve_point3_x": [np.nan, 20.0],
            "curve_point3_y": [np.nan, 1.0],
        }
    )
    df["curve_points"] = [
        pd.DataFrame({"point_order": [1, 2, 3, 4], "metric_value": [0, 9, 25, 75],
                      "index_score": [1, 0.7, 0.3, 0]}),
        None,  # falls back to the flat curve_point columns
    ]
    return df


def test_dataframe_input_matches_list_input():
    mapping = pd.DataFrame(
        {
            "metric_key": ["perImperv", "flatMetric"],
            "discipline": ["Hydrology", "Hydraulics"],
            "function_label": ["Catchment hydrology", "Floodplain connectivity"],
            "sort_order": [1, 2],
        }
    )
    b = build_deep_assessment_bundle(threshold_rows_frame(), mapping, ROUNDTRIP_METRIC_CONFIG,
                                     ROUNDTRIP_META)
    assert [blk["functionId"] for blk in b["metricsByFunction"]] == [
        "catchment-hydrology", "floodplain-connectivity",
    ]
    m1 = find_metric(b, "spring-perimperv")
    assert m1["curve"]["points"] == [
        {"x": 0.0, "y": 1.0}, {"x": 9.0, "y": 0.7}, {"x": 25.0, "y": 0.3}, {"x": 75.0, "y": 0.0}
    ]
    assert m1["curve"]["stratification"] == ""  # NA stratum -> ""
    m2 = find_metric(b, "spring-flatmetric")
    assert m2["metricName"] == "Flat metric"  # row display_name wins over config
    assert m2["curve"]["stratification"] == "Valley"
    assert m2["curve"]["points"] == [
        {"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 0.5}, {"x": 20.0, "y": 1.0}
    ]


# --------------------------------------------------------------------------- #
# deep_collect_curve_rows
# --------------------------------------------------------------------------- #
def test_collect_curve_rows_prefers_unstratified_complete():
    cp_x = pd.DataFrame({"metric_value": [1, 2], "index_score": [0, 1]})
    cp_u = pd.DataFrame({"metric_value": [3, 4], "index_score": [1, 0]})
    rows = pd.DataFrame(
        {"metric": ["A", "A"], "stratum": ["X", ""], "curve_status": ["complete", "complete"]}
    )
    rows["curve_points"] = [cp_x, cp_u]

    cp_b = pd.DataFrame({"metric_value": [5, 6], "index_score": [0, 1]})
    curve_row_b = pd.DataFrame({"curve_status": ["complete"]})  # no metric/stratum columns

    collected = deep_collect_curve_rows(
        {
            "A": {"phase4_curve_rows": rows},
            "B": {"stratum_results": {"Highland": {"reference_curve": {"curve_row": curve_row_b,
                                                                       "curve_points": cp_b}}}},
            "C": None,
        }
    )
    assert set(collected) == {"A", "B"}
    # A: the unstratified complete row wins even though it is listed second
    assert collected["A"]["stratum"] == ""
    assert collected["A"]["curve_points"] is cp_u
    assert [s["stratum"] for s in collected["A"]["all_strata"]] == ["X", ""]
    # B: stratum filled from the stratum_results key; metric filled from the map key
    assert collected["B"]["metric"] == "B"
    assert collected["B"]["stratum"] == "Highland"
    assert collected["B"]["curve_points"] is cp_b
    assert [s["stratum"] for s in collected["B"]["all_strata"]] == ["Highland"]


def test_collect_then_build_roundtrip():
    cp_c = pd.DataFrame({"metric_value": [10, 20, 40], "index_score": [1, 0.7, 0]})
    cp_e = pd.DataFrame({"metric_value": [8, 16, 30], "index_score": [1, 0.7, 0]})
    rows = pd.DataFrame(
        {"metric": ["wdr", "wdr"], "stratum": ["C Streams", "E Streams"],
         "curve_status": ["complete", "complete"]}
    )
    rows["curve_points"] = [cp_c, cp_e]
    collected = deep_collect_curve_rows({"wdr": {"phase4_curve_rows": rows}})
    assert collected["wdr"]["stratum"] == "C Streams"  # no unstratified row -> first complete
    assert len(collected["wdr"]["all_strata"]) == 2

    mapping = pd.DataFrame(
        {"metric_key": ["wdr"], "discipline": ["Geomorphology"],
         "function_label": ["Channel evolution"], "sort_order": [1]}
    )
    b = build_deep_assessment_bundle(collected, mapping, {}, {})
    m = find_metric(b, "spring-wdr")
    assert m["curve"]["stratification"] == "C Streams"
    assert [L["stratum"] for L in m["curveLayers"]] == ["C Streams", "E Streams"]
    assert m["activeStratum"] == "C Streams"


# --------------------------------------------------------------------------- #
# write_deep_assessment_bundle
# --------------------------------------------------------------------------- #
def test_write_bundle_roundtrips_json(tmp_path, bundle):
    out = tmp_path / "bundle.deep.json"
    ret = write_deep_assessment_bundle(bundle, out)
    assert ret == str(out)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded == bundle


def test_write_bundle_handles_numpy_scalars(tmp_path):
    rows = {
        "m": {
            "metric": "m",
            "curve_status": "complete",
            "stratum": np.nan,
            "curve_points": pd.DataFrame(
                {"metric_value": np.array([0.0, 1.0]), "index_score": np.array([0.0, 1.0])}
            ),
        }
    }
    mapping = pd.DataFrame(
        {"metric_key": ["m"], "discipline": ["Hydrology"],
         "function_label": ["Catchment hydrology"], "sort_order": [1]}
    )
    b = build_deep_assessment_bundle(rows, mapping, {}, {})
    out = tmp_path / "np.deep.json"
    write_deep_assessment_bundle(b, out)
    assert json.loads(out.read_text(encoding="utf-8"))["schemaVersion"] == 1


# --------------------------------------------------------------------------- #
# CONTRACT: the bundle must load, validate, and score in DEEP
# --------------------------------------------------------------------------- #
def _import_deep():
    if DEEP_ROOT not in sys.path:
        sys.path.insert(0, DEEP_ROOT)
    try:
        from deep import assessments, curves, models  # noqa: PLC0415
    except Exception as e:  # pragma: no cover - depends on sibling checkout
        pytest.skip(f"DEEP ({DEEP_ROOT}) not importable: {e}")
    return assessments, curves, models


def test_deep_contract_validate_bundle(bundle, tmp_path):
    assessments, _, _ = _import_deep()
    assert assessments.validate_bundle(bundle) == []  # empty list == OK
    # the written file (what DEEP actually ingests) validates too
    out = tmp_path / "contract.deep.json"
    write_deep_assessment_bundle(bundle, out)
    assert assessments.validate_bundle(json.loads(out.read_text(encoding="utf-8"))) == []


def test_deep_contract_from_bundle_and_score(bundle):
    assessments, deep_curves, models = _import_deep()
    loaded = assessments.from_bundle(bundle)  # raises on validation problems
    assert loaded.assessment_id == "spring-demo-willamette-rt"
    assert loaded.function_ids == EXPECTED_FIDS

    # round-trip scoring on the exported perImperv curve
    m = loaded.metrics_for_function("catchment-hydrology")[0]
    pts = m["curve"]["points"]
    assert deep_curves.interp_curve(pts, 0) == pytest.approx(1.0)
    assert deep_curves.interp_curve(pts, 9) == pytest.approx(0.7)
    assert deep_curves.interp_curve(pts, 17) == pytest.approx(0.5)  # midway 9..25
    assert deep_curves.interp_curve(pts, 1000) == pytest.approx(0.0)  # clamps right

    score, indices = deep_curves.function_index(
        [m], {m["metricId"]: models.MeasuredValue(metric_id=m["metricId"], value=17.0)}
    )
    assert score == pytest.approx(0.5 * 15)
    assert indices[m["metricId"]] == pytest.approx(0.5)

    # full site scoring runs on the loaded assessment
    result, fn_results = deep_curves.score_site(
        loaded, [models.MeasuredValue(metric_id="spring-perimperv", value=17.0)]
    )
    assert fn_results["catchment-hydrology"].score == pytest.approx(7.5)
    assert fn_results["habitat-provision"].na is True


def test_deep_contract_stratified_layers_score(bundle):
    _, deep_curves, models = _import_deep()
    m = find_metric(bundle, "spring-wdrstrat")
    # stratum-selected layer scores on that layer's points
    e_pts = deep_curves.active_points(m, "E Streams")
    assert deep_curves.interp_curve(e_pts, 16) == pytest.approx(0.7)
    # default follows activeStratum ("C Streams")
    c_pts = deep_curves.active_points(m)
    assert deep_curves.interp_curve(c_pts, 20) == pytest.approx(0.7)
    idx = deep_curves.metric_index(
        models.MeasuredValue(metric_id="spring-wdrstrat", value=16.0, stratum="E Streams"), m
    )
    assert idx == pytest.approx(0.7)
