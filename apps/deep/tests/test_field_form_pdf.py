"""The generated field worksheet and the metrics PDF.

The worksheet is built from whatever assessment is loaded, so it is checked on a
regional bundle from the baked registry and on a small bundle that carries the
methodology 0.12 additions (a fixed-criteria metric, a stratified metric, a
withheld metric). Text is read back with pypdf.
"""
from __future__ import annotations

import io

import pytest

from deep import config, field_form, report
from deep.assessments import LoadedAssessment

pypdf = pytest.importorskip("pypdf")
ASC = [{"x": 0, "y": 0}, {"x": 10, "y": 1}]
DELIN = {"watershedBasis": "site-engine",
         "siteEngine": {"engineVersion": "0.4.1", "status": "ok"},
         "delineation": {"gnis_name": "Mink Brook", "network": "nhdplus-hr",
                         "nhdplus_id": 10000900015475, "comid": 9327576, "huc8": "01080104",
                         "snapped_lat": 43.68788, "snapped_lon": -72.2477,
                         "drainage_area_sqkm": 33.5, "reach_length_ft": 1000}}


def _text(pdf: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    return " ".join(" ".join((pg.extract_text() or "") for pg in reader.pages).split())


def _bundle() -> dict:
    fixed = {"metricId": "spring-pctimp2019ws", "metricName": "Impervious surface",
             "xLabel": "Impervious surface (%)", "criteriaBasis": "fixed",
             "howToMeasure": "Impervious surface (%) is scored on fixed criteria.",
             "curve": {"points": [{"x": 0, "y": 1}, {"x": 10, "y": 0.69},
                                  {"x": 25.005, "y": 0.39}, {"x": 44.5, "y": 0}]}}
    embed = {"metricId": "spring-phab-xembed", "metricName": "Embeddedness",
             "xLabel": "Embeddedness (%)", "criteriaBasis": "reference",
             "methodContext": "Estimate the percent of each particle buried in fine sediment "
                              "at five points on each of eleven transects.",
             "curve": {"points": ASC}, "activeStratum": "",
             "curveLayers": [{"stratum": "", "points": ASC},
                             {"stratum": "ge_2", "points": ASC}],
             "stratifier": {"variable": "nhd_slope", "breaks": [0.005, 0.02], "right": False,
                            "classes": [{"key": "lt_0.5", "label": "Low gradient", "hasCurve": False},
                                        {"key": "0.5_to_2", "label": "Moderate gradient",
                                         "hasCurve": False},
                                        {"key": "ge_2", "label": "Steep (2 percent and above)",
                                         "hasCurve": True}]}}
    return {"assessmentId": "form-test", "assessmentName": "Form test assessment",
            "sourceCitation": "Test citation", "library": {"version": 3, "status": "preliminary"},
            "metricsByFunction": [
                {"functionId": "catchment-hydrology", "functionName": "Catchment hydrology",
                 "discipline": "Hydrology", "metrics": [fixed]},
                {"functionId": "hyporheic-connectivity", "functionName": "Hyporheic connectivity",
                 "discipline": "Hydraulics", "metrics": [embed]}],
            "insufficientReferenceSupport": [
                {"metricId": "spring-chem-ntl-diss", "metricName": "Dissolved nitrogen",
                 "units": "mg/L",
                 "functions": [{"functionId": "nutrient-cycling",
                                "functionName": "Nutrient cycling"}]}]}


def test_the_worksheet_follows_the_sfari_form():
    la = LoadedAssessment.from_dict(_bundle())
    pdf = report.build_field_forms_pdf(la, ref="form-test@v3", delineation=DELIN)
    assert pdf[:4] == b"%PDF"
    text = _text(pdf)
    assert field_form.TITLE in text
    assert "Form test assessment" in text and "form-test@v3" in text
    for label in ("Reach ID:", "Reach Length:", "Date:", "Assessor(s):", "Coordinates:"):
        assert label in text
    assert "RECORDING INSTRUCTIONS" in text
    assert "Functioning (15 to 11), Functioning At-Risk (10 to 6), or Non-Functioning (5 to 0)" \
        in text
    assert "HYDROLOGY FUNCTIONS" in text and "HYDRAULICS FUNCTIONS" in text
    assert text.count("Score:") == 3 and text.count("Notes/Other Metrics:") == 3
    # the function statement rides in the left cell, as on the SFARI form
    statement = config.functions_by_id()["catchment-hydrology"]["function_statement"]
    assert statement.split(",")[0] in text
    # the site is printed where the crew fills it in
    assert "NHDPlusID 10000900015475" in text and "1,000 ft" in text
    assert "43.68788, -72.24770" in text and "Mink Brook" in text


def test_each_metric_says_field_or_desk_units_and_method():
    la = LoadedAssessment.from_dict(_bundle())
    text = _text(report.build_field_forms_pdf(la))
    assert "Impervious surface (%) D" in text           # a desk metric
    assert "Embeddedness (%) F" in text                 # a field metric
    assert "Estimate the percent of each particle buried in fine sediment" in text
    assert "Curve set: [ ] All streams (pooled) [ ] Steep (2 percent and above)" in text


def test_withheld_metrics_are_named_and_need_no_value():
    la = LoadedAssessment.from_dict(_bundle())
    text = _text(report.build_field_forms_pdf(la))
    assert "BIOLOGY FUNCTIONS" not in text
    assert "Nutrient cycling" in text                   # a function only withheld metrics serve
    assert "Dissolved nitrogen not scored" in text
    assert "METRICS WITHHELD FOR INSUFFICIENT REFERENCE SUPPORT" in text


def test_desktop_values_are_printed_with_their_source():
    la = LoadedAssessment.from_dict(_bundle())
    measured = {"spring-pctimp2019ws": {
        "value": 1.3, "origin": "desktop", "engine": True, "basis": "site-engine",
        "source": "STAF site engine v0.4.1 impervious (HR reach watershed, NLCD 2021)"}}
    text = _text(report.build_field_forms_pdf(la, measured=measured, delineation=DELIN))
    assert "1.3" in text
    assert "DESKTOP: STAF site engine v0.4.1 impervious" in text


def test_the_bytes_are_reproducible_and_carry_no_em_dash():
    la = LoadedAssessment.from_dict(_bundle())
    one = report.build_field_forms_pdf(la, ref="form-test@v3", delineation=DELIN)
    two = report.build_field_forms_pdf(la, ref="form-test@v3", delineation=DELIN)
    assert one == two
    text = _text(one)
    assert "—" not in text and "–" not in text
    assert field_form.to_print_safe("a—b ≤ 5") == "a-b <= 5"


@pytest.mark.parametrize("ref", [r["assessmentRef"] for r in config._registry_records()][-3:])
def test_every_metric_of_a_baked_regional_bundle_is_on_the_form(ref):
    la = LoadedAssessment.from_dict(config.load_ref(ref))
    text = _text(report.build_field_forms_pdf(la, ref=ref))
    for m in la.all_metrics():
        name = field_form.to_print_safe(m.get("metricName") or "")
        assert " ".join(name.split()) in text, name
    assert field_form.TITLE in text


def test_no_assessment_still_prints_a_page():
    assert report.build_field_forms_pdf(None)[:4] == b"%PDF"
    assert report.build_metrics_pdf(None)[:4] == b"%PDF"


# --------------------------------------------------------------------------- #
# the metrics list and its PDF
# --------------------------------------------------------------------------- #
def test_metric_rows_give_each_metric_a_status():
    la = LoadedAssessment.from_dict(_bundle())
    rows = {r["metricId"]: r for r in report.metric_rows(la, {})}
    assert rows["spring-pctimp2019ws"]["status"] == report.STATUS_UNAVAILABLE
    assert rows["spring-pctimp2019ws"]["code"] == "D"
    assert rows["spring-phab-xembed"]["status"] == report.STATUS_FIELD
    assert rows["spring-chem-ntl-diss"]["status"] == report.STATUS_WITHHELD
    assert rows["spring-pctimp2019ws"]["scored_against"].startswith("Fixed criteria")
    pending = {r["metricId"]: r for r in report.metric_rows(la, {}, computing=True)}
    assert pending["spring-pctimp2019ws"]["status"] == report.STATUS_PENDING
    measured = {"spring-pctimp2019ws": {"value": 1.3, "origin": "desktop",
                                        "source": "StreamCat lookup engine pctimp2019"},
                "spring-phab-xembed": {"value": 22}}
    done = {r["metricId"]: r for r in report.metric_rows(la, measured)}
    assert done["spring-pctimp2019ws"]["status"] == report.STATUS_AVAILABLE
    assert done["spring-pctimp2019ws"]["value"] == "1.3"
    assert done["spring-phab-xembed"]["status"] == report.STATUS_ENTERED
    counts = report.metric_status_counts(list(done.values()))
    assert counts == {report.STATUS_AVAILABLE: 1, report.STATUS_ENTERED: 1,
                      report.STATUS_WITHHELD: 1}


def test_a_value_the_pairing_rule_withholds_reads_reference_only():
    bundle = _bundle()
    fitted = {"metricId": "spring-bfiws", "metricName": "Base flow index",
              "xLabel": "Base flow index (%)", "curve": {"points": ASC}}
    bundle["metricsByFunction"][0]["metrics"].append(fitted)
    la = LoadedAssessment.from_dict(bundle)
    measured = {"spring-bfiws": {"value": 40, "origin": "desktop", "engine": True,
                                 "source": "STAF site engine"}}
    rows = {r["metricId"]: r for r in report.metric_rows(la, measured)}
    assert rows["spring-bfiws"]["status"] == report.STATUS_REFERENCE_ONLY


def test_the_metrics_pdf_lists_every_metric_with_its_status():
    la = LoadedAssessment.from_dict(_bundle())
    measured = {"spring-pctimp2019ws": {"value": 1.3, "origin": "desktop",
                                        "source": "StreamCat lookup engine pctimp2019"}}
    text = _text(report.build_metrics_pdf(la, ref="form-test@v3", measured=measured,
                                          delineation=DELIN))
    assert "DEEP Metrics" in text and "Form test assessment" in text
    for word in ("Impervious surface", "Embeddedness", "Dissolved nitrogen",
                 report.STATUS_AVAILABLE, report.STATUS_FIELD, report.STATUS_WITHHELD,
                 "HR reach watershed (STAF site engine v0.4.1)"):
        assert word in text, word
    assert report.metrics_filename(la) == "deep-metrics-form-test.pdf"
